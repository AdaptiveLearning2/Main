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
from src.app.security import require_admin_token, require_learner_token
from src.app.services.stream_manager import StreamManager, UnknownDeviceError

logger = logging.getLogger(__name__)
settings = get_settings()
stream_manager = StreamManager()

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
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_admin_token)
) -> JSONResponse:
    try:
        await stream_manager.start(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "running"})


@app.post("/api/v1/session/stop")
async def stop_session(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_admin_token)
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
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_admin_token)
) -> JSONResponse:
    try:
        out = stream_manager.send_muse_bridge_command("refresh", device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "ok", "data": out})


@app.post("/api/v1/muse/connect")
async def muse_connect(body: MuseConnectBody, _: str = Depends(require_admin_token)) -> JSONResponse:
    try:
        out = stream_manager.send_muse_bridge_command("connect", body.device_id, name=body.name.strip())
    except UnknownDeviceError:
        raise _unknown_device(body.device_id)
    return JSONResponse({"status": "ok", "data": out})


@app.post("/api/v1/muse/disconnect")
async def muse_disconnect(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_admin_token)
) -> JSONResponse:
    try:
        out = stream_manager.send_muse_bridge_command("disconnect", device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "ok", "data": out})
