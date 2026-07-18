#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Do not honor XDG_DATA_HOME here.  When this script is launched from a
# sandboxed editor (for example the Snap build of VS Code), that variable
# points inside the editor's private data directory, which Rhythmbox never
# scans.  User-installed Rhythmbox plugins belong in this well-known path.
plugin_dir="$HOME/.local/share/rhythmbox/plugins/albumview"
mkdir -p "$plugin_dir"
cp "$script_dir/albumview.py" \
   "$script_dir/albumview.plugin" \
   "$script_dir/albumview.css" \
   "$plugin_dir/"
echo "Installed Album View in $plugin_dir"
echo "Restart Rhythmbox, then enable Album View in Tools → Plugins."
