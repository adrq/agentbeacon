use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, RwLock};

use tantivy::collector::TopDocs;
use tantivy::directory::MmapDirectory;
use tantivy::query::QueryParser;
use tantivy::schema::Value;
use tantivy::schema::{
    Field, IndexRecordOption, STORED, STRING, Schema, TextFieldIndexing, TextOptions,
};
use tantivy::{Index, IndexReader, IndexWriter, ReloadPolicy, TantivyDocument, Term};
use tracing::warn;

use crate::db::wiki::WikiPage;
use crate::error::SchedulerError;

// -- Error types --

#[derive(Debug)]
pub enum SearchError {
    Tantivy(String),
    Io(std::io::Error),
    QueryParse(String),
}

impl std::fmt::Display for SearchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            SearchError::Tantivy(msg) => write!(f, "tantivy error: {msg}"),
            SearchError::Io(e) => write!(f, "I/O error: {e}"),
            SearchError::QueryParse(msg) => write!(f, "query parse error: {msg}"),
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

// -- Result types --

pub struct WikiSearchResult {
    pub slug: String,
    pub title: String,
    pub revision_number: i64,
    pub updated_by: Option<String>,
    pub updated_at: String,
    pub score: f32,
}

// -- Schema fields --

struct WikiFields {
    page_id: Field,
    slug: Field,
    title: Field,
    body: Field,
    revision_number: Field,
    updated_by: Field,
    updated_at: Field,
}

fn build_schema() -> (Schema, WikiFields) {
    let mut builder = Schema::builder();

    // page_id: STRING (indexed, not tokenized) + STORED — used for delete_term
    let page_id = builder.add_text_field("page_id", STRING | STORED);

    // slug: STRING + STORED — result identification
    let slug = builder.add_text_field("slug", STRING | STORED);

    // title: TEXT (tokenized) + STORED — BM25 search + display
    let title_options = TextOptions::default()
        .set_indexing_options(
            TextFieldIndexing::default()
                .set_tokenizer("default")
                .set_index_option(IndexRecordOption::WithFreqsAndPositions),
        )
        .set_stored();
    let title = builder.add_text_field("title", title_options);

    // body: TEXT (tokenized), NOT stored — BM25 search only
    let body_options = TextOptions::default().set_indexing_options(
        TextFieldIndexing::default()
            .set_tokenizer("default")
            .set_index_option(IndexRecordOption::WithFreqsAndPositions),
    );
    let body = builder.add_text_field("body", body_options);

    // Metadata fields: stored only (i64 for revision, STRING for others)
    let revision_number = builder.add_i64_field("revision_number", STORED);
    let updated_by = builder.add_text_field("updated_by", STRING | STORED);
    let updated_at = builder.add_text_field("updated_at", STRING | STORED);

    let fields = WikiFields {
        page_id,
        slug,
        title,
        body,
        revision_number,
        updated_by,
        updated_at,
    };

    (builder.build(), fields)
}

// -- Per-project index --

struct ProjectIndex {
    _index: Index,
    reader: IndexReader,
    writer: Mutex<IndexWriter>,
    fields: WikiFields,
    query_parser: QueryParser,
}

// -- Main search index --

#[derive(Clone)]
pub struct WikiSearchIndex {
    inner: Arc<WikiSearchIndexInner>,
}

struct WikiSearchIndexInner {
    data_dir: PathBuf,
    indices: RwLock<HashMap<String, Arc<ProjectIndex>>>,
}

impl WikiSearchIndex {
    pub fn new(data_dir: PathBuf) -> Self {
        Self {
            inner: Arc::new(WikiSearchIndexInner {
                data_dir,
                indices: RwLock::new(HashMap::new()),
            }),
        }
    }

    /// Search wiki pages by query string. Returns results ranked by BM25 score.
    pub fn search(
        &self,
        project_id: &str,
        query: &str,
        limit: usize,
    ) -> Result<Vec<WikiSearchResult>, SchedulerError> {
        let project_index = self.get_or_create_index(project_id)?;
        let searcher = project_index.reader.searcher();

        let (parsed_query, parse_errors) = project_index.query_parser.parse_query_lenient(query);

        if !parse_errors.is_empty() {
            warn!(
                query = %query,
                errors = ?parse_errors,
                "wiki search query parse warnings"
            );
        }

        let top_docs = searcher
            .search(&parsed_query, &TopDocs::with_limit(limit))
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        let mut results = Vec::with_capacity(top_docs.len());
        for (score, doc_address) in top_docs {
            let doc: TantivyDocument = searcher
                .doc(doc_address)
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;

            let slug = doc
                .get_first(project_index.fields.slug)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let title = doc
                .get_first(project_index.fields.title)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let revision_number = doc
                .get_first(project_index.fields.revision_number)
                .and_then(|v| v.as_i64())
                .unwrap_or(0);

            let updated_by: Option<String> = doc
                .get_first(project_index.fields.updated_by)
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let updated_at = doc
                .get_first(project_index.fields.updated_at)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            results.push(WikiSearchResult {
                slug,
                title,
                revision_number,
                updated_by,
                updated_at,
                score,
            });
        }

        Ok(results)
    }

    /// Index a wiki page (create or update). Deletes old doc by page_id term, adds new doc, commits.
    pub fn index_page(&self, project_id: &str, page: &WikiPage) -> Result<(), SchedulerError> {
        let project_index = self.get_or_create_index(project_id)?;
        let mut writer = project_index
            .writer
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("writer lock poisoned: {e}")))?;

        // Delete any existing doc with this page_id
        let term = Term::from_field_text(project_index.fields.page_id, &page.id);
        writer.delete_term(term);

        // Add new document
        let mut doc = TantivyDocument::new();
        doc.add_text(project_index.fields.page_id, &page.id);
        doc.add_text(project_index.fields.slug, &page.slug);
        doc.add_text(project_index.fields.title, &page.title);
        doc.add_text(project_index.fields.body, &page.body);
        doc.add_i64(project_index.fields.revision_number, page.revision_number);
        if let Some(ref updated_by) = page.updated_by {
            doc.add_text(project_index.fields.updated_by, updated_by);
        }
        doc.add_text(
            project_index.fields.updated_at,
            page.updated_at.to_rfc3339(),
        );

        writer
            .add_document(doc)
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        writer
            .commit()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        drop(writer);

        // Reload reader for immediate search consistency
        project_index
            .reader
            .reload()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        Ok(())
    }

    /// Remove a page from the search index by page_id term.
    pub fn remove_page(&self, project_id: &str, page_id: &str) -> Result<(), SchedulerError> {
        let project_index = self.get_or_create_index(project_id)?;
        let mut writer = project_index
            .writer
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("writer lock poisoned: {e}")))?;

        let term = Term::from_field_text(project_index.fields.page_id, page_id);
        writer.delete_term(term);

        writer
            .commit()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        drop(writer);

        project_index
            .reader
            .reload()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        Ok(())
    }

    /// Rebuild the index for a project from a full set of pages.
    /// Uses delete_all_documents + re-add + commit (no directory drop/recreate to avoid lock conflicts).
    pub fn rebuild_project(
        &self,
        project_id: &str,
        pages: &[WikiPage],
    ) -> Result<(), SchedulerError> {
        let project_index = self.get_or_create_index(project_id)?;
        let mut writer = project_index
            .writer
            .lock()
            .map_err(|e| SearchError::Tantivy(format!("writer lock poisoned: {e}")))?;

        writer
            .delete_all_documents()
            .map_err(|e| SearchError::Tantivy(format!("delete_all_documents failed: {e}")))?;

        for page in pages {
            let mut doc = TantivyDocument::new();
            doc.add_text(project_index.fields.page_id, &page.id);
            doc.add_text(project_index.fields.slug, &page.slug);
            doc.add_text(project_index.fields.title, &page.title);
            doc.add_text(project_index.fields.body, &page.body);
            doc.add_i64(project_index.fields.revision_number, page.revision_number);
            if let Some(ref updated_by) = page.updated_by {
                doc.add_text(project_index.fields.updated_by, updated_by);
            }
            doc.add_text(
                project_index.fields.updated_at,
                page.updated_at.to_rfc3339(),
            );

            writer
                .add_document(doc)
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        }

        writer
            .commit()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;
        drop(writer);

        project_index
            .reader
            .reload()
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        Ok(())
    }

    /// Get or create a per-project index. Uses double-checked locking for thread safety.
    fn get_or_create_index(&self, project_id: &str) -> Result<Arc<ProjectIndex>, SchedulerError> {
        // Fast path: read lock
        {
            let indices =
                self.inner.indices.read().map_err(|e| {
                    SearchError::Tantivy(format!("indices read lock poisoned: {e}"))
                })?;
            if let Some(idx) = indices.get(project_id) {
                return Ok(Arc::clone(idx));
            }
        }

        // Slow path: write lock, re-check (TOCTOU safe)
        let mut indices = self
            .inner
            .indices
            .write()
            .map_err(|e| SearchError::Tantivy(format!("indices write lock poisoned: {e}")))?;

        if let Some(idx) = indices.get(project_id) {
            return Ok(Arc::clone(idx));
        }

        let project_dir = self.inner.data_dir.join(project_id);

        // Defense-in-depth: ensure resolved path is inside data_dir
        if !project_dir.starts_with(&self.inner.data_dir) {
            return Err(SearchError::Tantivy(
                "project_id resolved outside index data directory".into(),
            )
            .into());
        }

        std::fs::create_dir_all(&project_dir).map_err(SearchError::Io)?;

        let (schema, fields) = build_schema();

        let index = if project_dir.join("meta.json").exists() {
            let dir = MmapDirectory::open(&project_dir)
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;
            match Index::open(dir) {
                Ok(idx) => idx,
                Err(e) => {
                    // Corrupt index — remove and recreate fresh
                    warn!(
                        project_id = %project_id,
                        error = %e,
                        "corrupt wiki search index, recreating"
                    );
                    std::fs::remove_dir_all(&project_dir).map_err(SearchError::Io)?;
                    std::fs::create_dir_all(&project_dir).map_err(SearchError::Io)?;
                    let dir = MmapDirectory::open(&project_dir)
                        .map_err(|e| SearchError::Tantivy(e.to_string()))?;
                    Index::create(dir, schema, tantivy::IndexSettings::default())
                        .map_err(|e| SearchError::Tantivy(e.to_string()))?
                }
            }
        } else {
            let dir = MmapDirectory::open(&project_dir)
                .map_err(|e| SearchError::Tantivy(e.to_string()))?;
            Index::create(dir, schema, tantivy::IndexSettings::default())
                .map_err(|e| SearchError::Tantivy(e.to_string()))?
        };

        let reader = index
            .reader_builder()
            .reload_policy(ReloadPolicy::OnCommitWithDelay)
            .try_into()
            .map_err(|e: tantivy::TantivyError| SearchError::Tantivy(e.to_string()))?;

        let writer: IndexWriter = index
            .writer(15_000_000) // 15MB heap budget (per-project indexes are small)
            .map_err(|e| SearchError::Tantivy(e.to_string()))?;

        let mut query_parser = QueryParser::for_index(&index, vec![fields.title, fields.body]);
        query_parser.set_field_boost(fields.title, 2.0);

        let project_index = Arc::new(ProjectIndex {
            _index: index,
            reader,
            writer: Mutex::new(writer),
            fields,
            query_parser,
        });

        indices.insert(project_id.to_string(), Arc::clone(&project_index));
        Ok(project_index)
    }
}
