from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db

router = APIRouter(tags=["taxonomy"])


class RegionOut(BaseModel):
    region_id: int
    name: str
    region_type: str
    parent_region_id: int | None
    iso_code: str | None


class CategoryOut(BaseModel):
    category_id: int
    slug: str
    name: str
    parent_category_id: int | None
    description: str | None


@router.get("/regions", response_model=list[RegionOut])
async def list_regions(
    region_type: str | None = Query(default=None),
    parent_region_id: int | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    params: dict = {}

    if region_type:
        conditions.append("region_type = :region_type")
        params["region_type"] = region_type.strip().lower()
    if parent_region_id is not None:
        conditions.append("parent_region_id = :parent_region_id")
        params["parent_region_id"] = parent_region_id

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    rows = (
        await db.execute(
            text(
                f"""
                SELECT region_id, name, region_type, parent_region_id, iso_code
                FROM regions
                {where_sql}
                ORDER BY region_id ASC
                """
            ),
            params,
        )
    ).mappings().all()

    return [RegionOut(**dict(r)) for r in rows]


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            text(
                """
                SELECT category_id, slug, name, parent_category_id, description
                FROM heritage_categories
                ORDER BY category_id ASC
                """
            )
        )
    ).mappings().all()

    return [CategoryOut(**dict(r)) for r in rows]