// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Test-only pauses at named sites.
//!
//! `AGENTBEACON_TEST_PAUSE_AT=<site>:<milliseconds>` makes `pause_at(<site>)`
//! sleep for that long. Unset in every normal run.

/// Sleep at `site` when the pause variable names it.
pub async fn pause_at(site: &str) {
    let Ok(value) = std::env::var("AGENTBEACON_TEST_PAUSE_AT") else {
        return;
    };
    let Some((name, millis)) = value.split_once(':') else {
        return;
    };
    if name != site {
        return;
    }
    if let Ok(millis) = millis.parse::<u64>() {
        tokio::time::sleep(std::time::Duration::from_millis(millis)).await;
    }
}
