use std::path::PathBuf;
use std::sync::Mutex;
use std::sync::{Arc, OnceLock};

use tantivy::collector::{Count, TopDocs};
use tantivy::directory::MmapDirectory;
use tantivy::query::{
    BooleanQuery, BoostQuery, ConstScoreQuery, Occur, Query, TermQuery, TermSetQuery,
};
use tantivy::schema::Value;
use tantivy::schema::{
    Field, IndexRecordOption, STORED, STRING, Schema, TextFieldIndexing, TextOptions,
};
use tantivy::{Index, IndexReader, IndexWriter, ReloadPolicy, TantivyDocument, Term};
use tracing::warn;

use crate::db::wiki::WikiPage;
use crate::error::SchedulerError;

/// Child of the configured root that holds the index.
const INDEX_CHILD: &str = "index";
/// Prefix reserved for directories this binary creates under the root.
const OWNED_TEMP_PREFIX: &str = ".wiki-index-";

/// Weight applied to title matches relative to body matches.
const TITLE_BOOST: f32 = 2.0;

#[derive(Debug)]
pub enum SearchError {
    Tantivy(String),
    Io(std::io::Error),
}

impl std::fmt::Display for SearchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SearchError::Tantivy(msg) => write!(f, "tantivy error: {msg}"),
            SearchError::Io(e) => write!(f, "I/O error: {e}"),
        }
    }
}

impl From<tantivy::TantivyError> for SearchError {
    fn from(e: tantivy::TantivyError) -> Self {
        SearchError::Tantivy(e.to_string())
    }
}

impl From<std::io::Error> for SearchError {
    fn from(e: std::io::Error) -> Self {
        SearchError::Io(e)
    }
}

impl From<SearchError> for SchedulerError {
    fn from(e: SearchError) -> Self {
        SchedulerError::SearchFailed(e.to_string())
    }
}

pub struct WikiSearchResult {
    pub page_id: String,
    pub project_id: String,
    pub slug: String,
    pub title: String,
    pub revision_number: i64,
    pub updated_by: Option<String>,
    pub updated_at: String,
    pub tags: Vec<String>,
    pub score: f32,
}

/// The set of documents a caller may match.
///
/// `projects` grants every page owned by those projects. `tagged` grants pages
/// owned by a specific project and carrying a specific tag, as explicit pairs.
#[derive(Debug, Default, Clone)]
pub struct SearchScope {
    pub projects: Vec<String>,
    pub tagged: Vec<(String, String)>,
}

impl SearchScope {
    pub fn is_empty(&self) -> bool {
        self.projects.is_empty() && self.tagged.is_empty()
    }

    /// Narrow to a single project, keeping only what the scope already granted.
    pub fn narrowed_to(&self, project_id: &str) -> Self {
        Self {
            projects: self
                .projects
                .iter()
                .filter(|p| String::as_str(p) == project_id)
                .cloned()
                .collect(),
            tagged: self
                .tagged
                .iter()
                .filter(|(p, _)| p == project_id)
                .cloned()
                .collect(),
        }
    }
}

/// A page and the tag names attached to it, as indexed.
pub struct IndexedPage<'a> {
    pub page: &'a WikiPage,
    pub tags: &'a [String],
}

struct WikiFields {
    page_id: Field,
    project_id: Field,
    slug: Field,
    title: Field,
    body: Field,
    tags: Field,
    revision_number: Field,
    updated_by: Field,
    updated_at: Field,
}

fn build_schema() -> (Schema, WikiFields) {
    let mut builder = Schema::builder();

    let page_id = builder.add_text_field("page_id", STRING | STORED);

    let project_id = builder.add_text_field("project_id", STRING | STORED);

    let slug = builder.add_text_field("slug", STRING | STORED);

    let title_options = TextOptions::default()
        .set_indexing_options(
            TextFieldIndexing::default()
                .set_tokenizer("default")
                .set_index_option(IndexRecordOption::WithFreqsAndPositions),
        )
        .set_stored();
    let title = builder.add_text_field("title", title_options);

    let body_options = TextOptions::default().set_indexing_options(
        TextFieldIndexing::default()
            .set_tokenizer("default")
            .set_index_option(IndexRecordOption::WithFreqsAndPositions),
    );
    let body = builder.add_text_field("body", body_options);

    let tags = builder.add_text_field("tags", STRING | STORED);

    let revision_number = builder.add_i64_field("revision_number", STORED);
    let updated_by = builder.add_text_field("updated_by", STRING | STORED);
    let updated_at = builder.add_text_field("updated_at", STRING | STORED);

    let fields = WikiFields {
        page_id,
        project_id,
        slug,
        title,
        body,
        tags,
        revision_number,
        updated_by,
        updated_at,
    };

    (builder.build(), fields)
}

struct GlobalIndex {
    index: Index,
    reader: IndexReader,
    writer: Mutex<IndexWriter>,
    fields: WikiFields,
}

#[derive(Clone)]
pub struct WikiSearchIndex {
    inner: Arc<WikiSearchIndexInner>,
}

struct WikiSearchIndexInner {
    data_dir: PathBuf,
    index: OnceLock<Arc<GlobalIndex>>,
    init_lock: Mutex<()>,
}

impl WikiSearchIndex {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            inner: Arc::new(WikiSearchIndexInner {
                data_dir,
                index: OnceLock::new(),
                init_lock: Mutex::new(()),
            }),
        }
    }

    /// Search wiki pages within `scope`, ranked by BM25 then project then slug.
    pub fn search(
        &self,
        scope: &SearchScope,
        query: &str,
        limit: usize,
        offset: usize,
    ) -> Result<Vec<WikiSearchResult>, SchedulerError> {
        let index = self.get_or_create_index()?;

        let Some(scope_query) = self.scope_query(&index, scope) else {
            return Ok(Vec::new());
        };
        let Some(terms_query) = self.terms_query(&index, query) else {
            return Ok(Vec::new());
        };

        let combined =
            BooleanQuery::new(vec![(Occur::Must, scope_query), (Occur::Must, terms_query)]);

        let searcher = index.reader.searcher();

        let matched = searcher
            .search(&combined, &Count)
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        if matched == 0 || offset >= matched {
            return Ok(Vec::new());
        }

        let top_docs = searcher
            .search(&combined, &TopDocs::with_limit(matched))
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        let mut results = Vec::with_capacity(top_docs.len());
        for (score, doc_address) in top_docs {
            let doc: TantivyDocument = searcher
                .doc(doc_address)
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;
            results.push(read_result(&doc, &index.fields, score));
        }

        results.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.project_id.cmp(&b.project_id))
                .then_with(|| a.slug.cmp(&b.slug))
        });

        Ok(results.into_iter().skip(offset).take(limit).collect())
    }

    /// Boolean query matching everything the scope grants.
    fn scope_query(&self, index: &GlobalIndex, scope: &SearchScope) -> Option<Box<dyn Query>> {
        if scope.is_empty() {
            return None;
        }

        let mut clauses: Vec<(Occur, Box<dyn Query>)> = Vec::new();

        if !scope.projects.is_empty() {
            let terms = scope
                .projects
                .iter()
                .map(|p| Term::from_field_text(index.fields.project_id, p))
                .collect::<Vec<_>>();
            clauses.push((Occur::Should, Box::new(TermSetQuery::new(terms))));
        }

        for (project_id, tag) in &scope.tagged {
            let pair = BooleanQuery::new(vec![
                (
                    Occur::Must,
                    Box::new(TermQuery::new(
                        Term::from_field_text(index.fields.project_id, project_id),
                        IndexRecordOption::Basic,
                    )) as Box<dyn Query>,
                ),
                (
                    Occur::Must,
                    Box::new(TermQuery::new(
                        Term::from_field_text(index.fields.tags, tag),
                        IndexRecordOption::Basic,
                    )) as Box<dyn Query>,
                ),
            ]);
            clauses.push((Occur::Should, Box::new(pair)));
        }

        Some(Box::new(ConstScoreQuery::new(
            Box::new(BooleanQuery::new(clauses)),
            0.0,
        )))
    }

    /// One SHOULD clause per token per searched field, or `None` if the query
    /// yields no tokens.
    fn terms_query(&self, index: &GlobalIndex, query: &str) -> Option<Box<dyn Query>> {
        let mut analyzer = index.index.tokenizers().get("default")?;
        let mut token_stream = analyzer.token_stream(query);

        let mut clauses: Vec<(Occur, Box<dyn Query>)> = Vec::new();
        while let Some(token) = token_stream.next() {
            clauses.push((
                Occur::Should,
                Box::new(BoostQuery::new(
                    Box::new(TermQuery::new(
                        Term::from_field_text(index.fields.title, &token.text),
                        IndexRecordOption::WithFreqsAndPositions,
                    )),
                    TITLE_BOOST,
                )) as Box<dyn Query>,
            ));
            clauses.push((
                Occur::Should,
                Box::new(TermQuery::new(
                    Term::from_field_text(index.fields.body, &token.text),
                    IndexRecordOption::WithFreqsAndPositions,
                )) as Box<dyn Query>,
            ));
            clauses.push((
                Occur::Should,
                Box::new(TermQuery::new(
                    Term::from_field_text(index.fields.slug, &token.text),
                    IndexRecordOption::Basic,
                )) as Box<dyn Query>,
            ));
        }

        if clauses.is_empty() {
            return None;
        }
        Some(Box::new(BooleanQuery::new(clauses)))
    }

    /// Index a wiki page (create or update). Deletes old doc by page_id term, adds new doc, commits.
    pub fn index_page(&self, page: &WikiPage, tags: &[String]) -> Result<(), SchedulerError> {
        let index = self.get_or_create_index()?;
        let mut writer = index
            .writer
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("writer lock poisoned: {e}")))?;

        writer.delete_term(Term::from_field_text(index.fields.page_id, &page.id));
        writer
            .add_document(build_document(&index.fields, page, tags))
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        writer
            .commit()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        drop(writer);

        index
            .reader
            .reload()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        Ok(())
    }

    /// Remove a page from the search index by page_id term.
    pub fn remove_page(&self, page_id: &str) -> Result<(), SchedulerError> {
        let index = self.get_or_create_index()?;
        let mut writer = index
            .writer
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("writer lock poisoned: {e}")))?;

        writer.delete_term(Term::from_field_text(index.fields.page_id, page_id));
        writer
            .commit()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        drop(writer);

        index
            .reader
            .reload()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        Ok(())
    }

    /// Replace the whole index: one delete, every page added, one commit.
    pub fn rebuild_all(&self, pages: &[IndexedPage<'_>]) -> Result<(), SchedulerError> {
        let index = self.get_or_create_index()?;
        let mut writer = index
            .writer
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("writer lock poisoned: {e}")))?;

        writer
            .delete_all_documents()
            .map_err(|e| SearchError::Tantivy(format!("delete_all_documents failed: {e}")))?;

        for entry in pages {
            writer
                .add_document(build_document(&index.fields, entry.page, entry.tags))
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        }

        writer
            .commit()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        drop(writer);

        index
            .reader
            .reload()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        Ok(())
    }

    /// Build a fresh index beside the current one and swap it in.
    fn replace_index(
        root: &std::path::Path,
        dir: &std::path::Path,
        schema: Schema,
    ) -> Result<Index, SchedulerError> {
        let token = uuid::Uuid::new_v4().to_string();
        let staging = root.join(format!("{OWNED_TEMP_PREFIX}new-{token}"));
        std::fs::create_dir_all(&staging).map_err(SearchError::Io)?;

        {
            let mmap =
                MmapDirectory::open(&staging).map_err(|e| SearchError::Tantivy(e.to_string()))?;
            Index::create(mmap, schema, tantivy::IndexSettings::default())
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        }

        let discarded = root.join(format!("{OWNED_TEMP_PREFIX}old-{token}"));
        if dir.exists() {
            std::fs::rename(dir, &discarded).map_err(SearchError::Io)?;
        }
        if let Err(e) = std::fs::rename(&staging, dir) {
            if discarded.exists() {
                let _ = std::fs::rename(&discarded, dir);
            }
            return Err(SearchError::Io(e).into());
        }

        if discarded.exists() {
            warn!(
                path = %discarded.display(),
                "superseded wiki search index kept aside; remove it by hand once you are sure of its contents"
            );
        }

        let mmap = MmapDirectory::open(dir).map_err(|e| SearchError::Tantivy(e.to_string()))?;
        Index::open(mmap).map_err(|e| SearchError::Tantivy(e.to_string()).into())
    }

    fn get_or_create_index(&self) -> Result<Arc<GlobalIndex>, SchedulerError> {
        if let Some(index) = self.inner.index.get() {
            return Ok(Arc::clone(index));
        }

        let _guard = self
            .inner
            .init_lock
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("index init lock poisoned: {e}")))?;

        if let Some(index) = self.inner.index.get() {
            return Ok(Arc::clone(index));
        }

        let root = &self.inner.data_dir;
        std::fs::create_dir_all(root).map_err(SearchError::Io)?;
        let dir = root.join(INDEX_CHILD);

        let (schema, fields) = build_schema();

        let existing = if dir.join("meta.json").exists() {
            let mmap =
                MmapDirectory::open(&dir).map_err(|e| SearchError::Tantivy(e.to_string()))?;
            match Index::open(mmap) {
                Ok(idx) if idx.schema() == schema => Some(idx),
                Ok(_) => {
                    warn!("wiki search index schema differs from this binary, rebuilding");
                    None
                }
                Err(e) => {
                    warn!(error = %e, "corrupt wiki search index, rebuilding");
                    None
                }
            }
        } else {
            None
        };

        let index = match existing {
            Some(idx) => idx,
            None => Self::replace_index(root, &dir, schema)?,
        };

        let reader = index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()
            .map_err(|e: tantivy::TantivyError| SearchError::Tantivy(e.to_string()))?;

        let writer: IndexWriter = index
            .writer(50_000_000)
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        let global = Arc::new(GlobalIndex {
            index,
            reader,
            writer: Mutex::new(writer),
            fields,
        });

        let _ = self.inner.index.set(Arc::clone(&global));
        Ok(global)
    }
}

fn build_document(fields: &WikiFields, page: &WikiPage, tags: &[String]) -> TantivyDocument {
    let mut doc = TantivyDocument::new();
    doc.add_text(fields.page_id, &page.id);
    doc.add_text(fields.project_id, &page.project_id);
    doc.add_text(fields.slug, &page.slug);
    doc.add_text(fields.title, &page.title);
    doc.add_text(fields.body, &page.body);
    for tag in tags {
        doc.add_text(fields.tags, tag);
    }
    doc.add_i64(fields.revision_number, page.revision_number);
    if let Some(ref updated_by) = page.updated_by {
        doc.add_text(fields.updated_by, updated_by);
    }
    doc.add_text(fields.updated_at, page.updated_at.to_rfc3339());
    doc
}

fn read_result(doc: &TantivyDocument, fields: &WikiFields, score: f32) -> WikiSearchResult {
    let text = |field: Field| {
        doc.get_first(field)
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string()
    };

    WikiSearchResult {
        page_id: text(fields.page_id),
        project_id: text(fields.project_id),
        slug: text(fields.slug),
        title: text(fields.title),
        revision_number: doc
            .get_first(fields.revision_number)
            .and_then(|v| v.as_i64())
            .unwrap_or(0),
        updated_by: doc
            .get_first(fields.updated_by)
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        updated_at: text(fields.updated_at),
        tags: doc
            .get_all(fields.tags)
            .filter_map(|v| v.as_str())
            .map(|s| s.to_string())
            .collect(),
        score,
    }
}
