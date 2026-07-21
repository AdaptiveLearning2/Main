# Native Muse Bridge

Windows C++ service that connects to the Muse S headband via the libMuse SDK and streams normalized JSON over localhost TCP for the Python backend (EEGResearch).

Default endpoint: `127.0.0.1:8765`

## What it sends

Every JSON line is one of two kinds:

**EEG frame** (220Hz when connected, `PRESET_21`):
```json
{"kind":"eeg","mono_ts_ms":1735689600123,"tp9":714.2,"af7":698.5,"af8":702.1,"tp10":711.8,"bridge_mode":"libmuse","muse_connected":true,"muse_discovered":true,"bluetooth_enabled":true,"connection_state":1,"muse_devices":["MuseS-0FFC"],"active_muse_name":"MuseS-0FFC","firmware_version":"","delta":0.0,"theta":0.0,"alpha":0.0,"beta":0.0,"gamma":0.0}
```

**Status heartbeat** (every 200ms when no EEG, or on command response):
```json
{"kind":"status","bridge_mode":"libmuse","muse_connected":false,"muse_discovered":true,"bluetooth_enabled":true,"connection_state":3,"muse_devices":["MuseS-0FFC"],"active_muse_name":"","firmware_version":"","delta":0.0,"theta":0.0,"alpha":0.0,"beta":0.0,"gamma":0.0}
```

`bluetooth_enabled` reflects the Windows Bluetooth radio's on/off state (via `winrt::Windows::Devices::Radios::Radio`, the same check `GettingData32`/`GettingData` perform), re-queried on startup and on every `refresh` command. It defaults to `true` when the radio state can't be determined, so it's a diagnostic hint, not a gate on scanning.

Band power fields (`delta`–`gamma`) are non-zero once the headband has been streaming long enough for libMuse to compute them.

## Build and Run

### Via start.ps1 (recommended)

```powershell
cd C:\AdaptiveLearning
.\start.ps1 -Muse   # builds if missing, then launches bridge + full stack
```

### Build only (no run)

```powershell
cd C:\AdaptiveLearning\EEGResearch
.\scripts\run_native_bridge.ps1 -EnableLibMuse -BuildOnly
```

### Build and run standalone

```powershell
cd C:\AdaptiveLearning\EEGResearch
.\scripts\run_native_bridge.ps1 -EnableLibMuse
```

### Manual CMake

```powershell
$sdk = "C:\AdaptiveLearning\EEGResearch\libmuse_windows_8.0.5"
cmake -S native_bridge -B native_bridge/build -G "Visual Studio 18 2026" -A x64 -DENABLE_LIBMUSE=ON -DLIBMUSE_SDK_DIR="$sdk"
cmake --build native_bridge/build --config Release
# Copy runtime DLL next to exe
Copy-Item "$sdk\examples\lib\release\x64\libmuse.dll" native_bridge\build\Release\
.\native_bridge\build\Release\muse_native_bridge.exe
```

## Bridge Commands

Send newline-delimited JSON to the bridge from the Python adapter:

| Command | Effect |
|---------|--------|
| `{"cmd":"refresh"}` | Stop + restart BLE scan; populates `muse_devices` list |
| `{"cmd":"connect","name":"MuseS-0FFC"}` | Connect to named device |
| `{"cmd":"disconnect"}` | Disconnect current headband |

## Connection Protocol

libMuse runs this sequence on `run_asynchronously()`:

1. Version → VersionCheck → Halt → Preset (p21) → Status → StartStreaming

If `StartStreaming` returns `rc=3` (BadStateError: "headband was already streaming"), the headband's BLE state is stuck from a previous session that didn't disconnect cleanly. **Fix: power cycle the headband** (hold button until two beeps, wait 10s).

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `bind() failed on 127.0.0.1:8765` | Another process already on the port | `Stop-Process -Name muse_native_bridge -Force` or use `-Port 8766` |
| `StartStreaming rc=3` (BadStateError) | Headband stuck in streaming state | Power cycle headband |
| Band powers all 0 | Python bridge on port instead of C++ bridge | Stop Python bridge, ensure exe is running |
| No output when double-clicking | Console app | Run from PowerShell terminal |
| `muse_devices` stays empty, `bluetooth_enabled:false` in status | Windows Bluetooth radio is off | Turn on Bluetooth in Windows Settings, then send `{"cmd":"refresh"}` again |

## Runtime Requirements

- `libmuse.dll` must be in the same directory as `muse_native_bridge.exe`. `start.ps1` copies it automatically.
- Windows 10/11, Bluetooth adapter enabled.
- Muse S headband firmware 3.1.x (Athena hardware).

## Build Requirements (ENABLE_LIBMUSE builds)

- A Windows SDK and Visual Studio with the **Desktop development with C++** workload.
- C++/WinRT (used for the `bluetooth_enabled` radio-state check) **must be version-matched to libMuse**. `libmuse-wrt.lib` was built against C++/WinRT `2.0.240405.15` — the version pinned in `examples/GettingData32/packages.config` — and C++/WinRT embeds `#pragma detect_mismatch` guards on its version, so mixing versions is a hard link error, not a warning:

  ```
  error LNK2038: mismatch detected for 'C++/WinRT version':
    value '2.0.240405.15' doesn't match value '2.0.250303.1' in main.obj
  fatal error LNK1319: 12 mismatches detected
  ```

  The in-box C++/WinRT headers that ship with a recent Windows SDK are **newer than this** and will fail exactly that way. CMake therefore generates a version-matched projection at configure time using the `cppwinrt.exe` vendored with the libMuse SDK (`examples/packages/Microsoft.Windows.CppWinRT.2.0.240405.15/bin/`) and puts it ahead of the system include path. Nothing to install — but the SDK's `examples/packages/` directory does need to be present, and if libMuse is ever upgraded, this pinned version has to move with it.
