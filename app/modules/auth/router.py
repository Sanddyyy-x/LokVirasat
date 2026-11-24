from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    fetch_user_with_roles,
    hash_password,
    verify_password,
)
from app.modules.auth.schemas import (
    AuthResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    UserAuthOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path="/auth",
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    username = payload.username.strip().lower()
    email = payload.email.strip().lower()
    pw_hash = hash_password(payload.password)

    try:
        user_id = await db.scalar(
            text(
                """
                INSERT INTO users (username, email, password_hash, full_name, home_state, preferred_language)
                VALUES (:username, :email, :password_hash, :full_name, :home_state, :preferred_language)
                RETURNING user_id
                """
            ),
            {
                "username": username,
                "email": email,
                "password_hash": pw_hash,
                "full_name": payload.full_name.strip(),
                "home_state": payload.home_state.strip() if payload.home_state else None,
                "preferred_language": payload.preferred_language.strip(),
            },
        )

        role_keys = ["registered_user", "cultural_creator"] if payload.is_creator else ["registered_user"]
        await db.execute(
            text(
                """
                INSERT INTO user_roles (user_id, role_id)
                SELECT :user_id, role_id
                FROM roles
                WHERE role_key = ANY(:role_keys)
                """
            ),
            {"user_id": user_id, "role_keys": role_keys},
        )
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email is already registered",
        )

    user = await fetch_user_with_roles(db, str(user_id))
    access_token = create_access_token(user["user_id"], user["roles"])
    refresh_token = create_refresh_token(user["user_id"])
    _set_refresh_cookie(response, refresh_token)

    return AuthResponse(
        access_token=access_token,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
        user=UserAuthOut(**user),
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    identifier = payload.email_or_username.strip().lower()
    row = (
        await db.execute(
            text(
                """
                SELECT user_id, password_hash, account_status
                FROM users
                WHERE LOWER(email) = :identifier OR LOWER(username) = :identifier
                LIMIT 1
                """
            ),
            {"identifier": identifier},
        )
    ).mappings().first()

    if not row or not verify_password(payload.password, row["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    if row["account_status"] != "active":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is suspended or deactivated",
        )

    user = await fetch_user_with_roles(db, str(row["user_id"]))
    access_token = create_access_token(user["user_id"], user["roles"])
    refresh_token = create_refresh_token(user["user_id"])
    _set_refresh_cookie(response, refresh_token)

    return AuthResponse(
        access_token=access_token,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
        user=UserAuthOut(**user),
    )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_tokens(
    response: Response,
    body: RefreshRequest | None = None,
    refresh_token_cookie: str | None = Cookie(default=None, alias="refresh_token"),
    db: AsyncSession = Depends(get_db),
):
    token = refresh_token_cookie or (body.refresh_token if body else None)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")

    payload = decode_token(token, expected_type="refresh")
    user = await fetch_user_with_roles(db, payload["sub"])
    if not user or user["account_status"] != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user session")

    new_access = create_access_token(user["user_id"], user["roles"])
    new_refresh = create_refresh_token(user["user_id"])
    _set_refresh_cookie(response, new_refresh)

    return AuthResponse(
        access_token=new_access,
        expires_in_seconds=settings.access_token_expire_minutes * 60,
        user=UserAuthOut(**user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    response.delete_cookie(key="refresh_token", path="/auth")
    return Response(status_code=status.HTTP_204_NO_CONTENT)