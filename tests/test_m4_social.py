import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app


@pytest.mark.asyncio
async def test_likes_threaded_comments_follows_and_bookmarks():
    suffix = uuid.uuid4().hex[:8]
    creator_username = f"soc_creator_{suffix}"
    fan_username = f"soc_fan_{suffix}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register creator & fan
        c_res = await client.post(
            "/auth/register",
            json={
                "username": creator_username,
                "email": f"{creator_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Meenakshi Nair",
                "is_creator": True,
            },
        )
        assert c_res.status_code == 201
        creator_id = c_res.json()["user"]["user_id"]
        c_headers = {"Authorization": f"Bearer {c_res.json()['access_token']}"}

        f_res = await client.post(
            "/auth/register",
            json={
                "username": fan_username,
                "email": f"{fan_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Devansh Kulkarni",
                "is_creator": False,
            },
        )
        assert f_res.status_code == 201
        f_headers = {"Authorization": f"Bearer {f_res.json()['access_token']}"}

        # 2. Creator publishes a post
        post_res = await client.post(
            "/contents",
            headers=c_headers,
            json={
                "content_type": "post",
                "caption_or_summary": "Kathakali mudra workshop in Kochi",
                "tags": ["kathakali", "kerala"],
            },
        )
        assert post_res.status_code == 201
        content_id = post_res.json()["content_id"]

        # 3. Idempotent Like & Unlike
        like1 = await client.post(f"/contents/{content_id}/like", headers=f_headers)
        assert like1.status_code == 200
        assert like1.json()["like_count"] == 1

        like2 = await client.post(f"/contents/{content_id}/like", headers=f_headers)
        assert like2.status_code == 200
        assert like2.json()["like_count"] == 1  # No duplicate increment

        # 4. Threaded Comments (Parent + Reply + Edit + Cascade Delete)
        parent_c = await client.post(
            f"/contents/{content_id}/comments",
            headers=f_headers,
            json={"body": "Which Mudra represents the lotus?"},
        )
        assert parent_c.status_code == 201
        parent_id = parent_c.json()["comment_id"]

        reply_c = await client.post(
            f"/contents/{content_id}/comments",
            headers=c_headers,
            json={"body": "Padmakosham! We cover it in part 2.", "parent_comment_id": parent_id},
        )
        assert reply_c.status_code == 201
        assert reply_c.json()["parent_comment_id"] == parent_id

        comments_list = await client.get(f"/contents/{content_id}/comments")
        assert comments_list.status_code == 200
        assert len(comments_list.json()) == 2

        edit_c = await client.patch(
            f"/comments/{parent_id}",
            headers=f_headers,
            json={"body": "Which Kathakali mudra represents the blooming lotus?"},
        )
        assert edit_c.status_code == 200
        assert edit_c.json()["is_edited"] is True

        # Deleting parent cascades reply and syncs comment_count to 0
        del_c = await client.delete(f"/comments/{parent_id}", headers=f_headers)
        assert del_c.status_code == 204
        content_after = await client.get(f"/contents/{content_id}")
        assert content_after.json()["comment_count"] == 0

        # 5. Follow Graph (Self-follow blocked, follow & unfollow work)
        self_follow = await client.post(f"/users/{creator_id}/follow", headers=c_headers)
        assert self_follow.status_code == 400

        follow_res = await client.post(f"/users/{creator_id}/follow", headers=f_headers)
        assert follow_res.status_code == 200
        assert follow_res.json()["follower_count"] == 1

        followers_list = await client.get(f"/users/{creator_username}/followers")
        assert any(u["username"] == fan_username for u in followers_list.json())

        # 6. Bookmarks / Collections
        save1 = await client.post(
            f"/contents/{content_id}/save",
            headers=f_headers,
            json={"collection_name": "Performing Arts"},
        )
        assert save1.status_code == 200
        assert save1.json()["save_count"] == 1

        saved_list = await client.get(
            "/users/me/saved",
            headers=f_headers,
            params={"collection_name": "Performing Arts"},
        )
        assert saved_list.status_code == 200
        assert any(item["content_id"] == content_id for item in saved_list.json())

        unsave = await client.delete(f"/contents/{content_id}/save", headers=f_headers)
        assert unsave.status_code == 200
        assert unsave.json()["save_count"] == 0

    # Cleanup
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM users WHERE username IN (:u1, :u2)"),
            {"u1": creator_username, "u2": fan_username},
        )
        await session.commit()