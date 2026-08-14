#!/bin/bash
# AdaptiveLearning -- full stack launcher (macOS)
# Run from: /path/to/AdaptiveLearning
# Usage:
#   ./start.sh                     (simulator mode -- no headband needed)
#   ./start.sh --muse              (real Muse S headband -- libMuse is Windows-only)
#   ./start.sh --camera            (facial capture on camera index 0)
#   ./start.sh --camera --index 1  (a specific camera)
#   ./start.sh --gaze              (gaze landmarks too; implies --camera)
#   ./start.sh --gaze --no-emotion (gaze only -- no 35 MB FER+ model)

MUSE=false
CAMERA=false
CAMERA_INDEX=0
GAZE=false
NO_EMOTION=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --muse)       MUSE=true; shift ;;
        --camera)     CAMERA=true; shift ;;
        --index)      CAMERA_INDEX="$2"; shift 2 ;;
        --gaze)       GAZE=true; shift ;;
        --no-emotion) NO_EMOTION=true; shift ;;
        *)            echo "unknown option: $1"; exit 1 ;;
    esac
done

# Kept in step with start.ps1 deliberately: the same two rules, in the same
# order, for the same reasons. Gaze is a channel of the camera device, so every
# line that enables it lives inside the camera block -- without the promotion,
# `--gaze` alone would run to completion having silently done nothing.
if [ "$GAZE" = true ] && [ "$CAMERA" != true ]; then
    echo "--gaze implies --camera; enabling the camera too."
    CAMERA=true
fi

# The sidecar refuses a camera adapter with every channel off, because a camera
# that computes nothing is indistinguishable from a student out of shot. Read
# rather than assumed for the heart flag: this script never writes it, it is off
# by default, and it is hand-set when someone is testing that path -- so without
# the read this copy of the rule would refuse a heart-only camera the Python
# underneath would accept.
_heart_on=false
if [ -f "$(dirname "$0")/EEGResearch/.env" ] &&    grep -Eq '^[[:space:]]*FACE_HEART_ENABLED[[:space:]]*=[[:space:]]*true[[:space:]]*$'         "$(dirname "$0")/EEGResearch/.env"; then
    _heart_on=true
fi
if [ "$CAMERA" = true ] && [ "$NO_EMOTION" = true ] && [ "$GAZE" != true ]    && [ "$_heart_on" != true ]; then
    echo "--no-emotion without --gaze leaves the camera with nothing to measure."
    echo "  Add --gaze, or drop --camera."
    exit 1
fi

ROOT="$(cd "$(dirname "$0")" && pwd)"
EEG_DIR="$ROOT/EEGResearch"
BACKEND_DIR="$ROOT/Website/AdaptiveLearning/backend"
FRONTEND_DIR="$ROOT/Website/AdaptiveLearning/frontend"
TMP_DIR="/tmp/adaptivelearning"
mkdir -p "$TMP_DIR"

set_env_key() {
    # Rewrite a key in a .env, or append it if absent. Appending matters: a
    # first-time checkout has no FACE_* lines at all, and sed against a missing
    # key silently does nothing -- the flag would appear to work and change no
    # behaviour.
    # `sed -i ''` is BSD syntax. This script is macOS-only by design (libMuse
    # is Windows-only, so mac runs in sim), but under GNU sed the empty string
    # is read as the script argument and the failure is confusing rather than
    # obvious -- hence the note.
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
fi
echo -e "${GRAY}[1/5] Skipping native bridge (simulator mode on macOS)${NC}"

# Force simulator mode in EEG .env
EEG_ENV="$EEG_DIR/.env"
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

    # The `face` extra is optional, so a machine that has never installed it is
    # the normal case rather than a broken one. Checked here, at setup, because
    # the alternative is the sidecar starting cleanly and the camera failing
    # only when a lesson begins.
    if ! "$EEG_DIR/.venv/bin/python" -c "import cv2, onnxruntime" 2>/dev/null; then
        echo -e "  ${RED}ERROR: the 'face' extra is not installed in EEGResearch/.venv${NC}"
        echo -e "  ${YELLOW}Install it with:${NC}"
        echo -e "  ${YELLOW}  cd EEGResearch && source .venv/bin/activate && pip install -e '.[face]'${NC}"
        exit 1
    fi

    # MediaPipe is its own extra and is not in `.[face]`. Checked here for the
    # same reason cv2 is: `ensure_model` imports nothing heavy, so without this
    # setup succeeds, writes FACE_GAZE_ENABLED=true, and the channel then fails
    # on the first frame of a lesson as `landmarker_unavailable` --
    # indistinguishable from a missing model file.
    if [ "$GAZE" = true ]; then
        if ! "$EEG_DIR/.venv/bin/python" -c "import mediapipe" 2>/dev/null; then
            echo -e "  ${RED}ERROR: --gaze needs the 'gaze' extra, which is not installed${NC}"
            echo -e "  ${YELLOW}Install it with:${NC}"
            echo -e "  ${YELLOW}  cd EEGResearch && source .venv/bin/activate && pip install -e '.[face,gaze]'${NC}"
            exit 1
        fi
    fi

    # Fetch and verify the FER+ model now, not on the first frame. A 35 MB
    # download in front of a student's first session would look like the
    # feature being broken, and a checksum failure is an install problem that
    # should be seen here.
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

    # Same argument, for the 4 MB landmark bundle. The sidecar deliberately will
    # not fetch it itself -- a student's laptop must not reach the internet when
    # a lesson opens a camera.
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

    # macOS has no libMuse, so the headband half is always sim here.
    set_env_key "$EEG_ENV" "EEG_DEVICES" "default:sim,camera:face@$CAMERA_INDEX"
    set_env_key "$EEG_ENV" "FACE_ENABLED" "true"
    set_env_key "$EEG_ENV" "FACE_CAMERA_INDEX" "$CAMERA_INDEX"
    # Every FACE_* key on both branches, FACE_EMOTION_ENABLED included: its
    # config default is `true`, so leaving it unwritten put a third of the
    # camera's configuration in a Python default rather than in the .env a
    # reader checks.
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
    echo -e "  ${GRAY}EEG_DEVICES = default:sim,camera:face@$CAMERA_INDEX${NC}"
else
    set_env_key "$EEG_ENV" "FACE_ENABLED" "false"
    set_env_key "$EEG_ENV" "FACE_GAZE_ENABLED" "false"
    set_env_key "$EEG_ENV" "FACE_EMOTION_ENABLED" "false"

    # Remove only the camera entry this script writes, leaving any other devices
    # alone. Blanking EEG_DEVICES outright would silently destroy a hand-written
    # multi-headband registry -- "station1:muse@8765,station2:muse@8766" -- in a
    # file the docs tell people to edit. A stale camera entry still has to go, or
    # a later plain run keeps opening the webcam.
    if [ -f "$EEG_ENV" ] && grep -q '^EEG_DEVICES=' "$EEG_ENV"; then
        current="$(grep '^EEG_DEVICES=' "$EEG_ENV" | head -1 | cut -d= -f2-)"
        kept=""
        IFS=',' read -ra entries <<< "$current"
        for entry in "${entries[@]}"; do
            case "$entry" in
                ""|*:face|*:face@*) ;;
                *) kept="${kept:+$kept,}$entry" ;;
            esac
        done
        set_env_key "$EEG_ENV" "EEG_DEVICES" "$kept"
    fi
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
