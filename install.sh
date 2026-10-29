#!/usr/bin/env bash
# Spotarchive install script for Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/LargePenguin/spotarchive/main/install.sh | bash
#
# or, from a clone: ./install.sh
#
# Installs Spotarchive with all its dependencies into a virtualenv (.venv) in
# the project folder: the Python packages, SomeDL (the default download
# backend), the Chromium browser used for logging in and FFmpeg. Then links
# the `spotarchive` command into ~/.local/bin. Safe to run again to repair or
# update an install.
#
# Options (also settable through the environment variables in brackets):
#   --dir DIR      where to clone Spotarchive when not run from a clone
#                  [SPOTARCHIVE_DIR, default ~/spotarchive]
#   --bin-dir DIR  where to link the command [SPOTARCHIVE_BIN_DIR, default ~/.local/bin]
#   --no-browser   don't install Playwright's Chromium (Chrome or Edge is used instead)
#   --no-ffmpeg    don't install FFmpeg
#   --no-link      don't link the command
#   --dev          also install the test dependencies
#   -y, --yes      don't ask before installing system packages
set -euo pipefail

REPO_URL="https://github.com/LargePenguin/spotarchive.git"
INSTALL_DIR="${SPOTARCHIVE_DIR:-$HOME/spotarchive}"
BIN_DIR="${SPOTARCHIVE_BIN_DIR:-$HOME/.local/bin}"
WITH_BROWSER=1
WITH_FFMPEG=1
WITH_LINK=1
EXTRAS="somedl"
ASSUME_YES=0

if [ -t 1 ]; then
    BOLD=$'\e[1m' GREEN=$'\e[32m' YELLOW=$'\e[33m' RED=$'\e[31m' RESET=$'\e[0m'
else
    BOLD="" GREEN="" YELLOW="" RED="" RESET=""
fi
step() { printf '%s==>%s %s\n' "$BOLD$GREEN" "$RESET" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die() { printf '%s[x]%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) INSTALL_DIR="${2:?--dir needs a folder}"; shift ;;
        --bin-dir) BIN_DIR="${2:?--bin-dir needs a folder}"; shift ;;
        --no-browser) WITH_BROWSER=0 ;;
        --no-ffmpeg) WITH_FFMPEG=0 ;;
        --no-link) WITH_LINK=0 ;;
        --dev) EXTRAS="dev,somedl" ;;
        -y|--yes) ASSUME_YES=1 ;;
        -h|--help) sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p}' "$0" 2>/dev/null || echo "See the comment at the top of install.sh."; exit 0 ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac
    shift
done

# Asks a yes/no question on the terminal, even when the script is piped into bash.
confirm() {
    [ "$ASSUME_YES" = 1 ] && return 0
    local answer
    if ! { exec 3</dev/tty; } 2>/dev/null; then
        return 1
    fi
    printf '%s [Y/n] ' "$1" >/dev/tty
    read -r answer <&3 || answer=n
    exec 3<&-
    case "$answer" in ""|[Yy]*) return 0 ;; *) return 1 ;; esac
}

# --- Project folder ---------------------------------------------------------

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ] && grep -q '^name = "spotarchive"' "$SCRIPT_DIR/pyproject.toml"; then
    PROJECT_DIR="$SCRIPT_DIR"
    step "Using the Spotarchive folder $PROJECT_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
    PROJECT_DIR="$INSTALL_DIR"
    step "Updating $PROJECT_DIR"
    git -C "$PROJECT_DIR" pull --ff-only || warn "Couldn't update the clone; installing the version that's there."
else
    command -v git >/dev/null || die "git is needed to download Spotarchive. Install it with your package manager and run this again."
    [ -e "$INSTALL_DIR" ] && die "$INSTALL_DIR already exists and isn't a Spotarchive clone. Pick another folder with --dir."
    step "Downloading Spotarchive into $INSTALL_DIR"
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    PROJECT_DIR="$INSTALL_DIR"
fi

# A git worktree shares the main checkout's virtualenv; installing here would
# repoint it at the worktree (see CLAUDE.md).
if [ -f "$PROJECT_DIR/.git" ]; then
    die "$PROJECT_DIR is a git worktree. Run the install script from the main checkout."
fi

# --- Python -----------------------------------------------------------------

PYTHON=""
for candidate in python3 python python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null &&
        "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done
[ -n "$PYTHON" ] || die "Spotarchive needs Python 3.10 or newer. Install it with your package manager and run this again."
step "Using $("$PYTHON" --version) ($(command -v "$PYTHON"))"

VENV="$PROJECT_DIR/.venv"
if [ ! -x "$VENV/bin/python" ]; then
    step "Creating the virtualenv $VENV"
    if ! "$PYTHON" -m venv "$VENV"; then
        rm -rf "$VENV"
        die "Couldn't create a virtualenv. On Debian/Ubuntu install the python3-venv package and run this again."
    fi
fi
VPY="$VENV/bin/python"

step "Installing Spotarchive and its Python dependencies (including SomeDL)"
"$VPY" -m pip install --upgrade --quiet pip
"$VPY" -m pip install --upgrade --editable "$PROJECT_DIR[$EXTRAS]"

if [ "$WITH_BROWSER" = 1 ]; then
    step "Installing the Chromium browser for the Spotify login"
    "$VPY" -m playwright install chromium ||
        warn "Couldn't install Chromium. The login can still use an installed Chrome or Edge."
fi

# --- FFmpeg -----------------------------------------------------------------

install_ffmpeg() {
    local sudo=""
    [ "$(id -u)" -ne 0 ] && sudo="sudo"
    if command -v apt-get >/dev/null; then
        $sudo apt-get update && $sudo apt-get install -y ffmpeg
    elif command -v dnf >/dev/null; then
        $sudo dnf install -y ffmpeg || $sudo dnf install -y ffmpeg-free
    elif command -v pacman >/dev/null; then
        $sudo pacman -S --needed --noconfirm ffmpeg
    elif command -v zypper >/dev/null; then
        $sudo zypper --non-interactive install ffmpeg
    elif command -v apk >/dev/null; then
        $sudo apk add ffmpeg
    else
        return 1
    fi
}

if [ "$WITH_FFMPEG" = 1 ]; then
    if command -v ffmpeg >/dev/null; then
        step "FFmpeg is already installed"
    elif confirm "SomeDL needs FFmpeg. Install it with your package manager (may ask for your password)?"; then
        step "Installing FFmpeg"
        install_ffmpeg || warn "Couldn't install FFmpeg. Install it yourself before downloading: https://ffmpeg.org/download.html"
    else
        warn "Skipped FFmpeg. Install it before downloading: https://ffmpeg.org/download.html"
    fi
fi

# --- Command ----------------------------------------------------------------

if [ "$WITH_LINK" = 1 ]; then
    mkdir -p "$BIN_DIR"
    ln -sf "$VENV/bin/spotarchive" "$BIN_DIR/spotarchive"
    step "Linked the spotarchive command into $BIN_DIR"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR isn't on your PATH. Add it (e.g. in ~/.bashrc: export PATH=\"$BIN_DIR:\$PATH\") or run $VENV/bin/spotarchive." ;;
    esac
fi

"$VENV/bin/spotarchive" --version >/dev/null || die "The installed spotarchive command doesn't start."

cat <<EOF

${BOLD}${GREEN}Spotarchive is installed.${RESET}
Run ${BOLD}spotarchive${RESET} in the folder that should hold your music: it logs in,
lets you pick playlists and downloads them into ./data/.
Docs: https://github.com/LargePenguin/spotarchive/blob/main/docs/README.md
EOF
