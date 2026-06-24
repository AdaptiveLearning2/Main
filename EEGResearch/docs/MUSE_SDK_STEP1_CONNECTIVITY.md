# Step 1: Prove libMuse SDK connectivity (Windows)

Goal: run the **official sample app** from the SDK, pair your **Muse S Athena**, and confirm **live packets** (EEG or connection state) before writing the Python bridge.

SDK layout (this repo, not committed if you exclude vendor files):

- Solution: `libmuse_windows_8.0.5\examples\LibMuseExamples.sln`
- Primary UWP sample: `examples\GettingData\GettingData.vcxproj`
- Docs: `libmuse_windows_8.0.5\doc\index.html`

The `GettingData` project targets **UWP** (`Windows Store` app). The sample may use **MSVC toolset v145** (or `v143` in older copies)—see your `.vcxproj` under `PlatformToolset`. It uses minimum Windows **10.0.22621.0** and links `libmuse-uwp.lib` from `examples\lib\...` per platform.

## Prerequisites

1. **Windows 10/11** with **Bluetooth** (built-in or reliable USB dongle).
2. **Visual Studio 2022** with workloads:
   - **Desktop development with C++**
   - **Universal Windows Platform development** (UWP)
3. **Windows 10/11 SDK** matching or newer than the project minimum (project uses `WindowsTargetPlatformMinVersion` 10.0.22621.0).
4. **Muse S Athena** charged, worn correctly, and **not** connected to another app that holds the BLE session (phone app closed if needed).

## Verify SDK files before building

The linker needs import libraries under:

- `libmuse_windows_8.0.5\examples\lib\debug\x64\libmuse-uwp.lib` (example path for Debug x64)

If those folders are **missing** or **empty**, you have an incomplete SDK zip—re-download the official **LibMuse Windows** bundle from Interaxon / your Muse developer account and merge the `lib` (and any runtime DLLs they document) into the same layout.

## Build GettingData (recommended first proof)

1. Open `libmuse_windows_8.0.5\examples\LibMuseExamples.sln` in Visual Studio.
2. Set **Startup Project** to **GettingData**.
3. Select configuration **Debug** and platform **x64** (or **x86** if you only have Win32 libs—match the `lib` folder you actually have).
4. **Build → Build Solution** (or Build GettingData).
5. Fix any **NuGet restore** prompts (the sample may use CppWinRT packages under `examples\packages`).

Successful build produces a UWP app package you can deploy.

## Run on your PC

1. In Visual Studio, set the run target to **Local Machine** (typical for UWP).
2. Start debugging (**F5**).
3. When the app asks for Bluetooth, **allow** permissions.
4. Put the Muse in pairing/discoverable mode per Muse documentation.
5. In the app flow, **scan / connect** to your headband (exact UI labels follow the sample).

## What “success” looks like

Within a few minutes you should see **non-static** updates indicating a live link, for example:

- Muse appears in the device list and reaches **connected / streaming** state, or
- Numeric EEG-related values or packet counters **change** when you move, blink, or shift attention (depending on what the sample displays).

If the app only shows connection status, that is still a valid **step 1**—you have proven BLE + SDK path. Capture a short note: **SDK version folder name**, **build flavor** (Debug x64), and **what UI proved streaming**.

## Common failures

| Symptom | What to check |
|--------|------------------|
| **LNK1181 cannot open libmuse-uwp.lib** | `examples\lib\...` missing or wrong platform (Win32 vs x64). |
| **UWP deploy failed** | Developer mode, Windows SDK install, or project needs certificate / packaging (follow VS prompts). |
| **No device found** | Bluetooth off; Muse paired to phone; airplane mode; wrong radio. |
| **Connect then drops** | Power save on BT adapter; stay close to PC; close other Muse apps. |
| **BaseOutputPath / OutputPath is not set** (GettingData, `Debug` + `x64`) | See [Fix: OutputPath / BaseOutputPath](#fix-baseoutputpath--outputpath-is-not-set) below. |

## Fix: BaseOutputPath / OutputPath is not set

`open_libmuse_sdk_solution.ps1` only **opens** the solution; this error comes from **Visual Studio / MSBuild** when the project (usually **GettingData** UWP) does not load or evaluate correctly. Typical causes and fixes:

### 1) Install the right Visual Studio pieces

- **Workloads:** **Desktop development with C++** and **Universal Windows Platform development**
- **Individual components (VS Installer):** a **Windows 10/11 SDK** (e.g. **10.0.22621.0** or newer), and the **C++ UWP** / **C++ (v14x) Universal Windows Platform tools** if offered
- **MSVC toolset:** the project may reference **v145**. If you do not have v145, either install **MSVC v145** in the installer, **or** retarget the project (next step)

### 2) Retarget the UWP project (often fixes “OutputPath not set”)

1. In **Solution Explorer**, right-click **GettingData** → **Retarget projects** (or **Retarget to Windows 10/11**).
2. Choose a **Windows SDK version** that is actually installed (not “blank”).
3. Build again.

### 3) Match the platform the solution was designed for

The solution maps **x86** to **Win32** for UWP. Try:

- Toolbar: **Debug** + **x86** (not x64), then build **GettingData** again.

If you only have `libmuse-uwp.lib` under `examples\lib\debug\Win32\` and not `x64\`, you **must** use **x86/Win32**, not x64.

### 4) Align “Platform toolset” with what you have

1. Right-click **GettingData** → **Properties**
2. **Configuration Properties** → **General** → **Platform toolset**
3. If **v145** is missing from the dropdown, set it to **v143** (or the latest you have) → **Apply** → build again

### 5) Use the desktop sample instead (if UWP keeps failing)

The same solution includes **GettingData32** (Win32 + MFC, not UWP), which often has fewer platform/SDK headaches for a quick connectivity test:

- Set **GettingData32** as **Startup Project**
- Pick **Debug** and **x64** or **x86** to match `examples\lib\...` (that project links `libmuse-wrt.lib` per the SDK)

You still need **MFC** installed (**C++ MFC** in VS Installer) for **GettingData32**.

## After step 1 passes

Proceed to **Step 2**: fork the smallest sample into a **headless bridge** that forwards normalized samples to Python over localhost (see `docs/MUSE_WINDOWS_SDK.md`).

## Helper script

From the repo root:

```powershell
.\scripts\open_libmuse_sdk_solution.ps1
```

Opens `LibMuseExamples.sln` with the default app for `.sln` files (usually Visual Studio).
