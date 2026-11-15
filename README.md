[English](README.en.md) | **简体中文**

# Decky 字体选择器

一个 [Decky Loader](https://github.com/SteamDeckHomebrew/decky-loader) 插件：把 Steam Deck 上已安装的任意字体应用到 Steam 大屏界面（主界面、MainMenu、快捷访问菜单），不写 `~/homebrew/themes`，不依赖 CSS Loader；tab 发现 / 验活 / 弹窗监听依赖 CEF 调试口（Decky Loader 本身也依赖它，需在 Steam 设置 → 开发者开启「CEF 远程调试」）。

**当前版本：1.4.7。** 单包四语言——面板跟随 Steam 界面语言，内置简体中文 / English / 日本語 / 한국어 四套词典；`fontTools` 4.68.0 已随包内置，无需另装任何依赖。

| Release 资产 | 插件目录 | 界面语言 | `fontTools` |
| --- | --- | --- | --- |
| `DeckyFontPicker-1.4.7.zip` | `DeckyFontPicker/` | 简体中文 / English / 日本語 / 한국어（自动） | 已放在 `vendor/` 下 |

## 安装

1. 从 [Releases](../../releases) 页面下载 `DeckyFontPicker-1.4.7.zip`。
2. 在 Decky Loader 里打开 **设置 → 开发者 → 从 ZIP 安装插件**，或把压缩包解压到 `~/homebrew/plugins/`。
3. 重载 Decky（或重启 `plugin_loader.service`），打开快捷访问菜单。

ZIP 内容就是：

```
DeckyFontPicker/plugin.json
DeckyFontPicker/main.py
DeckyFontPicker/face_extract.py
DeckyFontPicker/dist/index.js
DeckyFontPicker/package.json
DeckyFontPicker/vendor/fontTools/...
DeckyFontPicker/vendor/LICENSE-fontTools
```

环境要求：Decky Loader 3.x（运行时使用 `decky-frontend-lib` 3.25）与 Python 3，并需在 Steam 设置 → 开发者 开启「CEF 远程调试」（tab 发现 / 验活 / 弹窗监听都依赖该调试口）。系统层面不需要安装任何东西。

## 功能

- 列出 `fc-list` 报告的所有字体族，凡是被 fontconfig 收录的字体都会出现（系统包、`~/.local/share/fonts`、`~/.fonts` 等）。
- 分区：`收藏夹`，然后是 `Serif 衬线`、`Sans 无衬线`、`Mono 等宽`、`其他`，最后是 `全部字体`；空分区整块隐藏。
  - 语言胶囊 `西文` / `中文` / `日文` / `韩文` 按字体实际覆盖的语言过滤列表；每款字体的行内会显示 `lang` 覆盖（中文标签优先），以及「含中文 / 无中文」、粗体、等宽等标记。
  - 分区归属是启发式的：`Mono` 看 fontconfig 的 `spacing`（100 / 110）或等宽关键词，`Serif` / `Sans` 看族名关键词，其余进 `其他`；分区标题下的说明行会写明当前规则。
- 即时搜索字体族名与别名，每次加载 60 条、可继续加载。
- 收藏（**X**）、分区折叠（**Y**）、「当前字体」可收起为一行摘要，仍能看出当前字体与注入状态。
- 用 Decky 自身的 `inject_css_into_tab` 往三个目标注入 `<style>`：主页、`MainMenu_uid*`、`QuickAccess_uid*`，并在 Steam 重渲染时自动重放。
- `notificationtoasts_*` 通知弹窗（好友游玩 / 控制器连接等）也应用所选字体：通过浏览器级 websocket 事件监听弹窗创建 / 销毁即时注入，事件驱动、零轮询（前提是 CEF 远程调试开启）。
- 状态卡：三行目标状态 + 右侧刷新图标构成一张可聚焦卡，按 A / 点击即重新注入当前字体；启动注入失败后若自动注入成功，卡片会变成绿色「自动注入」，不再残留红色报错。
- **按语言注入**（高级）：西文与中日韩分别选字体，两个槽位各自抽取嵌入；西文槽可以自动剔除汉字字形，保证中文 / 日文 / 韩文文字始终走中日韩槽。关闭分槽时两个槽位会被清空并拆除当前注入，重新开启后两槽为空。
- **禁用假加粗**、**使用 CJK 标点符号** 两个子开关，以及 **恢复 Steam 默认字体**。
- **CEF 调试口常开**：1.4.7 起不再有开关，tab 发现 / 验活 / 弹窗监听全部走 Chrome DevTools 端口，uid 发现达到毫秒级；高级分区只保留只读的端口状态 / 错误提示行。
- 高级分区里的 **复制日志** 按钮，方便反馈问题。

## 注入怎么做的

插件用 `fontTools`（`.ttc` / `.otc` 走 `TTCollection`）把你选中的那一个 face 抽出来，缓存到 `~/homebrew/data/DeckyFontPicker/fonts/`，再由仅监听回环地址的按需 HTTP 服务供给浏览器。注入的 `@font-face` 每次都指向同一个真实 face，不受 fontconfig 族名解析影响。抽出的人脸只在 ≤ 3 MiB 且环境里恰好有 `brotli` 时才压成 `woff2`，否则保留 `.ttf`——完整中日韩字体始终走 `.ttf`。

写路径刻意保守：样式按标签页逐个添加 / 替换，注入记账走 Decky 自身，串行锁避免并发标签页互相覆盖。`state.json` 位于 `~/homebrew/settings/DeckyFontPicker/`，日志位于 `~/homebrew/logs/DeckyFontPicker/`。

CEF 调试口不可用期间，注入窗口按 0.5→1→2→4→8s 退避重试、总上限 60s 后放弃，下次 UI 模式变化或用户动作再开窗；约 180 行 legacy「CEF 关闭」兜底（1..320 get_tab_id 逐号扫描、_tab_exists、验活二次复核、扫描预算）已整体删除，不再有兜底路径，存档见 [docs/LEGACY_FALLBACK.md](docs/LEGACY_FALLBACK.md)。

## 操作

| 输入 | 动作 |
| --- | --- |
| A / 点击 | 应用聚焦的字体（开启按语言注入时弹出槽位菜单：应用西文 / 应用中文） |
| X | 加入 / 移出收藏 |
| Y | 收起当前分区 |
| B | 返回 |

## 仓库结构

```
DeckyFontPicker/     当前版本的源码（plugin.json、main.py、face_extract.py、dist/index.js、package.json、vendor/）
FontPickerEN/        1.2.0 时代的英文单语包，仅为旧 release 保留（不属于 1.4.7）
README.md            本文件（简体中文）
README.en.md         英文说明
docs/                LEGACY_FALLBACK.md 等归档文档
LICENSE              MIT
.gitignore
```

### 关于 `FontPickerEN/`

`FontPickerEN/` 是到 **1.2.0** 为止与中文包并列发布的英文单语包——那个时期一个界面语言对应一个包，且每个包内置「按名引用 / 抽取嵌入」两种投递方式。从 1.3.x–1.4.x 起，插件变成**单包四语言、只有一条投递路径（抽取嵌入）**，因此 `FontPickerEN/` 停留在 1.2.0：它不会随 1.4.7 重建，旧 release 资产也仍指向 1.2.0。保留它只是为了让 1.2.0 的下载仍然可复现。请不要把它与 1.4.7 版同时启用——两者注入同一批 Steam 标签页，同一时间只能启用一个。

## 字体来源与限制

- 字体来自 `fc-list` 输出；除 `fontTools` 外仓库不打包任何字体。装完字体后先 `fc-cache -f`，再重新打开面板。
- 支持 `.ttf`、`.otf`、`.ttc`、`.otc`；集合字体只抽取实际用到的那一个 face 并缓存。
- 插件不改系统字体、不改 fontconfig，只为当前 Steam 会话注入一段 CSS `@font-face` 覆盖。
- 在面板里「恢复 Steam 默认字体」会移除已注入样式，无需重启 Steam 或 Decky。
- 退出大屏幕回到桌面后不再自动补注入桌面主窗口；进入大屏与用户动作的注入行为不变。

## 许可

MIT —— 见 [LICENSE](LICENSE)。Copyright (c) 2026 Honchane.

包内附带 [fontTools](https://github.com/fonttools/fonttools) 4.68.0，同为 MIT 许可 —— 见 `vendor/LICENSE-fontTools`。
