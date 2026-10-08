# The student kit

The bridge and the sidecar on a student's computer, installed by one installer and kept up to date by a scheduled
task. How to build, publish and install it is in `DEVELOPER_SETUP_WINDOWS.md`; this is why it is built that way.

## It runs the bridge and the sidecar with no toolchain

`EEGResearch/src/kit` is the launcher frozen into `AdaptiveLearningSensors.exe`; `EEGResearch/scripts/build_student_kit.ps1`
builds it into two Inno Setup installers (`EEGResearch/installer`): Setup, and Update, the same code with no settings. It is `start.ps1 -Hosted -Muse -Optics -Camera -Gaze`
read from `kit.json`, which `src/kit/config.py` refuses for exactly the reasons `Test-HostedArgs` refuses its arguments:
`tests/launcher_cases.py` is the one table both are tested against.

- **The environment, never a `.env`.** It removes every `Settings` name and every `MUSE_*` name before setting its own,
  so nothing from the session or a profile reaches either process, and the bridge never sees the tokens. The sidecar's
  `Settings` read no `.env` at all, so one left in the data folder changes nothing. `ADMIN_TOKEN` is fresh on every
  start: under push, every endpoint the page calls takes the learner token.
- **One copy per machine**, `Global\AdaptiveLearningSensors`, so a second person signed in at once gets no sensors. The
  sidecar drops a connection from another Windows session before reading a byte of it, since a page's session start
  carries its student's login token; it checks who owns the connection's other end. Both children are in a
  kill-on-close job, so neither outlives the launcher however that ends. `--stop` sets
  `Global\AdaptiveLearningSensorsStop` and returns only once the copy has exited; the installer waits on that.
- **Two supervised children.** The sidecar runs as the launcher's own exe with `--sidecar`, so a crash in camera code
  is restarted like a bridge crash; it watches the same stop event and shuts itself down, flushing its push client,
  with 15 s before it is ended, which `sensors.log` records: at most 2 s of uvicorn waiting for open connections, then
  the push client's 10 s `SHUTDOWN_BUDGET`, then the exit. `--stop` waits 20 s, since past that the installer kills
  the copy. The restart policy is `run_bridge_supervised.ps1`'s (`docs/signals.md`) in Python, stopping on 0 and on 78. Past its restart budget it
  retries every 300 s rather than giving up, since nobody is watching. Each run logs to its own file; the newest ten
  are kept. matplotlib's font list lives in the data folder: PyInstaller gives each process a new temp folder, so
  mediapipe's import rebuilt it at every lesson's first camera frame.
- **`--self-test`** runs before every installer is built: the models on a real portrait, the sidecar on port 0, and
  the bridge started, authenticated and required to answer `bridge_mode: libmuse` with its C++ runtime loaded from
  `bridge\` itself. A blank frame cannot tell a working model from one that never detects, and a launch that works
  cannot show where the runtime came from: the bridge inherits the frozen launcher's DLL folder, `_internal\`, ahead of
  System32. The four runtime files are the only names the two share, and `bridge\` is searched first. The `update`
  check verifies a signed test feed, refuses it altered, and fails a build with no keys to verify a real one.

## The kit updates itself

`src/kit/update.py`, run by the task `AdaptiveLearning Sensors Update`, which an all-users install into
`%ProgramFiles%\AdaptiveLearning Sensors` registers when *Keep the sensors up to date* is ticked (the default; IT
unticks it with `/MERGETASKS="!autoupdate"`). SYSTEM; at boot, at any sign-in, and hourly at a minute drawn at install;
one run at a time, on battery too, an hour at most. Its five actions run in order whatever the one before exited with:

| # | Action | What it does |
| --- | --- | --- |
| 1 | `--update` | Blocks the last install if it did not stick, reads the feed, stages what is due; when quiet, hard-links `apply.exe` and `rollback.exe` and writes `attempt.json` |
| 2 | `updates\apply.exe` | The Update installer, silently. Absent, the action fails and nothing happens |
| 3 | `--after-update` | Only after an install this run, by the version now installed: the self-test. A pass removes `rollback.exe`; a fail blocks the version |
| 4 | `updates\rollback.exe` | The way back, still there after a failed check, or when the new version could not even start |
| 5 | `--start-sessions` | Only after an install this run: the sensors again in each session at the keyboard, as its user |

- **Two layers, two jobs.** The gate's download key decides who can download; the feed's Ed25519 signature, and the
  SHA-256 and size it carries, decide what is installed. A leaked key lets someone download the kit, never makes a
  computer install anything. The signature covers `FEED_PREFIX` and the manifest's bytes; `update_keys.PUBLIC_KEYS`
  holds the everyday key and an offline recovery key, and either can sign.
- **Only when nobody is mid-lesson.** An install stops the sensors, so it waits until nobody is signed in, or everyone
  signed in under 3 minutes ago. A session Windows will not describe counts as a lesson. The launcher stands aside
  while `apply.exe` exists and `attempt.json` is under 15 minutes old; action 5 starts it after.
- **Never a downgrade from the feed.** Only a newer version is staged. The one way back is the local rollback to the
  manifest's `rollback_to`, which the publish script takes from what `latest.json` named before, and which is at least
  0.2.0, the first version with `--update`: a kit rolled back below it could never update again. A feed naming no way
  back is never installed.
- **A version that failed here is never retried.** Action 3 blocks it on a failed check; action 1 blocks any
  `attempt.json` version that `version.txt` does not now read, which covers a version whose Python cannot start, where
  action 3 never ran. It stays blocked until a newer one is published.
- **Rollout.** The signed `rollout` is a percentage; each computer's bucket is a hash of its `MachineGuid`, so the
  same computers go first. One whose id cannot be read takes the last bucket.
- **Settings in `update.json`, not `kit.json`.** `config.check` refuses a name it does not know, and after a rollback
  an older launcher reads the newer kit's files; `update.json`'s reader ignores unknown names for the same reason. An
  Update installer carries neither file: the build copies the kit without them, `kit_build.py scan` fails on either
  secret in ASCII or UTF-16, and it refuses (exit 7) where no `kit.json` is installed. `version.txt` is the installed
  version, since `kit.json`'s goes stale.
- **SYSTEM runs nothing a student can write.** Every update mode exits 2, writing nothing, unless it is SYSTEM (an
  admin, to register) running from exactly `%ProgramFiles%\AdaptiveLearning Sensors`. PATH is cut to Windows' folders
  before anything else loads. Redirects are refused, since following one sends the key to another host, and a download
  stops one byte past its size. A run of the task never replaces the task it is running in.
- **What a computer did** is in `{app}\updates\status.json`, which users can read, beside `update.log`, `apply.log`,
  `rollback.log` and the last self-test's `selftest.json`.
- **The gate** is `installer/update_gate`, a Worker on the private bucket: `GET`/`HEAD` on the two feeds and on exact
  Update installer names, one 401 for a missing or wrong key before any route, feeds `no-store`.

None of this shows a version that passes its self-test and still fails on a real headband or camera; canary, then a
gradual rollout, is what catches that.
