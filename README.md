**English** | [简体中文](README.zh-CN.md)

# Decky Font Picker

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that applies any font installed on your Steam Deck to the Steam Big Screen UI — Home, MainMenu and the Quick Access Menu — without touching `~/homebrew/themes`, CSS Loader or the CEF debug port.

The repository ships **four builds of the same plugin**: two UI languages × two packaging variants. All four are version **1.2.0** and share identical behaviour — same font discovery, same sections, same sorting, same controls, same two delivery modes. They differ only in UI language and in whether `fontTools` is bundled.

| Build | Release asset | Plugin folder | UI language | `fontTools` |
| --- | --- | --- | --- | --- |
| Chinese, bundled | `DeckyFontPicker-1.2.0-with-fonttools.zip` | `DeckyFontPicker/` | 简体中文 | vendored under `vendor/` |
| Chinese, no dependency | `DeckyFontPicker-1.2.0.zip` | `DeckyFontPicker/` | 简体中文 | not bundled |
| English, bundled | `FontPickerEN-1.2.0-with-fonttools.zip` | `FontPickerEN/` | English | vendored under `vendor/` |
| English, no dependency | `FontPickerEN-1.2.0.zip` | `FontPickerEN/` | English | not bundled |

The two variants of the same language use the **same plugin folder and the same plugin name**, so they are drop-in replacements for each other: installing one overwrites the other, and **only one of them can be installed at a time**. The two languages use separate folders and names, so they do not overwrite each other — but every build injects into the same Steam tabs, so only one build overall may be enabled (see [Only one build at a time](#only-one-build-at-a-time)).

## Two delivery modes, switchable in the panel

Every 1.2.0 package carries **both** delivery modes and lets you switch at runtime — there is no separate build per mode:

| Mode | Panel label (zh / en) | What it does | Needs `fontTools` |
| --- | --- | --- | --- |
| By-name reference | `按名引用` / `By name` | Injects `@font-face { src: local("<family>") }` and lets Chromium resolve the family through the host fontconfig. No extraction, no cache, no HTTP server. | no |
| Extract & embed | `抽取嵌入` / `Embedded` | Extracts the exact face you picked with `fontTools` (`TTCollection` for `.ttc` / `.otc`), caches it under `~/homebrew/data/<folder>/fonts/`, and serves it from a loopback-only HTTP server. | yes |

- **By-name is the default.** It is the cheapest path and works for every `.ttf` / `.otf`; read [By-name mode](#by-name-mode) for its prerequisites.
- **Switch with one click.** The mode row sits at the top of the panel and is always visible and focusable; press A / click it to toggle between the two modes. The row states which mode is active and why embedded mode is unavailable when `fontTools` cannot be found.
- **The switch is manual only.** The probe verdicts below never change the mode on their own — a miss is reported, not silently "fixed" by switching.
- Settings are per plugin folder, so the chosen mode survives a Decky reload.

## Features

- Lists every font family reported by `fc-list`, so anything registered with fontconfig appears automatically (SteamOS packages, `~/.local/share/fonts`, `~/.fonts`, …).
- Sections: `Favorites`, `Chinese Coverage`, then `Serif`, `Sans`, `Mono`, `Others` — each with `(zh)`, `(ja)` and `(ko)` subcategories — and finally `All Fonts`. Empty sections are hidden.
  - The base section of each kind excludes Chinese, Japanese and Korean fonts; the `(zh)` / `(ja)` / `(ko)` subcategories hold exactly those fonts. The halves are **mutually exclusive**, so no family is listed twice (favourites excepted).
  - **Primary language decides the subcategory.** A region token in the family name wins: `JP` → `(ja)`, `KR` → `(ko)`, `SC` / `TC` / `HK` / `CN` → `(zh)`. That is why `Noto Sans CJK JP` lands in `(ja)` while `Noto Sans CJK SC` lands in `(zh)`. Only when the name carries no region token does the coverage rule apply, in the order `zh > ja > ko`.
  - The fontconfig `lang` list cannot serve as the primary signal: `fc-list` returns it alphabetically, so its first entry is meaningless as a "main language". Only the family-name suffix is used, with the coverage rule as the fallback.
- Sorted by Chinese coverage first, then by lower-cased family name.
- Detects whether the selected family actually contains Chinese glyphs and reports the verdict per injection target, so menu text never silently turns into tofu. The plugin does **not** switch fonts for you.
- Injects into three targets: Home, `MainMenu_uid*` and `QuickAccess_uid*`, through Decky's own `inject_css_into_tab`, and re-applies the style across Steam re-renders.
- Panel: the mode row is persistent and clickable, the "Current font" block collapses to a single summary row that still shows the hit / miss / probe-failed state, and no debug output is rendered.
- Per-section collapse state, favourites (**X**), and "load more" paging in steps of 60.

## Install

1. Download one of the ZIPs from the [Releases](../../releases) page.
2. In Decky Loader open **Settings → Developer → Install Plugin from ZIP**, or extract the archive into `~/homebrew/plugins/`.
3. Reload Decky (or restart `plugin_loader.service`) and open the Quick Access Menu.
4. On SteamOS, pick a build **with fontTools** unless `fontTools` is already installed system-wide on that device. The no-dependency build still runs in by-name mode, but the mode row will report embedded mode as unavailable.

Each ZIP contains exactly:

```
# no-dependency build
<folder>/plugin.json
<folder>/main.py
<folder>/face_extract.py
<folder>/dist/index.js

# with-fonttools build (adds the vendored library, 4,700,553 bytes / 354 files)
<folder>/plugin.json
<folder>/main.py
<folder>/face_extract.py
<folder>/dist/index.js
<folder>/vendor/fontTools/...
<folder>/vendor/LICENSE-fontTools
```

Requirements: Decky Loader 3.x (`decky-frontend-lib` 3.25 is used at runtime) and Python 3. The no-dependency build additionally needs `fontTools` importable by the system `python3` for embedded mode; the with-fonttools build needs nothing else. By-name mode works in every build without `fontTools`.

## Chinese fonts on SteamOS need fontTools for embedded mode

On SteamOS almost every Chinese font is a **collection** (`.ttc` / `.otc`) that packs several faces into one file — `NotoSerifCJK-Regular.ttc` for example contains 5 faces (JP, KR, SC, TC, HK), and "Noto Serif CJK SC" is face **index 2** of that file. Browsers cannot use a whole collection as an `@font-face` source, so embedded mode extracts the single face you picked with `fontTools` (`TTCollection`) and caches it under `~/homebrew/data/<folder>/fonts/`.

Without `fontTools` that extraction cannot run. In 1.2.0 this no longer breaks the plugin: the mode row disables embedded mode and states the reason, and by-name mode keeps working. If you force embedded mode without `fontTools`, applying a CJK font fails and the log under `~/homebrew/logs/<folder>/` ends with a line like:

```
准备字体 'Noto Serif CJK SC' 失败: 抽取 NotoSerifCJK-Regular.ttc#2 失败: fontTools 不可用: No module named 'fontTools'
```

### Fix 1 — use a build with fontTools (recommended, zero dependencies)

Install `DeckyFontPicker-1.2.0-with-fonttools.zip` or `FontPickerEN-1.2.0-with-fonttools.zip`. `fontTools` 4.68.0 (pure Python, MIT) is vendored under `vendor/`, and `main.py` puts `<plugin dir>/vendor` at the front of `sys.path` (and into `PYTHONPATH` for the helper subprocess) before anything imports `fontTools`. Because the path is derived from the plugin's own location, the plugin keeps working if you rename or move its folder, and it never depends on the user site or on system packages.

Cost: 4,700,553 bytes unpacked for the `fontTools` tree (≈ 5.7 MB on disk with filesystem block overhead), 1,245,589 bytes for the Chinese ZIP and 1,243,066 bytes for the English one. `brotli` is **not** included; see below.

### Fix 2 — install fontTools into the user site by hand

No `pip`, no `sudo`, no developer mode, and no system packages are involved. Copy-paste:

```bash
# 1) download the wheel (official PyPI file, 1,248,357 bytes)
curl -L -O https://files.pythonhosted.org/packages/35/74/4ea331ecc65ba05a58b1e4e8ccd604c8a15565f876dfbf93fc2a2ea2e22b/fonttools-4.68.0-py3-none-any.whl

# 2) create the user site directory — CPython only mounts it if it already exists
python3 -c "import site; print(site.getusersitepackages())"
mkdir -p "$(python3 -c 'import site; print(site.getusersitepackages())')"

# 3) unpack the wheel into it
unzip -o -q fonttools-4.68.0-py3-none-any.whl -d "$(python3 -c 'import site; print(site.getusersitepackages())')"

# 4) verify
python3 -c "import fontTools; print(fontTools.version)"
```

Expected output of step 4: `4.68.0`. Step 2 matters: `~/.local/lib/python3.14/site-packages` (adjust to your Python version) is **not** created by Python automatically, and `site.addusersitepackages()` only adds the directory to `sys.path` when it already exists — without `mkdir -p` the wheel is unpacked into a directory that is never scanned.

Notes:

- Do **not** run `steamos-readonly disable`, do **not** use `pacman -S python-fonttools`, do **not** enable developer mode. Those touch the read-only root filesystem, get wiped by SteamOS updates, and are unnecessary for a pure-Python wheel.
- The wheel is `py3-none-any`, so it works on any Python 3 the system provides.
- On SteamOS there is no `pip` in the system Python; that is expected and not a problem.
- After installing, reopen the panel — the mode row should now offer embedded mode. No Steam or Decky restart is needed.

### brotli is optional

`brotli` is only used to compress an extracted face into `woff2`. The plugin only tries that when the extracted face is **≤ 3 MiB** (`WOFF2_MAX_BYTES`); if `brotli` is missing or compression fails it silently keeps the plain `.ttf`. Full CJK faces are far above 3 MiB (a Noto Serif CJK SC face is ≈ 24.5 MB), so they always stay `.ttf` anyway — leaving `brotli` out has no effect on Chinese fonts.

If you do want it: `brotli-1.2.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` is 1,420,557 bytes and unpacks to 5,161,307 bytes (one 5.1 MB `_brotli…so` plus a tiny `brotli.py`). It is a compiled extension, but the manylinux2014 wheel matches SteamOS (CPython 3.14 ABI, glibc ≥ 2.17), so it can be unpacked into the same user site folder without a compiler.

## By-name mode

Default mode, labelled `按名引用` / `By name`. It injects a rule like

```css
@font-face {
  font-family: "DeckyFontPicker";
  font-style: normal;
  font-weight: 400;
  font-display: block;
  src: local("Noto Serif CJK SC");
}
```

and lets Chromium resolve that family name through the host's fontconfig. No dependency, no extraction, no cache, no HTTP server.

### Prerequisite: a clean fontconfig

By-name mode only does what fontconfig tells it to. Chromium on Linux looks a family name up through fontconfig and honours the returned `FC_INDEX`, so the mechanism itself can hit a non-first face such as `Noto Serif CJK SC` inside `NotoSerifCJK-Regular.ttc`. It cannot, however, override a rule that deliberately points that name somewhere else.

Check before relying on this mode:

```bash
fc-match 'Noto Serif CJK SC'
fc-list -f '%{file}|%{index}|%{family}\n' | grep 'Noto Serif CJK'
```

Ask yourself whether the `file` and `index` you see are the face you meant to pick. If `fc-match` prints a Latin font, by-name mode will resolve there too.

Two real failure modes:

- A `prefer` binding that ranks a Latin family above the CJK one — for example `Noto Serif` placed before `Noto Serif CJK SC` — makes `local('Noto Serif CJK SC')` land on the Latin face.
- A family name containing `serif` / `sans` can get a generic family appended by fontconfig's `guessfamily`, widening the match in an unintended direction.

In both cases the panel reports a hit, Latin text uses the chosen font, and Chinese text silently falls back to something else — "selected" behaves like "not selected". Switch to embedded mode when that happens.

### Advantages

- No `fontTools`, hence no extra dependency at all.
- No face extraction and no cache: embedded mode leaves 20 MB+ per selected CJK face under `~/homebrew/data/<folder>/fonts/`, by-name mode leaves 0 bytes.
- No loopback HTTP font server.

### Costs and limits

- The font must be installed on the system and registered with fontconfig; otherwise applying it fails.
- Only weight 400 is registered. Bold is synthesised by the browser, so a real Bold face cannot be chosen.
- Name matching is at the mercy of the host configuration and can silently return the wrong font.
- On SteamOS only Noto Sans CJK is generally guaranteed; serif faces are not.

### How to tell whether it hit

The panel reports one of three verdicts per injection target:

| Verdict | Meaning |
| --- | --- |
| `Applied` / `已命中` | The family resolved, and the measured width differs from a nonexistent family — the selected font is really in use. |
| `Not applied (system fallback)` / `未命中（已回退系统字体）` | `document.fonts.load()` resolved nothing, so the UI is running on the system fallback font. |
| `Applied (no CJK glyphs)` / `已命中（无中文字形）` | The family resolved, but its measured width equals the missing-font width — Chinese is not covered, so only Latin gets the chosen font. The verdict is shown in amber. |

A failed probe is reported separately as `Probe failed (not applied)` / `探测失败（未生效）`, with the reason on a second line.

The probe calls `document.fonts.load('16px "<family>"', '直骨海')` and then measures `直骨海` at 64 px on a canvas in three families: the selected family, a deliberately nonexistent family, and `sans-serif`. Zero loaded faces means a miss; a width difference above 0.5 px means a real hit; loaded faces with an identical width mean "hit, but no Chinese glyphs".

## Embedded mode

Labelled `抽取嵌入` / `Embedded`. It extracts the real face of the selected font and embeds it, so a specific face inside a `.ttc` / `.otc` works even when fontconfig would resolve the family name elsewhere.

- Requires `fontTools`; the mode row greys out and explains why when it is missing.
- Extracted faces are cached under `~/homebrew/data/<folder>/fonts/`, so switching back and forth does not re-extract.
- Serves the face from a loopback-only HTTP server that is started on demand.
- Status per target is `Injected` / `已注入` while the style is live.

Choose embedded mode when fontconfig has a strong binding that hijacks the name, or when the font lives inside a collection and you need a specific face rather than whatever index fontconfig happens to return. Choose by-name mode when disk space matters and `fc-match` already resolves your target family correctly.

## Only one build at a time

Because all builds inject CSS into the same Steam tabs, **enabling two at once makes them fight over the same style block**, and disabling one can remove the style injected by the other. That applies across languages.

- The folders differ (`DeckyFontPicker/`, `FontPickerEN/`), so the two names never overwrite each other.
- The bundled and no-dependency variants of the *same* language share one folder and one plugin name, so the second install replaces the first.
- Each keeps its own settings and font cache under `~/homebrew/settings/<folder>/` and `~/homebrew/data/<folder>/`.
- Keep exactly **one** of them enabled in the Decky plugin list; disable or remove the other.

## Font sources and limitations

- Fonts come from `fc-list` output; nothing except `fontTools` is bundled in this repository. After installing a font, run `fc-cache -f` and reopen the panel.
- `.ttf`, `.otf`, `.ttc` and `.otc` are supported. For collections, embedded mode pulls out only the used face (via `fontTools`) and caches it under the plugin runtime directory.
- By-name mode relies entirely on fontconfig name resolution; it cannot pull a specific face out of a collection or force a weight — see [By-name mode](#by-name-mode).
- Section placement is heuristic: `Mono` uses fontconfig `spacing` (100 / 110) or monospace keywords, `Serif` / `Sans` use family-name keywords, and everything else falls into `Others`. The `(zh)` / `(ja)` / `(ko)` split additionally requires a region token in the family name or, without one, CJK coverage. A font can therefore be classified differently than you expect — the section note in the panel states the rule used.
- The plugin does not modify system fonts or fontconfig; it only injects a CSS `@font-face` override for the running Steam session.

## Controls

| Input | Action |
| --- | --- |
| A / click | Apply the focused font, or toggle the mode row when it is focused |
| X | Add / remove favourite |
| Y | Collapse the current section |
| B | Back |

## Repository layout

```
DeckyFontPicker/     Chinese build source (plugin.json, main.py, face_extract.py, dist/index.js, vendor/)
FontPickerEN/        English build source (same layout)
README.md            this file (English)
README.zh-CN.md      Chinese version of this file
LICENSE              MIT
.gitignore
```

The repository sources correspond to the `-with-fonttools` build, i.e. they include `vendor/fontTools/`. The no-dependency ZIP is the same tree with `vendor/` left out; `main.py` only touches the vendor path when that directory exists, so by-name mode and the rest of the behaviour are unchanged there.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Honchane.

The `-with-fonttools` builds bundle [fontTools](https://github.com/fonttools/fonttools) 4.68.0, also MIT licensed — see `vendor/LICENSE-fontTools`.
