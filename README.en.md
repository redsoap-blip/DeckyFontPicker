[简体中文](README.md) | **English**

# Decky Font Picker

[![latest release](https://img.shields.io/github/v/release/redsoap-blip/DeckyFontPicker?label=release&color=blue)](../../releases)
[![License](https://img.shields.io/github/license/redsoap-blip/DeckyFontPicker)](LICENSE)
[![Decky Loader](https://img.shields.io/badge/Decky%20Loader-3.x-1a9fff)](https://github.com/SteamDeckHomebrew/decky-loader)

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that applies any font already installed on your Steam Deck to the Steam Big Screen UI — Home, MainMenu, the Quick Access Menu, and notification toasts such as friend-online popups. It never writes to `~/homebrew/themes` and does not need CSS Loader: styles go in through Decky's own `inject_css_into_tab` and are removed again when you restore the default.

**Current version: 1.4.19.** One package, four UI languages — the panel follows the Steam UI language. `fontTools` 4.68.0 is bundled, so there is nothing else to install.

## Install

1. Download `DeckyFontPicker-<version>.zip` from the [latest release](../../releases).
2. Either install it from Decky Settings → Developer → Install Plugin from ZIP, or extract the archive into `~/homebrew/plugins/`.
3. Reload Decky (or restart `plugin_loader.service`) and open the Quick Access Menu.

The ZIP contains exactly:

```
DeckyFontPicker/plugin.json
DeckyFontPicker/main.py
DeckyFontPicker/face_extract.py
DeckyFontPicker/dist/index.js
DeckyFontPicker/package.json
DeckyFontPicker/vendor/fontTools/...
DeckyFontPicker/vendor/LICENSE-fontTools
```

| Requirement | Notes |
| --- | --- |
| Decky Loader | 3.x; `decky-frontend-lib` 3.25 is used at runtime |
| Python | System Python 3, used to run `face_extract.py` |
| CEF Remote Debugging | This is a requirement of **Decky Loader itself** (Steam Settings → Developer → "CEF Remote Debugging"). If Decky loads, it is already on — there is nothing extra to configure for the plugin. Tab discovery, liveness checks and toast listening reuse that port |
| `fontTools` | 4.68.0, vendored under `vendor/`; nothing to install system-wide |

## Features

### Font list and filtering

- The list comes from `fc-list`: every family fontconfig knows about shows up (distro packages, `~/.local/share/fonts`, `~/.fonts`, ...). Bitmap formats the browser cannot use (PCF / BDF / Type1) are filtered out. `fc-list` results are cached for 10 seconds, so reopening the panel does not rescan.
- **Font names rendered in their own typeface**: each row's family name is set in the font itself (always on, no switch) — a monospace's name is one glyph per cell, a serif's name carries serifs, tell them apart at a glance. It rides the same lazy tiny-subset pipeline as the preview samples (name glyphs join the subset, still a few KB); until it loads, or for glyphs the font lacks, names fall back per-character to the default UI font with no tofu.
- The list is ordered by install time: non-system fonts come first, newest on disk first (ties broken by family name), while system fonts (`/usr/share`, `/usr/local/share`) sit at the bottom in the backend's default order. There is no toggle (install time is the earliest file mtime of the family).
- Sections, in the order the current build renders them: `All Fonts`, `Favorites`, `History`, `Serif`, `Sans`, `Mono`, `Others`. A style section with no fonts is hidden entirely; `All Fonts`, `Favorites` and `History` are always present (an empty history reads "No history yet").
- **History**: every font you pick is recorded — most recent first, de-duplicated, no limit, persisted in `state.json`; uninstalled fonts are skipped. `Clear font history` in Advanced wipes it in one go.
- Language pills: `All Fonts` and the four style sections each show a row of `Latin` / `Chinese` / `Japanese` / `Korean` pills once expanded, with per-language counts; click them or press left / right on the gamepad. There is no "no filter" state — Latin is the default. Every family belongs to exactly one pill: a region token in the family name (JP / KR / SC / TC / HK / CN) wins, otherwise zh > ja > ko, and everything else counts as Latin.
- Row markers: `Chinese` / `No Chinese, auto fallback`, `Bold` / `No bold (synthetic)`, `Monospace`, `Favorite`, `In use`; a search hit on an alias adds an `Alias: xxx` line.
- Instant search over family names and aliases; the `{N} fonts` count is always shown above the list and shows the hit count while searching. Each section renders 60 entries first, then offers `Load more ({N} left)`. No hit shows "No matching fonts (searched both names and aliases)."; clearing the query returns to the sectioned view.
- Section placement is heuristic and mutually exclusive, tested in the order `Mono` → `Serif` → `Sans` → `Others`: `Mono` first checks fontconfig `spacing` (90 dual / 100 mono / 110 charcell), then a family name containing `mono` / `等宽` / `等寬` / `等幅` / `고정폭` / `fixed` and similar; `Serif` matches serif / song / ming / times / 楷 and similar, excluding names containing `sans`; `Sans` matches sans / grotesk / gothic / 黑 and similar; the rest go to `Others`. The note under each of the four style sections states the rule in use (`All Fonts` has no note).
- Sections start collapsed; press A / click the header to expand, and Y inside a section collapses it in place and returns focus to the header.

### Injection and the status card

- Three injection targets: Home, `MainMenu_uid*` and `QuickAccess_uid*`. Home is matched by candidate titles (`Steam 大屏幕模式` / `Steam Big Picture Mode` / `Steam` / `SP`) inside the CEF tab list; the two dynamic targets first try the known uid and otherwise take the highest current uid from the CEF tab titles.
- `notificationtoasts_*` popups are separate windows Steam creates on demand. They are driven by browser-level CEF websocket events (created / title changed / destroyed): inject on appearance, remove on teardown — event-driven, zero polling.
- All injection goes through Decky's own `inject_css_into_tab`, with exactly one style per tab; when Steam rebuilds a page, the style comes back in the next injection window.
- Status card: the current font on top (`Latin:` / `CJK:` rows when split is on, with "Follow system" for an unset CJK slot), then one row per target showing `Injected` / `Not injected`, with a refresh icon on the right. The whole card is one focusable item — A / click re-injects the current font. The summary on the right reads `Manual injection · time`, `Auto injection · time` or `Injection failed`; before any injection it reads `Not injected yet`. If the backend also has an auto-injection record (and the summary is not already the auto one), a second grey `Auto injection · time` line appears.
- If the injection at startup failed and a later automatic pass succeeded, the summary turns green "auto injection" instead of leaving a stale error.

### Split by language

- With `Split by language` on, A first opens an "Apply to Latin / Apply to Chinese" menu, pre-selected by the font's language: zh / ja / ko preselect Chinese, everything else preselects Latin. B cancels without writing anything.
- Each slot is extracted and embedded separately. The Latin slot can have CJK and private-use glyphs stripped (the mask covers CJK unified ideographs, kana, Hangul, full-width forms, PUA and more), so CJK text always uses the CJK slot; Western punctuation (curly quotes, ellipsis, middle dot) is always rendered by the Latin slot.
- When the CJK slot is empty and the Latin slot really was stripped, the full version of the Latin font is registered under the internal family `DeckyFontPicker-Full` to fill the CJK position of the stack.
- Turning split off clears both slots (including the injection bookkeeping) and tears the current injection down; turning it back on leaves both slots empty, so you pick again.
- If stripping fails for the Latin font, the original font is used and a red warning appears under the card: it may cover the CJK slot, and a Latin-only font fixes it.

### Advanced

- `Font preview samples`: a three-notch slider — 0 off, 1 Compact (a single-line sample per row), 2 Detailed (regular and bold samples); low-memory devices should use Off or Compact. Samples are served as tiny subsets keeping only the glyphs the sample text uses (a few KB), never the whole face.
- `Expand preview on focus`: when on, focusing a font row expands a large Latin / Chinese / Japanese / Korean sample inside the row. The old three-level control (including the inline layout) has been folded into this single switch; existing settings migrate automatically.
- `Disable synthetic bold`: on by default; adds `font-synthesis-weight: none` to the injected rule so fonts without a bold face are no longer emboldened synthetically. Independent of split and always visible.
- `Split by language`: the master switch described above.
- `Restore Steam default font`: removes the injection from the three targets and the toasts and clears both slots, without touching any switch. It is the standalone button near the bottom of the panel.
- `Copy log`: reads the tail of the newest log (up to 200 lines) and puts it on the clipboard for bug reports.
- A read-only debug-port line: `Debug port <port> · N tabs`, replaced by a red note while the port is not ready. The panel has no CEF toggle — Decky Loader guarantees the port and the plugin only reports its state.

## Controls

| Input | Action |
| --- | --- |
| A / click | Font row: apply the focused font (with split on, opens the slot menu). Section header: expand / collapse |
| X | Add / remove favourite |
| Y | Collapse the current section |
| Left / right | Switch the language pill of the current section |
| B | Back; cancels inside the slot menu |

## FAQ

**A font I installed is missing from the list.** fontconfig does not know it yet. Run `fc-cache -f` and reopen the panel; PCF / BDF / Type1 bitmap fonts are never listed.

**A Chinese or Japanese font has no effect, text falls back to the default.** With split off, a family without Chinese glyphs gets an installed CJK family appended to the fallback chain (Noto Sans CJK and friends first, then any family with Chinese); with split on, CJK text is served by the CJK slot only, so make sure that slot has a font. If no usable CJK font is installed at all, Steam's own fallback is all that is left.

**It says the CEF debug port is not ready.** That only happens during the first few dozen seconds after Steam starts, or while steamwebhelper is reloading. CEF Remote Debugging is a hard prerequisite of Decky Loader — **if Decky loads, it is already on**, so there is no switch to go looking for. The plugin reads the port from steamwebhelper's command line, falls back to 8080 / 8081, and retries within its 30-second window; if the window passes without an injection, press the status card to re-inject.

**Are large font files compressed?** Only a face produced by extraction or stripping, at most 3 MiB, is converted to woff2, and only when `brotli` is available; full CJK faces normally stay `.ttf`. A single-file font that is not stripped is served straight from its system path with no conversion.

**Does it modify system fonts or fontconfig?** No. It only injects a CSS `@font-face` override into the running Steam session; it never touches system font directories or fontconfig.

**How do I remove everything?** Press `Restore Steam default font` in the panel. To clean up traces as well, delete `~/homebrew/data/DeckyFontPicker/` (extracted cache), `~/homebrew/settings/DeckyFontPicker/state.json` (selection, favourites, history) and `~/homebrew/logs/DeckyFontPicker/` (logs).

**Can it run alongside `FontPickerEN/`?** No. Both inject into the same Steam tabs, so only one may be enabled at a time.

**Do fonts survive leaving Big Screen for the desktop?** The desktop main window is no longer re-injected automatically; the styles disappear with the Big Screen pages, and the UI-mode event re-injects them when you go back to Big Screen. User actions still inject as usual.

## How it works

Fonts take a single "extract and embed" path: a single-file font that is not stripped is referenced from its system path, while `.ttc` / `.otc` files are opened with `fontTools` (`TTCollection`) to extract the exact face in use, cached under `~/homebrew/data/DeckyFontPicker/fonts/`, where the directory is capped at the 9 most recently used files (in-use files count towards the cap and are always kept). Files are served by an on-demand HTTP server bound to `127.0.0.1` only, on a random port behind a random token, so the injected `@font-face` always points at the real face and never depends on fontconfig resolving a family name.

The write path is deliberately conservative: one serial lock per tab, old styles owned by the plugin removed before the single new one is written, and bookkeeping handled by Decky itself; `state.json` lives in `~/homebrew/settings/DeckyFontPicker/` and logs in `~/homebrew/logs/DeckyFontPicker/`.

Injection runs in a bounded window: a Big Screen UI-mode change (and the start-up restore of a saved font) opens it, it waits 2 s, then retries with 0.5 → 1 → 2 → 4 → 8 s backoff for at most 30 s and gives up; the next UI-mode change opens a new window, while a user action runs the injection again directly. If it is still not injected when the window expires, press the status card to re-inject or reopen the Quick Access Menu to start a new window. Since the current release the ~180 lines of "CEF closed" fallback (a 1..320 `get_tab_id` scan, second-pass validation, scan budget) are gone entirely; the archived code is in [docs/LEGACY_FALLBACK.md](docs/LEGACY_FALLBACK.md). When the port is not ready it never falls back to blind title probing — all three targets wait for the retry window.

## Compatibility and known limitations

- Fonts come from `fc-list`; apart from `fontTools` the repository bundles no fonts.
- `.ttf`, `.otf`, `.ttc` and `.otc` are supported; for collections only the face actually used is extracted.
- System fonts and fontconfig are never modified — the plugin only injects styles into the current Steam session.
- Returning to the desktop no longer auto-injects into the desktop main window; injection on entering Big Screen and on user actions is unchanged.
- `FontPickerEN/` (the English-only package frozen at 1.2.0) cannot be enabled at the same time as the current build.
- CEF Remote Debugging is a prerequisite of Decky Loader itself, inherited by the plugin — the combination "Decky works but CEF is off" does not exist; the plugin merely waits for the port during Steam's start-up.

## Repository layout

```
DeckyFontPicker/     source of the current build (plugin.json, main.py, face_extract.py, dist/index.js, package.json, vendor/)
FontPickerEN/        English-only package frozen at 1.2.0, kept so old releases stay reproducible; must not run next to the new build
README.md            Chinese README
README.en.md         this file
docs/                LEGACY_FALLBACK.md and other archived docs
LICENSE              MIT
.gitignore
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Honchane.

The package bundles [fontTools](https://github.com/fonttools/fonttools) 4.68.0, also MIT licensed — see `vendor/LICENSE-fontTools`.
