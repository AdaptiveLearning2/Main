# Muse on Windows (Interaxon libMuse SDK)

This project targets **Windows only** and integrates with the headband through Interaxon’s **Windows libMuse SDK** (headers and examples live alongside your dev environment; the SDK folder is not committed to this repo).

**Step 1 — prove the SDK and headband work:** follow `docs/MUSE_SDK_STEP1_CONNECTIVITY.md` (build/run **GettingData** from `LibMuseExamples.sln`). Use `.\scripts\open_libmuse_sdk_solution.ps1` from the repo root to open that solution in Visual Studio.

## Why a bridge

libMuse is a **native C++ API** (UWP/desktop patterns depending on the bundle you use). This Python codebase should **not** implement BLE or the Muse wire protocol directly. Instead:

1. A small **native bridge** executable uses the SDK to discover, connect, stream, and reconnect.
2. The bridge forwards **normalized EEG samples** to Python over **localhost** (TCP, named pipe, or gRPC—TCP is simplest to debug).

Python then owns **filters, features, confidence, adaptation**, and optional file replay for tests.

## Bridge responsibilities

- Discover Muse devices and connect to the target headband (Muse S Athena as supported by your SDK version).
- Subscribe to EEG (and optionally PPG/ACC later if needed).
- Emit a **stable, versioned** message format on every sample or packet batch.
- Handle **reconnect** and surface **stream health** (last packet time, gaps, errors).
- Use **SDK-documented** timestamps; if you only have wall clock, document that explicitly.

## Recommended message shape (JSON lines over TCP)

Each line is one JSON object. Start with `schema_version` so Python can evolve parsers.

Example (single sample row—adjust to match your actual SDK callback):

```json
{
  "schema_version": 1,
  "mono_ts_ms": 1735689600123,
  "device_ts_ms": null,
  "seq": 9001,
  "tp9": 12.34,
  "af7": 12.34,
  "af8": 12.34,
  "tp10": 12.34,
  "units": "microvolts_or_sdk_native",
  "sample_rate_hz": 256
}
```

**Fields to define in your first integration PR:**

- Units and scaling as returned by the SDK (do not guess; read SDK docs / sample code).
- Whether you send **one row per sample** or **batched arrays** (batching reduces overhead).
- Sequence numbers for gap detection.

## Python side (this repo)

- Implement a `MuseIngestionAdapter` (or TCP client) that reads lines from `127.0.0.1:<port>` and produces `EegSample` objects for `SignalProcessor`.
- Keep the **simulator** behind a flag (`EEG_SOURCE=sim`) so CI and laptops without hardware still run.
- Extend metrics with **packets/sec**, **last sample age**, **parse errors**, **reconnects**.

## Build and run (high level)

1. Install Visual Studio workload for **Desktop development with C++** and any **UWP** components required by the SDK examples (match the SDK’s documented prerequisites).
2. Open the SDK examples solution (e.g. `LibMuseExamples.sln` in the SDK tree) and confirm **GettingData** (or equivalent) streams EEG from your headband.
3. Fork the smallest example into a **headless bridge** that writes to TCP instead of UI.
4. Document in your bridge README:
   - SDK version string
   - Target platform (x64 / ARM64)
   - Required runtime DLLs next to the exe

## Versioning

Record the **exact libMuse / SDK package version** you build against. Packet types and supported models can change between releases.

## Non-medical use

EEG-derived outputs here are for **research and learning feedback**, not clinical or diagnostic use.
