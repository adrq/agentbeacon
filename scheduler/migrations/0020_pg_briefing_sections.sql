-- 0020: Seed new briefing sections (coordination, messaging, recovery) and slim rest_api.
-- Uses standard single-quote strings (no dollar-quoting) so sqlx::Any can parse them.

INSERT INTO config (name, value) VALUES
    ('briefing.coordination', 'When waiting for a reply from another agent or for a child to complete,
**end your turn**. The system will resume you automatically when:
- A message arrives for you
- A child session completes or crashes
- The user responds to an escalation

Do NOT poll in a loop or sleep-wait. Just finish your turn.

**Authority is separate from communication.**
- Authority flows through the tree: you delegate to children, your parent delegates to you.
- Communication flows freely: you can message any agent in the execution by hierarchical name.
- Messaging a peer is requesting cooperation, not issuing commands. You have no authority over peers.')
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;

INSERT INTO config (name, value) VALUES
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

Write a wiki page:
  curl -X PUT "$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>" \
    -H "Authorization: Bearer $AGENTBEACON_SESSION_ID" \
    -H "Content-Type: application/json" \
    -d "{\"title\": \"Page Title\", \"body\": \"Content here\"}"')
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;

INSERT INTO config (name, value) VALUES
    ('briefing.recovery', 'When a child session crashes, the system automatically attempts recovery (up to 3 retries).
- Do NOT immediately re-delegate the same work to a new child.
- Do NOT assume the work is lost.
- Continue with other work. You will be notified when the child recovers or permanently fails.
- Only re-delegate if the status reaches a terminal failure state.')
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;

INSERT INTO config (name, value) VALUES
    ('briefing.escalate', 'Use the AgentBeacon `escalate` REST API to surface questions to the user.

`POST $AGENTBEACON_API_BASE/api/escalate`
```json
{"questions": [{"question": "Your question here", "options": [{"label": "A", "description": "..."}]}], "importance": "blocking"}
```
- `importance`: `"blocking"` (default) means the agent should end its turn and wait for an answer in the next turn. `"fyi"` is fire-and-forget.
- `options`: optional array of 2-5 `{label, description}` choices per question.
- `context`: optional string with additional context per question.
- Max 4 questions per batch.
- Answers are delivered as normal messages to this session.')
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;

INSERT INTO config (name, value) VALUES
    ('briefing.rest_api', 'Environment variables for API access:
- `$AGENTBEACON_SESSION_ID` — your auth token (use as Bearer header)
- `$AGENTBEACON_API_BASE` — scheduler base URL
- `$AGENTBEACON_EXECUTION_ID` — current execution
- `$AGENTBEACON_PROJECT_ID` — current project (if set)
`GET $AGENTBEACON_API_BASE/api/docs` for the full API reference.
Discover running sessions via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions`.
Write scripts to interact with the API (e.g. discover agents, filter results, send messages in a loop) rather than making one curl call at a time — process data in code, not in your context window.')
ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value;

INSERT INTO schema_migrations (version, applied_at) VALUES (20, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING;
