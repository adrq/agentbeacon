"""A2A mode FastAPI server for HTTP JSON-RPC and agent card endpoints."""

from typing import Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from .task_store import TaskStore
from .jsonrpc import JSONRPCDispatcher
from .agent_card import create_agent_card_dict


class A2AServer:
    """FastAPI server for A2A protocol over HTTP."""

    def __init__(self, port: int = 8080, custom_responses: Dict[str, str] = None):
        self.port = port
        self.app = FastAPI(title="Mock A2A Agent", version="1.0.0")
        self.task_store = TaskStore()
        self.jsonrpc_dispatcher = JSONRPCDispatcher(self.task_store, custom_responses)
        self._setup_routes()

    def _setup_routes(self):
        """Configure FastAPI routes."""

        @self.app.get("/.well-known/agent-card.json")
        async def get_agent_card():
            """Return A2A agent card."""
            base_url = f"http://localhost:{self.port}"
            card = create_agent_card_dict(base_url, self.port)
            return JSONResponse(
                content=card, headers={"content-type": "application/json"}
            )

        @self.app.post("/rpc")
        async def handle_jsonrpc(request: Request):
            """Handle JSON-RPC requests."""
            try:
                request_data = await request.json()
                response_data = await self.jsonrpc_dispatcher.handle_request_async(
                    request_data
                )
                return JSONResponse(content=response_data)
            except Exception:
                # Return JSON-RPC error for invalid requests
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                )

    def run(self):
        """Start the FastAPI server."""
        uvicorn.run(
            self.app,
            host="0.0.0.0",
            port=self.port,
            log_level="error",  # Reduce log noise for testing
        )

    async def run_async(self):
        """Start the FastAPI server asynchronously."""
        config = uvicorn.Config(
            self.app, host="0.0.0.0", port=self.port, log_level="error"
        )
        server = uvicorn.Server(config)
        await server.serve()


def start_a2a_server(port: int = 8080, custom_responses: Dict[str, str] = None):
    """Start A2A server on specified port."""
    server = A2AServer(port, custom_responses)
    server.run()
