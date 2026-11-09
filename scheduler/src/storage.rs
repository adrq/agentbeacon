// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Where the database and the wiki search index live, and how they are described to users.

use std::fmt;
use std::fs::{DirBuilder, OpenOptions};
use std::io;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};
use std::str::FromStr;

use common::DataDir;
use sqlx::any::AnyConnectOptions;
use sqlx::postgres::PgConnectOptions;
use sqlx::sqlite::SqliteConnectOptions;

/// Which default applies when no location is given.
#[derive(Debug, Clone, Copy)]
pub enum DefaultLocation<'a> {
    /// `scheduler-{port}.db` and `wiki-index-{port}/` in the current directory.
    WorkingDir,
    /// `db/scheduler-{port}.db` and `db/wiki-index-{port}/` in the data directory.
    DataDir(&'a DataDir),
}

/// The thing a storage error is about; selects the override named in messages.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Resource {
    Database,
    WikiIndex,
}

impl Resource {
    fn noun(self) -> &'static str {
        match self {
            Resource::Database => "the database",
            Resource::WikiIndex => "the wiki search index",
        }
    }

    fn flag(self) -> &'static str {
        match self {
            Resource::Database => "--db-url",
            Resource::WikiIndex => "--wiki-index-dir",
        }
    }
}

#[derive(Debug)]
pub enum StorageError {
    NoDataDir(Resource),
    DataDirNotUtf8,
    UnusablePath(PathBuf),
    UnsupportedSqliteUrl(String),
    Create(PathBuf, Resource, io::Error),
}

impl fmt::Display for StorageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StorageError::NoDataDir(resource) => write!(
                f,
                "cannot determine a data directory for {}; set AGENTBEACON_DATA_DIR or pass {}",
                resource.noun(),
                resource.flag()
            ),
            StorageError::DataDirNotUtf8 => write!(f, "AGENTBEACON_DATA_DIR is not valid UTF-8"),
            StorageError::UnusablePath(path) => write!(
                f,
                "the database path {path:?} cannot be used in a database URL; \
                 set AGENTBEACON_DATA_DIR to a different directory or pass --db-url"
            ),
            StorageError::UnsupportedSqliteUrl(url) => write!(
                f,
                "unsupported SQLite URL '{url}': use sqlite:<path>, \
                 optionally followed by ?mode=rwc, ?mode=rw or ?mode=ro"
            ),
            StorageError::Create(path, resource, error) => write!(
                f,
                "cannot create {} for {}: {error}; set AGENTBEACON_DATA_DIR or pass {}",
                path.display(),
                resource.noun(),
                resource.flag()
            ),
        }
    }
}

impl std::error::Error for StorageError {}

/// The database to use for one run.
#[derive(Debug, Clone)]
pub struct DbLocation {
    pub url: String,
    /// Whether the URL came from `--db-url` or `DATABASE_URL`.
    pub given: bool,
    /// The file path when the URL is the data-directory default.
    pub default_path: Option<PathBuf>,
}

/// The `mode` of a SQLite URL.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum SqliteMode {
    Default,
    Rwc,
    Rw,
    Ro,
}

/// A validated SQLite target: the absolute file SQLite opens, and the URL's mode.
#[derive(Debug, Clone, PartialEq)]
pub struct SqliteTarget {
    pub path: PathBuf,
    pub mode: SqliteMode,
}

/// A database described without credentials.
#[derive(Debug, Clone, PartialEq)]
pub enum DbTarget {
    Sqlite {
        path: PathBuf,
    },
    Postgres {
        user: String,
        server: PgServer,
        database: String,
    },
    Unrecognized,
}

#[derive(Debug, Clone, PartialEq)]
pub enum PgServer {
    Tcp { host: String, port: u16 },
    Socket { dir: PathBuf, port: u16 },
}

impl fmt::Display for DbTarget {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DbTarget::Sqlite { path } => write!(f, "SQLite at {}", path.display()),
            DbTarget::Postgres {
                user,
                server: PgServer::Tcp { host, port },
                database,
            } => write!(
                f,
                "PostgreSQL database \"{database}\" on {host}:{port} as {user}"
            ),
            DbTarget::Postgres {
                user,
                server: PgServer::Socket { dir, port },
                database,
            } => write!(
                f,
                "PostgreSQL database \"{database}\" on socket {} (port {port}) as {user}",
                dir.display()
            ),
            DbTarget::Unrecognized => write!(f, "an unrecognized database URL"),
        }
    }
}

/// The database for this run: the given URL, or the default for `port`.
pub fn resolve_db(
    db_url: Option<&str>,
    port: u16,
    default: DefaultLocation,
) -> Result<DbLocation, StorageError> {
    if let Some(url) = db_url {
        if url.starts_with("sqlite:") {
            sqlite_target(url)?;
        }
        return Ok(DbLocation {
            url: url.to_string(),
            given: true,
            default_path: None,
        });
    }
    match default {
        DefaultLocation::WorkingDir => Ok(DbLocation {
            url: format!("sqlite://scheduler-{port}.db?mode=rwc"),
            given: false,
            default_path: None,
        }),
        DefaultLocation::DataDir(data_dir) => {
            let path = data_root(data_dir, Resource::Database)?
                .join("db")
                .join(format!("scheduler-{port}.db"));
            let Some(text) = path.to_str() else {
                return Err(StorageError::UnusablePath(path));
            };
            let url = format!("sqlite://{text}?mode=rwc");
            if pool_filename(&url).as_deref() != Some(path.as_path()) {
                return Err(StorageError::UnusablePath(path));
            }
            Ok(DbLocation {
                url,
                given: false,
                default_path: Some(path),
            })
        }
    }
}

/// The wiki search index directory for this run.
pub fn resolve_wiki_index_dir(
    flag: Option<&str>,
    port: u16,
    default: DefaultLocation,
) -> Result<PathBuf, StorageError> {
    if let Some(dir) = flag {
        return Ok(PathBuf::from(dir));
    }
    match default {
        DefaultLocation::WorkingDir => Ok(PathBuf::from(format!("wiki-index-{port}"))),
        DefaultLocation::DataDir(data_dir) => Ok(data_root(data_dir, Resource::WikiIndex)?
            .join("db")
            .join(format!("wiki-index-{port}"))),
    }
}

fn data_root(data_dir: &DataDir, resource: Resource) -> Result<PathBuf, StorageError> {
    let dir = match data_dir {
        DataDir::Override(dir) => {
            if dir.to_str().is_none() {
                return Err(StorageError::DataDirNotUtf8);
            }
            dir
        }
        DataDir::Platform(dir) => dir,
        DataDir::Unavailable => return Err(StorageError::NoDataDir(resource)),
    };
    std::path::absolute(dir).map_err(|e| StorageError::Create(dir.clone(), resource, e))
}

/// The filename the connection pool would hand to SQLite for `url`.
fn pool_filename(url: &str) -> Option<PathBuf> {
    let any = AnyConnectOptions::from_str(url).ok()?;
    let sqlite = SqliteConnectOptions::try_from(&any).ok()?;
    Some(sqlite.get_filename().to_path_buf())
}

/// Validates a SQLite URL against the supported forms and returns its target.
pub fn sqlite_target(url: &str) -> Result<SqliteTarget, StorageError> {
    let unsupported = || StorageError::UnsupportedSqliteUrl(url.to_string());
    let mode = match url.split_once('?').map(|(_, query)| query) {
        None => SqliteMode::Default,
        Some("mode=rwc") => SqliteMode::Rwc,
        Some("mode=rw") => SqliteMode::Rw,
        Some("mode=ro") => SqliteMode::Ro,
        Some(_) => return Err(unsupported()),
    };
    let name = pool_filename(url).ok_or_else(unsupported)?;
    let text = name.to_str().ok_or_else(unsupported)?;
    if text.is_empty() || text == ":memory:" || text.starts_with("file:") {
        return Err(unsupported());
    }
    let path = std::path::absolute(&name).map_err(|_| unsupported())?;
    Ok(SqliteTarget { path, mode })
}

/// Describes `db_url` for display; never includes a password.
pub fn describe(db_url: &str) -> DbTarget {
    if db_url.starts_with("sqlite:") {
        return match sqlite_target(db_url) {
            Ok(target) => DbTarget::Sqlite { path: target.path },
            Err(_) => DbTarget::Unrecognized,
        };
    }
    if db_url.starts_with("postgres:") || db_url.starts_with("postgresql:") {
        let Ok(options) = PgConnectOptions::from_str(db_url) else {
            return DbTarget::Unrecognized;
        };
        let port = options.get_port();
        let host = options.get_host();
        let server = if let Some(dir) = options.get_socket() {
            PgServer::Socket {
                dir: dir.clone(),
                port,
            }
        } else if host.starts_with('/') {
            PgServer::Socket {
                dir: PathBuf::from(host),
                port,
            }
        } else {
            PgServer::Tcp {
                host: host.to_string(),
                port,
            }
        };
        let user = options.get_username().to_string();
        let database = options
            .get_database()
            .map(str::to_string)
            .unwrap_or_else(|| user.clone());
        return DbTarget::Postgres {
            user,
            server,
            database,
        };
    }
    DbTarget::Unrecognized
}

/// Whether setup should ask before creating the SQLite database of `location`.
pub fn needs_create_confirmation(location: &DbLocation) -> bool {
    if !location.url.starts_with("sqlite:") {
        return false;
    }
    let Ok(target) = sqlite_target(&location.url) else {
        return false;
    };
    if target.mode != SqliteMode::Rwc {
        return false;
    }
    if location.given && !target.path.parent().is_some_and(Path::is_dir) {
        return false;
    }
    matches!(std::fs::metadata(&target.path), Err(e) if e.kind() == io::ErrorKind::NotFound)
}

/// Creates `dir` with mode 0700 if missing, along with its parents.
fn create_private_dir(dir: &Path, resource: Resource) -> Result<(), StorageError> {
    if let Some(parent) = dir.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| StorageError::Create(parent.to_path_buf(), resource, e))?;
    }
    match DirBuilder::new().mode(0o700).create(dir) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == io::ErrorKind::AlreadyExists && dir.is_dir() => Ok(()),
        Err(e) => Err(StorageError::Create(dir.to_path_buf(), resource, e)),
    }
}

/// Creates the data-directory database: its directory with mode 0700 and the file with 0600.
/// Existing ones are left as they are.
pub fn prepare_default(path: &Path) -> Result<(), StorageError> {
    if let Some(dir) = path.parent() {
        create_private_dir(dir, Resource::Database)?;
    }
    match OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)
    {
        Ok(_) => Ok(()),
        Err(e)
            if e.kind() == io::ErrorKind::AlreadyExists
                && std::fs::metadata(path).is_ok_and(|m| m.is_file()) =>
        {
            Ok(())
        }
        Err(e) => Err(StorageError::Create(
            path.to_path_buf(),
            Resource::Database,
            e,
        )),
    }
}

/// Creates the data-directory wiki search index directory, its parent with mode 0700.
pub fn prepare_default_index(dir: &Path) -> Result<(), StorageError> {
    if let Some(parent) = dir.parent() {
        create_private_dir(parent, Resource::WikiIndex)?;
    }
    std::fs::create_dir_all(dir)
        .map_err(|e| StorageError::Create(dir.to_path_buf(), Resource::WikiIndex, e))
}
