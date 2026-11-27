from __future__ import annotations

import os
import re
import sys
import threading

# 搬迁兼容：本文件的 __file__ 现在指向 fp/ 子目录，而原 main.py 用 __file__ 回退插件根目录。
# 预置 DECKY_PLUGIN_DIR 让下方 PLUGIN_DIR 常量保持搬迁前的求值与语义（外部已设置则不覆盖）。


HOME = os.path.expanduser("~")


PLUGIN_DIR = os.environ.get("DECKY_PLUGIN_DIR") or os.path.dirname(os.path.abspath(__file__))


# 自带 fontTools 的变体：把插件目录下的 vendor 放到 sys.path 最前。
# 走的是 PLUGIN_DIR 计算值，所以插件目录改名或移动后依然生效，
# 也不依赖系统 site-packages / user site 里有没有 fontTools。
VENDOR_DIR = os.path.join(PLUGIN_DIR, "vendor")


if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)


SETTINGS_DIR = os.environ.get("DECKY_PLUGIN_SETTINGS_DIR") or os.path.join(
    HOME, "homebrew", "settings", "DeckyFontPicker"
)


RUNTIME_DIR = os.environ.get("DECKY_PLUGIN_RUNTIME_DIR") or os.path.join(
    HOME, "homebrew", "data", "DeckyFontPicker"
)


STATE_FILE = os.environ.get("FONTPICKER_STATE_FILE") or os.path.join(SETTINGS_DIR, "state.json")


# 本插件日志目录（Decky 的 logger 按插件名分目录写日志），面板「复制日志」从这里读最新文件
LOG_DIR = os.environ.get("DECKY_PLUGIN_LOG_DIR") or os.path.join(
    HOME, "homebrew", "logs", "DeckyFontPicker"
)


# 抽取出来的单 face 缓存，只放插件自己的目录
CACHE_DIR = os.environ.get("FONTPICKER_CACHE_DIR") or os.path.join(RUNTIME_DIR, "fonts")


# Decky 自己的本地服务（不是 Steam 的 CEF 调试口）
DECKY_HOST = "127.0.0.1"


DECKY_PORT = int(os.environ.get("SERVER_PORT", "1337"))


FC_FORMAT = "%{family}\t%{style}\t%{weight}\t%{file}\t%{index}\t%{fontformat}\t%{lang}\t%{spacing}\t%{charset}\n"


# 浏览器能直接用的格式（PCF/BDF/Type1 位图字体剔除）
USABLE_FORMATS = {"TrueType", "CFF", "CFF2", "WOFF", "WOFF2"}


COLLECTION_EXT = {".ttc", ".otc"}


DIRECT_EXT = {".ttf", ".otf", ".woff", ".woff2"}


MIME_BY_EXT = {
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


# fontconfig 的 weight 数值 -> CSS font-weight
_FC_WEIGHTS = [
    (0, 100), (40, 200), (50, 300), (55, 350), (75, 400), (80, 400),
    (100, 500), (180, 600), (200, 700), (205, 800), (210, 900), (215, 950),
]


# 抽出来的单 face 小于这个体积才再压成 woff2，否则直接用 ttf
# （CJK 全字集 ttc 单 face 转 woff2 实测约 54s，得不偿失）
WOFF2_MAX_BYTES = 3 * 1024 * 1024


# 抽取缓存上限：按文件算（不是按字体/字重算）。最坏实况：西文 ttc 三字重的
# 整 face + 剔字产物共 6 个 + 中文槽 ttc 三字重 3 个 = 9，上限从 2 提到 9；
# 启动清理改为按 state 反推保护集，清理的「保护优先于上限」逻辑不变
CACHE_KEEP_FILES = 9


CACHE_EXTS = (".ttf", ".otf", ".woff", ".woff2")


# 预览子集缓存：只保留字节上限，不再限制文件数（低配机预热几十个字体时，
# 40 文件上限会把一部分挤出缓存、反复冷抽面）。字节上限默认 8MB，高级分区
# 滑块可在 1-30MB 间调节，state 的 preview_cache_mb 为准。
PREVIEW_CACHE_DEFAULT_MB = 8


PREFERRED_CJK = [
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "Noto Sans CJK TC",
    "Noto Sans CJK JP",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Droid Sans Fallback",
]


EMOJI_FALLBACK = ["Twemoji", "Noto Color Emoji"]


# 注入模式：只保留抽取嵌入（local() 按名引用已弃用）
MODE_EXTRACT = "extract"


# 内部 family 名：注入时用它承载 @font-face，重新注入时旧的会被整体替换。
# 分槽注入用双族：西文槽挂 INTERNAL_FAMILY，CJK 槽挂 INTERNAL_FAMILY_CJK。
# 兜底内部族：西文槽剔字且 CJK 槽空时，西文字体的完整版 face 以这个族名
# 回填栈的 CJK 位置（@font-face 真绑定，不依赖按名系统字体解析）；
# 文件与西文槽共用同一份，不新增缓存命名。
# _build_css 一律引用这些常量（不写字面量）、payload 也从这里取，两处不会漂移。
INTERNAL_FAMILY = "DeckyFontPicker"


INTERNAL_FAMILY_CJK = "DeckyFontPicker-CJK"


INTERNAL_FAMILY_FULL = "DeckyFontPicker-Full"


STEAM_UI_FONT = "Motiva Sans"  # Steam 自带 UI 字体（PUA 图标字形在其中）。改名时改这一处，


# 列表样张懒加载：行内 / 展开块四语言样张用到的全部字符。后端按这张表把每个字体
# 抽成只含样张字形的极小子集（几 KB），经 FontServer 以 @font-face 提供给前端。
# Steam CEF 跑在 pressure-vessel 沙箱里，按 family 名引用系统字体一律 fallback，
# 只有 @font-face + 127.0.0.1 URL 这种真绑定才生效（当前全局注入已实证）。
# 子集产物放 CACHE_DIR/preview/ 子目录：主 face 的 9 文件 LRU 只扫 CACHE_DIR 顶层，
# 不会误删预览子集，预览子集也不占用那 9 个名额。
# 与前端 dist/index.js 的 SAMPLES 常量互指：改样张文本必须 bump #pv2
# （_prepare_preview_face 的缓存键盐）并同步本字符表。
PREVIEW_SAMPLE_CHARS = (
    "The quick brown fox jumps over 0123"
    "永和九年 岁在癸丑 汉字预览"
    "いろはにほへと 漢字カタカナ"
    "다람쥐 헌 쳇바퀴 한글"
)


PREVIEW_CACHE_SUBDIR = "preview"


# 方案 D：西文槽剔字掩码（基线版）。西文 face 的 cmap 命中这些区间时，
# 用 fontTools subset 剔除后再注入，保证字体栈里西文槽不会抢走中日韩字形。
# 区间由原 B 方案 CJK 表扩充而来（含 PUA）；v2 实机修订：补入谚文字母等
# 相邻区——实测谚文音节被剔后，HarfBuzz 会用保留的谚文字母（ㄷㅏㄴ）分解
# 渲染「한」，以栈首身份遮住 CJK 槽/Full 回填，故字母区必须一并剔掉。
LATIN_STRIP_RANGES_BASE = [
    (0x1100, 0x11FF),  # 谚文字母（v2）：无它则谚文音节被分解渲染，遮蔽 CJK 槽
    (0x2E80, 0x2FDF),  # CJK 部首与符号
    (0x3000, 0x312F),  # CJK 符号标点 + 平假名
    (0x3130, 0x318F),  # 谚文兼容字母
    (0x31A0, 0x31BF),  # 注音符号扩展（v2）
    (0x31C0, 0x31EF),  # CJK 笔画（v2）
    (0x31F0, 0x31FF),  # 片假名音标扩展
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 统一表意文字
    (0xA960, 0xA97F),  # 谚文字母扩展-A（v2）
    (0xAC00, 0xD7FF),  # 谚文音节
    (0xF900, 0xFAFF),  # CJK 兼容表意文字
    (0xFE10, 0xFE4F),  # 竖排形式 + CJK 兼容形式（v2 起点前移）
    (0xFF00, 0xFFEF),  # 全角/半角形式
    (0x1B000, 0x1B0FF),  # 日文补充长音符号/小假名（v2）
    (0x20000, 0x2FA1F),  # CJK 扩展 B~F
    (0xE000, 0xF8FF),  # 私有区（PUA）：Steam 图标字形在自带的 Motiva Sans 内，防自装字体抢
]


# 剔字掩码版本：掩码内容变更（如 v2 补入谚文字母区）时 +1，产物文件名随之
# 变化，旧产物自然失效并按陈旧缓存清理，不需要手工清缓存
LATIN_STRIP_MASK_VERSION = 2


# 三个注入目标。
# 主界面标题是稳定的；QuickAccess / MainMenu 在 Steam 里永远带动态后缀
# （实测 QuickAccess_uid2 … QuickAccess_uid236，随 CEF 上下文创建自增），
# 所以这两个目标需要先发现当前 uid，再用 "QuickAccess_uid<uid>" 精确注入。
TARGETS = [
    ("main", "Home", ["Steam 大屏幕模式", "Steam Big Picture Mode", "Steam", "SP"]),
    ("menu", "MainMenu", []),
    ("qam", "QuickAccess", []),
]


DYNAMIC_PREFIX = {"menu": "MainMenu_uid", "qam": "QuickAccess_uid"}


# 兜底 tab：注入成功也不代表大屏界面真的用上了样式，状态里一律不算「已注入」。
# SharedJSContext 已从 main 候选里移除，这里只作为历史 state / 未来兜底的防御。
FALLBACK_TABS = {"SharedJSContext"}


# 弹窗（好友上线 / 正在游玩提示）是 Steam 按需创建的独立 CEF 窗口，
# 标题形如 notificationtoasts_<n>。窗口生老病死全靠事件，现有注入窗口追不上，
# 所以挂一个浏览器级 websocket 监听 targetCreated /
# targetInfoChanged / targetDestroyed，命中弹窗立即注入 / 随销毁拆除。
# 纯事件驱动、零轮询。
TOAST_TAB_PREFIX = "notificationtoasts"


TOAST_WS_BACKOFF = (2.0, 5.0, 15.0, 30.0)  # websocket 断线重连退避，用尽后按 30 s


_LIVE = None  # 当前 Plugin 实例，供 Decky 以类为 self 回调时回落


# UI 模式事件驱动的有界注入窗口：前端订阅 Steam 的 UI 模式变化（大屏 4 / 桌面 7），
# 变化那一刻三个页面的文档都会销毁重建，只在这个时刻补注入；窗口之外后台完全静默。
UI_MODE_BIG_PICTURE = 4


UI_MODE_DESKTOP = 7


WINDOW_DELAY = 2.0  # 模式切换后先等页面回调建完


WINDOW_BACKOFF = (0.5, 1.0, 2.0, 4.0, 8.0)  # 单次窗口内的重试退避，用尽后按 8 s 继续


WINDOW_TOTAL = 30.0  # 单次窗口总时长上限：覆盖大屏切换后页面创建 + 登录慢导致的短暂延迟；到期后由用户动作或下一次 UI 模式变化重开窗


# CEF 调试口：直接读 Steam 的 /json/list，一次列出全部 tab。
# 前置条件：Steam 设置 → 开发者 → 「CEF 远程调试」（底层是给 steamwebhelper 加
# --remote-debugging-port）——Decky Loader 本身也依赖该调试口。
CEF_PORTS = (8080, 8081)


CEF_TIMEOUT = 0.5


CEF_CACHE_TTL = 0.3


CEF_TAB_RE = re.compile(r"^(MainMenu|QuickAccess)_uid(\d+)$")


_CEF_CACHE = {"at": 0.0, "tabs": None, "port": None, "why": "", "state": None}


_CEF_LOCK = threading.Lock()


_FAMILY_CACHE = {"at": 0.0, "data": None, "lock": threading.Lock()}


# 无样张字形字体的负缓存：键 (file, index) → 该 face 是否与样张字符集无交集。
# 生命周期 = 插件进程，与预览子集缓存键 sha1(file#index#pv2) 的陈旧性语义一致：
# 都不随字体文件中途变更而失效（进程内文件被替换的概率可忽略）。fail-open
# （读取异常视为有字形）与 charset 纯区间路径的判定结果同样入缓存——同一 face
# 反复判定没有意义。
_PREVIEW_NO_GLYPH_FACES = {}


# token 缓存：每次 RPC 都重新取一次会让请求数翻倍（320 次扫描 = 640 个请求）
_TOKEN_CACHE = {"value": None, "at": 0.0, "lock": threading.Lock()}


# 同一 tab 的注入 / 移除必须串行，避免并发下写出两个 style 元素、丢掉一个 css_id
_TAB_LOCKS = {}


_TAB_LOCKS_GUARD = threading.Lock()


# 我们在每个 tab 里写过、还未移除的 style（tab -> {css_id: True}）。
# 由工作线程在写成功后立即记账：协程被取消也不会丢掉已写入那份的 css_id。
_OWNED = {}


_FT_CACHE = {"at": 0.0, "ok": False, "version": None, "brotli_ok": False}


# 预览字体请求统计：按（字体服务端口 + family）记录 get_preview_font 调用次数与结果
_PREVIEW_STATS = {"lock": threading.Lock(), "calls": {}}


STATE_VERSION = 1  # state.json 结构版本号；无 _v 或低于此值视为旧档
