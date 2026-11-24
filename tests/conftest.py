import pytest_asyncio
from app.core.database import engine


@pytest_asyncio.fixture(autouse=True)
async def dispose_engine_between_tests():
    yield
    await engine.dispose()