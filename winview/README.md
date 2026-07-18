# WinView -- a live overview of your open windows

A small GTK app for X11 / XFCE that shows your open windows as **live
thumbnails**, so you can see and jump to any of them at a glance. Three modes from
one script:

- **Window** *(default)* -- a persistent, normal window that shows in the taskbar and
  Alt+Tab, and stays open. Keyboard-navigable: **arrows** move, **Enter** focuses the
  selected window, **Ctrl+W** closes it, and **just start typing** to filter. Meant to
  stay open and **start on login** (see below).
- **Overview** (`--overview`) -- summon a full-screen expose grid, click a window to
  focus it, `Esc` to dismiss. Good bound to a hotkey.
- **Panel** (`--panel`) -- an always-on-top strip that refreshes live.

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
python3 winview.py               # persistent window (default)
python3 winview.py --overview            # full-screen expose grid (summon + dismiss)
python3 winview.py --panel               # docked strip, top of screen
python3 winview.py --panel --edge bottom
```

### Start on login

Copy the launcher into your autostart folder:

```sh
cp winview.desktop ~/.config/autostart/
```

(That's already done on this machine.) It launches the persistent window on login.
Remove `~/.config/autostart/winview.desktop` to stop it.

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
