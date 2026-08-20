from __future__ import annotations

import logging
from time import perf_counter

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.app.config import get_settings
from src.app.schemas import Envelope
from src.app.security import (
    require_admin_token,
    require_learner_token,
    require_local_controller,
)
from src.app.services.push_client import PushClient
from src.app.services.stream_manager import StreamManager, UnknownDeviceError

logger = logging.getLogger(__name__)
settings = get_settings()
stream_manager = StreamManager()
# None when push ingestion is off (the default, co-located dev stack), rather than a
# disabled instance, so there's no object to accidentally start and duplicate writers.
push_client = PushClient(settings.backend_url) if settings.push_enabled else None

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "*.local", "testserver"]
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def request_timing(request: Request, call_next):
    start = perf_counter()
    response = await call_next(request)
    response.headers["X-Process-Time"] = f"{(perf_counter() - start):.4f}"
    return response


@app.on_event("shutdown")
async def _stop_pushing() -> None:
    """Flush the queue and drop the token when the process goes down.

    Without this a Ctrl-C loses whatever was queued since the last tick.
    `stop()` bounds its own flush by the request timeout, so a backend that's
    already gone only delays exit by seconds, not indefinitely.
    """
    if push_client is not None:
        stream_manager.set_payload_consumer(None)
        await push_client.stop()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


def _unknown_device(device_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Unknown device_id: {device_id!r}")


@app.get("/api/v1/devices")
async def list_devices(_: str = Depends(require_learner_token)) -> JSONResponse:
    return JSONResponse({"status": "ok", "data": stream_manager.list_devices()})


@app.post("/api/v1/session/start")
async def start_session(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_local_controller)
) -> JSONResponse:
    try:
        await stream_manager.start(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "running"})


@app.post("/api/v1/session/stop")
async def stop_session(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_local_controller)
) -> JSONResponse:
    try:
        await stream_manager.stop(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "stopped"})


@app.get("/api/v1/state")
async def get_state(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_learner_token)
) -> JSONResponse:
    try:
        snapshot = stream_manager.snapshot(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    if not snapshot or "timestamp" not in snapshot:
        return JSONResponse(Envelope(status="idle", data=None, message="No stream data yet").model_dump())
    return JSONResponse(
        Envelope(status="ok", data=snapshot, message="Latest interpreted EEG state").model_dump()
    )


class PushStartBody(BaseModel):
    session_id: str = Field(..., min_length=1)
    # The student's own backend bearer token, handed over by the browser. Held in
    # memory only, never logged or persisted -- this runs on a student's laptop.
    # Excluded from repr so it can't leak into a log line via an f-string.
    access_token: str = Field(..., min_length=1, repr=False)


@app.post("/api/v1/push/start")
async def push_start(body: PushStartBody, _: str = Depends(require_learner_token)) -> JSONResponse:
    """Begin posting this session's samples to the website backend.

    Refuses when push is off, rather than duplicating a poller's writes: under pull
    ingestion the backend already polls this sidecar, and both running at once
    would insert every EEG sample twice with no error and no dedupe key to catch it.
    """
    if push_client is None:
        raise HTTPException(
            status_code=409,
            detail=("This sidecar runs with PUSH_ENABLED=false: the website backend "
                    "polls it instead, and pushing as well would write every sample "
                    "twice. Nothing is wrong with the sensors."),
        )
    await push_client.start(body.session_id, body.access_token)
    stream_manager.set_payload_consumer(push_client.submit_payload)
    return JSONResponse({"status": "pushing", "session_id": body.session_id})


@app.post("/api/v1/push/stop")
async def push_stop(_: str = Depends(require_learner_token)) -> JSONResponse:
    """Stop pushing, flush the tail, and forget the token."""
    if push_client is None:
        return JSONResponse({"status": "not_configured"})
    stream_manager.set_payload_consumer(None)
    await push_client.stop()
    return JSONResponse({"status": "stopped"})


@app.get("/api/v1/push/status")
async def push_status(_: str = Depends(require_learner_token)) -> JSONResponse:
    """Queue depths, delivery counts and the last error.

    `enabled: false` rather than a 404 when push is off. `recorded` counts what
    the backend said it stored, not what was sent -- the backend drops samples for
    an unconsented sensor, so counting sent would show a healthy session that
    recorded nothing.
    """
    if push_client is None:
        return JSONResponse({"status": "ok", "data": {"enabled": False}})
    return JSONResponse({"status": "ok", "data": {"enabled": True, **push_client.status()}})


class MuseConnectBody(BaseModel):
    name: str = Field(..., min_length=1)
    device_id: str = StreamManager.DEFAULT_DEVICE_ID


@app.get("/api/v1/muse/status")
async def muse_status(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_learner_token)
) -> JSONResponse:
    try:
        snapshot = stream_manager.snapshot(device_id)
        running = stream_manager.is_running(device_id)
        ingestion = stream_manager.muse_ingestion_snapshot(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    channels = snapshot.get("channels") if isinstance(snapshot, dict) else None
    bands = snapshot.get("bands") if isinstance(snapshot, dict) else None
    return JSONResponse(
        {
            "status": "ok",
            "data": {
                "running": running,
                "ingestion": ingestion,
                "brain_signals": channels if isinstance(channels, dict) else None,
                "brain_bands": bands if isinstance(bands, dict) else None,
            },
        }
    )


@app.post("/api/v1/muse/refresh")
async def muse_refresh(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_local_controller)
) -> JSONResponse:
    try:
        out = stream_manager.send_muse_bridge_command("refresh", device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "ok", "data": out})


@app.post("/api/v1/muse/connect")
async def muse_connect(body: MuseConnectBody, _: str = Depends(require_local_controller)) -> JSONResponse:
    try:
        out = stream_manager.send_muse_bridge_command("connect", body.device_id, name=body.name.strip())
    except UnknownDeviceError:
        raise _unknown_device(body.device_id)
    return JSONResponse({"status": "ok", "data": out})


@app.post("/api/v1/muse/disconnect")
async def muse_disconnect(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_local_controller)
) -> JSONResponse:
    try:
        out = stream_manager.send_muse_bridge_command("disconnect", device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "ok", "data": out})
