"""The two raw-capture scripts run the shipped path, not a copy of it.

The analysis takes its 1/f fit from the one helper, or the printed
slope and the other bands follow a local copy while alpha follows the
shipped fit.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from src.app.services.eeg_spectrum import alpha_residual, one_over_f_fit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def test_the_analysis_has_no_fit_of_its_own():
    src = (SCRIPTS / "analyze_raw_capture.py").read_text(encoding="utf-8")
    assert "polyfit" not in src
    assert "one_over_f_fit(" in src


def test_alpha_residual_is_measured_against_one_over_f_fit():
    rng = np.random.default_rng(0)
    f = np.linspace(0.5, 60, 240)
    log_psd = -1.7 * np.log10(f) + 0.3 + rng.normal(0, 0.05, f.size)
    slope, intercept = one_over_f_fit(f, log_psd)
    r, s = alpha_residual(f, log_psd)
    assert s == slope
    fitted = intercept + slope * np.log10(f)
    band = (f >= 8) & (f < 12)
    assert r == float(np.mean(log_psd[band] - fitted[band]))
