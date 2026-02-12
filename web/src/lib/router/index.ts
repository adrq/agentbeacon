import type { Screen } from '../types';
import { currentScreen, selectedExecutionId } from '../stores/appState';

interface RouteState {
  screen: Screen;
  executionId: string | null;
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
      return { screen: 'ExecutionDetail', executionId: execMatch[1] };
    }

    return { screen: 'Home', executionId: null };
  }

  private handleRouteChange() {
    const route = this.parseHash(window.location.hash);
    currentScreen.set(route.screen);
    selectedExecutionId.set(route.executionId);
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
