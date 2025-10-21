use serde_json::Value as JsonValue;
use uuid::Uuid;

/// Inject real runtime values for Message.messageId and Message.kind
///
/// This function mutates the task JSON to inject:
/// - messageId: UUID v4 (if absent)
/// - kind: "message" (if absent)
///
/// User-provided values are preserved exactly as authored.
/// This ensures A2A compliance at worker dispatch while preserving workflow ergonomics.
pub fn inject_runtime_message_fields(task: &mut JsonValue) {
    // Handle MessageSendParams structure (task.message)
    if let Some(message) = task.get_mut("message").and_then(|m| m.as_object_mut()) {
        if !message.contains_key("messageId") {
            let message_id = Uuid::new_v4().to_string();
            message.insert("messageId".to_string(), JsonValue::String(message_id));
        }

        if !message.contains_key("kind") {
            message.insert("kind".to_string(), JsonValue::String("message".to_string()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_inject_messageid_when_absent() {
        let mut task = json!({
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "Hello"}]
            }
        });

        inject_runtime_message_fields(&mut task);

        let message = task.get("message").unwrap();
        assert!(message.get("messageId").is_some());
        // Verify injected messageId is a valid UUID
        let message_id = message.get("messageId").unwrap().as_str().unwrap();
        assert!(Uuid::parse_str(message_id).is_ok());
        assert_eq!(message.get("kind").unwrap().as_str().unwrap(), "message");
    }

    #[test]
    fn test_no_injection_when_both_present() {
        let mut task = json!({
            "message": {
                "messageId": "custom-123",
                "kind": "message",
                "role": "user",
                "parts": [{"kind": "text", "text": "Hello"}]
            }
        });

        inject_runtime_message_fields(&mut task);

        let message = task.get("message").unwrap();
        assert_eq!(
            message.get("messageId").unwrap().as_str().unwrap(),
            "custom-123"
        );
        assert_eq!(message.get("kind").unwrap().as_str().unwrap(), "message");
    }

    #[test]
    fn test_no_panic_on_missing_message() {
        let mut task = json!({
            "configuration": {
                "blocking": true
            }
        });

        // Should not panic
        inject_runtime_message_fields(&mut task);

        // Task unchanged
        assert!(task.get("message").is_none());
    }
}
