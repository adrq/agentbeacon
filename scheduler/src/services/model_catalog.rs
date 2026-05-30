use std::collections::HashMap;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

const CACHE_TTL: Duration = Duration::from_secs(60);

#[derive(Debug, Clone, Serialize)]
pub struct ModelSuggestion {
    pub id: String,
    pub label: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recommended: Option<bool>,
}

struct CacheEntry {
    models: Vec<ModelSuggestion>,
    fetched_at: Instant,
}

static CACHE: std::sync::LazyLock<Mutex<HashMap<String, CacheEntry>>> =
    std::sync::LazyLock::new(|| Mutex::new(HashMap::new()));

// Per-platform fetch locks to prevent thundering-herd CLI spawns on cache expiry
static FETCH_LOCKS: std::sync::LazyLock<HashMap<&'static str, tokio::sync::Mutex<()>>> =
    std::sync::LazyLock::new(|| {
        let mut m = HashMap::new();
        for platform in ["claude_sdk", "copilot_sdk", "codex_sdk", "acp"] {
            m.insert(platform, tokio::sync::Mutex::new(()));
        }
        m
    });

pub async fn list_models(platform: &str) -> Vec<ModelSuggestion> {
    // Fast path: check cache without holding the fetch lock
    {
        let cache = CACHE.lock().unwrap();
        if let Some(entry) = cache.get(platform)
            && entry.fetched_at.elapsed() < CACHE_TTL
        {
            return entry.models.clone();
        }
    }

    // Acquire per-platform lock to serialize CLI spawns
    let fetch_lock = match FETCH_LOCKS.get(platform) {
        Some(lock) => lock,
        None => return vec![],
    };
    let _guard = fetch_lock.lock().await;

    // Re-check cache — another request may have populated it while we waited
    {
        let cache = CACHE.lock().unwrap();
        if let Some(entry) = cache.get(platform)
            && entry.fetched_at.elapsed() < CACHE_TTL
        {
            return entry.models.clone();
        }
    }

    let result = match platform {
        "claude_sdk" => claude_models(),
        "copilot_sdk" => copilot_models().await,
        "codex_sdk" => codex_models().await,
        "acp" => vec![],
        _ => vec![],
    };

    // Update cache
    {
        let mut cache = CACHE.lock().unwrap();
        cache.insert(
            platform.to_string(),
            CacheEntry {
                models: result.clone(),
                fetched_at: Instant::now(),
            },
        );
    }

    result
}

fn claude_models() -> Vec<ModelSuggestion> {
    vec![
        ModelSuggestion {
            id: "opus".to_string(),
            label: "Claude Opus (alias)".to_string(),
            description: Some("Alias — resolves to current Opus".to_string()),
            recommended: Some(true),
        },
        ModelSuggestion {
            id: "sonnet".to_string(),
            label: "Claude Sonnet (alias)".to_string(),
            description: Some("Alias — resolves to current Sonnet".to_string()),
            recommended: Some(true),
        },
        ModelSuggestion {
            id: "haiku".to_string(),
            label: "Claude Haiku (alias)".to_string(),
            description: Some("Alias — resolves to current Haiku".to_string()),
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-opus-4-8".to_string(),
            label: "Claude Opus 4.8".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-opus-4-8[1m]".to_string(),
            label: "Claude Opus 4.8 (1M context)".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-opus-4-7".to_string(),
            label: "Claude Opus 4.7".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-opus-4-7[1m]".to_string(),
            label: "Claude Opus 4.7 (1M context)".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-opus-4-6".to_string(),
            label: "Claude Opus 4.6".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-opus-4-6[1m]".to_string(),
            label: "Claude Opus 4.6 (1M context)".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-sonnet-4-6".to_string(),
            label: "Claude Sonnet 4.6".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-sonnet-4-6[1m]".to_string(),
            label: "Claude Sonnet 4.6 (1M context)".to_string(),
            description: None,
            recommended: None,
        },
        ModelSuggestion {
            id: "claude-haiku-4-5".to_string(),
            label: "Claude Haiku 4.5".to_string(),
            description: None,
            recommended: None,
        },
    ]
}

async fn copilot_models() -> Vec<ModelSuggestion> {
    match copilot_models_inner().await {
        Ok(models) => models,
        Err(e) => {
            tracing::warn!("copilot models.list failed: {e}");
            // Return stale cache if available
            let cache = CACHE.lock().unwrap();
            cache
                .get("copilot_sdk")
                .map(|e| e.models.clone())
                .unwrap_or_default()
        }
    }
}

async fn copilot_models_inner() -> Result<Vec<ModelSuggestion>, String> {
    let mut cmd = tokio::process::Command::new("copilot");
    cmd.args([
        "--headless",
        "--no-auto-update",
        "--stdio",
        "--log-level",
        "error",
    ]);

    if std::env::var("COPILOT_SDK_AUTH_TOKEN").is_ok() {
        cmd.args(["--auth-token-env", "COPILOT_SDK_AUTH_TOKEN"]);
    }

    cmd.stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    crate::process_group::configure_child_process(&mut cmd);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn copilot: {e}"))?;

    let result = rpc_exchange_and_parse(&mut child, "models.list", "/result/models").await;

    crate::process_group::kill_child_group(&mut child).await;
    let _ = child.wait().await;

    let models_array = result?;

    Ok(models_array
        .iter()
        .filter_map(|m| {
            let id = m.get("id")?.as_str()?.to_string();
            let label = m
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or(&id)
                .to_string();
            Some(ModelSuggestion {
                id,
                label,
                description: None,
                recommended: None,
            })
        })
        .collect())
}

async fn codex_models() -> Vec<ModelSuggestion> {
    match codex_models_inner().await {
        Ok(models) => models,
        Err(e) => {
            tracing::warn!("codex model/list failed: {e}");
            let cache = CACHE.lock().unwrap();
            cache
                .get("codex_sdk")
                .map(|e| e.models.clone())
                .unwrap_or_default()
        }
    }
}

async fn codex_models_inner() -> Result<Vec<ModelSuggestion>, String> {
    let mut cmd = tokio::process::Command::new("codex");
    cmd.args(["app-server", "--listen", "stdio://"]);
    cmd.stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    crate::process_group::configure_child_process(&mut cmd);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("failed to spawn codex: {e}"))?;

    let result = codex_rpc_exchange(&mut child).await;

    crate::process_group::kill_child_group(&mut child).await;
    let _ = child.wait().await;

    let data = result?;

    Ok(data
        .iter()
        .filter_map(|m| {
            let id = m.get("id")?.as_str()?.to_string();
            let label = m
                .get("name")
                .and_then(|v| v.as_str())
                .unwrap_or(&id)
                .to_string();
            Some(ModelSuggestion {
                id,
                label,
                description: None,
                recommended: None,
            })
        })
        .collect())
}

/// Codex uses newline-delimited JSON-RPC (not LSP framing).
async fn codex_rpc_exchange(
    child: &mut tokio::process::Child,
) -> Result<Vec<serde_json::Value>, String> {
    let request = r#"{"jsonrpc":"2.0","id":1,"method":"model/list","params":{}}"#;
    let mut line = request.to_string();
    line.push('\n');

    let stdin = child.stdin.as_mut().ok_or("no stdin")?;
    stdin
        .write_all(line.as_bytes())
        .await
        .map_err(|e| format!("write failed: {e}"))?;
    stdin
        .flush()
        .await
        .map_err(|e| format!("flush failed: {e}"))?;
    drop(child.stdin.take());

    let stdout = child.stdout.as_mut().ok_or("no stdout")?;
    let mut reader = tokio::io::BufReader::new(stdout);
    let mut response_line = String::new();
    tokio::time::timeout(
        Duration::from_secs(10),
        tokio::io::AsyncBufReadExt::read_line(&mut reader, &mut response_line),
    )
    .await
    .map_err(|_| "timeout reading codex response".to_string())?
    .map_err(|e| format!("read error: {e}"))?;

    let resp: serde_json::Value =
        serde_json::from_str(response_line.trim()).map_err(|e| format!("invalid JSON: {e}"))?;

    if let Some(error) = resp.get("error") {
        return Err(format!("RPC error: {error}"));
    }

    resp.pointer("/result/data")
        .and_then(|v| v.as_array())
        .cloned()
        .ok_or_else(|| "no /result/data array in response".to_string())
}

/// Send an LSP-framed JSON-RPC request to a child process and parse the response.
/// Returns the array at `result_pointer` on success.
async fn rpc_exchange_and_parse(
    child: &mut tokio::process::Child,
    method: &str,
    result_pointer: &str,
) -> Result<Vec<serde_json::Value>, String> {
    let request = format!(
        r#"{{"jsonrpc":"2.0","id":1,"method":"{}","params":{{}}}}"#,
        method
    );
    let framed = format!("Content-Length: {}\r\n\r\n{}", request.len(), request);

    let stdin = child.stdin.as_mut().ok_or("no stdin")?;
    stdin
        .write_all(framed.as_bytes())
        .await
        .map_err(|e| format!("write failed: {e}"))?;
    stdin
        .flush()
        .await
        .map_err(|e| format!("flush failed: {e}"))?;
    drop(child.stdin.take());

    let stdout = child.stdout.as_mut().ok_or("no stdout")?;
    let body = read_lsp_frame(stdout).await?;

    let resp: serde_json::Value =
        serde_json::from_str(&body).map_err(|e| format!("invalid JSON: {e}"))?;

    if let Some(error) = resp.get("error") {
        return Err(format!("RPC error: {error}"));
    }

    resp.pointer(result_pointer)
        .and_then(|v| v.as_array())
        .cloned()
        .ok_or_else(|| format!("no {result_pointer} array in response"))
}

async fn read_lsp_frame<R: AsyncReadExt + Unpin>(reader: &mut R) -> Result<String, String> {
    let mut header_buf = Vec::new();
    let mut b = [0u8; 1];
    let mut found_end = false;

    // Read until we find \r\n\r\n
    let deadline = Instant::now() + Duration::from_secs(10);
    while Instant::now() < deadline {
        match tokio::time::timeout(Duration::from_secs(10), reader.read(&mut b)).await {
            Ok(Ok(0)) => return Err("EOF before header complete".to_string()),
            Ok(Ok(_)) => {
                header_buf.push(b[0]);
                if header_buf.len() >= 4 && header_buf[header_buf.len() - 4..] == *b"\r\n\r\n" {
                    found_end = true;
                    break;
                }
            }
            Ok(Err(e)) => return Err(format!("read error: {e}")),
            Err(_) => return Err("timeout reading LSP header".to_string()),
        }
    }

    if !found_end {
        return Err("timeout waiting for LSP header".to_string());
    }

    let header_str =
        String::from_utf8(header_buf).map_err(|e| format!("invalid header UTF-8: {e}"))?;

    let content_length = header_str
        .lines()
        .find_map(|line| {
            let lower = line.to_lowercase();
            if lower.starts_with("content-length:") {
                lower
                    .strip_prefix("content-length:")
                    .and_then(|v| v.trim().parse::<usize>().ok())
            } else {
                None
            }
        })
        .ok_or("no Content-Length header")?;

    let mut body = vec![0u8; content_length];
    tokio::time::timeout(Duration::from_secs(10), reader.read_exact(&mut body))
        .await
        .map_err(|_| "timeout reading body".to_string())?
        .map_err(|e| format!("body read error: {e}"))?;

    String::from_utf8(body).map_err(|e| format!("invalid body UTF-8: {e}"))
}
