from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from time import perf_counter

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.app import kit_info
from src.app.config import get_settings
from src.app.schemas import Envelope
from src.app.security import (
    require_admin_token,
    require_learner_token,
    require_local_controller,
)
from src.app.services.push_client import PushClient
from src.app.services.stream_manager import DeviceReleasing, StreamManager, UnknownDeviceError

logger = logging.getLogger(__name__)
settings = get_settings()
stream_manager = StreamManager()


async def _stop_refused_sensors(sensors: set[str], still_current: Callable[[], bool]) -> None:
    """Stop each running device of a sensor the backend refuses: the lesson page that would may be gone.

    Checked before each device: a refusal from a session that has since ended stops nothing more."""
    for device in stream_manager.list_devices():
        if not still_current():
            return
        sensor = "camera" if device["kind"] == "face" else "headband"
        if device["running"] and sensor in sensors:
            logger.warning("push: stopping %s; recording from the %s is not permitted",
                           device["device_id"], sensor)
            try:
                await stream_manager.stop(device["device_id"])
            except DeviceReleasing as exc:
                # Its stream has stopped; the next check tries again, and the other devices are not held up.
                logger.warning("push: %s", exc)


def _make_push_client() -> PushClient | None:
    """None when push is off, so there is no instance to start by accident and duplicate writers."""
    if not settings.push_enabled:
        return None
    client = PushClient(settings.backend_url)
    client.set_refusal_handler(_stop_refused_sensors)
    return client


push_client = _make_push_client()


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    yield
    # Flush the queue and drop the token on shutdown, within the push client's SHUTDOWN_BUDGET.
    if push_client is not None:
        stream_manager.set_payload_consumer(None)
        await push_client.stop()
        await push_client.cancel_refusals()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.sidecar_docs else None,
    redoc_url="/redoc" if settings.sidecar_docs else None,
    openapi_url="/openapi.json" if settings.sidecar_docs else None,
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.allowed_origins.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(
    # Loopback names only: "*.local" let a page on the LAN rebind its own mDNS name to 127.0.0.1.
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
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
def healthz() -> dict[str, object]:
    return {"status": "ok", "kit": kit_info.for_healthz()}


def _unknown_device(device_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Unknown device_id: {device_id!r}")


def _releasing(exc: DeviceReleasing) -> HTTPException:
    """A 503: a start did not connect; a stop's stream has stopped but the device is still being let go."""
    return HTTPException(status_code=503, detail=str(exc))


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
    except DeviceReleasing as exc:
        raise _releasing(exc)
    if push_client is not None:
        # A sensor started after a parent turned it back on must not be judged by the answer before that.
        push_client.recheck()
    return JSONResponse({"status": "running"})


@app.post("/api/v1/session/arm")
async def arm_session(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_local_controller)
) -> JSONResponse:
    """Recording starts now: gather the per-session baseline from here, not during pairing.

    Idempotent, and safe on a device that is not streaming.
    """
    try:
        stream_manager.arm_baseline(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    return JSONResponse({"status": "armed"})


@app.post("/api/v1/session/stop")
async def stop_session(
    device_id: str = StreamManager.DEFAULT_DEVICE_ID, _: str = Depends(require_local_controller)
) -> JSONResponse:
    try:
        await stream_manager.stop(device_id)
    except UnknownDeviceError:
        raise _unknown_device(device_id)
    except DeviceReleasing as exc:
        raise _releasing(exc)
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
    # The student's bearer token: memory only, never logged; repr=False keeps it out of logs.
    access_token: str = Field(..., min_length=1, repr=False)


@app.post("/api/v1/push/start")
async def push_start(body: PushStartBody, _: str = Depends(require_learner_token)) -> JSONResponse:
    """Begin posting this session's samples to the website backend.

    409 when push is off: the backend's poller already pulls, and both would write every sample twice.
    """
    if push_client is None:
        raise HTTPException(
            status_code=409,
            detail=("This sidecar runs with PUSH_ENABLED=false: the website backend "
                    "polls it instead, and pushing as well would write every sample "
                    "twice. Nothing is wrong with the sensors."),
        )
    # A new session id arms the baseline (push's /session/arm); a repeat is a token refresh.
    # start() decides "new" under its lock, so two concurrent starts cannot both re-arm.
    new_session = await push_client.start(body.session_id, body.access_token)
    stream_manager.set_payload_consumer(push_client.submit_payload)
    if new_session:
        # Best effort: no default device must not turn a working push into a 500.
        try:
            stream_manager.arm_baseline()
        except UnknownDeviceError:
            logger.warning("push/start: no default device to arm the baseline on; "
                           "scores stay relative to stream start")
    return JSONResponse({"status": "pushing", "session_id": body.session_id})


class PushStopBody(BaseModel):
    # The lesson the page is leaving: a second tab's lesson that took delivery over must not be stopped.
    session_id: str | None = Field(default=None, min_length=1)


@app.post("/api/v1/push/stop")
async def push_stop(body: PushStopBody | None = None, _: str = Depends(require_learner_token)) -> JSONResponse:
    """Stop pushing, flush the tail, and forget the token; with `session_id`, only if that session is pushing.

    503 when another start or stop held the push client past its budget: nothing was stopped or ended."""
    if push_client is None:
        return JSONResponse({"status": "not_configured"})
    only = body.session_id if body else None
    not_current = JSONResponse({"status": "not_current", "ended_session": False})
    if only is not None and push_client.session_id != only:
        return not_current
    stream_manager.set_payload_consumer(None)
    # End the session only if one was pushing: pagehide fires this under pull too, and
    # ending there would wipe a live armed baseline that nothing re-arms.
    was_pushing = push_client.session_id is not None
    stopped = await push_client.stop(only_session=only)
    if stopped is None:
        # Another lesson took delivery over while this waited for the lock: it keeps its consumer.
        stream_manager.set_payload_consumer(push_client.submit_payload)
        return not_current
    if not stopped:
        return JSONResponse({"status": "not_stopped", "ended_session": False}, status_code=503,
                            headers={"Retry-After": "1"})
    if was_pushing:
        # Push's only session end (the stream stays up); best effort.
        try:
            stream_manager.end_session()
        except UnknownDeviceError:
            logger.warning("push/stop: no default device to end the session on")
    return JSONResponse({"status": "stopped", "ended_session": was_pushing})


@app.get("/api/v1/push/status")
async def push_status(_: str = Depends(require_learner_token)) -> JSONResponse:
    """Queue depths, delivery counts and the last error.

    `enabled: false` when push is off. `recorded` counts what the backend stored, not what
    was sent: it drops samples for an unconsented sensor.
    """
    if push_client is None:
        return JSONResponse({"status": "ok", "data": {"enabled": False}})
    return JSONResponse({"status": "ok", "data": {"enabled": True, **push_client.status()}})


class MuseConnectBody(BaseModel):
    name: str = Field(..., min_length=1)
    device_id: str = StreamManager.DEFAULT_DEVICE_ID


class AnswerBody(BaseModel):
    correct: bool
    difficulty: str | None = None
    device_id: str = StreamManager.DEFAULT_DEVICE_ID


@app.post("/api/v1/session/answer")
async def session_answer(body: AnswerBody, _: str = Depends(require_local_controller)) -> JSONResponse:
    """The backend recorded an answer for the student on this device.

    Moves the simulator's hidden state; a real headband ignores it (`applied: false`).
    """
    try:
        out = stream_manager.report_answer(body.device_id, correct=body.correct, difficulty=body.difficulty)
    except UnknownDeviceError:
        raise _unknown_device(body.device_id)
    return JSONResponse({"status": "ok", "data": out})


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
