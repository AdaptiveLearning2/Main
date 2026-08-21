"""Tests the `face` device kind: parsing, addressing, and staying importable.

The last matters most: the sidecar must import on a machine with no cv2, no
mediapipe and no onnxruntime -- CI's state, and any deployment that never
turns the camera on. These tests catch a top-level OpenCV import creeping
into the chain before it takes the sidecar down at startup.

The import checks run in a subprocess, since asserting on `sys.modules`
in-process only works while those packages are uninstalled -- it would pass
or fail based on the environment rather than the code.
"""

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
    """`station1:face@2` means camera index 2. Reusing the port field for it
    would let a face device collide with a muse endpoint in the uniqueness
    check."""
    devices = parse_eeg_devices(_settings(EEG_DEVICES="station1:face@2"))
    assert devices["station1"].kind == "face"
    assert devices["station1"].camera_index == 2


def test_a_face_device_without_an_index_falls_back_to_the_setting():
    devices = parse_eeg_devices(
        _settings(EEG_DEVICES="station1:face", FACE_CAMERA_INDEX="3")
    )
    assert devices["station1"].camera_index == 3


def test_two_face_devices_cannot_share_a_camera():
    """Two devices on index 0 would fight over one webcam and interleave
    frames from the same student into two sessions."""
    with pytest.raises(ValueError, match="already used by another face device"):
        parse_eeg_devices(_settings(EEG_DEVICES="a:face@0,b:face@0"))


def test_a_face_device_and_a_muse_device_do_not_collide():
    """Camera index and port are separate address spaces, so a face on index
    8765 and a muse on port 8765 are unrelated."""
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
    """A deployment with a headband and no webcam is the common case."""
    assert _settings().face_enabled is False


def test_fps_has_a_floor_rather_than_accepting_any_number():
    """A camera reporting 0 fps would make the POS window zero-length and the
    sample interval infinite, so fps needs a floor rather than a check at
    every call site."""
    with pytest.raises(ValueError):
        _settings(FACE_FPS="0")
    with pytest.raises(ValueError):
        _settings(FACE_CAMERA_INDEX="-1")
    assert _settings(FACE_FPS="15").face_fps == 15.0


# -- the property that must not regress --

def test_the_sidecar_imports_without_any_camera_dependency():
    """A top-level `import cv2` anywhere in the chain would be invisible in
    development, where the extra is installed, and would take the sidecar
    down at startup on every machine that never enables the camera.
    """
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
        # Pass the repo root as an argument rather than relying on cwd, which
        # would only work when pytest runs from EEGResearch/.
        str(Path(__file__).resolve().parents[1])],
        capture_output=True, text=True,
    )
    assert "clean" in result.stdout, result.stderr


def test_a_face_adapter_is_built_without_touching_a_camera_dependency():
    """Building the adapter stores two factories and touches no hardware, so
    a device registry can *name* a camera on a machine that has none -- e.g.
    a classroom image shipped to every laptop where only some have webcams
    enabled. cv2 is only required at connect(), when something actually
    tries to open the device, so the failure there names the real problem
    instead of a missing import at startup.

    Checked in a subprocess, since `sys.modules` is process-global: once any
    other test has imported cv2, an in-process assertion that it's absent
    would pass or fail based on test ordering rather than this code.
    """
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
        # Pass the repo root as an argument rather than relying on cwd, which
        # would only work when pytest runs from EEGResearch/.
        str(Path(__file__).resolve().parents[1])],
        capture_output=True, text=True,
    )
    assert "clean" in result.stdout, result.stderr


def test_an_unknown_source_names_the_three_valid_ones():
    from src.app.services.eeg_ingestion import build_ingestion_adapter

    with pytest.raises(ValueError, match="'face' for camera capture"):
        build_ingestion_adapter(_settings(), kind="thermal")


def test_the_device_config_default_carries_the_camera_index():
    """Single-device mode synthesises a `default` device from the plain
    settings and must carry the camera index too, or `EEG_SOURCE=face` with
    no EEG_DEVICES would always open camera 0."""
    devices = parse_eeg_devices(_settings(EEG_SOURCE="face", FACE_CAMERA_INDEX="2"))
    assert devices["default"] == DeviceConfig(
        device_id="default", kind="face",
        host="127.0.0.1", port=8765, camera_index=2,
    )
