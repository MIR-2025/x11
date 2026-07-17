# WinView -- a live overview of your open windows

A small GTK app for X11 / XFCE that shows your open windows as **live
thumbnails**, so you can see and jump to any of them at a glance. Two modes from
one script:

- **Overview** -- summon a full-screen grid, click a window to focus it, `Esc` to
  dismiss. Bind it to a hotkey for an expose-style switcher. *(default)*
- **Panel** -- an always-on-top strip that stays open and refreshes live; click a
  thumbnail to focus that window.

It uses `libwnck` to enumerate windows (with live open/close/focus events) and
captures each window's contents with `Gdk.pixbuf_get_from_window` -- which works
for background windows too, because the XFCE compositor keeps their pixmaps.

## Requirements

Standard on XFCE, and already present on this machine:

- **X11** session (not Wayland -- a plain app can't see other windows on Wayland)
- **A running compositor** (XFCE's is on by default; needed for thumbnails of
  windows that aren't on top). Check: *Settings -> Window Manager Tweaks ->
  Compositor -> Enable display compositing*.
- `python3-gi`, GTK 3, and `gir1.2-wnck-3.0`

## Run

```sh
python3 winview.py               # overview (default)
python3 winview.py --overview
python3 winview.py --panel               # docked strip, top of screen
python3 winview.py --panel --edge bottom
```

### Bind the overview to a hotkey (recommended)

*Settings -> Keyboard -> Application Shortcuts -> Add*:

- Command: `python3 /media/rwhitney/MIR/x11/winview/winview.py --overview`
- Then press the key combo you want (e.g. `Super+grave`, or a spare key).

Now that key pops the grid; click a window or hit `Esc`.

### Autostart the panel

*Settings -> Session and Startup -> Application Autostart -> Add* with command
`python3 /media/rwhitney/MIR/x11/winview/winview.py --panel`, or drop
`winview-panel.desktop` into `~/.config/autostart/`.

## How it works

- `Wnck.Screen` gives the window list plus `window-opened` / `window-closed` /
  `active-window-changed` signals, so both modes stay current.
- `capture_thumb(xid)` wraps each window's X id as a foreign `GdkWindow` and reads
  its pixbuf, scaled to a thumbnail. Minimized windows (which have no capturable
  contents) fall back to their **app icon**.
- Clicking a card calls `win.activate(event_time)` to focus that window (and raise
  its workspace).

## Limits and notes (v1)

- **Thumbnails need the compositor on.** With it off, only on-top windows would
  capture; the rest would fall back to icons.
- **Minimized windows** show their icon, not a preview -- a minimized window has no
  live contents to grab.
- **The panel floats** (keep-above) and does not yet reserve screen space, so
  windows can slide under it. It also rebuilds every ~4s, so you may see a slight
  flicker. Both are easy v2 fixes (strut reservation + in-place refresh).
- All windows from all workspaces are shown together; per-workspace grouping is a
  possible v2.

## Possible v2

- Per-workspace sections; a search/filter box.
- Keyboard navigation in the overview (arrows + Enter) and a close (`x`) button on
  each card.
- Panel: reserve space (`_NET_WM_STRUT`) and refresh thumbnails in place (no flicker).
