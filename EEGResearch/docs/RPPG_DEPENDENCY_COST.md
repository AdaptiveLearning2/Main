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

### And then it exports, and the export is invalid

```
net.export('rm.onnx', format='onnx', ...)   ->  "Saved artifact"   22 MB, ~90 s
```

Loading that file is where it ends:

```
INVALID_GRAPH: ... ("...mamba_1/conv1d_2_1/StatefulPartitionedCall", StatefulPartitionedCall)
  No Op registered for StatefulPartitionedCall with domain_version of 15
```

`tf2onnx` did not convert Mamba's **grouped Conv1D** (`groups=internal_dim`, a
depthwise convolution) and left a TensorFlow function call in the graph.
Identical at opsets 15, 17 and 18. The export *reports success*; only loading it
under onnxruntime reveals the artefact is not a model -- which is worth knowing
before trusting an exporter's exit code.

### Where this actually leaves the escape route

Much closer than the previous section claimed, and blocked on one named layer
rather than on the architecture:

1. **Replace the grouped Conv1D** with a formulation tf2onnx converts --
   `DepthwiseConv1D`, or a reshape to depthwise Conv2D. Another vendored patch
   of the same size as the others, and the last known obstacle.
2. Then re-export and **check numerical agreement against the Keras output**,
   which is the step this stopped short of.

The patches are small, mechanical and independently useful -- they make the model
backend-agnostic, which is worth something on its own -- but they are patches to a
vendored dependency, which has to be maintained against upstream. That is the
real cost to weigh, not the size of any one edit.

## Reading this

This is a cost, not a verdict. Camera rPPG is still *validated-and-rejected* on
the POS result from 2026-08-08, and nothing measured here changes that; it prices
the reopening the plan proposes.

The cheap escape has been tried and did not work, so the choice is now between
three real options rather than two:

- **Accept the cost** — 600 MB and ~34 s of start-up on a student's laptop, plus
  a pinned deprecated `setuptools` — and go on to the capture, which is the only
  thing that can produce an accuracy number.
- **Finish the ONNX export.** It is one named layer away: the model already
  runs under TensorFlow and exports, and only Mamba's grouped Conv1D fails to
  convert. The cost is maintaining ~15 lines of patch against a vendored
  dependency, not reimplementing anything.
- **Stop here.** *"Rejected again, here is the number"* is a complete outcome by
  the phase's own definition, and this time the number is about the price rather
  than the accuracy.

Two corrections to earlier readings of this file, both of which were too
pessimistic: the Mamba scan is *not* an obstacle, and the model is *not*
JAX-bound in any way that requires reimplementation. What remains is a converter
gap on one layer and a patch set to maintain.

Reproduce with:

```bash
pip install --target /tmp/rppgenv open-rppg "setuptools<81"
PYTHONPATH=/tmp/rppgenv python -c "import time, rppg; t=time.perf_counter(); rppg.Model('RhythmMamba.pure'); print(time.perf_counter()-t)"
```

Nothing here was added to any requirements file. `open-rppg` has still never been
installed into this repo's environment — it was measured in a throwaway target
directory and deleted.
