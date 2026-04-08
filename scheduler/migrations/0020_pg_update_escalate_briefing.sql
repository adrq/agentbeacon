-- 0020: Update escalate briefing text for REST API migration
-- Only update rows that still contain the old MCP default; preserve operator customizations.
UPDATE config SET value = 'Use the AgentBeacon `escalate` REST API to surface questions to the user.

`POST $AGENTBEACON_API_BASE/api/escalate`
```json
{"questions": [{"question": "Your question here", "options": [{"label": "A", "description": "..."}]}], "importance": "blocking"}
```
- `importance`: `"blocking"` (default) means the agent should end its turn and wait for the user''s answer in the next turn; `"fyi"` is fire-and-forget.
- `options`: optional array of 2-5 `{label, description}` choices per question.
- `context`: optional string with additional context per question.
- Max 4 questions per batch.
- The user''s answer is delivered as a normal message to this session.'
WHERE name = 'briefing.escalate'
  AND value LIKE '%MCP tool%';
