package protocol

import (
	"encoding/json"
	"fmt"
)

// ACP (Agent Client Protocol) Types
// Based on ACP specification v1.0

// Basic Type Aliases
type SessionId string
type ProtocolVersion int
type AuthMethodId string
type ToolCallId string
type PermissionOptionId string

// Protocol Constants
const (
	// Stop Reasons
	StopReasonEndTurn         = "end_turn"
	StopReasonMaxTokens       = "max_tokens"
	StopReasonMaxTurnRequests = "max_turn_requests"
	StopReasonRefusal         = "refusal"
	StopReasonCancelled       = "cancelled"

	// Tool Call Status
	ToolCallStatusPending    = "pending"
	ToolCallStatusInProgress = "in_progress"
	ToolCallStatusCompleted  = "completed"
	ToolCallStatusFailed     = "failed"
	ToolCallStatusCancelled  = "cancelled"

	// Tool Kinds
	ToolKindFileEdit    = "file_edit"
	ToolKindFileCreate  = "file_create"
	ToolKindFileDelete  = "file_delete"
	ToolKindShell       = "shell"
	ToolKindWebBrowsing = "web_browsing"
	ToolKindSearch      = "search"
	ToolKindOther       = "other"

	// Permission Option Kinds
	PermissionOptionKindAllow    = "allow"
	PermissionOptionKindDeny     = "deny"
	PermissionOptionKindAllowAll = "allow_all"
	PermissionOptionKindDenyAll  = "deny_all"
	PermissionOptionKindCustom   = "custom"

	// Request Permission Outcomes
	RequestPermissionOutcomeApproved  = "approved"
	RequestPermissionOutcomeDenied    = "denied"
	RequestPermissionOutcomeCancelled = "cancelled"

	// Plan Entry Priority
	PlanEntryPriorityHigh   = "high"
	PlanEntryPriorityMedium = "medium"
	PlanEntryPriorityLow    = "low"

	// Plan Entry Status
	PlanEntryStatusPending    = "pending"
	PlanEntryStatusInProgress = "in_progress"
	PlanEntryStatusCompleted  = "completed"

	// Content Block Types
	ContentBlockTypeText         = "text"
	ContentBlockTypeImage        = "image"
	ContentBlockTypeAudio        = "audio"
	ContentBlockTypeResourceLink = "resource_link"
	ContentBlockTypeResource     = "resource"

	// Session Update Types
	SessionUpdateUserMessageChunk  = "user_message_chunk"
	SessionUpdateAgentMessageChunk = "agent_message_chunk"
	SessionUpdateAgentThoughtChunk = "agent_thought_chunk"
	SessionUpdateToolCall          = "tool_call"
	SessionUpdateToolCallUpdate    = "tool_call_update"
	SessionUpdatePlan              = "plan"
	SessionUpdateAvailableCommands = "available_commands_update"
	SessionUpdateCurrentMode       = "current_mode_update"
)

// ToolCallContent Types
const (
	ToolCallContentTypeStandard = "content"
	ToolCallContentTypeDiff     = "diff"
	ToolCallContentTypeTerminal = "terminal"
)

// Type aliases
type SessionModeId string

// Capability Types
type ClientCapabilities struct {
	Fs       FileSystemCapability `json:"fs"`
	Terminal bool                 `json:"terminal"`
}

type ACPAgentCapabilities struct {
	LoadSession        bool               `json:"loadSession"`
	PromptCapabilities PromptCapabilities `json:"promptCapabilities"`
	McpCapabilities    McpCapabilities    `json:"mcpCapabilities"`
}

type FileSystemCapability struct {
	ReadTextFile  bool `json:"readTextFile"`
	WriteTextFile bool `json:"writeTextFile"`
}

type PromptCapabilities struct {
	Image           bool `json:"image"`
	Audio           bool `json:"audio"`
	EmbeddedContext bool `json:"embeddedContext"`
}

type McpCapabilities struct {
	Http bool `json:"http"`
	Sse  bool `json:"sse"`
}

// Authentication Types
type AuthMethod struct {
	Id          AuthMethodId `json:"id"`
	Name        string       `json:"name"`
	Description *string      `json:"description,omitempty"`
}

// Session Configuration Types
type McpServer struct {
	Command string        `json:"command"`
	Args    []string      `json:"args,omitempty"`
	Env     []EnvVariable `json:"env,omitempty"`
	Headers []HttpHeader  `json:"headers,omitempty"`
}

type EnvVariable struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type HttpHeader struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

// Content Block Types (Union Type)
type ContentBlock interface {
	GetType() string
}

type TextContent struct {
	Type        string       `json:"type"`
	Text        string       `json:"text"`
	Annotations *Annotations `json:"annotations,omitempty"`
}

func (t TextContent) GetType() string { return t.Type }

type ImageContent struct {
	Type        string       `json:"type"`
	Data        string       `json:"data"`
	MimeType    string       `json:"mimeType"`
	Uri         *string      `json:"uri,omitempty"`
	Annotations *Annotations `json:"annotations,omitempty"`
}

func (i ImageContent) GetType() string { return i.Type }

type AudioContent struct {
	Type        string       `json:"type"`
	Data        string       `json:"data"`
	MimeType    string       `json:"mimeType"`
	Annotations *Annotations `json:"annotations,omitempty"`
}

func (a AudioContent) GetType() string { return a.Type }

type ResourceLink struct {
	Type        string       `json:"type"`
	Uri         string       `json:"uri"`
	Name        string       `json:"name"`
	MimeType    *string      `json:"mimeType,omitempty"`
	Title       *string      `json:"title,omitempty"`
	Description *string      `json:"description,omitempty"`
	Size        *int64       `json:"size,omitempty"`
	Annotations *Annotations `json:"annotations,omitempty"`
}

func (r ResourceLink) GetType() string { return r.Type }

type EmbeddedResource struct {
	Type        string                   `json:"type"`
	Resource    EmbeddedResourceResource `json:"resource"`
	Annotations *Annotations             `json:"annotations,omitempty"`
}

func (e EmbeddedResource) GetType() string { return e.Type }

type EmbeddedResourceResource interface {
	GetUri() string
}

type TextResourceContents struct {
	Uri      string  `json:"uri"`
	Text     string  `json:"text"`
	MimeType *string `json:"mimeType,omitempty"`
}

func (t TextResourceContents) GetUri() string { return t.Uri }

type BlobResourceContents struct {
	Uri      string  `json:"uri"`
	Blob     string  `json:"blob"`
	MimeType *string `json:"mimeType,omitempty"`
}

func (b BlobResourceContents) GetUri() string { return b.Uri }

// Annotations for optional client metadata
type Annotations struct {
	Audience     []string `json:"audience,omitempty"`
	Priority     *float64 `json:"priority,omitempty"`
	LastModified *string  `json:"lastModified,omitempty"`
}

// Request/Response Types
type InitializeRequest struct {
	ProtocolVersion    ProtocolVersion    `json:"protocolVersion"`
	ClientCapabilities ClientCapabilities `json:"clientCapabilities"`
}

type InitializeResponse struct {
	ProtocolVersion   ProtocolVersion      `json:"protocolVersion"`
	AgentCapabilities ACPAgentCapabilities `json:"agentCapabilities"`
	AuthMethods       []AuthMethod         `json:"authMethods"`
}

type AuthenticateRequest struct {
	MethodId AuthMethodId `json:"methodId"`
}

type NewSessionRequest struct {
	Cwd        string      `json:"cwd"`
	McpServers []McpServer `json:"mcpServers"`
}

type NewSessionResponse struct {
	SessionId SessionId         `json:"sessionId"`
	Modes     *SessionModeState `json:"modes,omitempty"`
}

type LoadSessionRequest struct {
	SessionId  SessionId   `json:"sessionId"`
	Cwd        string      `json:"cwd"`
	McpServers []McpServer `json:"mcpServers"`
}

type LoadSessionResponse struct {
	Modes *SessionModeState `json:"modes,omitempty"`
}

type PromptRequest struct {
	SessionId SessionId      `json:"sessionId"`
	Prompt    []ContentBlock `json:"prompt"`
}

type PromptResponse struct {
	StopReason string `json:"stopReason"`
}

type ReadTextFileRequest struct {
	SessionId SessionId `json:"sessionId"`
	Path      string    `json:"path"`
	Line      *int      `json:"line,omitempty"`
	Limit     *int      `json:"limit,omitempty"`
}

type ReadTextFileResponse struct {
	Content string `json:"content"`
}

type WriteTextFileRequest struct {
	SessionId SessionId `json:"sessionId"`
	Path      string    `json:"path"`
	Content   string    `json:"content"`
}

type RequestPermissionRequest struct {
	SessionId SessionId          `json:"sessionId"`
	ToolCall  ToolCallUpdate     `json:"toolCall"`
	Options   []PermissionOption `json:"options"`
}

type RequestPermissionResponse struct {
	Outcome string `json:"outcome"`
}

type CancelNotification struct {
	SessionId SessionId `json:"sessionId"`
}

type SessionNotification struct {
	SessionId SessionId     `json:"sessionId"`
	Update    SessionUpdate `json:"update"`
}

// Session Update Types (Union Type)
type SessionUpdate interface {
	GetSessionUpdateType() string
}

type UserMessageChunk struct {
	SessionUpdate string       `json:"sessionUpdate"`
	Content       ContentBlock `json:"content"`
}

func (u UserMessageChunk) GetSessionUpdateType() string { return u.SessionUpdate }

type AgentMessageChunk struct {
	SessionUpdate string       `json:"sessionUpdate"`
	Content       ContentBlock `json:"content"`
}

func (a AgentMessageChunk) GetSessionUpdateType() string { return a.SessionUpdate }

type AgentThoughtChunk struct {
	SessionUpdate string       `json:"sessionUpdate"`
	Content       ContentBlock `json:"content"`
}

func (a AgentThoughtChunk) GetSessionUpdateType() string { return a.SessionUpdate }

type ToolCall struct {
	SessionUpdate string             `json:"sessionUpdate"`
	ToolCallId    ToolCallId         `json:"toolCallId"`
	Title         string             `json:"title"`
	Kind          *string            `json:"kind,omitempty"`
	Status        *string            `json:"status,omitempty"`
	Content       []ToolCallContent  `json:"content,omitempty"`
	Locations     []ToolCallLocation `json:"locations,omitempty"`
	RawInput      json.RawMessage    `json:"rawInput,omitempty"`
	RawOutput     json.RawMessage    `json:"rawOutput,omitempty"`
}

func (t ToolCall) GetSessionUpdateType() string { return t.SessionUpdate }

type ToolCallUpdate struct {
	SessionUpdate string             `json:"sessionUpdate"`
	ToolCallId    ToolCallId         `json:"toolCallId"`
	Status        *string            `json:"status,omitempty"`
	Content       []ToolCallContent  `json:"content,omitempty"`
	Kind          *string            `json:"kind,omitempty"`
	Locations     []ToolCallLocation `json:"locations,omitempty"`
	RawInput      json.RawMessage    `json:"rawInput,omitempty"`
	RawOutput     json.RawMessage    `json:"rawOutput,omitempty"`
}

func (t ToolCallUpdate) GetSessionUpdateType() string { return t.SessionUpdate }

type Plan struct {
	SessionUpdate string      `json:"sessionUpdate"`
	Entries       []PlanEntry `json:"entries"`
}

func (p Plan) GetSessionUpdateType() string { return p.SessionUpdate }

type AvailableCommandsUpdate struct {
	SessionUpdate     string             `json:"sessionUpdate"`
	AvailableCommands []AvailableCommand `json:"availableCommands"`
}

func (a AvailableCommandsUpdate) GetSessionUpdateType() string { return a.SessionUpdate }

type CurrentModeUpdate struct {
	SessionUpdate string        `json:"sessionUpdate"`
	CurrentModeId SessionModeId `json:"currentModeId"`
}

func (c CurrentModeUpdate) GetSessionUpdateType() string { return c.SessionUpdate }

// Tool and Permission Types
type ToolCallContent interface {
	GetContentType() string
}

// ToolCallContent union type variants
type ToolCallContentStandard struct {
	Type    string       `json:"type"`
	Content ContentBlock `json:"content"`
}

func (t ToolCallContentStandard) GetContentType() string { return t.Type }

type ToolCallContentDiff struct {
	Type    string  `json:"type"`
	Path    string  `json:"path"`
	OldText *string `json:"oldText"`
	NewText string  `json:"newText"`
}

func (t ToolCallContentDiff) GetContentType() string { return t.Type }

type ToolCallContentTerminal struct {
	Type       string `json:"type"`
	TerminalId string `json:"terminalId"`
}

func (t ToolCallContentTerminal) GetContentType() string { return t.Type }

type ToolCallLocation struct {
	Path string `json:"path"`
	Line *int   `json:"line,omitempty"`
}

type PermissionOption struct {
	Id   PermissionOptionId `json:"id"`
	Name string             `json:"name"`
	Kind string             `json:"kind"`
}

type PlanEntry struct {
	Content  string `json:"content"`
	Priority string `json:"priority"`
	Status   string `json:"status"`
}

// Session Mode Types (UNSTABLE)
type SessionModeState struct {
	Modes []SessionMode `json:"modes"`
}

type SessionMode struct {
	Id          string             `json:"id"`
	Name        string             `json:"name"`
	Description *string            `json:"description,omitempty"`
	Commands    []AvailableCommand `json:"commands,omitempty"`
}

type AvailableCommand struct {
	Name        string                       `json:"name"`
	Description string                       `json:"description"`
	Input       *AvailableCommandInputObject `json:"input,omitempty"`
}

// Union type for AvailableCommandInput (currently only one variant)
type AvailableCommandInput interface {
	GetInputType() string
}

type AvailableCommandInputObject struct {
	Hint string `json:"hint"`
}

func (a AvailableCommandInputObject) GetInputType() string { return "object" }

// Helper Functions for Content Blocks
func NewTextContent(text string) TextContent {
	return TextContent{
		Type: ContentBlockTypeText,
		Text: text,
	}
}

func NewImageContent(data, mimeType string) ImageContent {
	return ImageContent{
		Type:     ContentBlockTypeImage,
		Data:     data,
		MimeType: mimeType,
	}
}

func NewAudioContent(data, mimeType string) AudioContent {
	return AudioContent{
		Type:     ContentBlockTypeAudio,
		Data:     data,
		MimeType: mimeType,
	}
}

func NewResourceLink(uri, name string) ResourceLink {
	return ResourceLink{
		Type: ContentBlockTypeResourceLink,
		Uri:  uri,
		Name: name,
	}
}

func NewEmbeddedResource(resource EmbeddedResourceResource) EmbeddedResource {
	return EmbeddedResource{
		Type:     ContentBlockTypeResource,
		Resource: resource,
	}
}

func NewTextResourceContents(uri, text string) TextResourceContents {
	return TextResourceContents{
		Uri:  uri,
		Text: text,
	}
}

func NewBlobResourceContents(uri, blob string) BlobResourceContents {
	return BlobResourceContents{
		Uri:  uri,
		Blob: blob,
	}
}

// Helper Functions for Session Updates
func NewUserMessageChunk(content ContentBlock) UserMessageChunk {
	return UserMessageChunk{
		SessionUpdate: SessionUpdateUserMessageChunk,
		Content:       content,
	}
}

func NewAgentMessageChunk(content ContentBlock) AgentMessageChunk {
	return AgentMessageChunk{
		SessionUpdate: SessionUpdateAgentMessageChunk,
		Content:       content,
	}
}

func NewAgentThoughtChunk(content ContentBlock) AgentThoughtChunk {
	return AgentThoughtChunk{
		SessionUpdate: SessionUpdateAgentThoughtChunk,
		Content:       content,
	}
}

func NewToolCall(id ToolCallId, title string) ToolCall {
	return ToolCall{
		SessionUpdate: SessionUpdateToolCall,
		ToolCallId:    id,
		Title:         title,
	}
}

func NewToolCallUpdate(id ToolCallId) ToolCallUpdate {
	return ToolCallUpdate{
		SessionUpdate: SessionUpdateToolCallUpdate,
		ToolCallId:    id,
	}
}

func NewPlan(entries []PlanEntry) Plan {
	return Plan{
		SessionUpdate: SessionUpdatePlan,
		Entries:       entries,
	}
}

func NewPlanEntry(content, priority, status string) PlanEntry {
	return PlanEntry{
		Content:  content,
		Priority: priority,
		Status:   status,
	}
}

func NewAvailableCommandsUpdate(commands []AvailableCommand) AvailableCommandsUpdate {
	return AvailableCommandsUpdate{
		SessionUpdate:     SessionUpdateAvailableCommands,
		AvailableCommands: commands,
	}
}

func NewCurrentModeUpdate(modeId SessionModeId) CurrentModeUpdate {
	return CurrentModeUpdate{
		SessionUpdate: SessionUpdateCurrentMode,
		CurrentModeId: modeId,
	}
}

func NewToolCallContentStandard(content ContentBlock) ToolCallContentStandard {
	return ToolCallContentStandard{
		Type:    ToolCallContentTypeStandard,
		Content: content,
	}
}

func NewToolCallContentDiff(path string, oldText *string, newText string) ToolCallContentDiff {
	return ToolCallContentDiff{
		Type:    ToolCallContentTypeDiff,
		Path:    path,
		OldText: oldText,
		NewText: newText,
	}
}

func NewToolCallContentTerminal(terminalId string) ToolCallContentTerminal {
	return ToolCallContentTerminal{
		Type:       ToolCallContentTypeTerminal,
		TerminalId: terminalId,
	}
}

// Custom JSON marshaling/unmarshaling for union types
func UnmarshalContentBlock(data []byte) (ContentBlock, error) {
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}

	typeField, ok := raw["type"].(string)
	if !ok {
		return nil, fmt.Errorf("missing or invalid 'type' field in ContentBlock")
	}

	switch typeField {
	case ContentBlockTypeText:
		var textContent TextContent
		if err := json.Unmarshal(data, &textContent); err != nil {
			return nil, err
		}
		return textContent, nil
	case ContentBlockTypeImage:
		var imageContent ImageContent
		if err := json.Unmarshal(data, &imageContent); err != nil {
			return nil, err
		}
		return imageContent, nil
	case ContentBlockTypeAudio:
		var audioContent AudioContent
		if err := json.Unmarshal(data, &audioContent); err != nil {
			return nil, err
		}
		return audioContent, nil
	case ContentBlockTypeResourceLink:
		var resourceLink ResourceLink
		if err := json.Unmarshal(data, &resourceLink); err != nil {
			return nil, err
		}
		return resourceLink, nil
	case ContentBlockTypeResource:
		return unmarshalEmbeddedResource(data)
	default:
		return nil, fmt.Errorf("unknown ContentBlock type: %s", typeField)
	}
}

func UnmarshalSessionUpdate(data []byte) (SessionUpdate, error) {
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}

	sessionUpdateField, ok := raw["sessionUpdate"].(string)
	if !ok {
		return nil, fmt.Errorf("missing or invalid 'sessionUpdate' field in SessionUpdate")
	}

	switch sessionUpdateField {
	case SessionUpdateUserMessageChunk:
		return unmarshalUserMessageChunk(data)
	case SessionUpdateAgentMessageChunk:
		return unmarshalAgentMessageChunk(data)
	case SessionUpdateAgentThoughtChunk:
		return unmarshalAgentThoughtChunk(data)
	case SessionUpdateToolCall:
		var toolCall ToolCall
		if err := json.Unmarshal(data, &toolCall); err != nil {
			return nil, err
		}
		return toolCall, nil
	case SessionUpdateToolCallUpdate:
		var toolCallUpdate ToolCallUpdate
		if err := json.Unmarshal(data, &toolCallUpdate); err != nil {
			return nil, err
		}
		return toolCallUpdate, nil
	case SessionUpdatePlan:
		var plan Plan
		if err := json.Unmarshal(data, &plan); err != nil {
			return nil, err
		}
		return plan, nil
	case SessionUpdateAvailableCommands:
		var availableCommandsUpdate AvailableCommandsUpdate
		if err := json.Unmarshal(data, &availableCommandsUpdate); err != nil {
			return nil, err
		}
		return availableCommandsUpdate, nil
	case SessionUpdateCurrentMode:
		var currentModeUpdate CurrentModeUpdate
		if err := json.Unmarshal(data, &currentModeUpdate); err != nil {
			return nil, err
		}
		return currentModeUpdate, nil
	default:
		return nil, fmt.Errorf("unknown SessionUpdate type: %s", sessionUpdateField)
	}
}

func unmarshalUserMessageChunk(data []byte) (UserMessageChunk, error) {
	var raw struct {
		SessionUpdate string          `json:"sessionUpdate"`
		Content       json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return UserMessageChunk{}, err
	}

	content, err := UnmarshalContentBlock(raw.Content)
	if err != nil {
		return UserMessageChunk{}, err
	}

	return UserMessageChunk{
		SessionUpdate: raw.SessionUpdate,
		Content:       content,
	}, nil
}

func unmarshalAgentMessageChunk(data []byte) (AgentMessageChunk, error) {
	var raw struct {
		SessionUpdate string          `json:"sessionUpdate"`
		Content       json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return AgentMessageChunk{}, err
	}

	content, err := UnmarshalContentBlock(raw.Content)
	if err != nil {
		return AgentMessageChunk{}, err
	}

	return AgentMessageChunk{
		SessionUpdate: raw.SessionUpdate,
		Content:       content,
	}, nil
}

func unmarshalAgentThoughtChunk(data []byte) (AgentThoughtChunk, error) {
	var raw struct {
		SessionUpdate string          `json:"sessionUpdate"`
		Content       json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return AgentThoughtChunk{}, err
	}

	content, err := UnmarshalContentBlock(raw.Content)
	if err != nil {
		return AgentThoughtChunk{}, err
	}

	return AgentThoughtChunk{
		SessionUpdate: raw.SessionUpdate,
		Content:       content,
	}, nil
}

func UnmarshalToolCallContent(data []byte) (ToolCallContent, error) {
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}

	typeField, ok := raw["type"].(string)
	if !ok {
		return nil, fmt.Errorf("missing or invalid 'type' field in ToolCallContent")
	}

	switch typeField {
	case ToolCallContentTypeStandard:
		return unmarshalToolCallContentStandard(data)
	case ToolCallContentTypeDiff:
		var diffContent ToolCallContentDiff
		if err := json.Unmarshal(data, &diffContent); err != nil {
			return nil, err
		}
		return diffContent, nil
	case ToolCallContentTypeTerminal:
		var terminalContent ToolCallContentTerminal
		if err := json.Unmarshal(data, &terminalContent); err != nil {
			return nil, err
		}
		return terminalContent, nil
	default:
		return nil, fmt.Errorf("unknown ToolCallContent type: %s", typeField)
	}
}

func unmarshalToolCallContentStandard(data []byte) (ToolCallContentStandard, error) {
	var raw struct {
		Type    string          `json:"type"`
		Content json.RawMessage `json:"content"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return ToolCallContentStandard{}, err
	}

	content, err := UnmarshalContentBlock(raw.Content)
	if err != nil {
		return ToolCallContentStandard{}, err
	}

	return ToolCallContentStandard{
		Type:    raw.Type,
		Content: content,
	}, nil
}

func unmarshalEmbeddedResource(data []byte) (EmbeddedResource, error) {
	var raw struct {
		Type        string          `json:"type"`
		Resource    json.RawMessage `json:"resource"`
		Annotations *Annotations    `json:"annotations,omitempty"`
	}
	if err := json.Unmarshal(data, &raw); err != nil {
		return EmbeddedResource{}, err
	}

	resource, err := unmarshalEmbeddedResourceResource(raw.Resource)
	if err != nil {
		return EmbeddedResource{}, err
	}

	return EmbeddedResource{
		Type:        raw.Type,
		Resource:    resource,
		Annotations: raw.Annotations,
	}, nil
}

func unmarshalEmbeddedResourceResource(data []byte) (EmbeddedResourceResource, error) {
	var raw map[string]interface{}
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil, err
	}

	// Check if it has "text" field (TextResourceContents) or "blob" field (BlobResourceContents)
	if _, hasText := raw["text"]; hasText {
		var textRes TextResourceContents
		if err := json.Unmarshal(data, &textRes); err != nil {
			return nil, err
		}
		return textRes, nil
	} else if _, hasBlob := raw["blob"]; hasBlob {
		var blobRes BlobResourceContents
		if err := json.Unmarshal(data, &blobRes); err != nil {
			return nil, err
		}
		return blobRes, nil
	}

	return nil, fmt.Errorf("invalid EmbeddedResourceResource: must have either 'text' or 'blob' field")
}
