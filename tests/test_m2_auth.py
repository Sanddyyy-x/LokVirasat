import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app


@pytest.mark.asyncio
async def test_register_login_refresh_and_profile_flow():
    suffix = uuid.uuid4().hex[:8]
    username = f"user_{suffix}"
    email = f"{username}@lokvirasat.dev"
    password = "StrongPassword123"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register creator account
        reg = await client.post(
            "/auth/register",
            json={
                "username": username,
                "email": email,
                "password": password,
                "full_name": "Kavita Sharma",
                "home_state": "Rajasthan",
                "preferred_language": "hi",
                "is_creator": True,
            },
        )
        assert reg.status_code == 201, reg.text
        reg_data = reg.json()
        assert reg_data["user"]["roles"] == ["registered_user", "cultural_creator"]
        assert "refresh_token" in reg.cookies

        # 2. Duplicate registration returns 409
        dup = await client.post(
            "/auth/register",
            json={
                "username": username,
                "email": email,
                "password": password,
                "full_name": "Kavita Sharma",
            },
        )
        assert dup.status_code == 409

        # 3. Login & verify token
        login = await client.post(
            "/auth/login",
            json={"email_or_username": email, "password": password},
        )
        assert login.status_code == 200
        access_token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # 4. Get & update /users/me
        me = await client.get("/users/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == username

        patch = await client.patch(
            "/users/me",
            headers=headers,
            json={"bio": "Documenting Rajasthani folk songs"},
        )
        assert patch.status_code == 200
        assert patch.json()["bio"] == "Documenting Rajasthani folk songs"

        # 5. Public profile lookup
        pub = await client.get(f"/users/{username}")
        assert pub.status_code == 200
        assert pub.json()["full_name"] == "Kavita Sharma"

        # 6. Refresh token rotation
        ref = await client.post("/auth/refresh")
        assert ref.status_code == 200
        assert ref.json()["access_token"]

    # Clean up test user
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM users WHERE username = :u"), {"u": username})
        await session.commit()