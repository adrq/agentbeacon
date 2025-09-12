pub mod a2a;
pub mod schemas;
pub mod validation;

pub use a2a::{A2AArtifact, A2ATaskStatus, Message, Part};
pub use validation::{
    ValidationError, validate_a2a_request, validate_agents_config, validate_sync_request,
    validate_sync_response,
};
