[English](README.md) | **简体中文**

# Decky 字体选择器

一个 [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) 插件：把 Steam Deck 上已安装的任意字体应用到 Steam 大屏界面（主界面、MainMenu、快捷访问菜单），不写 `~/homebrew/themes`，不依赖 CSS Loader，也不连 CEF 调试口。

仓库提供**同一个插件的六个包**：两种界面语言 × 三种字体投递方式。六份都是 **1.1.0**，行为完全一致（字体发现、分区规则、排序、操作方式都相同），只有界面语言、是否自带 `fontTools`、以及字体怎么送到浏览器不同。

| 版本 | Release 资产 | 插件目录 | 界面语言 | 字体投递 |
| --- | --- | --- | --- | --- |
| 中文，无依赖 | `DeckyFontPicker-1.1.0.zip` | `DeckyFontPicker/` | 简体中文 | 抽取 face + 本地 HTTP |
| 中文，自带依赖 | `DeckyFontPicker-1.1.0-with-fonttools.zip` | `DeckyFontPicker/` | 简体中文 | 抽取 face + 本地 HTTP |
| 中文，`local()` | `DeckyFontPickerLocal-1.1.0.zip` | `DeckyFontPickerLocal/` | 简体中文 | `local()`，不抽取、不缓存 |
| 英文，无依赖 | `FontPickerEN-1.1.0.zip` | `FontPickerEN/` | English | 抽取 face + 本地 HTTP |
| 英文，自带依赖 | `FontPickerEN-1.1.0-with-fonttools.zip` | `FontPickerEN/` | English | 抽取 face + 本地 HTTP |
| 英文，`local()` | `FontPickerENLocal-1.1.0.zip` | `FontPickerENLocal/` | English | `local()`，不抽取、不缓存 |

同语言的两种**抽取版**目录名和插件名完全相同，是互为替代的两个版本：装第二个会直接覆盖第一个，因此**同一时间只能装其中一个**。`local()` 版各自使用独立目录（`DeckyFontPickerLocal/`、`FontPickerENLocal/`）和独立插件名，不会覆盖前两者；但所有版本都往同一批 Steam 标签页注入 CSS，整体上仍然**只能启用一个**（见[同一时间只装一个](#同一时间只装一个)）。

`local()` 版不抽取 face、也不启动 HTTP 服务，完全依赖宿主 fontconfig 把所选族名解析到你真正想用的那一款字体——安装前请先读 [`local()` 模式](#local-模式)。

## 功能

- 列出 `fc-list` 报告的所有字体族，凡是被 fontconfig 收录的字体都会出现（系统包、`~/.local/share/fonts`、`~/.fonts` 等）。
- 11 个分区：`收藏`、`中文字体覆盖`、`衬线`、`衬线 (zh)`、`无衬线`、`无衬线 (zh)`、`等宽`、`等宽 (zh)`、`其它`、`其它 (zh)`、`全部字体`。
  - 基础分区会排除 `lang` 含 `zh` 的字体，`(zh)` 分区正好只放这些中文字体；两半**互斥**，同一个字体族不会重复出现（收藏除外）。空分区自动隐藏。
  - 英文版中这批分区标题为 `Favorites` / `Chinese Coverage` / `Serif` / `Serif (zh)` / `Sans` / `Sans (zh)` / `Mono` / `Mono (zh)` / `Others` / `Others (zh)` / `All Fonts`。
- 先按中文覆盖度排序，再按字体族名小写排序。
- 会检测所选字体族是否真的含中文字形，不含时自动回退到一款支持中文的字体，避免菜单文字变成方块。
- 通过仅监听回环地址的 HTTP 服务供给字体，并用 Decky 自身的 `inject_css_into_tab` 注入一段 `<style>`，随后在 Steam 重渲染时自动重放。`local()` 版跳过这个服务，直接按族名引用已安装字体。
- 注入三个目标：主页、`MainMenu_uid*`、`QuickAccess_uid*`。
- 分区折叠状态记忆、独立的「当前字体」折叠块、每个目标的实时注入状态、收藏（**X**）、每次加载 60 条的「加载更多」。

## 安装

1. 从 [Releases](../../releases) 页面下载其中一个 ZIP。
2. 在 Decky Loader 里打开 **设置 → 开发者 → 从 ZIP 安装插件**，或把压缩包解压到 `~/homebrew/plugins/`。
3. 重载 Decky（或重启 `plugin_loader.service`），打开快捷访问菜单。
4. 在 SteamOS 上，除非那台机器系统里已经有 `fontTools`，否则请选**自带 fontTools** 的包；也可以选 `local()` 版——它不需要 `fontTools`，但要求 fontconfig 能正确解析你选的字体（见 [`local()` 模式](#local-模式)）。

每个 ZIP 的内容就是：

```
# 无依赖版
<目录>/plugin.json
<目录>/main.py
<目录>/face_extract.py
<目录>/dist/index.js

# 自带 fontTools 版（多出一个 vendor 目录，4,898,851 字节 / 354 个文件）
<目录>/plugin.json
<目录>/main.py
<目录>/face_extract.py
<目录>/dist/index.js
<目录>/vendor/fontTools/...
<目录>/vendor/LICENSE-fontTools

# local() 版（不抽取、不缓存、不起 HTTP 服务）
<目录>/plugin.json
<目录>/main.py
<目录>/dist/index.js
```

环境要求：Decky Loader 3.x（运行时使用 `decky-frontend-lib` 3.25）和 Python 3。无依赖版还要求系统 `python3` 能 `import fontTools`；自带版不需要任何额外依赖；`local()` 版既不需要 `fontTools`，也没有 `vendor/`，只需要一个能正确解析所选字体族的 fontconfig。

## SteamOS 上的中文字体需要 fontTools

SteamOS 里几乎所有中文字体都是**集合字体**（`.ttc` / `.otc`），一个文件里打包了多个 face。例如 `NotoSerifCJK-Regular.ttc` 内含 5 个 face（JP、KR、SC、TC、HK），「Noto Serif CJK SC」是这个文件的 **index 2**。浏览器不能把整个集合直接当作 `@font-face` 的字体源，所以插件会用 `fontTools`（`TTCollection`）把你选中的那一个 face 抽出来，缓存到 `~/homebrew/data/<目录>/fonts/`。

缺 `fontTools` 时抽取无法进行，而插件又是**先移除旧样式、再准备新字体**，所以整次应用都会失败。表现是：

- 切到任何中文字体都失败；`.ttf` / `.otf` 字体不受影响，因为它们不需要抽取、直接供给。
- 三个注入目标（主页 / MainMenu / QuickAccess）全部变成**未注入**。
- `~/homebrew/logs/<目录>/` 下的日志最后会出现类似这样一行：

  ```
  准备字体 'Noto Serif CJK SC' 失败: 抽取 NotoSerifCJK-Regular.ttc#2 失败: fontTools 不可用: No module named 'fontTools'
  ```

### 解法一 —— 用自带 fontTools 的包（推荐，零依赖）

装 `DeckyFontPicker-1.1.0-with-fonttools.zip` 或 `FontPickerEN-1.1.0-with-fonttools.zip`。`fontTools` 4.68.0（纯 Python，MIT）已经放进 `vendor/`；`main.py` 在任何人 import `fontTools` 之前，把 `<插件目录>/vendor` 插到 `sys.path` 最前（同时注入到辅助子进程的 `PYTHONPATH`）。这个路径来自插件自身的实际位置，所以插件目录改名或移动后仍然有效，也完全不依赖 user site 或系统包。

代价：下载 1,248,357 字节（wheel 本体），解包后 4,898,851 字节（算上文件系统块开销约 5.7 MB）。**不含** `brotli`，原因见下。

### 解法二 —— 手动装到 user site

不需要 `pip`，不需要 `sudo`，不需要开发者模式，也不装任何系统包。直接照抄：

```bash
# 1) 下载 wheel（PyPI 官方文件，1,248,357 字节）
curl -L -O https://files.pythonhosted.org/packages/35/74/4ea331ecc65ba05a58b1e4e8ccd604c8a15565f876dfbf93fc2a2ea2e22b/fonttools-4.68.0-py3-none-any.whl

# 2) 建好 user site 目录 —— 目录不存在时 CPython 根本不会挂载它
python3 -c "import site; print(site.getusersitepackages())"
mkdir -p "$(python3 -c 'import site; print(site.getusersitepackages())')"

# 3) 把 wheel 解包进去
unzip -o -q fonttools-4.68.0-py3-none-any.whl -d "$(python3 -c 'import site; print(site.getusersitepackages())')"

# 4) 验证
python3 -c "import fontTools; print(fontTools.version)"
```

第 4 步应输出 `4.68.0`。第 2 步不能省：`~/.local/lib/python3.14/site-packages`（按你的 Python 版本）不会由 Python 自动创建，而 `site.addusersitepackages()` 只在目录**已存在**时才把它加入 `sys.path`；少了 `mkdir -p`，解包出来的东西永远不会被搜到。

注意：

- **不要**执行 `steamos-readonly disable`，**不要**用 `pacman -S python-fonttools`，**不要**开开发者模式。这些都会动只读根分区、会被 SteamOS 更新清掉，而纯 Python 的 wheel 根本用不上。
- 这个 wheel 是 `py3-none-any`，系统自带的任意 Python 3 都能用。
- SteamOS 的系统 Python 没有 `pip`，这是正常的，不影响上面的做法。
- 装完只需在插件面板里**重新选一次字体**（或切到别的字体再切回来），不用重启 Steam 或 Decky。

### brotli 是可选项

`brotli` 只用于把抽出来的单 face 压成 `woff2`，而且只有在单 face **≤ 3 MiB**（`WOFF2_MAX_BYTES`）时才会尝试；没有 `brotli` 或压缩失败，插件会静默保留 `.ttf`，不影响字体生效。完整 CJK face 远大于 3 MiB（Noto Serif CJK SC 单 face 约 24.5 MB），本来就不会走 woff2，所以不装 `brotli` 对中文字体没有任何影响。

确实需要的话：`brotli-1.2.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` 是 1,420,557 字节，解包 5,161,307 字节（一个 5.1 MB 的 `_brotli…so` 加一个很小的 `brotli.py`）。它是编译扩展，但这个 manylinux2014 wheel 与 SteamOS 匹配（CPython 3.14 ABI、glibc ≥ 2.17），同样可以不解包编译、直接解到上面那个 user site 目录里用。

## `local()` 模式

抽取版会把字体里的某一个 face 变成文件、再从 `127.0.0.1` 供给浏览器。`local()` 版两件事都不做，只注入这样一条规则：

```css
@font-face {
  font-family: "DeckyFontPickerLocal";
  font-style: normal;
  font-weight: 400;
  font-display: block;
  src: local("Noto Serif CJK SC");
}
```

然后让 Chromium 通过宿主 fontconfig 解析这个族名。没有额外依赖、没有抽取、没有缓存、没有 HTTP 服务。

### 使用前提：fontconfig 必须是「干净」的

`local()` 完全按 fontconfig 的说法办事。Linux 上的 Chromium 通过 fontconfig 查族名，并读取它返回的 `FC_INDEX`，所以机制本身可以命中 `NotoSerifCJK-Regular.ttc` 里 index 2 这种非首个 face。但它无法对抗一条把该族名指向别处的规则。

用之前先自查：

```bash
fc-match 'Noto Serif CJK SC'
fc-list -f '%{file}|%{index}|%{family}\n' | grep 'Noto Serif CJK'
```

看输出里的 `file` 与 `index` 是不是你想选的那一款 face。如果 `fc-match` 打出来的是一款拉丁字体，`local()` 也会解析到那里。

两个真实的翻车场景：

- fontconfig 里有 `prefer` 强绑定，把拉丁族排在 CJK 族前面（例如 `Noto Serif` 排在 `Noto Serif CJK SC` 之前），`local('Noto Serif CJK SC')` 就会落到拉丁字体上。
- 族名里含 `serif` / `sans`，被 fontconfig 的 `guessfamily` 追加了泛型族，匹配范围被意外放宽。

这两种情况下，包看起来是启用的、面板也报「已命中」，英文用的是你选的那款，中文却静默回退到别的字体——「选了」等于「没选」。

### 优势

- 不需要 `fontTools`，因此不需要任何额外依赖（自带版要多 4.9 MB / 354 个文件，手动装 wheel 也要下载 1.25 MB）。
- 不抽取 face、不写缓存：抽取版每选中一款 CJK 字体都会在 `~/homebrew/data/<目录>/fonts/` 下留下 20 MB 以上的 ttf，`local()` 版为 0。
- 不启动本地 HTTP 字体服务。

### 代价与限制

- 字体必须在系统里装好并被 fontconfig 收录，没装就失败。
- 只注册 400 这一个字重，粗体由浏览器合成，无法精确指定字重。
- 名字匹配完全受宿主配置影响，可能静默拿到错误的字体。
- SteamOS 一般只保证 Noto Sans CJK，Serif 面不保证存在。

### 如何判断是否命中

面板上每个注入目标会给出三行结论之一：

| 面板结论 | 含义 |
| --- | --- |
| `local 已命中` | 族名解析成功，且量出的文字宽度与「不存在的字体族」不同——真正用上了所选字体。 |
| `local 未命中（已回退系统字体）` | `document.fonts.load()` 没有加载到任何 face，界面实际跑在系统回退字体上。 |
| `local 已命中（无中文字形）` | 族名解析成功，但宽度与「不存在的字体族」相同，说明它没有中文字形——英文被替换了，中文没有。 |

探测方式是先调用 `document.fonts.load('16px "<内部族名>"', '直骨海')`，再用 canvas 以 64 px 量「直骨海」在三个族下的宽度：内部族名、一个故意不存在的字体族、以及 `serif`。加载到 0 个 face 即判定未命中；宽度差大于 0.5 px 判定真正命中；有 face 但宽度完全相同，判定为「命中但无中文字形」。

### 取舍建议

- 系统配置干净、`fc-match` 能解析到目标字体，又想省掉依赖和磁盘占用时，选 `local()` 版。
- fontconfig 里存在强绑定、或字体在 `.ttc` 里且需要精确指定字重/face 时，必须用抽取版。

## 同一时间只装一个

所有版本都往同一批 Steam 标签页注入 CSS，**同时启用两个会互相抢同一个 style 块**，禁用其中一个可能把另一个注入的样式一起删掉。跨语言、跨投递方式都一样。

- 目录名不同（`DeckyFontPicker/`、`FontPickerEN/`、`DeckyFontPickerLocal/`、`FontPickerENLocal/`），四个目录名互不覆盖。
- **同语言**的两种抽取版共用一个目录和一个插件名，后装的会替换先装的。
- 每个版本各自使用 `~/homebrew/settings/<目录>/` 与 `~/homebrew/data/<目录>/` 保存设置和字体缓存；`local()` 版不会创建 data 目录。
- 在 Decky 插件列表里只保留**一个**启用，其余禁用或删除。

## 字体来源与限制

- 字体来自 `fc-list` 的输出；除了 `fontTools`，仓库不再打包任何字体。装完字体后执行 `fc-cache -f`，再重新打开面板。
- 支持 `.ttf`、`.otf`、`.ttc`、`.otc`。集合字体由抽取版只抽出用到的那一个 face（用 `fontTools`），缓存到插件自己的运行目录。
- `local()` 版完全依赖 fontconfig 的族名解析，既不能从集合字体里指定某个 face，也不能强制某个字重——见 [`local()` 模式](#local-模式)。
- 分区归置是启发式的：`等宽` 看 fontconfig 的 `spacing`（100 / 110）或 monospace 关键字，`衬线` / `无衬线` 看字体族名关键词，其余归到 `其它`。因此某款字体的归类可能和直觉不同——面板里的分区说明会标出所用规则。
- 插件不修改系统字体或 fontconfig，只对当前 Steam 会话注入一段 CSS `@font-face` 覆盖。

## 操作

| 输入 | 动作 |
| --- | --- |
| A / 点击 | 应用当前聚焦的字体 |
| X | 添加 / 取消收藏 |
| Y | 折叠当前分区 |
| B | 返回 |

## 仓库结构

```
DeckyFontPicker/     中文版源码（plugin.json、main.py、face_extract.py、dist/index.js）
FontPickerEN/        英文版源码（结构相同）
README.md            英文说明
README.zh-CN.md      本文件（中文说明）
LICENSE              MIT
.gitignore
```

仓库源码对应的是 `-with-fonttools` 形态，即包含 `vendor/fontTools/`。无依赖版的 ZIP 就是同一份源码去掉 `vendor/`：`main.py` 只在 `vendor/` 目录存在时才动 `sys.path`，所以去掉后行为不变。

## 许可

MIT —— 见 [LICENSE](LICENSE)。Copyright (c) 2026 Honchane。

`-with-fonttools` 版本另外打包了 [fontTools](https://github.com/fonttools/fonttools) 4.68.0，同样是 MIT 许可 —— 见 `vendor/LICENSE-fontTools`。
