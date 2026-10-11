# DCX for the settings window

`dccore.mrc` draws its settings window (`/dccore settings`) with **DCX** - Dialog Control eXtensions for mIRC - when it can load it, and with mIRC's own controls when it cannot.

- Project: https://github.com/twig/dcxdll
- Licence: BSD-3-Clause, in [LICENSE-DCX.txt](LICENSE-DCX.txt). DCX is not part of DCCore; it is shipped here unmodified so the window works without a separate download.

## The file

| | |
|---|---|
| Release | `3.1-git978` (https://github.com/twig/dcxdll/releases/tag/3.1-git978) |
| Build | `dcx310dev66git978Optimal.zip` |
| File | `dcx.dll`, unmodified, 32-bit (x86) |
| SHA-256 | `321e9ac2168734edd6ff6e48f4bd9540e9297f5e1360773edda9d554e620db12` |

The script checks that hash before it loads the file, and `tests/test_the_bundled_dcx_dll_is_the_pinned_release.py` checks the file, this table and the script against each other - so the dll cannot be swapped without all three changing together.

The release zip also holds `WebView2Loader.dll`. It is not shipped: `dcx.dll` loads it only for DCX's web control, which the window does not use, and imports nothing but Windows' own libraries otherwise.

## When the built-in window is used instead

`/dccore dcx` says which window is in use and why. The built-in one opens when:

- the option *Use the built-in settings window, not DCX* is on (the settings window's *This mIRC window* page);
- the mIRC version is below 7.45 - DCX 3.1 needs 7.45 or later;
- mIRC is the 64-bit build - `dcx.dll` is 32-bit and cannot be loaded into it;
- `dcx.dll` is missing, is not the file above (the hash differs), or will not load (an antivirus blocking it, say).

Both windows are built from the same layout (`scripts/mirc/settings_window_layout.py`) and edit the same settings.

## Updating DCX

Replace `dcx.dll` with the new release's file, then change the release, build and SHA-256 above **and** `dccore.dcx.sha256` in `dccore.mrc`. The test fails until all three agree.
