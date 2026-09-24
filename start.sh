#!/bin/bash
# AdaptiveLearning -- full stack launcher (macOS). Run from the repo root.
# Usage: ./start.sh [--muse [--optics]] [--camera [--index N]] [--gaze [--no-emotion]]
#   no flags = simulator; --muse = real Muse S (libMuse is Windows-only); --gaze implies --camera;
#   --no-emotion skips the 35 MB FER+ model; --optics = headband PPG -> heart rate (Windows only).
#   --local-calm is refused: this launcher always runs the simulator (use start.ps1 -Muse -LocalCalm).

MUSE=false
CAMERA=false
CAMERA_INDEX=0
GAZE=false
NO_EMOTION=false
OPTICS=false
OPTICS_PRESET=""
LOCAL_CALM=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --muse)       MUSE=true; shift ;;
        --camera)     CAMERA=true; shift ;;
        --index)      CAMERA_INDEX="$2"; shift 2 ;;
        --gaze)       GAZE=true; shift ;;
        --no-emotion) NO_EMOTION=true; shift ;;
        --optics)     OPTICS=true; shift ;;
        --preset)     OPTICS_PRESET="$2"; shift 2 ;;
        --local-calm) LOCAL_CALM=true; shift ;;
        *)            echo "unknown option: $1"; exit 1 ;;
    esac
done

# Guards kept in step with start.ps1, same order. Gaze is enabled inside the camera block, so promote.
if [ "$GAZE" = true ] && [ "$CAMERA" != true ]; then
    echo "--gaze implies --camera; enabling the camera too."
    CAMERA=true
fi

# The sidecar refuses a camera with every channel off. Reads the hand-set
# FACE_HEART_ENABLED (never written here), the adapter's third channel.
_heart_on=false
_env_probe="$(dirname "$0")/EEGResearch/.env"
# `-i` for parity with PowerShell's case-insensitive -match.
if [ -f "$_env_probe" ] && grep -Eqi '^[[:space:]]*FACE_HEART_ENABLED[[:space:]]*=[[:space:]]*true[[:space:]]*$' "$_env_probe"; then
    _heart_on=true
fi
if [ "$CAMERA" = true ] && [ "$NO_EMOTION" = true ] && [ "$GAZE" != true ]    && [ "$_heart_on" != true ]; then
    echo "--no-emotion without --gaze leaves the camera with nothing to measure."
    echo "  Add --gaze, or drop --camera."
    exit 1
fi

# The optics guards, as start.ps1, so both scripts agree on a valid invocation.
if [ "$OPTICS" = true ] && [ "$MUSE" != true ]; then
    echo "--optics needs --muse: the simulator has no optical channel to enable."
    echo "  Add --muse, or drop --optics."
    exit 1
fi
# Refused outright: this launcher always forces EEG_SOURCE=sim, where local calm is a placeholder.
if [ "$LOCAL_CALM" = true ]; then
    echo "--local-calm needs a headband, and libMuse is Windows-only: this launcher always runs the simulator."
    echo "  Use start.ps1 -Muse -LocalCalm on Windows, or drop --local-calm."
    exit 1
fi
if [ -n "$OPTICS_PRESET" ] && [ "$OPTICS" != true ]; then
    echo "--preset does nothing without --optics."
    echo "  Add --optics, or drop --preset."
    exit 1
fi
# The bridge would fall back to 1035 and say so only on its own stderr.
if [ -n "$OPTICS_PRESET" ] && ! echo "$OPTICS_PRESET" | grep -Eq '^103[1-6]$'; then
    echo "--preset '$OPTICS_PRESET' is not a preset (expected 1031-1036)."
    echo "  16 CH: 1031/1032   8 CH: 1033/1034   4 CH: 1035/1036   (odd = low power)"
    exit 1
fi
if [ "$OPTICS_PRESET" = "1031" ] || [ "$OPTICS_PRESET" = "1032" ]; then
    echo "  WARNING: PRESET_$OPTICS_PRESET is 16 CH optics. Measured on hardware:"
    echo "  the BLE link drops within ~20s and electrode contact collapses to"
    echo "  [4,4,4,4] -- it takes EEG down with it. 1033-1036 held for minutes."
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
EEG_DIR="$ROOT/EEGResearch"
BACKEND_DIR="$ROOT/Website/AdaptiveLearning/backend"
FRONTEND_DIR="$ROOT/Website/AdaptiveLearning/frontend"
TMP_DIR="/tmp/adaptivelearning"
mkdir -p "$TMP_DIR"

update_device_registry() {
    # As start.ps1's Update-DeviceRegistry: drop the camera entry, re-point an existing
    # `default:` to `$2`, keep other stations; `$3` (camera) is appended with `default:` ensured.
    # `$4` = "check": validate and write nothing.
    local path="$1" headband="$2" camera="${3:-}" mode="${4:-apply}" current kept entry has_default=false
    [ -f "$path" ] || return 0
    if ! grep -q '^EEG_DEVICES=' "$path"; then
        [ -n "$camera" ] && [ "$mode" != check ] && set_env_key "$path" "EEG_DEVICES" "$headband,$camera"
        return 0
    fi
    current="$(grep '^EEG_DEVICES=' "$path" | tail -1 | cut -d= -f2-)"
    IFS=',' read -ra entries <<< "$current"
    # A named station on the headband's address is unresolvable here: write nothing,
    # return 1. sim entries are exempt. Same rule as start.ps1.
    for entry in "${entries[@]}"; do
        case "$entry" in
            ""|*:face|*:face@*|default:*) ;;
            *) if [ "${headband#*:}" != sim ] && [ "${entry#*:}" = "${headband#*:}" ]; then
                   echo "EEG_DEVICES names $entry on the bridge address this run's headband needs (${headband#*:})."
                   echo "  The backend drives the 'default' device, and two muse devices cannot share a bridge port."
                   echo "  Give that station its own port, remove it, or run without --muse. Neither .env was changed."
                   return 1
               fi ;;
        esac
    done
    [ "$mode" = check ] && return 0
    kept=""
    for entry in "${entries[@]}"; do
        case "$entry" in
            ""|*:face|*:face@*) ;;
            default:*) kept="${kept:+$kept,}$headband"; has_default=true ;;
            *) kept="${kept:+$kept,}$entry" ;;
        esac
    done
    if [ -n "$camera" ]; then
        [ "$has_default" = true ] || kept="$headband${kept:+,$kept}"
        kept="${kept:+$kept,}$camera"
    fi
    set_env_key "$path" "EEG_DEVICES" "$kept"
    return 0
}

set_env_key() {
    # Rewrite a key in a .env, or append it if absent.
    # `sed -i ''` is BSD syntax: this script is macOS-only, and GNU sed misreads it.
    local path="$1" key="$2" value="$3"
    [ -f "$path" ] || return 0
    if grep -q "^${key}=" "$path"; then
        sed -i '' "s|^${key}=.*|${key}=${value}|" "$path"
    else
        printf '%s=%s\n' "$key" "$value" >> "$path"
    fi
}

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
GRAY='\033[0;90m'
RED='\033[0;31m'
NC='\033[0m'

# Prefer python3, fall back to python
PYTHON=$(command -v python3 || command -v python)
if [ -z "$PYTHON" ]; then
    echo "ERROR: Python not found. Install Python 3.11+ and retry."
    exit 1
fi

check_venv() {
    local dir="$1"
    local mode="$2"   # "editable" or "requirements"
    local need_rebuild=false

    if [ ! -f "$dir/.venv/bin/activate" ]; then
        echo -e "  ${YELLOW}No venv found -- creating one in $dir...${NC}"
        need_rebuild=true
    else
        local system_ver
        local venv_ver
        system_ver="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
        venv_ver="$("$dir/.venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)"
        if [ "$venv_ver" != "$system_ver" ]; then
            echo -e "  ${YELLOW}Venv Python ($venv_ver) does not match system Python ($system_ver) -- rebuilding...${NC}"
            rm -rf "$dir/.venv"
            need_rebuild=true
        fi
    fi

    if [ "$need_rebuild" = true ]; then
        pushd "$dir" > /dev/null
        "$PYTHON" -m venv .venv
        if [ "$mode" = "editable" ]; then
            .venv/bin/pip install -e . -q
        else
            .venv/bin/pip install -r requirements.txt -q
        fi
        popd > /dev/null
    fi
}

# Write a shell script to a temp file and open it in a new Terminal window.
# This avoids all AppleScript quoting issues with && and spaces.
open_terminal() {
    local title="$1"
    local workdir="$2"
    local command="$3"
    local script_file="$TMP_DIR/$(echo "$title" | tr ' :' '__').sh"

    cat > "$script_file" <<SCRIPT
#!/bin/bash
echo "=== $title ==="
cd "$workdir"
$command
echo ""
echo "Process ended. Press Enter to close."
read
SCRIPT
    chmod +x "$script_file"

    osascript \
        -e "tell application \"Terminal\"" \
        -e "  activate" \
        -e "  do script \"$script_file\"" \
        -e "end tell" > /dev/null 2>&1

    echo -e "  ${GREEN}Started: $title${NC}"
    sleep 1
}

echo ""
echo -e "${CYAN}========================================${NC}"
echo -e "${CYAN}  AdaptiveLearning -- Starting Stack${NC}"
echo -e "${CYAN}========================================${NC}"
echo ""

# 1. Muse bridge
if [ "$MUSE" = true ]; then
    echo -e "${CYAN}[1/5] Native Muse Bridge${NC}"
    echo -e "  ${YELLOW}libMuse SDK is Windows-only -- switching to simulator mode.${NC}"
    # --optics passes every guard yet configures a bridge that cannot run here.
    if [ "$OPTICS" = true ]; then
        echo -e "  ${YELLOW}--optics configures the bridge, so it has no effect here either.${NC}"
    fi
fi
echo -e "${GRAY}[1/5] Skipping native bridge (simulator mode on macOS)${NC}"

# Force simulator mode in EEG .env
EEG_ENV="$EEG_DIR/.env"
BACKEND_ENV="$BACKEND_DIR/.env"
FRONTEND_ENV="$FRONTEND_DIR/.env"

# Validate the registry before any .env write; apply on each branch after provisioning.
_camera_entry=""
[ "$CAMERA" = true ] && _camera_entry="camera:face@$CAMERA_INDEX"
update_device_registry "$EEG_ENV" "default:sim" "$_camera_entry" check || exit 1

if [ -f "$EEG_ENV" ]; then
    if grep -q "^EEG_SOURCE=muse" "$EEG_ENV" 2>/dev/null; then
        sed -i '' 's/^EEG_SOURCE=muse/EEG_SOURCE=sim/' "$EEG_ENV"
        echo -e "  ${YELLOW}Set EEG_SOURCE=sim in EEGResearch/.env${NC}"
    fi
fi

# 1b. Camera
EMOTION_MODEL="$EEG_DIR/models/emotion-ferplus-8.onnx"
LANDMARK_MODEL="$EEG_DIR/models/face_landmarker.task"
if [ "$CAMERA" = true ]; then
    echo -e "${CYAN}[1/5] Camera (index $CAMERA_INDEX)${NC}"

    if [ ! -x "$EEG_DIR/.venv/bin/python" ]; then
        echo -e "  ${RED}ERROR: EEGResearch/.venv not found -- create it first.${NC}"
        exit 1
    fi

    # The optional `face` extra is checked at setup; one probe per module, as start.ps1.
    missing=""
    for mod in cv2 onnxruntime; do
        if ! "$EEG_DIR/.venv/bin/python" -c "import $mod" 2>/dev/null; then
            missing="$missing $mod"
        fi
    done
    if [ -n "$missing" ]; then
        # '.[face,gaze]' under --gaze, or they fail again at the mediapipe check.
        extra=".[face]"
        [ "$GAZE" = true ] && extra=".[face,gaze]"
        echo -e "  ${RED}ERROR: the 'face' extra is not installed in EEGResearch/.venv${NC}"
        echo -e "  ${RED}  could not import:${missing}${NC}"
        echo -e "  ${YELLOW}Install it with:${NC}"
        echo -e "  ${YELLOW}  cd EEGResearch && source .venv/bin/activate && pip install -e '$extra'${NC}"
        echo -e "  ${YELLOW}If cv2 is the one failing and pip says it is already installed,${NC}"
        echo -e "  ${YELLOW}uninstall opencv-python first -- it and opencv-contrib-python${NC}"
        echo -e "  ${YELLOW}both provide cv2, and whichever landed last owns the import.${NC}"
        exit 1
    fi

    # MediaPipe is its own extra; without this check gaze dies on the first frame as `landmarker_unavailable`.
    if [ "$GAZE" = true ]; then
        if ! "$EEG_DIR/.venv/bin/python" -c "import mediapipe" 2>/dev/null; then
            echo -e "  ${RED}ERROR: --gaze needs the 'gaze' extra, which is not installed${NC}"
            echo -e "  ${YELLOW}Install it with:${NC}"
            echo -e "  ${YELLOW}  cd EEGResearch && source .venv/bin/activate && pip install -e '.[face,gaze]'${NC}"
            exit 1
        fi
    fi

    # Fetch and verify the 35 MB FER+ model at setup, not on the first frame.
    if [ "$NO_EMOTION" != true ]; then
        echo -e "  ${GRAY}Checking emotion model...${NC}"
        if ! (cd "$EEG_DIR" && ./.venv/bin/python -c "
from pathlib import Path
from src.app.services.face_emotion import ensure_model
ensure_model(Path('$EMOTION_MODEL'))
"); then
            echo -e "  ${RED}ERROR: emotion model could not be fetched or failed verification${NC}"
            exit 1
        fi
    fi

    # Likewise the 4 MB landmark bundle; the sidecar never fetches it itself.
    if [ "$GAZE" = true ]; then
        echo -e "  ${GRAY}Checking face landmark model...${NC}"
        if ! (cd "$EEG_DIR" && ./.venv/bin/python -c "
from src.app.services.face_landmarks import ensure_model
ensure_model('$LANDMARK_MODEL')
"); then
            echo -e "  ${RED}ERROR: face landmark model could not be fetched or failed verification${NC}"
            exit 1
        fi
    fi

    # Applied only after provisioning succeeded.
    update_device_registry "$EEG_ENV" "default:sim" "$_camera_entry"
    set_env_key "$EEG_ENV" "FACE_ENABLED" "true"
    set_env_key "$EEG_ENV" "FACE_CAMERA_INDEX" "$CAMERA_INDEX"
    # Every FACE_* key on both branches; FACE_EMOTION_ENABLED's config default is `true`.
    if [ "$GAZE" = true ]; then
        set_env_key "$EEG_ENV" "FACE_GAZE_ENABLED" "true"
    else
        set_env_key "$EEG_ENV" "FACE_GAZE_ENABLED" "false"
    fi
    if [ "$NO_EMOTION" = true ]; then
        set_env_key "$EEG_ENV" "FACE_EMOTION_ENABLED" "false"
    else
        set_env_key "$EEG_ENV" "FACE_EMOTION_ENABLED" "true"
    fi
    set_env_key "$EEG_ENV" "FACE_LANDMARK_MODEL_PATH" "$LANDMARK_MODEL"
    # Push: the camera's only writer is /api/signals/face.
    set_env_key "$EEG_ENV" "PUSH_ENABLED" "true"
    # Always sdk, on both branches: --local-calm is refused above.
    set_env_key "$EEG_ENV" "EEG_SPECTRUM_SOURCE" "sdk"
    set_env_key "$EEG_ENV" "BACKEND_URL" "http://127.0.0.1:8000"
    set_env_key "$BACKEND_ENV" "INGEST_MODE" "push"
    # Without the token every browser call to the sidecar 401s. `-f` first: a fresh checkout has no file.
    API_TOKEN_VALUE=""
    [ -f "$EEG_ENV" ] && API_TOKEN_VALUE="$(sed -n 's/^API_TOKEN=//p' "$EEG_ENV" | tail -1)"
    if [ -n "$API_TOKEN_VALUE" ]; then
        set_env_key "$FRONTEND_ENV" "VITE_EEG_LOCAL_TOKEN" "$API_TOKEN_VALUE"
    else
        echo -e "  ${YELLOW}No API_TOKEN in $EEG_ENV yet -- VITE_EEG_LOCAL_TOKEN not set.${NC}"
        echo -e "  ${YELLOW}The browser will 401 against the sidecar. Re-run this script once it has started.${NC}"
    fi
    # Read back, since the registry is composed onto existing stations.
    echo -e "  ${GRAY}$(grep '^EEG_DEVICES=' "$EEG_ENV" | tail -1)${NC}"
else
    set_env_key "$EEG_ENV" "FACE_ENABLED" "false"
    set_env_key "$EEG_ENV" "FACE_GAZE_ENABLED" "false"
    set_env_key "$EEG_ENV" "FACE_EMOTION_ENABLED" "false"
    # Back to pull, written on both branches so a stale push cannot disable the poller.
    set_env_key "$EEG_ENV" "PUSH_ENABLED" "false"
    set_env_key "$BACKEND_ENV" "INGEST_MODE" "pull"
    set_env_key "$EEG_ENV" "EEG_SPECTRUM_SOURCE" "sdk"

    # Drops the camera entry and re-points the headband entry.
    update_device_registry "$EEG_ENV" "default:sim"
fi

# 2. Ollama
echo -e "${CYAN}[2/5] Ollama (LLM)${NC}"
if ! command -v ollama &> /dev/null; then
    echo -e "  ${YELLOW}Ollama not found. Install from https://ollama.com then re-run.${NC}"
else
    if ! pgrep -x "ollama" > /dev/null; then
        open_terminal "Ollama" "$ROOT" "ollama serve"
        sleep 3
    else
        echo -e "  ${GRAY}Ollama already running -- skipping.${NC}"
    fi
    if ! ollama list 2>/dev/null | grep -q "llama3.1:8b"; then
        echo -e "  ${YELLOW}Pulling llama3.1:8b (this may take a while)...${NC}"
        ollama pull llama3.1:8b
    fi
fi

# 3. EEGResearch backend
echo -e "${CYAN}[3/5] EEGResearch backend (port 8001)${NC}"
check_venv "$EEG_DIR" "editable"
open_terminal "EEG Backend :8001" "$EEG_DIR" \
    "source .venv/bin/activate && uvicorn src.app.main:app --host 127.0.0.1 --port 8001 --reload"
sleep 2

# 4. Website backend
echo -e "${CYAN}[4/5] Website backend (port 8000)${NC}"
check_venv "$BACKEND_DIR" "requirements"
open_terminal "Website Backend :8000" "$BACKEND_DIR" \
    "source .venv/bin/activate && uvicorn main:app --reload --port 8000"
sleep 2

# 5. Frontend
echo -e "${CYAN}[5/5] Frontend (Vite)${NC}"
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    echo -e "  ${YELLOW}node_modules not found -- running npm install...${NC}"
    pushd "$FRONTEND_DIR" > /dev/null
    npm install
    popd > /dev/null
fi
open_terminal "Frontend :5173" "$FRONTEND_DIR" "npm run dev"

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  All services started!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  Frontend:    ${GREEN}http://localhost:5173${NC}"
echo -e "  Website API: ${GREEN}http://localhost:8000${NC}"
echo -e "  EEG API:     ${GREEN}http://localhost:8001${NC}"
echo ""
echo -e "  ${YELLOW}EEG is running in simulator mode on macOS.${NC}"
echo ""
