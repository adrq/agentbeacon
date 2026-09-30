// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

const STATUS_LABELS: Record<string, string> = {
  working: 'Working',
  awaiting_input: 'Turn Complete',
  idle: 'Idle',
  stopped: 'Stopped',
  unassigned: 'Unassigned',
  crashed: 'Crashed',
  completed: 'Completed',
  failed: 'Failed',
  canceled: 'Canceled',
};

// Returns the server-restart pause label for a paused session, else the normal label.
export function sessionStatusLabel(
  status: string,
  desiredBy: string | null | undefined,
  normalLabel?: string,
): string {
  if (status === 'stopped' && desiredBy === 'system:restart') {
    return 'Paused (server restart)';
  }
  return normalLabel ?? STATUS_LABELS[status] ?? status;
}
