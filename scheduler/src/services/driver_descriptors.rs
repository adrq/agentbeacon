use std::collections::HashMap;
use std::sync::LazyLock;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DriverDescriptor {
    pub platform: String,
    pub label: String,
    pub schema: serde_json::Value,
    pub fields: Vec<FieldAnnotation>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldAnnotation {
    pub pointer: String,
    pub label: String,
    pub help_text: Option<String>,
    pub group: String,
    pub widget: String,
    pub storage: String,
    pub support_status: serde_json::Value,
    pub secret: bool,
    pub suggestions_source: Option<String>,
}

static DESCRIPTORS: LazyLock<HashMap<String, DriverDescriptor>> = LazyLock::new(|| {
    let mut map = HashMap::new();
    for (platform, json_str) in [
        (
            "claude_sdk",
            include_str!("../../assets/driver-descriptors/claude_sdk.json"),
        ),
        (
            "copilot_sdk",
            include_str!("../../assets/driver-descriptors/copilot_sdk.json"),
        ),
        (
            "codex_sdk",
            include_str!("../../assets/driver-descriptors/codex_sdk.json"),
        ),
        (
            "acp",
            include_str!("../../assets/driver-descriptors/acp.json"),
        ),
    ] {
        let descriptor: DriverDescriptor = serde_json::from_str(json_str).unwrap_or_else(|e| {
            panic!("failed to parse descriptor for {platform}: {e}");
        });
        map.insert(platform.to_string(), descriptor);
    }
    map
});

pub fn get_descriptor(platform: &str) -> Option<&'static DriverDescriptor> {
    DESCRIPTORS.get(platform)
}

pub fn validate_config(platform: &str, config: &serde_json::Value) -> Result<(), String> {
    let descriptor = match get_descriptor(platform) {
        Some(d) => d,
        None => return Ok(()),
    };

    let validator = jsonschema::validator_for(&descriptor.schema)
        .map_err(|e| format!("internal error: invalid descriptor schema for {platform}: {e}"))?;

    let errors: Vec<String> = validator
        .iter_errors(config)
        .map(|e| {
            let path = e.instance_path.to_string();
            if path.is_empty() {
                e.to_string()
            } else {
                format!("{path}: {e}")
            }
        })
        .collect();

    if errors.is_empty() {
        Ok(())
    } else {
        Err(errors.join("; "))
    }
}
