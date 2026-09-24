"""The ONNX export's dependency-free parts: the injected DFT helpers, and patch()'s refusals."""

from __future__ import annotations

import importlib.util
import pathlib

import numpy as np
import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "scripts" / "export_rhythmmamba_onnx.py")


def _module():
    spec = importlib.util.spec_from_file_location("export_rhythmmamba_onnx", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


export = _module()

# Executed from the exact string the script injects, so the test cannot drift.
_injected: dict = {"np": np}
exec(export.DFT_HELPERS, _injected)          # noqa: S102
dft_matrices = _injected["_dft_matrices"]
idft_matrices = _injected["_idft_matrices"]


# -- the transforms --

@pytest.mark.parametrize("L", [7, 8, 16, 160])
def test_the_dft_matrices_match_numpy_rfft(L):
    """Odd, even (unmirrored Nyquist bin), and the model's window length 160."""
    rng = np.random.default_rng(0)
    x = rng.standard_normal((3, L)).astype("float32")

    cos_m, sin_m = dft_matrices(L)
    real, imag = x @ cos_m, x @ sin_m
    expected = np.fft.rfft(x, axis=-1)

    assert real.shape == (3, L // 2 + 1)
    assert np.abs(real - expected.real).max() < 1e-3
    assert np.abs(imag - expected.imag).max() < 1e-3


@pytest.mark.parametrize("L", [7, 8, 16, 160])
def test_the_inverse_matrices_reconstruct_the_signal(L):
    rng = np.random.default_rng(1)
    x = rng.standard_normal((3, L)).astype("float32")

    cos_m, sin_m = dft_matrices(L)
    icos_m, isin_m = idft_matrices(L)
    back = (x @ cos_m) @ icos_m + (x @ sin_m) @ isin_m

    assert np.abs(back - x).max() < 1e-3


@pytest.mark.parametrize("L", [8, 160])
def test_the_inverse_matches_numpy_irfft_on_arbitrary_spectra(L):
    """The model reweights coefficients before inverting, so arbitrary spectra must invert."""
    rng = np.random.default_rng(2)
    real = rng.standard_normal((3, L // 2 + 1)).astype("float32")
    imag = rng.standard_normal((3, L // 2 + 1)).astype("float32")

    icos_m, isin_m = idft_matrices(L)
    got = real @ icos_m + imag @ isin_m
    expected = np.fft.irfft(real + 1j * imag, n=L, axis=-1)

    assert np.abs(got - expected).max() < 1e-3


def test_the_matrices_are_float32_so_they_do_not_promote_the_graph():
    """float64 constants would silently upcast every downstream op."""
    for m in (*dft_matrices(160), *idft_matrices(160)):
        assert m.dtype == np.float32


# -- refusing to patch source it does not recognise --

def test_patching_fails_loudly_when_upstream_has_moved(tmp_path):
    """A half-patched model would still import and produce numbers."""
    models = tmp_path / "rppg" / "models.py"
    models.parent.mkdir()
    models.write_text("# a models.py that shares nothing with 0.1.1\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        export.patch(models)
    # Specific: "upstream source has changed" also appears in the conv-count message.
    assert "could not apply" in str(exc.value)


def test_patching_refuses_when_the_conv_site_count_is_wrong(tmp_path):
    """Expects BiMamba's and Mamba's sites; patching by position would hit the wrong layer."""
    src = "\n".join(old for _, old, _ in export.PATCHES)
    src += "\n" + export.CONV_ORIGINAL + "\n"        # one site, not two
    models = tmp_path / "rppg" / "models.py"
    models.parent.mkdir()
    models.write_text(src, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        export.patch(models)
    assert "conv1d call sites" in str(exc.value)


def test_patching_twice_is_refused_rather_than_compounded(tmp_path):
    """Already-patched source says so rather than failing with 'could not apply'."""
    models = tmp_path / "rppg" / "models.py"
    models.parent.mkdir()
    models.write_text("def _dft_matrices(L):\n    pass\n", encoding="utf-8")

    export.patch(models)          # prints "already patched", does not raise
