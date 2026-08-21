#!/usr/bin/env python3
"""Export open-rppg's RhythmMamba.pure to a standalone ONNX model.

Why this exists
---------------
`open-rppg` costs ~600 MB of dependencies and ~34s of start-up (see
docs/RPPG_DEPENDENCY_COST.md). Exported to ONNX it costs **nothing new** --
onnxruntime is already a dependency -- and loads in ~1.5s. The exported file is
22 MB and agrees with the unpatched package at correlation 0.99985.

The `.onnx` file itself is not committed: it's derived from weights whose
licence terms belong to their authors, and a binary nobody can regenerate is
worse than a script that produces it.

**This does not make camera rPPG work.** Accuracy against a reference is
still unmeasured and needs a synchronised video and ECG capture. This only
establishes that the plumbing is affordable.

What it patches, and why
-------------------------
Five edits to a vendored `rppg/models.py`, applied to an installed copy (never
committed to that package -- run against a throwaway install):

1. **Don't force the JAX backend** (`models.py:2`) -- the module overrides the
   caller's `KERAS_BACKEND` at import.
2. **Make the precision policy overridable** (`models.py:18`) -- the model
   relies on JAX's implicit float16/float32 promotion, which TensorFlow
   refuses. Running at float32 sidesteps every such site at once.
3. **`Block_mamba.call`: replace three `.at[].set()` lines** -- JAX-only
   indexed updates over a static range (a temporal shift and a cumulative
   average), rewritten as concat-of-slices. The only JAX binding in the
   forward pass.
4. **`Mamba.call`: compute the grouped Conv1D explicitly** -- it's really a
   depthwise convolution (`groups == filters == channels`), but tf2onnx
   leaves Keras 3's grouped Conv1D unconverted. The layer stays so
   `load_weights` still matches by structure; only the computation changes.
5. **`Frequencydomain_FFN.call`: replace `rfft`/`irfft` with constant
   matmuls** -- tf2onnx can't convert TensorFlow's RFFT. Since the transform
   length is fixed by the input signature, the DFT is just a constant matrix.

All five are verified together, not by inspection: the script captures the
unpatched model's output before patching, and the final check compares the
ONNX graph against that baseline. Measured on the same input at float32, the
patches alone move the output by 1.1e-04 -- op reordering, not a behaviour
change.

Usage
-----
    pip install --target /tmp/rppgenv open-rppg "setuptools<81" tensorflow tf2onnx
    python scripts/export_rhythmmamba_onnx.py --rppg /tmp/rppgenv --out rm.onnx

Then it needs nothing but onnxruntime:

    sess = onnxruntime.InferenceSession("rm.onnx")
    bvp = sess.run(None, {sess.get_inputs()[0].name: frames})[0]   # (1, 160)

`frames` is `(1, 160, 128, 128, 3)` float32. The shape is static: a different
window length needs a new export.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import textwrap

PATCHES: list[tuple[str, str, str]] = [
    (
        "force the JAX backend",
        'os.environ["KERAS_BACKEND"] = "jax"',
        'os.environ.setdefault("KERAS_BACKEND", "jax")',
    ),
    (
        "hardcode mixed_float16",
        'keras.mixed_precision.set_global_policy("mixed_float16")',
        'keras.mixed_precision.set_global_policy(os.environ.get("RPPG_POLICY", "mixed_float16"))',
    ),
    (
        "Block_mamba temporal shift",
        """        x_o = x_r
        for i in range(1, segment):
            x_o = x_o.at[i*B:(i+1)*B,:D-i*tt,:].set(x_r[i*B:(i+1)*B,i*tt:,:])""",
        """        parts = [x_r[0:B]]
        for i in range(1, segment):
            shifted = x_r[i*B:(i+1)*B, i*tt:, :]
            pad = ops.zeros_like(x_r[i*B:(i+1)*B, :i*tt, :])
            parts.append(ops.concatenate([shifted, pad], axis=1))
        x_o = ops.concatenate(parts, axis=0)""",
    ),
    (
        "Block_mamba cumulative average",
        """        for i in range(1, segment):
            for j in range(i):
                x_o = x_o.at[0:B, tt*i: tt*(i+1) , :].set(x_o[0:B, tt*i: tt*(i+1) , :] + x_o[B*(j+1):B*(j+2), tt*(i-j-1): tt*(i-j),:])
            x_o = x_o.at[0:B, tt*i: tt*(i+1) , :].set(x_o[0:B, tt*i: tt*(i+1) , :] / (i+1))""",
        """        head = [x_o[0:B, 0:tt, :]]
        for i in range(1, segment):
            seg_i = x_o[0:B, tt*i: tt*(i+1), :]
            for j in range(i):
                seg_i = seg_i + x_o[B*(j+1):B*(j+2), tt*(i-j-1): tt*(i-j), :]
            head.append(seg_i / (i+1))
        x_o = ops.concatenate([ops.concatenate(head, axis=1), x_o[B:]], axis=0)""",
    ),
    (
        "Frequencydomain_FFN forward transform",
        '        real, imag = ops.rfft(rearrange(x, "b l c -> b c l"))',
        """        _dc, _ds = _dft_matrices(L)
        _xt = rearrange(x, "b l c -> b c l")
        real = ops.einsum("bcl,lk->bck", _xt, _dc)
        imag = ops.einsum("bcl,lk->bck", _xt, _ds)""",
    ),
    (
        "Frequencydomain_FFN inverse transform",
        "        x = ops.irfft((x_real, x_imag))",
        """        _ic, _is = _idft_matrices(L)
        x = (ops.einsum("bck,kl->bcl", x_real, _ic)
             + ops.einsum("bck,kl->bcl", x_imag, _is))""",
    ),
]

# Applied separately from PATCHES: the same source line also appears in
# BiMamba (never called by RhythmMamba, different padding mode), so a blind
# replace would silently patch the wrong layer.
CONV_ORIGINAL = "        x = self.conv1d(x)[:, :seq_len]"
CONV_PATCHED = """        # Never called now, but the weights still need to exist for
        # load_weights to populate them.
        if not self.conv1d.built:
            self.conv1d.build(x.shape)
        k = ops.transpose(self.conv1d.kernel, (0, 2, 1))
        pad = self.d_conv - 1
        x_pad = ops.concatenate([ops.zeros_like(x[:, :pad, :]), x], axis=1)
        x = ops.depthwise_conv(x_pad, k, strides=1, padding='valid')
        x = x + ops.reshape(self.conv1d.bias, (1, 1, -1))
        x = x[:, :seq_len]"""

DFT_HELPERS = '''

_DFT_CACHE = {}


def _dft_matrices(L):
    """Real and imaginary parts of the length-L RFFT, as (L, L//2+1) matrices."""
    key = ("f", int(L))
    if key not in _DFT_CACHE:
        n = np.arange(L)[:, None]
        k = np.arange(L // 2 + 1)[None, :]
        ang = -2.0 * np.pi * n * k / L
        _DFT_CACHE[key] = (np.cos(ang).astype("float32"),
                           np.sin(ang).astype("float32"))
    return _DFT_CACHE[key]


def _idft_matrices(L):
    """The inverse, as (L//2+1, L) matrices.

    Hermitian symmetry doubles every bin except DC and, for even L, Nyquist.
    """
    key = ("i", int(L))
    if key not in _DFT_CACHE:
        k = np.arange(L // 2 + 1)[:, None]
        n = np.arange(L)[None, :]
        ang = 2.0 * np.pi * k * n / L
        weight = np.full((L // 2 + 1, 1), 2.0)
        weight[0] = 1.0
        if L % 2 == 0:
            weight[-1] = 1.0
        _DFT_CACHE[key] = ((weight * np.cos(ang) / L).astype("float32"),
                           (-weight * np.sin(ang) / L).astype("float32"))
    return _DFT_CACHE[key]

'''


def patch(models_py: pathlib.Path) -> None:
    src = models_py.read_text(encoding="utf-8")
    if "_dft_matrices" in src:
        print("already patched")
        return

    for name, old, new in PATCHES:
        if old not in src:
            raise SystemExit(
                f"could not apply '{name}': upstream source has changed.\n"
                f"This script is written against open-rppg 0.1.1."
            )
        src = src.replace(old, new, 1)

    # Second occurrence only -- the first belongs to BiMamba.
    if src.count(CONV_ORIGINAL) != 2:
        raise SystemExit(
            f"expected 2 conv1d call sites (BiMamba, Mamba), found "
            f"{src.count(CONV_ORIGINAL)} -- upstream source has changed"
        )
    head, sep, tail = src.partition(CONV_ORIGINAL)
    src = head + sep + tail.replace(CONV_ORIGINAL, CONV_PATCHED, 1)

    src = src.replace("class Frequencydomain_FFN(layers.Layer):",
                      DFT_HELPERS.lstrip("\n") + "\nclass Frequencydomain_FFN(layers.Layer):", 1)
    models_py.write_text(src, encoding="utf-8")
    print(f"patched {models_py}")


BASELINE = textwrap.dedent("""
    import glob, os, sys
    import numpy as np, rppg, rppg.main as M

    out = sys.argv[1]
    w = glob.glob(os.path.join(os.path.dirname(rppg.__file__),
                               'weights', 'RhythmMamba.pure.weights.h5'))[0]
    net = M.RhythmMamba()
    net(np.zeros((1, 160, 128, 128, 3), dtype='float32'))
    net.load_weights(w)

    rng = np.random.default_rng(0)
    x = rng.random((1, 160, 128, 128, 3), dtype='float32')
    np.save(out + '.input.npy', x)
    np.save(out + '.expected.npy', np.asarray(net(x), dtype='float32'))
    print('baseline captured from the unpatched model')
""")

EXPORT = textwrap.dedent("""
    import glob, os, sys, time
    import numpy as np, keras, rppg, rppg.main as M

    out = sys.argv[1]
    w = glob.glob(os.path.join(os.path.dirname(rppg.__file__),
                               'weights', 'RhythmMamba.pure.weights.h5'))[0]
    net = M.RhythmMamba()
    net(np.zeros((1, 160, 128, 128, 3), dtype='float32'))
    net.load_weights(w)

    t = time.perf_counter()
    net.export(out, format='onnx',
               input_signature=[keras.InputSpec(shape=(1, 160, 128, 128, 3),
                                                dtype='float32')])
    print(f'exported {os.path.getsize(out) / 1e6:.0f} MB in {time.perf_counter() - t:.0f}s')
""")

VERIFY = textwrap.dedent("""
    import sys, time
    import numpy as np, onnxruntime as ort

    for module in ('jax', 'keras', 'tensorflow', 'rppg'):
        assert module not in sys.modules, f'{module} is loaded; this is not a clean check'

    out = sys.argv[1]
    x = np.load(out + '.input.npy')
    expected = np.load(out + '.expected.npy')

    t = time.perf_counter()
    sess = ort.InferenceSession(out, providers=['CPUExecutionProvider'])
    load = time.perf_counter() - t
    name = sess.get_inputs()[0].name
    t = time.perf_counter()
    got = np.asarray(sess.run(None, {name: x})[0]).reshape(expected.shape)
    infer = time.perf_counter() - t

    diff = float(np.abs(got - expected).max())
    corr = float(np.corrcoef(got.ravel(), expected.ravel())[0, 1])
    print(f'  session load    {load:.2f}s')
    print(f'  inference       {infer:.2f}s for 160 frames')
    print(f'  max abs diff    {diff:.3e}  (vs the UNPATCHED model)')
    print(f'  correlation     {corr:.8f}')
    # Correlation, not absolute difference: the reference package runs at
    # mixed_float16 while the export runs at float32, so individual samples
    # move by ~5e-2 on a waveform spanning ~4 -- that's precision, not a wiring
    # bug. A genuinely broken graph would score nothing close to 0.999.
    assert corr > 0.999, f'exported waveform does not match the original ({corr})'
    print('  OK -- matches the unpatched model')
""")


def main() -> int:
    ap = argparse.ArgumentParser(description="Export RhythmMamba.pure to ONNX")
    ap.add_argument("--rppg", required=True,
                    help="target dir holding an installed open-rppg (patched in place)")
    ap.add_argument("--out", default="rhythmmamba_pure.onnx")
    ap.add_argument("--verify-with", default=None,
                    help="target dir holding onnxruntime and numpy ONLY; "
                         "skipped if omitted, and skipping it is how an invalid "
                         "graph gets shipped -- the exporter reports success either way")
    args = ap.parse_args()

    models = pathlib.Path(args.rppg) / "rppg" / "models.py"
    if not models.exists():
        raise SystemExit(f"no open-rppg at {models}")
    if "_dft_matrices" in models.read_text(encoding="utf-8"):
        raise SystemExit(
            f"{models} is already patched, so there is no pristine model left "
            f"to measure against. Reinstall open-rppg into a clean target."
        )

    # The reference must come from the UNPATCHED model. Capturing it after
    # patching would have the patched model grading itself, proving only that
    # ONNX matches the patched TensorFlow code, not that the patches preserved
    # the original's behaviour.
    print("capturing the baseline from the unpatched model...")
    base_env = dict(os.environ, PYTHONPATH=args.rppg, KERAS_BACKEND="jax",
                    TF_CPP_MIN_LOG_LEVEL="3")
    base_env.pop("RPPG_POLICY", None)      # use the package's own mixed_float16
    if subprocess.run([sys.executable, "-c", BASELINE, args.out],
                      env=base_env).returncode:
        return 1

    patch(models)

    env = dict(os.environ, PYTHONPATH=args.rppg, KERAS_BACKEND="tensorflow",
               RPPG_POLICY="float32", TF_CPP_MIN_LOG_LEVEL="3")
    if subprocess.run([sys.executable, "-c", EXPORT, args.out], env=env).returncode:
        return 1

    if not args.verify_with:
        print("\nNOT VERIFIED. Pass --verify-with to load the result under "
              "onnxruntime alone.\nThe export reports success even when it "
              "leaves unconvertible ops in the graph.")
        return 0

    print("\nverifying under onnxruntime alone:")
    env = dict(os.environ, PYTHONPATH=args.verify_with)
    return subprocess.run([sys.executable, "-c", VERIFY, args.out], env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
