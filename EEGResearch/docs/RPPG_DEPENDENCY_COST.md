# What `open-rppg` costs to install and start

Measured 2026-08-12 on Windows 11, Python 3.12.1. This is the third of Phase 12's
three blockers — *"what it costs on a student's laptop"* — and it is the only one
that could be answered without a camera, an ECG or a consenting adult. It is
answered here so the other two are attempted, or not, with the price known.

The plan asked for install size and cold-start time before committing to the
dependency, on the grounds that *"a heart rate that doubles the footprint of the
thing a child has to install is a trade to make deliberately."* It does not
double it. It roughly triples it, and adds half a minute to every start.

## The numbers

| | |
| --- | --- |
| Installed footprint, `open-rppg` and every dependency | **918 MB** |
| Of which the camera path already installs (`cv2`, `onnxruntime`, `numpy`, `scipy`) | ~310 MB |
| **New** cost over today's `face` extra | **~600 MB** |
| Largest single addition | `jaxlib`, **252 MB** |
| Next largest | `rppg` 89 MB (weights included), `av.libs` 66 MB, `matplotlib` 32 MB |
| Wheel download | 107 MB new |
| `import rppg` | **5.3 s** (interpreter baseline 0.04 s) |
| `rppg.Model('RhythmMamba.pure')` | **27.5 s** on top of that |
| Import + load, wall clock | **~34 s** |

Timings are the best of three runs, so they are the optimistic case; a cold file
cache on a school laptop will be worse.

## The packaging problem

`open-rppg` 0.1.1 imports `pkg_resources`, which setuptools removed in 81.0.
On a current environment it fails at import:

```
ModuleNotFoundError: No module named 'pkg_resources'
```

Adopting it therefore means pinning `setuptools<81` in the sidecar — a deprecated
package, held back to satisfy one dependency, on software installed on other
people's machines. That is a maintenance liability rather than a one-off cost,
and it is not visible from the package's own metadata; it only appears when you
try to import it.

## What did work

`RhythmMamba.pure.weights.h5` **is** published and loads. Both suffixes ship:

```
RhythmMamba.pure.weights.h5
RhythmMamba.rlap.weights.h5
```

So the licence-safe path is real, not theoretical — the reason to prefer `.pure`
(PURE-trained, no RLAP lineage, no agreement to interpret) survives contact with
the actual package. That was worth confirming independently of whether the
dependency is affordable, because it is the half that a future attempt cannot
work around.

## What this does not measure

- **Inference cost.** Nothing was run over frames. A 27 s model load is a
  start-up cost paid once; per-window inference is a different number and is
  what would decide whether this can run alongside EEG on the same laptop.
- **Accuracy.** Still unmeasured, still blocked on a synchronised video and ECG
  capture, which is Phase 12's first blocker and unchanged by any of this.
- **Inference accuracy or speed under ONNX.** See below — the export does not
  currently succeed, so there is nothing to measure yet.

## The ONNX escape route, attempted

ONNX Runtime is already a dependency of the camera extra, so if these weights
export, the 600 MB and most of the start-up go away and the `pkg_resources`
liability with them — the problem is `open-rppg` the package, not the model.
Attempted 2026-08-12. **It does not currently export**, and the reason is not
the one expected.

`RhythmMamba` is a `keras.Model` subclass and Keras 3.15's
`Model.export(..., format="onnx")` is the supported route. Under the JAX backend
that delegates to `jax2onnx`, and there it fails:

```
NotImplementedError: Unsupported conv layouts:
  lhs=(0, 4, 1, 2, 3), rhs=(4, 3, 0, 1, 2), out=(0, 4, 1, 2, 3)
```

That is a **channels-last 3D convolution** in `Fusion_Stem`, which `jax2onnx`'s
converter does not handle. Forcing `channels_first` globally does not help: the
model code hardcodes channels-last reshapes and fails to build at all —

```
cannot reshape array of shape (1, 3, 640, 128, 128) into shape (3, 160, 128, 12)
```

**Note what this did *not* establish.** The expected blocker was the Mamba
selective scan, since state-space models often use ops with no ONNX equivalent.
The export dies on the convolution before reaching it, so whether the scan is
exportable is **still unknown**. Anyone resuming this should not read "conv
layout" as the only obstacle.

### The TensorFlow route, pursued properly

The first attempt failed with `'EagerTensor' object has no attribute 'at'` and I
concluded the model was JAX-bound by construction and that dropping JAX meant
reimplementing it. **That was wrong, and twice over.** Reading the source and
then patching it establishes something much narrower.

**What is actually JAX-specific in the forward pass:**

| Thing | Verdict |
| --- | --- |
| `selective_scan` -- the Mamba scan | **Not a blocker.** Pure `keras.ops`: einsum, pad, `cumsum`, exp, reversal. The cumsum formulation, no `lax.scan`, no custom op. Every op has an ONNX equivalent, and it is backend-agnostic already. This was the thing most expected to block, and it does not. |
| `scale_seg` -- the `@jax.jit` nested loop | **Dead code.** Its call site in `Block_mamba.call` is commented out and the logic inlined. |
| `Block_mamba.call` -- three `.at[].set()` lines | The only real one. Python loops over a *static* range (`segment = 4`), so they are a temporal shift and a cumulative average, expressible as concat-of-slices in ~10 backend-agnostic lines. |
| `models.py:2`, `models.py:18` | Forces `KERAS_BACKEND="jax"` and `mixed_float16` globally. |

Everything else matching `jax`/`jnp` in that file is in `load_*` wrappers or
other architectures, not in `RhythmMamba`'s graph.

**The model relies on JAX's implicit dtype promotion**, which surfaces once the
backend changes: `A` and `D` are float32 by construction while the rest of the
graph is float16 under the mixed-precision policy, and TF refuses to multiply
across the two. Four separate sites appeared before the pattern was clear. The
one-line fix is to run the policy at `float32` rather than patch each site.

With those changes -- one line for the backend, one for the policy, ~10 for
`Block_mamba` -- **the model runs under TensorFlow and the weights load.**

### And then it exports, and the export is invalid -- three times

`net.export(..., format='onnx')` prints `Saved artifact` and writes 22 MB. The
file is not a model. Loading it under onnxruntime is the only way to find out,
and it took three rounds:

| Unconverted op | Where | Fix |
| --- | --- | --- |
| `StatefulPartitionedCall` | Mamba's **grouped Conv1D** | `groups == filters == channels`, so it *is* depthwise; compute it with `ops.depthwise_conv` and one kernel transpose |
| `StatefulPartitionedCall` | the same, again | the first patch landed on `BiMamba` -- the same source line, in a class `RhythmMamba` never calls, with `same` padding instead of `causal`. Patch the *second* occurrence. |
| `RFFT` | `Frequencydomain_FFN` | tf2onnx converts it at no opset it supports (it caps at 19). The transform length is fixed by the input signature, so the DFT is a constant matrix and the transform is a matmul. |

Each rewrite was checked against the version it replaced as it was made, and all
of them together are checked against the unpatched package by the script itself
-- see the numbers below. The DFT matrices additionally have unit tests against
`numpy.fft` (`tests/test_export_rhythmmamba_onnx.py`), which need none of the
2.5 GB toolchain and so run in CI.

## It works

```
*** RUNS under onnxruntime alone -- no jax, keras or tensorflow imported ***
  session load     1.58 s
  inference        0.97 s for 160 frames
  max abs diff     4.802e-02   (vs the UNPATCHED package)
  correlation      0.99984906
```

**Against the unpatched package**, not against the patched model. An earlier
version of this measured the ONNX graph against the *patched* TensorFlow model,
which only established that the export reproduced the thing it was exported
from -- it said nothing about whether the patches preserved the original's
behaviour. The script now captures the baseline before applying any patch, so
the comparison is end-to-end by construction.

The three numbers that matter, all on the same input:

| Comparison | max abs diff | correlation |
| --- | --- | --- |
| patches alone, both JAX, both float32 | 1.06e-04 | 0.9999999993 |
| patches alone, both JAX, at the package's `mixed_float16` | 1.76e-02 | 0.99998229 |
| **ONNX vs the package as shipped** | **4.80e-02** | **0.99984906** |
| ONNX vs the package at float32, like for like | 4.96e-03 | 0.99999837 |

Read together: the patches are equivalent to op reordering (row 1), float16
accumulation is what makes that visible (row 2), and the end-to-end divergence
is dominated by precision and by TF/ONNX kernel differences rather than by the
rewrites. On a biosignal waveform 0.9998 is agreement; a mis-wired graph does
not land near it.

| | `open-rppg` | exported ONNX |
| --- | --- | --- |
| New dependencies | ~600 MB | **none** -- onnxruntime is already in the `face` extra |
| Start-up | ~34 s | **1.5 s** |
| Artefact | -- | 22 MB |
| Deprecated `setuptools<81` pin | required | not required |
| Inference, 160 frames | not measured | 0.93 s -- 5.3 s of video at 30 fps, so ~6x real time on CPU |

That last row is the one that answers a question the plan asked separately:
whether this could run alongside EEG on the same laptop. On this hardware, a
25 s window's worth of inference costs about four seconds of CPU.

`scripts/export_rhythmmamba_onnx.py` is the recipe, and it verifies its own
output under onnxruntime by default -- because the exporter reports success
either way, and skipping that check is exactly how an invalid graph would get
shipped.

The `.onnx` is deliberately **not committed**: it is derived from weights whose
licence terms belong to their authors, and a binary nobody can regenerate is
worse than a script that regenerates it.

### What this does and does not settle

It settles the **cost**. Camera rPPG no longer implies 600 MB, a 34 s start or a
deprecated setuptools pin; it implies a 22 MB file and a dependency already
present.

It settles nothing about **accuracy**, which is Phase 12's first blocker and
needs a synchronised video and ECG capture. The confidence gate is still
undesigned and still has to be measured on its refusals. The three-minute POS
rejection from 2026-08-08 still stands as the last word on whether this webcam
can produce a heart rate at all.

The remaining cost is the patch set: ~20 lines against a vendored dependency,
which has to be reapplied when `open-rppg` moves. The script fails loudly rather
than silently if upstream changes under it.

## The capture, which is what is left

`scripts/capture_face_video_ecg.py` records the frames Phase 12's first blocker
needs. It is the only script here that writes images of a face to disk, and it
is built around making the second half -- deleting them -- hard to forget.

**It stores 128x128 face crops, not video.** That is exactly what RhythmMamba
consumes, and it is lossless: rPPG reads colour variation well under one part in
a hundred, and every lossy codec is designed to discard precisely that, so an
MP4 would look identical and measure nothing. Crops rather than full frames both
because it is what the model takes and because 128x128 is ~1.5 MB/s where raw
640x480 is ~27 MB/s -- five minutes is 440 MB rather than 8 GB. The cost is that
the ROI choice is baked in; a later analysis wanting a different region has to
re-capture. That is the deliberate trade against storing more of a person than
the measurement needs.

**It refuses to write anywhere inside the repository.** Not a stylistic
preference: everything else this project writes is committable by design, this
is the one artefact that must never be, and `git add -A` does not ask. The check
is on path components rather than string prefixes, so a sibling directory named
`AdaptiveLearning-captures` is not caught by accident.

**Alignment is by offset search, not by a shared clock.** Nothing here talks to
a watch. The header records the wall-clock start and every frame carries a
`perf_counter` offset, which is what lets the two be lined up afterwards -- the
same approach `test_hrv_against_ecg.py` already uses with its `PAIRS` offsets.

**Exposure lock is recorded, not assumed.** Auto-exposure hunting is a
brightness oscillation the model would read as a pulse. `OpenCvFrameSource`
tries to lock it; whether the driver accepted is written into the header, so an
unlocked capture is known about before the analysis rather than after.

`--delete` removes the frames and the per-frame log, keeps the header, and
stamps it with the deletion time -- because a cleaned-up capture with no trace
is indistinguishable from one nobody cleaned up.

### What is still needed after the capture

- **A confidence gate, designed and measured on its refusals.** `ppg_processing`'s
  is inapplicable to a single channel and always was. One input that did not
  exist when this was written: `face_geometry` (Phase 11) now yields head pose,
  which is a motion signal independent of the periodicity being confused -- the
  role CLAUDE.md gives the accelerometer for the headband's gait failure. It has
  to be measured against the capture before it means anything.
- **A decision recorded either way.** "Rejected again, here is the number" is a
  complete outcome by the phase's own definition.

## Reading this

Camera rPPG is still *validated-and-rejected* on the POS result from
2026-08-08, and nothing here changes that. What has changed is the price: the
reopening the plan proposes no longer costs 600 MB and half a minute of
start-up, so the only remaining question is the one that always mattered --
whether it is accurate, which needs a capture.

The cheap escape has been tried and did not work, so the choice is now between
three real options rather than two:

- **Accept the cost** — 600 MB and ~34 s of start-up on a student's laptop, plus
  a pinned deprecated `setuptools` — and go on to the capture, which is the only
  thing that can produce an accuracy number.
- **The ONNX export is done** (`scripts/export_rhythmmamba_onnx.py`), so the
  cost objection is gone: no new dependencies, 1.5 s start-up, 22 MB. What is
  left to decide is whether to spend a capture session on accuracy.
- **Stop here.** *"Rejected again, here is the number"* is a complete outcome by
  the phase's own definition, and this time the number is about the price rather
  than the accuracy.

Three earlier readings of this file were wrong, all pessimistic: the Mamba scan
is not an obstacle, the model is not JAX-bound in any way requiring
reimplementation, and the export was not one layer away -- it was three, and all
three are done. Each was corrected by reading the source or loading the artefact
rather than by reasoning from the previous error message.

Reproduce with:

```bash
pip install --target /tmp/rppgenv open-rppg "setuptools<81"
PYTHONPATH=/tmp/rppgenv python -c "import time, rppg; t=time.perf_counter(); rppg.Model('RhythmMamba.pure'); print(time.perf_counter()-t)"
```

Nothing here was added to any requirements file. `open-rppg` has still never been
installed into this repo's environment — it was measured in a throwaway target
directory and deleted.
