# The theme's DLLs

`dccore.mrc` uses these two for its dark theme and its icons (`/dccore theme`, `/dccore icons`, the section "Theme and icons" in the script). Both are DCCore's own, built from the sources in `src/`. Without them the script works and looks as it did before the theme: a missing `dccore_ui.dll` leaves the dialogs light, a missing `dccore_icons.dll` leaves the menus and the control panel's page list without icons.

## The files

| | `dccore_ui.dll` | `dccore_icons.dll` |
|---|---|---|
| What | Dark title bar, background and controls for the script's dialogs; the icons beside the script's own menu items | 52 icons (16, 20, 24, 32 and 48 px), resources only - no code is run |
| Source | `src/dccore_ui.c` | `src/make_icons.py` (draws them; `icons-preview.png` shows all of them) |
| Build | 32-bit (x86), mingw-w64 | 32-bit (x86), mingw-w64 `windres` |
| SHA-256 | `4b29be657370837c7d7b5af9d089371d252167a8b8d67b7d098f5ffec9c2401f` | `9b3d5002aa24a44309038501686ae74292c550f8158c99fcf93fdcabee69e4ee` |

The script checks `dccore_ui.dll` against its hash (`dccore.theme.uisha256`) before it loads it, as it checks `dcx.dll`: a file that is not this build is not run. `dccore_icons.dll` is only read for its pictures, by DCX and by Windows.

## `dccore_ui.dll`

Calls, through mIRC's `$dll`:

- `ApplyTheme <hwnd> <mode> [x]` - mode 1 dark, 0 back to the system's look. `x` means the dialog is marked by DCX: DCX paints its background and its own controls (they carry the window property `dcx_cthis`), so only mIRC's controls and the title bar are touched. Every subclass is taken off again before the DLL is unloaded.

  Most of mIRC's controls take the dark colours from the dialog (`WM_CTLCOLOR*`) and Windows' dark themes (`DarkMode_Explorer`, `DarkMode_CFD`, `DarkMode_ItemsView`). Some would not, and are painted by the DLL: check boxes and radio buttons (a dark glyph of its own - Windows' is a white box), push buttons (Windows draws a disabled one light grey), group boxes, list headers (Windows' dark header has black text, which custom draw cannot change), and disabled text (Windows draws it embossed, white with a grey shadow, whatever the colours). Glyphs and buttons are drawn four times as large and averaged down, for smooth edges. Clicks, keys and state stay the controls' own.

  Text without ClearType: ClearType tints each letter's edges red and blue, and light text on a dark background comes out thin and blurred. In the dark theme every control of the dialog, mIRC's and DCX's, gets a twin of its font with `ANTIALIASED_QUALITY` - grey smoothing where the font asks for it, crisp pixels where it does not (Tahoma at mIRC's usual sizes). mIRC's controls share one twin per font; a DCX control gets one of its own, since DCX deletes a control's font when it changes or ends it. The light theme, an unload, or the control's end gives each control its own font back. DCX's texts draw with the font DCX was given (`xdid -f`), not the window's, so in the dark theme the script gives each DCX text and table its font with DCX's flag `a` (anti-aliased) as it makes it (`dccore.sw.fontflags`, `dccore.sw.dcxaa`).
- `MenuIcons <icon file>|off` - icons beside this script's menu items and nobody else's. A hook on mIRC's thread (`WH_CALLWNDPROCRET`) sees each `WM_INITMENUPOPUP` after mIRC has filled the menu; an item whose text begins with a number and `$chr(11)` (the script's `$dccore.mi`) loses that mark and gets that icon as its menu bitmap (`hbmpItem`, 32-bit with alpha). Every other item and every other menu is left as mIRC made it: no owner-draw, no colours. An item another program draws itself is skipped.
- `ThemeMenus 1|0` - Windows' dark popup menus for the whole mIRC process (uxtheme's unnamed functions 135 and 136). The script does not call it: it would change every menu in mIRC, not only this script's.
- `Version` - `dccore_ui 1.5`.

The dark dialogs are not applied while mIRC itself is in dark mode (mIRC 7.79 and later, `$darkmode`): mIRC then draws its dialogs dark already. The menus follow mIRC: dark in its own dark mode, light otherwise.

Build:

```
i686-w64-mingw32-gcc -O2 -Wall -shared -s -o dccore_ui.dll dccore_ui.c \
    -Wl,--kill-at -Wl,--no-insert-timestamp -static-libgcc \
    -lcomctl32 -luxtheme -ldwmapi -lgdi32 -luser32
```

`--no-insert-timestamp` makes the build the same file every time, so the hash above can be checked against a build of your own.

## `dccore_icons.dll`

The icons' order is their number, and the script's `dccore.ico.names` must list them in the same order: `app console settings script status slots queue dlqueue uptime version help lists fetch ask purge library rebuild audio ban unban clear ignore moveup movedown update rehash stop connect disconnect pair unpair trust chat downloads panel font dashboard cancel again user speed search pack folder hash palette megaphone mail bug key home shield`. A new icon goes at the end, so the others keep their numbers.

Build:

```
python3 make_icons.py build          # the .ico files, dccore_icons.rc, preview.png
cd build
i686-w64-mingw32-windres dccore_icons.rc -O coff -o res.o
i686-w64-mingw32-gcc -shared -s -o dccore_icons.dll ../dll.c res.o -Wl,--no-insert-timestamp -static-libgcc
```

`src/dll.c` is an empty `DllMain` returning `TRUE`; the icons are all in the resources.

## Where they show

- **Menus**: with `/dccore icons on` the script starts `MenuIcons` and gives each of its items its number (`$dccore.mi`). Only those items get a picture; the menus are otherwise mIRC's own. DCX is not involved.
- **The control panel's page list**: just after the window is made, in a timer of its own, the DCX listview gets the whole file (`xdid -w +n -1`) and its rows are filled again, each page with its icon by name (`dccore.theme.pagemap`). A page the layout gains later has no icon until it is named there.
- **Without DCX** - not loadable, or the built-in window chosen in the mIRC settings - the page list has no icons; the menu icons and the dark dialogs only need `dccore_ui.dll`.
