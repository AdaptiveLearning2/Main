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
- **A lighter path.** ONNX Runtime is already a dependency of the camera extra.
  Whether these weights can be exported to ONNX and run without JAX, Keras and
  `av` was not investigated, and it is the obvious thing to try before accepting
  600 MB. It would also sidestep the `pkg_resources` problem, since the
  liability is `open-rppg` the package rather than the model.

## Reading this

This is a cost, not a verdict. Camera rPPG is still *validated-and-rejected* on
the POS result from 2026-08-08, and this measurement does not change that; it
prices the reopening the plan proposes. Two things follow:

- If the phase proceeds, the ONNX question should be asked first. 600 MB and a
  34 s start are worth avoiding, and the export may be cheap.
- If it does not, this is the recorded reason for one of the three blockers, and
  *"rejected again, here is the number"* is a complete outcome by the phase's own
  definition.

Reproduce with:

```bash
pip install --target /tmp/rppgenv open-rppg "setuptools<81"
PYTHONPATH=/tmp/rppgenv python -c "import time, rppg; t=time.perf_counter(); rppg.Model('RhythmMamba.pure'); print(time.perf_counter()-t)"
```

Nothing here was added to any requirements file. `open-rppg` has still never been
installed into this repo's environment — it was measured in a throwaway target
directory and deleted.
