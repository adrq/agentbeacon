// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { writable } from 'svelte/store';

/** True once the server has answered 401: the login view replaces the app. */
export const signedOut = writable(false);

export function markSignedOut() {
  signedOut.set(true);
}
