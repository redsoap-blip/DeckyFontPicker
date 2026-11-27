"""字体选择器（纯 Decky 注入版，抽取嵌入）

- 字体枚举：fc-list 按 face 读取（family/style/weight/file/index/format/lang）
- 投递方式只有抽取嵌入（extract）：
  @font-face + 本地 HTTP 服务（127.0.0.1 + 随机端口 + 随机 token），
  .ttc/.otc 用 fontTools 抽出实际用到的 face，缓存到插件自己的缓存目录。
- local() 按名引用模式已完全弃用：不再生成按名引用的 @font-face，也不再做命中探测。
- 样式注入：调用 Decky 自身的 legacy 方法 inject_css_into_tab / remove_css_from_tab
  （不写 ~/homebrew/themes，不依赖 CSS Loader）；tab 发现 / 验活 / 弹窗监听一律走
  CEF 调试口——Decky Loader 本身也依赖它，无需「CEF 关闭」的探测回退，
  被移除的 legacy 兜底代码见 docs/LEGACY_FALLBACK.md
- 例外：notificationtoasts_* 弹窗窗口由 CEF 调试口的浏览器级 websocket 事件监听
  注入（见 TOAST_TAB_PREFIX 处注释）
"""

from __future__ import annotations  # PEP 563：注解惰性化为字符串，永不运行期求值（TypedDict 类体注解与内建泛型注解均不再抬高解释器地板）

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fp import *  # noqa: F401,F403
from fp import __all__  # noqa: F401
