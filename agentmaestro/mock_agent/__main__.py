"""Console script entry point for mock agent."""

import argparse
import sys

from .stdio_mode import start_stdio_mode
from .a2a_server import start_a2a_server
from .acp_mode import start_acp_mode
from .config import load_responses


def main():
    """Main entry point for mock-agent console script."""
    import os

    os.environ["PYTHONUNBUFFERED"] = "1"
    sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)
    sys.stderr = os.fdopen(sys.stderr.fileno(), "w", buffering=1)

    parser = argparse.ArgumentParser(description="Mock A2A agent for testing")
    parser.add_argument(
        "--mode",
        choices=["stdio", "a2a", "acp"],
        default="stdio",
        help="Agent mode: stdio, a2a (HTTP JSON-RPC), or acp",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for A2A HTTP server (default: 8080)",
    )
    parser.add_argument("--config", type=str, help="Custom response file path")
    parser.add_argument(
        "--protocol-version",
        type=int,
        default=1,
        help="ACP protocol version to return in initialize (default: 1)",
    )
    parser.add_argument(
        "--hang-initialize",
        action="store_true",
        help="Hang indefinitely during ACP initialize (for timeout testing)",
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Run a scripted scenario instead of echo mode (e.g., 'demo')",
    )
    parser.add_argument(
        "--delegate-to",
        type=str,
        default=None,
        help="Child agent name for delegation scenarios",
    )
    parser.add_argument(
        "--delegate-count",
        type=int,
        default=2,
        help="Number of children for delegate-multi scenario (default: 2)",
    )

    args = parser.parse_args()

    custom_responses = load_responses(args.config)
    print(f"Mock agent starting in {args.mode} mode", file=sys.stderr)

    try:
        if args.mode == "stdio":
            start_stdio_mode(custom_responses)
        elif args.mode == "a2a":
            start_a2a_server(args.port, custom_responses)
        elif args.mode == "acp":
            start_acp_mode(
                custom_responses,
                protocol_version=args.protocol_version,
                hang_initialize=args.hang_initialize,
                scenario=args.scenario,
                delegate_to=args.delegate_to,
                delegate_count=args.delegate_count,
            )
        else:
            print(f"Unknown mode: {args.mode}", file=sys.stderr)
            return 1

    except KeyboardInterrupt:
        print("Mock agent interrupted", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Mock agent error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
