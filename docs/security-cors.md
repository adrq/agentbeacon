# CORS Configuration

AgentBeacon enforces restricted Cross-Origin Resource Sharing (CORS) policies to prevent unauthorized access from arbitrary web origins.

## Default Allowed Origins

1. **Scheduler's Own Origin**: `http://localhost:{port}` (always allowed)
2. **Vite Dev Server**: `http://localhost:{port+1000}` (only when `DEV_MODE=1`)

## Configuration

### Custom Origins

Add additional origins via the `CORS_ALLOWED_ORIGINS` environment variable:

```bash
# Single origin
CORS_ALLOWED_ORIGINS="http://localhost:3000" ./bin/agentbeacon

# Multiple origins (comma-separated)
CORS_ALLOWED_ORIGINS="http://localhost:3000,http://localhost:8080" ./bin/agentbeacon
```

### Allowed Methods

- GET
- POST
- PUT
- DELETE
- OPTIONS

### Allowed Headers

- `Content-Type`
- `Authorization`

### Credentials

Credentials (cookies, HTTP authentication) are enabled for approved origins.

## Usage Examples

### Production

```bash
./bin/agentbeacon --port 9456

# Allowed: http://localhost:9456
```

### Development

```bash
DEV_MODE=1 ./bin/agentbeacon --port 9456

# Allowed:
# - http://localhost:10456 (Vite dev server, port+1000)
# - http://localhost:9456 (scheduler)
```

### Custom Frontend

```bash
CORS_ALLOWED_ORIGINS="http://localhost:3000,https://app.example.com" \
  ./bin/agentbeacon --port 9456

# Allowed:
# - http://localhost:3000
# - https://app.example.com
# - http://localhost:9456
```


## Best Practices

1. Use HTTPS in production - configure `CORS_ALLOWED_ORIGINS` with `https://` URLs
2. Minimize allowed origins - only add origins that legitimately need access
3. Keep `DEV_MODE` disabled in production
4. Regularly audit `CORS_ALLOWED_ORIGINS` configuration
