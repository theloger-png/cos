"""VM template management endpoints."""

from __future__ import annotations

import uuid

from common.models import VMTemplate as VMTemplateSchema
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Tenant, VMTemplate
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/templates", tags=["templates"])


class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    cpu_cores: int
    ram_mb: int
    disk_gb: int
    os_type: str
    image_path: str


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
    )


@router.get("", response_model=list[VMTemplateSchema])
async def list_templates(
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey, Tenant | None] = Depends(current_auth),
) -> list[VMTemplateSchema]:
    """List all available VM templates."""
    result = await session.execute(select(VMTemplate))
    return [_tpl_to_schema(t) for t in result.scalars().all()]


@router.post("", response_model=VMTemplateSchema, status_code=status.HTTP_201_CREATED)
async def create_template(
    body: TemplateCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey, Tenant | None] = Depends(current_auth),
) -> VMTemplateSchema:
    """Create a new VM template."""
    tpl = VMTemplate(
        name=body.name,
        description=body.description,
        cpu_cores=body.cpu_cores,
        ram_mb=body.ram_mb,
        disk_gb=body.disk_gb,
        os_type=body.os_type,
        image_path=body.image_path,
    )
    session.add(tpl)
    await session.commit()
    await session.refresh(tpl)
    return _tpl_to_schema(tpl)


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey, Tenant | None] = Depends(current_auth),
) -> None:
    """Delete a VM template."""
    result = await session.execute(select(VMTemplate).where(VMTemplate.id == template_id))
    tpl = result.scalar_one_or_none()
    if not tpl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    await session.delete(tpl)
    await session.commit()
