#!/usr/bin/env node
// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

"use strict";

const { execFileSync } = require("child_process");
const resolve = require("../lib/resolve");

try {
  execFileSync(resolve("agentbeacon"), process.argv.slice(2), {
    stdio: "inherit",
  });
} catch (e) {
  if (typeof e.status === "number") {
    process.exit(e.status);
  }
  if (e.signal) {
    process.kill(process.pid, e.signal);
  }
  throw e;
}
