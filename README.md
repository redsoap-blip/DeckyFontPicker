# Decky Font Picker

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that applies any font installed on your Steam Deck to the Steam Big Screen UI — Home, MainMenu and the Quick Access Menu — without touching `~/homebrew/themes`, CSS Loader or the CEF debug port.

The repository ships **two builds of the same plugin**, which differ only in UI language:

| Build | Release asset | Plugin folder | UI language |
| --- | --- | --- | --- |
| Chinese | `DeckyFontPicker-1.0.1.zip` | `DeckyFontPicker/` | 简体中文 |
| English | `FontPickerEN-1.0.1.zip` | `FontPickerEN/` | English |

Both are version **1.0.1** and share identical behaviour: same font discovery, same section rules, same sorting, same controls.

## Features

- Lists every font family reported by `fc-list`, so anything registered with fontconfig appears automatically (SteamOS packages, `~/.local/share/fonts`, `~/.fonts`, …).
- 11 sections: `Favorites`, `Chinese Coverage`, `Serif`, `Serif (zh)`, `Sans`, `Sans (zh)`, `Mono`, `Mono (zh)`, `Others`, `Others (zh)`, `All Fonts`.
  - Base sections exclude fonts whose `lang` includes `zh`; the `(zh)` sections contain exactly those Chinese fonts. The two halves are **mutually exclusive**, so no family is listed twice (favourites excepted). Empty sections are hidden.
- Sorted by Chinese coverage first, then by lower-cased family name.
- Detects whether the selected family actually contains Chinese glyphs and falls back to a Chinese-capable family when it does not, so menu text never turns into tofu.
- Serves the font from a loopback-only HTTP server and injects one `<style>` block through Decky's own `inject_css_into_tab`, then re-applies it across Steam re-renders.
- Injects into three targets: Home, `MainMenu_uid*` and `QuickAccess_uid*`.
- Per-section collapse state, a separate collapsible "Current font" block, live injection status per target, favourites (**X**), and "load more" paging in steps of 60.

## Install

1. Download one of the ZIPs from the [Releases](../../releases) page.
2. In Decky Loader open **Settings → Developer → Install Plugin from ZIP**, or extract the archive into `~/homebrew/plugins/`.
3. Reload Decky (or restart `plugin_loader.service`) and open the Quick Access Menu.

Each ZIP contains exactly:

```
<folder>/plugin.json
<folder>/main.py
<folder>/face_extract.py
<folder>/dist/index.js
```

Requirements: Decky Loader 3.x (`decky-frontend-lib` 3.25 is used at runtime) and Python 3 with `fontTools` available to the plugin loader.

## Only one build at a time

Because both builds inject CSS into the same Steam tabs, **enabling both at once makes them fight over the same style block**, and disabling one can remove the style injected by the other.

- The folders differ (`DeckyFontPicker/` vs `FontPickerEN/`), so they can be installed side by side without overwriting each other.
- Each keeps its own settings and font cache under `~/homebrew/settings/<folder>/` and `~/homebrew/data/<folder>/`.
- Keep exactly **one** of them enabled in the Decky plugin list; disable the other.

## Font sources and limitations

- Fonts come from `fc-list` output; nothing is bundled in this repository. After installing a font, run `fc-cache -f` and reopen the panel.
- `.ttf`, `.otf`, `.ttc` and `.otc` are supported. For collections only the used face is extracted (via `fontTools`) and cached under the plugin runtime directory.
- Section placement is heuristic: `Mono` uses fontconfig `spacing` (100 / 110) or monospace keywords, `Serif` / `Sans` use family-name keywords, and everything else falls into `Others`. A font can therefore be classified differently than you expect — the section note in the panel states the rule used.
- The plugin does not modify system fonts or fontconfig; it only injects a CSS `@font-face` override for the running Steam session.

## Controls

| Input | Action |
| --- | --- |
| A / click | Apply the focused font |
| X | Add / remove favourite |
| Y | Collapse the current section |
| B | Back |

## Repository layout

```
DeckyFontPicker/     Chinese build source (plugin.json, main.py, face_extract.py, dist/index.js)
FontPickerEN/        English build source (same layout)
README.md            this file
LICENSE              MIT
.gitignore
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Honchane.
