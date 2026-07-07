from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from src.app.config import get_settings
from src.app.services.adaptation import AdaptationEngine
from src.app.services.eeg_ingestion import build_ingestion_adapter, enrich_ingestion_dict
from src.app.services.signal_processing import SignalProcessor


class StreamManager:
    CONTRACT_VERSION = "1.1.0"

    def __init__(self) -> None:
        self.settings = get_settings()
        self.adapter = build_ingestion_adapter(self.settings)
        self.processor = SignalProcessor()
        self.adaptation = AdaptationEngine()
        self.latest_payload: dict[str, Any] = {}
        self.samples_processed = 0
        self.errors_seen = 0
        self._task: asyncio.Task[None] | None = None
        self.running = False

    async def start(self) -> None:
        if self.running:
            return
        # Adapter connect can block on network I/O (TCP bridge mode), so keep it
        # off the event loop thread to avoid freezing API request handling.
        await asyncio.to_thread(self.adapter.connect)
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Adapter disconnect can block on socket shutdown/thread joins.
        await asyncio.to_thread(self.adapter.disconnect)

    async def _loop(self) -> None:
        period = 1 / max(1, self.settings.eeg_sample_hz)
        while self.running:
            try:
                # Adapter reads can block (especially TCP bridge mode before first EEG frame),
                # so move them off the event loop thread to keep API endpoints responsive.
                sample = await asyncio.to_thread(self.adapter.read_sample)
                # Metadata access can also block (e.g., lock contention in TCP adapter),
                # so keep it off the event loop thread as well.
                raw_meta = (
                    await asyncio.to_thread(self.adapter.get_ingestion_meta)
                    if hasattr(self.adapter, "get_ingestion_meta")
                    else {}
                )
                features = self.processor.update(sample, raw_meta)
                state = self.adaptation.infer_state(features)
                policy = self.adaptation.next_question_policy(state)
                self.latest_payload = {
                    "contract_version": self.CONTRACT_VERSION,
                    "timestamp": sample.timestamp.isoformat(),
                    "channels": {
                        "tp9": sample.channel_tp9,
                        "af7": sample.channel_af7,
                        "af8": sample.channel_af8,
                        "tp10": sample.channel_tp10,
                    },
                    "features": features,
                    "state": {
                        "label": state.label,
                        "reason": state.reason,
                        "confidence": state.confidence,
                        "focus_score": state.focus_score,
                        "calm_score": state.calm_score,
                    },
                    "question_policy": policy,
                }
                self.samples_processed += 1
            except Exception:
                self.errors_seen += 1
                # No EEG data arrived this cycle (headset unplugged, bridge idle, etc.).
                # Zero the reported scores instead of leaving the last stale reading in
                # place, and clear the rolling window so real scores don't resume by
                # blending pre-gap and post-gap samples.
                self.processor.window.clear()
                self.adaptation.last_label = "no_signal"
                self.adaptation.last_change_ts = float("-inf")
                self.latest_payload = self._no_signal_payload()
            await asyncio.sleep(period)

    def _no_signal_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.CONTRACT_VERSION,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "channels": {"tp9": 0.0, "af7": 0.0, "af8": 0.0, "tp10": 0.0},
            "features": {
                "focus_score": 0.0,
                "calm_score": 0.0,
                "confidence": 0.0,
                "signal_quality": "no_signal",
            },
            "state": {
                "label": "no_signal",
                "reason": "No EEG data received",
                "confidence": 0.0,
                "focus_score": 0.0,
                "calm_score": 0.0,
            },
            "question_policy": {
                "action": "fallback_default",
                "difficulty": self.adaptation.current_difficulty,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        out = dict(self.latest_payload)
        out.setdefault("contract_version", self.CONTRACT_VERSION)
        if hasattr(self.adapter, "get_ingestion_meta"):
            raw_meta = self.adapter.get_ingestion_meta()
            ing = enrich_ingestion_dict(self.settings, raw_meta)
            out["ingestion"] = ing
            out["bands"] = {
                "delta": float(raw_meta.get("delta", 0.0)),
                "theta": float(raw_meta.get("theta", 0.0)),
                "alpha": float(raw_meta.get("alpha", 0.0)),
                "beta": float(raw_meta.get("beta", 0.0)),
                "gamma": float(raw_meta.get("gamma", 0.0)),
            }
        return out

    def metrics(self) -> dict[str, int | bool]:
        return {
            "contract_version": self.CONTRACT_VERSION,
            "running": self.running,
            "samples_processed": self.samples_processed,
            "errors_seen": self.errors_seen,
        }

    def send_muse_bridge_command(self, cmd: str, **kwargs: Any) -> dict[str, Any]:
        """Forward JSON control lines to muse_native_bridge when using TCP muse ingestion."""
        send = getattr(self.adapter, "send_bridge_command", None)
        if send is None or not callable(send):
            return {"ok": False, "error": "commands require EEG_SOURCE=muse"}
        body: dict[str, Any] = {"cmd": cmd}
        body.update(kwargs)
        try:
            send(body)
        except (OSError, RuntimeError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def muse_ingestion_snapshot(self) -> dict[str, Any]:
        if hasattr(self.adapter, "get_ingestion_meta"):
            return enrich_ingestion_dict(self.settings, self.adapter.get_ingestion_meta())
        return enrich_ingestion_dict(self.settings, {})
