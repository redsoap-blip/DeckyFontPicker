**English** | [简体中文](README.zh-CN.md)

# Decky Font Picker

A [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) plugin that applies any font installed on your Steam Deck to the Steam Big Screen UI — Home, MainMenu and the Quick Access Menu — without touching `~/homebrew/themes`, CSS Loader or the CEF debug port.

The repository ships **six builds of the same plugin**: two UI languages × three delivery variants. All six are version **1.1.0** and share identical behaviour — same font discovery, same section rules, same sorting, same controls. They differ only in UI language, in whether they bundle `fontTools`, and in how the font reaches the browser.

| Build | Release asset | Plugin folder | UI language | Font delivery |
| --- | --- | --- | --- | --- |
| Chinese, no dependency | `DeckyFontPicker-1.1.0.zip` | `DeckyFontPicker/` | 简体中文 | extract face, loopback HTTP |
| Chinese, bundled | `DeckyFontPicker-1.1.0-with-fonttools.zip` | `DeckyFontPicker/` | 简体中文 | extract face, loopback HTTP |
| Chinese, `local()` | `DeckyFontPickerLocal-1.1.0.zip` | `DeckyFontPickerLocal/` | 简体中文 | `local()`, no extraction, no cache |
| English, no dependency | `FontPickerEN-1.1.0.zip` | `FontPickerEN/` | English | extract face, loopback HTTP |
| English, bundled | `FontPickerEN-1.1.0-with-fonttools.zip` | `FontPickerEN/` | English | extract face, loopback HTTP |
| English, `local()` | `FontPickerENLocal-1.1.0.zip` | `FontPickerENLocal/` | English | `local()`, no extraction, no cache |

The two extraction variants of the same language use the **same plugin folder and the same plugin name**, so they are drop-in replacements for each other: installing one overwrites the other, and **only one of them can be installed at a time**. The `local()` variant of each language has its own folder and its own plugin name, so it does not overwrite either of them — but all builds still inject into the same Steam tabs, so only one build overall may be enabled (see [Only one build at a time](#only-one-build-at-a-time)).

The `local()` builds never extract a face and never start an HTTP server; they depend on the host fontconfig resolving the selected family to the font you actually mean. Read [`local()` mode](#local-mode) before installing one.

## Features

- Lists every font family reported by `fc-list`, so anything registered with fontconfig appears automatically (SteamOS packages, `~/.local/share/fonts`, `~/.fonts`, …).
- 11 sections: `Favorites`, `Chinese Coverage`, `Serif`, `Serif (zh)`, `Sans`, `Sans (zh)`, `Mono`, `Mono (zh)`, `Others`, `Others (zh)`, `All Fonts`.
  - Base sections exclude fonts whose `lang` includes `zh`; the `(zh)` sections contain exactly those Chinese fonts. The two halves are **mutually exclusive**, so no family is listed twice (favourites excepted). Empty sections are hidden.
- Sorted by Chinese coverage first, then by lower-cased family name.
- Detects whether the selected family actually contains Chinese glyphs and falls back to a Chinese-capable family when it does not, so menu text never turns into tofu.
- Serves the font from a loopback-only HTTP server and injects one `<style>` block through Decky's own `inject_css_into_tab`, then re-applies it across Steam re-renders. The `local()` builds skip the server and reference the installed font by family name instead.
- Injects into three targets: Home, `MainMenu_uid*` and `QuickAccess_uid*`.
- Per-section collapse state, a separate collapsible "Current font" block, live injection status per target, favourites (**X**), and "load more" paging in steps of 60.

## Install

1. Download one of the ZIPs from the [Releases](../../releases) page.
2. In Decky Loader open **Settings → Developer → Install Plugin from ZIP**, or extract the archive into `~/homebrew/plugins/`.
3. Reload Decky (or restart `plugin_loader.service`) and open the Quick Access Menu.
4. On SteamOS, pick a build **with fontTools** unless `fontTools` is already installed system-wide on that device — or pick a `local()` build, which needs no `fontTools` but does need a fontconfig that resolves your font correctly (see [`local()` mode](#local-mode)).

Each ZIP contains exactly:

```
# no-dependency build
<folder>/plugin.json
<folder>/main.py
<folder>/face_extract.py
<folder>/dist/index.js

# with-fonttools build (adds the vendored library, 4,898,851 bytes / 354 files)
<folder>/plugin.json
<folder>/main.py
<folder>/face_extract.py
<folder>/dist/index.js
<folder>/vendor/fontTools/...
<folder>/vendor/LICENSE-fontTools

# local() build (no extraction, no cache, no HTTP server)
<folder>/plugin.json
<folder>/main.py
<folder>/dist/index.js
```

Requirements: Decky Loader 3.x (`decky-frontend-lib` 3.25 is used at runtime) and Python 3. The no-dependency build additionally needs `fontTools` importable by the system `python3`; the with-fonttools build needs nothing else; the `local()` builds need neither `fontTools` nor `vendor/`, only a fontconfig that resolves the selected family.

## Chinese fonts on SteamOS need fontTools

On SteamOS almost every Chinese font is a **collection** (`.ttc` / `.otc`) that packs several faces into one file — `NotoSerifCJK-Regular.ttc` for example contains 5 faces (JP, KR, SC, TC, HK), and "Noto Serif CJK SC" is face **index 2** of that file. Browsers cannot use a whole collection as an `@font-face` source, so the plugin extracts the single face you picked with `fontTools` (`TTCollection`) and caches it under `~/homebrew/data/<folder>/fonts/`.

Without `fontTools` that extraction cannot run, and because the plugin removes the previous style *before* preparing the new font, the whole apply fails. The symptoms are:

- Switching to any Chinese font fails; `.ttf` / `.otf` fonts still work because they are served directly without extraction.
- All three injection targets (Home / MainMenu / QuickAccess) flip to **not injected** — `未注入` in the Chinese build, `Not injected` in the English build.
- The plugin log under `~/homebrew/logs/<folder>/` ends with a line like:

  ```
  准备字体 'Noto Serif CJK SC' 失败: 抽取 NotoSerifCJK-Regular.ttc#2 失败: fontTools 不可用: No module named 'fontTools'
  ```

### Fix 1 — use a build with fontTools (recommended, zero dependencies)

Install `DeckyFontPicker-1.1.0-with-fonttools.zip` or `FontPickerEN-1.1.0-with-fonttools.zip`. `fontTools` 4.68.0 (pure Python, MIT) is vendored under `vendor/`, and `main.py` puts `<plugin dir>/vendor` at the front of `sys.path` (and into `PYTHONPATH` for the helper subprocess) before anything imports `fontTools`. Because the path is derived from the plugin's own location, the plugin keeps working if you rename or move its folder, and it never depends on the user site or on system packages.

Cost: 1,248,357 bytes of download (the wheel) / 4,898,851 bytes unpacked (≈ 5.7 MB on disk with filesystem block overhead). `brotli` is **not** included; see below.

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
- After installing, just re-select the font in the plugin panel (or switch to another font and back). No Steam or Decky restart is needed.

### brotli is optional

`brotli` is only used to compress an extracted face into `woff2`. The plugin only tries that when the extracted face is **≤ 3 MiB** (`WOFF2_MAX_BYTES`); if `brotli` is missing or compression fails it silently keeps the plain `.ttf`. Full CJK faces are far above 3 MiB (a Noto Serif CJK SC face is ≈ 24.5 MB), so they always stay `.ttf` anyway — leaving `brotli` out has no effect on Chinese fonts.

If you do want it: `brotli-1.2.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` is 1,420,557 bytes and unpacks to 5,161,307 bytes (one 5.1 MB `_brotli…so` plus a tiny `brotli.py`). It is a compiled extension, but the manylinux2014 wheel matches SteamOS (CPython 3.14 ABI, glibc ≥ 2.17), so it can be unpacked into the same user site folder without a compiler.

## `local()` mode

The extraction builds turn one face of a font into a file and serve it from `127.0.0.1`. The `local()` builds do neither. They inject a rule like

```css
@font-face {
  font-family: "FontPickerENLocal";
  font-style: normal;
  font-weight: 400;
  font-display: block;
  src: local("Noto Serif CJK SC");
}
```

and let Chromium resolve that family name through the host's fontconfig. No dependency, no extraction, no cache, no HTTP server.

### Prerequisite: a clean fontconfig

`local()` only does what fontconfig tells it to. Chromium on Linux looks a family name up through fontconfig and honours the returned `FC_INDEX`, so the mechanism itself can hit a non-first face such as `Noto Serif CJK SC` inside `NotoSerifCJK-Regular.ttc`. It cannot, however, override a rule that deliberately points that name somewhere else.

Check before relying on this build:

```bash
fc-match 'Noto Serif CJK SC'
fc-list -f '%{file}|%{index}|%{family}\n' | grep 'Noto Serif CJK'
```

Ask yourself whether the `file` and `index` you see are the face you meant to pick. If `fc-match` prints a Latin font, `local()` will resolve there too.

Two real failure modes:

- A `prefer` binding that ranks a Latin family above the CJK one — for example `Noto Serif` placed before `Noto Serif CJK SC` — makes `local('Noto Serif CJK SC')` land on the Latin face.
- A family name containing `serif` / `sans` can get a generic family appended by fontconfig's `guessfamily`, widening the match in an unintended direction.

In both cases the build looks enabled and reports a hit, Latin text uses the chosen font, and Chinese text silently falls back to something else — “selected” behaves like “not selected”.

### Advantages

- No `fontTools`, hence no extra dependency at all.
- No face extraction and no cache: the extraction builds leave 20 MB+ per selected CJK face under `~/homebrew/data/<folder>/fonts/`, the `local()` builds leave 0 bytes.
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
| `local() hit` | The family resolved, and the measured width differs from a nonexistent family — the selected font is really in use. |
| `local() miss (system fallback)` | `document.fonts.load()` resolved nothing, so the UI is running on the system fallback font. |
| `local() hit, no Chinese glyphs` | The family resolved, but its measured width equals the missing-font width — Chinese is not covered, so only Latin gets the chosen font. |

The probe calls `document.fonts.load('16px "<internal name>"', '直骨海')` and then measures `直骨海` at 64 px on a canvas in three families: the internal name, a deliberately nonexistent family, and `serif`. Zero loaded faces means a miss; a width difference above 0.5 px means a real hit; loaded faces with an identical width mean “hit, but no Chinese glyphs”.

### Which build to use

- Choose a `local()` build when `fc-match` resolves your target family to the face you want, when disk space matters, and when you would rather not ship `fontTools`.
- Choose an extraction build when fontconfig has a strong binding that hijacks the name, or when the font lives inside a `.ttc` and you need a specific weight/face rather than whatever index fontconfig happens to return.

## Only one build at a time

Because all builds inject CSS into the same Steam tabs, **enabling two at once makes them fight over the same style block**, and disabling one can remove the style injected by the other. That applies both across languages and across delivery variants.

- The folders differ (`DeckyFontPicker/`, `FontPickerEN/`, `DeckyFontPickerLocal/`, `FontPickerENLocal/`), so the four folder names never overwrite each other.
- The two extraction variants of the *same* language share one folder and one plugin name, so the second install replaces the first.
- Each keeps its own settings and font cache under `~/homebrew/settings/<folder>/` and `~/homebrew/data/<folder>/`; the `local()` builds never create the data directory.
- Keep exactly **one** of them enabled in the Decky plugin list; disable or remove the others.

## Font sources and limitations

- Fonts come from `fc-list` output; nothing except `fontTools` is bundled in this repository. After installing a font, run `fc-cache -f` and reopen the panel.
- `.ttf`, `.otf`, `.ttc` and `.otc` are supported. For collections the extraction builds pull out only the used face (via `fontTools`) and cache it under the plugin runtime directory.
- The `local()` builds rely entirely on fontconfig name resolution; they cannot pull a specific face out of a collection or force a weight — see [`local()` mode](#local-mode).
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
README.md            this file (English)
README.zh-CN.md      Chinese version of this file
LICENSE              MIT
.gitignore
```

The repository sources correspond to the `-with-fonttools` build, i.e. they include `vendor/fontTools/`. The no-dependency ZIP is the same tree with `vendor/` left out; `main.py` only touches the vendor path when that directory exists, so the behaviour is unchanged there.

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Honchane.

The `-with-fonttools` builds bundle [fontTools](https://github.com/fonttools/fonttools) 4.68.0, also MIT licensed — see `vendor/LICENSE-fontTools`.
