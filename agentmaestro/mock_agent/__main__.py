"""Console script entry point for mock agent."""

import argparse
import sys

from .stdio_mode import start_stdio_mode
from .a2a_server import start_a2a_server
from .acp_mode import start_acp_mode
from .config import load_responses


def main():
    """Main entry point for mock-agent console script."""
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

    args = parser.parse_args()

    # Load custom responses from config file
    custom_responses = load_responses(args.config)

    # Print startup message to stderr
    print(f"Mock agent starting in {args.mode} mode", file=sys.stderr)

    try:
        # Dispatch to appropriate mode handler
        if args.mode == "stdio":
            start_stdio_mode(custom_responses)
        elif args.mode == "a2a":
            start_a2a_server(args.port, custom_responses)
        elif args.mode == "acp":
            start_acp_mode(custom_responses)
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
