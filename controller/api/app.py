"""FastAPI application factory for the COS controller."""

from __future__ import annotations

from fastapi import FastAPI

from controller.api.routers import nodes, vms, networks, tenants, templates


def create_app() -> FastAPI:
    """Construct and configure the FastAPI application."""
    app = FastAPI(title="COS Controller", version="1.0.0")

    app.include_router(nodes.router)
    app.include_router(vms.router)
    app.include_router(networks.router)
    app.include_router(tenants.router)
    app.include_router(templates.router)

    return app
