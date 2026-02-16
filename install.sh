#!/bin/bash
#
# fgap-gh installer
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/delight-co/finest-grained-auth-proxy/main/install.sh | bash -s -- --replace
#

set -e

REPO="delight-co/finest-grained-auth-proxy"
INSTALL_DIR="/usr/local/bin"

REPLACE=false
for arg in "$@"; do
    case "$arg" in
        --replace) REPLACE=true ;;
        *) echo "[fgap] Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# Install Python package from GitHub
if command -v uv &>/dev/null; then
    echo "[fgap] Installing with uv..."
    uv pip install "git+https://github.com/${REPO}.git"
elif command -v pip &>/dev/null; then
    echo "[fgap] Installing with pip..."
    pip install "git+https://github.com/${REPO}.git"
else
    echo "[fgap] Error: pip or uv is required" >&2
    exit 1
fi

# Verify fgap-gh is available
FGAP_GH=$(command -v fgap-gh 2>/dev/null || true)
if [[ -z "$FGAP_GH" ]]; then
    echo "[fgap] Error: fgap-gh not found in PATH after install" >&2
    exit 1
fi

if $REPLACE; then
    TARGET="${INSTALL_DIR}/gh"

    EXISTING_GH=$(command -v gh 2>/dev/null || true)
    if [[ -n "$EXISTING_GH" && "$EXISTING_GH" != "$TARGET" ]]; then
        echo "[fgap] Removing existing gh at ${EXISTING_GH}..."
        sudo rm -f "${EXISTING_GH}"
    fi

    echo "[fgap] Linking gh -> fgap-gh..."
    sudo ln -sf "${FGAP_GH}" "${TARGET}"
    echo "[fgap] Done! 'gh' now routes through fgap proxy."
else
    echo "[fgap] Installed fgap-gh at ${FGAP_GH}"
    echo "[fgap] Done! Run 'fgap-gh --help' to get started."
    echo "[fgap] Use --replace to install as 'gh'."
fi
