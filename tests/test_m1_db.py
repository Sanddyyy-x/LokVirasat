import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.core.database import AsyncSessionLocal
from app.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["public_tables"] >= 20  # 18 MVP + 2 tracking + schema_migrations


@pytest.mark.asyncio
async def test_seed_data_and_article_trigger():
    async with AsyncSessionLocal() as session:
        roles_count = await session.scalar(text("SELECT COUNT(*) FROM roles"))
        assert roles_count == 6

        # Verify trg_enforce_article_consistency rejects attaching article_details to a 'post'
        author_id = await session.scalar(text("SELECT user_id FROM users WHERE username = 'demo_creator'"))
        post_id = await session.scalar(
            text(
                """
                INSERT INTO contents (author_id, content_type, caption_or_summary)
                VALUES (:author_id, 'post', 'Trigger integrity test post')
                RETURNING content_id
                """
            ),
            {"author_id": author_id},
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                text(
                    """
                    INSERT INTO article_details (content_id, body_markdown)
                    VALUES (:content_id, 'Should fail because content_type is post')
                    """
                ),
                {"content_id": post_id},
            )
        await session.rollback()