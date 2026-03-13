import type { NavSection, RouteMode } from '../types';
import {
  activeSection, sidebarOpen, selectedExecutionId,
  selectedProjectId, selectedAgentId, routeMode,
} from '../stores/appState';

export interface RouteState {
  section: NavSection;
  mode: RouteMode;
  executionId: string | null;
  projectId: string | null;
  agentId: string | null;
  wikiSlug: string | null;
}

type RouteChangeCallback = (route: RouteState) => void;

class HashRouter {
  private callbacks: Set<RouteChangeCallback> = new Set();
  private guardCallbacks: Set<() => boolean> = new Set();
  private lastHash = '';
  private skipNextGuard = false;

  constructor() {
    this.lastHash = window.location.hash.replace(/^#?\/?/, '');
    window.addEventListener('hashchange', () => this.onHashChange());
    this.handleRouteChange();
  }

  addNavigationGuard(guard: () => boolean): () => void {
    this.guardCallbacks.add(guard);
    return () => this.guardCallbacks.delete(guard);
  }

  private shouldGuard(): boolean {
    return [...this.guardCallbacks].some(g => g());
  }

  private onHashChange() {
    if (this.skipNextGuard) {
      this.skipNextGuard = false;
    } else if (this.shouldGuard() && !window.confirm('You have unsaved changes. Leave anyway?')) {
      history.replaceState(null, '', '#/' + this.lastHash);
      return;
    }
    this.handleRouteChange();
  }

  private parseHash(hash: string): RouteState {
    const cleanHash = hash.replace(/^#?\/?/, '').split('?')[0];
    const base: Omit<RouteState, 'section' | 'mode'> = {
      executionId: null, projectId: null, agentId: null, wikiSlug: null,
    };

    // Execution detail (singular — existing route)
    const execMatch = cleanHash.match(/^execution\/([^/]+)$/);
    if (execMatch) {
      return { ...base, section: 'executions', mode: 'view', executionId: execMatch[1] };
    }

    // Executions: new form, then list
    if (cleanHash === 'executions/new') {
      return { ...base, section: 'executions', mode: 'new' };
    }
    if (cleanHash === 'executions') {
      return { ...base, section: 'executions', mode: 'view' };
    }

    // Projects: new, edit, detail, list — order matters (new/edit before {id})
    if (cleanHash === 'projects/new') {
      return { ...base, section: 'projects', mode: 'new' };
    }
    const projectEditMatch = cleanHash.match(/^projects\/([^/]+)\/edit$/);
    if (projectEditMatch) {
      return { ...base, section: 'projects', mode: 'edit', projectId: projectEditMatch[1] };
    }
    const projectDetailMatch = cleanHash.match(/^projects\/([^/]+)$/);
    if (projectDetailMatch) {
      return { ...base, section: 'projects', mode: 'view', projectId: projectDetailMatch[1] };
    }
    if (cleanHash === 'projects') {
      return { ...base, section: 'projects', mode: 'view' };
    }

    // Agents: new, edit, detail, list — order matters
    if (cleanHash === 'agents/new') {
      return { ...base, section: 'agents', mode: 'new' };
    }
    const agentEditMatch = cleanHash.match(/^agents\/([^/]+)\/edit$/);
    if (agentEditMatch) {
      return { ...base, section: 'agents', mode: 'edit', agentId: agentEditMatch[1] };
    }
    const agentDetailMatch = cleanHash.match(/^agents\/([^/]+)$/);
    if (agentDetailMatch) {
      return { ...base, section: 'agents', mode: 'view', agentId: agentDetailMatch[1] };
    }
    if (cleanHash === 'agents') {
      return { ...base, section: 'agents', mode: 'view' };
    }

    // Wiki routes: #/wiki, #/wiki/{projectId}/{slug}
    const wikiPageMatch = cleanHash.match(/^wiki\/([^/]+)\/(.+)$/);
    if (wikiPageMatch) {
      return { ...base, section: 'wiki', mode: 'view', projectId: wikiPageMatch[1], wikiSlug: wikiPageMatch[2].toLowerCase() };
    }
    if (cleanHash === 'wiki' || cleanHash.startsWith('wiki?')) {
      return { ...base, section: 'wiki', mode: 'view' };
    }

    if (cleanHash === 'settings') {
      return { ...base, section: 'settings', mode: 'view' };
    }

    return { ...base, section: 'home', mode: 'view' };
  }

  private handleRouteChange() {
    const route = this.parseHash(window.location.hash);
    this.lastHash = window.location.hash.replace(/^#?\/?/, '');
    activeSection.set(route.section);
    routeMode.set(route.mode);
    sidebarOpen.set(route.section !== 'home' && route.section !== 'wiki' && route.section !== 'settings');
    selectedExecutionId.set(route.executionId);
    // Don't set selectedProjectId for wiki routes — it would interfere with projects section
    if (route.section !== 'wiki') {
      selectedProjectId.set(route.projectId);
    }
    selectedAgentId.set(route.agentId);
    this.callbacks.forEach(cb => cb(route));
  }

  navigate(path: string): void {
    if (this.shouldGuard() && !window.confirm('You have unsaved changes. Leave anyway?')) return;
    const target = path.startsWith('#') ? path.slice(1) : path;
    // Only skip the next guard if the hash will actually change; otherwise
    // setting the hash is a no-op and skipNextGuard would stay stale.
    const currentClean = window.location.hash.replace(/^#?\/?/, '');
    const targetClean = target.replace(/^\//, '');
    if (currentClean !== targetClean) {
      this.skipNextGuard = true;
    }
    window.location.hash = target;
  }

  getCurrentRoute(): RouteState {
    return this.parseHash(window.location.hash);
  }

  onRouteChange(callback: RouteChangeCallback): () => void {
    this.callbacks.add(callback);
    return () => this.callbacks.delete(callback);
  }
}

export const router = new HashRouter();
