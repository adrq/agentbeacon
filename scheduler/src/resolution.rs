//! Shared parsing for escalation ownership and question-resolution markers on the decisions
//! read path.

use chrono::DateTime;
use serde_json::{Value, json};

/// Top-level `role` value for agent-authored events.
pub const ROLE_AGENT: &str = "ROLE_AGENT";
/// Top-level `role` value for user-authored events.
pub const ROLE_USER: &str = "ROLE_USER";

/// `data.type` of an escalation part.
pub const ESCALATE_TYPE: &str = "escalate";
/// `data.type` of an answer resolution marker.
pub const ANSWER_TYPE: &str = "question_answer";
/// `data.type` of a dismissal marker.
pub const DISMISS_TYPE: &str = "question_dismiss";
/// `data.type` of a sender annotation.
pub const SENDER_TYPE: &str = "sender";

/// Upper bound on a fully serialized resolution marker payload, in bytes.
pub const MARKER_SERIALIZED_CAP_BYTES: usize = 96 * 1024;

const RESERVED_EVENT_ID: i64 = i64::MAX;
const RESERVED_TIMESTAMP: &str = "9999-12-31T23:59:59Z";

/// True when `s` is a resolved_at the read path can parse AND is exactly the reserved width.
pub fn resolved_at_is_representable(s: &str) -> bool {
    s.len() == RESERVED_TIMESTAMP.len() && DateTime::parse_from_rfc3339(s).is_ok()
}

/// Resolution ordering key; comparison is lexicographic over its fields.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct OrderKey {
    pub primary_id: i64,
    pub own_event_id: i64,
    pub part_ordinal: i64,
}

/// Whether a resolution candidate answers or dismisses its batch.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResolutionKind {
    Answer,
    Dismiss,
}

/// One validated resolution candidate emitted from a platform event.
#[derive(Clone, Debug)]
pub struct ResolutionCandidate {
    pub kind: ResolutionKind,
    pub batch_id: String,
    pub order_key: OrderKey,
    /// Answer text, or `None` when the marker omits it (API returns `answer: null`).
    pub answer_text: Option<String>,
    /// Raw `resolved_at` string as stored, if any.
    pub resolved_at_raw: Option<String>,
    /// The validated embedded `resolved_event_id`, if present and valid.
    pub embedded_resolved_event_id: Option<i64>,
    /// True when this answer's text was truncated.
    pub truncated: bool,
}

/// An eligible escalation part that can bind batch ownership.
#[derive(Clone, Debug)]
pub struct EscalatePart {
    pub batch_id: String,
    pub part_ordinal: i64,
}

/// Facts extracted from a message-lane answer source (backfill/detector candidate).
#[derive(Clone, Debug)]
pub struct AnswerSource {
    pub batch_id: String,
    /// Answer text under the single empty/absent rule.
    pub answer_text: Option<String>,
}

/// Outcome of validating an inbound `question_answer` on the live answer route.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AnswerValidation {
    /// Not an answer request — no `question_answer` part present.
    NotAnAnswer,
    /// A single valid `question_answer` part for this batch.
    Valid {
        batch_id: String,
        answer_text: Option<String>,
    },
    /// More than one `question_answer` part.
    MultipleParts,
    /// A `question_answer` part with an absent or non-string `batch_id`.
    MissingBatchId,
    /// A `question_answer` request that also carries a `sender` part.
    SenderPresent,
    /// A `question_answer` request whose parts violate the object-shape domain.
    MalformedParts,
}

/// True if every `parts` element is a JSON object and every present `data` member is an object.
fn parts_domain_valid(parts: &[Value]) -> bool {
    parts
        .iter()
        .all(|p| p.is_object() && p.get("data").is_none_or(|d| d.is_object()))
}

/// Parse a payload string into its top-level `parts` array, if present and domain-valid.
pub fn parse_parts(payload: &str) -> Option<Vec<Value>> {
    let mut value: Value = serde_json::from_str(payload).ok()?;
    let arr = value.get_mut("parts")?.as_array_mut()?;
    if !parts_domain_valid(arr) {
        return None;
    }
    Some(std::mem::take(arr))
}

/// The top-level `role` string of a parsed payload, if present.
pub fn top_role(payload: &str) -> Option<String> {
    let value: Value = serde_json::from_str(payload).ok()?;
    value.get("role").and_then(|r| r.as_str()).map(String::from)
}

/// Parse a payload ONCE into its top-level `role` and domain-valid `parts`, if present.
pub fn parse_role_and_parts(payload: &str) -> Option<(Option<String>, Vec<Value>)> {
    let mut value: Value = serde_json::from_str(payload).ok()?;
    let role = value.get("role").and_then(|r| r.as_str()).map(String::from);
    let arr = value.get_mut("parts")?.as_array_mut()?;
    if !parts_domain_valid(arr) {
        return None;
    }
    Some((role, std::mem::take(arr)))
}

fn part_data(part: &Value) -> Option<&Value> {
    part.get("data")
}

fn part_data_type(part: &Value) -> Option<&str> {
    part_data(part)?.get("type").and_then(|t| t.as_str())
}

/// Validated embedded `resolved_event_id`: a positive JSON integer within i64 range.
pub fn embedded_resolved_event_id(data: &Value) -> Option<i64> {
    match data.get("resolved_event_id") {
        Some(Value::Number(n)) => n.as_i64().filter(|v| *v > 0),
        _ => None,
    }
}

/// Answer text extraction under the single empty/absent rule.
pub fn answer_text_from_parts(parts: &[Value]) -> Option<String> {
    parts
        .iter()
        .find_map(|p| p.get("text").and_then(|t| t.as_str()))
        .filter(|s| !s.is_empty())
        .map(String::from)
}

/// True if any part is a `sender` data annotation.
pub fn has_sender_part(parts: &[Value]) -> bool {
    parts.iter().any(|p| part_data_type(p) == Some(SENDER_TYPE))
}

fn answer_part_count(parts: &[Value]) -> usize {
    parts
        .iter()
        .filter(|p| part_data_type(p) == Some(ANSWER_TYPE))
        .count()
}

/// Escalation parts of an agent platform event, ordered by part ordinal.
pub fn escalate_parts(payload: &str) -> Vec<EscalatePart> {
    if top_role(payload).as_deref() != Some(ROLE_AGENT) {
        return Vec::new();
    }
    let Some(parts) = parse_parts(payload) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (ordinal, part) in parts.iter().enumerate() {
        let Some(data) = part_data(part) else {
            continue;
        };
        if data.get("type").and_then(|t| t.as_str()) != Some(ESCALATE_TYPE) {
            continue;
        }
        if let Some(batch_id) = data.get("batch_id").and_then(|b| b.as_str()) {
            out.push(EscalatePart {
                batch_id: batch_id.to_string(),
                part_ordinal: ordinal as i64,
            });
        }
    }
    out
}

/// Resolution candidates (answers/dismissals) emitted by a platform event's parts.
pub fn resolution_candidates(parts: &[Value], own_event_id: i64) -> Vec<ResolutionCandidate> {
    if answer_part_count(parts) > 1 {
        return Vec::new();
    }
    let mut out = Vec::new();
    for (ordinal, part) in parts.iter().enumerate() {
        let Some(data) = part_data(part) else {
            continue;
        };
        let data_type = data.get("type").and_then(|t| t.as_str());
        let Some(batch_id) = data.get("batch_id").and_then(|b| b.as_str()) else {
            continue;
        };
        match data_type {
            Some(ANSWER_TYPE) => {
                let embedded = embedded_resolved_event_id(data);
                let primary_id = embedded.unwrap_or(own_event_id);
                out.push(ResolutionCandidate {
                    kind: ResolutionKind::Answer,
                    batch_id: batch_id.to_string(),
                    order_key: OrderKey {
                        primary_id,
                        own_event_id,
                        part_ordinal: ordinal as i64,
                    },
                    answer_text: data
                        .get("answer_text")
                        .and_then(|t| t.as_str())
                        .filter(|s| !s.is_empty())
                        .map(String::from),
                    resolved_at_raw: data
                        .get("resolved_at")
                        .and_then(|t| t.as_str())
                        .map(String::from),
                    embedded_resolved_event_id: embedded,
                    truncated: data
                        .get("truncated")
                        .and_then(|t| t.as_bool())
                        .unwrap_or(false),
                });
            }
            Some(DISMISS_TYPE) => {
                out.push(ResolutionCandidate {
                    kind: ResolutionKind::Dismiss,
                    batch_id: batch_id.to_string(),
                    order_key: OrderKey {
                        primary_id: own_event_id,
                        own_event_id,
                        part_ordinal: ordinal as i64,
                    },
                    answer_text: None,
                    resolved_at_raw: None,
                    embedded_resolved_event_id: None,
                    truncated: false,
                });
            }
            _ => {}
        }
    }
    out
}

/// Eligibility + borrowed facts for a message-lane answer source, without cloning the answer.
pub fn historical_answer_source_ref<'a>(
    role: Option<&str>,
    parts: &'a [Value],
) -> Option<(String, Option<&'a str>)> {
    if role != Some(ROLE_USER) {
        return None;
    }
    if has_sender_part(parts) || answer_part_count(parts) != 1 {
        return None;
    }
    let batch_id = parts.iter().find_map(|p| {
        let data = part_data(p)?;
        if data.get("type").and_then(|t| t.as_str()) == Some(ANSWER_TYPE) {
            data.get("batch_id")
                .and_then(|b| b.as_str())
                .map(String::from)
        } else {
            None
        }
    })?;
    let answer = parts
        .iter()
        .find_map(|p| p.get("text").and_then(|t| t.as_str()))
        .filter(|s| !s.is_empty());
    Some((batch_id, answer))
}

/// Answer-source facts for a message-lane event, if it is an eligible source.
pub fn historical_answer_source(payload: &str) -> Option<AnswerSource> {
    if top_role(payload).as_deref() != Some(ROLE_USER) {
        return None;
    }
    let parts = parse_parts(payload)?;
    if has_sender_part(&parts) || answer_part_count(&parts) != 1 {
        return None;
    }
    let batch_id = parts.iter().find_map(|p| {
        let data = part_data(p)?;
        if data.get("type").and_then(|t| t.as_str()) == Some(ANSWER_TYPE) {
            data.get("batch_id")
                .and_then(|b| b.as_str())
                .map(String::from)
        } else {
            None
        }
    })?;
    Some(AnswerSource {
        batch_id,
        answer_text: answer_text_from_parts(&parts),
    })
}

/// Validate an inbound message's parts for the live answer route.
pub fn validate_live_answer(parts: &[Value]) -> AnswerValidation {
    let count = answer_part_count(parts);
    if count == 0 {
        return AnswerValidation::NotAnAnswer;
    }
    if count > 1 {
        return AnswerValidation::MultipleParts;
    }
    if !parts_domain_valid(parts) {
        return AnswerValidation::MalformedParts;
    }
    let batch_id = parts.iter().find_map(|p| {
        let data = part_data(p)?;
        if data.get("type").and_then(|t| t.as_str()) == Some(ANSWER_TYPE) {
            data.get("batch_id")
                .and_then(|b| b.as_str())
                .filter(|s| !s.is_empty())
                .map(String::from)
        } else {
            None
        }
    });
    let Some(batch_id) = batch_id else {
        return AnswerValidation::MissingBatchId;
    };
    if has_sender_part(parts) {
        return AnswerValidation::SenderPresent;
    }
    AnswerValidation::Valid {
        batch_id,
        answer_text: answer_text_from_parts(parts),
    }
}

/// A `LIKE` pattern that matches the stored form of a `batch_id`.
pub fn like_pattern_for_batch_id(batch_id: &str) -> String {
    let encoded = serde_json::to_string(batch_id).unwrap_or_else(|_| batch_id.to_string());
    let inner = encoded
        .strip_prefix('"')
        .and_then(|s| s.strip_suffix('"'))
        .unwrap_or(&encoded);
    let mut escaped = String::with_capacity(inner.len() + 2);
    escaped.push('%');
    for c in inner.chars() {
        if c == '\\' || c == '%' || c == '_' {
            escaped.push('\\');
        }
        escaped.push(c);
    }
    escaped.push('%');
    escaped
}

/// Build the marker `data` object.
pub fn marker_data(
    batch_id: &str,
    resolved_event_id: i64,
    resolved_at: &str,
    answer_text: Option<&str>,
    truncated: bool,
) -> Value {
    let mut data = json!({
        "type": ANSWER_TYPE,
        "batch_id": batch_id,
        "resolved_event_id": resolved_event_id,
        "resolved_at": resolved_at,
    });
    if let Some(text) = answer_text {
        data["answer_text"] = json!(text);
    }
    if truncated {
        data["truncated"] = json!(true);
    }
    data
}

/// Build the full marker event payload (a single data part, no role, no text part).
pub fn marker_payload(
    batch_id: &str,
    resolved_event_id: i64,
    resolved_at: &str,
    answer_text: Option<&str>,
    truncated: bool,
) -> Value {
    json!({
        "parts": [
            {"data": marker_data(batch_id, resolved_event_id, resolved_at, answer_text, truncated)}
        ]
    })
}

/// serde_json's serialized byte width of one char inside a JSON string (excluding quotes).
fn char_escaped_width(c: char) -> usize {
    match c {
        '"' | '\\' | '\n' | '\r' | '\t' | '\u{0008}' | '\u{000c}' => 2,
        c if (c as u32) < 0x20 => 6,
        c => c.len_utf8(),
    }
}

/// Exact serialized byte length of `s` as a JSON string, EXCLUDING the surrounding quotes.
pub fn escaped_len(s: &str) -> usize {
    s.chars().map(char_escaped_width).sum()
}

/// Serialized decimal length of an i64.
fn i64_digits(n: i64) -> usize {
    let sign = usize::from(n < 0);
    let mut m = n.unsigned_abs();
    let mut d = 1;
    while m >= 10 {
        m /= 10;
        d += 1;
    }
    sign + d
}

/// Serialized byte length of a marker.
pub fn serialized_marker_bytes(
    batch_id: &str,
    resolved_event_id: i64,
    resolved_at: &str,
    answer_text: Option<&str>,
    truncated: bool,
) -> usize {
    const BASE: usize = 99;
    const ANSWER_FIELD: usize = 17;
    const TRUNCATED_FIELD: usize = 17;
    let mut n =
        BASE + escaped_len(batch_id) + i64_digits(resolved_event_id) + escaped_len(resolved_at);
    if let Some(a) = answer_text {
        n += ANSWER_FIELD + escaped_len(a);
    }
    if truncated {
        n += TRUNCATED_FIELD;
    }
    n
}

/// Conservative reserved-envelope serialized size for the live path.
pub fn reserved_envelope_bytes(batch_id: &str, answer_text: Option<&str>) -> usize {
    serialized_marker_bytes(
        batch_id,
        RESERVED_EVENT_ID,
        RESERVED_TIMESTAMP,
        answer_text,
        false,
    )
}

/// Whether the reserved-envelope estimate exceeds the serialized cap (live 400 gate).
pub fn reserved_envelope_exceeds_cap(batch_id: &str, answer_text: Option<&str>) -> bool {
    reserved_envelope_bytes(batch_id, answer_text) > MARKER_SERIALIZED_CAP_BYTES
}

/// Whether a marker's envelope alone exceeds the cap with empty, non-truncated answer text.
pub fn envelope_only_exceeds_cap(
    batch_id: &str,
    resolved_event_id: i64,
    resolved_at: &str,
) -> bool {
    serialized_marker_bytes(batch_id, resolved_event_id, resolved_at, None, false)
        > MARKER_SERIALIZED_CAP_BYTES
}

/// Whether a backfill source yields no marker that fits the serialized cap.
pub fn source_yields_no_marker(
    batch_id: &str,
    source_event_id: i64,
    answer_text: Option<&str>,
) -> bool {
    if envelope_only_exceeds_cap(batch_id, source_event_id, RESERVED_TIMESTAMP) {
        return true;
    }
    let normalized = answer_text.filter(|s| !s.is_empty());
    if serialized_marker_bytes(
        batch_id,
        source_event_id,
        RESERVED_TIMESTAMP,
        normalized,
        false,
    ) <= MARKER_SERIALIZED_CAP_BYTES
    {
        return false;
    }
    serialized_marker_bytes(batch_id, source_event_id, RESERVED_TIMESTAMP, None, true)
        > MARKER_SERIALIZED_CAP_BYTES
}

/// Fit answer text into the serialized budget for a backfilled marker.
pub fn fit_answer_text(
    batch_id: &str,
    resolved_event_id: i64,
    resolved_at: &str,
    answer_text: &str,
) -> (Option<String>, bool) {
    let normalized = if answer_text.is_empty() {
        None
    } else {
        Some(answer_text)
    };
    if serialized_marker_bytes(batch_id, resolved_event_id, resolved_at, normalized, false)
        <= MARKER_SERIALIZED_CAP_BYTES
    {
        return (normalized.map(String::from), false);
    }

    let fixed = serialized_marker_bytes(batch_id, resolved_event_id, resolved_at, Some(""), true);
    let budget = MARKER_SERIALIZED_CAP_BYTES as i64 - fixed as i64;
    let mut acc: i64 = 0;
    let mut cut = 0usize;
    for (i, ch) in answer_text.char_indices() {
        let w = char_escaped_width(ch) as i64;
        if acc + w > budget {
            break;
        }
        acc += w;
        cut = i + ch.len_utf8();
    }
    let prefix = &answer_text[..cut];
    let text = if prefix.is_empty() {
        None
    } else {
        Some(prefix.to_string())
    };
    (text, true)
}
