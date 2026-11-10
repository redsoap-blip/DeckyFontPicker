[English](README.md) | **简体中文**

# Decky 字体选择器

一个 [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) 插件：把 Steam Deck 上已安装的任意字体应用到 Steam 大屏界面（主界面、MainMenu、快捷访问菜单），不写 `~/homebrew/themes`，不依赖 CSS Loader，也不连 CEF 调试口。

仓库提供**同一个插件的四个包**：两种界面语言 × 两种依赖形态。四份都是 **1.0.2**，行为完全一致（字体发现、分区规则、排序、操作方式都相同），只有界面语言和是否自带 `fontTools` 不同。

| 版本 | Release 资产 | 插件目录 | 界面语言 | fontTools |
| --- | --- | --- | --- | --- |
| 中文，无依赖 | `DeckyFontPicker-1.0.2.zip` | `DeckyFontPicker/` | 简体中文 | 不带 |
| 中文，自带依赖 | `DeckyFontPicker-1.0.2-with-fonttools.zip` | `DeckyFontPicker/` | 简体中文 | 放在 `vendor/` |
| 英文，无依赖 | `FontPickerEN-1.0.2.zip` | `FontPickerEN/` | English | 不带 |
| 英文，自带依赖 | `FontPickerEN-1.0.2-with-fonttools.zip` | `FontPickerEN/` | English | 放在 `vendor/` |

同语言的两种形态**目录名和插件名完全相同**，是互为替代的两个版本：装第二个会直接覆盖第一个，因此**同一时间只能装其中一个**。

## 功能

- 列出 `fc-list` 报告的所有字体族，凡是被 fontconfig 收录的字体都会出现（系统包、`~/.local/share/fonts`、`~/.fonts` 等）。
- 11 个分区：`收藏`、`中文字体覆盖`、`衬线`、`衬线 (zh)`、`无衬线`、`无衬线 (zh)`、`等宽`、`等宽 (zh)`、`其它`、`其它 (zh)`、`全部字体`。
  - 基础分区会排除 `lang` 含 `zh` 的字体，`(zh)` 分区正好只放这些中文字体；两半**互斥**，同一个字体族不会重复出现（收藏除外）。空分区自动隐藏。
  - 英文版中这批分区标题为 `Favorites` / `Chinese Coverage` / `Serif` / `Serif (zh)` / `Sans` / `Sans (zh)` / `Mono` / `Mono (zh)` / `Others` / `Others (zh)` / `All Fonts`。
- 先按中文覆盖度排序，再按字体族名小写排序。
- 会检测所选字体族是否真的含中文字形，不含时自动回退到一款支持中文的字体，避免菜单文字变成方块。
- 通过仅监听回环地址的 HTTP 服务供给字体，并用 Decky 自身的 `inject_css_into_tab` 注入一段 `<style>`，随后在 Steam 重渲染时自动重放。
- 注入三个目标：主页、`MainMenu_uid*`、`QuickAccess_uid*`。
- 分区折叠状态记忆、独立的「当前字体」折叠块、每个目标的实时注入状态、收藏（**X**）、每次加载 60 条的「加载更多」。

## 安装

1. 从 [Releases](../../releases) 页面下载其中一个 ZIP。
2. 在 Decky Loader 里打开 **设置 → 开发者 → 从 ZIP 安装插件**，或把压缩包解压到 `~/homebrew/plugins/`。
3. 重载 Decky（或重启 `plugin_loader.service`），打开快捷访问菜单。
4. 在 SteamOS 上，除非那台机器系统里已经有 `fontTools`，否则请选**自带 fontTools** 的包——原因见下一节。

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
```

环境要求：Decky Loader 3.x（运行时使用 `decky-frontend-lib` 3.25）和 Python 3。无依赖版还要求系统 `python3` 能 `import fontTools`；自带版不需要任何额外依赖。

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

装 `DeckyFontPicker-1.0.2-with-fonttools.zip` 或 `FontPickerEN-1.0.2-with-fonttools.zip`。`fontTools` 4.68.0（纯 Python，MIT）已经放进 `vendor/`；`main.py` 在任何人 import `fontTools` 之前，把 `<插件目录>/vendor` 插到 `sys.path` 最前（同时注入到辅助子进程的 `PYTHONPATH`）。这个路径来自插件自身的实际位置，所以插件目录改名或移动后仍然有效，也完全不依赖 user site 或系统包。

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

## 同一时间只装一个

所有版本都往同一批 Steam 标签页注入 CSS，**同时启用两个会互相抢同一个 style 块**，禁用其中一个可能把另一个注入的样式一起删掉。跨语言、跨依赖形态都一样。

- 目录名不同（`DeckyFontPicker/` 与 `FontPickerEN/`），所以中英文版可以并存、互不覆盖。
- **同语言**的两种形态共用一个目录和一个插件名，后装的会替换先装的。
- 每个版本各自使用 `~/homebrew/settings/<目录>/` 与 `~/homebrew/data/<目录>/` 保存设置和字体缓存。
- 在 Decky 插件列表里只保留**一个**启用，其余禁用或删除。

## 字体来源与限制

- 字体来自 `fc-list` 的输出；除了 `fontTools`，仓库不再打包任何字体。装完字体后执行 `fc-cache -f`，再重新打开面板。
- 支持 `.ttf`、`.otf`、`.ttc`、`.otc`。集合字体只抽取用到的那一个 face（用 `fontTools`），缓存到插件自己的运行目录。
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
