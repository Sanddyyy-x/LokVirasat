import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app


@pytest.mark.asyncio
async def test_taxonomy_and_content_crud_with_fts_search():
    suffix = uuid.uuid4().hex[:8]
    creator_username = f"creator_{suffix}"
    viewer_username = f"viewer_{suffix}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Verify taxonomy endpoints
        regions_res = await client.get("/regions", params={"region_type": "district"})
        assert regions_res.status_code == 200
        regions = regions_res.json()
        assert any(r["name"] == "Bikaner" for r in regions)
        bikaner_id = next(r["region_id"] for r in regions if r["name"] == "Bikaner")

        cats_res = await client.get("/categories")
        assert cats_res.status_code == 200
        arch_id = next(c["category_id"] for c in cats_res.json() if c["slug"] == "architecture")

        # 2. Register a creator and a standard viewer
        creator_reg = await client.post(
            "/auth/register",
            json={
                "username": creator_username,
                "email": f"{creator_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Rohan Joshi",
                "is_creator": True,
            },
        )
        assert creator_reg.status_code == 201
        creator_headers = {"Authorization": f"Bearer {creator_reg.json()['access_token']}"}

        viewer_reg = await client.post(
            "/auth/register",
            json={
                "username": viewer_username,
                "email": f"{viewer_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Ananya Verma",
                "is_creator": False,
            },
        )
        viewer_headers = {"Authorization": f"Bearer {viewer_reg.json()['access_token']}"}

        # 3. Standard viewer without creator role cannot publish (403)
        forbidden = await client.post(
            "/contents",
            headers=viewer_headers,
            json={"content_type": "post", "caption_or_summary": "Should be blocked"},
        )
        assert forbidden.status_code == 403

        # 4. Creator publishes a cultural article
        unique_term = f"JunagarhFort{suffix}"
        create_res = await client.post(
            "/contents",
            headers=creator_headers,
            json={
                "content_type": "article",
                "title": f"{unique_term} Bastions",
                "caption_or_summary": "16th-century unconquered desert fort in Bikaner.",
                "language_code": "en",
                "region_id": bikaner_id,
                "category_id": arch_id,
                "historical_era": "16th Century",
                "tags": ["#Junagarh", "Bikaner", "bikaner"],
                "article_details": {
                    "body_markdown": "## History\nCommissioned by Raja Rai Singh in 1589.",
                    "reading_time_mins": 4,
                    "references_json": [{"source": "ASI Bikaner Circle"}],
                },
            },
        )
        assert create_res.status_code == 201, create_res.text
        created = create_res.json()
        content_id = created["content_id"]
        assert created["tags"] == ["junagarh", "bikaner"]
        assert created["article_details"]["reading_time_mins"] == 4

        # 5. Search & filter feed via FTS + tag + region
        feed_res = await client.get(
            "/contents",
            params={"q": unique_term, "tag": "junagarh", "region_id": bikaner_id},
        )
        assert feed_res.status_code == 200
        assert any(item["content_id"] == content_id for item in feed_res.json()["items"])

        # 6. Update & delete content
        patch_res = await client.patch(
            f"/contents/{content_id}",
            headers=creator_headers,
            json={"historical_era": "1589 CE - Mughal-Rajput Era"},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["historical_era"] == "1589 CE - Mughal-Rajput Era"

        del_res = await client.delete(f"/contents/{content_id}", headers=creator_headers)
        assert del_res.status_code == 204

    # Cleanup test users
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM users WHERE username IN (:u1, :u2)"),
            {"u1": creator_username, "u2": viewer_username},
        )
        await session.commit()