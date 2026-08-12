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

Two routes remain, neither tried:

1. **Keras with the TensorFlow backend, then `tf2onnx`.** Keras 3 can load the
   same `.weights.h5` under a different backend, and the TF export path is more
   mature. Costs a TensorFlow install, but only at export time — the artefact
   shipped would be the `.onnx` file.
2. **Patch `jax2onnx`'s conv converter** to transpose channels-last inputs. A
   narrower fix, upstreamable, and it would still leave the Mamba question open.

### A Windows aside

`jax2onnx` pulls `flax`, which pulls `orbax-checkpoint`, whose own bundled test
fixtures exceed the 260-character path limit and **cannot be installed on
Windows** without enabling long-path support:

```
[WinError 206] The filename or extension is too long:
  ...orbax/checkpoint/experimental/v1/_src/testing/compatibility/checkpoints/...
```

Working around it here meant installing the chain with `--no-deps` one package
at a time. That is export-time tooling on a developer machine rather than
anything a student would install, so it is an inconvenience rather than a
finding about the product — but it is why this took longer than it should, and
it is worth knowing before someone repeats it. On Linux or macOS it is a
non-issue.

## Reading this

This is a cost, not a verdict. Camera rPPG is still *validated-and-rejected* on
the POS result from 2026-08-08, and nothing measured here changes that; it prices
the reopening the plan proposes.

The cheap escape has been tried and did not work, so the choice is now between
three real options rather than two:

- **Accept the cost** — 600 MB and ~34 s of start-up on a student's laptop, plus
  a pinned deprecated `setuptools` — and go on to the capture, which is the only
  thing that can produce an accuracy number.
- **Spend a day on the TensorFlow export route** before committing to that, on
  the chance it makes the cost disappear. It is the more promising of the two
  untried routes, and it would still leave the Mamba question open.
- **Stop here.** *"Rejected again, here is the number"* is a complete outcome by
  the phase's own definition, and this time the number is about the price rather
  than the accuracy.

What should not happen is accepting 600 MB without anyone having tried the TF
route, or dismissing the phase on the conv error alone — that error is a
converter limitation on one layer, not a statement about the model.

Reproduce with:

```bash
pip install --target /tmp/rppgenv open-rppg "setuptools<81"
PYTHONPATH=/tmp/rppgenv python -c "import time, rppg; t=time.perf_counter(); rppg.Model('RhythmMamba.pure'); print(time.perf_counter()-t)"
```

Nothing here was added to any requirements file. `open-rppg` has still never been
installed into this repo's environment — it was measured in a throwaway target
directory and deleted.
