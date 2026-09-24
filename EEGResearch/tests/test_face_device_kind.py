"""The `face` device kind: parsing, addressing, and the sidecar importing without cv2."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from src.app.config import DeviceConfig, Settings, parse_eeg_devices


def _settings(**kw) -> Settings:
    base = {"API_TOKEN": "x", "ADMIN_TOKEN": "y", "EEG_SOURCE": "sim"}
    base.update(kw)
    return Settings(**base)


# -- addressing --

def test_a_face_device_is_addressed_by_camera_index_not_by_port():
    """Reusing the port field would collide with a muse endpoint in the uniqueness check."""
    devices = parse_eeg_devices(_settings(EEG_DEVICES="station1:face@2"))
    assert devices["station1"].kind == "face"
    assert devices["station1"].camera_index == 2


def test_a_face_device_without_an_index_falls_back_to_the_setting():
    devices = parse_eeg_devices(
        _settings(EEG_DEVICES="station1:face", FACE_CAMERA_INDEX="3")
    )
    assert devices["station1"].camera_index == 3


def test_two_face_devices_cannot_share_a_camera():
    with pytest.raises(ValueError, match="already used by another face device"):
        parse_eeg_devices(_settings(EEG_DEVICES="a:face@0,b:face@0"))


def test_a_face_device_and_a_muse_device_do_not_collide():
    devices = parse_eeg_devices(_settings(EEG_DEVICES="cam:face@8765,band:muse@8765"))
    assert devices["cam"].camera_index == 8765
    assert devices["band"].port == 8765


def test_a_non_numeric_camera_index_is_rejected_with_a_useful_message():
    with pytest.raises(ValueError, match="face takes a camera index"):
        parse_eeg_devices(_settings(EEG_DEVICES="a:face@usb0"))


def test_a_negative_camera_index_is_rejected():
    with pytest.raises(ValueError, match="camera index must be >= 0"):
        parse_eeg_devices(_settings(EEG_DEVICES="a:face@-1"))


def test_an_unknown_kind_names_all_three():
    with pytest.raises(ValueError, match="'sim', 'muse' or 'face'"):
        parse_eeg_devices(_settings(EEG_DEVICES="a:webcam"))


def test_muse_and_sim_devices_carry_no_camera_index():
    devices = parse_eeg_devices(_settings(EEG_DEVICES="a:sim,b:muse@8765"))
    assert devices["a"].camera_index is None
    assert devices["b"].camera_index is None


# -- settings floors --

def test_the_camera_is_off_unless_asked_for():
    assert _settings().face_enabled is False


def test_fps_has_a_floor_rather_than_accepting_any_number():
    """0 fps would make the POS window zero-length and the sample interval infinite."""
    with pytest.raises(ValueError):
        _settings(FACE_FPS="0")
    with pytest.raises(ValueError):
        _settings(FACE_CAMERA_INDEX="-1")
    assert _settings(FACE_FPS="15").face_fps == 15.0


# -- the property that must not regress --

def test_the_sidecar_imports_without_any_camera_dependency():
    """A top-level `import cv2` would take the sidecar down on machines without the camera extra."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import importlib, sys
            sys.path.insert(0, sys.argv[1])
            for module in (
                "src.app.config",
                "src.app.services.eeg_ingestion",
                "src.app.services.stream_manager",
                "src.app.services.face_ingestion",
                "src.app.services.face_roi",
                "src.app.services.pos_rppg",
            ):
                importlib.import_module(module)

            leaked = [m for m in ("cv2", "mediapipe", "onnxruntime")
                      if m in sys.modules]
            assert not leaked, f"importing the sidecar pulled in {leaked}"
            print("clean")
        """),
        # The repo root as an argument, so this works from any cwd.
        str(Path(__file__).resolve().parents[1])],
        capture_output=True, text=True,
    )
    assert "clean" in result.stdout, result.stderr


def test_a_face_adapter_is_built_without_touching_a_camera_dependency():
    """A registry may name a camera on a machine with none; cv2 is needed only at connect()."""
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import sys
            sys.path.insert(0, sys.argv[1])
            from src.app.config import Settings
            from src.app.services.eeg_ingestion import build_ingestion_adapter

            settings = Settings(API_TOKEN="x", ADMIN_TOKEN="y",
                                EEG_DEVICES="camera:face@0")
            adapter = build_ingestion_adapter(settings, kind="face", camera_index=0)
            assert hasattr(adapter, "connect") and hasattr(adapter, "drain_samples")
            leaked = [m for m in ("cv2", "mediapipe", "onnxruntime")
                      if m in sys.modules]
            assert not leaked, f"building the adapter pulled in {leaked}"
            print("clean")
        """),
        str(Path(__file__).resolve().parents[1])],
        capture_output=True, text=True,
    )
    assert "clean" in result.stdout, result.stderr


def test_an_unknown_source_names_the_three_valid_ones():
    from src.app.services.eeg_ingestion import build_ingestion_adapter

    with pytest.raises(ValueError, match="'face' for camera capture"):
        build_ingestion_adapter(_settings(), kind="thermal")


def test_the_device_config_default_carries_the_camera_index():
    """Otherwise `EEG_SOURCE=face` with no EEG_DEVICES would always open camera 0."""
    devices = parse_eeg_devices(_settings(EEG_SOURCE="face", FACE_CAMERA_INDEX="2"))
    assert devices["default"] == DeviceConfig(
        device_id="default", kind="face",
        host="127.0.0.1", port=8765, camera_index=2,
    )
