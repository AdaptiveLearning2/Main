# Native Muse Bridge

Windows C++ service that reads libMuse packets and streams normalized JSON over localhost TCP for the Python backend.

## What it sends

Each JSON line includes:

- raw EEG channels: `tp9`, `af7`, `af8`, `tp10`
- device/connection metadata (`muse_connected`, `connection_state`, device name/version)
- Muse bands when available: `delta`, `theta`, `alpha`, `beta`, `gamma`

Default endpoint:

- `127.0.0.1:8765`

## Build and Run

Recommended (from repo root):

- `.\scripts\run_native_bridge.ps1 -EnableLibMuse`

Manual CMake:

1. `cmake -S native_bridge -B native_bridge/build -G "Visual Studio 18 2026" -A x64 -DENABLE_LIBMUSE=ON -DLIBMUSE_SDK_DIR="C:/EEgProject/EEGResearch/libmuse_windows_8.0.5"`
2. `cmake --build native_bridge/build --config Release`
3. `.\native_bridge\build\Release\muse_native_bridge.exe`

## Bridge Commands

The bridge accepts newline-delimited JSON commands from the Python adapter:

- `{"cmd":"refresh"}`
- `{"cmd":"connect","name":"Muse-XXXX"}`
- `{"cmd":"disconnect"}`

## Troubleshooting

### Port already in use

If bind fails on `8765`, run with another port:

- `.\scripts\run_native_bridge.ps1 -EnableLibMuse -Port 8766`

Then set API env to match:

- `MUSE_BRIDGE_PORT=8766`

### No visible output when double-clicking

This is a console app; run from PowerShell so startup errors are visible.
