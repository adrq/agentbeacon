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

### Search wiki
`GET /api/wiki/search?q=search+terms`
BM25 full-text search over titles and bodies. The only search endpoint, and the one
to reach for before listing pages. Results are scoped to what you may read: your own
project plus any page shared into it. Add `project=` (slug or id) to narrow within
that scope. `limit` defaults to 50 (1..100), `offset` to 0. Results carry `page_id`,
`project_id`, `project_slug`, `slug`, `title`, `revision_number`, `updated_at`,
`updated_by`, `tags` and `score` — never a body, and never an access level. Fetch the
page itself to learn what you may do with it.

### Create page
`PUT /api/projects/{project_id}/wiki/pages/{slug}`
```json
{"title": "Page Title", "body": "Page content", "tags": ["design"]}
```
Creates a page. Fails with 409 `slug_exists` if the slug is taken — use `PATCH` to
change an existing page. Only your own project accepts a create.

### Edit page
`PATCH /api/projects/{project_id}/wiki/pages/{slug}`
```json
{"revision_number": 5,
 "edits": [{"old_string": "...", "new_string": "...", "replace_all": false}],
 "title": {"old": "Old Title", "new": "New Title"},
 "add_tags": ["api-contract"], "remove_tags": ["draft"],
 "summary": "publish contract v2"}
```
The only way to update a page. Every field except `revision_number` is optional, but at
least one operation is required. There is deliberately no `body` field: replace a whole
body with a single edit whose `old_string` is the current body.

Replacing operations must name what they expect to find; additive ones are idempotent.
`edits` must match `old_string` exactly once (or set `replace_all`). `title` takes
`{"old": ..., "new": ...}` and returns 422 `title_mismatch` if `old` is not the current
title. `remove_tags` returns 422 `tag_not_present` if the page does not carry the tag.
`add_tags` succeeds even where a tag is already present. A request that changes no tag
and edits nothing is a no-op: the revision does not advance.

Unknown fields are rejected with 400 rather than ignored. On 409/422 the response
includes `current_page`; note that `tags` there are not guaranteed atomic with the page
row (they are read in a separate query).

### Publishing a page to a share tag
Some tags are *share tags*: the operator has given them member projects, so tagging a
page with one publishes it to every other member. That never happens implicitly. A write
that would attach a share tag is rejected until you acknowledge it:
```json
{"error": "share_tag_requires_confirmation",
 "publishes": [
   {"tag": "api-contract",
    "shares_with": [{"project": "sandbox", "access": "read_write"}],
    "slug_conflicts": [{"project": "sandbox", "slug": "api-contract"}]},
   {"tag": "spec",
    "shares_with": [{"project": "merge-manager", "access": "read"}],
    "slug_conflicts": []}
 ],
 "warning": "all history travels with the page",
 "remedy": "retry with \"acknowledge_share\": true to publish"}
```
`publishes` lists every tag the request would publish into, one entry each, so a
single tag is a one-element list. `slug_conflicts` is per entry: a conflict means
another member of *that* tag already publishes the same slug.

Retry with `"acknowledge_share": true`, on either `PUT` or `PATCH`. One acknowledgement
covers the whole request. You can send it up front if you already intend to publish.
Read the warning literally: publishing a page publishes its whole revision history,
including anything written while it was private and edited out later.

### Reading and editing another project's page
Pages shared into your project appear in your own listings and searches, each carrying
its owning project. Reach one at its owner's address:
`GET /api/projects/{owning_project}/wiki/pages/{slug}`, using the `project_slug` from
the listing. Page reads and listings carry an `"access"` field of `read` or `read_write`
telling you what you may do; search results do not, so fetch the page before writing.

With `read_write` you may send `edits` and `title`. Three things stay with the owning
project whatever your access: `add_tags` and `remove_tags` (tags control who can see the
page), `DELETE` (unrecoverable from your side), and creating a page — contribute by
publishing from your own wiki instead. Those return 403 `not_page_owner` or
403 `cross_project_create`. A page you may not read returns 404, exactly as a missing
page does.

### Read page
`GET /api/projects/{project_id}/wiki/pages/{slug}`
Returns page with current `revision_number`.

### List pages
`GET /api/projects/{project_id}/wiki/pages`
Your project's pages plus any shared into it, each with its owning project and your
`access` to it.

### Delete page
`DELETE /api/projects/{project_id}/wiki/pages/{slug}`

### Page revisions
`GET /api/projects/{project_id}/wiki/pages/{slug}/revisions`

### Get specific revision
`GET /api/projects/{project_id}/wiki/pages/{slug}/revisions/{rev}`

### Share tag administration
Operator-level, outside any project namespace, because a share tag spans projects and
none owns it. These routes refuse a session token with 403 `operator_scope_only`.

`GET /api/wiki/tags` — every tag, with its member projects inline where it has any.
`POST /api/wiki/tags/{tag_id}/members` — admit a project: `{"project": ..., "access_level": "read"}`.
`PATCH /api/wiki/tags/{tag_id}/members/{project}` — change a member's access level.
`DELETE /api/wiki/tags/{tag_id}/members/{project}` — revoke a member.

There is no route that creates a tag: a tag exists once a page carries it, and admitting
its first member is what makes it a share tag. Admitting a member or widening one to
`read_write` returns 409 `membership_requires_confirmation` with the affected pages when
the change would expose or grant anything; retry with `"acknowledge_share": true`.

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
curl "$AGENTBEACON_API_BASE/api/wiki/search?q=auth+design" \
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
