# Album View for Rhythmbox

A native Rhythmbox 3 plugin that presents the music library as an Apple Music-inspired album grid. Select a cover to see the complete album, then play it in order, shuffle it, add it to the queue, or double-click an individual song.

![Album View showing the Rhythmbox library grid](src/images/Screenshot%20From%202026-07-17%2021-07-12.png)

## Features

- Responsive cover-art album grid
- Search by album or album artist
- Album detail page with track numbers, artists, durations, and full-size artwork
- Play, Shuffle, and Add to Queue controls
- Automatic refresh when the Rhythmbox library changes
- Native GTK styling that follows the current desktop theme

## Install

Run:

```bash
chmod +x install.sh
./install.sh
```

Restart Rhythmbox, open **Tools → Plugins**, and enable **Album View**. An **Albums** item will appear beneath Music in the sidebar.

To install manually, copy `albumview.py`, `albumview.plugin`, and `albumview.css` to:

```text
~/.local/share/rhythmbox/plugins/albumview/
```

## Compatibility

Designed for Rhythmbox 3.x with Python 3, GTK 3, and the `python3-gi` Rhythmbox bindings. It was developed against Rhythmbox 3.4.9.

## License

Album View is free software licensed under the [GNU General Public License v3.0](LICENSE).
