// Svelte action: runs a callback when a reader reaches the top of a list.

export interface TopSentinelOptions {
  /** Identifies the list. Changing it resets the action's state. */
  identity: string;
  /** Scroll container the sentinel is observed within. */
  root: () => HTMLElement | undefined;
  /** False when `onReach` must not run. */
  enabled: () => boolean;
  /** Fetch exactly one older page. */
  onReach: (signal: AbortSignal) => Promise<void>;
}

/** Runs `onReach` when upward input reaches the node, one call at a time. */
export function topSentinel(node: HTMLElement, options: TopSentinelOptions) {
  let opts = options;
  let observed: HTMLElement | undefined;
  let observer: IntersectionObserver | undefined;
  let controller: AbortController | undefined;
  let inFlight = false;
  // Identifies the current run.
  let token = 0;
  // Set by upward input, cleared when a call starts.
  let reaching = false;
  let lastScrollTop = 0;
  // When upward input was last seen.
  let interactedAt = 0;
  const INTERACTION_WINDOW_MS = 1500;

  /** Whether the node currently overlaps the root. */
  function onScreen(): boolean {
    const root = opts.root();
    if (!root) return false;
    const node_ = node.getBoundingClientRect();
    const rootRect = root.getBoundingClientRect();
    return node_.bottom > rootRect.top && node_.top < rootRect.bottom;
  }

  async function pump(): Promise<void> {
    if (inFlight || !reaching || !onScreen() || !opts.enabled()) return;
    reaching = false;
    const mine = token;
    // Owned by this run.
    const own = new AbortController();
    inFlight = true;
    controller = own;
    try {
      await opts.onReach(own.signal);
    } catch {
      // Retried on the next intersection.
    } finally {
      if (controller === own) {
        inFlight = false;
        controller = undefined;
      }
    }
    if (mine !== token) return;
    await new Promise(resolve => requestAnimationFrame(() => resolve(null)));
    if (mine !== token) return;

    if (onScreen()) await pump();
  }

  function reachUp() {
    reaching = true;
    void pump();
  }

  function noteInteraction() {
    interactedAt = Date.now();
  }

  // Keys that move a scroll container upward. Space moves it DOWN unless it is
  // shifted, so bare Space is not one of them.
  const SCROLL_UP_KEYS = new Set(['PageUp', 'ArrowUp', 'Home']);
  const isUpwardKey = (event: KeyboardEvent) => {
    // A modified combination is a shortcut, not a scroll.
    if (event.ctrlKey || event.metaKey || event.altKey) return false;
    if (event.key === ' ' || event.key === 'Spacebar') return event.shiftKey;
    return SCROLL_UP_KEYS.has(event.key);
  };

  // Elements a key press acts on rather than scrolling.
  const CONTROLS = [
    'a[href]', 'button', 'input', 'select', 'textarea', 'summary',
    '[contenteditable=""]', '[contenteditable="true"]',
    '[role="button"]', '[role="link"]', '[role="checkbox"]', '[role="radio"]',
    '[role="tab"]', '[role="menuitem"]', '[role="switch"]', '[role="option"]',
  ].join(',');

  /**
   * Reaches on an upward key while the node is on screen.
   *
   * Ignores presses whose target is a control within the root.
   */
  function onKeyDown(event: KeyboardEvent) {
    if (!isUpwardKey(event)) return;
    const target = event.target as Element | null;
    if (target && target !== opts.root() && target.closest?.(CONTROLS)) return;
    noteInteraction();
    if (onScreen()) reachUp();
  }

  /**
   * Notes a press whose x lies at or beyond the root's `clientWidth`.
   *
   * Measured against the root's box, not the event target's.
   */
  function onPointerDown(event: PointerEvent) {
    const root = opts.root();
    if (!root) return;
    if (event.clientX - root.getBoundingClientRect().left >= root.clientWidth) noteInteraction();
  }

  // Only upward input is noted.
  function onWheel(event: WheelEvent) {
    if (event.deltaY >= 0) return;
    noteInteraction();
    reachUp();
  }

  // A finger travelling down the screen moves the content down.
  let touchY: number | null = null;

  // Records where the touch began; direction comes from the move.
  function onTouchStart(event: TouchEvent) {
    touchY = event.touches[0]?.clientY ?? null;
  }

  function onTouchMove(event: TouchEvent) {
    const y = event.touches[0]?.clientY ?? null;
    if (y !== null && touchY !== null && y > touchY) {
      noteInteraction();
      reachUp();
    }
    touchY = y;
  }

  function onTouchEnd() {
    touchY = null;
  }

  function onScroll() {
    const root = opts.root();
    if (!root) return;
    // Upward movement within INTERACTION_WINDOW_MS of upward input.
    const moved = root.scrollTop < lastScrollTop;
    lastScrollTop = root.scrollTop;
    if (moved && Date.now() - interactedAt < INTERACTION_WINDOW_MS) reachUp();
  }

  function detach(root: HTMLElement | undefined) {
    root?.removeEventListener('wheel', onWheel);
    root?.removeEventListener('scroll', onScroll);
    root?.removeEventListener('touchstart', onTouchStart as EventListener);
    root?.removeEventListener('touchmove', onTouchMove as EventListener);
    root?.removeEventListener('touchend', onTouchEnd);
    root?.removeEventListener('touchcancel', onTouchEnd);
    root?.removeEventListener('pointerdown', onPointerDown as EventListener);
    root?.removeEventListener('keydown', onKeyDown as EventListener);
  }

  function observe() {
    const root = opts.root();
    if (root === observed) return;
    observer?.disconnect();
    detach(observed);
    observed = root;
    touchY = null;
    if (!root) return;
    lastScrollTop = root.scrollTop;
    root.addEventListener('wheel', onWheel, { passive: true });
    root.addEventListener('scroll', onScroll, { passive: true });
    root.addEventListener('touchstart', onTouchStart as EventListener, { passive: true });
    root.addEventListener('touchmove', onTouchMove as EventListener, { passive: true });
    root.addEventListener('touchend', onTouchEnd, { passive: true });
    root.addEventListener('touchcancel', onTouchEnd, { passive: true });
    root.addEventListener('pointerdown', onPointerDown as EventListener, { passive: true });
    root.addEventListener('keydown', onKeyDown as EventListener, { passive: true });
    observer = new IntersectionObserver(
      entries => {
        if (entries.some(e => e.isIntersecting)) void pump();
      },
      { root },
    );
    observer.observe(node);
  }

  observe();

  return {
    update(next: TopSentinelOptions) {
      const changed = next.identity !== opts.identity;
      opts = next;
      if (changed) {
        // Abandons the call in flight and every trace of the previous list.
        token += 1;
        controller?.abort();
        controller = undefined;
        inFlight = false;
        reaching = false;
        interactedAt = 0;
        // A touch in progress belongs to the previous list.
        touchY = null;
        const root = opts.root();
        if (root) lastScrollTop = root.scrollTop;
      }
      observe();
    },
    destroy() {
      token += 1;
      observer?.disconnect();
      detach(observed);
      controller?.abort();
      controller = undefined;
    },
  };
}
