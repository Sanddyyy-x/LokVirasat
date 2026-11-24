from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)
    home_state: str | None = Field(default=None, max_length=80)
    preferred_language: str = Field(default="en", max_length=10)
    is_creator: bool = False


class LoginRequest(BaseModel):
    email_or_username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


class UserAuthOut(BaseModel):
    user_id: UUID
    username: str
    email: EmailStr
    full_name: str
    home_state: str | None
    preferred_language: str
    is_verified_badge: bool
    roles: list[str]
    created_at: datetime


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user: UserAuthOut