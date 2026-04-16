-- 0021: Add PATCH (edit) example to briefing.messaging.

INSERT INTO config (name, value, created_at, updated_at) VALUES
    ('briefing.messaging', 'Send a message:
  curl -X POST "$AGENTBEACON_API_BASE/api/messages" \
    -H "Authorization: Bearer $AGENTBEACON_SESSION_ID" \
    -H "Content-Type: application/json" \
    -d "{\"to\": \"<hierarchical-name>\", \"parts\": [{\"text\": \"your message\"}]}"

Read your messages:
  curl "$AGENTBEACON_API_BASE/api/messages?session_id=$AGENTBEACON_SESSION_ID"

Read messages since a known event ID:
  curl "$AGENTBEACON_API_BASE/api/messages?session_id=$AGENTBEACON_SESSION_ID&since_id=<id>"

Discover sessions in your execution:
  curl "$AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions" \
    -H "Authorization: Bearer $AGENTBEACON_SESSION_ID"

Read a wiki page:
  curl "$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>" \
    -H "Authorization: Bearer $AGENTBEACON_SESSION_ID"

Write a wiki page (full rewrite or create):
  curl -X PUT "$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>" \
    -H "Authorization: Bearer $AGENTBEACON_SESSION_ID" \
    -H "Content-Type: application/json" \
    -d "{\"title\": \"Page Title\", \"body\": \"Content here\"}"
- Include revision_number from GET response when updating an existing page.

Edit a wiki page (targeted find/replace -- prefer this over PUT when updating an existing page):
  curl -X PATCH "$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>" \
    -H "Authorization: Bearer $AGENTBEACON_SESSION_ID" \
    -H "Content-Type: application/json" \
    -d "{\"edits\": [{\"old_string\": \"find me\", \"new_string\": \"replace me\"}], \"revision_number\": 5}"
- old_string must match exactly once (or set replace_all: true).
- edits are applied in order, all-or-nothing.
- revision_number must match the current page revision (GET it first).',
    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
ON CONFLICT (name) DO UPDATE SET
    value = EXCLUDED.value,
    updated_at = CURRENT_TIMESTAMP;

INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (21, CURRENT_TIMESTAMP);
