
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse

from backend.lifecycle import create_task, task_registry
from backend.AI import gemini as gemini_helper
from backend.ai_client import suggest_defense_for_event, summarize_replay
from backend.auth import require_auth
from backend.db import db
from backend.defense import defense
from backend.replay import replays
from backend.simulation import sim
from backend.telemetry import telemetry
from backend.telemetry_helper import TelemetryMiddleware, get_events
from backend.topology import NODES
from backend.ws_manager import manager
from .logging_config import configure_logging
from .env import validate_env


# ─────────────────────────────────────────────────────────────────────────────
# Optional dependencies
# ─────────────────────────────────────────────────────────────────────────────

try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST as _CT_LATEST,
        generate_latest as _gen_latest,
    )
except ModuleNotFoundError:  # pragma: no cover
    _CT_LATEST = "text/plain; charset=utf-8"

    def _gen_latest() -> bytes:  # type: ignore[misc]
        return b""


try:
    from opentelemetry.instrumentation.fastapi import (
        FastAPIInstrumentor as _OTELInstrumentor,
    )
except ModuleNotFoundError:  # pragma: no cover

    class _OTELInstrumentor:  # type: ignore[no-redef]
        @staticmethod
        def instrument_app(app) -> None:
            return None


logger = logging.getLogger("guardio")

_WS_HEARTBEAT_INTERVAL = 30.0


# ─────────────────────────────────────────────────────────────────────────────
# Application lifespan
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Guardio backend…")

    if hasattr(db, "initialize_async"):
        try:
            await db.initialize_async()
        except Exception:
            logger.exception("DB init failed")

    yield

    logger.info("Shutting down Guardio backend…")

    try:
        await task_registry.flush_all_tasks_async()
    except Exception:
        logger.exception("Error flushing background tasks")

    if hasattr(db, "close_async"):
        try:
            await db.close_async()
        except Exception:
            logger.exception("Error closing DB")


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI application
# ─────────────────────────────────────────────────────────────────────────────

configure_logging()
validate_env()

app = FastAPI(
    title="Guardio Backend",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Middleware
# ─────────────────────────────────────────────────────────────────────────────

try:
    from backend.ratelimit import RateLimitMiddleware

    _rate = int(os.environ.get("GUARDIO_RATE_LIMIT_PER_MIN", "120"))
    app.add_middleware(
        RateLimitMiddleware,
        limit_per_min=_rate,
    )
except Exception:
    pass

app.add_middleware(TelemetryMiddleware)


@app.middleware("http")
async def _request_id(request, call_next):
    rid = request.headers.get("X-Request-Id") or str(uuid.uuid4())

    request.state.request_id = rid

    response = await call_next(request)

    response.headers["X-Request-Id"] = rid

    return response


# ─────────────────────────────────────────────────────────────────────────────
# Global exception handler
# ─────────────────────────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def _unhandled(request, exc: Exception):
    rid = getattr(request.state, "request_id", None)

    logging.getLogger("backend").exception(
        "Unhandled exception",
        exc_info=exc,
        extra={"request_id": rid},
    )

    return JSONResponse(
        {
            "error": "internal_server_error",
            "request_id": rid,
        },
        status_code=500,
    )


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name": "Guardio Backend",
        "status": "online",
        "docs": "/docs",
        "health": "/health",
        "live": "/live",
        "ready": "/ready",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/live")
async def liveness():
    return JSONResponse(
        {
            "status": "ok",
            "ts": _ts(),
        }
    )


@app.get("/ready")
async def readiness():
    return JSONResponse(
        {
            "status": "ready",
            "ts": _ts(),
            "database": await db.readiness_snapshot_async(),
        }
    )


@app.get("/health")
async def health():
    return await liveness()


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/start")
async def start_sim(x_api_key: str = Depends(require_auth)):
    await sim.start()

    return {
        "started": True,
    }


@app.post("/stop")
async def stop_sim(x_api_key: str = Depends(require_auth)):
    rid = await sim.stop()

    return {
        "stopped": True,
        "replay_id": rid,
    }


@app.post("/attack")
async def launch_attack(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    name = payload.get("name")

    if not name:
        return JSONResponse(
            {
                "error": "missing attack name",
            },
            status_code=400,
        )

    create_task(sim.launch_attack(name))

    return {
        "launched": name,
    }


@app.post("/simulation/start")
async def simulation_start(x_api_key: str = Depends(require_auth)):
    await sim.start()

    return {
        "status": "simulation started",
    }


@app.post("/simulation/stop")
async def simulation_stop(x_api_key: str = Depends(require_auth)):
    rid = await sim.stop()

    return {
        "status": "simulation stopped",
        "replay_id": rid,
    }


@app.post("/simulation/attack")
async def simulation_attack(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    name = payload.get("name")

    if not name:
        return JSONResponse(
            {
                "error": "missing attack name",
            },
            status_code=400,
        )

    sim.active_attack = name

    create_task(sim.launch_attack(name))

    return {
        "status": f"attack {name} started",
    }


@app.get("/simulation/status")
async def simulation_status():
    return sim.snapshot()


# ─────────────────────────────────────────────────────────────────────────────
# Topology
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/topology")
async def get_topology():
    """Return full network topology with current node states."""

    return {
        "nodes": {
            nid: n.to_dict()
            for nid, n in NODES.items()
        },
        "ts": _ts(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Defense
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/defense/firewall/block")
async def block_host(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    host = payload.get("host")

    if not host:
        return JSONResponse(
            {
                "error": "missing host",
            },
            status_code=400,
        )

    await defense.block_host(host)

    return {
        "blocked": host,
    }


@app.post("/defense/firewall/unblock")
async def unblock_host(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    host = payload.get("host")

    if not host:
        return JSONResponse(
            {
                "error": "missing host",
            },
            status_code=400,
        )

    await defense.unblock_host(host)

    return {
        "unblocked": host,
    }


@app.post("/defense/segment")
async def create_segment(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    name = payload.get("name")
    hosts = payload.get("hosts") or []

    if not name:
        return JSONResponse(
            {
                "error": "missing segment name",
            },
            status_code=400,
        )

    await defense.create_segment(
        name,
        set(hosts),
    )

    return {
        "segment": name,
        "hosts": hosts,
    }


@app.delete("/defense/segment/{name}")
async def delete_segment(
    name: str,
    x_api_key: str = Depends(require_auth),
):
    await defense.remove_segment(name)

    return {
        "deleted": name,
    }


@app.post("/defense/honeypot")
async def add_honeypot(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    host = payload.get("host")

    if not host:
        return JSONResponse(
            {
                "error": "missing host",
            },
            status_code=400,
        )

    await defense.add_honeypot(host)

    return {
        "honeypot": host,
    }


@app.delete("/defense/honeypot")
async def remove_honeypot(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    host = payload.get("host")

    if not host:
        return JSONResponse(
            {
                "error": "missing host",
            },
            status_code=400,
        )

    await defense.remove_honeypot(host)

    return {
        "removed": host,
    }


@app.get("/defense/status")
async def defense_status():
    return await defense.get_snapshot()


# ─────────────────────────────────────────────────────────────────────────────
# Status / Metrics
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/status")
async def status_snapshot():
    return {
        "simulation": sim.snapshot(),
        "defense": await defense.get_snapshot(),
        "clients": await manager.count(),
    }


@app.get("/metrics")
async def metrics():
    snap = telemetry.snapshot()

    snap["websocket_clients"] = await manager.count()
    snap["simulation"] = sim.snapshot()

    return snap


@app.get("/metrics/prometheus")
def prometheus_metrics():
    return Response(
        content=_gen_latest(),
        media_type=_CT_LATEST,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Replays
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/replays")
async def list_replays():
    try:
        items = await replays.list_async()
    except Exception:
        items = replays.list()

    return {
        "replays": items,
    }


@app.get("/replay/{rid}")
async def get_replay(rid: str):
    try:
        events = await replays.get_async(rid)
    except Exception:
        events = replays.get(rid)

    if events is None:
        return JSONResponse(
            {
                "error": "not found",
            },
            status_code=404,
        )

    return {
        "id": rid,
        "events": events,
    }


# ─────────────────────────────────────────────────────────────────────────────
# AI
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/ai/summarize/{rid}")
async def ai_summarize(
    rid: str,
    x_api_key: str = Depends(require_auth),
):
    try:
        summary = await summarize_replay(rid)

        return {
            "id": rid,
            "summary": summary,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )


@app.post("/ai/suggest")
async def ai_suggest(
    payload: dict,
    x_api_key: str = Depends(require_auth),
):
    """Return a defense suggestion for the given event (non-blocking)."""

    try:
        loop = asyncio.get_running_loop()

        suggestion = await loop.run_in_executor(
            None,
            suggest_defense_for_event,
            payload,
        )

        return {
            "suggestion": suggestion,
        }

    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
        )


@app.post("/AI/generate")
async def ai_generate(payload: dict):
    prompt = payload.get("prompt", "")

    try:
        loop = asyncio.get_running_loop()

        text = await loop.run_in_executor(
            None,
            lambda: gemini_helper.generate_text(prompt),
        )

        return {
            "text": text,
        }

    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Gemini service is currently unavailable",
        )


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Public WebSocket feed — no API key required so dashboards can connect
    without exposing credentials. Emit ``ping`` to receive a ``pong``.
    """

    await manager.connect(websocket)

    try:
        while True:
            try:
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=_WS_HEARTBEAT_INTERVAL,
                )

            except asyncio.TimeoutError:
                await websocket.send_json(
                    {
                        "type": "ping",
                        "ts": _ts(),
                    }
                )

                continue

            if data == "ping":
                await websocket.send_json(
                    {
                        "type": "pong",
                        "ts": _ts(),
                    }
                )

    except WebSocketDisconnect:
        pass

    except Exception:
        pass

    finally:
        await manager.disconnect(websocket)


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/telemetry/events")
def telemetry_events():
    return get_events()


# ─────────────────────────────────────────────────────────────────────────────
# OpenTelemetry
# ─────────────────────────────────────────────────────────────────────────────

_OTELInstrumentor.instrument_app(app)
