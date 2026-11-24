import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app


@pytest.mark.asyncio
async def test_recommendation_ready_interaction_and_search_tracking():
    suffix = uuid.uuid4().hex[:8]
    creator_username = f"rec_creator_{suffix}"
    learner_username = f"rec_learner_{suffix}"
    guest_session = f"guest_sess_{suffix}"
    user_session = f"user_sess_{suffix}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Lookup taxonomy IDs (Bikaner + Folk Music)
        regions = (await client.get("/regions", params={"region_type": "district"})).json()
        bikaner_id = next(r["region_id"] for r in regions if r["name"] == "Bikaner")

        categories = (await client.get("/categories")).json()
        folk_music_id = next(c["category_id"] for c in categories if c["slug"] == "folk-music")

        # 2. Register creator & learner
        c_res = await client.post(
            "/auth/register",
            json={
                "username": creator_username,
                "email": f"{creator_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Sartaj Manganiyar",
                "is_creator": True,
            },
        )
        assert c_res.status_code == 201
        creator_id = c_res.json()["user"]["user_id"]
        c_headers = {"Authorization": f"Bearer {c_res.json()['access_token']}"}

        l_res = await client.post(
            "/auth/register",
            json={
                "username": learner_username,
                "email": f"{learner_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Priya Rathore",
                "is_creator": False,
            },
        )
        assert l_res.status_code == 201
        l_headers = {"Authorization": f"Bearer {l_res.json()['access_token']}"}

        # 3. Publish a cultural post tagged with Bikaner + Folk Music
        post_res = await client.post(
            "/contents",
            headers=c_headers,
            json={
                "content_type": "post",
                "title": "Kamaicha Ballads of Western Rajasthan",
                "caption_or_summary": "Bow-played folk instrument tradition of Manganiyar musicians.",
                "region_id": bikaner_id,
                "category_id": folk_music_id,
                "tags": ["kamaicha", "manganiyar", "folk"],
            },
        )
        assert post_res.status_code == 201
        content_id = post_res.json()["content_id"]

        # 4. Guest (unauthenticated) impression + search log
        guest_imp = await client.post(
            "/interactions",
            json={
                "session_id": guest_session,
                "event_type": "impression",
                "feed_source": "explore",
                "content_id": content_id,
                "position_index": 0,
            },
        )
        assert guest_imp.status_code == 201
        guest_body = guest_imp.json()
        assert guest_body["user_id"] is None
        assert guest_body["context_json"]["region_id"] == bikaner_id
        assert guest_body["context_json"]["category_slug"] == "folk-music"
        assert "kamaicha" in guest_body["context_json"]["tags"]

        search_res = await client.post(
            "/interactions/searches",
            json={
                "session_id": guest_session,
                "query_text": "Kamaicha folk music",
                "region_id": bikaner_id,
                "category_id": folk_music_id,
                "content_type": "post",
                "result_count": 1,
            },
        )
        assert search_res.status_code == 201
        assert search_res.json()["query_text"] == "Kamaicha folk music"

        # 5. Authenticated batch interactions: impression, view (dwell_ms), video_progress, skip, like, bookmark, follow
        batch_res = await client.post(
            "/interactions/batch",
            headers=l_headers,
            json={
                "events": [
                    {
                        "session_id": user_session,
                        "event_type": "impression",
                        "feed_source": "home",
                        "content_id": content_id,
                        "position_index": 1,
                    },
                    {
                        "session_id": user_session,
                        "event_type": "view",
                        "feed_source": "home",
                        "content_id": content_id,
                        "dwell_ms": 14500,
                    },
                    {
                        "session_id": user_session,
                        "event_type": "video_progress",
                        "feed_source": "reels",
                        "content_id": content_id,
                        "watch_ms": 38000,
                        "completion_pct": 84.44,
                    },
                    {
                        "session_id": user_session,
                        "event_type": "like",
                        "feed_source": "home",
                        "content_id": content_id,
                    },
                    {
                        "session_id": user_session,
                        "event_type": "bookmark",
                        "feed_source": "home",
                        "content_id": content_id,
                    },
                    {
                        "session_id": user_session,
                        "event_type": "follow",
                        "feed_source": "creator_profile",
                        "target_user_id": creator_id,
                    },
                ]
            },
        )
        assert batch_res.status_code == 201, batch_res.text
        assert batch_res.json()["recorded_count"] == 6

        # Verify view_count incremented on content
        content_after = (await client.get(f"/contents/{content_id}")).json()
        assert content_after["view_count"] >= 1

        # 6. Verify user taxonomy affinity summary & popularity baseline feed
        affinity = await client.get("/interactions/affinity/me", headers=l_headers)
        assert affinity.status_code == 200
        aff_data = affinity.json()
        assert any(r["region_id"] == bikaner_id for r in aff_data["top_regions"])
        assert any(c["category_id"] == folk_music_id for c in aff_data["top_categories"])
        assert any(t["tag"] == "kamaicha" for t in aff_data["top_tags"])

        baseline = await client.get(
            "/recommendations/baseline",
            params={"region_id": bikaner_id, "category_id": folk_music_id},
        )
        assert baseline.status_code == 200
        assert any(item["content_id"] == content_id for item in baseline.json())

    # Cleanup test records
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM search_queries WHERE session_id IN (:s1, :s2)"),
            {"s1": guest_session, "s2": user_session},
        )
        await session.execute(
            text("DELETE FROM user_interactions WHERE session_id IN (:s1, :s2)"),
            {"s1": guest_session, "s2": user_session},
        )
        await session.execute(
            text("DELETE FROM users WHERE username IN (:u1, :u2)"),
            {"u1": creator_username, "u2": learner_username},
        )
        await session.commit()