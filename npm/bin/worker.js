#!/usr/bin/env node
"use strict";

const { execFileSync } = require("child_process");
const resolve = require("../lib/resolve");

try {
  execFileSync(resolve("agentbeacon-worker"), process.argv.slice(2), {
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
