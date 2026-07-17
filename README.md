# x11

Small **X11 / Linux-desktop utilities**. Each subfolder is a self-contained package
you can run on its own -- no build step, standard system libraries.

## Packages

| Package | What it does |
| --- | --- |
| [`winview/`](winview/) | A live overview of your open windows -- an expose-style grid (`--overview`) or an always-on strip (`--panel`), with real window thumbnails. Python 3 + GTK 3 + libwnck. |

## Requirements

An **X11** session (not Wayland -- a normal app can't enumerate other windows on
Wayland), GTK 3, and `python3-gi`. Each package's own README lists specifics.

## License

[MIT](LICENSE)
