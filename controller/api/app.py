"""FastAPI application factory for the COS controller."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from controller.api.operations_middleware import OperationLogMiddleware
from controller.api.routers import nodes, vms, networks, tenants, templates, console, operations
from controller.api.routers import auth as auth_router


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    app = FastAPI(title="COS Controller", version="1.0.0")

    # Added first = innermost, so CORS headers still wrap its error responses.
    app.add_middleware(OperationLogMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router.router)
    app.include_router(nodes.router)
    app.include_router(vms.router)
    app.include_router(networks.router)
    app.include_router(tenants.router)
    app.include_router(templates.router)
    app.include_router(console.router)
    app.include_router(operations.router)

    return app
