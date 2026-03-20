use anyhow::{Context, Result};
use rust_embed::RustEmbed;
use std::path::{Path, PathBuf};

#[derive(RustEmbed)]
#[folder = "../executors/dist/"]
#[include = "claude-executor.js"]
#[include = "copilot-executor.js"]
#[include = "common/*.js"]
pub struct EmbeddedExecutors;

/// Resolve the platform data directory for AgentBeacon.
/// Returns `$AGENTBEACON_DATA_DIR` if set, otherwise XDG data dir / "agentbeacon".
pub fn resolve_data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("AGENTBEACON_DATA_DIR") {
        return PathBuf::from(dir);
    }
    dirs::data_dir()
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var("HOME").unwrap_or_else(|_| "/tmp".into()))
                .join(".local/share")
        })
        .join("agentbeacon")
}

/// Extract embedded executor files to `data_dir/executors/` if needed.
/// Returns the path to the executors directory.
///
/// Skips extraction when `data_dir/.version` matches `CARGO_PKG_VERSION`.
/// Writes `.version` last for crash safety — incomplete extraction re-triggers next start.
pub fn extract_if_needed(data_dir: &Path) -> Result<PathBuf> {
    let executors_dir = data_dir.join("executors");
    let version_file = data_dir.join(".version");
    let current_version = env!("CARGO_PKG_VERSION");

    // Check version marker — skip extraction if versions match
    if let Ok(stored) = std::fs::read_to_string(&version_file)
        && stored.trim() == current_version
    {
        tracing::debug!("Embedded executors up to date (v{current_version})");
        return Ok(executors_dir);
    }

    tracing::info!("Extracting embedded executors to {}", data_dir.display());

    std::fs::create_dir_all(&executors_dir)
        .with_context(|| format!("failed to create {}", executors_dir.display()))?;

    // Extract all embedded executor files
    for filename in EmbeddedExecutors::iter() {
        let content = EmbeddedExecutors::get(&filename)
            .with_context(|| format!("embedded file missing: {filename}"))?;
        let dest = executors_dir.join(filename.as_ref());
        if let Some(parent) = dest.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&dest, content.data.as_ref())
            .with_context(|| format!("failed to write {}", dest.display()))?;
    }

    // Extract package.json and package-lock.json for future SDK install (KI-87).
    // Uses the real executors/ files — `npm ci --omit=dev` skips devDependencies.
    std::fs::write(
        data_dir.join("package.json"),
        include_str!("../../executors/package.json"),
    )
    .context("failed to write package.json")?;
    std::fs::write(
        data_dir.join("package-lock.json"),
        include_str!("../../executors/package-lock.json"),
    )
    .context("failed to write package-lock.json")?;

    // Write version marker LAST — crash safety
    std::fs::write(&version_file, current_version).context("failed to write .version marker")?;

    tracing::info!(
        "Extracted {} executor files (v{current_version})",
        EmbeddedExecutors::iter().count()
    );

    Ok(executors_dir)
}

// --- SDK installation (KI-87) ---

pub struct SdkPackage {
    pub driver: &'static str,
    pub npm_package: &'static str,
}

pub const SDK_PACKAGES: &[SdkPackage] = &[
    SdkPackage {
        driver: "claude",
        npm_package: "@anthropic-ai/claude-agent-sdk",
    },
    SdkPackage {
        driver: "copilot",
        npm_package: "@github/copilot-sdk",
    },
];

/// Check if an SDK is installed by reading its package.json in node_modules.
/// Returns the installed version, or None if not installed.
pub fn check_sdk_installed(data_dir: &Path, npm_package: &str) -> Option<String> {
    let pkg_json = data_dir
        .join("node_modules")
        .join(npm_package)
        .join("package.json");
    let content = std::fs::read_to_string(pkg_json).ok()?;
    let parsed: serde_json::Value = serde_json::from_str(&content).ok()?;
    parsed.get("version")?.as_str().map(|s| s.to_string())
}

/// Verify npm is available on PATH.
fn find_npm() -> Result<()> {
    let status = std::process::Command::new("npm")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status();
    match status {
        Ok(s) if s.success() => Ok(()),
        _ => anyhow::bail!("npm not found on PATH. Install Node.js from https://nodejs.org/"),
    }
}

/// Install all SDK dependencies from the extracted package.json and lockfile.
/// Uses `npm ci` for deterministic, lockfile-pinned installation.
pub fn install_sdks(data_dir: &Path) -> Result<()> {
    find_npm()?;

    tracing::info!("Installing executor SDK dependencies...");

    let status = std::process::Command::new("npm")
        .args(["ci", "--omit=dev"])
        .current_dir(data_dir)
        .status()
        .context("failed to run npm ci")?;

    if !status.success() {
        anyhow::bail!("npm ci failed with exit code {status}");
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_embedded_executors_contain_expected_files() {
        let files: Vec<String> = EmbeddedExecutors::iter().map(|f| f.to_string()).collect();

        // Production files must be present
        assert!(files.contains(&"claude-executor.js".to_string()));
        assert!(files.contains(&"copilot-executor.js".to_string()));
        assert!(files.contains(&"common/protocol.js".to_string()));
        assert!(files.contains(&"common/stdio-bridge.js".to_string()));

        // Mock files, source maps, and type declarations must be excluded
        for f in &files {
            assert!(!f.starts_with("mock-"), "mock file included: {f}");
            assert!(!f.ends_with(".js.map"), "source map included: {f}");
            assert!(!f.ends_with(".d.ts"), "type declaration included: {f}");
        }
    }

    #[test]
    fn test_extract_creates_files_and_version_marker() {
        let tmp = tempfile::tempdir().unwrap();
        let data_dir = tmp.path();

        let executors_dir = extract_if_needed(data_dir).unwrap();

        assert!(executors_dir.join("claude-executor.js").exists());
        assert!(executors_dir.join("copilot-executor.js").exists());
        assert!(executors_dir.join("common/protocol.js").exists());
        assert!(executors_dir.join("common/stdio-bridge.js").exists());
        assert!(data_dir.join("package.json").exists());
        assert!(data_dir.join("package-lock.json").exists());

        let version = std::fs::read_to_string(data_dir.join(".version")).unwrap();
        assert_eq!(version.trim(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn test_extract_skips_when_version_matches() {
        let tmp = tempfile::tempdir().unwrap();
        let data_dir = tmp.path();

        // First extraction
        extract_if_needed(data_dir).unwrap();

        // Record mtime of a file
        let executor_path = data_dir.join("executors/claude-executor.js");
        let mtime_before = std::fs::metadata(&executor_path)
            .unwrap()
            .modified()
            .unwrap();

        // Brief sleep to ensure filesystem timestamp granularity
        std::thread::sleep(std::time::Duration::from_millis(50));

        // Second extraction — should skip
        extract_if_needed(data_dir).unwrap();

        let mtime_after = std::fs::metadata(&executor_path)
            .unwrap()
            .modified()
            .unwrap();
        assert_eq!(
            mtime_before, mtime_after,
            "file was re-written despite matching version"
        );
    }

    #[test]
    fn test_extract_re_extracts_on_version_mismatch() {
        let tmp = tempfile::tempdir().unwrap();
        let data_dir = tmp.path();

        // First extraction
        extract_if_needed(data_dir).unwrap();

        // Tamper with version file
        std::fs::write(data_dir.join(".version"), "0.0.0-stale").unwrap();

        // Should re-extract
        extract_if_needed(data_dir).unwrap();

        let version = std::fs::read_to_string(data_dir.join(".version")).unwrap();
        assert_eq!(version.trim(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn test_check_sdk_not_installed() {
        let tmp = tempfile::tempdir().unwrap();
        assert_eq!(
            check_sdk_installed(tmp.path(), "@anthropic-ai/claude-agent-sdk"),
            None
        );
    }

    #[test]
    fn test_check_sdk_installed() {
        let tmp = tempfile::tempdir().unwrap();
        let pkg_dir = tmp
            .path()
            .join("node_modules/@anthropic-ai/claude-agent-sdk");
        std::fs::create_dir_all(&pkg_dir).unwrap();
        std::fs::write(pkg_dir.join("package.json"), r#"{"version":"1.2.3"}"#).unwrap();
        assert_eq!(
            check_sdk_installed(tmp.path(), "@anthropic-ai/claude-agent-sdk"),
            Some("1.2.3".to_string())
        );
    }

    #[test]
    fn test_sdk_packages_covers_drivers() {
        let drivers: Vec<&str> = SDK_PACKAGES.iter().map(|p| p.driver).collect();
        assert!(drivers.contains(&"claude"));
        assert!(drivers.contains(&"copilot"));
    }
}
