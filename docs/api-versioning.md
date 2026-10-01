# API versioning

The product REST and SSE contract is served under `/api/v1`. Everything under
that prefix is covered by the promises on this page.

Outside the promise, and unversioned by design:

| Path | What it is |
|---|---|
| `GET /api/versions` | Version discovery. Additive forever. |
| `GET /api/health`, `GET /api/ready` | Operational probes. |
| `GET /api/docs` | The agent-facing API reference. A docs locator, not a JSON wire contract. |
| `POST /rpc`, `GET /.well-known/agent-card.json` | A2A. |
| `/mcp` | MCP Streamable HTTP. |
| `POST /api/worker/sync`, `POST /api/worker/events` | Internal worker endpoints; not part of the client API. |

There are no unversioned aliases for product routes. A client that needs to
tell a v1 server from an older one asks `/api/versions`: **a non-JSON response
or a 404 means the server predates v1.** An older server has no route there, so
read the body's content type rather than assuming a particular status.

## Discovery

```
GET /api/versions
Cache-Control: no-store
```

```json
{
  "discovery_version": 1,
  "server": { "version": "<the running release version>" },
  "rest_versions": ["v1.0"],
  "limits": {
    "events":    { "default_page": 100, "max_page": 500,
                   "string_leaf_max_bytes": 16384, "payload_budget_bytes": 65536 },
    "decisions": { "resolved_default_page": 50, "resolved_max_page": 200 }
  }
}
```

- `discovery_version` is frozen at `1`. The document only ever grows.
- `server.version` is the running release's version, for diagnostics and
  update copy. Never branch on it.
- `rest_versions` is an **append-only** list of point versions. Every release
  that grows the wire additively appends the next one and documents it here.
- `limits` carries the operational bounds. Read them; never hardcode them.
  `events.payload_budget_bytes` is the size at which a served payload is
  shortened at all: a payload under it is served exactly as stored.
  `events.string_leaf_max_bytes` is the per-leaf shortening target applied when
  a payload exceeds `payload_budget_bytes` — not an independent per-leaf cap,
  and not a promise about leaves in payloads that are already within budget.

**Client rule.** Keep a compiled-in map of client-feature → minimum point
version, and enable each feature only when its version is at or below the
highest entry your client also knows in `rest_versions`. Tolerate unknown
members everywhere.

## What is additive and what is breaking

**Additive** — safe in a point version:

- new optional response members;
- new point versions;
- new problem codes (see `api-problems.md`);
- new event kinds and new enum cases, given the fallback obligations below.

**Breaking** — needs a new major version:

- new required request members;
- tightened validation on formerly valid input;
- changed defaults, nullability, ordering or precision;
- replacing a bare array with a pagination envelope;
- renaming or reusing a code.

The rubric above is about what a **deployed client** must tolerate: unknown
members, unknown codes, unknown event kinds. That tolerance is what makes an
addition additive.

## Ids and cursors

Every id and cursor on the v1 wire is an **opaque string**. Clients compare
them for equality and pass them back verbatim; they never parse, order or do
arithmetic on them.

Ordering comes from the server. Within a single fetched stream — one REST page,
one SSE connection — events arrive in id order, so "newest" is simply
last-in-sequence and "answered after" is later-in-sequence. To interleave two
streams, key on `created_at` with a deterministic tiebreak.

## Event payload schemas

An event record carries `event_type` and a `payload`. The kinds this contract
owns are `message`, `state_change`, `escalate`, `question_answer`,
`question_dismiss`, `message_delivered`, `turn_complete`, `delegate`,
`child_continued` and `child_crashed`.

Raw provider content passed through inside message parts is **not** one of
those kinds. It is documented as opaque: generic display and download only, no
schema, no stability, and new `data.type` values may appear at any time.

**Clients must render an unknown `event_type` and an unknown payload version
generically, and must never crash on either.** That obligation is what makes
new kinds additive.

## Pagination

Bounded reads return:

```json
{ "items": [ … ], "next_cursor": "…", "has_more": true }
```

`next_cursor` is opaque and is `null` when there is nothing further. Event
pages are always ascending by id in both cursor directions: `before` and
`after` choose the *window*, never the wire order.

## Idempotency

Requests do not accept an `Idempotency-Key` header.

Answering a decision is idempotent: a retried answer either succeeds or comes
back as `decision.already_resolved` carrying the resolution that won. Compare it to
what you submitted — a match is success. Plain text messages carry no such
guard: a double send delivers the text twice, which is visible in the session
history the client already fetches.
