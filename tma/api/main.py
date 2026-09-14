"""Music Legends — Telegram Mini App FastAPI backend.
Serves /api/* routes AND the built React frontend at /.
"""
import sys, os
# Make repo root importable so database.py, config/, battle_engine.py all resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from tma.api.routers import users, cards, packs, economy, battle, marketplace, trade, dust, battle_pass, stars, telegram_hosts  # noqa: E402

app = FastAPI(title="Music Legends TMA", version="1.0.0", docs_url="/api/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Telegram WebView origin is unpredictable
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health ────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "service": "tma-api"}


# ── Routers ───────────────────────────────────────────────────────
app.include_router(users.router)
app.include_router(cards.router)
app.include_router(packs.router)
app.include_router(economy.router)
app.include_router(battle.router)
app.include_router(marketplace.router)
app.include_router(trade.router)
app.include_router(dust.router)
app.include_router(battle_pass.router)
app.include_router(stars.router)  # Phase 5: Telegram Stars replaces Stripe inside the Mini App
app.include_router(telegram_hosts.router)

# ── Telegram Bot webhook ───────────────────────────────────────────
from tma.api.bot.handlers import setup_webhook_route  # noqa: E402
setup_webhook_route(app)

# ── Phase 4 background jobs (opt-in) ──────────────────────────────
# ENABLE_MOMENTUM_JOB=true → weekly YouTube view refresh (Mon 03:00 UTC) and a
# daily genre backfill batch (04:00 UTC). Single-replica only; no Redis needed.
@app.on_event("startup")
async def _start_phase4_jobs():
    if os.environ.get("ENABLE_MOMENTUM_JOB", "false").lower() not in ("1", "true", "yes"):
        return
    try:
        import asyncio
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from database import get_db

        def _momentum():
            from services.momentum_service import refresh_momentum
            return refresh_momentum(get_db())

        def _genres():
            from services.genre_resolver import backfill_genres
            return backfill_genres(get_db(), limit=int(os.environ.get("GENRE_BACKFILL_BATCH", "200")))

        sched = AsyncIOScheduler(timezone="UTC")
        loop = asyncio.get_event_loop()
        sched.add_job(lambda: loop.run_in_executor(None, _momentum),
                      CronTrigger(day_of_week="mon", hour=3, minute=0), id="momentum_weekly")
        sched.add_job(lambda: loop.run_in_executor(None, _genres),
                      CronTrigger(hour=4, minute=0), id="genre_backfill_daily")
        sched.start()
        app.state.phase4_scheduler = sched
        print("[JOBS] Phase 4 scheduler started (momentum weekly, genre backfill daily)")
    except Exception as e:  # never block startup on a scheduler problem
        print(f"[JOBS] Phase 4 scheduler not started: {e}")


# ── Serve built React app (only if dist/ exists) ──────────────────
_dist = os.path.join(os.path.dirname(__file__), "../frontend/dist")
if os.path.isdir(_dist):
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
