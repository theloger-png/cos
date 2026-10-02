"""VM template management endpoints."""

from __future__ import annotations

import asyncio
import os
import uuid
from urllib.parse import urlparse

from common.models import VMTemplate as VMTemplateSchema
from controller.agent_client.client import AgentClient
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Node, Tenant, VMTemplate
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])

_IMAGES_DIR = "/var/lib/libvirt/images"
# Must comfortably exceed the agent's own internal curl timeout (1850s) for
# template_image_fetch, or the controller would give up and report a false
# timeout failure while the agent is still legitimately downloading.
_FETCH_IMAGE_TIMEOUT_SECONDS = 1900


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    cpu_cores: int
    ram_mb: int
    disk_gb: int
    os_type: str
    image_path: str = ""
    image_url: str | None = None
    cloud_init_user: str = "ubuntu"


class FetchImageRequest(BaseModel):
    url: str | None = None


class NodeFetchResult(BaseModel):
    node_id: uuid.UUID
    hostname: str
    success: bool
    error: str | None = None


class FetchImageResponse(BaseModel):
    image_path: str
    results: list[NodeFetchResult]


def _filename_from_url(url: str) -> str:
    """Derive a local filename from a URL's path, e.g.
    https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img
    -> noble-server-cloudimg-amd64.img
    """
    name = os.path.basename(urlparse(url).path)
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not derive a filename from image_url (URL path is empty)",
        )
    return name


def _tpl_to_schema(t: VMTemplate) -> VMTemplateSchema:
    return VMTemplateSchema(
        id=t.id,
        name=t.name,
        description=t.description,
        cpu_cores=t.cpu_cores,
        ram_mb=t.ram_mb,
        disk_gb=t.disk_gb,
        os_type=t.os_type,
        image_path=t.image_path,
        image_url=t.image_url,
        cloud_init_user=t.cloud_init_user,
        created_at=t.created_at,
    )


@router.get("", response_model=list[VMTemplateSchema])
async def list_templates(
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> list[VMTemplateSchema]:
    """List all available VM templates."""
    result = await session.execute(select(VMTemplate))
    return [_tpl_to_schema(t) for t in result.scalars().all()]


@router.post("", response_model=VMTemplateSchema, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> VMTemplateSchema:
    """Create a new VM template.

    image_path and image_url are both optional individually, but at least
    one is required: give image_url for the normal flow (the image is
    downloaded to every node via POST .../fetch-image after creating the
    template; image_path is auto-derived from the URL's filename so it's
    ready as soon as the first successful download completes), or give
    image_path directly if you're pointing at a file placed on nodes by
    some other means (no image_url set, so .../fetch-image can't be used
    without also supplying a url there explicitly).
    """
    image_path = body.image_path
    if not image_path:
        if not body.image_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide at least one of image_path or image_url",
            )
        image_path = os.path.join(_IMAGES_DIR, _filename_from_url(body.image_url))

    tpl = VMTemplate(
        name=body.name,
        description=body.description,
        cpu_cores=body.cpu_cores,
        ram_mb=body.ram_mb,
        disk_gb=body.disk_gb,
        os_type=body.os_type,
        image_path=image_path,
        image_url=body.image_url,
        cloud_init_user=body.cloud_init_user,
    )
    session.add(tpl)
    await session.commit()
    await session.refresh(tpl)
    return _tpl_to_schema(tpl)


@router.post("/{template_id}/fetch-image", response_model=FetchImageResponse)
async def fetch_template_image(
    template_id: uuid.UUID,
    body: FetchImageRequest | None = None,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> FetchImageResponse:
    """Download a template's base image to every currently online node.

    Uses the URL in the request body if given, otherwise falls back to the
    template's already-stored image_url. Either way, a successful call
    updates the template's image_url (if it changed) and image_path (to the
    resolved local path) - so a template created with only image_path can
    be migrated to this flow by simply calling this endpoint with a url the
    first time.

    Returns per-node results so the caller can show which nodes succeeded
    and which didn't; the template's image_path is updated as long as at
    least one node succeeded; the overall HTTP call doesn't fail just
    because fewer than all nodes succeeded. Nodes that are offline at the
    time of the call are not included in nodes results and must be caught up
    by calling this endpoint again later (once they're back online).
    """
    result = await session.execute(select(VMTemplate).where(VMTemplate.id == template_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    url = (body.url if body and body.url else None) or tpl.image_url
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No image_url given in the request body or already stored on this template",
        )
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_url must start with http:// or https://",
        )
    filename = _filename_from_url(url)

    nodes_result = await session.execute(select(Node).where(Node.status == "online"))
    nodes = list(nodes_result.scalars().all())
    if not nodes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No nodes are currently online to download the image to",
        )

    agent = AgentClient()

    async def _fetch_on_node(node: Node) -> NodeFetchResult:
        cmd_result = await agent.send_command(
            node.ip_address,
            "template_image_fetch",
            {"url": url, "filename": filename},
            timeout_seconds=_FETCH_IMAGE_TIMEOUT_SECONDS,
        )
        return NodeFetchResult(
            node_id=node.id,
            hostname=node.hostname,
            success=cmd_result.success,
            error=None if cmd_result.success else cmd_result.error,
        )

    node_results = await asyncio.gather(*(_fetch_on_node(n) for n in nodes))

    if any(r.success for r in node_results):
        tpl.image_url = url
        tpl.image_path = os.path.join(_IMAGES_DIR, filename)
        await session.commit()

    return FetchImageResponse(image_path=tpl.image_path, results=list(node_results))


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> None:
    """Delete a VM template."""
    result = await session.execute(select(VMTemplate).where(VMTemplate.id == template_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    await session.delete(tpl)
    await session.commit()
