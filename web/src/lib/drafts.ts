// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Per-session composer drafts, persisted to localStorage.
 *
 * iOS Safari kills the content process under memory pressure and reloads the
 * page, which silently discarded whatever was in the composer. Draft text is
 * the one piece of unsent user work in the app, so it survives a reload.
 *
 * localStorage rather than sessionStorage: on iOS the OS frequently terminates
 * Safari outright, not just a tab, and sessionStorage is not reliably restored
 * across that. The cost is that drafts outlive the tab, hence the GC below.
 *
 * Attachments are deliberately not persisted — they are File objects with
 * object-URL previews and cannot be revived across a reload.
 */

const PREFIX = 'agentbeacon-draft-';

/** Drafts older than this are dropped on the next GC pass. */
const TTL_MS = 7 * 24 * 60 * 60 * 1000;

/** Hard cap on retained drafts, newest first. Bounds quota use on iOS (~5MB). */
const MAX_DRAFTS = 20;

interface StoredDraft {
  text: string;
  updatedAt: number;
}

function key(sessionId: string): string {
  return `${PREFIX}${sessionId}`;
}

/** Every draft key currently in localStorage. Returns [] if storage is unavailable. */
function draftKeys(): string[] {
  try {
    const keys: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k?.startsWith(PREFIX)) keys.push(k);
    }
    return keys;
  } catch {
    return [];
  }
}

function readAt(k: string): StoredDraft | null {
  try {
    const raw = localStorage.getItem(k);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredDraft;
    if (typeof parsed?.text !== 'string' || typeof parsed?.updatedAt !== 'number') return null;
    return parsed;
  } catch {
    // Unparseable or storage unavailable; treat as absent so a corrupt entry
    // can never block the composer.
    return null;
  }
}

/**
 * Drop expired drafts, then trim to MAX_DRAFTS newest. Entries that fail to
 * parse are removed too, since they can never be restored.
 */
export function gcDrafts(now: number = Date.now()): void {
  const live: { k: string; updatedAt: number }[] = [];
  for (const k of draftKeys()) {
    const entry = readAt(k);
    if (!entry || now - entry.updatedAt > TTL_MS) {
      try { localStorage.removeItem(k); } catch { /* storage unavailable */ }
      continue;
    }
    live.push({ k, updatedAt: entry.updatedAt });
  }
  if (live.length <= MAX_DRAFTS) return;
  live.sort((a, b) => b.updatedAt - a.updatedAt);
  for (const { k } of live.slice(MAX_DRAFTS)) {
    try { localStorage.removeItem(k); } catch { /* storage unavailable */ }
  }
}

/**
 * An expired draft reads as absent even before GC removes its key. Without this
 * the retention rule is defeated for any session you revisit: the restore effect
 * runs before the mount GC, so a stale draft reaches the composer and the
 * debounced save then writes it straight back with a fresh timestamp.
 */
export function loadDraft(sessionId: string, now: number = Date.now()): string {
  const entry = readAt(key(sessionId));
  if (!entry || now - entry.updatedAt > TTL_MS) return '';
  return entry.text;
}

/**
 * Persist (or clear, when empty) the draft for a session.
 *
 * Unlike the shared safeSetItem helper this does not swallow the write failure
 * outright: a quota error is the one case where losing the draft is the exact
 * bug being fixed, so we GC and retry once before giving up.
 */
export function saveDraft(sessionId: string, text: string): void {
  if (!text) {
    clearDraft(sessionId);
    return;
  }
  const payload = JSON.stringify({ text, updatedAt: Date.now() } satisfies StoredDraft);
  try {
    localStorage.setItem(key(sessionId), payload);
  } catch {
    gcDrafts();
    try {
      localStorage.setItem(key(sessionId), payload);
    } catch {
      // Storage genuinely unavailable (private mode) or still over quota.
      // Nothing further to do — the in-memory draft is unaffected.
    }
  }
}

export function clearDraft(sessionId: string): void {
  try { localStorage.removeItem(key(sessionId)); } catch { /* storage unavailable */ }
}
