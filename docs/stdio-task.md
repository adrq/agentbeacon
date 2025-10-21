# stdio Task Envelope Contract

## Frame Structure
The worker emits a single line of UTF-8 JSON terminated by `\n`. The JSON structure embeds the shared task contract inside `body.task` so stdio agents can inspect metadata if they choose, while a derived plain-text `prompt` is supplied for agents that only understand text.

```json
{
  "version": "1.0",
  "type": "task",
  "body": {
    "workflowRegistryId": "team/refactor-auth",
    "workflowVersion": "a3f4b2c1",
    "workflowRef": "team/refactor-auth:latest",
    "agent": "mock-a2a-writer",
    "task": {
      "message": {
        "messageId": "550e8400-e29b-41d4-a716-446655440000",
        "kind": "message",
        "role": "user",
        "parts": [
          {
            "kind": "text",
            "text": "Compose a welcome message introducing AgentMaestro to a new teammate."
          }
        ]
      },
      "configuration": {},
      "metadata": {"priority": "normal"}
    },
    "prompt": "Compose a welcome message introducing AgentMaestro to a new teammate.",
    "metadata": {"priority": "normal"}
  }
}
```

## Notes
- Optional fields (`configuration`, `metadata`) remain inside `body.task`; adapters that do not need them may ignore the segments.
- `prompt` is derived from the first `TextPart` in `task.message.parts` purely for legacy stdio agents; it must not diverge from the canonical structured content.
- `version` allows future framing adjustments without altering the shared task contract.
