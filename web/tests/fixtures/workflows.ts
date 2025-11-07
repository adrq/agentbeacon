export const VALID_WORKFLOW = `name: test-workflow
tasks:
  - id: task1
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-task1"
        kind: message
        parts:
          - kind: text
            text: Execute task 1
`;

export const TWO_TASK_WORKFLOW = `name: two-task-workflow
tasks:
  - id: task1
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-task1"
        kind: message
        parts:
          - kind: text
            text: Execute task 1
  - id: task2
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-task2"
        kind: message
        parts:
          - kind: text
            text: Execute task 2
`;

export const INVALID_WORKFLOW = `name: invalid-workflow
tasks:
  - id: task1
    agent: mock-agent
    task: [unclosed bracket
`;

export const DELAYED_WORKFLOW = `name: delayed-workflow
tasks:
  - id: task1
    agent: mock-agent
    task:
      message:
        role: user
        messageId: "msg-task1"
        kind: message
        parts:
          - kind: text
            text: DELAY_5
`;
