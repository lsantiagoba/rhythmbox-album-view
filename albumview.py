# -*- coding: utf-8 -*-
"""An Apple Music-inspired album browser for Rhythmbox 3.x."""

import os
import random

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Peas", "1.0")
gi.require_version("RB", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, Peas, RB


UNKNOWN_ALBUM = "Unknown Album"
UNKNOWN_ARTIST = "Unknown Artist"
COVER_SIZE = 176
ALBUMS_PER_PAGE = 80


def text(entry, prop, fallback=""):
    value = entry.get_string(prop)
    return value.strip() if value and value.strip() else fallback


def number(entry, prop):
    try:
        return int(entry.get_ulong(prop))
    except (TypeError, ValueError, AttributeError):
        return 0


def duration_label(seconds):
    seconds = max(0, int(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


class AlbumViewSource(RB.Source):
    __gtype_name__ = "AlbumViewSource"

    def __init__(self):
        super().__init__()
        self._albums = []
        self._filtered = []
        self._selected = None
        self._art_store = RB.ExtDB(name="album-art")
        self._art_generation = 0
        self._db_handlers = []
        self._search_id = 0
        self._last_query = None
        self._page = 0
        self._build_ui()

    def do_selected(self):
        if not self._albums:
            self.reload_library()

    def do_can_delete(self):
        return False

    def do_can_pause(self):
        return True

    def do_get_entry_view(self):
        return None

    def _build_ui(self):
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.get_style_context().add_class("album-view-root")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        header.get_style_context().add_class("album-view-header")
        title = Gtk.Label(label="Albums", xalign=0)
        title.get_style_context().add_class("album-view-title")
        header.pack_start(title, True, True, 0)

        self.search = Gtk.SearchEntry(placeholder_text="Search albums or artists")
        self.search.set_width_chars(28)
        self.search.connect("search-changed", self._on_search_changed)
        header.pack_end(self.search, False, False, 0)

        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON)
        refresh.set_tooltip_text("Refresh albums")
        refresh.connect("clicked", lambda _button: self.reload_library())
        header.pack_end(refresh, False, False, 0)
        root.pack_start(header, False, False, 0)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=180)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)

        grid_scroll = Gtk.ScrolledWindow()
        grid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.flow = Gtk.FlowBox()
        self.flow.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flow.set_homogeneous(False)
        self.flow.set_row_spacing(24)
        self.flow.set_column_spacing(20)
        self.flow.set_min_children_per_line(1)
        self.flow.set_max_children_per_line(12)
        self.flow.set_margin_start(24)
        self.flow.set_margin_end(24)
        self.flow.set_margin_top(20)
        self.flow.set_margin_bottom(30)
        grid_scroll.add(self.flow)

        grid_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        grid_page.pack_start(grid_scroll, True, True, 0)
        pager = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        pager.set_halign(Gtk.Align.CENTER)
        pager.set_margin_top(8)
        pager.set_margin_bottom(12)
        self.page_previous = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.page_previous.set_tooltip_text("Previous albums")
        self.page_previous.connect("clicked", self._previous_page)
        self.page_label = Gtk.Label()
        self.page_next = Gtk.Button.new_from_icon_name(
            "go-next-symbolic", Gtk.IconSize.BUTTON)
        self.page_next.set_tooltip_text("Next albums")
        self.page_next.connect("clicked", self._next_page)
        pager.pack_start(self.page_previous, False, False, 0)
        pager.pack_start(self.page_label, False, False, 0)
        pager.pack_start(self.page_next, False, False, 0)
        grid_page.pack_start(pager, False, False, 0)
        self.stack.add_named(grid_page, "grid")

        self.detail_scroll = Gtk.ScrolledWindow()
        self.detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.detail_scroll.add(self.detail_box)
        self.stack.add_named(self.detail_scroll, "detail")
        root.pack_start(self.stack, True, True, 0)
        self.pack_start(root, True, True, 0)
        self.show_all()

    def connect_database(self):
        db = self.props.shell.props.db
        self._db_handlers = [
            db.connect("entry-added", self._database_changed),
            db.connect("entry-deleted", self._database_changed),
        ]
        self.reload_library()

    def disconnect_database(self):
        if self._search_id:
            GLib.source_remove(self._search_id)
            self._search_id = 0
        if getattr(self, "_reload_id", 0):
            GLib.source_remove(self._reload_id)
            self._reload_id = 0
        db = self.props.shell.props.db
        for handler in self._db_handlers:
            db.disconnect(handler)
        self._db_handlers = []

    def _database_changed(self, *_args):
        if getattr(self, "_reload_id", 0):
            GLib.source_remove(self._reload_id)
        self._reload_id = GLib.timeout_add(600, self._delayed_reload)

    def _delayed_reload(self):
        self._reload_id = 0
        self.reload_library()
        return GLib.SOURCE_REMOVE

    def reload_library(self):
        db = self.props.shell.props.db
        song_type = db.entry_type_get_by_name("song")
        grouped = {}

        def collect(entry, _data):
            try:
                if entry.get_entry_type() != song_type:
                    return
                album = text(entry, RB.RhythmDBPropType.ALBUM, UNKNOWN_ALBUM)
                artist = text(entry, RB.RhythmDBPropType.ALBUM_ARTIST)
                if not artist:
                    artist = text(entry, RB.RhythmDBPropType.ARTIST, UNKNOWN_ARTIST)
                key = (album.casefold(), artist.casefold())
                grouped.setdefault(key, {"title": album, "artist": artist, "tracks": []})["tracks"].append(entry)
            except Exception:
                return

        db.entry_foreach(collect, None)
        self._albums = list(grouped.values())
        for album in self._albums:
            album["tracks"].sort(key=lambda e: (
                number(e, RB.RhythmDBPropType.DISC_NUMBER),
                number(e, RB.RhythmDBPropType.TRACK_NUMBER),
                text(e, RB.RhythmDBPropType.TITLE).casefold()))
        self._albums.sort(key=lambda a: (a["artist"].casefold(), a["title"].casefold()))
        self._last_query = None
        self._apply_search()

    def _on_search_changed(self, _entry):
        # Typing used to rebuild hundreds of GTK widgets for every keystroke.
        if self._search_id:
            GLib.source_remove(self._search_id)
        self._search_id = GLib.timeout_add(180, self._apply_search)

    def _apply_search(self):
        self._search_id = 0
        query = self.search.get_text().strip().casefold()
        if query == self._last_query and self.stack.get_visible_child_name() == "grid":
            return GLib.SOURCE_REMOVE
        self._last_query = query
        self._filtered = [a for a in self._albums
                          if not query or query in a["title"].casefold() or query in a["artist"].casefold()]
        self._page = 0
        self._render_grid()
        return GLib.SOURCE_REMOVE

    def _previous_page(self, _button=None):
        if self._page > 0:
            self._page -= 1
            self._render_grid()

    def _next_page(self, _button=None):
        if (self._page + 1) * ALBUMS_PER_PAGE < len(self._filtered):
            self._page += 1
            self._render_grid()

    def _clear_container(self, container):
        for child in container.get_children():
            container.remove(child)

    def _render_grid(self):
        self._clear_container(self.flow)
        self._art_generation += 1
        if not self._filtered:
            empty = Gtk.Label(label="No albums found", margin_top=60)
            empty.get_style_context().add_class("dim-label")
            self.flow.add(empty)
        page_count = max(1, (len(self._filtered) + ALBUMS_PER_PAGE - 1) // ALBUMS_PER_PAGE)
        self._page = min(self._page, page_count - 1)
        start = self._page * ALBUMS_PER_PAGE
        end = start + ALBUMS_PER_PAGE
        self.page_label.set_text("Page %d of %d · %d albums" % (
            self._page + 1, page_count, len(self._filtered)))
        self.page_previous.set_sensitive(self._page > 0)
        self.page_next.set_sensitive(end < len(self._filtered))
        for album in self._filtered[start:end]:
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            cell.set_halign(Gtk.Align.START)
            cell.set_valign(Gtk.Align.START)
            button = Gtk.Button()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_halign(Gtk.Align.START)
            button.set_valign(Gtk.Align.START)
            button.set_size_request(COVER_SIZE + 16, -1)
            button.get_style_context().add_class("album-card")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            image = self._cover_image(album, COVER_SIZE)
            box.pack_start(image, False, False, 0)
            name = Gtk.Label(label=album["title"], xalign=0, ellipsize=3, max_width_chars=22)
            name.get_style_context().add_class("album-card-title")
            artist = Gtk.Label(label=album["artist"], xalign=0, ellipsize=3, max_width_chars=22)
            artist.get_style_context().add_class("album-card-artist")
            box.pack_start(name, False, False, 0)
            box.pack_start(artist, False, False, 0)
            button.add(box)
            button.connect("clicked", lambda _button, item=album: self._show_album(item))
            # FlowBox stretches its direct child to the full grid cell.  Keep
            # that allocation on a neutral wrapper so the button's hover and
            # active states cover only the visible album card.
            cell.pack_start(button, False, False, 0)
            self.flow.add(cell)
        self.flow.show_all()
        self.stack.set_visible_child_name("grid")

    def _cover_image(self, album, size, priority=False):
        image = Gtk.Image.new_from_icon_name("rhythmbox-missing-artwork", Gtk.IconSize.DIALOG)
        image.set_pixel_size(size)
        image.set_size_request(size, size)
        image.get_style_context().add_class("album-cover")
        entry = album["tracks"][0]
        key = entry.create_ext_db_key(RB.RhythmDBPropType.ALBUM)
        generation = self._art_generation

        # ExtDB.request() does much more than read the artwork cache: on a
        # miss it starts every art-search provider, including a new GStreamer
        # Discoverer.  Calling it for every grid card exhausts file
        # descriptors and several gigabytes of memory on a large library.
        # Grid cards therefore use the synchronous, cache-only lookup.
        try:
            filename, _store_key = self._art_store.lookup(key)
            if filename:
                art = GdkPixbuf.Pixbuf.new_from_file_at_scale(filename, size, size, True)
                image.set_from_pixbuf(art)
                return image
        except (GLib.Error, TypeError, AttributeError):
            pass

        if not priority:
            return image

        # An explicit detail view may fetch one missing cover.  Guard the
        # callback because the user can leave the page while it is running.
        def art_ready(_key, _store_key, filename, pixbuf):
            if generation == self._art_generation and image.get_parent() is not None:
                try:
                    art = pixbuf
                    if art is None and filename:
                        art = GdkPixbuf.Pixbuf.new_from_file_at_scale(filename, size, size, True)
                    elif art is not None:
                        art = art.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                    if art is not None:
                        image.set_from_pixbuf(art)
                except (GLib.Error, TypeError, AttributeError):
                    pass

        try:
            self._art_store.request(key, art_ready)
        except (GLib.Error, TypeError, AttributeError):
            pass
        return image

    def _show_album(self, album):
        self._selected = album
        self._clear_container(self.detail_box)
        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=26)
        hero.get_style_context().add_class("album-detail-hero")

        back = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        back.set_tooltip_text("Back to albums")
        back.set_valign(Gtk.Align.START)
        back.connect("clicked", self._show_grid)
        hero.pack_start(back, False, False, 0)
        # Detail artwork must not wait behind every card in a large library.
        hero.pack_start(self._cover_image(album, 220, priority=True), False, False, 0)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        info.set_valign(Gtk.Align.END)
        kind = Gtk.Label(label="ALBUM", xalign=0)
        kind.get_style_context().add_class("album-detail-kind")
        title = Gtk.Label(label=album["title"], xalign=0)
        title.set_line_wrap(True)
        title.get_style_context().add_class("album-detail-title")
        artist = Gtk.Label(label=album["artist"], xalign=0)
        artist.get_style_context().add_class("album-detail-artist")
        total = sum(number(t, RB.RhythmDBPropType.DURATION) for t in album["tracks"])
        meta = Gtk.Label(label="%d songs • %d min" % (len(album["tracks"]), total // 60), xalign=0)
        meta.get_style_context().add_class("dim-label")
        info.pack_start(kind, False, False, 0)
        info.pack_start(title, False, False, 0)
        info.pack_start(artist, False, False, 0)
        info.pack_start(meta, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_top=10)
        play = Gtk.Button(label="  Play  ")
        play.get_style_context().add_class("suggested-action")
        play.connect("clicked", lambda _button: self._play_album(album, False))
        shuffle = Gtk.Button(label="Shuffle")
        shuffle.connect("clicked", lambda _button: self._play_album(album, True))
        queue = Gtk.Button(label="Add to Queue")
        queue.connect("clicked", lambda _button: self._queue_album(album))
        controls.pack_start(play, False, False, 0)
        controls.pack_start(shuffle, False, False, 0)
        controls.pack_start(queue, False, False, 0)
        info.pack_start(controls, False, False, 0)
        hero.pack_start(info, True, True, 0)
        self.detail_box.pack_start(hero, False, False, 0)

        tracks = Gtk.ListBox()
        tracks.set_selection_mode(Gtk.SelectionMode.NONE)
        tracks.get_style_context().add_class("album-track-list")
        for index, entry in enumerate(album["tracks"]):
            row = Gtk.ListBoxRow()
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            line.set_margin_start(16)
            line.set_margin_end(16)
            line.set_margin_top(10)
            line.set_margin_bottom(10)
            track_no = number(entry, RB.RhythmDBPropType.TRACK_NUMBER) or index + 1
            no = Gtk.Label(label=str(track_no), width_chars=3, xalign=1)
            no.get_style_context().add_class("dim-label")
            label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            song = Gtk.Label(label=text(entry, RB.RhythmDBPropType.TITLE, "Untitled"), xalign=0, ellipsize=3)
            song.get_style_context().add_class("album-track-title")
            performer = Gtk.Label(label=text(entry, RB.RhythmDBPropType.ARTIST, album["artist"]), xalign=0, ellipsize=3)
            performer.get_style_context().add_class("dim-label")
            label_box.pack_start(song, False, False, 0)
            label_box.pack_start(performer, False, False, 0)
            length = Gtk.Label(label=duration_label(number(entry, RB.RhythmDBPropType.DURATION)))
            length.get_style_context().add_class("dim-label")
            line.pack_start(no, False, False, 0)
            line.pack_start(label_box, True, True, 0)
            line.pack_end(length, False, False, 0)
            row.add(line)
            row._album_entry = entry
            tracks.add(row)
        tracks.connect("row-activated", lambda _list, row: self._play_entry(row._album_entry))
        self.detail_box.pack_start(tracks, False, False, 0)
        self.detail_box.show_all()
        self.stack.set_visible_child_name("detail")
        self.detail_scroll.get_vadjustment().set_value(0)

    def _show_grid(self, _button=None):
        # The grid remains mounted behind the detail page.  Reusing it makes
        # Back instantaneous and preserves its scroll position and widgets.
        self.stack.set_visible_child_name("grid")

    def _play_entry(self, entry):
        self.props.shell.props.shell_player.play_entry(entry, self)

    def _queue_album(self, album, skip_first=False):
        queue = self.props.shell.props.queue_source
        tracks = album["tracks"][1:] if skip_first else album["tracks"]
        for entry in tracks:
            queue.add_entry(entry, -1)

    def _play_album(self, album, shuffle):
        tracks = list(album["tracks"])
        if shuffle:
            random.shuffle(tracks)
        if not tracks:
            return
        queue = self.props.shell.props.queue_source
        # Put the remaining songs at the front, preserving their order.  This
        # makes "Play" complete the selected album before an existing queue.
        for entry in reversed(tracks[1:]):
            queue.add_entry(entry, 0)
        self._play_entry(tracks[0])


class AlbumViewPlugin(GObject.Object, Peas.Activatable):
    __gtype_name__ = "AlbumViewPlugin"
    object = GObject.Property(type=GObject.Object)

    def do_activate(self):
        shell = self.object
        self._load_css()
        song_type = shell.props.db.entry_type_get_by_name("song")
        self.source = GObject.new(
            AlbumViewSource,
            shell=shell,
            plugin=self,
            name="Albums",
            icon=Gio.ThemedIcon.new("media-optical-symbolic"),
            entry_type=song_type,
        )
        library_group = RB.DisplayPageGroup.get_by_id("library")
        shell.append_display_page(self.source, library_group)
        GLib.idle_add(self._place_after_music)
        self.source.connect_database()

    def do_deactivate(self):
        if getattr(self, "source", None):
            self.source.disconnect_database()
            self.source.delete_thyself()
            self.source = None

    def _place_after_music(self):
        """Keep Albums beside Music, before the next library source."""
        model = self.object.props.display_page_model

        def find_page(treeiter, page):
            while treeiter is not None:
                if model[treeiter][1] == page:
                    return treeiter
                child = model.iter_children(treeiter)
                found = find_page(child, page) if child else None
                if found:
                    return found
                treeiter = model.iter_next(treeiter)
            return None

        music_iter = find_page(model.get_iter_first(), self.object.props.library_source)
        album_iter = find_page(model.get_iter_first(), self.source)
        if music_iter and album_iter:
            next_iter = model.iter_next(music_iter)
            # DisplayPageModel is not a Gtk.TreeStore on all Rhythmbox
            # versions (3.4.9 has no move_before method).
            if next_iter and hasattr(model, "move_before"):
                model.move_before(album_iter, next_iter)
        return GLib.SOURCE_REMOVE

    def _load_css(self):
        provider = Gtk.CssProvider()
        css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "albumview.css")
        provider.load_from_path(css_path)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._css_provider = provider
