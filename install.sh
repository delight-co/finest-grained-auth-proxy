#!/bin/bash
#
# fgap CLI wrapper installer
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

# Verify wrappers are available
FGAP_GH=$(command -v fgap-gh 2>/dev/null || true)
if [[ -z "$FGAP_GH" ]]; then
    echo "[fgap] Error: fgap-gh not found in PATH after install" >&2
    exit 1
fi

FGAP_GOG=$(command -v fgap-gog 2>/dev/null || true)
if [[ -z "$FGAP_GOG" ]]; then
    echo "[fgap] Error: fgap-gog not found in PATH after install" >&2
    exit 1
fi

if $REPLACE; then
    # Replace gh
    TARGET_GH="${INSTALL_DIR}/gh"
    EXISTING_GH=$(command -v gh 2>/dev/null || true)
    if [[ -n "$EXISTING_GH" && "$EXISTING_GH" != "$TARGET_GH" ]]; then
        echo "[fgap] Removing existing gh at ${EXISTING_GH}..."
        sudo rm -f "${EXISTING_GH}"
    fi
    echo "[fgap] Linking gh -> fgap-gh..."
    sudo ln -sf "${FGAP_GH}" "${TARGET_GH}"

    # Replace gog
    TARGET_GOG="${INSTALL_DIR}/gog"
    EXISTING_GOG=$(command -v gog 2>/dev/null || true)
    if [[ -n "$EXISTING_GOG" && "$EXISTING_GOG" != "$TARGET_GOG" ]]; then
        echo "[fgap] Removing existing gog at ${EXISTING_GOG}..."
        sudo rm -f "${EXISTING_GOG}"
    fi
    echo "[fgap] Linking gog -> fgap-gog..."
    sudo ln -sf "${FGAP_GOG}" "${TARGET_GOG}"

    echo "[fgap] Done! 'gh' and 'gog' now route through fgap proxy."
else
    echo "[fgap] Installed fgap-gh at ${FGAP_GH}"
    echo "[fgap] Installed fgap-gog at ${FGAP_GOG}"
    echo "[fgap] Done! Run 'fgap-gh --help' or 'fgap-gog --help' to get started."
    echo "[fgap] Use --replace to install as 'gh' and 'gog'."
fi
