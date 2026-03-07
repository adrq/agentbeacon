"""Support python -m agentbeacon by exec'ing the binary."""

import os
import sys

from agentbeacon._find_binary import _find_binary


def main():
    binary = _find_binary("agentbeacon")
    args = [binary, *sys.argv[1:]]
    if sys.platform == "win32":
        import subprocess

        raise SystemExit(subprocess.run(args).returncode)
    else:
        os.execvp(binary, args)


if __name__ == "__main__":
    main()
