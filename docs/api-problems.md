# API error codes

Errors that a client is expected to branch on are returned as RFC 9457 problem
documents with `Content-Type: application/problem+json`:

```json
{
  "title": "Decision already resolved",
  "status": 409,
  "detail": "this decision was already resolved",
  "code": "decision.already_resolved",
  "resolution": { "kind": "answered", "answer_text": "…", "resolved_at": "…" }
}
```

- `code` is the only member to branch on. It is never renamed and never reused
  for a different condition.
- `title` and `detail` are display text. Do not parse them.
- `status` repeats the HTTP status.
- Any other member is an extension carrying data for that specific code.
- There is no `type` member. RFC 9457 defines an absent `type` as
  `about:blank`.

## Client rule

Not every error body is a problem document. A proxy, tunnel or crash can return
plain text at any time. The rule is therefore:

> If the body parses as `application/problem+json`, branch on `code`.
> Otherwise display the status and the body text.

Endpoints outside the table below return `{"error": "<text>"}` with the
appropriate status. The wiki endpoints are in that group: every wiki error
response uses the `{"error": ...}` form, including the conflict and confirmation
responses that carry recovery data alongside it.

## Codes

| Code | Status | Meaning | Extensions |
|---|---|---|---|
| `request.invalid` | 400 | The request could not be interpreted as written. | |
| `request.too_large` | 413 | The request body exceeds the accepted size. | |
| `request.method_not_allowed` | 405 | The path exists but does not support this method. | |
| `resource.not_found` | 404 | No resource matches the path. | |
| `resource.conflict` | 409 | The request conflicts with the current state. | |
| `auth.unauthorized` | 401 | No usable credential was presented. | |
| `auth.forbidden` | 403 | The credential is not permitted to do this. | |
| `internal.error` | 500 | The server failed to complete the request. | |
| `decision.not_found` | 404 | No decision has this id. | |
| `decision.already_resolved` | 409 | The decision was answered or dismissed first. | `resolution` |
| `decision.expired` | 409 | The execution holding the decision has ended. | |
| `decision.wrong_session` | 409 | The decision belongs to a different session. | |
| `decision.answer_too_large` | 400 | The answer text exceeds the accepted size. | |
| `execution.terminated` | 409 | The execution has ended and accepts no more input. | |

## The `resolution` extension

`decision.already_resolved` carries the resolution that won:

```json
{ "kind": "answered", "answer_text": "PostgreSQL", "resolved_at": "2026-01-01T00:00:00Z" }
```

`kind` is `answered` or `dismissed`. `answer_text` is present only on answers,
and only when the answer carried text. Compare it against what you submitted:
a match means your submission is the one that landed, and the operation should
render as success. A mismatch means someone else resolved it first, and the
winning resolution is what to show.

## Adding codes

New codes are additive. A client that does not recognize a `code` falls back to
displaying `status` and `detail`, which is why adding one never breaks a
deployed client. Existing codes are never renamed and never change their
status.
