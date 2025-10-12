/**
 * Placeholder Data Store
 * Hard-coded data for visual scaffold demonstration
 *
 * This file contains all placeholder data displayed in the three-screen UI.
 * NO backend integration - all data is static and hard-coded.
 */

import type {
  WorkflowCard,
  ActivityEntry,
  TemplateCard,
  RunEntry,
  VersionEntry
} from '../types';

// ============================================================================
// Workflow Cards (Dashboard)
// ============================================================================

export const workflowCards: WorkflowCard[] = [
  {
    id: 'workflow-1',
    name: 'Fix Frontend Tests',
    pinned: true,
    status: 'running',
    runStats: { runningCount: 2, completedToday: 5 },
    version: 'v1.2.0'
  },
  {
    id: 'workflow-2',
    name: 'Implement User Stories',
    pinned: true,
    status: 'running',
    runStats: { runningCount: 1, completedToday: 3 },
    version: 'v2.0.1'
  },
  {
    id: 'workflow-3',
    name: 'Update Dependencies',
    pinned: false,
    status: 'idle',
    runStats: { runningCount: 0, completedToday: 2 },
    version: 'v1.0.0',
    lastStatus: 'success'
  },
  {
    id: 'workflow-4',
    name: 'Refactor API',
    pinned: false,
    status: 'idle',
    runStats: { runningCount: 0, completedToday: 0 },
    version: 'v1.1.0',
    lastStatus: 'failed',
    lastFailedTime: '2h ago'
  }
];

// ============================================================================
// Activity Entries (Dashboard Recent Activity)
// ============================================================================

export const activityEntries: ActivityEntry[] = [
  {
    id: 'run-24',
    workflowName: 'Fix Frontend Tests',
    runNumber: 24,
    status: 'running',
    startedAt: '2 min ago',
    version: 'v1.2.0',
    input: 'Focus on authentication tests',
    progress: 60
  },
  {
    id: 'run-18',
    workflowName: 'Implement User Stories',
    runNumber: 18,
    status: 'success',
    startedAt: '1h ago',
    version: 'v2.0.1',
    duration: '12min',
    output: '15 files changed, 234 lines added'
  },
  {
    id: 'run-12',
    workflowName: 'Refactor API',
    runNumber: 12,
    status: 'failed',
    startedAt: '2h ago',
    version: 'v1.1.0',
    duration: '8min',
    error: 'Build failed - see logs'
  }
];

// ============================================================================
// Template Cards (Template Gallery)
// ============================================================================

export const templateCards: TemplateCard[] = [
  {
    id: 'template-1',
    name: 'Fix All Test Failures',
    icon: '🔧',
    usageCount: 287,
    description: 'Run tests, fix issues, verify pass'
  },
  {
    id: 'template-2',
    name: 'Implement Feature from Issue',
    icon: '📝',
    usageCount: 195,
    description: "Parse req's, generate impl + tests"
  },
  {
    id: 'template-3',
    name: 'Refactor for Performance',
    icon: '⚡',
    usageCount: 143,
    description: 'Profile & optimize w/ tests'
  },
  {
    id: 'template-4',
    name: 'Add Missing Tests',
    icon: '✅',
    usageCount: 128,
    description: 'Analyze coverage & generate'
  },
  {
    id: 'template-5',
    name: 'Update Dependencies Safely',
    icon: '📦',
    usageCount: 96,
    description: 'Check, test, fix breaks iteratively'
  }
];

// ============================================================================
// Run Entries (Workflow Editor → Runs Tab)
// ============================================================================

export const runEntries: RunEntry[] = [
  {
    runNumber: 24,
    status: 'running',
    version: 'v1.2.0',
    startedAt: '2m ago',
    input: 'Focus on auth tests',
    nodeProgress: {
      completed: ['analyze'],
      running: ['fix_tests'],
      waiting: ['validate']
    }
  },
  {
    runNumber: 23,
    status: 'success',
    version: 'v1.2.0',
    startedAt: '1h ago',
    duration: '12min',
    input: 'Fix login form tests',
    output: '8 files, 42 tests fixed'
  },
  {
    runNumber: 22,
    status: 'failed',
    version: 'v1.1.0',
    startedAt: '2h ago',
    duration: '8min',
    input: 'Fix all failing tests',
    error: 'Build failed at validation step'
  }
];

// ============================================================================
// Version Entries (Workflow Editor → Versions Tab)
// ============================================================================

export const versionEntries: VersionEntry[] = [
  {
    version: 'v1.2.0',
    isCurrent: true,
    timestamp: '2h ago',
    commitMessage: 'Added retry logic for flaky tests',
    runStats: { total: 5, success: 4, failed: 0, running: 1 }
  },
  {
    version: 'v1.1.0',
    isCurrent: false,
    timestamp: '1d ago',
    commitMessage: 'Fixed test detection pattern',
    runStats: { total: 8, success: 6, failed: 2, running: 0 }
  },
  {
    version: 'v1.0.0',
    isCurrent: false,
    timestamp: '3d ago',
    commitMessage: 'Initial workflow from template',
    runStats: { total: 3, success: 3, failed: 0, running: 0 }
  }
];

// ============================================================================
// Sample YAML (Workflow Editor → Definition Tab)
// ============================================================================

export const sampleYAML = `name: "Fix Frontend Tests"
description: "Analyze and fix failing frontend tests"

tasks:
  - id: analyze
    agent: claude-code
    prompt: "Analyze failing tests and identify root causes"
    depends_on: []

  - id: fix_tests
    agent: claude-code
    prompt: "Fix the identified test failures"
    depends_on: [analyze]

  - id: validate
    agent: cursor
    prompt: "Run tests to verify all fixes work"
    depends_on: [fix_tests]
`;
