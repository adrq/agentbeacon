// Embedded JSON Schemas for validation
// All schemas are embedded at compile time to eliminate runtime file dependencies

pub const AGENTS_SCHEMA: &str = include_str!("../../docs/agents-schema.json");
pub const A2A_SCHEMA: &str = include_str!("../../docs/a2a-v0.3.0.schema.json");
pub const SYNC_REQUEST_SCHEMA: &str = include_str!("../../docs/worker-sync-request.schema.json");
pub const SYNC_RESPONSE_SCHEMA: &str = include_str!("../../docs/worker-sync-response.schema.json");
