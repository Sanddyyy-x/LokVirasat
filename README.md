# LokVirasat Backend API

Backend service for **LokVirasat** (Smart India Hackathon 2026 — PS 26214), a cultural heritage social platform combining regional taxonomy, multi-format publishing, and recommendation-ready interaction tracking[cite: 1, 2].

## Tech Stack

- **Framework:** FastAPI (Python 3.11+)
- **Database:** PostgreSQL 15+ (Supabase) via SQLAlchemy (`asyncpg`)[cite: 2]
- **Auth:** Argon2id password hashing, JWT access tokens, rotating HttpOnly refresh cookies[cite: 2]
- **Testing:** `pytest` + `pytest-asyncio` + `httpx`

---

## Implemented Milestones (1–6)

| Milestone | Scope | Key Endpoints |
| :--- | :--- | :--- |
| **1. Schema, Migrations & Seed** | 18 MVP tables[cite: 2] + 2 recommendation-tracking tables (`user_interactions`, `search_queries`)[cite: 1], triggers, GIN/FTS indexes[cite: 2], and seed data[cite: 1] | `GET /health` |
| **2. Auth, RBAC & Profiles** | Registration, login, refresh rotation, profile updates, and stackable `user_roles`[cite: 1, 2] | `POST /auth/register`, `/login`, `/refresh`, `/logout`; `GET/PATCH /users/me`; `GET /users/{username}` |
| **3. Content & Taxonomy** | Unified `contents` (`post`, `reel`, `article`) with subtype tables, regional/category filters, and full-text search[cite: 1, 2] | `GET /regions`, `/categories`; `POST/GET/PATCH/DELETE /contents` |
| **4. Core Social Features** | Idempotent likes, nested threaded comments, directed follow graph, and named bookmark collections[cite: 1, 2] | `POST/DELETE /contents/{id}/like`, `/save`; `POST/GET/PATCH/DELETE` comments; `POST/DELETE /users/{id}/follow`; `GET /users/me/saved` |
| **5. Media Lifecycle** | 4-step pipeline (`presign` → `confirm` → `url` → `cleanup`) with size/MIME checks, 30m TTL, and 5m signed URLs[cite: 1, 2] | `POST /media/presign`, `/{id}/confirm`, `/cleanup`; `GET /media/{id}/url`; `DELETE /media/{id}` |
| **6. Interaction Tracking** | Captures impressions, `dwell_ms`, `watch_ms`, `completion_pct`, skips, social signals, search logs, `feed_source`, and taxonomy context[cite: 1] | `POST /interactions`, `/interactions/batch`, `/interactions/searches`; `GET /interactions/affinity/me`, `/recommendations/baseline` |

---

## Project Structure

```text
LokVirasat/
├── app/
│   ├── core/               # Settings, async DB engine, JWT & RBAC dependencies
│   ├── modules/
│   │   ├── auth/           # Registration, login, refresh rotation, logout
│   │   ├── users/          # /users/me & public profile endpoints
│   │   ├── taxonomy/       # Indian regions & heritage categories
│   │   ├── contents/       # Post, reel, and article CRUD + FTS search
│   │   ├── social/         # Likes, threaded comments, follows, bookmarks
│   │   ├── media/          # Presigned upload, confirmation, signed URLs, cleanup
│   │   └── interactions/   # Event/search telemetry, user affinity, popularity baseline
│   └── main.py             # FastAPI app & router registration
├── db/
│   ├── migrations/         # Versioned SQL migrations (001, 002)
│   ├── seed.sql            # Repeatable roles, taxonomy, demo users & content seed
│   └── run_migrations.py   # Idempotent migration & seed runner
├── tests/                  # Integration tests covering Milestones 1–6
├── .env.example
├── pytest.ini
└── requirements.txt