use serde::Serialize;
use serde_json::json;

use crate::api::problem::{Problem, ProblemCode};
use crate::api::versions::{EVENT_PAYLOAD_BUDGET_BYTES, EVENTS_DEFAULT_PAGE, EVENTS_MAX_PAGE};
use crate::db;
use crate::error::SchedulerError;

/// Shared execution response shape used by list, detail, and create endpoints.
#[derive(Debug, Serialize)]
pub struct ExecutionResponse {
    pub id: String,
    pub project_id: Option<String>,
    pub parent_execution_id: Option<String>,
    pub context_id: String,
    pub desired: String,
    pub outcome: Option<String>,
    /// Display status derived on read.
    pub status: String,
    /// Whether the execution can be completed (vs canceled).
    pub completion_eligible: bool,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
    pub max_depth: i64,
    pub max_width: i64,
    pub sandbox_policy: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

/// Derived execution fields computed from session tree state.
pub struct ExecutionDerived {
    pub status: String,
    pub completion_eligible: bool,
}

impl ExecutionResponse {
    /// Derive from paired (session, pending_turns) snapshot — single-query path.
    pub fn derive_from_snapshot(
        exec: &db::Execution,
        snapshot: &[(db::sessions::Session, i64)],
    ) -> ExecutionDerived {
        if let Some(ref outcome) = exec.outcome {
            return ExecutionDerived {
                status: outcome.clone(),
                completion_eligible: false,
            };
        }
        if exec.desired == "terminate" {
            return ExecutionDerived {
                status: "canceled".to_string(),
                completion_eligible: false,
            };
        }

        let any_active = snapshot
            .iter()
            .any(|(s, pending)| crate::services::transition::is_active(s, *pending));

        if any_active {
            return ExecutionDerived {
                status: "working".to_string(),
                completion_eligible: false,
            };
        }

        let root = snapshot.iter().find(|(s, _)| s.parent_session_id.is_none());
        let root_alive = root.is_some_and(|(r, _)| r.outcome.is_none());

        let all_quiescent = snapshot.iter().all(|(s, pending)| {
            s.outcome.is_some() || crate::services::transition::is_quiescent(s, *pending)
        });

        let no_unnotified_failure = !snapshot.iter().any(|(s, _)| {
            s.outcome.as_deref() == Some("failed")
                && !s.parent_notified
                && s.parent_session_id.as_ref().is_some_and(|pid| {
                    snapshot
                        .iter()
                        .find(|(p, _)| p.id == *pid)
                        .is_some_and(|(p, _)| p.outcome.is_none())
                })
        });

        let eligible = root_alive && all_quiescent && no_unnotified_failure;

        ExecutionDerived {
            status: "awaiting_input".to_string(),
            completion_eligible: eligible,
        }
    }

    /// Derive from separate session and pending_counts arrays (list endpoint path).
    pub fn derive(
        exec: &db::Execution,
        sessions: &[db::sessions::Session],
        pending_counts: &[(String, i64)],
    ) -> ExecutionDerived {
        if let Some(ref outcome) = exec.outcome {
            return ExecutionDerived {
                status: outcome.clone(),
                completion_eligible: false,
            };
        }
        if exec.desired == "terminate" {
            return ExecutionDerived {
                status: "canceled".to_string(),
                completion_eligible: false,
            };
        }

        let pending_for = |sid: &str| -> i64 {
            pending_counts
                .iter()
                .find(|(s, _)| s == sid)
                .map(|(_, c)| *c)
                .unwrap_or(0)
        };

        let any_active = sessions
            .iter()
            .any(|s| crate::services::transition::is_active(s, pending_for(&s.id)));

        if any_active {
            return ExecutionDerived {
                status: "working".to_string(),
                completion_eligible: false,
            };
        }

        let root = sessions.iter().find(|s| s.parent_session_id.is_none());

        let root_alive = root.is_some_and(|r| r.outcome.is_none());

        let all_quiescent = sessions.iter().all(|s| {
            s.outcome.is_some() || crate::services::transition::is_quiescent(s, pending_for(&s.id))
        });

        let no_unnotified_failure = !sessions.iter().any(|s| {
            s.outcome.as_deref() == Some("failed")
                && !s.parent_notified
                && s.parent_session_id.as_ref().is_some_and(|pid| {
                    sessions
                        .iter()
                        .find(|p| p.id == *pid)
                        .is_some_and(|p| p.outcome.is_none())
                })
        });

        let eligible = root_alive && all_quiescent && no_unnotified_failure;

        ExecutionDerived {
            status: "awaiting_input".to_string(),
            completion_eligible: eligible,
        }
    }
}

/// Parse sandbox_policy JSON string, returning error on malformed data.
fn parse_sandbox_policy_json(raw: &str) -> Result<serde_json::Value, SchedulerError> {
    let policy = crate::services::sandbox::parse_sandbox_policy(raw)?;
    crate::services::sandbox::build_sandbox_driver_config(&policy)
}

impl ExecutionResponse {
    /// Fallible conversion — propagates malformed sandbox_policy as internal error.
    pub fn try_from_execution(e: db::Execution) -> Result<Self, SchedulerError> {
        let metadata = serde_json::from_str(&e.metadata).unwrap_or_else(|_| serde_json::json!({}));
        let sandbox_policy = parse_sandbox_policy_json(&e.sandbox_policy)?;
        let status = if let Some(ref outcome) = e.outcome {
            outcome.clone()
        } else if e.desired == "terminate" {
            "canceled".to_string()
        } else {
            "awaiting_input".to_string()
        };
        Ok(Self {
            id: e.id,
            project_id: e.project_id,
            parent_execution_id: e.parent_execution_id,
            context_id: e.context_id,
            desired: e.desired,
            outcome: e.outcome,
            status,
            completion_eligible: false,
            title: e.title,
            metadata,
            max_depth: e.max_depth,
            max_width: e.max_width,
            sandbox_policy,
            created_at: e.created_at.to_rfc3339(),
            updated_at: e.updated_at.to_rfc3339(),
            completed_at: e.completed_at.map(|dt| dt.to_rfc3339()),
        })
    }
}

impl From<db::Execution> for ExecutionResponse {
    fn from(e: db::Execution) -> Self {
        match Self::try_from_execution(e) {
            Ok(resp) => resp,
            Err(err) => {
                tracing::error!("malformed execution sandbox_policy: {err}");
                unreachable!("invalid sandbox_policy: {err}");
            }
        }
    }
}

/// Derive session display status from internal state.
pub fn derive_session_display_status(s: &db::sessions::Session, pending_turns: i64) -> String {
    if let Some(ref outcome) = s.outcome {
        return outcome.clone();
    }
    if crate::services::transition::is_active(s, pending_turns) {
        return "working".to_string();
    }
    if s.desired == "stop" {
        return "stopped".to_string();
    }
    match s.executor_state.as_str() {
        "idle" => "idle".to_string(),
        "unassigned" => "unassigned".to_string(),
        "crashed" => "crashed".to_string(),
        _ => "unassigned".to_string(),
    }
}

/// Fallback derivation without pending_turns data (used by From<Session>).
/// Covers most cases but lacks pending_turns data.
fn derive_session_display_status_fallback(s: &db::sessions::Session) -> String {
    if let Some(ref outcome) = s.outcome {
        return outcome.clone();
    }
    if s.executor_state == "running" || s.command_token.is_some() {
        return "working".to_string();
    }
    if s.desired == "stop" {
        return "stopped".to_string();
    }
    match s.executor_state.as_str() {
        "idle" => "idle".to_string(),
        "unassigned" => "unassigned".to_string(),
        "crashed" => "crashed".to_string(),
        _ => "unassigned".to_string(),
    }
}

/// Shared session response shape.
#[derive(Debug, Serialize)]
pub struct SessionResponse {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub agent_session_id: Option<String>,
    pub cwd: Option<String>,
    pub worktree_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_commit_sha: Option<String>,
    pub desired: String,
    pub executor_state: String,
    pub outcome: Option<String>,
    /// Server-derived display status.
    pub status: String,
    pub desired_by: Option<String>,
    pub worker_id: Option<String>,
    pub command_type: Option<String>,
    pub parent_notified: bool,
    pub recovery_attempts: i64,
    pub metadata: serde_json::Value,
    pub sandbox_policy: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl SessionResponse {
    /// Fallible conversion — propagates malformed sandbox_policy as internal error.
    pub fn try_from_session(s: db::sessions::Session) -> Result<Self, SchedulerError> {
        let metadata = serde_json::from_str(&s.metadata).unwrap_or_else(|_| serde_json::json!({}));
        let sandbox_policy = parse_sandbox_policy_json(&s.sandbox_policy)?;
        let status = derive_session_display_status_fallback(&s);
        Ok(Self {
            id: s.id,
            execution_id: s.execution_id,
            parent_session_id: s.parent_session_id,
            agent_id: s.agent_id,
            agent_session_id: s.agent_session_id,
            cwd: s.cwd,
            worktree_path: s.worktree_path,
            base_commit_sha: s.base_commit_sha,
            desired: s.desired,
            executor_state: s.executor_state,
            outcome: s.outcome,
            status,
            desired_by: s.desired_by,
            worker_id: s.worker_id,
            command_type: s.command_type,
            parent_notified: s.parent_notified,
            recovery_attempts: s.recovery_attempts,
            metadata,
            sandbox_policy,
            created_at: s.created_at.to_rfc3339(),
            updated_at: s.updated_at.to_rfc3339(),
            completed_at: s.completed_at.map(|dt| dt.to_rfc3339()),
        })
    }
}

impl From<db::sessions::Session> for SessionResponse {
    fn from(s: db::sessions::Session) -> Self {
        match Self::try_from_session(s) {
            Ok(resp) => resp,
            Err(err) => {
                tracing::error!("malformed session sandbox_policy: {err}");
                unreachable!("invalid sandbox_policy: {err}");
            }
        }
    }
}

/// A page of items with an opaque continuation cursor.
#[derive(Debug, Serialize)]
pub struct PageV1<T> {
    pub items: Vec<T>,
    pub next_cursor: Option<String>,
    pub has_more: bool,
}

/// Shared event response shape.
#[derive(Debug, Serialize)]
pub struct EventV1 {
    pub id: String,
    pub execution_id: String,
    pub session_id: Option<String>,
    pub event_type: String,
    pub payload: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub truncated: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub byte_size: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub omitted_paths: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub msg_seq: Option<i64>,
    pub created_at: String,
}

/// The `event_type` an escalation is served under.
fn surfaced_event_type(stored: String, payload: &serde_json::Value) -> String {
    if stored != "platform" {
        return stored;
    }
    let data_type = payload
        .get("parts")
        .and_then(|p| p.get(0))
        .and_then(|p| p.get("data"))
        .and_then(|d| d.get("type"))
        .and_then(|t| t.as_str());
    if data_type == Some(crate::resolution::ESCALATE_TYPE) {
        crate::resolution::ESCALATE_TYPE.to_string()
    } else {
        stored
    }
}

impl EventV1 {
    /// Build a record whose payload is served exactly as stored.
    pub fn full(e: db::events::Event) -> Self {
        let payload = serde_json::from_str(&e.payload).unwrap_or(json!(e.payload));
        let event_type = surfaced_event_type(e.event_type, &payload);
        Self {
            id: e.id.to_string(),
            execution_id: e.execution_id,
            session_id: e.session_id,
            event_type,
            payload,
            truncated: None,
            byte_size: None,
            omitted_paths: None,
            msg_seq: e.msg_seq,
            created_at: e.created_at.to_rfc3339(),
        }
    }
}

impl From<db::events::Event> for EventV1 {
    fn from(e: db::events::Event) -> Self {
        let stored_len = e.payload.len();
        let mut record = Self::full(e);
        if stored_len <= EVENT_PAYLOAD_BUDGET_BYTES {
            return record;
        }
        let outcome = payload_budget::apply(record.payload);
        record.payload = outcome.payload;
        if outcome.changed || outcome.final_size > EVENT_PAYLOAD_BUDGET_BYTES {
            record.truncated = Some(true);
            record.byte_size = Some(stored_len as u64);
        }
        if !outcome.omitted_paths.is_empty() {
            record.omitted_paths = Some(outcome.omitted_paths);
        }
        record
    }
}

/// Serving-time shortening of oversized event payloads.
///
/// Structure, keys, array membership and every non-string value are preserved;
/// only string values are shortened, and base64 attachment leaves are emptied
/// rather than cut.
pub mod payload_budget {
    use serde_json::Value;

    use crate::api::versions::{EVENT_PAYLOAD_BUDGET_BYTES, EVENT_STRING_LEAF_MAX_BYTES};

    /// Bound on ceiling recomputations.
    const MAX_PASSES: usize = 32;

    /// Leaves this size or smaller are never shortened.
    const LEAF_PROTECTION_FLOOR_BYTES: usize = 256;

    /// The result of shortening one payload.
    pub struct Outcome {
        pub payload: Value,
        pub omitted_paths: Vec<String>,
        /// True when the served payload differs from the stored one.
        pub changed: bool,
        /// Serialized size of the served payload.
        pub final_size: usize,
    }

    #[derive(Clone, Debug, PartialEq, Eq)]
    enum Seg {
        Key(String),
        Index(usize),
    }

    fn format_path(path: &[Seg]) -> String {
        let mut out = String::new();
        for seg in path {
            match seg {
                Seg::Key(k) => {
                    if !out.is_empty() {
                        out.push('.');
                    }
                    out.push_str(k);
                }
                Seg::Index(i) => out.push_str(&format!("[{i}]")),
            }
        }
        out
    }

    /// True for the attachment paths whose values are base64 and must never be cut.
    fn is_attachment(path: &[Seg]) -> bool {
        match path {
            [Seg::Key(parts), Seg::Index(_), Seg::Key(raw)] if parts == "parts" && raw == "raw" => {
                true
            }
            [
                Seg::Key(content),
                Seg::Index(_),
                Seg::Key(source),
                Seg::Key(data),
            ] if content == "content" && source == "source" && data == "data" => true,
            _ => false,
        }
    }

    fn collect(value: &Value, path: &mut Vec<Seg>, out: &mut Vec<(Vec<Seg>, usize)>) {
        match value {
            Value::String(s) => out.push((path.clone(), s.len())),
            Value::Array(items) => {
                for (i, item) in items.iter().enumerate() {
                    path.push(Seg::Index(i));
                    collect(item, path, out);
                    path.pop();
                }
            }
            Value::Object(map) => {
                for (key, item) in map {
                    path.push(Seg::Key(key.clone()));
                    collect(item, path, out);
                    path.pop();
                }
            }
            _ => {}
        }
    }

    fn at_mut<'a>(value: &'a mut Value, path: &[Seg]) -> Option<&'a mut Value> {
        let mut cursor = value;
        for seg in path {
            cursor = match seg {
                Seg::Key(k) => cursor.as_object_mut()?.get_mut(k)?,
                Seg::Index(i) => cursor.as_array_mut()?.get_mut(*i)?,
            };
        }
        Some(cursor)
    }

    /// The largest per-leaf ceiling whose retained total stays within `allowed`.
    fn shared_ceiling(lengths: &[(Vec<Seg>, usize)], allowed: usize) -> usize {
        let mut sorted: Vec<usize> = lengths.iter().map(|(_, len)| *len).collect();
        sorted.sort_unstable();
        let mut below = 0usize;
        for (index, len) in sorted.iter().enumerate() {
            let remaining = sorted.len() - index;
            let Some(headroom) = allowed.checked_sub(below) else {
                return 0;
            };
            let ceiling = headroom / remaining;
            if ceiling <= *len {
                return ceiling;
            }
            below += len;
        }
        sorted.last().copied().unwrap_or(0)
    }

    /// The longest prefix of `s` not exceeding `target` bytes that ends on a
    /// character boundary.
    fn cut(s: &str, target: usize) -> &str {
        if s.len() <= target {
            return s;
        }
        let mut end = target;
        while end > 0 && !s.is_char_boundary(end) {
            end -= 1;
        }
        &s[..end]
    }

    /// Shorten `payload` toward the serving budget.
    pub fn apply(payload: Value) -> Outcome {
        let original = payload.clone();
        let mut payload = payload;
        let mut omitted_paths = Vec::new();

        let mut leaves = Vec::new();
        collect(&payload, &mut Vec::new(), &mut leaves);

        for (path, len) in &leaves {
            if *len > 0
                && is_attachment(path)
                && let Some(slot) = at_mut(&mut payload, path)
            {
                *slot = Value::String(String::new());
                omitted_paths.push(format_path(path));
            }
        }

        let mut lengths: Vec<(Vec<Seg>, usize)> = leaves
            .into_iter()
            .filter(|(path, len)| !is_attachment(path) && *len > LEAF_PROTECTION_FLOOR_BYTES)
            .collect();

        for (path, len) in lengths.iter_mut() {
            if *len > EVENT_STRING_LEAF_MAX_BYTES
                && let Some(slot) = at_mut(&mut payload, path)
                && let Some(text) = slot.as_str()
            {
                let shortened = cut(text, EVENT_STRING_LEAF_MAX_BYTES).to_string();
                *len = shortened.len();
                *slot = Value::String(shortened);
            }
        }

        let mut within_budget = false;
        for _ in 0..MAX_PASSES {
            let size = serde_json::to_string(&payload)
                .map(|s| s.len())
                .unwrap_or(0);
            if size <= EVENT_PAYLOAD_BUDGET_BYTES {
                within_budget = true;
                break;
            }
            let retained: usize = lengths.iter().map(|(_, len)| *len).sum();
            if retained == 0 {
                break;
            }
            let overflow = size - EVENT_PAYLOAD_BUDGET_BYTES;
            let allowed = retained.saturating_sub(overflow);
            let ceiling = shared_ceiling(&lengths, allowed);

            let mut shortened_any = false;
            for (path, len) in lengths.iter_mut() {
                if *len <= ceiling {
                    continue;
                }
                let Some(slot) = at_mut(&mut payload, path) else {
                    *len = 0;
                    continue;
                };
                let Some(text) = slot.as_str() else {
                    *len = 0;
                    continue;
                };
                let shortened = cut(text, ceiling).to_string();
                shortened_any |= shortened.len() < *len;
                *len = shortened.len();
                *slot = Value::String(shortened);
            }
            if !shortened_any {
                for (path, len) in lengths.iter_mut() {
                    if *len == 0 {
                        continue;
                    }
                    if let Some(slot) = at_mut(&mut payload, path) {
                        *slot = Value::String(String::new());
                    }
                    *len = 0;
                }
            }
        }

        if !within_budget {
            let final_size = serde_json::to_string(&original)
                .map(|s| s.len())
                .unwrap_or(0);
            return Outcome {
                payload: original,
                omitted_paths: Vec::new(),
                changed: false,
                final_size,
            };
        }

        let final_size = serde_json::to_string(&payload)
            .map(|s| s.len())
            .unwrap_or(0);
        let changed = payload != original;
        Outcome {
            payload,
            omitted_paths,
            changed,
            final_size,
        }
    }
}

/// Query parameters shared by the bounded event reads.
#[derive(Debug, serde::Deserialize)]
pub struct EventPageQuery {
    pub before: Option<String>,
    pub after: Option<String>,
    pub limit: Option<i64>,
}

/// The window one bounded event read selects.
pub enum EventWindow {
    Before(Option<i64>),
    After(i64),
}

impl EventPageQuery {
    /// Resolve the requested window and page size, or the problem that rejects them.
    pub fn resolve(&self) -> Result<(EventWindow, i64), SchedulerError> {
        let invalid = |detail: &str| {
            SchedulerError::Problem(Box::new(
                Problem::new(ProblemCode::RequestInvalid).with_detail(detail.to_string()),
            ))
        };
        if self.before.is_some() && self.after.is_some() {
            return Err(invalid("before and after cannot be combined"));
        }
        let parse = |value: &str| {
            value
                .parse::<i64>()
                .ok()
                .filter(|v| *v > 0)
                .ok_or_else(|| invalid("cursor is not a valid event id"))
        };
        let window = match (&self.before, &self.after) {
            (Some(before), _) => EventWindow::Before(Some(parse(before)?)),
            (_, Some(after)) => EventWindow::After(parse(after)?),
            _ => EventWindow::Before(None),
        };
        let limit = match self.limit {
            None => EVENTS_DEFAULT_PAGE,
            Some(n) if (1..=EVENTS_MAX_PAGE).contains(&n) => n,
            Some(_) => {
                return Err(invalid(
                    "limit must be between 1 and the advertised maximum",
                ));
            }
        };
        Ok((window, limit))
    }
}

/// Wrap a scanned window of events in the page envelope.
///
/// The scan is ascending by id and must have been taken with `limit + 1` rows.
pub fn event_page(
    scan: db::events::EventScan,
    limit: i64,
    window: &EventWindow,
) -> PageV1<EventV1> {
    let db::events::EventScan { events, ids } = scan;
    let has_more = ids.len() as i64 > limit;

    let (probe, next_cursor) = if has_more {
        match window {
            EventWindow::Before(_) => (ids.first().copied(), ids.get(1).copied()),
            EventWindow::After(_) => (
                ids.last().copied(),
                ids.get(ids.len().saturating_sub(2)).copied(),
            ),
        }
    } else {
        (None, None)
    };

    let items: Vec<EventV1> = events
        .into_iter()
        .filter(|e| Some(e.id) != probe)
        .map(EventV1::from)
        .collect();
    PageV1 {
        items,
        next_cursor: next_cursor.map(|id| id.to_string()),
        has_more,
    }
}
