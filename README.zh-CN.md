[English](README.md) | **简体中文**

# Decky 字体选择器

一个 [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) 插件：把 Steam Deck 上已安装的任意字体应用到 Steam 大屏界面（主界面、MainMenu、快捷访问菜单），不写 `~/homebrew/themes`，不依赖 CSS Loader，也不连 CEF 调试口。

仓库提供**同一个插件的四个包**：两种界面语言 × 两种打包形态。四份都是 **1.2.0**，行为完全一致（字体发现、分区规则、排序、操作方式、两种投递模式都相同），只有界面语言、以及是否自带 `fontTools` 不同。

| 版本 | Release 资产 | 插件目录 | 界面语言 | `fontTools` |
| --- | --- | --- | --- | --- |
| 中文，自带依赖 | `DeckyFontPicker-1.2.0-with-fonttools.zip` | `DeckyFontPicker/` | 简体中文 | 已放在 `vendor/` 下 |
| 中文，无依赖 | `DeckyFontPicker-1.2.0.zip` | `DeckyFontPicker/` | 简体中文 | 未打包 |
| 英文，自带依赖 | `FontPickerEN-1.2.0-with-fonttools.zip` | `FontPickerEN/` | English | 已放在 `vendor/` 下 |
| 英文，无依赖 | `FontPickerEN-1.2.0.zip` | `FontPickerEN/` | English | 未打包 |

同语言的两个包目录名和插件名完全相同，是互为替代的两个版本：装第二个会直接覆盖第一个，因此**同一时间只能装其中一个**。中英两版目录名不同、互不覆盖；但所有包都往同一批 Steam 标签页注入 CSS，整体上仍然**只能启用一个**（见[同一时间只装一个](#同一时间只装一个)）。

## 两种投递模式，面板里随时切换

1.2.0 不再按投递方式分包。四个包都**同时内置**两种投递方式，运行时切换：

| 模式 | 面板文案（中 / 英） | 做的事 | 需要 `fontTools` |
| --- | --- | --- | --- |
| 按名引用 | `按名引用` / `By name` | 注入 `@font-face { src: local("<族名>") }`，让 Chromium 通过宿主 fontconfig 解析族名。不抽 face、不缓存、不起 HTTP 服务。 | 不需要 |
| 抽取嵌入 | `抽取嵌入` / `Embedded` | 用 `fontTools`（`.ttc` / `.otc` 走 `TTCollection`）把你选中的那一个 face 抽出来，缓存到 `~/homebrew/data/<目录>/fonts/`，再由仅监听回环地址的 HTTP 服务供给浏览器。 | 需要 |

- **默认是按名引用**，代价最小，`.ttf` / `.otf` 都能用；使用前提见[按名引用模式](#按名引用模式)。
- **一键切换**：「注入方式」这一行常驻在面板顶部，始终可见、可聚焦，按 A / 点击即可在两种模式间切换。这一行会显示当前模式，以及抽取嵌入不可用时的具体原因。
- **只由用户手动切换**：下面的探测结论不会触发任何自动切换——未命中只如实报告，不会偷偷帮你换模式。
- 模式保存在插件自己的设置里，重载 Decky 后仍然有效。

## 功能

- 列出 `fc-list` 报告的所有字体族，凡是被 fontconfig 收录的字体都会出现（系统包、`~/.local/share/fonts`、`~/.fonts` 等）。
- 分区：`收藏夹`、`含中文`，然后是 `Serif 衬线`、`Sans 无衬线`、`Mono 等宽`、`其他` 四类，每类下带 `（zh）`、`（ja）`、`（ko）` 三个子类，最后是 `全部字体`。空分区整块隐藏。
  - 每个基础类都排除中文、日文、韩文字体，`（zh）` / `（ja）` / `（ko）` 子类正好只放这些字体；两半**互斥**，同一个字体族不会重复出现（收藏除外）。
  - **子类归属由主语言决定**：族名里的地区标记优先——`JP` → `（ja）`、`KR` → `（ko）`、`SC` / `TC` / `HK` / `CN` → `（zh）`。所以 `Noto Sans CJK JP` 落在 `（ja）`，`Noto Sans CJK SC` 落在 `（zh）`。族名没有地区标记时，才按 `zh > ja > ko` 的覆盖规则回退。
  - fontconfig 的 `lang` 列表不能当主语言判据：`fc-list` 返回的顺序是字母序，第一条没有「主语言」含义。实际只用族名后缀，覆盖规则只作兜底。
- 先按中文覆盖度排序，再按字体族名小写排序。
- 会检测所选字体族是否真的含中文字形，并按注入目标分别给出结论，避免菜单文字悄悄变成方块；插件**不会**自动替你换字体。
- 用 Decky 自身的 `inject_css_into_tab` 往三个目标注入 `<style>`：主页、`MainMenu_uid*`、`QuickAccess_uid*`，并在 Steam 重渲染时自动重放。
- 面板：注入方式常驻可点，「当前字体」收起后只剩一行摘要、仍然能看出命中 / 未注入 / 探测失败，且不渲染任何调试信息。
- 分区折叠状态记忆、收藏（**X**）、每次加载 60 条的「加载更多」。

## 安装

1. 从 [Releases](../../releases) 页面下载其中一个 ZIP。
2. 在 Decky Loader 里打开 **设置 → 开发者 → 从 ZIP 安装插件**，或把压缩包解压到 `~/homebrew/plugins/`。
3. 重载 Decky（或重启 `plugin_loader.service`），打开快捷访问菜单。
4. 在 SteamOS 上，除非那台机器系统里已经有 `fontTools`，否则请选**自带 fontTools** 的包。无依赖包仍可用按名引用模式，只是「注入方式」行会把抽取嵌入标为不可用。

每个 ZIP 的内容就是：

```
# 无依赖版
<目录>/plugin.json
<目录>/main.py
<目录>/face_extract.py
<目录>/dist/index.js

# 自带 fontTools 版（多出一个 vendor 目录，4,700,553 字节 / 354 个文件）
<目录>/plugin.json
<目录>/main.py
<目录>/face_extract.py
<目录>/dist/index.js
<目录>/vendor/fontTools/...
<目录>/vendor/LICENSE-fontTools
```

环境要求：Decky Loader 3.x（运行时使用 `decky-frontend-lib` 3.25）和 Python 3。无依赖版要跑抽取嵌入，还要求系统 `python3` 能 `import fontTools`；自带版不需要任何额外依赖。按名引用模式在任何包里都不需要 `fontTools`。

## SteamOS 上的中文字体：抽取嵌入需要 fontTools

SteamOS 里几乎所有中文字体都是**集合字体**（`.ttc` / `.otc`），一个文件里打包了多个 face。例如 `NotoSerifCJK-Regular.ttc` 内含 5 个 face（JP、KR、SC、TC、HK），「Noto Serif CJK SC」是这个文件的 **index 2**。浏览器不能把整个集合直接当作 `@font-face` 的字体源，所以抽取嵌入模式会用 `fontTools`（`TTCollection`）把你选中的那一个 face 抽出来，缓存到 `~/homebrew/data/<目录>/fonts/`。

缺 `fontTools` 时抽取无法进行。1.2.0 里这不会再把插件搞坏：「注入方式」行会把抽取嵌入标为不可用并说明原因，按名引用模式照常工作。如果仍然强行用抽取嵌入去应用 CJK 字体，则会失败，`~/homebrew/logs/<目录>/` 下的日志最后会出现类似这样一行：

```
准备字体 'Noto Serif CJK SC' 失败: 抽取 NotoSerifCJK-Regular.ttc#2 失败: fontTools 不可用: No module named 'fontTools'
```

### 解法一 —— 用自带 fontTools 的包（推荐，零依赖）

装 `DeckyFontPicker-1.2.0-with-fonttools.zip` 或 `FontPickerEN-1.2.0-with-fonttools.zip`。`fontTools` 4.68.0（纯 Python，MIT）已经放进 `vendor/`；`main.py` 在任何人 import `fontTools` 之前，把 `<插件目录>/vendor` 插到 `sys.path` 最前（同时注入到辅助子进程的 `PYTHONPATH`）。这个路径来自插件自身的实际位置，所以插件目录改名或移动后仍然有效，也完全不依赖 user site 或系统包。

代价：`fontTools` 树解包后 4,700,553 字节（算上文件系统块开销约 5.7 MB），中文包 1,245,589 字节、英文包 1,243,066 字节。**不含** `brotli`，原因见下。

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
- 装完重新打开面板即可，「注入方式」行应该已经提供抽取嵌入。不用重启 Steam 或 Decky。

### brotli 是可选项

`brotli` 只用于把抽出来的单 face 压成 `woff2`，而且只有在单 face **≤ 3 MiB**（`WOFF2_MAX_BYTES`）时才会尝试；没有 `brotli` 或压缩失败，插件会静默保留 `.ttf`，不影响字体生效。完整 CJK face 远大于 3 MiB（Noto Serif CJK SC 单 face 约 24.5 MB），本来就不会走 woff2，所以不装 `brotli` 对中文字体没有任何影响。

确实需要的话：`brotli-1.2.1-cp314-cp314-manylinux2014_x86_64.manylinux_2_17_x86_64.whl` 是 1,420,557 字节，解包 5,161,307 字节（一个 5.1 MB 的 `_brotli…so` 加一个很小的 `brotli.py`）。它是编译扩展，但这个 manylinux2014 wheel 与 SteamOS 匹配（CPython 3.14 ABI、glibc ≥ 2.17），同样可以不解包编译、直接解到上面那个 user site 目录里用。

## 按名引用模式

默认模式，面板文案 `按名引用`。它只注入这样一条规则：

```css
@font-face {
  font-family: "DeckyFontPicker";
  font-style: normal;
  font-weight: 400;
  font-display: block;
  src: local("Noto Serif CJK SC");
}
```

然后让 Chromium 通过宿主 fontconfig 解析这个族名。没有额外依赖、没有抽取、没有缓存、没有 HTTP 服务。

### 使用前提：fontconfig 必须是「干净」的

按名引用模式完全按 fontconfig 的说法办事。Linux 上的 Chromium 通过 fontconfig 查族名，并读取它返回的 `FC_INDEX`，所以机制本身可以命中 `NotoSerifCJK-Regular.ttc` 里 index 2 这种非首个 face。但它无法对抗一条把该族名指向别处的规则。

用之前先自查：

```bash
fc-match 'Noto Serif CJK SC'
fc-list -f '%{file}|%{index}|%{family}\n' | grep 'Noto Serif CJK'
```

看输出里的 `file` 与 `index` 是不是你想选的那一款 face。如果 `fc-match` 打出来的是一款拉丁字体，按名引用也会解析到那里。

两个真实的翻车场景：

- fontconfig 里有 `prefer` 强绑定，把拉丁族排在 CJK 族前面（例如 `Noto Serif` 排在 `Noto Serif CJK SC` 之前），`local('Noto Serif CJK SC')` 就会落到拉丁字体上。
- 族名里含 `serif` / `sans`，被 fontconfig 的 `guessfamily` 追加了泛型族，匹配范围被意外放宽。

这两种情况下，面板会报「已命中」，英文用的是你选的那款，中文却静默回退到别的字体——「选了」等于「没选」。此时请切到抽取嵌入。

### 优势

- 不需要 `fontTools`，因此不需要任何额外依赖（自带版要多约 4.7 MB / 354 个文件，手动装 wheel 也要下载 1.25 MB）。
- 不抽取 face、不写缓存：抽取嵌入每选中一款 CJK 字体都会在 `~/homebrew/data/<目录>/fonts/` 下留下 20 MB 以上的 ttf，按名引用为 0。
- 不启动本地 HTTP 字体服务。

### 代价与限制

- 字体必须在系统里装好并被 fontconfig 收录，没装就失败。
- 只注册 400 这一个字重，粗体由浏览器合成，无法精确指定字重。
- 名字匹配完全受宿主配置影响，可能静默拿到错误的字体。
- SteamOS 一般只保证 Noto Sans CJK，Serif 面不保证存在。

### 如何判断是否命中

面板上每个注入目标会给出三种结论之一：

| 面板结论 | 含义 |
| --- | --- |
| `已命中` | 族名解析成功，且量出的文字宽度与「不存在的字体族」不同——真正用上了所选字体。 |
| `未命中（已回退系统字体）` | `document.fonts.load()` 没有加载到任何 face，界面实际跑在系统回退字体上。 |
| `已命中（无中文字形）` | 族名解析成功，但宽度与「不存在的字体族」相同，说明它没有中文字形——英文被替换了，中文没有。这条结论用黄色显示。 |

探测本身出错时另报 `探测失败（未生效）`，原因写在第二行小字里。

探测方式是先调用 `document.fonts.load('16px "<族名>"', '直骨海')`，再用 canvas 以 64 px 量「直骨海」在三个族下的宽度：所选族名、一个故意不存在的字体族、以及 `sans-serif`。加载到 0 个 face 即判定未命中；宽度差大于 0.5 px 判定真正命中；有 face 但宽度完全相同，判定为「命中但无中文字形」。

## 抽取嵌入模式

面板文案 `抽取嵌入`。它把所选字体的真实 face 抽出来嵌入页面，所以即使 fontconfig 把族名解析到别处，`.ttc` / `.otc` 里的指定 face 也能强行生效。

- 依赖 `fontTools`；缺失时「注入方式」行会置灰并说明原因。
- 抽出的 face 缓存在 `~/homebrew/data/<目录>/fonts/`，来回切换不会重复抽取。
- 由按需启动的、仅监听回环地址的 HTTP 服务供给。
- 样式生效期间，每个目标的状态是 `已注入`。

fontconfig 里存在强绑定、名字被劫持，或字体在集合文件里且需要精确指定 face 时，用抽取嵌入；配置干净、`fc-match` 已能正确解析、又在意磁盘占用时，用按名引用。

## 同一时间只装一个

所有包都往同一批 Steam 标签页注入 CSS，**同时启用两个会互相抢同一个 style 块**，禁用其中一个可能把另一个注入的样式一起删掉。跨语言也一样。

- 目录名不同（`DeckyFontPicker/`、`FontPickerEN/`），两个名字互不覆盖。
- **同语言**的自带依赖版与无依赖版共用一个目录和一个插件名，后装的会替换先装的。
- 每个包各自使用 `~/homebrew/settings/<目录>/` 与 `~/homebrew/data/<目录>/` 保存设置和字体缓存。
- 在 Decky 插件列表里只保留**一个**启用，另一个禁用或删除。

## 字体来源与限制

- 字体来自 `fc-list` 的输出；除了 `fontTools`，仓库不再打包任何字体。装完字体后执行 `fc-cache -f`，再重新打开面板。
- 支持 `.ttf`、`.otf`、`.ttc`、`.otc`。集合字体由抽取嵌入模式只抽出用到的那一个 face（用 `fontTools`），缓存到插件自己的运行目录。
- 按名引用模式完全依赖 fontconfig 的族名解析，既不能从集合字体里指定某个 face，也不能强制某个字重——见[按名引用模式](#按名引用模式)。
- 分区归置是启发式的：`等宽` 看 fontconfig 的 `spacing`（100 / 110）或 monospace 关键字，`衬线` / `无衬线` 看字体族名关键词，其余归到 `其他`；`（zh）` / `（ja）` / `（ko）` 再按族名地区标记、没有标记时按 CJK 覆盖度划分。因此某款字体的归类可能和直觉不同——面板里的分区说明会标出所用规则。
- 插件不修改系统字体或 fontconfig，只对当前 Steam 会话注入一段 CSS `@font-face` 覆盖。

## 操作

| 输入 | 动作 |
| --- | --- |
| A / 点击 | 应用当前聚焦的字体；焦点在「注入方式」行时切换模式 |
| X | 添加 / 取消收藏 |
| Y | 折叠当前分区 |
| B | 返回 |

## 仓库结构

```
DeckyFontPicker/     中文版源码（plugin.json、main.py、face_extract.py、dist/index.js、vendor/）
FontPickerEN/        英文版源码（结构相同）
README.md            英文说明
README.zh-CN.md      本文件（中文说明）
LICENSE              MIT
.gitignore
```

仓库源码对应的是 `-with-fonttools` 形态，即包含 `vendor/fontTools/`。无依赖版的 ZIP 就是同一份源码去掉 `vendor/`：`main.py` 只在 `vendor/` 目录存在时才动 `sys.path`，去掉后按名引用模式及其余行为都不变。

## 许可

MIT —— 见 [LICENSE](LICENSE)。Copyright (c) 2026 Honchane。

`-with-fonttools` 版本另外打包了 [fontTools](https://github.com/fonttools/fonttools) 4.68.0，同样是 MIT 许可 —— 见 `vendor/LICENSE-fontTools`。
