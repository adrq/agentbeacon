use serde_json::json;
use uuid::Uuid;

use crate::db::{DbPool, executions, workflows};
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;
use crate::task_preparation;
use crate::validation::SchemaValidator;
use common::dag::WorkflowDAG;

/// Build TaskAssignment with registry metadata
///
/// Extracts node from DAG, retrieves workflow metadata from database,
/// builds registry fields, auto-generates contextId if missing,
/// and validates task against A2A schema before queueing.
pub async fn build_task_assignment(
    pool: &DbPool,
    validator: &SchemaValidator,
    execution_id: &str,
    node_id: &str,
    dag: &WorkflowDAG,
) -> Result<TaskAssignment, SchedulerError> {
    let node = dag
        .tasks
        .get(node_id)
        .ok_or_else(|| SchedulerError::NotFound(format!("Node not found in DAG: {node_id}")))?;

    let exec_uuid = Uuid::parse_str(execution_id)
        .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid execution ID: {e}")))?;

    let execution = executions::get_by_id(pool, &exec_uuid).await?;
    let (registry_id, version, ref_str) = if let Some(namespace) = execution.workflow_namespace {
        let workflow = workflows::get_by_id(pool, &execution.workflow_id).await?;

        let ver = execution
            .workflow_version
            .unwrap_or_else(|| "unknown".to_string());

        (
            Some(format!("{}/{}", namespace, workflow.name)),
            Some(ver.clone()),
            Some(format!("{}/{}:{}", namespace, workflow.name, ver)),
        )
    } else {
        (None, None, None)
    };

    let mut task = node.task.clone();
    if task.get("contextId").is_none() {
        task["contextId"] = json!(Uuid::new_v4().to_string());
    }

    task_preparation::inject_runtime_message_fields(&mut task);

    validator.validate_task(&task)?;

    Ok(TaskAssignment {
        execution_id: execution_id.to_string(),
        node_id: node_id.to_string(),
        agent: node.agent.clone(),
        task,
        workflow_registry_id: registry_id,
        workflow_version: version,
        workflow_ref: ref_str,
        protocol_metadata: None,
    })
}
