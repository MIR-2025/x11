#!/usr/bin/env python3
"""WinView -- a live overview of your open windows (X11 / XFCE).

Two modes, one engine:

  winview.py --overview   Summon a full-screen grid of window thumbnails.
                          Click one to focus it; Esc (or click a blank area)
                          dismisses. Great bound to a hotkey. (default)

  winview.py --panel      A always-on-top strip that stays open and refreshes
                          live. Click a thumbnail to focus that window.
                          --edge top|bottom chooses where it sits.

Needs: python3-gi, GTK 3, libwnck (gir1.2-wnck-3.0). All standard on XFCE.
"""

import argparse
import math
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Wnck', '3.0')
gi.require_version('GdkX11', '3.0')
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Wnck, GdkX11, Pango  # noqa: E402
import cairo  # noqa: E402

try:
    from Xlib import display as _xlib_display, X as _xlib_X
    _HAVE_XLIB = True
except Exception:
    _HAVE_XLIB = False

# ---- sizes ----
OV_TW, OV_TH = 300, 176        # overview thumbnail box
PANEL_H = 128                   # panel strip height
PANEL_TW, PANEL_TH = 176, 96    # panel thumbnail box

CSS = b"""
window.wv, window.panel { background: rgba(18, 21, 26, 0.97); }
window.dash { background: #14171c; }
.dash-bar { background: #1f232b; border-bottom: 1px solid #333a45; }
.hdr { color: #9aa4b2; font-size: 12px; }
.card { background: #23272f; border: 1px solid #333a45; border-radius: 11px; }
.card:hover { background: #2c313b; border-color: #4c8bf5; }
.thumb { background: #0f1216; border-radius: 8px; }
.title { color: #e7ebf1; font-size: 12px; }
.subtitle { color: #9aa4b2; font-size: 10px; }
"""


def apply_css():
    prov = Gtk.CssProvider()
    prov.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)


def pump():
    """Let Wnck/GTK process pending events (so the window list is populated)."""
    for _ in range(80):
        if Gtk.events_pending():
            Gtk.main_iteration()


def best_grid(n, avail_w, avail_h, aspect, gap, chrome_h, pad):
    """Pick the column count that makes the thumbnails as large as possible while
    fitting all n windows in avail_w x avail_h. Returns (cols, thumb_w, thumb_h)."""
    best = None
    for cols in range(1, max(1, n) + 1):
        rows = math.ceil(n / cols)
        cell_w = (avail_w - (cols - 1) * gap) / cols
        cell_h = (avail_h - (rows - 1) * gap) / rows
        tmax_w = cell_w - 2 * pad
        tmax_h = cell_h - chrome_h
        if tmax_w < 40 or tmax_h < 30:
            continue
        tw = min(tmax_w, tmax_h * aspect)
        th = tw / aspect
        area = tw * th
        if best is None or area > best[0]:
            best = (area, cols, int(tw), int(th))
    if best is None:
        return (min(n, 4) or 1, 220, 130)
    return best[1], best[2], best[3]


def usable_windows(screen):
    out = []
    for w in screen.get_windows():
        if w.is_skip_tasklist():
            continue
        t = w.get_window_type()
        if t in (Wnck.WindowType.DESKTOP, Wnck.WindowType.DOCK, Wnck.WindowType.SPLASHSCREEN):
            continue
        out.append(w)
    return out


_XDISPLAY = None


def _xdisplay():
    global _XDISPLAY
    if _XDISPLAY is None and _HAVE_XLIB:
        try:
            _XDISPLAY = _xlib_display.Display()
        except Exception:
            _XDISPLAY = False
    return _XDISPLAY or None


def capture_surface(xid, max_w, max_h):
    """Grab a window's contents as a scaled cairo surface, or None.

    Uses XGetImage (python-xlib), NOT gdk_pixbuf_get_from_window: the latter can abort
    the whole process in cairo (`cairo_surface_mark_dirty` on a surface that carries
    mime data) when it captures a window that's mid-render -- a race that's impossible
    to guard against from Python. XGetImage bypasses cairo/gdk, and still sees
    obscured windows because the compositor keeps their pixmaps."""
    d = _xdisplay()
    if d is None:
        return None
    try:
        xw = d.create_resource_object('window', xid)
        g = xw.get_geometry()
        w, h = g.width, g.height
        if w <= 0 or h <= 0 or g.depth not in (24, 32):
            return None
        img = xw.get_image(0, 0, w, h, _xlib_X.ZPixmap, 0xffffffff)
        data = img.data
        if not isinstance(data, (bytes, bytearray)):
            data = bytes(data)
    except Exception:
        return None
    try:
        big = cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        dst = big.get_data()
        ds = big.get_stride()
        ss = len(data) // h
        if ss == ds and len(dst) == len(data):
            dst[:] = data
        else:
            row = min(ss, ds)
            for y in range(h):
                dst[y * ds:y * ds + row] = data[y * ss:y * ss + row]
        big.mark_dirty()
        scale = min(max_w / w, max_h / h)
        tw, th = max(1, int(w * scale)), max(1, int(h * scale))
        small = cairo.ImageSurface(cairo.FORMAT_RGB24, tw, th)
        cr = cairo.Context(small)
        cr.scale(scale, scale)
        cr.set_source_surface(big, 0, 0)
        cr.get_source().set_filter(cairo.FILTER_GOOD)
        cr.paint()
        return small
    except Exception:
        return None


def scaled_icon(win, size):
    icon = win.get_icon()
    if icon is None:
        return None
    if icon.get_width() != size:
        icon = icon.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
    return icon


def clean_pixbuf(pb):
    """Rebuild a pixbuf from just its raw pixels, dropping any options. Some pixbufs
    (notably app icons) carry the original file bytes as an option; GDK then attaches
    that as cairo "mime data", and drawing it aborts in cairo_surface_mark_dirty
    (`Assertion !_cairo_surface_has_mime_data`). A pixel-only copy has no mime data."""
    if pb is None:
        return None
    try:
        return GdkPixbuf.Pixbuf.new_from_bytes(
            pb.read_pixel_bytes(), pb.get_colorspace(), pb.get_has_alpha(),
            pb.get_bits_per_sample(), pb.get_width(), pb.get_height(), pb.get_rowstride())
    except Exception:
        return pb


def pixbuf_area(pb, w, h, css_class=None):
    """A DrawingArea that paints a pixbuf centered, via cairo (Gtk.Image and
    gdk_cairo_set_source_pixbuf both mark the surface dirty, which aborts on pixbufs
    that carry mime data -- so we draw a cleaned, pixel-only copy)."""
    pb = clean_pixbuf(pb)
    da = Gtk.DrawingArea()
    da.set_size_request(w, h)
    if css_class:
        da.get_style_context().add_class(css_class)

    def on_draw(widget, cr):
        if pb is None:
            return False
        a = widget.get_allocation()
        iw, ih = pb.get_width(), pb.get_height()
        Gdk.cairo_set_source_pixbuf(cr, pb, (a.width - iw) / 2, (a.height - ih) / 2)
        cr.paint()
        return False

    da.connect('draw', on_draw)
    return da


def surface_area(surface, w, h, css_class=None):
    """A DrawingArea that paints a cairo surface centered (for window thumbnails,
    which we capture as surfaces rather than pixbufs)."""
    da = Gtk.DrawingArea()
    da.set_size_request(w, h)
    if css_class:
        da.get_style_context().add_class(css_class)

    def on_draw(widget, cr):
        if surface is None:
            return False
        a = widget.get_allocation()
        sw, sh = surface.get_width(), surface.get_height()
        cr.set_source_surface(surface, (a.width - sw) / 2, (a.height - sh) / 2)
        cr.paint()
        return False

    da.connect('draw', on_draw)
    return da


def thumb_holder(win, tw, th):
    """A fixed-size thumbnail of the window, or its icon as a fallback."""
    surf = None if win.is_minimized() else capture_surface(win.get_xid(), tw - 4, th - 4)
    if surf is not None:
        return surface_area(surf, tw, th, 'thumb')
    return pixbuf_area(scaled_icon(win, 48), tw, th, 'thumb')


def make_card(win, on_click, tw, th, title_chars=26):
    card = Gtk.EventBox()
    card.get_style_context().add_class('card')
    card.set_above_child(False)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    for m in ('top', 'bottom', 'start', 'end'):
        getattr(box, 'set_margin_' + m)(8)
    box.pack_start(thumb_holder(win, tw, th), False, False, 0)

    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    mini = scaled_icon(win, 16)
    if mini is not None:
        row.pack_start(pixbuf_area(mini, 16, 16), False, False, 0)
    lbl = Gtk.Label(label=win.get_name() or '(untitled)')
    lbl.get_style_context().add_class('title')
    lbl.set_ellipsize(Pango.EllipsizeMode.END)
    lbl.set_max_width_chars(title_chars)
    lbl.set_xalign(0.0)
    row.pack_start(lbl, True, True, 0)
    box.pack_start(row, False, False, 0)

    card.add(box)
    card.connect('button-press-event', lambda _w, e: on_click(win, e))
    return card


class Overview:
    """Full-screen grid; click focuses a window and dismisses."""

    def __init__(self):
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()
        pump()
        self.wins = usable_windows(self.screen)
        self._built = False
        self._tries = 0

        self.win = Gtk.Window()
        self.win.get_style_context().add_class('wv')
        self.win.set_decorated(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_keep_above(True)
        self.win.fullscreen()
        self.win.connect('key-press-event', self._on_key)
        self.win.connect('destroy', Gtk.main_quit)

        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for m in ('top', 'bottom', 'start', 'end'):
            getattr(self.outer, 'set_margin_' + m)(20)
        bg = Gtk.EventBox()
        bg.add(self.outer)
        bg.connect('button-press-event', lambda *_a: (self.win.destroy(), True)[1])
        self.win.add(bg)
        self.win.show_all()

        # Build the grid from the window's ACTUAL size once the WM has fullscreened
        # it. Guessing a monitor's geometry gets the wrong size on multi-monitor /
        # HiDPI setups, so the grid ends up filling only part of the screen.
        GLib.timeout_add(30, self._build_once)

    def _build_once(self):
        self._tries += 1
        alloc = self.win.get_allocation()
        if (alloc.width < 400 or alloc.height < 400) and self._tries < 40:
            return True   # not fullscreened yet -- poll again
        self._populate(max(alloc.width, 800), max(alloc.height, 600))
        return False

    def _populate(self, w_px, h_px):
        n = len(self.wins)
        GAP, MARGIN, HEADER_H = 16, 20, 34
        avail_w = w_px - 2 * MARGIN
        avail_h = h_px - 2 * MARGIN - HEADER_H
        cols, tw, th = best_grid(n, avail_w, avail_h, w_px / max(1, h_px), GAP, 40, 8) if n else (1, OV_TW, OV_TH)

        hdr = Gtk.Label()
        hdr.get_style_context().add_class('hdr')
        hdr.set_markup('%d open window%s  ·  click to focus  ·  Esc to close'
                       % (n, '' if n == 1 else 's'))
        hdr.set_xalign(0.0)
        self.outer.pack_start(hdr, False, False, 0)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_halign(Gtk.Align.CENTER)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_min_children_per_line(cols)
        flow.set_max_children_per_line(cols)
        flow.set_row_spacing(GAP)
        flow.set_column_spacing(GAP)
        flow.set_homogeneous(True)
        for w in self.wins:
            flow.add(make_card(w, self._pick, tw, th))
        sw.add(flow)
        self.outer.pack_start(sw, True, True, 0)
        self.outer.show_all()

    def _pick(self, win, event):
        try:
            win.activate(event.time)
        except Exception:
            pass
        self.win.destroy()
        return True   # stop propagation to the backdrop

    def _on_key(self, _w, event):
        if event.keyval == Gdk.KEY_Escape:
            self.win.destroy()


class Panel:
    """Always-on-top strip that stays open and refreshes live."""

    REFRESH_MS = 4000

    def __init__(self, edge='top'):
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()
        pump()

        self.win = Gtk.Window()
        self.win.get_style_context().add_class('panel')
        self.win.set_decorated(False)
        self.win.set_skip_taskbar_hint(True)
        self.win.set_skip_pager_hint(True)
        self.win.set_keep_above(True)
        self.win.set_accept_focus(False)
        self.win.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.win.connect('destroy', Gtk.main_quit)

        gscreen = Gdk.Screen.get_default()
        width = gscreen.get_width()
        self.win.set_size_request(width, PANEL_H)
        self.win.move(0, 0 if edge == 'top' else gscreen.get_height() - PANEL_H)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.strip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for m in ('top', 'bottom', 'start', 'end'):
            getattr(self.strip, 'set_margin_' + m)(8)
        sw.add(self.strip)
        self.win.add(sw)

        self._pending = False
        for sig in ('window-opened', 'window-closed', 'active-window-changed'):
            self.screen.connect(sig, self._schedule)
        GLib.timeout_add(self.REFRESH_MS, self._tick)

        self.rebuild()
        self.win.show_all()

    def rebuild(self):
        for c in self.strip.get_children():
            self.strip.remove(c)
        for w in usable_windows(self.screen):
            self.strip.pack_start(make_card(w, self._focus, PANEL_TW, PANEL_TH, title_chars=16), False, False, 0)
        self.strip.show_all()

    def _focus(self, win, event):
        try:
            win.activate(event.time)
        except Exception:
            pass
        return True

    def _schedule(self, *_a):
        if self._pending:
            return
        self._pending = True
        GLib.timeout_add(250, self._do_scheduled)

    def _do_scheduled(self):
        self._pending = False
        self.rebuild()
        return False

    def _tick(self):
        self.rebuild()
        return True


class Dashboard:
    """A persistent, normal window -- shows in the taskbar and Alt+Tab ("tabbable"),
    keyboard-navigable (arrows to move, Enter to focus a window, Ctrl+W to close one,
    or just type to filter). Meant to stay open and start on login. This is the
    default mode."""

    REFRESH_MS = 5000
    TARGET_TW = 340          # desired thumbnail width; cards grow/shrink around this
    MAX_COLS = 8
    ASPECT = 250 / 148       # thumbnail width : height

    def __init__(self):
        self.screen = Wnck.Screen.get_default()
        self.screen.force_update()
        pump()
        self.query = ''
        self.TW, self.TH = 250, 148   # current card size (recomputed to fill the width)
        self._last_w = 0
        self._resize_id = 0

        self.win = Gtk.Window()
        self.win.get_style_context().add_class('dash')
        self.win.set_title('WinView')
        self.win.set_default_size(*self._default_size())
        try: self.win.set_icon_name('preferences-system-windows')
        except Exception: pass
        self.win.connect('destroy', Gtk.main_quit)
        self.win.connect('key-press-event', self._on_key)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        bar.get_style_context().add_class('dash-bar')
        for m in ('top', 'bottom', 'start', 'end'):
            getattr(bar, 'set_margin_' + m)(10)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text('Filter windows by title or app...')
        self.search.connect('search-changed', self._on_search)
        bar.pack_start(self.search, True, True, 0)
        self.count = Gtk.Label()
        self.count.get_style_context().add_class('hdr')
        bar.pack_end(self.count, False, False, 8)
        box.pack_start(bar, False, False, 0)

        self.sw = Gtk.ScrolledWindow()
        self.sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.sw.connect('size-allocate', self._on_resize)
        self.flow = Gtk.FlowBox()
        self.flow.set_valign(Gtk.Align.START)
        self.flow.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flow.set_min_children_per_line(1)
        self.flow.set_max_children_per_line(self.MAX_COLS)
        self.flow.set_row_spacing(14)
        self.flow.set_column_spacing(14)
        self.flow.set_homogeneous(True)
        for m in ('top', 'bottom', 'start', 'end'):
            getattr(self.flow, 'set_margin_' + m)(14)
        self.flow.set_filter_func(self._filter)
        self.flow.connect('child-activated', self._on_activated)
        self.sw.add(self.flow)
        box.pack_start(self.sw, True, True, 0)
        self.win.add(box)

        self._pending = False
        for sig in ('window-opened', 'window-closed', 'active-window-changed'):
            self.screen.connect(sig, self._schedule)
        GLib.timeout_add(self.REFRESH_MS, self._tick)

        self.rebuild()
        self.win.show_all()
        self.flow.get_style_context()  # ensure realized
        GLib.idle_add(lambda: (self.flow.grab_focus(), False)[1])

    def _default_size(self):
        """Open proportional to the monitor so there's room for big cards (esp. 4K)."""
        try:
            disp = Gdk.Display.get_default()
            mon = disp.get_primary_monitor() or disp.get_monitor(0)
            geo = mon.get_workarea()
            return max(900, int(geo.width * 0.72)), max(600, int(geo.height * 0.8))
        except Exception:
            return 1280, 800

    def _avail_w(self):
        w = self.sw.get_allocated_width()
        if w <= 1:
            w = self.win.get_allocated_width()
        if w <= 1:
            w = self.win.get_size()[0]
        return max(320, w)

    def _avail_h(self):
        h = self.sw.get_allocated_height()
        if h <= 1:
            h = self.win.get_size()[1]
        return max(240, h)

    def _layout(self, avail_w, n):
        """Choose columns + thumbnail size so the cards fill the available width.
        Few windows -> fewer, bigger cards; a wide/4K viewport -> bigger cards, not
        a strip of small ones with empty space to the right."""
        GAP, MARGIN, OVER = 14, 14, 18   # flow spacing, flow margins, per-card chrome
        inner = max(1, avail_w - 2 * MARGIN)
        cols = round((inner + GAP) / (self.TARGET_TW + OVER + GAP))
        cols = max(1, min(self.MAX_COLS, cols, max(1, n)))
        tw = max(160, int((inner - (cols - 1) * GAP) / cols) - OVER)
        th = int(tw / self.ASPECT)
        # Don't let a card grow taller than the viewport -- otherwise a single window
        # becomes one giant card you have to scroll to see.
        max_th = int(self._avail_h() * 0.62)
        if max_th > 60 and th > max_th:
            th, tw = max_th, int(max_th * self.ASPECT)
        return cols, tw, th

    def _on_resize(self, _w, alloc):
        # Re-fit the cards to the new width (debounced -- one rebuild after the drag).
        if abs(alloc.width - self._last_w) < 8:
            return
        self._last_w = alloc.width
        if self._resize_id:
            GLib.source_remove(self._resize_id)
        self._resize_id = GLib.timeout_add(120, self._resize_done)

    def _resize_done(self):
        self._resize_id = 0
        self.rebuild()
        return False

    def rebuild(self):
        for c in self.flow.get_children():
            self.flow.remove(c)
        wins = usable_windows(self.screen)
        n = len(wins)
        cols, self.TW, self.TH = self._layout(self._avail_w(), n)
        self.flow.set_max_children_per_line(cols)
        for w in wins:
            child = Gtk.FlowBoxChild()
            child.win = w
            app = w.get_application().get_name() if w.get_application() else ''
            child.search = ((w.get_name() or '') + ' ' + app).lower()
            child.add(make_card(w, self._click, self.TW, self.TH))
            self.flow.add(child)
        self.count.set_text(str(n) + (' window' if n == 1 else ' windows'))
        self.flow.show_all()
        self.flow.invalidate_filter()

    def _click(self, win, event):
        self._focus(win, event.time)
        return False  # let FlowBox still select the child

    def _on_activated(self, _flow, child):
        if getattr(child, 'win', None):
            self._focus(child.win, Gtk.get_current_event_time())

    def _focus(self, win, ts):
        try:
            win.activate(ts or Gtk.get_current_event_time())
        except Exception:
            pass

    def _on_search(self, entry):
        self.query = entry.get_text().strip().lower()
        self.flow.invalidate_filter()

    def _filter(self, child):
        return (not self.query) or (self.query in getattr(child, 'search', ''))

    def _on_key(self, _w, event):
        ctrl = event.state & Gdk.ModifierType.CONTROL_MASK
        alt = event.state & Gdk.ModifierType.MOD1_MASK
        if ctrl and event.keyval in (Gdk.KEY_w, Gdk.KEY_W):
            sel = self.flow.get_selected_children()
            if sel and getattr(sel[0], 'win', None):
                try: sel[0].win.close(Gtk.get_current_event_time())
                except Exception: pass
            return True
        if event.keyval == Gdk.KEY_Escape and self.search.get_text():
            self.search.set_text('')
            return True
        # type-to-filter: a printable key (not space) jumps into the search box
        if not ctrl and not alt and not self.search.has_focus():
            uni = Gdk.keyval_to_unicode(event.keyval)
            if uni:
                ch = chr(uni)
                if ch.isprintable() and ch != ' ':
                    self.search.grab_focus()
                    self.search.set_text(self.search.get_text() + ch)
                    self.search.set_position(-1)
                    return True
        return False

    def _schedule(self, *_a):
        if self._pending:
            return
        self._pending = True
        GLib.timeout_add(250, self._do_scheduled)

    def _do_scheduled(self):
        self._pending = False
        self.rebuild()
        return False

    def _tick(self):
        self.rebuild()
        return True


def main():
    ap = argparse.ArgumentParser(description='A live overview of your open windows.')
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument('--window', action='store_true', help='persistent, tabbable window (default)')
    grp.add_argument('--overview', action='store_true', help='summon a full-screen grid')
    grp.add_argument('--panel', action='store_true', help='a docked, always-on strip')
    ap.add_argument('--edge', choices=['top', 'bottom'], default='top', help='panel position')
    args = ap.parse_args()

    apply_css()
    if args.overview:
        Overview()
    elif args.panel:
        Panel(edge=args.edge)
    else:
        Dashboard()
    Gtk.main()


if __name__ == '__main__':
    main()
