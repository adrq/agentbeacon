use axum::{Router, http::header, response::IntoResponse, routing::get};

use crate::app::AppState;

const API_DOCS: &str = r#"# AgentBeacon REST API Reference

All endpoints require: `Authorization: Bearer $AGENTBEACON_SESSION_ID`
Base URL: `$AGENTBEACON_API_BASE`

## Messages

Send direct messages to coordinate with other agents in your execution.
You can message any agent whose hierarchical name you know — use Agent Discovery to find them.

### Send message
`POST /api/messages`
```json
{"to": "HIERARCHICAL_NAME", "parts": [{"text": "message text"}]}
```

### Read received messages
`GET /api/messages?session_id={session_id}&since_id={last_event_id}`
Returns messages you've received for the given session. Use `since_id` to poll for new messages.

## Agent Pool

Query the configured agent pool — the agent configs available for delegation in this execution.

### List agent configs
`GET /api/executions/{execution_id}/agents`
Returns agent configs (id, name, description, type) available for delegation.

## Running Sessions

Discover the live sessions (running agent instances) in your execution.

### List sessions in execution
`GET /api/executions/{execution_id}/sessions`
Returns all sessions with hierarchical names, agent types, roles, statuses, and parent relationships.

## Wiki

Read and write shared project knowledge. Use the wiki to publish your findings,
record design decisions, and check what other agents have already discovered.
Search before duplicating work.

### Create/update page
`PUT /api/projects/{project_id}/wiki/pages/{slug}`
```json
{"title": "Page Title", "body": "Page content", "revision_number": 5}
```
Include `revision_number` from the last read for optimistic concurrency. Omit for new pages.

### Edit page
`PATCH /api/projects/{project_id}/wiki/pages/{slug}`
```json
{"edits": [{"old_string": "...", "new_string": "...", "replace_all": false}], "revision_number": 5}
```
Applies sequential find/replace edits to an existing page. `revision_number` required. All edits applied atomically or none. Returns 422 if any `old_string` is missing or ambiguous. On 409/422 the response includes a `current_page` field; note that `tags` in `current_page` are not guaranteed atomic with the page row (they are read in a separate query).

### Read page
`GET /api/projects/{project_id}/wiki/pages/{slug}`
Returns page with current `revision_number`.

### List pages
`GET /api/projects/{project_id}/wiki/pages`

### Search wiki
`GET /api/projects/{project_id}/wiki/pages?q=search+terms`
Same endpoint as list, with `q` query param for BM25 full-text search.

### Delete page
`DELETE /api/projects/{project_id}/wiki/pages/{slug}`

### Page revisions
`GET /api/projects/{project_id}/wiki/pages/{slug}/revisions`

### Get specific revision
`GET /api/projects/{project_id}/wiki/pages/{slug}/revisions/{rev}`

## Executions

### Get execution status
`GET /api/executions/{execution_id}`

### List execution events
`GET /api/executions/{execution_id}/events`

## Sessions

### Get session
`GET /api/sessions/{session_id}`

## Escalation (root lead only)

`POST /api/escalate`
Authorization: Bearer $AGENTBEACON_SESSION_ID

Request body:
```json
{
  "questions": [
    {
      "question": "string",
      "context": "string",
      "options": [
        {
          "label": "string",
          "description": "string"
        }
      ]
    }
  ],
  "importance": "blocking"
}
```

- `questions`: required, 1-4 items
- `question`: required — the question text
- `context`: optional — additional context for the user
- `options`: optional, 2-5 items — multiple-choice options with `label` and `description`
- `importance`: optional — `"blocking"` (default) or `"fyi"`

Response: `{"question_ids": [123], "batch_id": "uuid"}`

## Examples

```bash
# Discover agent configs available for delegation
curl $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/agents \
  -H "Authorization: Bearer $AGENTBEACON_SESSION_ID"

# Discover running sessions (peer agents)
curl $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions \
  -H "Authorization: Bearer $AGENTBEACON_SESSION_ID"

# Send a message to another agent
curl -X POST $AGENTBEACON_API_BASE/api/messages \
  -H "Authorization: Bearer $AGENTBEACON_SESSION_ID" \
  -H "Content-Type: application/json" \
  -d '{"to":"swift-falcon/bold-eagle","parts":[{"text":"auth module ready for review"}]}'

# Read a wiki page
curl $AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/architecture \
  -H "Authorization: Bearer $AGENTBEACON_SESSION_ID"

# Search wiki
curl "$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages?q=auth+design" \
  -H "Authorization: Bearer $AGENTBEACON_SESSION_ID"

# Escalate a question to the user (root lead only)
curl -X POST $AGENTBEACON_API_BASE/api/escalate \
  -H "Authorization: Bearer $AGENTBEACON_SESSION_ID" \
  -H "Content-Type: application/json" \
  -d '{"questions": [{"question": "JWT or session cookies?", "options": [{"label": "JWT", "description": "Stateless"}, {"label": "Cookies", "description": "Simpler"}]}], "importance": "blocking"}'
```
"#;

async fn docs_handler() -> impl IntoResponse {
    (
        [(header::CONTENT_TYPE, "text/markdown; charset=utf-8")],
        API_DOCS,
    )
}

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/docs", get(docs_handler))
}
