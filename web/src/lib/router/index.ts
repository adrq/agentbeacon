import type { Screen } from '../types';
import { currentScreen, selectedExecutionId, selectedProjectId } from '../stores/appState';

interface RouteState {
  screen: Screen;
  executionId: string | null;
  projectId: string | null;
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
      return { screen: 'ExecutionDetail', executionId: execMatch[1], projectId: null };
    }

    const projectDetailMatch = cleanHash.match(/^projects\/([^/]+)$/);
    if (projectDetailMatch) {
      return { screen: 'ProjectDetail', executionId: null, projectId: projectDetailMatch[1] };
    }

    if (cleanHash === 'projects') {
      return { screen: 'Projects', executionId: null, projectId: null };
    }

    if (cleanHash === 'agents') {
      return { screen: 'Agents', executionId: null, projectId: null };
    }

    return { screen: 'Home', executionId: null, projectId: null };
  }

  private handleRouteChange() {
    const route = this.parseHash(window.location.hash);
    currentScreen.set(route.screen);
    selectedExecutionId.set(route.executionId);
    selectedProjectId.set(route.projectId);
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
