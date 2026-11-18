[English](README.en.md) | **简体中文**

# Decky 字体选择器

[![latest release](https://img.shields.io/github/v/release/redsoap-blip/DeckyFontPicker?label=release&color=blue)](../../releases)
[![License](https://img.shields.io/github/license/redsoap-blip/DeckyFontPicker)](LICENSE)
[![Decky Loader](https://img.shields.io/badge/Decky%20Loader-3.x-1a9fff)](https://github.com/SteamDeckHomebrew/decky-loader)

一个 [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) 插件：把 Steam Deck 上已经装好的任意字体应用到 Steam 大屏界面——主页、MainMenu、快捷访问菜单，以及好友上线这类通知弹窗。它不写 `~/homebrew/themes`，也不依赖 CSS Loader，样式由 Decky 自己的 `inject_css_into_tab` 注入，恢复默认时再拆掉。

**当前版本：1.4.19。** 单包四语言，面板跟随 Steam 界面语言；`fontTools` 4.68.0 已随包内置，不需要另装依赖。

## 安装

1. 到 [latest release](../../releases) 下载 `DeckyFontPicker-<版本>.zip`。
2. 任选一种装法：
   - Decky 设置 → 开发者 → 从 ZIP 安装插件；
   - 或把压缩包解压到 `~/homebrew/plugins/`。
3. 重载 Decky（或重启 `plugin_loader.service`），打开快捷访问菜单。

ZIP 里就是这些内容：

```
DeckyFontPicker/plugin.json
DeckyFontPicker/main.py
DeckyFontPicker/face_extract.py
DeckyFontPicker/dist/index.js
DeckyFontPicker/package.json
DeckyFontPicker/vendor/fontTools/...
DeckyFontPicker/vendor/LICENSE-fontTools
```

| 环境要求 | 说明 |
| --- | --- |
| Decky Loader | 3.x，运行时使用 `decky-frontend-lib` 3.25 |
| Python | 系统 Python 3，`face_extract.py` 由它执行 |
| CEF 远程调试 | 这是 **Decky Loader 自身**的前置条件（Steam 设置 → 开发者 → 「CEF 远程调试」）。能正常加载 Decky，就说明它已经开着，插件不需要你额外做任何设置 —— tab 发现、验活、弹窗监听直接复用这个调试口 |
| `fontTools` | 4.68.0，内置在 `vendor/` 下，不需要系统安装 |

## 功能

### 字体列表与筛选

- 列表来自 `fc-list`：凡是 fontconfig 收录的字体都会出现（系统包、`~/.local/share/fonts`、`~/.fonts` 等），浏览器用不了的位图格式（PCF / BDF / Type1）会被剔除。`fc-list` 结果有 10 秒缓存，重复打开面板不重新扫描。
- **字体名渲染自身字体**：每行字体名用该字体自己的字形显示（永远开启，无开关）——等宽字体的名字一字一格、衬线的名字带衬线脚，扫一眼就能分辨。实现上与预览样张共用极小子集懒加载（族名字符并入子集，仍只几 KB）；字体未加载或缺对应字形时逐字符回退默认字体，不会出豆腐块。
- 列表默认按安装时间排：非系统字体按字体落盘时间新→旧排在前（同一时间按族名字母序），系统字体（`/usr/share`、`/usr/local/share`）垫底并保持后端默认序；没有切换开关（时间取该族所有字体文件里最早的那个）。
- 分区（按当前构建的渲染顺序）：`全部字体`、`收藏夹`、`历史字体`、`Serif 衬线`、`Sans 无衬线`、`Mono 等宽`、`其他`。四个风格分区没有字体时整块隐藏；`全部字体`、`收藏夹`、`历史字体` 始终保留（历史为空时显示「暂无使用记录」）。
- **历史字体**：每次选字体都会记一笔，最近使用在前、去重、不设上限，跟着 `state.json` 持久化；已卸载的字体自动跳过。高级分区里的 `清除历史字体记录` 可以一次清空。
- 语言胶囊：`全部字体` 与四个风格分区展开后，标题下方各有一排 `西文` / `中文` / `日文` / `韩文`，显示各语言的款数，点击或左右键切换。没有「不筛选」态——默认就是西文。每款字体按单一主语言归入一个胶囊：族名里的地区标记（JP / KR / SC / TC / HK / CN）优先，其次按 zh > ja > ko，其余算西文。
- 行内标记：`含中文` / `无中文，自动回退`、`有 Bold` / `无 Bold（合成加粗）`、`等宽`、`★已收藏`、`当前使用`；搜索命中别名时多一行 `别名：xxx`。
- 即时搜索：主名与别名都查；上方常显 `共 N 款字体`（搜索时是命中数）。每个分区先显示 60 条，超出时给 `加载更多（还剩 N 款）`。没有命中时提示「没有匹配的字体（主名和别名都查过了）。」，清空搜索即回到分类浏览。
- 分区归属是启发式的，判定顺序 `Mono` → `Serif` → `Sans` → `其他`，四类互斥：`Mono` 先看 fontconfig 的 `spacing`（90 双宽 / 100 等宽 / 110 字符格），其次族名含 `mono` / `等宽` / `等寬` / `等幅` / `고정폭` / `fixed` 等关键词；`Serif` 看族名关键词（serif / song / ming / times / 楷 等，名字里含 `sans` 的排除）；`Sans` 看 sans / grotesk / gothic / 黑 等；其余进 `其他`。四个风格分区的说明行会写明当前规则（`全部字体` 没有说明行）。
- 分区默认折叠，标题行 A / 点击展开；在分区内按 Y 就地收起并把焦点送回标题。

### 注入与状态卡

- 三个注入目标：主页、`MainMenu_uid*`、`QuickAccess_uid*`。主页按候选标题（`Steam 大屏幕模式` / `Steam Big Picture Mode` / `Steam` / `SP`）在 CEF tab 列表里匹配；两个动态目标先按已知 uid 试，不行再从 CEF 的 tab 标题里取当前最大 uid。
- `notificationtoasts_*` 通知弹窗是 Steam 按需创建的独立窗口，由 CEF 浏览器级 websocket 事件（创建 / 标题变化 / 销毁）驱动：出现即注入，销毁即拆除，事件驱动、零轮询。
- 注入统一走 Decky 自身的 `inject_css_into_tab`，每个 tab 只保留一份样式；Steam 重建页面后，会在下一次注入窗口里补回来。
- 状态卡：顶部是当前字体（开启分槽时显示 `西文：` / `中日韩：` 两行，中日韩槽未选时显示「跟随系统」），下面三行目标各显示 `已注入` / `未注入`，右侧是刷新图标；整张卡是一个可聚焦项，A / 点击即重新注入当前字体。右侧摘要给出 `手动注入 · 时间`、`自动注入 · 时间`、`注入失败`，还没注入过时显示 `尚未注入`；若后端另有自动补注记录（摘要本身不是「自动注入」时），卡片上会多一行灰色的 `自动注入 · 时间`。
- 启动时注入失败、随后自动补注成功的，摘要会翻成绿色的「自动注入」，不残留红色报错。

### 按语言分槽

- 打开 `按语言注入` 后，A 会先弹出「应用西文 / 应用中文」小菜单，按字体语言预选：zh / ja / ko 预选中文，其余预选西文；B 取消，不写任何配置。
- 两个槽各自抽取嵌入。西文槽可以剔除中日韩与私用区字形（掩码覆盖 CJK 统一表意文字、假名、谚文、全角形式、PUA 等），保证中日韩文字走中日韩槽；西文标点（弯引号、省略号、间隔号）始终由西文槽渲染。
- 中日韩槽为空、西文槽又确实剔了字时，会把西文字体的完整版 face 以内部族 `DeckyFontPicker-Full` 回填到字体栈的中日韩位置。
- 关闭分槽会清空两个槽位（含注入记账）并拆除当前注入；重新开启后两槽为空，需要重新选。
- 西文槽剔字失败时回退原始字体，并在卡片下方给红色警告：可能遮蔽中日韩槽位，换一款纯西文字体可解决。

### 高级

- `字体预览样张`：三档滑块——0 关闭、1 单行（紧凑样张）、2 双行（常规 + 粗体两行样张）；低内存设备建议关闭或单行。样张由只保留样张文本码位的极小子集（几 KB）按需供给，不加载整个字体面。
- `聚焦展开预览`：开启后聚焦字体行时行内展开西 / 中 / 日 / 韩四语言大样张；旧版本的三档控制（含内嵌布局）已合并为这一个开关，旧配置自动迁移。
- `禁用假加粗`：默认开启，给注入规则加 `font-synthesis-weight: none`，没有粗体字重的字体不再被合成加粗。与分槽开关无关，始终显示。
- `按语言注入`：上面的分槽主开关。
- `恢复 Steam 默认字体`：拆除三个目标与弹窗的注入，清空两个槽位，不动任何开关；这是列表之后、高级分区之前的独立按钮。
- `复制日志`：读取最新日志的尾部（最多 200 行）并写入剪贴板，方便反馈问题。
- 调试端口只读状态行：`调试端口 <端口> · N 个 tab`；调试口未就绪时换成红色说明。面板不提供 CEF 开关 —— 调试口由 Decky Loader 保证，插件只报告状态。

## 操作

| 输入 | 动作 |
| --- | --- |
| A / 点击 | 字体行：应用聚焦的字体（分槽开启时弹出槽位菜单）；分区标题：展开 / 收起 |
| X | 加入 / 移出收藏 |
| Y | 收起当前分区 |
| 左 / 右 | 切换当前分区的语言胶囊 |
| B | 返回；分槽菜单里为取消 |

## 常见问题

**装完字体后列表里没有？** 字体没被 fontconfig 收录。先 `fc-cache -f` 再重开面板；PCF / BDF / Type1 这类位图字体不会被列出。

**中文或日文字体不生效、落回默认？** 未开分槽时，所选字体不含中文会在回退链里自动补一个已安装的中日韩族（优先 Noto Sans CJK 等，其次任意含中文的族），再接 emoji 与 `system-ui` / `sans-serif`；开了分槽，中日韩文字只由中日韩槽提供，请确认该槽已选字体。系统里确实没有可用中日韩字体时，只能落到 Steam 自带字体。

**提示 CEF 调试口未就绪？** 它只会在 Steam 刚启动、或 steamwebhelper 重载的那几十秒里出现。CEF 远程调试是 Decky Loader 的硬前提，**能加载 Decky 就说明它已经开着**，不用去设置里找开关。插件从 steamwebhelper 的启动参数读端口，读不到再试 8080 / 8081，并在 30 秒窗口内自动重试；窗口过了还没注入上，点一下状态卡重新注入即可。

**字体文件很大，会被压缩吗？** 只有抽取或剔字得到的单个 face、体积 ≤ 3 MiB 且环境里 `brotli` 可用时才会转成 woff2；完整的中日韩字体通常仍是 `.ttf`。未剔字的单文件字体（`.ttf` / `.otf`）直接由回环服务供给系统原文件，不做转换。

**会修改系统字体或 fontconfig 吗？** 不会。插件只在当前 Steam 会话里注入一段 CSS `@font-face` 覆盖，不碰系统字体目录、不写 fontconfig。

**怎么彻底还原？** 先在面板点 `恢复 Steam 默认字体`；要连痕迹一起清掉，再删 `~/homebrew/data/DeckyFontPicker/`（抽取缓存）、`~/homebrew/settings/DeckyFontPicker/state.json`（选择、收藏与历史）、`~/homebrew/logs/DeckyFontPicker/`（日志）。

**能和 `FontPickerEN/` 一起装吗？** 不能。两者注入同一批 Steam 标签页，同一时间只能启用一个。

**退出大屏回桌面后字体还在吗？** 桌面主窗口不再自动补注入；大屏页面销毁后样式随之消失，再进大屏时由 UI 模式事件重新补注，用户动作也照常注入。

## 工作原理

字体走「抽取嵌入」一条路径：未剔字的单文件字体直接引用系统原文件，`.ttc` / `.otc` 用 `fontTools`（`TTCollection`）抽出实际使用的那一个 face，缓存到 `~/homebrew/data/DeckyFontPicker/fonts/`，整个目录按最近使用只留 9 个文件（正在使用的计入名额、一定保留）。文件由只绑 `127.0.0.1`、随机端口 + 随机 token 的按需 HTTP 服务供给，注入的 `@font-face` 因此始终指向真实 face，不受 fontconfig 族名解析影响。

写路径刻意保守：每个 tab 一把串行锁，先清掉自己留下的旧样式再写唯一一份，注入记账走 Decky 自身；`state.json` 位于 `~/homebrew/settings/DeckyFontPicker/`，日志位于 `~/homebrew/logs/DeckyFontPicker/`。

注入是有界窗口：大屏 UI 模式变化（以及插件加载时恢复已保存字体）会开窗，先等 2 s，再按 0.5 → 1 → 2 → 4 → 8 s 退避重试，单次窗口上限 30 s，到期放弃；下一次 UI 模式变化再开新窗口，用户动作则直接重走一遍注入。到期仍未注入时，点一下状态卡重新注入或在快捷访问菜单里再切一次即可重开窗口。当前版本起已整体删除约 180 行「CEF 关闭」兜底路径（1..320 逐号扫描、验活二次复核、扫描预算），存档见 [docs/LEGACY_FALLBACK.md](docs/LEGACY_FALLBACK.md)。调试口未就绪时不会退化成按标题盲试 —— 三个目标一视同仁地等窗口重试。

## 兼容性与已知限制

- 字体来自 `fc-list`；仓库除 `fontTools` 外不打包任何字体。
- 支持 `.ttf`、`.otf`、`.ttc`、`.otc`；集合字体只抽实际使用的 face。
- 不改系统字体、不改 fontconfig，只为当前 Steam 会话注入样式。
- 退出大屏回到桌面后不再自动补注桌面主窗口；进入大屏与用户动作的注入行为不变。
- `FontPickerEN/`（1.2.0 冻结的英文单语包）不能与当前版本同时启用。
- CEF 远程调试是 Decky Loader 自身的前提，插件继承它 —— 不存在「Decky 能用但 CEF 关闭」这种组合，插件只在 Steam 启动早期等调试口就绪。

## 仓库结构

```
DeckyFontPicker/     当前版本源码（plugin.json、main.py、face_extract.py、dist/index.js、package.json、vendor/）
FontPickerEN/        1.2.0 冻结的英文单语包，仅为旧 release 可复现保留，不能与新版同时启用
README.md            本文件（简体中文）
README.en.md         英文说明
docs/                LEGACY_FALLBACK.md 等归档文档
LICENSE              MIT
.gitignore
```

## 许可

MIT —— 见 [LICENSE](LICENSE)。Copyright (c) 2026 Honchane.

包内附带 [fontTools](https://github.com/fonttools/fonttools) 4.68.0，同为 MIT 许可 —— 见 `vendor/LICENSE-fontTools`。
