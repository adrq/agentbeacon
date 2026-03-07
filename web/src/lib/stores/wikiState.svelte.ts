import { safeGetItem, safeSetItem } from './appState';

const STORAGE_KEY = 'agentbeacon-wiki-tabs';

export interface WikiTab {
  id: string;
  type: 'search' | 'page';
  projectId?: string;
  slug?: string;
  title: string;
  editorDraft?: string;
  editorBaseRevision?: number;
  isCreate?: boolean;
  editorDraftTitle?: string;
}

function generateId(): string {
  return Math.random().toString(36).slice(2, 10);
}

function makeSearchTab(projectId?: string): WikiTab {
  return { id: generateId(), type: 'search', title: 'Search', projectId };
}

function loadTabs(): WikiTab[] {
  const raw = safeGetItem(STORAGE_KEY);
  if (!raw) return [makeSearchTab()];
  try {
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.length > 0) return parsed;
  } catch { /* corrupted */ }
  return [makeSearchTab()];
}

function loadActiveTabId(initialTabs: WikiTab[]): string {
  const stored = safeGetItem(STORAGE_KEY + '-active');
  if (stored && initialTabs.some(t => t.id === stored)) return stored;
  return initialTabs[0].id;
}

let tabs = $state<WikiTab[]>(loadTabs());
let activeTabId = $state<string>(loadActiveTabId(tabs));

function persistTabs(): void {
  safeSetItem(STORAGE_KEY, JSON.stringify(tabs));
}

function persistActiveTab(): void {
  safeSetItem(STORAGE_KEY + '-active', activeTabId);
}

function dedupKey(projectId: string, slug: string): string {
  return `page:${projectId}:${slug}`;
}

export function getWikiTabs(): WikiTab[] {
  return tabs;
}

export function getActiveTabId(): string {
  return activeTabId;
}

export function getActiveTab(): WikiTab {
  return tabs.find(t => t.id === activeTabId) ?? tabs[0];
}

export function openPage(projectId: string, slug: string, title: string, isCreate?: boolean): void {
  const existing = tabs.find(t => t.type === 'page' && t.projectId === projectId && t.slug === slug);
  if (existing) {
    if (isCreate && !existing.isCreate) {
      tabs = tabs.map(t => t.id === existing.id ? { ...t, isCreate: true } : t);
      persistTabs();
    }
    activeTabId = existing.id;
    persistActiveTab();
    return;
  }
  const key = dedupKey(projectId, slug);
  const newTab: WikiTab = { id: key, type: 'page', projectId, slug, title, isCreate };
  tabs = [...tabs, newTab];
  activeTabId = newTab.id;
  persistTabs();
  persistActiveTab();
}

export function closeTab(id: string): void {
  if (tabs.length <= 1) return;
  const idx = tabs.findIndex(t => t.id === id);
  if (idx === -1) return;
  // The first search tab is unclosable
  if (idx === 0 && tabs[0].type === 'search') return;
  tabs = tabs.filter(t => t.id !== id);
  if (activeTabId === id) {
    const newIdx = Math.min(idx, tabs.length - 1);
    activeTabId = tabs[newIdx].id;
  }
  persistTabs();
  persistActiveTab();
}

export function openSearchTab(projectId?: string): void {
  const newTab = makeSearchTab(projectId);
  tabs = [...tabs, newTab];
  activeTabId = newTab.id;
  persistTabs();
  persistActiveTab();
}

export function setActiveTab(id: string): void {
  if (tabs.some(t => t.id === id)) {
    activeTabId = id;
    persistActiveTab();
  }
}

export function updateTabDraft(id: string, draft: string): void {
  tabs = tabs.map(t => t.id === id ? { ...t, editorDraft: draft } : t);
  persistTabs();
}

export function updateTabEditMeta(id: string, baseRevision: number | undefined, draftTitle: string | undefined): void {
  tabs = tabs.map(t => t.id === id ? { ...t, editorBaseRevision: baseRevision, editorDraftTitle: draftTitle } : t);
  persistTabs();
}

export function clearTabCreateFlag(id: string): void {
  tabs = tabs.map(t => t.id === id ? { ...t, isCreate: undefined } : t);
  persistTabs();
}

export function updateTabProjectId(id: string, projectId: string | null): void {
  tabs = tabs.map(t => t.id === id ? { ...t, projectId: projectId ?? undefined } : t);
  persistTabs();
}

export function updateTabTitle(id: string, title: string): void {
  tabs = tabs.map(t => t.id === id ? { ...t, title } : t);
  persistTabs();
}

export function resetTabs(): void {
  const initial = makeSearchTab();
  tabs = [initial];
  activeTabId = initial.id;
  persistTabs();
  persistActiveTab();
}
