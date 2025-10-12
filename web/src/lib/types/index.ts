/**
 * TypeScript Type Definitions
 * Three-Screen UI Architecture with Placeholder Components
 *
 * This file exports all data model types and component interfaces for the application.
 */

// ============================================================================
// Core Types
// ============================================================================

export type Screen = 'Dashboard' | 'TemplateGallery' | 'WorkflowEditor' | 'RunDetails';
export type Theme = 'light' | 'dark';
export type WorkflowStatus = 'running' | 'idle';
export type RunStatus = 'running' | 'success' | 'failed';

// ============================================================================
// Data Models
// ============================================================================

export interface WorkflowCard {
  id: string;
  name: string;
  pinned: boolean;
  status: WorkflowStatus;
  runStats: {
    runningCount: number;
    completedToday: number;
  };
  version: string;
  lastStatus?: 'success' | 'failed';
  lastFailedTime?: string;
}

export interface ActivityEntry {
  id: string;
  workflowName: string;
  runNumber: number;
  status: RunStatus;
  startedAt: string;
  version: string;
  duration?: string;
  input?: string;
  output?: string;
  error?: string;
  progress?: number;
}

export interface TemplateCard {
  id: string;
  name: string;
  icon: string;
  usageCount: number;
  description: string;
}

export interface RunEntry {
  runNumber: number;
  status: RunStatus;
  version: string;
  startedAt: string;
  duration?: string;
  input: string;
  output?: string;
  error?: string;
  nodeProgress?: {
    completed: string[];
    running: string[];
    waiting: string[];
  };
}

export interface VersionEntry {
  version: string;
  isCurrent: boolean;
  timestamp: string;
  commitMessage: string;
  runStats: {
    total: number;
    success: number;
    failed: number;
    running: number;
  };
}

export interface RouteParams {
  workflowId?: string;
  runId?: string;
}

export interface TabState {
  screenId: Screen;
  activeTabIndex: number;
  tabs: string[];
}

export interface SplitPanelState {
  storageKey: string;
  leftPanelWidth: number;
}

export interface ThemeState {
  current: Theme;
}

// ============================================================================
// Component Props and Events
// ============================================================================

// Screen Components
export interface DashboardProps {
  theme: Theme;
}

export interface DashboardEvents {
  navigateToTemplateGallery: CustomEvent<void>;
  navigateToWorkflowEditor: CustomEvent<{ workflowId: string }>;
  navigateToRunDetails: CustomEvent<{ runId: string }>;
}

export interface TemplateGalleryProps {
  theme: Theme;
}

export interface TemplateGalleryEvents {
  navigateToDashboard: CustomEvent<void>;
  selectTemplate: CustomEvent<{ templateId: string }>;
}

export interface WorkflowEditorScreenProps {
  theme: Theme;
  params: RouteParams;
}

export interface WorkflowEditorScreenEvents {
  navigateToDashboard: CustomEvent<void>;
  navigateToRunDetails: CustomEvent<{ runId: string }>;
}

export interface RunDetailsProps {
  theme: Theme;
  params: RouteParams;
}

export interface RunDetailsEvents {
  navigateToWorkflowEditor: CustomEvent<{ workflowId: string }>;
}

// Shared UI Components
export interface SplitPanelProps {
  storageKey: string;
  initialLeftWidth?: number;
  minWidth?: number;
  maxWidth?: number;
}

export interface SplitPanelEvents {
  resize: CustomEvent<{ leftWidth: number }>;
}

export interface TabNavigationProps {
  tabs: string[];
  activeTabIndex: number;
  theme: Theme;
}

export interface TabNavigationEvents {
  tabChange: CustomEvent<{ index: number; label: string }>;
}

export interface BreadcrumbSegment {
  label: string;
  path: string;
}

export interface BreadcrumbProps {
  segments: BreadcrumbSegment[];
  theme: Theme;
}

export interface BreadcrumbEvents {
  navigate: CustomEvent<{ path: string }>;
}

export interface WorkflowCardComponentProps {
  workflow: WorkflowCard;
  theme: Theme;
}

export interface WorkflowCardComponentEvents {
  open: CustomEvent<void>;
  run: CustomEvent<void>;
}

export interface TemplateCardComponentProps {
  template: TemplateCard;
  theme: Theme;
}

export interface TemplateCardComponentEvents {
  use: CustomEvent<void>;
}

export interface ActivityEntryComponentProps {
  entry: ActivityEntry;
  theme: Theme;
}

export interface ActivityEntryComponentEvents {
  viewDetails: CustomEvent<void>;
  viewResults: CustomEvent<void>;
  debug: CustomEvent<void>;
  stop: CustomEvent<void>;
  rerun: CustomEvent<void>;
}

export interface RunEntryComponentProps {
  run: RunEntry;
  theme: Theme;
}

export interface RunEntryComponentEvents {
  viewDetails: CustomEvent<void>;
  stop: CustomEvent<void>;
  rerun: CustomEvent<void>;
  compare: CustomEvent<void>;
  debug: CustomEvent<void>;
}

export interface VersionEntryComponentProps {
  version: VersionEntry;
  theme: Theme;
}

export interface VersionEntryComponentEvents {
  view: CustomEvent<void>;
  run: CustomEvent<void>;
}

export interface StatusBadgeProps {
  status: RunStatus;
  size?: 'small' | 'medium' | 'large';
  theme: Theme;
}

export interface DiffViewerProps {
  filePath: string;
  beforeCode: string;
  afterCode: string;
  theme: Theme;
}

export interface DiffViewerEvents {
  acceptAll: CustomEvent<void>;
  rejectAll: CustomEvent<void>;
  reviewEach: CustomEvent<void>;
}

export interface ScreenHeaderProps {
  breadcrumbSegments: BreadcrumbSegment[];
  theme: Theme;
}

export interface ScreenHeaderEvents {
  navigate: CustomEvent<{ path: string }>;
}

export interface CollapsibleSectionProps {
  title: string;
  expanded?: boolean;
  theme: Theme;
}

export interface CollapsibleSectionEvents {
  toggle: CustomEvent<{ expanded: boolean }>;
}

// Modified Existing Components
export interface ModifiedWorkflowEditorProps {
  value: string;
  theme: Theme;
  readOnly?: boolean;
}

export interface ModifiedWorkflowEditorEvents {
  change: CustomEvent<string>;
}

export interface NodeStatus {
  [nodeId: string]: 'completed' | 'running' | 'waiting' | 'failed';
}

export interface ModifiedDAGVisualizationProps {
  workflow: string;
  isValid: boolean;
  theme: Theme;
  executionState?: NodeStatus;
  placeholderMode?: boolean;
}

export interface ErrorPanelProps {
  errors: Array<{
    type: 'syntax' | 'structural' | 'semantic';
    message: string;
    line?: number;
    node?: string;
    nodes?: string[];
  }>;
  theme: Theme;
  visible: boolean;
}

export interface ErrorPanelEvents {
  toggle: CustomEvent<void>;
}

export interface ModifiedOutputPanelProps {
  isExecuting: boolean;
  logs?: string[];
}

export interface ThemeToggleEvents {
  themeChange: CustomEvent<Theme>;
}

// ============================================================================
// Router and Store Contracts
// ============================================================================

export interface Router {
  navigate(path: string): void;
  getCurrentRoute(): { screen: Screen; params: RouteParams };
  onRouteChange(callback: (route: { screen: Screen; params: RouteParams }) => void): () => void;
}

export interface PlaceholderDataStore {
  workflowCards: WorkflowCard[];
  activityEntries: ActivityEntry[];
  templateCards: TemplateCard[];
  runEntries: RunEntry[];
  versionEntries: VersionEntry[];
  sampleYAML: string;
}
