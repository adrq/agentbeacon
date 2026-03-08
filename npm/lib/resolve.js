"use strict";

const PLATFORMS = {
  "linux-x64": "@agentbeacon/cli-linux-x64",
  "linux-arm64": "@agentbeacon/cli-linux-arm64",
};

function resolve(binaryName) {
  const key = `${process.platform}-${process.arch}`;
  const pkg = PLATFORMS[key];
  if (!pkg) {
    throw new Error(
      `AgentBeacon does not ship binaries for ${process.platform}-${process.arch}.\n` +
        `Supported platforms: ${Object.keys(PLATFORMS).join(", ")}.\n` +
        `Open an issue: https://github.com/adrq/agentbeacon/issues`
    );
  }
  try {
    return require.resolve(`${pkg}/bin/${binaryName}`);
  } catch (_e) {
    throw new Error(
      `Could not find the ${binaryName} binary. The platform package ${pkg} ` +
        `may not be installed.\nTry reinstalling: npm install -g agentbeacon`
    );
  }
}

module.exports = resolve;
