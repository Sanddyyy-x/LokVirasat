from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import engine, get_db
from app.modules.auth.router import router as auth_router
from app.modules.contents.router import router as contents_router
from app.modules.interactions.router import router as interactions_router
from app.modules.media.router import router as media_router
from app.modules.social.router import router as social_router
from app.modules.taxonomy.router import router as taxonomy_router
from app.modules.users.router import router as users_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await engine.dispose()


app = FastAPI(
    title="LokVirasat API",
    version="0.6.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(taxonomy_router)
app.include_router(contents_router)
app.include_router(social_router)
app.include_router(media_router)
app.include_router(interactions_router)


@app.get("/health", tags=["system"])
async def health_check(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    table_count = await db.scalar(
        text("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'")
    )
    return {"status": "ok", "database": "connected", "public_tables": table_count}