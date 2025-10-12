/**
 * Hash-Based Router
 * Simple client-side router for three-screen UI architecture
 *
 * Routes:
 * - / or /#/ → Dashboard
 * - /#/templates → TemplateGallery
 * - /#/editor/:workflowId → WorkflowEditorScreen
 * - /#/run/:runId → RunDetails
 *
 * Features:
 * - Hash-based routing (no server-side configuration needed)
 * - URL parameter extraction
 * - Browser back/forward support via hashchange event
 * - Route change subscriptions
 */

import type { Screen, RouteParams, Router } from '../types';
import { currentScreen, routeParams } from '../stores/appState';

type RouteChangeCallback = (route: { screen: Screen; params: RouteParams }) => void;

class HashRouter implements Router {
  private callbacks: Set<RouteChangeCallback> = new Set();

  constructor() {
    this.init();
  }

  private init() {
    window.addEventListener('hashchange', () => this.handleRouteChange());
    this.handleRouteChange();
  }

  private parseHash(hash: string): { screen: Screen; params: RouteParams } {
    const cleanHash = hash.replace(/^#?\/?/, '');

    if (cleanHash === '' || cleanHash === '/') {
      return { screen: 'Dashboard', params: {} };
    }

    if (cleanHash === 'templates') {
      return { screen: 'TemplateGallery', params: {} };
    }

    const editorMatch = cleanHash.match(/^editor\/([^/]+)$/);
    if (editorMatch) {
      return { screen: 'WorkflowEditor', params: { workflowId: editorMatch[1] } };
    }

    const runMatch = cleanHash.match(/^run\/([^/]+)$/);
    if (runMatch) {
      return { screen: 'RunDetails', params: { runId: runMatch[1] } };
    }

    return { screen: 'Dashboard', params: {} };
  }

  private handleRouteChange() {
    const route = this.parseHash(window.location.hash);
    currentScreen.set(route.screen);
    routeParams.set(route.params);

    this.callbacks.forEach(callback => callback(route));
  }

  navigate(path: string): void {
    const cleanPath = path.startsWith('#') ? path.slice(1) : path;
    window.location.hash = cleanPath;
  }

  getCurrentRoute(): { screen: Screen; params: RouteParams } {
    return this.parseHash(window.location.hash);
  }

  onRouteChange(callback: RouteChangeCallback): () => void {
    this.callbacks.add(callback);
    return () => this.callbacks.delete(callback);
  }
}

export const router = new HashRouter();
