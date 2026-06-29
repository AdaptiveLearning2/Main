#!/bin/bash
# AdaptiveLearning -- full stack launcher (macOS)
# Run from: /path/to/AdaptiveLearning
# Usage:
#   ./start.sh          (simulator mode -- no headband needed)
#   ./start.sh --muse   (real Muse S headband -- note: libMuse is Windows-only)

MUSE=false
for arg in "$@"; do
    [[ "$arg" == "--muse" ]] && MUSE=true
done

ROOT="$(cd "$(dirname "$0")" && pwd)"
EEG_DIR="$ROOT/EEGResearch"
BACKEND_DIR="$ROOT/Website/AdaptiveLearning/backend"
FRONTEND_DIR="$ROOT/Website/AdaptiveLearning/frontend"
TMP_DIR="/tmp/adaptivelearning"
mkdir -p "$TMP_DIR"

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
GRAY='\033[0;90m'
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
