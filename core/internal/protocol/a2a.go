package protocol

import (
	"time"
)

// A2A Protocol Core Types

type AgentCard struct {
	ProtocolVersion                   string                 `json:"protocolVersion"`
	Name                              string                 `json:"name"`
	Description                       string                 `json:"description"`
	URL                               string                 `json:"url"`
	Version                           string                 `json:"version"`
	Capabilities                      AgentCapabilities      `json:"capabilities"`
	DefaultInputModes                 []string               `json:"defaultInputModes"`
	DefaultOutputModes                []string               `json:"defaultOutputModes"`
	Skills                            []AgentSkill           `json:"skills"`
	IconURL                           string                 `json:"iconUrl,omitempty"`
	Provider                          *AgentProvider         `json:"provider,omitempty"`
	DocumentationURL                  string                 `json:"documentationUrl,omitempty"`
	PreferredTransport                string                 `json:"preferredTransport"`
	AdditionalInterfaces              []AgentInterface       `json:"additionalInterfaces,omitempty"`
	SecuritySchemes                   map[string]interface{} `json:"securitySchemes,omitempty"`
	Security                          []map[string][]string  `json:"security,omitempty"`
	SupportsAuthenticatedExtendedCard bool                   `json:"supportsAuthenticatedExtendedCard,omitempty"`
	Signatures                        []AgentCardSignature   `json:"signatures,omitempty"`
}

type AgentCapabilities struct {
	Streaming              bool             `json:"streaming,omitempty"`
	PushNotifications      bool             `json:"pushNotifications,omitempty"`
	StateTransitionHistory bool             `json:"stateTransitionHistory,omitempty"`
	Extensions             []AgentExtension `json:"extensions,omitempty"`
}

type AgentInterface struct {
	URL       string `json:"url"`
	Transport string `json:"transport"`
}

type AgentCardSignature struct {
	Protected string                 `json:"protected"`
	Signature string                 `json:"signature"`
	Header    map[string]interface{} `json:"header,omitempty"`
}

type AgentExtension struct {
	URI         string                 `json:"uri"`
	Description string                 `json:"description,omitempty"`
	Required    bool                   `json:"required,omitempty"`
	Params      map[string]interface{} `json:"params,omitempty"`
}

type AgentProvider struct {
	Organization string `json:"organization"`
	URL          string `json:"url"`
}

type AgentSkill struct {
	ID          string                `json:"id"`
	Name        string                `json:"name"`
	Description string                `json:"description"`
	Tags        []string              `json:"tags"`
	Examples    []string              `json:"examples,omitempty"`
	InputModes  []string              `json:"inputModes,omitempty"`
	OutputModes []string              `json:"outputModes,omitempty"`
	Security    []map[string][]string `json:"security,omitempty"`
}

type Task struct {
	ID        string                 `json:"id"`
	ContextID string                 `json:"contextId"`
	Status    TaskStatus             `json:"status"`
	History   []Message              `json:"history,omitempty"`
	Artifacts []Artifact             `json:"artifacts,omitempty"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
	Kind      string                 `json:"kind"`
}

type TaskStatus struct {
	State     string   `json:"state"`
	Message   *Message `json:"message,omitempty"`
	Timestamp string   `json:"timestamp,omitempty"`
}

type Message struct {
	Role             string                 `json:"role"`
	Parts            []Part                 `json:"parts"`
	Metadata         map[string]interface{} `json:"metadata,omitempty"`
	Extensions       []string               `json:"extensions,omitempty"`
	ReferenceTaskIDs []string               `json:"referenceTaskIds,omitempty"`
	MessageID        string                 `json:"messageId"`
	TaskID           string                 `json:"taskId,omitempty"`
	ContextID        string                 `json:"contextId,omitempty"`
	Kind             string                 `json:"kind"`
}

// FilePart represents a file segment within a message or artifact
type FilePart struct {
	URI      string `json:"uri,omitempty"`
	Bytes    string `json:"bytes,omitempty"`
	Name     string `json:"name,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
}

// DataPart represents structured data (e.g., JSON) within a message or artifact
type DataPart struct {
	Data map[string]interface{} `json:"data"`
}

// Part represents a discriminated union for message/artifact content
type Part struct {
	Kind     string                 `json:"kind"`
	Text     string                 `json:"text,omitempty"`
	File     *FilePart              `json:"file,omitempty"`
	Data     *DataPart              `json:"data,omitempty"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

type Artifact struct {
	ArtifactID  string                 `json:"artifactId"`
	Name        string                 `json:"name,omitempty"`
	Description string                 `json:"description,omitempty"`
	Parts       []Part                 `json:"parts"`
	Metadata    map[string]interface{} `json:"metadata,omitempty"`
	Extensions  []string               `json:"extensions,omitempty"`
}

type PushNotificationConfig struct {
	ID             string                              `json:"id,omitempty"`
	URL            string                              `json:"url"`
	Token          string                              `json:"token,omitempty"`
	Authentication *PushNotificationAuthenticationInfo `json:"authentication,omitempty"`
}

type PushNotificationAuthenticationInfo struct {
	Schemes     []string `json:"schemes"`
	Credentials string   `json:"credentials,omitempty"`
}

// RPC Method Parameter Types

type MessageSendParams struct {
	Message       Message                   `json:"message"`
	Configuration *MessageSendConfiguration `json:"configuration,omitempty"`
	Metadata      map[string]interface{}    `json:"metadata,omitempty"`
}

type MessageSendConfiguration struct {
	AcceptedOutputModes    []string                `json:"acceptedOutputModes,omitempty"`
	HistoryLength          int                     `json:"historyLength,omitempty"`
	Blocking               bool                    `json:"blocking,omitempty"`
	PushNotificationConfig *PushNotificationConfig `json:"pushNotificationConfig,omitempty"`
}

type TaskQueryParams struct {
	ID            string                 `json:"id"`
	HistoryLength int                    `json:"historyLength,omitempty"`
	Metadata      map[string]interface{} `json:"metadata,omitempty"`
}

type TaskCancelParams struct {
	ID       string                 `json:"id"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// Streaming Event Types

type TaskStatusUpdateEvent struct {
	TaskID    string                 `json:"taskId"`
	ContextID string                 `json:"contextId"`
	Kind      string                 `json:"kind"`
	Status    TaskStatus             `json:"status"`
	Final     bool                   `json:"final"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
}

type TaskArtifactUpdateEvent struct {
	TaskID    string                 `json:"taskId"`
	ContextID string                 `json:"contextId"`
	Kind      string                 `json:"kind"`
	Artifact  Artifact               `json:"artifact"`
	Append    bool                   `json:"append,omitempty"`
	LastChunk bool                   `json:"lastChunk,omitempty"`
	Metadata  map[string]interface{} `json:"metadata,omitempty"`
}

// Additional Method Parameter Types

type TaskIdParams struct {
	ID       string                 `json:"id"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

type TaskPushNotificationConfig struct {
	TaskID                 string                 `json:"taskId"`
	PushNotificationConfig PushNotificationConfig `json:"pushNotificationConfig"`
}

type GetTaskPushNotificationConfigParams struct {
	ID                       string                 `json:"id"`
	Metadata                 map[string]interface{} `json:"metadata,omitempty"`
	PushNotificationConfigID string                 `json:"pushNotificationConfigId,omitempty"`
}

type ListTaskPushNotificationConfigParams struct {
	ID       string                 `json:"id"`
	Metadata map[string]interface{} `json:"metadata,omitempty"`
}

// Helper functions for creating common structures

func NewTextPart(text string) Part {
	return Part{
		Kind: "text",
		Text: text,
	}
}

func NewTaskStatus(state string, message *Message) TaskStatus {
	return TaskStatus{
		State:     state,
		Message:   message,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
}

func NewTaskStatusWithTextMessage(state, messageText string) TaskStatus {
	var msg *Message
	if messageText != "" {
		msg = &Message{
			Role:      "agent",
			Parts:     []Part{NewTextPart(messageText)},
			MessageID: "status-msg",
			Kind:      "message",
		}
	}
	return TaskStatus{
		State:     state,
		Message:   msg,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}
}

func NewTaskStatusUpdateEvent(taskID, contextID string, status TaskStatus, final bool) TaskStatusUpdateEvent {
	return TaskStatusUpdateEvent{
		TaskID:    taskID,
		ContextID: contextID,
		Kind:      "status-update",
		Status:    status,
		Final:     final,
	}
}

func NewTaskArtifactUpdateEvent(taskID, contextID string, artifact Artifact) TaskArtifactUpdateEvent {
	return TaskArtifactUpdateEvent{
		TaskID:    taskID,
		ContextID: contextID,
		Kind:      "artifact-update",
		Artifact:  artifact,
	}
}

// Task state constants matching A2A protocol
const (
	TaskStateSubmitted     = "submitted"
	TaskStateWorking       = "working"
	TaskStateInputRequired = "input-required"
	TaskStateCompleted     = "completed"
	TaskStateCanceled      = "canceled"
	TaskStateFailed        = "failed"
	TaskStateRejected      = "rejected"
	TaskStateAuthRequired  = "auth-required"
	TaskStateUnknown       = "unknown"
)

// A2A-specific error codes (standard JSON-RPC codes are in jsonrpc package)
const (
	ErrorCodeTaskNotFound         = -32001
	ErrorCodeTaskNotCancelable    = -32002
	ErrorCodeUnsupportedOperation = -32004
)
