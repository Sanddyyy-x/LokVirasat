import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app
from app.modules.media.router import run_media_cleanup


@pytest.mark.asyncio
async def test_media_lifecycle_reel_publishing_and_cleanup():
    suffix = uuid.uuid4().hex[:8]
    creator_username = f"media_creator_{suffix}"
    other_username = f"media_other_{suffix}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register creator & another user
        c_res = await client.post(
            "/auth/register",
            json={
                "username": creator_username,
                "email": f"{creator_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Arjun Deshmukh",
                "is_creator": True,
            },
        )
        assert c_res.status_code == 201
        c_headers = {"Authorization": f"Bearer {c_res.json()['access_token']}"}

        o_res = await client.post(
            "/auth/register",
            json={
                "username": other_username,
                "email": f"{other_username}@lokvirasat.dev",
                "password": "StrongPassword123",
                "full_name": "Neha Patil",
                "is_creator": False,
            },
        )
        o_headers = {"Authorization": f"Bearer {o_res.json()['access_token']}"}

        # 2. Reject oversized image (>10 MB) & invalid MIME
        oversized = await client.post(
            "/media/presign",
            headers=c_headers,
            json={
                "media_kind": "image",
                "mime_type": "image/jpeg",
                "file_size_bytes": 12 * 1024 * 1024,
                "filename": "huge.jpg",
            },
        )
        assert oversized.status_code == 400

        bad_mime = await client.post(
            "/media/presign",
            headers=c_headers,
            json={
                "media_kind": "image",
                "mime_type": "application/x-msdownload",
                "file_size_bytes": 1024,
                "filename": "bad.exe",
            },
        )
        assert bad_mime.status_code == 400

        # 3. Presign & confirm a vertical reel video + thumbnail image
        vid_pre = await client.post(
            "/media/presign",
            headers=c_headers,
            json={
                "media_kind": "video",
                "mime_type": "video/mp4",
                "file_size_bytes": 15 * 1024 * 1024,
                "filename": "lavani_performance.mp4",
                "duration_seconds": 45,
            },
        )
        assert vid_pre.status_code == 201
        video_id = vid_pre.json()["media_id"]

        vid_conf = await client.post(
            f"/media/{video_id}/confirm",
            headers=c_headers,
            json={"width_px": 1080, "height_px": 1920, "duration_seconds": 45},
        )
        assert vid_conf.status_code == 200
        assert vid_conf.json()["upload_status"] == "active"

        thumb_pre = await client.post(
            "/media/presign",
            headers=c_headers,
            json={
                "media_kind": "image",
                "mime_type": "image/webp",
                "file_size_bytes": 400_000,
                "filename": "lavani_thumb.webp",
            },
        )
        assert thumb_pre.status_code == 201
        thumb_id = thumb_pre.json()["media_id"]

        thumb_conf = await client.post(f"/media/{thumb_id}/confirm", headers=c_headers)
        assert thumb_conf.status_code == 200

        # 4. Publish a Reel using confirmed video + thumbnail (verifies DB trigger!)
        reel_res = await client.post(
            "/contents",
            headers=c_headers,
            json={
                "content_type": "reel",
                "title": "Traditional Lavani Folk Dance",
                "caption_or_summary": "Live dholki accompaniment in Pune.",
                "tags": ["lavani", "maharashtra"],
                "media_ids": [video_id, thumb_id],
                "reel_details": {
                    "video_media_id": video_id,
                    "thumbnail_media_id": thumb_id,
                    "duration_seconds": 45,
                    "aspect_ratio": "9:16",
                },
            },
        )
        assert reel_res.status_code == 201, reel_res.text
        assert reel_res.json()["reel_details"]["duration_seconds"] == 45

        # 5. Private signed document access control + orphan cleanup
        doc_pre = await client.post(
            "/media/presign",
            headers=c_headers,
            json={
                "media_kind": "document",
                "mime_type": "application/pdf",
                "file_size_bytes": 500_000,
                "filename": "researcher_cv.pdf",
                "access_level": "private_signed",
            },
        )
        doc_id = doc_pre.json()["media_id"]
        await client.post(f"/media/{doc_id}/confirm", headers=c_headers)

        denied = await client.get(f"/media/{doc_id}/url", headers=o_headers)
        assert denied.status_code == 403

        allowed = await client.get(f"/media/{doc_id}/url", headers=c_headers)
        assert allowed.status_code == 200
        assert "expires_in=300" in allowed.json()["url"]

        # Mark orphaned & run cleanup worker
        del_media = await client.delete(f"/media/{doc_id}", headers=c_headers)
        assert del_media.status_code == 204

    async with AsyncSessionLocal() as session:
        cleanup_res = await run_media_cleanup(session)
        assert str(doc_id) in [str(mid) for mid in cleanup_res.deleted_media_ids]

        # Cleanup test users
        await session.execute(
            text("DELETE FROM users WHERE username IN (:u1, :u2)"),
            {"u1": creator_username, "u2": other_username},
        )
        await session.commit()