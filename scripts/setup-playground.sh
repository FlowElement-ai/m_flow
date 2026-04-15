#!/usr/bin/env bash
# =============================================================================
# M-flow Playground Setup
#
# One-command setup for the Playground with face recognition.
# Clones fanjing-face-recognition, downloads models, configures .env.
#
# Usage:
#   ./scripts/setup-playground.sh           # interactive setup
#   ./scripts/setup-playground.sh --yes     # non-interactive (accept defaults)
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
FANJING_DIR="$(dirname "$PROJECT_DIR")/fanjing-face-recognition"
FANJING_REPO="https://github.com/FlowElement-ai/fanjing-face-recognition.git"

AUTO_YES=false
if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
    AUTO_YES=true
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

confirm() {
    if $AUTO_YES; then return 0; fi
    local msg="$1"
    read -rp "$(echo -e "${BOLD}$msg [Y/n]${NC} ")" answer
    [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

detect_os() {
    case "$(uname -s)" in
        Darwin*) echo "macos" ;;
        Linux*)  echo "linux" ;;
        MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
        *)       echo "unknown" ;;
    esac
}

check_command() {
    command -v "$1" >/dev/null 2>&1
}

# ── Step 0: Prerequisites ───────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  M-flow Playground Setup${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

OS=$(detect_os)
info "Detected OS: $OS"

for cmd in git python3 curl; do
    if ! check_command "$cmd"; then
        fail "$cmd is required but not found. Please install it first."
    fi
done
ok "Prerequisites: git, python3, curl"

# ── Step 1: Clone fanjing-face-recognition ──────────────────────────────────

echo ""
info "Step 1/4: fanjing-face-recognition repository"

if [[ -d "$FANJING_DIR" ]]; then
    ok "Already cloned at $FANJING_DIR"
    if [[ -d "$FANJING_DIR/.git" ]]; then
        if confirm "  Pull latest changes?"; then
            git -C "$FANJING_DIR" pull --ff-only 2>/dev/null || warn "Pull failed (not on a tracking branch?). Continuing with existing code."
        fi
    fi
else
    info "Cloning $FANJING_REPO"
    info "Target: $FANJING_DIR"
    if ! git clone "$FANJING_REPO" "$FANJING_DIR"; then
        fail "Clone failed. Check your network connection and try again."
    fi
    ok "Cloned successfully"
fi

# ── Step 2: Download models ─────────────────────────────────────────────────

echo ""
info "Step 2/4: Face recognition models"

MODELS_DIR="$FANJING_DIR/models"
SPEAKING_DIR="$MODELS_DIR/speaking"
mkdir -p "$SPEAKING_DIR"

download_model() {
    local name="$1" path="$2" script="$3"
    if [[ -f "$path" ]]; then
        local size
        size=$(wc -c < "$path" | tr -d ' ')
        if [[ "$size" -gt 10000 ]]; then
            ok "$name: already exists ($(( size / 1048576 )) MB)"
            return 0
        fi
        warn "$name: file exists but seems incomplete, re-downloading..."
    fi
    info "Downloading $name..."
    if ! (cd "$FANJING_DIR" && python3 "$script"); then
        warn "$name: download failed. You can retry manually:"
        warn "  cd $FANJING_DIR && python3 $script"
        return 1
    fi
    ok "$name: downloaded"
}

download_url() {
    local name="$1" path="$2" url="$3"
    if [[ -f "$path" ]]; then
        local size
        size=$(wc -c < "$path" | tr -d ' ')
        if [[ "$size" -gt 10000 ]]; then
            ok "$name: already exists ($(( size / 1048576 )) MB)"
            return 0
        fi
    fi
    info "Downloading $name..."
    if ! curl -fSL -o "$path" "$url"; then
        warn "$name: download failed. You can retry manually:"
        warn "  curl -L -o $path '$url'"
        return 1
    fi
    ok "$name: downloaded"
}

DOWNLOAD_ERRORS=0

download_model "det_10g.onnx (face detection)" \
    "$MODELS_DIR/det_10g.onnx" \
    "scripts/download_model.py" || DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))

download_model "w600k_r50.onnx (face embedding)" \
    "$MODELS_DIR/w600k_r50.onnx" \
    "scripts/download_arcface.py" || DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))

download_model "silero_vad_half.onnx (voice activity)" \
    "$SPEAKING_DIR/silero_vad_half.onnx" \
    "scripts/download_silero_vad.py" || DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))

download_url "face_landmarker.task (speaking detection)" \
    "$MODELS_DIR/face_landmarker.task" \
    "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task" || DOWNLOAD_ERRORS=$((DOWNLOAD_ERRORS + 1))

if [[ "$DOWNLOAD_ERRORS" -gt 0 ]]; then
    warn "$DOWNLOAD_ERRORS model(s) failed to download. Face recognition will still work with available models."
fi

# ── Step 3: Configure .env ──────────────────────────────────────────────────

echo ""
info "Step 3/4: Environment configuration"

ENV_FILE="$PROJECT_DIR/.env"
ENV_TEMPLATE="$PROJECT_DIR/.env.template"

if [[ ! -f "$ENV_FILE" ]]; then
    if [[ -f "$ENV_TEMPLATE" ]]; then
        info "Creating .env from template..."
        cp "$ENV_TEMPLATE" "$ENV_FILE"
        ok ".env created from .env.template"
        warn "Please edit .env to set LLM_API_KEY and other required values."
    else
        fail ".env.template not found at $ENV_TEMPLATE"
    fi
else
    ok ".env already exists"
fi

if grep -q "^FACE_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    EXISTING_KEY=$(grep "^FACE_API_KEY=" "$ENV_FILE" | head -1 | cut -d= -f2-)
    if [[ -n "$EXISTING_KEY" ]]; then
        ok "FACE_API_KEY already configured"
    else
        info "FACE_API_KEY exists but is empty, generating..."
        NEW_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
        sed -i.bak "s/^FACE_API_KEY=.*/FACE_API_KEY=$NEW_KEY/" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
        ok "FACE_API_KEY generated"
    fi
else
    info "Generating FACE_API_KEY..."
    NEW_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "" >> "$ENV_FILE"
    echo "FACE_API_KEY=$NEW_KEY" >> "$ENV_FILE"
    ok "FACE_API_KEY added to .env"
fi

# ── Step 4: Install fanjing dependencies (host mode) ────────────────────────

echo ""
info "Step 4/4: Python dependencies for fanjing-face-recognition"

if [[ "$OS" == "macos" || "$OS" == "windows" ]]; then
    info "On $OS, face recognition must run on the host (camera access)."
    VENV_DIR="$FANJING_DIR/.venv"
    if [[ "$OS" == "windows" ]]; then
        VENV_PYTHON="$VENV_DIR/Scripts/python"
        VENV_PIP="$VENV_DIR/Scripts/pip"
    else
        VENV_PYTHON="$VENV_DIR/bin/python3"
        VENV_PIP="$VENV_DIR/bin/pip"
    fi
    if [[ -d "$VENV_DIR" ]] && "$VENV_PYTHON" -c "import cv2" 2>/dev/null; then
        ok "Virtual environment exists with dependencies"
    else
        if confirm "  Create virtual environment and install dependencies?"; then
            info "Creating venv at $VENV_DIR..."
            python3 -m venv "$VENV_DIR"
            info "Installing requirements..."
            if "$VENV_PIP" install -r "$FANJING_DIR/requirements.txt" -q; then
                ok "Dependencies installed"
            else
                warn "Installation failed. Try manually:"
                warn "  cd $FANJING_DIR && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
            fi
        else
            warn "Skipped. Install manually before running face recognition."
        fi
    fi
else
    info "On Linux, face recognition can run in Docker (--profile playground)."
    info "Skipping host dependency installation."
    ok "Use: docker compose --profile ui --profile playground up --build -d"
fi

# ── Summary ─────────────────────────────────────────────────────────────────

echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD}  Setup Complete${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""

FACE_API_KEY=$(grep "^FACE_API_KEY=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || echo "")

if [[ "$OS" == "linux" ]]; then
    echo -e "${GREEN}Linux detected — full Docker deployment available:${NC}"
    echo ""
    echo "  docker compose --profile ui --profile playground up --build -d"
    echo ""
    echo "  Playground UI: http://localhost:3000 → Playground tab"
    echo ""
else
    echo -e "${GREEN}$OS detected — recommended hybrid deployment:${NC}"
    echo ""
    echo "  # Terminal 1: Start face recognition (on host)"
    echo "  cd $FANJING_DIR"
    if [[ -d "$FANJING_DIR/.venv" ]]; then
        echo "  source .venv/bin/activate"
    fi
    echo "  export FACE_API_KEY=\"$FACE_API_KEY\""
    echo "  python run_web_v2.py --host 0.0.0.0 --port 5001 --no-browser"
    echo ""
    echo "  # Terminal 2: Start M-flow (in Docker)"
    echo "  cd $PROJECT_DIR"
    echo "  docker compose --profile ui up --build -d"
    echo ""
    echo "  Playground UI: http://localhost:3000 → Playground tab"
    echo "  Face recognition: http://localhost:5001"
    echo ""
fi

if ! grep -q "^LLM_API_KEY=.\+" "$ENV_FILE" 2>/dev/null || grep -q "^LLM_API_KEY=your_api_key" "$ENV_FILE" 2>/dev/null; then
    warn "Remember to set LLM_API_KEY in .env before starting M-flow!"
fi

echo -e "${BLUE}Documentation: https://github.com/FlowElement-ai/m_flow#playground-with-face-recognition${NC}"
echo ""
