// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

export interface MenuNavOptions {
  /** Called when the menu should close. */
  onclose: () => void;
  /** The button that opened the menu; focus returns to it on Escape. */
  trigger?: HTMLElement | null;
  /** Which item gets focus on open: the first one (default) or the checked one. */
  initialFocus?: 'first' | 'checked';
}

const ITEM_SELECTOR = '[role="menuitem"], [role="menuitemradio"], [role="menuitemcheckbox"]';

/**
 * Keyboard and dismissal behaviour for a popup menu: focus moves to the first
 * (or checked) item on open, arrow keys, Home and End move between items, Escape
 * closes and returns focus to the trigger, Tab and an outside press close. It
 * also closes when a layout change hides the trigger (resize, narrower pane).
 */
export function menuNav(node: HTMLElement, options: MenuNavOptions) {
  let opts = options;

  const items = () =>
    [...node.querySelectorAll<HTMLElement>(ITEM_SELECTOR)].filter(el => !(el as HTMLButtonElement).disabled);

  function close(refocus: boolean) {
    opts.onclose();
    if (refocus) opts.trigger?.focus();
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      close(true);
      return;
    }
    if (e.key === 'Tab') {
      close(false);
      return;
    }
    const list = items();
    if (list.length === 0) return;
    const current = list.indexOf(document.activeElement as HTMLElement);
    let next = -1;
    if (e.key === 'ArrowDown') next = (current + 1) % list.length;
    else if (e.key === 'ArrowUp') next = (current - 1 + list.length) % list.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = list.length - 1;
    if (next >= 0) {
      e.preventDefault();
      list[next].focus();
    }
  }

  function onPointerDown(e: PointerEvent) {
    const target = e.target as Node;
    if (!node.contains(target) && !opts.trigger?.contains(target)) close(false);
  }

  // A hidden trigger takes its menu out of view too; close rather than stay open unseen.
  const resizeObserver = new ResizeObserver(() => {
    if (opts.trigger && opts.trigger.getClientRects().length === 0) close(false);
  });
  if (opts.trigger) resizeObserver.observe(opts.trigger);

  node.addEventListener('keydown', onKeydown);
  document.addEventListener('pointerdown', onPointerDown);
  const list = items();
  const checked = opts.initialFocus === 'checked' ? list.find(el => el.getAttribute('aria-checked') === 'true') : undefined;
  (checked ?? list[0])?.focus();

  return {
    update(next: MenuNavOptions) {
      opts = next;
    },
    destroy() {
      resizeObserver.disconnect();
      node.removeEventListener('keydown', onKeydown);
      document.removeEventListener('pointerdown', onPointerDown);
    },
  };
}
