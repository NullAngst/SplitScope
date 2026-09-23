#!/bin/sh
# Adds SplitScope to your application menu (current user only).
# Run it from the extracted SplitScope folder. Re-run after moving the folder.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
mkdir -p "$APPS" "$ICONS"
cp "$HERE/icon.png" "$ICONS/splitscope.png"
cat > "$APPS/splitscope.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SplitScope
Comment=Audio separation workstation
Exec="$HERE/SplitScope" %f
Icon=splitscope
Terminal=false
Categories=AudioVideo;Audio;
MimeType=audio/mpeg;audio/flac;audio/x-wav;audio/wav;audio/ogg;audio/mp4;audio/aac;
StartupWMClass=SplitScope
DESKTOP
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" >/dev/null 2>&1 || true
echo "Installed $APPS/splitscope.desktop"
