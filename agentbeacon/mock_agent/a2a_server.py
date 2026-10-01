# SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A2A mode FastAPI server for HTTP JSON-RPC and agent card endpoints."""

from typing import Dict

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

from .task_store import TaskStore
from .jsonrpc import JSONRPCDispatcher
from .agent_card import create_agent_card_dict


class A2AServer:
    def __init__(self, port: int = 8080, custom_responses: Dict[str, str] = None):
        self.port = port
        self.app = FastAPI(title="Mock A2A Agent", version="1.0.0")
        self.task_store = TaskStore()
        self.jsonrpc_dispatcher = JSONRPCDispatcher(self.task_store, custom_responses)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get("/.well-known/agent-card.json")
        async def get_agent_card():
            base_url = f"http://localhost:{self.port}"
            card = create_agent_card_dict(base_url, self.port)
            return JSONResponse(
                content=card, headers={"content-type": "application/json"}
            )

        @self.app.post("/rpc")
        async def handle_jsonrpc(request: Request):
            try:
                request_data = await request.json()
                response_data = await self.jsonrpc_dispatcher.handle_request_async(
                    request_data
                )
                return JSONResponse(content=response_data)
            except Exception:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                )

    def run(self):
        uvicorn.run(
            self.app,
            host="0.0.0.0",
            port=self.port,
            log_level="error",
        )

    async def run_async(self):
        config = uvicorn.Config(
            self.app, host="0.0.0.0", port=self.port, log_level="error"
        )
        server = uvicorn.Server(config)
        await server.serve()


def start_a2a_server(port: int = 8080, custom_responses: Dict[str, str] = None):
    server = A2AServer(port, custom_responses)
    server.run()
