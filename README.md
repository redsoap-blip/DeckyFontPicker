**English** | [简体中文](README.zh-CN.md)

# Decky Font Picker

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that applies any font installed on your Steam Deck to the Steam Big Screen UI — Home, MainMenu and the Quick Access Menu — without touching `~/homebrew/themes`, CSS Loader or the CEF debug port.

**Current version: 1.4.2.** One build, four UI languages — the panel follows the Steam UI language and ships 简体中文 / English / 日本語 / 한국어 dictionaries. `fontTools` 4.68.0 is vendored, so there is nothing else to install.

| Release asset | Plugin folder | UI languages | `fontTools` |
| --- | --- | --- | --- |
| `DeckyFontPicker-1.4.2.zip` | `DeckyFontPicker/` | 简体中文 / English / 日本語 / 한국어 (auto) | vendored under `vendor/` |

## Install

1. Download `DeckyFontPicker-1.4.2.zip` from the [Releases](../../releases) page.
2. In Decky Loader open **Settings → Developer → Install Plugin from ZIP**, or extract the archive into `~/homebrew/plugins/`.
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

Requirements: Decky Loader 3.x (`decky-frontend-lib` 3.25 is used at runtime) and Python 3. Nothing needs to be installed system-wide.

## Features

- Lists every font family reported by `fc-list`, so anything registered with fontconfig appears automatically (SteamOS packages, `~/.local/share/fonts`, `~/.fonts`, …).
- Sections: `Favorites`, then `Serif`, `Sans`, `Mono`, `Others`, and finally `All Fonts`. Empty sections are hidden.
  - Language capsules — `Latin` / `Chinese` / `Japanese` / `Korean` — filter the list by the languages a face actually covers; the row for each font shows its `lang` coverage (Chinese tags first) plus `含中文` / `无中文` and bold / monospace markers.
  - Section placement is heuristic: `Mono` uses the fontconfig `spacing` field (100 / 110) or monospace keywords, `Serif` / `Sans` use family-name keywords, everything else falls into `Others`. The section note in the panel states the rule used.
- Instant search over family names and aliases, with paging in steps of 60.
- Favourites (**X**), per-section collapse (**Y**), and a collapsed "Current font" block that summarises the active font and its injection state.
- Injects into three targets: Home, `MainMenu_uid*` and `QuickAccess_uid*`, through Decky's own `inject_css_into_tab`, and re-applies the style across Steam re-renders.
- Status card: three target rows plus a refresh icon form one focusable card — press A / click it to re-inject the current font. A failed injection that later succeeds on its own switches the card to a green "auto re-injected" state instead of leaving a stale error.
- **Split by language** (Advanced): pick a Latin font and a CJK font separately. Each face is extracted and embedded, and a Latin slot font can have its CJK glyphs stripped so the CJK slot always wins for Chinese/Japanese/Korean text.
- **Disable synthetic bold** and **CJK punctuation** toggles for the font faces, plus **Restore Steam default font**.
- **CEF fast mode** (optional): uses the Chrome DevTools endpoint for faster tab discovery. Requires Steam Settings → Developer → CEF Remote Debugging; when the port is unavailable the plugin falls back to the regular probe automatically and font injection is unaffected.
- **Copy log** button in the Advanced section for bug reports.

## How injection works

The plugin extracts the exact face you picked with `fontTools` (`TTCollection` for `.ttc` / `.otc`), caches it under `~/homebrew/data/DeckyFontPicker/fonts/`, and serves it from a loopback-only HTTP server started on demand. The injected `@font-face` rules therefore point at the same face every time, regardless of how fontconfig would resolve the family name. An extracted face is compressed to `woff2` only when it is ≤ 3 MiB and `brotli` happens to be available; otherwise the plain `.ttf` is kept, which is always the case for full CJK faces.

The write path is deliberately conservative: styles are added and replaced one tab at a time, injection bookkeeping goes through Decky, and a single serial lock keeps concurrent tabs from racing. `state.json` lives in `~/homebrew/settings/DeckyFontPicker/`; logs are under `~/homebrew/logs/DeckyFontPicker/`.

## Controls

| Input | Action |
| --- | --- |
| A / click | Apply the focused font (with split enabled, opens the slot menu: apply to Latin / apply to Chinese) |
| X | Add / remove favourite |
| Y | Collapse the current section |
| B | Back |

## Repository layout

```
DeckyFontPicker/     current build source (plugin.json, main.py, face_extract.py, dist/index.js, package.json, vendor/)
FontPickerEN/        legacy 1.2.0 English-only build, kept for the old releases (not part of 1.4.2)
README.md            this file (English)
README.zh-CN.md      Chinese version of this file
LICENSE              MIT
.gitignore
```

### About `FontPickerEN/`

`FontPickerEN/` is the English-only build that shipped alongside the Chinese build up to **1.2.0**, when the plugin used one package per UI language and offered both a by-name and an extract delivery mode. From the 1.3.x–1.4.x line the plugin is a **single package with four UI languages and a single delivery path (extract & embed)**, so `FontPickerEN/` is frozen at 1.2.0: it is not rebuilt for 1.4.2 and its old release assets still refer to 1.2.0. It is kept only so the 1.2.0 downloads stay reproducible. Do not install it next to the 1.4.2 build — both inject into the same Steam tabs, so only one may be enabled.

## Font sources and limitations

- Fonts come from `fc-list` output; nothing except `fontTools` is bundled. After installing a font, run `fc-cache -f` and reopen the panel.
- `.ttf`, `.otf`, `.ttc` and `.otc` are supported. For collections, only the used face is extracted and cached.
- The plugin does not modify system fonts or fontconfig; it only injects a CSS `@font-face` override for the running Steam session.
- Restoring the Steam default font from the panel removes the injected styles; no Steam or Decky restart is needed.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Honchane.

The build bundles [fontTools](https://github.com/fonttools/fonttools) 4.68.0, also MIT licensed — see `vendor/LICENSE-fontTools`.
