"""A `face` device produces a payload, tick after tick.

This file exists because a review found the chain was never joined. Every piece
of the camera pipeline had tests and worked in isolation; `DeviceSession` had no
face branch, so the loop reached for `sample.channel_tp9` on a `FaceSample`,
raised on every tick, and the generic handler swallowed it into `errors_seen`
and a warning. The session looked alive and produced nothing — and the launcher
flag added in the same change shipped exactly that configuration.

The lesson is the shape of the test, not the fix: unit tests over every stage
cannot see a missing seam between them.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from src.app.config import Settings, parse_eeg_devices
from src.app.services.face_emotion import EmotionResult
from src.app.services.stream_manager import DeviceSession

FPS = 30.0


def _settings(**kw) -> Settings:
    base = {"API_TOKEN": "x", "ADMIN_TOKEN": "y", "EEG_SOURCE": "sim",
            "EEG_DEVICES": "camera:face@0", "FACE_FPS": str(FPS)}
    base.update(kw)
    return Settings(**base)


class FakeFaceAdapter:
    """Duck-types FaceCaptureAdapter, with no camera behind it."""

    def __init__(self, *, bpm=72.0, seconds=25.0, measured=FPS,
                 emotion=None, heart_enabled=True, emotion_enabled=True):
        self.fps = FPS
        self.heart_enabled = heart_enabled
        self.emotion_enabled = emotion_enabled
        self._emotion = emotion
        self._measured = measured
        # Against the rate the adapter *reports*, so the fake is self-consistent:
        # one claiming 22 fps must carry colour actually sampled at 22 fps.
        t = np.arange(int(seconds * measured)) / measured
        base = np.array([180.0, 120.0, 110.0])
        wave = 0.005 * np.sin(2 * np.pi * bpm / 60.0 * t)
        self._rgb = base * (1.0 + np.outer(wave, np.array([0.10, 0.60, 0.30])))
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def drain_samples(self, max_batch):
        from src.app.services.face_ingestion import FaceSample

        return [FaceSample(float(i), (180.0, 120.0, 110.0), 0.9) for i in range(5)]

    def rgb_window(self, seconds):
        stamps = np.arange(len(self._rgb)) / self._measured
        return self._rgb, self._measured, 0.9, stamps

    def latest_emotion(self):
        return self._emotion

    def get_ingestion_meta(self):
        return {"camera_connected": self.connected, "emotion_degraded": False}


def _session(adapter, **kw) -> DeviceSession:
    settings = _settings(**kw)
    device = parse_eeg_devices(settings)["camera"]
    session = DeviceSession("camera", settings, device)
    session.adapter = adapter
    return session


def _tick(session: DeviceSession) -> dict:
    """Run the loop until it produces a payload, and snapshot it.

    Snapshotted *before* stop(), because stop() deliberately replaces
    latest_payload with the no-signal shape -- a stopped session must not keep
    reporting its last live reading. Reading it afterwards measures the teardown,
    not the tick.
    """
    captured: dict = {}

    async def run():
        await session.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if session.latest_payload.get("heart") or session.latest_payload.get("face"):
                captured.update(session.latest_payload)
                break
        await session.stop()

    asyncio.run(run())
    return captured


def test_a_face_device_produces_a_payload_rather_than_erroring():
    """The regression this file exists for. Before the face branch, this raised
    AttributeError every tick and the error was swallowed into a warning."""
    session = _session(FakeFaceAdapter(emotion=EmotionResult("happy", 0.9, True)))
    payload = _tick(session)

    assert payload, "no payload was produced"
    assert session.errors_seen == 0, "the loop is still erroring"
    assert payload["device_id"] == "camera"
    assert "heart" in payload and "face" in payload


def test_the_heart_block_carries_a_rate_from_the_camera():
    session = _session(FakeFaceAdapter(bpm=72.0))
    payload = _tick(session)

    assert payload["heart"]["source"] == "rppg"
    assert payload["heart"]["bpm"] == pytest.approx(72.0, abs=3.0)


def test_a_camera_running_slow_still_reports_the_right_rate():
    """A camera configured for 30 and delivering 22 must report 72, not 98.

    This test used to assert the opposite -- that the window was *rejected* --
    because the time base came from sample index, so 30/22 scaled the bpm by
    +36%. Refusing to answer was the safe response to an arithmetic error.

    With the samples placed by their own timestamps the error is gone, and
    refusing would now be discarding a good measurement. The camera's actual
    rate is not a fault to be gated on; it is an input.
    """
    session = _session(FakeFaceAdapter(bpm=72.0, measured=22.0))
    payload = _tick(session)

    assert payload["heart"]["bpm"] == pytest.approx(72.0, abs=3.0)
    assert payload["heart"]["measured_fps"] == 22.0


def test_a_frame_rate_below_nyquist_is_refused():
    """Resampling cannot manufacture a signal that was never sampled."""
    session = _session(FakeFaceAdapter(bpm=72.0, measured=6.0))
    payload = _tick(session)

    assert payload["heart"]["bpm"] is None
    assert payload["heart"]["rejected_by"] == "frame_rate_too_low"


def test_the_emotion_block_reaches_the_payload():
    """FER+ was unreachable in production before this: build_ingestion_adapter
    passed neither emotion_enabled nor a model path, so the classifier was never
    constructed -- while both launchers downloaded and verified 35 MB for it."""
    session = _session(FakeFaceAdapter(emotion=EmotionResult("sad", 0.81, True)))
    payload = _tick(session)

    assert payload["face"]["emotion"] == "sad"
    assert payload["face"]["trusted"]


def test_a_disabled_channel_is_absent_from_the_payload():
    session = _session(FakeFaceAdapter(emotion_enabled=False))
    payload = _tick(session)

    assert "heart" in payload
    assert "face" not in payload


def test_the_payload_omits_eeg_fields_rather_than_faking_them():
    """A camera has no electrode channels and no cognitive state. Empty ones
    would let a caller average them into an EEG session's numbers."""
    payload = _tick(_session(FakeFaceAdapter()))

    for absent in ("channels", "features", "state", "question_policy"):
        assert absent not in payload, f"{absent} was fabricated for a camera"
    for present in ("contract_version", "device_id", "timestamp", "ingestion"):
        assert present in payload, f"{present} missing from the camera envelope"


def test_the_factory_passes_the_emotion_settings_through():
    """The wiring gap itself: build_ingestion_adapter dropped emotion_enabled,
    so the setting existed and changed nothing."""
    from src.app.services.eeg_ingestion import build_ingestion_adapter

    adapter = build_ingestion_adapter(
        _settings(FACE_EMOTION_ENABLED="true", FACE_HEART_ENABLED="true"),
        kind="face", camera_index=0,
    )
    assert adapter.emotion_enabled is True
    assert adapter.heart_enabled is True

    heart_only = build_ingestion_adapter(
        _settings(FACE_EMOTION_ENABLED="false", FACE_HEART_ENABLED="true"),
        kind="face", camera_index=0,
    )
    assert heart_only.emotion_enabled is False


# ── the API boundary ─────────────────────────────────────────────────────────

def test_a_face_device_serves_state_over_the_api_without_a_500():
    """The seam a review found after the last one, one layer up.

    `_face_payload` deliberately omits channels/features/state/question_policy,
    and a test asserted exactly that -- but `InterpretedEegData` still declared
    all four as required, so `Envelope(data=snapshot)` raised a ValidationError
    and every GET /api/v1/state for a camera returned 500.

    Every unit test passed because they all asserted on `latest_payload`
    directly and never went through the envelope. Same lesson twice: the seam
    with no test is the one that breaks.
    """
    from fastapi.testclient import TestClient

    from src.app.config import get_settings
    from src.app.main import app, stream_manager

    settings = get_settings()
    device = parse_eeg_devices(_settings())["camera"]
    session = DeviceSession("camera", settings, device)
    session.adapter = FakeFaceAdapter(emotion=EmotionResult("happy", 0.9, True))
    session.latest_payload = _tick(session)

    stream_manager._sessions["camera"] = session
    try:
        client = TestClient(app)
        response = client.get(
            "/api/v1/state?device_id=camera",
            headers={"Authorization": f"Bearer {settings.api_token}"},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["kind"] == "camera"
        assert data["heart"]["source"] == "rppg"
        assert data["face"]["emotion"] == "happy"
        for absent in ("channels", "features", "state", "question_policy"):
            assert absent not in data
    finally:
        stream_manager._sessions.pop("camera", None)


def test_an_eeg_payload_still_serves_as_an_eeg_payload():
    """The union must not resolve an EEG record to the camera model.

    It did at first: CameraData had fewer required fields and pydantic ignores
    extras, so an EEG payload validated as a camera and `channels`, `features`
    and `state` were silently dropped from the response. The required `kind`
    discriminator is what prevents it -- a default value would have made it
    useless, because an EEG payload carrying no kind would simply take it.
    """
    from src.app.schemas import CameraData, Envelope, InterpretedEegData

    camera = _tick(_session(FakeFaceAdapter()))
    assert isinstance(Envelope(status="ok", data=camera, message="").data, CameraData)

    eeg_settings = Settings(API_TOKEN="x", ADMIN_TOKEN="y", EEG_DEVICES="band:sim")
    eeg_session = DeviceSession("band", eeg_settings,
                                parse_eeg_devices(eeg_settings)["band"])

    captured: dict = {}

    async def run():
        await eeg_session.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if eeg_session.latest_payload.get("channels"):
                captured.update(eeg_session.latest_payload)
                break
        await eeg_session.stop()

    asyncio.run(run())
    assert captured, "the sim device produced no payload"

    envelope = Envelope(status="ok", data=captured, message="")
    assert isinstance(envelope.data, InterpretedEegData)
    dumped = envelope.model_dump()["data"]
    assert dumped["channels"] is not None
    assert dumped["state"] is not None
