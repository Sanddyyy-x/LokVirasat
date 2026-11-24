import asyncio
import os
from pathlib import Path
import asyncpg
from dotenv import load_dotenv

load_dotenv()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SEED_FILE = Path(__file__).parent / "seed.sql"


async def main() -> None:
    dsn = os.environ["DIRECT_DATABASE_URL"]
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            """
        )

        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            already_applied = await conn.fetchval(
                "SELECT 1 FROM schema_migrations WHERE filename = $1",
                sql_file.name,
            )
            if already_applied:
                print(f"[skip] {sql_file.name}")
                continue

            print(f"[apply] {sql_file.name}")
            async with conn.transaction():
                await conn.execute(sql_file.read_text(encoding="utf-8"))
                await conn.execute(
                    "INSERT INTO schema_migrations (filename) VALUES ($1)",
                    sql_file.name,
                )

        print("[seed] Running seed.sql")
        async with conn.transaction():
            await conn.execute(SEED_FILE.read_text(encoding="utf-8"))
        print("Done.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())