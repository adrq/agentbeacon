---
title: Authentication
description: The user, logins for the web UI and scripts, and how agents and workers authenticate.
---

Every API request is authenticated. Only the health, readiness, version and documentation endpoints, the agent card, the web UI's static files and the login endpoint are open without a credential.

## The user

AgentBeacon has one user, created with `--setup` against the database you run with (the same `--port` or `--db-url`):

```bash
agentbeacon --setup                       # prompts for a username and password
AGENTBEACON_USERNAME=admin AGENTBEACON_PASSWORD='…' agentbeacon --setup --user-only
```

- The username is 1–64 characters of letters, digits, `.`, `_` and `-`.
- The password is 8–256 characters.
- `--user-only` creates or updates the user and skips SDK installation. Without credentials and with a user already present, it changes nothing.
- Running `--setup` again with a new password changes it and signs out every existing login. Running it with a different username is refused: only one user is supported.

## Signing in

The web UI shows a sign-in page when it has no valid login, and **Settings → Account → Sign out** ends the current login.

Scripts and tools log in with a bearer token:

```bash
curl -X POST http://localhost:9456/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "…", "label": "my-script", "delivery": "bearer"}'
# {"token": "…", "login": {"id": "…", "label": "my-script", "created_at": "…", "expires_at": "…"}}

curl http://localhost:9456/api/v1/executions -H "Authorization: Bearer $TOKEN"
```

With `"delivery": "cookie"` the login is set as an `HttpOnly`, `SameSite=Strict` cookie instead and the token is not returned. Writes authenticated by the cookie must carry an `Origin` header naming the server's own origin or an origin listed in `CORS_ALLOWED_ORIGINS`.

Logins expire after 30 days. Change this with `--login-ttl` or `AGENTBEACON_LOGIN_TTL` (for example `12h`, `90d`).

## Managing logins

| Request | Effect |
|---|---|
| `GET /api/v1/auth/logins` | Your unexpired logins: `id`, `label`, `created_at`, `expires_at`, and `current` for the one making the request. |
| `DELETE /api/v1/auth/logins/{id}` | Revoke one login. |
| `POST /api/v1/auth/logout` | Revoke the login making the request. |

## Agents

Each agent session receives its own token as `$AGENTBEACON_SESSION_TOKEN` and in its MCP connection. `$AGENTBEACON_SESSION_ID` is the session's identifier and is not a credential. An agent can read its own execution, send messages to any session in it, use the wiki, and escalate (root lead only); other routes answer `403 auth.forbidden`.

## Externally supervised workers

Workers started by AgentBeacon receive their credential automatically. A worker run by another supervisor is admitted first:

```bash
curl -X POST http://localhost:9456/api/v1/workers -H "Authorization: Bearer $TOKEN"
# {"worker_id": "…", "token": "…"}

AGENTBEACON_WORKER_TOKEN=… agentbeacon-worker --scheduler-url http://localhost:9456 --worker-id …
```

The admission lasts until the scheduler restarts or stops hearing from the worker; the supervisor then admits it again.

## Errors

A missing, expired or revoked credential answers `401 auth.unauthorized`; a credential that may not use the route answers `403 auth.forbidden`. See [error codes](https://github.com/adrq/agentbeacon/blob/main/docs/api-problems.md).
