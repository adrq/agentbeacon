#!/bin/bash

# AgentMaestro A2A Protocol Demo
# Demonstrates complete A2A workflow: Agent Card → Workflow Submission → Task Polling → Completion

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

AGENTMAESTRO_PORT=9456
MOCK_AGENT_PORT=9457
MOCK_AGENT_PID=""
AGENTMAESTRO_PID=""

cleanup() {
    echo -e "\n${YELLOW}🧹 Cleaning up processes...${NC}"
    if [[ -n "$MOCK_AGENT_PID" ]]; then
        kill $MOCK_AGENT_PID 2>/dev/null || true
        echo "✓ Mock agent stopped"
    fi
    if [[ -n "$AGENTMAESTRO_PID" ]]; then
        kill $AGENTMAESTRO_PID 2>/dev/null || true
        echo "✓ AgentMaestro stopped"
    fi
}

trap cleanup EXIT

echo -e "${BLUE}🚀 AgentMaestro A2A Protocol Demo${NC}"
echo -e "Complete A2A workflow execution flow demonstration\n"

if [[ ! -f "./bin/agentmaestro" ]]; then
    echo -e "${RED}❌ agentmaestro binary not found. Run 'make build' first${NC}"
    exit 1
fi

if [[ ! -f "./bin/mock-agent" ]]; then
    echo -e "${RED}❌ mock-agent binary not found. Run 'make build' first${NC}"
    exit 1
fi

echo -e "${BLUE}1. Starting Mock A2A Agent on port $MOCK_AGENT_PORT...${NC}"
./bin/mock-agent --mode a2a --port $MOCK_AGENT_PORT &
MOCK_AGENT_PID=$!
echo "   Mock Agent PID: $MOCK_AGENT_PID"

sleep 2

echo -e "\n${BLUE}2. Verifying Mock Agent is responding...${NC}"
if curl -s "http://localhost:$MOCK_AGENT_PORT/.well-known/agent-card.json" > /dev/null; then
    echo -e "   ${GREEN}✓ Mock Agent is ready${NC}"
else
    echo -e "   ${RED}❌ Mock Agent not responding${NC}"
    exit 1
fi

echo -e "\n${BLUE}3. Starting AgentMaestro on port $AGENTMAESTRO_PORT...${NC}"
./bin/agentmaestro -port $AGENTMAESTRO_PORT &
AGENTMAESTRO_PID=$!
echo "   AgentMaestro PID: $AGENTMAESTRO_PID"

sleep 3

echo -e "\n${BLUE}4. Verifying AgentMaestro is responding...${NC}"
if curl -s "http://localhost:$AGENTMAESTRO_PORT/api/health" > /dev/null; then
    echo -e "   ${GREEN}✓ AgentMaestro is ready${NC}"
else
    echo -e "   ${RED}❌ AgentMaestro not responding${NC}"
    exit 1
fi

echo -e "\n${BLUE}5. Fetching AgentMaestro Agent Card...${NC}"
echo -e "${YELLOW}GET http://localhost:$AGENTMAESTRO_PORT/.well-known/agent-card.json${NC}"
AGENT_CARD=$(curl -s "http://localhost:$AGENTMAESTRO_PORT/.well-known/agent-card.json")
echo "$AGENT_CARD" | jq '.'
echo -e "   ${GREEN}✓ Agent Card retrieved successfully${NC}"

WORKFLOW_YAML='name: a2a-demo-workflow
description: Demo workflow with A2A agent node
nodes:
  - id: local-task
    agent: demo-agent
    prompt: "This runs on local stdio agent"
  - id: external-a2a-task
    agent: external-agent
    agent_url: http://localhost:'$MOCK_AGENT_PORT'/rpc
    prompt: "This runs on external A2A agent"
    depends_on: [local-task]
  - id: final-task
    agent: demo-agent
    prompt: "Final task back to local agent"
    depends_on: [external-a2a-task]'

echo -e "\n${BLUE}6. Submitting workflow via A2A protocol...${NC}"
echo -e "${YELLOW}Workflow YAML:${NC}"
echo "$WORKFLOW_YAML"

CONTEXT_ID=$(uuidgen)
ESCAPED_YAML=$(echo "$WORKFLOW_YAML" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')
JSON_RPC_REQUEST='{
  "jsonrpc": "2.0",
  "method": "message/send",
  "params": {
    "contextId": "'$CONTEXT_ID'",
    "messages": [
      {
        "role": "user",
        "parts": [
          {
            "kind": "text",
            "text": "'$ESCAPED_YAML'"
          }
        ],
        "messageId": "'$(uuidgen)'",
        "kind": "message"
      }
    ]
  },
  "id": "demo-request-1"
}'

echo -e "\n${YELLOW}Sending JSON-RPC request to /rpc endpoint...${NC}"
RESPONSE=$(curl -s -X POST "http://localhost:$AGENTMAESTRO_PORT/rpc" \
  -H "Content-Type: application/json" \
  -d "$JSON_RPC_REQUEST")

echo "Response:"
echo "$RESPONSE" | jq '.'

TASK_ID=$(echo "$RESPONSE" | jq -r '.result.id')
if [[ "$TASK_ID" == "null" || -z "$TASK_ID" ]]; then
    echo -e "${RED}❌ Failed to get task ID from response${NC}"
    exit 1
fi

echo -e "\n${GREEN}✓ Workflow submitted successfully${NC}"
echo -e "   Task ID: $TASK_ID"
echo -e "   Context ID: $CONTEXT_ID"

echo -e "\n${BLUE}7. Polling task status until completion...${NC}"
MAX_POLLS=30
POLL_COUNT=0

while [[ $POLL_COUNT -lt $MAX_POLLS ]]; do
    POLL_COUNT=$((POLL_COUNT + 1))
    echo -e "   Poll $POLL_COUNT/$MAX_POLLS..."

    POLL_REQUEST='{
      "jsonrpc": "2.0",
      "method": "tasks/get",
      "params": {
        "taskId": "'$TASK_ID'"
      },
      "id": "poll-'$POLL_COUNT'"
    }'

    STATUS_RESPONSE=$(curl -s -X POST "http://localhost:$AGENTMAESTRO_PORT/rpc" \
      -H "Content-Type: application/json" \
      -d "$POLL_REQUEST")

    TASK_STATE=$(echo "$STATUS_RESPONSE" | jq -r '.result.status.state')

    echo -e "      Current state: ${YELLOW}$TASK_STATE${NC}"

    if [[ "$TASK_STATE" == "completed" ]]; then
        echo -e "\n${GREEN}🎉 Workflow completed successfully!${NC}"
        echo -e "${YELLOW}Final task status:${NC}"
        echo "$STATUS_RESPONSE" | jq '.result'
        break
    elif [[ "$TASK_STATE" == "failed" ]]; then
        echo -e "\n${RED}❌ Workflow failed${NC}"
        echo -e "${YELLOW}Final task status:${NC}"
        echo "$STATUS_RESPONSE" | jq '.result'
        exit 1
    elif [[ "$TASK_STATE" == "canceled" ]]; then
        echo -e "\n${YELLOW}⚠️  Workflow was canceled${NC}"
        echo "$STATUS_RESPONSE" | jq '.result'
        exit 1
    fi

    sleep 2
done

if [[ $POLL_COUNT -ge $MAX_POLLS ]]; then
    echo -e "\n${RED}❌ Timeout waiting for workflow completion${NC}"
    exit 1
fi

echo -e "\n${BLUE}8. Demo completed successfully!${NC}"
echo -e "\n${GREEN}✓ Mock A2A Agent started and responded${NC}"
echo -e "${GREEN}✓ AgentMaestro A2A server started${NC}"
echo -e "${GREEN}✓ Agent Card endpoint accessible${NC}"
echo -e "${GREEN}✓ Workflow submitted via JSON-RPC${NC}"
echo -e "${GREEN}✓ Mixed agent types (stdio + A2A) executed${NC}"
echo -e "${GREEN}✓ Task polling worked correctly${NC}"
echo -e "${GREEN}✓ Complete A2A protocol flow validated${NC}"

echo -e "\n${BLUE}🎯 A2A Protocol Implementation: VERIFIED${NC}"
