import type { NavSection } from '../types';
import {
  activeSection, sidebarOpen, selectedExecutionId,
  selectedProjectId, selectedAgentId,
} from '../stores/appState';

interface RouteState {
  section: NavSection;
  executionId: string | null;
  projectId: string | null;
  agentId: string | null;
}

type RouteChangeCallback = (route: RouteState) => void;

class HashRouter {
  private callbacks: Set<RouteChangeCallback> = new Set();

  constructor() {
    window.addEventListener('hashchange', () => this.handleRouteChange());
    this.handleRouteChange();
  }

  private parseHash(hash: string): RouteState {
    const cleanHash = hash.replace(/^#?\/?/, '');

    const execMatch = cleanHash.match(/^execution\/([^/]+)$/);
    if (execMatch) {
      return { section: 'executions', executionId: execMatch[1], projectId: null, agentId: null };
    }

    if (cleanHash === 'executions') {
      return { section: 'executions', executionId: null, projectId: null, agentId: null };
    }

    const projectDetailMatch = cleanHash.match(/^projects\/([^/]+)$/);
    if (projectDetailMatch) {
      return { section: 'projects', executionId: null, projectId: projectDetailMatch[1], agentId: null };
    }

    if (cleanHash === 'projects') {
      return { section: 'projects', executionId: null, projectId: null, agentId: null };
    }

    const agentDetailMatch = cleanHash.match(/^agents\/([^/]+)$/);
    if (agentDetailMatch) {
      return { section: 'agents', executionId: null, projectId: null, agentId: agentDetailMatch[1] };
    }

    if (cleanHash === 'agents') {
      return { section: 'agents', executionId: null, projectId: null, agentId: null };
    }

    return { section: 'home', executionId: null, projectId: null, agentId: null };
  }

  private handleRouteChange() {
    const route = this.parseHash(window.location.hash);
    activeSection.set(route.section);
    sidebarOpen.set(route.section !== 'home');
    selectedExecutionId.set(route.executionId);
    selectedProjectId.set(route.projectId);
    selectedAgentId.set(route.agentId);
    this.callbacks.forEach(cb => cb(route));
  }

  navigate(path: string): void {
    window.location.hash = path.startsWith('#') ? path.slice(1) : path;
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
