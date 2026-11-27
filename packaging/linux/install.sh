#!/usr/bin/env bash
# Install the Linux build for the current user (no root needed):
#   ~/.local/bin/mamarmiton, its icon, and a menu entry so app launchers find it.
#
#   packaging/linux/install.sh [path/to/mamarmiton]     # default: dist/mamarmiton
#   packaging/linux/install.sh --uninstall
#
# Recipes stay in ~/.local/share/recette_app; uninstalling never touches them.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="$HOME/.local/bin/mamarmiton"
ICON="$HOME/.local/share/icons/hicolor/512x512/apps/mamarmiton.png"
DESKTOP="$HOME/.local/share/applications/mamarmiton.desktop"

refresh() {
  command -v update-desktop-database >/dev/null && update-desktop-database -q "$HOME/.local/share/applications" || true
  command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
}

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$BIN" "$ICON" "$DESKTOP"
  refresh
  echo "Mamarmiton removed (your recipes in ~/.local/share/recette_app are kept)."
  exit 0
fi

SOURCE="${1:-$HERE/../../dist/mamarmiton}"
if [ ! -x "$SOURCE" ]; then
  echo "No executable at $SOURCE. Build it first: packaging/linux/build.sh" >&2
  exit 1
fi

install -Dm755 "$SOURCE" "$BIN"
install -Dm644 "$HERE/mamarmiton.png" "$ICON"
install -Dm644 /dev/stdin "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Mamarmiton
GenericName=Carnet de recettes
Comment=Le carnet de recettes de la famille
Exec=$BIN
Icon=mamarmiton
Terminal=false
Categories=Utility;
Keywords=recette;cuisine;recipe;cooking;
StartupWMClass=Mamarmiton
EOF
refresh
echo "Installed $BIN and the Mamarmiton menu entry."
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "Note: ~/.local/bin is not on your PATH (the menu entry works anyway)." ;; esac
