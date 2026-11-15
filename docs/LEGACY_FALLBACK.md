# LEGACY_FALLBACK.md — 已移除的「CEF 关闭」探测回退代码存档

> 本文存档于 v1.4.6 之后的重构提交。这些代码曾用于在「CEF 远程调试不可用」时
> 兜底完成 tab 发现与验活；经过论证该场景在运行态不存在，已于重构中整体移除。
> 如需考古，请检出本提交的父提交（tag `v1.4.6`）。

## 一、为什么移除

1. **Decky Loader 自身依赖 CEF 调试口**。它向 steamwebhelper 注入、提供
   `get_tab_id` / `execute_in_tab` / `inject_css_into_tab` 等 RPC 的前提，就是
   Steam 设置 → 开发者 → 「CEF 远程调试」开启（`--remote-debugging-port`）。
2. **本插件完全依赖 Decky Loader**：插件前端、字体注入全部经由 Decky。
3. 因此「插件能工作 ⇒ CEF 调试口必然存在」，「CEF 关闭但 Decky 正常」在运行态
   是空集。「CEF 关闭」唯一真实的含义是 **Decky 与插件一起失效**，轮不到回退出场。
4. **运行日志实证**（2026-11-15 13:22:29-39，steamwebhelper 重启过渡期）：
   CEF HTTP 探测（`/json/list` 拒绝连接）与 legacy RPC（`注入 Steam 大屏幕模式 失败:
   timed out` ×3）**同时失败、同时恢复**——两条路径不存在独立存活的时刻。

## 二、被移除的代码清单（v1.4.6 及之前的行为）

| 代码 | 原行为 |
|---|---|
| `_CEF_FAST = {"on": False}` + `__init__` 里的 `cef_fast` 状态读取 | CEF 快速模式开关的运行时状态；`state.json` 的 `cef_fast` 键控制 |
| `set_cef_fast(enabled)` 方法 | 前端开关入口：切换状态、探测端口、启停弹窗监听、开一次注入窗口 |
| `_tab_exists(tab)` | CEF 可用时查标题集合；否则 `get_tab_id` RPC 逐个问 |
| `_scan_uid(hint, prefix, missed)` 的探测循环 | CEF 不可用时用 `ThreadPoolExecutor`（8 线程、16 一批）对 `MainMenu_uid1..320` / `QuickAccess_uid1..320` 逐号 `get_tab_id` 探测；先试上次 uid + 往后 60 个，`missed` 集合做事件内续传 |
| `_style_exists` 的 `get_tab_id` 复核 | `execute_in_tab` 返回无 "not found" 的 500 时，再用 `get_tab_id` 区分「tab 没了」和「RPC 抖动」 |
| `_uid_scan_budget` / `WINDOW_UID_SCAN_LIMIT = 2` | 每个注入窗口内每个动态目标最多 2 次 uid 扫描的预算（限制昂贵的探测） |
| `_scan_miss` 续传集合 | 本注入事件内已确认 miss 的 uid 记账，避免重复探测 |
| 常量 `UID_SCAN_MAX=320`、`UID_SCAN_AHEAD=60`、`_SCAN_WORKERS=8`、`_SCAN_CHUNK=16` | 探测范围与并发参数（实测 Decky legacy RPC 基本串行：8 线程 ≈1.5×，32 线程仅 1.7×） |

## 三、移除后保留的等效机制

| 机制 | 说明 |
|---|---|
| 注入窗口（`_inject_window`） | 模式变化/用户动作后按 0.5→1→2→4→8s 退避重试，总上限 60s |
| tab 验活 | `_style_exists` 经 `execute_in_tab` 读样式：明确 not found 判死，其余记 None 下轮再验；`_window_target` 先用 CEF 标题快照预判 tab 消亡 |
| uid 发现 | `_scan_uid(prefix)` 直接从 `/json/list` 标题取最大 uid，毫秒级 |
| 弹窗监听 | `_toast_watcher` 浏览器级 websocket，断线按 2/5/15/30s 退避自动重连 |
| 状态翻转日志 | `_cef_log_state` 仅在可用⇄不可用变化时打一行 |

## 四、语义变化提示

- CEF 调试口不可用期间：注入/验活会随窗口退避重试直到窗口到期（60s）放弃，
  下一次 UI 模式变化或用户动作再开窗——**不再有慢速但可用的兜底路径**。
- `_style_exists` 不再做 `get_tab_id` 二次确认：tab 消亡的判定依据是
  `execute_in_tab` 明确 not found，或 CEF 标题快照里该 tab 已消失；
  其余失败一律记「无法判断」交由下轮重试。
