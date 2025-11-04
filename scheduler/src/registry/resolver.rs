use crate::db::{DbPool, WorkflowVersion, workflow_version};
use crate::error::SchedulerError;

/// Parse workflow reference into components
/// Format: "namespace/name:version" or "namespace/name" (defaults to :latest)
pub fn parse_workflow_ref(workflow_ref: &str) -> Result<(String, String, String), SchedulerError> {
    // Split by ':' to separate version
    let parts: Vec<&str> = workflow_ref.split(':').collect();

    let (namespace_name, version) = match parts.len() {
        1 => (parts[0], "latest"),
        2 => (parts[0], parts[1]),
        _ => {
            return Err(SchedulerError::ValidationFailed(format!(
                "parse workflow reference failed: invalid format '{workflow_ref}'. Expected 'namespace/name:version'"
            )));
        }
    };

    // Split namespace/name
    let ns_parts: Vec<&str> = namespace_name.split('/').collect();
    if ns_parts.len() != 2 {
        return Err(SchedulerError::ValidationFailed(format!(
            "parse workflow reference failed: invalid format '{workflow_ref}'. Expected 'namespace/name:version'"
        )));
    }

    let namespace = ns_parts[0].to_string();
    let name = ns_parts[1].to_string();
    let version = version.to_string();

    // Validate namespace format (FR-022)
    if !namespace
        .chars()
        .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_' || c == '-')
    {
        return Err(SchedulerError::ValidationFailed(format!(
            "validation failed: invalid namespace '{namespace}'. Must match pattern ^[a-z0-9_-]+$"
        )));
    }

    Ok((namespace, name, version))
}

/// Resolve workflow reference to WorkflowVersion
/// Handles "latest" version resolution and provides descriptive errors (FR-035, FR-040)
pub async fn resolve_workflow_ref(
    pool: &DbPool,
    workflow_ref: &str,
) -> Result<WorkflowVersion, SchedulerError> {
    let (namespace, name, version) = parse_workflow_ref(workflow_ref)?;

    // Query database for workflow version
    let result = workflow_version::get_by_ref(pool, &namespace, &name, &version).await?;

    match result {
        Some(wf) => Ok(wf),
        None => {
            // Check if workflow exists with different version
            let versions = workflow_version::list_versions(pool, &namespace, &name).await?;

            if versions.is_empty() {
                // No workflow found at all (FR-035)
                Err(SchedulerError::NotFound(format!(
                    "workflow not found: {workflow_ref}"
                )))
            } else if version == "latest" {
                // No is_latest=true version found (FR-040)
                let available = versions
                    .iter()
                    .map(|v| v.version.clone())
                    .collect::<Vec<_>>()
                    .join(", ");
                Err(SchedulerError::ValidationFailed(format!(
                    "resolve workflow reference failed: ambiguous '{workflow_ref}'. Multiple versions found but no version marked as 'latest'. Available versions: {available}. Please specify version explicitly (e.g., '{namespace}/{name}:v1.0.0')"
                )))
            } else {
                // Specific version not found but others exist
                let available = versions
                    .iter()
                    .map(|v| v.version.clone())
                    .collect::<Vec<_>>()
                    .join(", ");
                Err(SchedulerError::NotFound(format!(
                    "workflow version not found: '{workflow_ref}'. Available versions: {available}"
                )))
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_workflow_ref_with_version() {
        let (ns, name, ver) = parse_workflow_ref("team/auth:v1.2.3").unwrap();
        assert_eq!(ns, "team");
        assert_eq!(name, "auth");
        assert_eq!(ver, "v1.2.3");
    }

    #[test]
    fn test_parse_workflow_ref_latest_default() {
        let (ns, name, ver) = parse_workflow_ref("team/auth").unwrap();
        assert_eq!(ns, "team");
        assert_eq!(name, "auth");
        assert_eq!(ver, "latest");
    }

    #[test]
    fn test_parse_workflow_ref_with_latest_explicit() {
        let (ns, name, ver) = parse_workflow_ref("team/auth:latest").unwrap();
        assert_eq!(ns, "team");
        assert_eq!(name, "auth");
        assert_eq!(ver, "latest");
    }

    #[test]
    fn test_parse_workflow_ref_invalid_format_missing_name() {
        let result = parse_workflow_ref("team:v1.2.3");
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("parse workflow reference failed")
        );
    }

    #[test]
    fn test_parse_workflow_ref_invalid_format_too_many_colons() {
        let result = parse_workflow_ref("team/auth:v1:v2");
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("parse workflow reference failed")
        );
    }

    #[test]
    fn test_parse_workflow_ref_invalid_namespace_uppercase() {
        let result = parse_workflow_ref("Team/auth:v1.2.3");
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("invalid namespace")
        );
    }

    #[test]
    fn test_parse_workflow_ref_valid_namespace_with_dash_underscore() {
        let (ns, _, _) = parse_workflow_ref("team-123_test/auth:v1.2.3").unwrap();
        assert_eq!(ns, "team-123_test");
    }

    #[test]
    fn test_parse_workflow_ref_invalid_namespace_special_char() {
        let result = parse_workflow_ref("team@test/auth:v1.2.3");
        assert!(result.is_err());
        assert!(
            result
                .unwrap_err()
                .to_string()
                .contains("invalid namespace")
        );
    }

    #[test]
    fn test_workflow_ref_roundtrip() {
        // Test that assignment.rs format matches parser expectations
        let namespace = "team";
        let name = "auth";
        let version = "v1.2.3";

        // Simulate assignment.rs format (after fix)
        let workflow_ref = format!("{namespace}/{name}:{version}");

        // Parse it back
        let (parsed_ns, parsed_name, parsed_ver) = parse_workflow_ref(&workflow_ref).unwrap();

        assert_eq!(parsed_ns, namespace);
        assert_eq!(parsed_name, name);
        assert_eq!(parsed_ver, version);
    }
}
