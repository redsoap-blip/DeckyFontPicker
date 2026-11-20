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

import asyncio
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TypedDict
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    import decky

    _logger = decky.logger
except Exception:  # 本地跑测试时没有 decky 模块
    _logger = None


def log(message: str):
    if _logger is not None:
        try:
            _logger.info(f"[FontPicker] {message}")
            return
        except Exception:
            pass
    print(f"[FontPicker] {message}")


def _recent_log(limit: int = 200) -> dict:
    """读取本插件最新日志的尾部若干行（只读），供面板「复制日志」使用。"""
    try:
        names = [n for n in os.listdir(LOG_DIR) if n.endswith(".log")]
    except OSError as e:
        return {"ok": False, "error": f"日志目录不可读：{e.strerror or e}"}
    if not names:
        return {"ok": False, "error": "日志目录里还没有日志文件"}
    paths = [os.path.join(LOG_DIR, n) for n in names]
    try:
        latest = max(paths, key=os.path.getmtime)
        with open(latest, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
    except OSError as e:
        return {"ok": False, "error": f"日志读取失败：{e.strerror or e}"}
    total = len(lines)
    shown = lines[-limit:] if total > limit else lines
    text = "\n".join(shown)
    if total > limit:
        text = f"（仅保留最近 {limit} 行，共 {total} 行）\n" + text
    return {
        "ok": True,
        "file": os.path.basename(latest),
        "lines": len(shown),
        "total": total,
        "text": text,
    }


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
PREVIEW_CACHE_MAX_BYTES = 8 * 1024 * 1024

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
                               # 并同步前端 dist/index.js CSS 里的两处 Motiva Sans（该文件有互指注释）。

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


def _strip_variant():
    """固定使用基础剔字掩码的缓存后缀。"""
    return f".latin{LATIN_STRIP_MASK_VERSION}"

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


# --------------------------------------------------------------------------- #
# fontconfig 读取
# --------------------------------------------------------------------------- #

class _KeyedError(RuntimeError):
    """面向用户的错误：message 保留中文（日志用），key/args 供前端按语言显示。"""

    def __init__(self, key, args=(), message=None):
        self.key = key
        self.args_ = list(args)
        super().__init__(message or key)


def _elapsed_ms(t0):
    """自 t0（time.monotonic()）起经过的毫秒数，用于耗时日志。"""
    return int((time.monotonic() - t0) * 1000)


def _run(args, timeout=20):
    env = None
    if os.path.isdir(VENDOR_DIR):
        env = dict(os.environ)
        prev = env.get("PYTHONPATH")
        env["PYTHONPATH"] = VENDOR_DIR + (os.pathsep + prev if prev else "")
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def _css_weight(fc_weight) -> int:
    try:
        value = float(fc_weight)
    except (TypeError, ValueError):
        return 400
    best = min(_FC_WEIGHTS, key=lambda item: abs(item[0] - value))
    return best[1]


def _has_zh(lang):
    for tag in (lang or "").split("|"):
        if tag == "zh" or tag.startswith("zh-") or tag in ("yue",):
            return True
    return False


# 日文 / 韩文：fontconfig 里是 ja、ko（以及可能的 ja-*、ko-* 变体）；
# 注意 "kok"（孔卡尼语）不是韩文，所以只按整词或 "ko-" 前缀匹配，不做裸前缀
def _has_ja(lang):
    for tag in (lang or "").split("|"):
        if tag == "ja" or tag.startswith("ja-"):
            return True
    return False


def _has_ko(lang):
    for tag in (lang or "").split("|"):
        if tag == "ko" or tag.startswith("ko-"):
            return True
    return False


# 主语言：优先看族名里的地区标记（Noto ... CJK JP / SC 这类），JP→ja、KR→ko、SC/TC/HK/CN→zh。
# fontconfig 的 lang 是字母序（第一条通常是 aa），不能当“主语言”，所以没有地区标记时
# 返回空串，前端再回退到 zh > ja > ko 的覆盖规则。
def _primary_lang(family):
    for token in reversed(re.findall(r"[A-Za-z]+", family or "")):
        up = token.upper()
        if up == "JP":
            return "ja"
        if up == "KR":
            return "ko"
        if up in ("SC", "TC", "HK", "CN"):
            return "zh"
    return ""


# 「含中文」判定：不是中文主字体（lang 不含 zh），但 charset 与 CJK 统一表意文字
# 的交集达到这个码位数，才算「字符集里确实带汉字」，避免只带几个汉字就误入。
ZH_EXTRA_MIN_CHARS = 500
ZH_RANGES = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF))  # 扩展 A + 基本区


def _parse_charset(spec) -> list[tuple[int, int]]:
    """解析 fontconfig 的 charset（"20-7e a0-ff 4e00-9fff" 这种区间串）。"""
    ranges = []
    for token in (spec or "").split():
        lo, sep, hi = token.partition("-")
        try:
            start = int(lo, 16)
            end = int(hi, 16) if sep else start
        except ValueError:
            continue
        if end >= start:
            ranges.append((start, end))
    return ranges


def _cjk_coverage(ranges) -> int:
    """charset 区间与 CJK 统一表意文字区间的交集码位数（同一码位只算一次）。"""
    if not ranges:
        return 0
    merged = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    total = 0
    for zlo, zhi in ZH_RANGES:
        for lo, hi in merged:
            if hi < zlo or lo > zhi:
                continue
            total += min(hi, zhi) - max(lo, zlo) + 1
    return total


def _faces():
    """返回去重后的 face 列表。"""
    try:
        proc = _run(["fc-list", "-f", FC_FORMAT])
    except Exception as exc:  # pragma: no cover - 环境异常
        log(f"fc-list 失败: {exc}")
        return []
    if proc.returncode != 0:
        log(f"fc-list 返回 {proc.returncode}: {proc.stderr.strip()[:200]}")
        return []

    faces = []
    seen = set()
    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        families = [f.strip() for f in parts[0].split(",") if f.strip()]
        if not families:
            continue
        path = parts[3].strip()
        ext = os.path.splitext(path)[1].lower()
        fmt = parts[5].strip()
        if not path or not os.path.isfile(path):
            continue
        # face 文件 mtime（单位：秒），用于「最新安装」排序（family 取所有 face 的最小值）
        try:
            mtime = int(os.path.getmtime(path))
        except OSError:
            mtime = 0
        if ext not in DIRECT_EXT and ext not in COLLECTION_EXT:
            continue
        if fmt and fmt not in USABLE_FORMATS:
            continue
        try:
            index = int(parts[4].strip() or 0)
        except ValueError:
            index = 0
        try:
            spacing = int(float(parts[7].strip() or 0))
        except (IndexError, ValueError):
            spacing = 0
        zh = _has_zh(parts[6].strip())
        ja = _has_ja(parts[6].strip())
        ko = _has_ko(parts[6].strip())
        charset = parts[8] if len(parts) > 8 else ""
        # 已在「中文」类的字体不再进「含中文」，两类不重叠
        zh_extra = (not zh) and _cjk_coverage(_parse_charset(charset)) >= ZH_EXTRA_MIN_CHARS
        key = (families[0], parts[1].strip(), path, index)
        if key in seen:
            continue
        seen.add(key)
        faces.append(
            {
                "family": families[0],
                "aliases": families,
                "style": parts[1].strip(),
                "weight": _css_weight(parts[2]),
                "file": path,
                "mtime": mtime,
                "index": index,
                "format": fmt,
                "zh": zh,
                "ja": ja,
                "ko": ko,
                # fontconfig 的 lang（"zh-cn|en|ja" 这种），前端直接展示覆盖范围
                "lang": parts[6].strip(),
                "zh_extra": zh_extra,
                # fontconfig 的 spacing：90 = dual、100 = mono、110 = charcell 都按等宽处理
                # （实测 JetBrains Mono、霞鹜文楷等宽 标的是 90）；0/其它 = 比例字体
                "mono": spacing in (90, 100, 110),
                "collection": ext in COLLECTION_EXT,
            }
        )
    return faces


# 系统字体目录：全部 face 都落在这些目录下时，family 视为「系统自带」
_SYSTEM_FONT_DIRS = ("/usr/share", "/usr/local/share")


def _is_system_face_path(path):
    """face 文件是否位于系统字体目录（/usr/share 或 /usr/local/share）。"""
    p = (path or "").rstrip("/")
    return any(p == d or p.startswith(d + "/") for d in _SYSTEM_FONT_DIRS)


def _family_map_build():
    """family 映射构建（走 fc-list 子进程，较慢，由 _family_map 缓存包装）。"""
    families = {}
    for face in _faces():
        entry = families.setdefault(
            face["family"],
            {"name": face["family"], "aliases": [], "faces": [], "langs": [], "zh": False, "ja": False, "ko": False, "primary": _primary_lang(face["family"]), "zh_extra": False, "mono": False, "install_mtime": None, "is_system": True},
        )
        # 每个 face 的 family 字段会带上该 face 自己的派生名（如 "Fira Sans Light"），
        # 这里按 family 合并成别名集合，仅用于前端搜索与显示，不参与选面。
        for alias in face["aliases"]:
            if alias != face["family"] and alias not in entry["aliases"]:
                entry["aliases"].append(alias)
        entry["faces"].append(face)
        # lang 覆盖按 face 顺序去重合并，保留 fontconfig 的原始顺序
        for tag in face["lang"].split("|"):
            tag = tag.strip()
            if tag and tag not in entry["langs"]:
                entry["langs"].append(tag)
        entry["zh"] = entry["zh"] or face["zh"]
        entry["ja"] = entry["ja"] or face["ja"]
        entry["ko"] = entry["ko"] or face["ko"]
        entry["zh_extra"] = entry["zh_extra"] or face["zh_extra"]
        entry["mono"] = entry["mono"] or face["mono"]
        # 安装时间取所有 face 文件 mtime 的最小值（最早出现的那个文件，单位秒）；
        # 只有全部 face 都在系统字体目录下才算系统字体
        mtime = face.get("mtime") or 0
        if mtime and (entry["install_mtime"] is None or mtime < entry["install_mtime"]):
            entry["install_mtime"] = mtime
        if not _is_system_face_path(face["file"]):
            entry["is_system"] = False
    return families


# ── fc-list 结果缓存 ──
# fc-list 子进程约 100–300ms，而 _family_map 被 set_font / 面板刷新 / 注入流程高频调用；
# TTL 内直接复用上次结果，装/删系统字体后最多 FAMILY_TTL 秒内可见，面板重开不受影响。
# 模式与 _TOKEN_CACHE / _FT_CACHE 一致：双检 + 锁，避免并发线程重复跑 fc-list。
FAMILY_TTL = 10.0
_FAMILY_CACHE = {"at": 0.0, "data": None, "lock": threading.Lock()}


def _family_map(force=False) -> "dict[str, FamilyEntry]":
    """返回 family 映射；TTL 内复用缓存，force=True 强制重跑 fc-list。"""
    now = time.time()
    if not force and _FAMILY_CACHE["data"] is not None and now - _FAMILY_CACHE["at"] < FAMILY_TTL:
        return _FAMILY_CACHE["data"]
    with _FAMILY_CACHE["lock"]:
        now = time.time()
        if not force and _FAMILY_CACHE["data"] is not None and now - _FAMILY_CACHE["at"] < FAMILY_TTL:
            return _FAMILY_CACHE["data"]
        _t0 = time.monotonic()
        _FAMILY_CACHE["data"] = _family_map_build()
        log(f"[耗时] 字体列表重建（fc-list）: {_elapsed_ms(_t0)} ms，共 {len(_FAMILY_CACHE['data'])} 个族")
        _FAMILY_CACHE["at"] = now
        return _FAMILY_CACHE["data"]


def _lang_display_order(tags) -> list:
    """lang 展示顺序：中文 → 英文 → 常见西文/日韩 → 其它（同组内保持 fontconfig 原顺序）。

    fontconfig 的 lang 按字母排序，直接截前几个只会看到 aa/ab/af 这类冷门项，
    对用户没有意义，所以只在展示层重新分组。
    """
    common = [
        "en", "de", "fr", "es", "it", "pt", "nl", "ru",
        "pl", "tr", "sv", "da", "fi", "nb", "cs", "ja", "ko",
    ]
    rank = {tag: i for i, tag in enumerate(common)}

    def group(tag):
        t = tag.lower()
        base = t.split("-")[0]
        if t == "zh" or t.startswith("zh-") or t == "yue":
            return 0
        if base == "en":
            return 1
        if base in rank:
            return 2
        return 3

    def order(tag):
        t = tag.lower()
        base = t.split("-")[0]
        return (group(tag), rank.get(base, 99))

    return sorted(tags, key=order)


# --------------------------------------------------------------------------- #
# 类型注解（纯注解，运行期无强校验）
#
# TypedDict 只给类型检查器/读者描述字典结构，运行期不做任何键或类型校验：
# 缺键、多键、类型不符都不会报错，实际行为与本文件 1.4.19 完全一致。
# 字段以下方构造点的实码为准（_faces / _family_map_build / Plugin._payload）；
# total=False 表示所有键都可能缺省（face 按条件带键、payload 失败路径才补键）。
# --------------------------------------------------------------------------- #


class FaceDict(TypedDict, total=False):
    """_faces() 构造的单个 face（_family_map_build 原样收进 entry["faces"]）。"""

    family: str  # face 自己的派生族名（如 "Fira Sans Light"），映射键
    aliases: list[str]  # 全部族名（含主名），仅用于搜索/显示，不参与选面
    style: str  # fontconfig 的样式名（Regular / Bold / Italic …）
    weight: int  # _css_weight 折算的 CSS 字重（400/500/700 …）
    file: str  # 字体文件绝对路径
    mtime: int  # 文件 mtime（秒），「最新安装」排序用，取不到为 0
    index: int  # 集合字体里的 face 序号，单文件为 0
    format: str  # fontconfig 报的格式（TrueType / CFF …）
    zh: bool  # lang 含中文
    ja: bool  # lang 含日文
    ko: bool  # lang 含韩文
    lang: str  # fontconfig 的原始 lang 串（"zh-cn|en|ja" 这种）
    zh_extra: bool  # 非 zh 字体但 charset 带足量汉字（「含中文」类）
    mono: bool  # 等宽（fontconfig spacing 90/100/110 都算）
    collection: bool  # 是否 .ttc/.otc 集合（抽取嵌入需要 fontTools）


class FamilyEntry(TypedDict, total=False):
    """_family_map_build() 构造的 family 映射值。"""

    name: str  # 主族名（等于映射键）
    aliases: list[str]  # 按 family 合并去重后的族名集合（含主名）
    faces: list[FaceDict]  # 该族全部 face
    langs: list[str]  # 全部 face 的 lang 标签去重合并（保持 fontconfig 原顺序）
    zh: bool  # 任一 face 含中文
    ja: bool  # 任一 face 含日文
    ko: bool  # 任一 face 含韩文
    primary: str  # 族名地区标记推断的主语言（zh/ja/ko），推断不出为空串
    zh_extra: bool  # 任一 face 属「含中文」
    mono: bool  # 任一 face 等宽
    install_mtime: int | None  # 所有 face mtime 的最小值（秒），还没有 face 时 None
    is_system: bool  # 所有 face 都在系统字体目录下（缺省 True，遇外部目录置 False）


class StatePayload(TypedDict, total=False):
    """Plugin._payload() 返回的面板状态（_apply 等失败路径会再补 error* 键）。"""

    ok: bool  # _payload 恒 True；失败路径翻 False 并补 error / error_key / error_args
    inject_level: str  # _inject_status 四态：done / healed / failed / none
    inject_text: str  # 最近一次用户注入的时间戳文案
    heal_text: str  # 最近一次自动补注的时间戳文案
    mode: str  # 投递模式，恒 MODE_EXTRACT
    family: str | None  # 当前注入的族名，未注入为 None
    family_cjk: str | None  # CJK 槽族名（state 原值），未设置为 None
    has_css: bool  # 是否已有注入的 CSS
    targets: list  # 注入目标逐项状态（key/label/tab/ok/fallback）
    server: dict  # {"running": bool, "port": int}
    woff2_ok: bool  # 系统 Python 有无 brotli（woff2 压缩可用性），前端提示行用
    fallback: list  # 回退链族名列表（_detail["fallback"]，无 detail 时 []）
    zh: bool | None  # 所选族是否含中文（_detail["zh"]，未注入为 None）
    regular: dict | None  # 西文 Regular 槽 face 详情（face_info），无则 None
    medium: dict | None  # 西文 Medium 槽 face 详情，无则 None
    bold: dict | None  # 西文 Bold 槽 face 详情，无则 None
    cjk: dict | None  # CJK 槽 face 详情，无则 None
    cjk_split: bool  # 分槽注入开关
    synth_weight_none: bool  # 去掉合成粗斜体开关（缺省开）
    preview: bool  # 预览开关（档位非 0）
    preview_level: int  # 预览档位
    expand_level: int  # 预览展开档位
    preview_cache_mb: int  # 预览子集缓存字节上限（MB），1-30，默认 8（高级分区滑块）
    strip: dict  # 剔字记账 {"done": bool, "fallback": bool}
    full_backfill: bool  # 完整版 face 是否以 Full 内部族回填
    uid: str | None  # 当前已发现的 UI uid
    favorites: list  # 收藏列表（清洗后）
    history: list  # 历史记录
    cef: dict  # {"port": int|None, "tabs": int|None, "error": str}
    # ---- 条件键：失败 / 卸载路径才出现 ----
    error: str  # 失败原因（人类可读中文）
    error_key: str  # 前端按语言显示用的错误键（err.*）
    error_args: list  # 错误键的插值参数
    removed: int  # 卸载路径删除的样式条数


def list_families(families=None) -> list[dict]:
    out = []
    for name, entry in (families if families is not None else _family_map()).items():
        weights = sorted({f["weight"] for f in entry["faces"]})
        langs = _lang_display_order(entry["langs"])
        item = {
            "family": name,
            "aliases": entry["aliases"],
            "zh": entry["zh"],
            "weights": weights,
            "has_bold": any(w >= 600 for w in weights),
            "faces": len(entry["faces"]),
            # 前几个 lang 标签 + 总数，前端一行内展示覆盖范围（完整列表太大）
            "langs": langs[:8],
            "lang_count": len(langs),
        }
        if entry["mono"]:
            item["mono"] = True  # 只在等宽时才带这个键，减小返回体
        if entry["ja"]:
            item["ja"] = True  # 同上：只在 lang 含日文时才带
        if entry["ko"]:
            item["ko"] = True  # 同上：只在 lang 含韩文时才带
        if entry["primary"]:
            item["primary"] = entry["primary"]  # 族名地区标记给出的主语言（zh / ja / ko）
        if entry["zh_extra"]:
            item["zh_extra"] = True  # 同理：只在命中「含中文」时才带这个键
        # 「最新安装」排序用的字段：
        # install_mtime = 该 family 所有 face 文件 mtime 的最小值（最早落盘的文件，单位：秒）
        if entry.get("install_mtime") is not None:
            item["install_mtime"] = int(entry["install_mtime"])
        # is_system = 所有 face 都在 /usr/share 或 /usr/local/share 下（缺省视为 false）
        if entry.get("is_system"):
            item["is_system"] = True
        out.append(item)
    # 排序：中文 → 含中文 → 其它（仅影响「全部字体」列表的先后）
    out.sort(key=lambda item: (0 if item["zh"] else 1 if item.get("zh_extra") else 2, item["family"].lower()))
    return out


def _face_score(face):
    """倾斜/压缩/非常规字形排在后面。"""
    style = (face.get("style") or "").lower()
    italic = 2 if ("italic" in style or "oblique" in style) else 0
    width = 1 if any(w in style for w in ("condensed", "compressed", "narrow", "expanded", "wide")) else 0
    plain = 0 if ("regular" in style or "normal" in style or "book" in style) else 1
    return (italic, width, plain)


def _pick_faces(entry, prefer_zh=True) -> "tuple[FaceDict | None, FaceDict | None, FaceDict | None]":
    """挑 Regular(≈80) / Medium(≈100) / Bold(≈200)，返回三元组。

    prefer_zh 控制是否优先在「含中文字形」的 face 里挑：CJK 槽保持 True（1.4.2 行为）；
    西文槽分槽注入时传 False —— 否则抽一个带汉字的西文字体会优先挑到它里面
    带中文的那个 face（坑 4），剔字前它会以栈首身份遮蔽中日韩回退。
    Medium 只在家族里确实有 CSS 500 的 face（fc ≈100，如 Noto Medium）时返回，
    绝不拿 400/700 凑数；与 Regular 同一个 face 时置空，不重复注册。
    """
    faces = entry["faces"]
    if prefer_zh:
        zh_faces = [f for f in faces if f["zh"]]
        pool = zh_faces or faces
    else:
        pool = faces

    def closest(target, minimum=None, exact=None):
        candidates = [f for f in pool if minimum is None or f["weight"] >= minimum]
        if exact is not None:
            candidates = [f for f in candidates if f["weight"] == exact]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda f: (abs(f["weight"] - target),) + _face_score(f) + (f["collection"],),
        )

    regular = closest(400)
    medium = closest(500, exact=500)
    bold = closest(700, minimum=600)
    if medium is not None and regular is not None and medium["file"] == regular["file"] and medium["index"] == regular["index"]:
        medium = None  # 同一个 face 不需要重复注册
    if bold is not None and regular is not None and bold["file"] == regular["file"] and bold["index"] == regular["index"]:
        bold = None  # 同一个 face 不需要重复注册
    return regular, medium, bold


def _pick_auto_cjk(families, family):
    """现行为的自动 CJK 回退：PREFERRED_CJK 里第一个已安装且非自身的族，
    兜底再挑任意一个 zh 族。返回 0 或 1 个族名的列表。"""
    for name in PREFERRED_CJK:
        if name in families and name != family:
            return [name]
    for name, entry in families.items():
        if entry["zh"] and name != family:
            return [name]
    return []


def _build_chain(families, family, has_zh):
    """1.4.2 行为的回退链：所选字体不含中文时自动补一个已安装的 CJK 族，
    再固定接 emoji 与通用回退。返回值不含内部族名。"""
    chain = _pick_auto_cjk(families, family) if not has_zh else []
    chain.extend(EMOJI_FALLBACK)
    chain.extend(["system-ui", "sans-serif"])
    return chain


def _fallback_families(families, family, has_zh, split_on=False, cjk_family=None):
    """主开关开时在现行为之上做分槽调整；关时逐字节等价于 1.4.2 的 _build_chain。

    - 分槽开 + CJK 槽已选：中日韩字形由 DeckyFontPicker-CJK 内部族承担，不再自动补；
    - 分槽开 + CJK 槽未选（跟随系统）：西文槽已剔字、不再覆盖 CJK，
      无条件自动补一个 PREFERRED_CJK（剔字后 has_zh 的前提已不成立）；
    - 分槽开（两种情况都）：追加 "Motiva Sans" —— Steam 自带字体（clientui.uifont），
      PUA 图标字形打包在它里面，剔字掩码清掉 PUA 后图标由这里兜底；
      Steam 改名时这里要跟着调整。
    - 分槽开 + CJK 槽空 + 西文槽剔字时，_prepare_extract 会把这里的自动补族
      替换为 INTERNAL_FAMILY_FULL 完整版 @font-face 回填（见该函数）。
    """
    if not split_on:
        return _build_chain(families, family, has_zh)
    if cjk_family:
        chain = []
    else:
        chain = _pick_auto_cjk(families, family)
    chain.append(STEAM_UI_FONT)
    chain.extend(EMOJI_FALLBACK)
    chain.extend(["system-ui", "sans-serif"])
    return chain


# --------------------------------------------------------------------------- #
# 字体资源：抽取 / 压缩 / 缓存
# --------------------------------------------------------------------------- #

def _system_python():
    for candidate in ("/usr/bin/python3", "/usr/bin/python", "python3"):
        if candidate.startswith("/") and os.path.exists(candidate):
            return candidate
    return "python3"


def _helper():
    return os.path.join(PLUGIN_DIR, "face_extract.py")


def _cache_key(face):
    try:
        st = os.stat(face["file"])
        stamp = f'{face["file"]}|{face["index"]}|{st.st_mtime_ns}|{st.st_size}'
    except OSError:
        stamp = f'{face["file"]}|{face["index"]}'
    return hashlib.sha1(stamp.encode("utf-8")).hexdigest()[:16]


def _touch(path):
    """刷新 mtime，作为「最近使用时间」记录。"""
    try:
        os.utime(path, None)
    except OSError:
        pass


def _human_size(size):
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _is_cache_file(path):
    if not path:
        return False
    return os.path.dirname(os.path.abspath(path)) == os.path.abspath(CACHE_DIR)


def _prune_font_cache(protect=(), keep=CACHE_KEEP_FILES):
    """只保留最近使用的 keep 个缓存 face 文件，其余删除。

    protect 里的文件（正在注入/刚写入）一定保留，并计入 keep 名额。
    按 mtime 排序（命中缓存时会 _touch 刷新，所以 mtime 即最近使用时间）。
    只处理缓存目录内 *.ttf/*.otf/*.woff/*.woff2，不删目录、不碰其它目录；
    删除失败只记日志，不抛异常影响注入。返回删除数量。
    *.part 为 helper 崩溃残留的临时文件（face_extract.py 原子改名前写 out+".part"），
    超 5 分钟的在此收尾删除。
    """
    protected = {os.path.abspath(p) for p in protect if p}
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return 0
    # helper 崩溃残留的 *.part 临时文件（face_extract.py 写 out+".part"，原子改名前
    # 崩溃即残留）。不占 LRU 名额，单独按超时收尾：超过 5 分钟的删掉，新鲜的留着
    # （可能是正在写入的）。
    now_ts = time.time()
    part_removed = 0
    for name in names:
        if not name.endswith(".part"):
            continue
        part_path = os.path.join(CACHE_DIR, name)
        try:
            st = os.stat(part_path)
        except OSError:
            continue
        if not os.path.isfile(part_path) or now_ts - st.st_mtime <= 300:
            continue
        try:
            os.remove(part_path)
        except OSError as exc:
            log(f"缓存清理：残留临时文件 {name} 删除不了（{exc.strerror or exc}），先留着")
            continue
        part_removed += 1
        log(f"缓存清理：删除残留临时文件 {name}（写入未收尾超过 5 分钟）")
    entries = []
    for name in names:
        if not name.lower().endswith(CACHE_EXTS):
            continue
        path = os.path.join(CACHE_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        entries.append((os.path.abspath(path), st))
    entries.sort(key=lambda item: item[1].st_mtime_ns, reverse=True)
    keepers = [p for p, _ in entries if p in protected]
    others = [p for p, _ in entries if p not in protected]
    slots = max(0, keep - len(keepers))
    keepers.extend(others[:slots])
    removed = 0
    for path in others[slots:]:
        name = os.path.basename(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        try:
            os.remove(path)
        except OSError as exc:
            log(f"缓存清理失败：{name}（{_human_size(size)}）删除不了（{exc.strerror or exc}），先留着")
            continue
        removed += 1
        log(f"缓存清理：删除 {name}（{_human_size(size)}），原因：缓存上限 {keep} 个 face 文件，它不在最近使用之列")
    if removed or part_removed:
        extra = f"，另有残留临时文件 {part_removed} 个" if part_removed else ""
        log(f"缓存清理：原有 {len(entries)} 个，保留 {len(keepers)} 个，删除 {removed} 个{extra}")
    return removed + part_removed


def _woff2_or_ttf(ttf, woff2, timed=False):
    """woff2 尾块唯一入口：合法 ttf 已在手，能压则压（成功即删 ttf），
    失败或超尺寸回退注入 ttf。返回 (路径, MIME)。"""
    if os.path.getsize(ttf) > WOFF2_MAX_BYTES:
        return ttf, "font/ttf"
    _t0 = time.monotonic()
    try:
        proc = _run([_system_python(), _helper(), "woff2", ttf, woff2], timeout=180)
    except Exception as exc:
        log(f"woff2 压缩异常（{os.path.basename(ttf)}）: {exc!r}，回退注入 ttf")
        return ttf, "font/ttf"
    if timed:
        log(f"[耗时] woff2 压缩 {os.path.basename(ttf)}: {_elapsed_ms(_t0)} ms rc={proc.returncode}")
    if proc.returncode == 0 and os.path.exists(woff2) and os.path.getsize(woff2) > 0:
        try:
            os.remove(ttf)
        except OSError:
            pass
        return woff2, "font/woff2"
    return ttf, "font/ttf"


def _ensure_font_file(face):
    """返回 (本地文件路径, MIME)。单文件直接引用系统文件，集合抽出单个 face 再缓存。"""
    path = face["file"]
    ext = os.path.splitext(path)[1].lower()
    if ext in DIRECT_EXT:
        return path, MIME_BY_EXT.get(ext, "font/ttf")

    os.makedirs(CACHE_DIR, exist_ok=True)
    key = _cache_key(face)
    ttf = os.path.join(CACHE_DIR, f"{key}.ttf")
    woff2 = os.path.join(CACHE_DIR, f"{key}.woff2")

    if os.path.exists(woff2) and os.path.getsize(woff2) > 0:
        _touch(woff2)  # 命中缓存 = 一次使用，刷新 LRU 时间
        return woff2, "font/woff2"
    if os.path.exists(ttf) and os.path.getsize(ttf) > 0:
        _touch(ttf)
    if not (os.path.exists(ttf) and os.path.getsize(ttf) > 0):
        _t0 = time.monotonic()
        proc = _run(
            [_system_python(), _helper(), "extract", path, str(face["index"]), ttf],
            timeout=180,
        )
        log(f"[耗时] 抽取 {os.path.basename(path)}#{face['index']}: {_elapsed_ms(_t0)} ms rc={proc.returncode}")
        if proc.returncode != 0 or not os.path.exists(ttf):
            raise RuntimeError(f"抽取 {os.path.basename(path)}#{face['index']} 失败: {proc.stderr.strip()[:200]}")

    return _woff2_or_ttf(ttf, woff2, timed=True)


def _ensure_latin_file(face):
    """西文槽的剔字版 face（方案 D）。返回 (路径, MIME, 剔字发生, 剔字失败)。

    - 读 face cmap 与剔除掩码求交：交集为空 → 原样使用（绝大多数西文字体，
      helper 退出码 0 且不产出文件，无剔字开销）；
    - 交集非空 → fontTools subset 剔字，产物按掩码版本命名缓存
      （{key}.latin{掩码版本}.ttf/.woff2，掩码内容变更时版本号 +1 自动重剔）；
    - 失败（退出码≠0 或产物缺失/为空）→ 回退原始 face，strip_fallback=True，
      调用方据此给面板警告，绝不静默。剔除掩码常量在模块级，区间串传给 helper。
    """
    variant = _strip_variant()
    key = _cache_key(face)
    ttf = os.path.join(CACHE_DIR, f"{key}{variant}.ttf")
    woff2 = os.path.join(CACHE_DIR, f"{key}{variant}.woff2")
    ranges = LATIN_STRIP_RANGES_BASE
    strip_arg = ",".join(f"{lo:X}-{hi:X}" if hi > lo else f"{lo:X}" for lo, hi in ranges)

    if os.path.exists(woff2) and os.path.getsize(woff2) > 0:
        _touch(woff2)  # 命中缓存 = 一次使用，刷新 LRU 时间
        return woff2, "font/woff2", True, False
    if os.path.exists(ttf) and os.path.getsize(ttf) > 0:
        _touch(ttf)
    if not (os.path.exists(ttf) and os.path.getsize(ttf) > 0):
        try:
            _t0 = time.monotonic()
            proc = _run(
                [_system_python(), _helper(), "subset", face["file"], str(face["index"]), ttf, strip_arg],
                timeout=180,
            )
            rc, err = proc.returncode, proc.stderr.strip()[:200]
            log(f"[耗时] 剔字子集 {os.path.basename(face['file'])}#{face['index']}"
                f"（基础掩码）: {_elapsed_ms(_t0)} ms rc={rc}")
        except Exception as exc:  # 起进程失败按剔字失败处理
            rc, err = 1, str(exc)
        produced = os.path.exists(ttf)
        if rc != 0 or (produced and os.path.getsize(ttf) <= 0):
            log(f"西文槽剔字失败（{os.path.basename(face['file'])}#{face['index']}）: {err}，回退原始 face")
            path, mime = _ensure_font_file(face)
            return path, mime, False, True
        if not produced:
            # helper 的约定：退出码 0 且无产物 = cmap 与掩码无交集，原样使用
            path, mime = _ensure_font_file(face)
            return path, mime, False, False

    path, mime = _woff2_or_ttf(ttf, woff2)
    return path, mime, True, False


def _preview_cache_dir():
    """预览子集专用目录：避开主 face 的 9 文件 LRU（只扫 CACHE_DIR 顶层）。"""
    return os.path.join(CACHE_DIR, PREVIEW_CACHE_SUBDIR)


def _prune_preview_cache(limit_bytes=None):
    """preview 子集缓存回收：按 mtime LRU 保留最近使用，只按字节总量上限
    （默认取 state 的 preview_cache_mb，1-30MB 可调），不再限制文件数；
    *.part 为 helper 崩溃残留,超 5 分钟收尾删除。
    只扫 CACHE_DIR/preview/ 子目录,不碰主 face 的 9 文件 LRU。返回删除数量。"""
    if limit_bytes is None:
        limit_bytes = _preview_cache_mb_of(read_state()) * 1024 * 1024
    d = _preview_cache_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    now_ts = time.time()
    removed = 0
    for name in names:
        if not name.endswith(".part"):
            continue
        path = os.path.join(d, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path) or now_ts - st.st_mtime <= 300:
            continue
        try:
            os.remove(path)
        except OSError as exc:
            log(f"缓存清理：残留临时文件 {name} 删除不了（{exc.strerror or exc}），先留着")
            continue
        removed += 1
        log(f"缓存清理：删除残留临时文件 {name}（写入未收尾超过 5 分钟）")
    entries = []
    for name in names:
        if not name.lower().endswith((".ttf", ".woff2")):
            continue
        path = os.path.join(d, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        entries.append((path, st))
    entries.sort(key=lambda item: item[1].st_mtime_ns, reverse=True)
    kept_count = 0
    kept_bytes = 0
    for path, st in entries:
        if kept_bytes + st.st_size <= limit_bytes:
            kept_count += 1
            kept_bytes += st.st_size
            continue
        try:
            os.remove(path)
        except OSError as exc:
            log(f"缓存清理：preview 子集 {os.path.basename(path)} 删除不了（{exc.strerror or exc}），先留着")
            continue
        removed += 1
    if removed:
        log(f"缓存清理：preview 子集原有 {len(entries)} 个，保留 {kept_count} 个"
            f"（{_human_size(kept_bytes)} / 上限 {_human_size(limit_bytes)}），删除 {removed} 个")
    return removed


def _prepare_preview_face(family):
    """返回 (本地路径, MIME)：只含样张字符的极小子集，命中缓存直接复用。

    前端通过 get_preview_font RPC 按需调用，只有真正滚进视口的预览行才会触发；
    不像主注入那样抽整 face、也不会和缓存清理抢名额。子集失败时退回整 face
    （慢但能显示），字体不存在返回 None。
    """
    entry = _family_map().get(family)
    if entry is None:
        return None
    faces = _pick_faces(entry, prefer_zh=True)
    face = next((f for f in faces if f is not None), None)
    if face is None:
        return None

    key = hashlib.sha1(
        (face["file"] + "#" + str(face["index"]) + "#pv2").encode("utf-8")
    ).hexdigest()[:20]
    d = _preview_cache_dir()
    os.makedirs(d, exist_ok=True)
    ttf = os.path.join(d, key + ".ttf")
    woff2 = os.path.join(d, key + ".woff2")

    if os.path.exists(woff2) and os.path.getsize(woff2) > 0:
        return woff2, "font/woff2"
    if not (os.path.exists(ttf) and os.path.getsize(ttf) > 0):
        proc = _run(
            [_system_python(), _helper(), "preview", face["file"], str(face["index"]), ttf,
             # 保留字符 = 样张文本 + 族名：名字元素永远用自身字体渲染（字体名自成样张），
             # 子集必须覆盖族名字符；族名通常十几个字符，产物仍是几 KB。
             PREVIEW_SAMPLE_CHARS + family],
            timeout=180,
        )
        if proc.returncode != 0 or not os.path.exists(ttf) or os.path.getsize(ttf) <= 0:
            err = (proc.stderr or "").strip()[:200]
            log(f"预览子集失败（{os.path.basename(face['file'])}#{face['index']}）: {err}，回退整 face")
            return _ensure_font_file(face)

    result = _woff2_or_ttf(ttf, woff2)
    _prune_preview_cache()
    return result


def cache_files_for_state(state, families):
    """按 state 反推当前注入会用到的缓存文件名(零抽取)。
    "state → 预期缓存名"推导的唯一入口:启动保护集与 _prepare_extract 的实际产物键名
    共用 _pick_faces/_cache_key 原语,规则变更只改这里(一致性由 _tests_v1420 测试守护)。

    family / family_cjk → _pick_faces 选面 → _cache_key → {key}.ttf/.woff2；
    主开关开启时西文槽的剔字产物 {key}.latin{掩码版本}.ttf/.woff2 一并保护
    （旧掩码版本的剔字产物不在保护集内，视为陈旧缓存照常清理）。
    字体已卸载则选不到 face，自然跳过；单文件字体不进缓存，反推不到文件名也无妨。
    只做键名拼装，不抽取、不落盘。
    """
    names = []

    def full_names(face):
        key = _cache_key(face)
        return (os.path.join(CACHE_DIR, f"{key}.ttf"), os.path.join(CACHE_DIR, f"{key}.woff2"))

    split_on = bool(state.get("cjk_split"))
    family = state.get("family")
    if family and family in families:
        reg, med, bold = _pick_faces(families[family], prefer_zh=not split_on)
        for face in (reg, med if split_on else None, bold):
            if face is None:
                continue
            names.extend(full_names(face))
            if split_on:
                variant = _strip_variant()
                key = _cache_key(face)
                names.append(os.path.join(CACHE_DIR, f"{key}{variant}.ttf"))
                names.append(os.path.join(CACHE_DIR, f"{key}{variant}.woff2"))
    if split_on:
        cjk_family = state.get("family_cjk")
        if cjk_family and cjk_family in families:
            reg, med, bold = _pick_faces(families[cjk_family], prefer_zh=True)
            for face in (reg, med, bold):
                if face is not None:
                    names.extend(full_names(face))
    return names


def _protected_cache_files(state, families):
    """启动清理的保护集(委托 cache_files_for_state)。"""
    return cache_files_for_state(state, families)


# --------------------------------------------------------------------------- #
# 本地字体 HTTP 服务
# --------------------------------------------------------------------------- #

class FontServer:
    """只绑 127.0.0.1，随机端口 + 随机 token 前缀，仅提供已注册的字体文件。"""

    def __init__(self):
        self._httpd = None
        self._thread = None
        self.port = None
        self.token = None
        self._assets = {}

    @property
    def running(self):
        return self._httpd is not None

    def start(self):
        if self._httpd is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")

            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_HEAD(self):
                self._serve(True)

            def do_GET(self):
                self._serve(False)

            def _serve(self, head_only):
                prefix = f"/f/{owner.token}/"
                path = self.path.split("?")[0]
                if not path.startswith(prefix):
                    self.send_response(404)
                    self._cors()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                item = owner._assets.get(path[len(prefix):])
                if item is None:
                    self.send_response(404)
                    self._cors()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                file_path, mime = item
                try:
                    size = os.path.getsize(file_path)
                except OSError:
                    self.send_response(404)
                    self._cors()
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(size))
                self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                self.end_headers()
                if head_only:
                    return
                try:
                    with open(file_path, "rb") as fp:
                        while True:
                            chunk = fp.read(256 * 1024)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        httpd = ThreadingHTTPServer((DECKY_HOST, 0), Handler)
        httpd.daemon_threads = True
        self._httpd = httpd
        self.port = httpd.server_address[1]
        self.token = secrets.token_urlsafe(12)
        self._thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self._thread.start()
        log(f"字体服务已启动 127.0.0.1:{self.port}")

    def register(self, file_path, mime):
        if self._httpd is None:
            self.start()
        ext = os.path.splitext(file_path)[1].lower() or ".bin"
        name = hashlib.sha1(file_path.encode("utf-8")).hexdigest()[:20] + ext
        self._assets[name] = (file_path, mime)
        return f"http://{DECKY_HOST}:{self.port}/f/{self.token}/{name}"

    def stop(self):
        httpd, self._httpd = self._httpd, None
        self._assets = {}
        if httpd is None:
            return
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
        self.port = None
        self.token = None
        log("字体服务已停止")


# --------------------------------------------------------------------------- #
# Decky legacy 方法调用（走 Decky 自己的本地 HTTP 服务，不碰 CEF 调试口）
# --------------------------------------------------------------------------- #

# token 缓存：每次 RPC 都重新取一次会让请求数翻倍（320 次扫描 = 640 个请求）
_TOKEN_CACHE = {"value": None, "at": 0.0, "lock": threading.Lock()}
TOKEN_TTL = 25.0

# 同一 tab 的注入 / 移除必须串行，避免并发下写出两个 style 元素、丢掉一个 css_id
_TAB_LOCKS = {}
_TAB_LOCKS_GUARD = threading.Lock()
# 我们在每个 tab 里写过、还未移除的 style（tab -> {css_id: True}）。
# 由工作线程在写成功后立即记账：协程被取消也不会丢掉已写入那份的 css_id。
_OWNED = {}


def _tab_lock(tab):
    with _TAB_LOCKS_GUARD:
        lock = _TAB_LOCKS.get(tab)
        if lock is None:
            lock = _TAB_LOCKS[tab] = threading.Lock()
        return lock


def _decky_token(force=False):
    now = time.time()
    if not force:
        with _TOKEN_CACHE["lock"]:
            token = _TOKEN_CACHE["value"]
            if token and now - _TOKEN_CACHE["at"] < TOKEN_TTL:
                return token
    with urlopen(f"http://{DECKY_HOST}:{DECKY_PORT}/auth/token", timeout=3) as res:
        token = res.read().decode("utf-8").strip()
    with _TOKEN_CACHE["lock"]:
        _TOKEN_CACHE["value"] = token
        _TOKEN_CACHE["at"] = time.time()
    return token


def _decky_post(name, payload, token, timeout):
    base = f"http://{DECKY_HOST}:{DECKY_PORT}"
    request = Request(
        f"{base}/methods/{name}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-Decky-Auth": token},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as res:
        return json.loads(res.read().decode("utf-8"))


def _decky_method(name, payload, timeout=5):
    try:
        return _decky_post(name, payload, _decky_token(), timeout)
    except HTTPError as exc:
        # Decky 重启后旧 token 会 401/403，强制换一次再试；其它 HTTP 错误直接返回
        if exc.code not in (401, 403):
            return {"success": False, "result": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"success": False, "result": str(exc)}
    try:
        return _decky_post(name, payload, _decky_token(force=True), timeout)
    except Exception as exc:
        return {"success": False, "result": str(exc)}


def _inject_css_locked(tab, css):
    res = _decky_method("inject_css_into_tab", {"tab": tab, "style": css})
    if res.get("success") and res.get("result"):
        return str(res["result"])
    log(f"注入 {tab} 失败: {res.get('result')}")
    return None


def _classify_remove_failure(result_text):
    """移除失败归类：gone = 样式所附着的 tab/页面/弹窗已不存在（无需移除），
    real = 真故障（必须保持大声日志，绝不静默）。Decky 3.x 对已销毁 context 的
    remove_css_from_tab 返回的 result 文本要么含 "not found"，要么是内部异常
    包装串 "exceptions must derive from BaseException"（两种实测都对应 tab 已
    不存在）。其余一律 real——Decky 改版后退化为大声日志是 fail-safe。"""
    text = str(result_text or "")
    if "not found" in text or "exceptions must derive from BaseException" in text:
        return "gone"
    return "real"


def _remove_css_locked(tab, css_id, quiet=False):
    res = _decky_method("remove_css_from_tab", {"tab": tab, "css_id": css_id})
    if not res.get("success") and not quiet:
        if _classify_remove_failure(res.get("result")) == "gone":
            log(f"移除 {tab}:样式已随页面重建/弹窗已销毁，无需移除")
        else:
            log(f"移除 {tab}/{css_id} 失败: {res.get('result')}")
    return bool(res.get("success"))


def _replace_css(tab, css):
    """先清掉本 tab 里自己留下的全部旧样式，再写唯一一份。

    两件事在同一把 tab 锁内完成，所以同一 tab 上不会出现两份并存的时刻；
    记账也在工作线程里做（而不是等协程拿到返回值再记），
    这样即便外层协程在等待期间被取消，已写进去的那份也留在 _OWNED 里，
    下一次注入会先把它清掉，不会变成没人认领的孤儿。
    """
    with _tab_lock(tab):
        for old_id in list(_OWNED.get(tab) or ()):
            _remove_css_locked(tab, old_id, quiet=True)
        _OWNED.pop(tab, None)
        css_id = _inject_css_locked(tab, css)
        if css_id:
            _OWNED[tab] = {css_id: True}
        return css_id


def _remove_css(tab, css_id, quiet=False):
    with _tab_lock(tab):
        ok = _remove_css_locked(tab, css_id, quiet=quiet)
        owned = _OWNED.get(tab)
        if owned is not None:
            owned.pop(css_id, None)
            if not owned:
                _OWNED.pop(tab, None)
    return ok


def _cef_port_hint():
    """从 steamwebhelper 的启动参数里找 --remote-debugging-port，返回 (port, 说明)。"""
    try:
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fp:
                    raw = fp.read()
            except Exception:
                continue
            if b"steamwebhelper" not in raw:
                continue
            for part in raw.split(b"\x00"):
                text = part.decode("utf-8", "ignore")
                if text.startswith("--remote-debugging-port="):
                    value = text.split("=", 1)[1]
                    if value.isdigit():
                        return int(value), "steamwebhelper 启动参数"
    except Exception:
        pass
    return None, "steamwebhelper 启动参数里没有 --remote-debugging-port"


def _cef_why_message(attempts):
    """按探测次序组装 CEF 不可用原因(纯函数)。
    attempts: [(port, source, detail), ...] 按尝试顺序,source 为 _cef_port_hint
    的说明或"回落端口",detail 为异常文本。主端口(hint/8080)失败原因优先呈现,
    回落端口并列补充;仅当没有任何主端口尝试记录时才单独报回落。"""
    mains = [(p, s, d) for p, s, d in attempts if s != "回落端口"]
    fallbacks = [(p, s, d) for p, s, d in attempts if s == "回落端口"]
    if not mains and fallbacks:
        p, _s, d = fallbacks[0]
        return f"回落端口 {p} 不可用（{d}）"
    parts = [f"主端口 {p} 无响应（{d}）" for p, _s, d in mains]
    parts += [f"回落 {p} 亦不可用" for p, _s, d in fallbacks]
    return "；".join(parts)


def _cef_fetch(force=False):
    """读 /json/list，返回 tab 列表；任何失败返回 None。结果缓存 300 ms。"""
    now = time.monotonic()
    with _CEF_LOCK:
        if not force and now - _CEF_CACHE["at"] < CEF_CACHE_TTL:
            return _CEF_CACHE["tabs"]
        ports = []
        hint, source = _cef_port_hint()
        if hint:
            ports.append((hint, source))
        for fallback in CEF_PORTS:
            if fallback not in [p for p, _ in ports]:
                ports.append((fallback, "回落端口"))
        tabs, used, why = None, None, ""
        attempts = []
        for candidate, src in ports:
            try:
                with urlopen(f"http://127.0.0.1:{candidate}/json/list", timeout=CEF_TIMEOUT) as res:
                    data = json.loads(res.read().decode("utf-8", "ignore"))
                if isinstance(data, list):
                    tabs = [t for t in data if isinstance(t, dict) and t.get("title")]
                    used = candidate
                    why = f"{src}（端口 {candidate}）列出 {len(tabs)} 个 tab"
                    break
            except Exception as exc:
                attempts.append((candidate, src, str(exc)))
        if tabs is None:
            why = _cef_why_message(attempts)
        _CEF_CACHE.update({"at": now, "tabs": tabs, "port": used, "why": why})
        return tabs


def _cef_log_state(ok, reason):
    """只在「可用 ⇄ 不可用」状态变化时打一行日志，避免每轮刷屏。"""
    prev = _CEF_CACHE.get("state")
    if prev is None or ok == prev:
        _CEF_CACHE["state"] = ok
        return
    _CEF_CACHE["state"] = ok
    if ok:
        log(f"CEF 调试口恢复：{reason}")
    else:
        log(f"CEF 调试口尚未就绪（{reason}）：注入窗口会重试，弹窗监听自动重连")


def _cef_titles():
    """返回当前 tab 标题集合；调试口尚未就绪时返回 None（调用方按退避重试）。"""
    tabs = _cef_fetch()
    if tabs is None:
        _cef_log_state(False, _CEF_CACHE.get("why") or "调试端口无响应")
        return None
    _cef_log_state(True, _CEF_CACHE.get("why") or "")
    return {t.get("title") for t in tabs}


def _eval_in_tab(tab, code):
    return _decky_method("execute_in_tab", {"tab": tab, "run_async": False, "code": code}, timeout=2)


def _style_exists(tab, css_id):
    """返回 True / False / None(无法判断)。

    execute_in_tab 对已消失的 tab 返回 HTTP 500，文本里没有 "not found"，
    单凭它分不清「tab 没了」和「RPC 出问题」；
    execute_in_tab 失败且文本含 not found 才判 False，其余记 None 不猜。
    """
    res = _eval_in_tab(tab, f"document.getElementById({json.dumps(css_id)}) !== null")
    if not res.get("success"):
        text = str(res.get("result") or "")
        if "not found" in text:
            return False
        return None
    inner = res.get("result")
    # legacy 路由会再包一层 {success, result}
    if isinstance(inner, dict):
        return bool(inner.get("success")) and inner.get("result") is True
    return inner is True


def _scan_uid(prefix):
    """从 CEF tab 标题里直接取当前 uid（menu 扫 MainMenu_uid*，qam 扫 QuickAccess_uid*）。

    调试口尚未就绪时返回 None，调用方按「没扫到」记账，随注入窗口退避重试。
    """
    titles = _cef_titles()
    if titles is None:
        return None
    best = None
    for title in titles:
        match = CEF_TAB_RE.match(title or "")
        if match and match.group(1) + "_uid" == prefix:
            value = int(match.group(2))
            best = value if best is None else max(best, value)
    return best


# --------------------------------------------------------------------------- #
# 前置条件：抽取嵌入在需要 fontTools 时才强制校验
# --------------------------------------------------------------------------- #

_FT_CACHE = {"at": 0.0, "ok": False, "version": None, "brotli_ok": False}

# 预览字体请求统计：按（字体服务端口 + family）记录 get_preview_font 调用次数与结果
_PREVIEW_STATS = {"lock": threading.Lock(), "calls": {}}


def _fonttools_refresh(force=False):
    """真正起一次子进程探测 fontTools。只允许插件加载时调用。

    插件目录下带 vendor/ 的完整版也算有。60 秒内重复调只返回旧快照。
    """
    now = time.time()
    if not force and now - _FT_CACHE["at"] < 60:
        return _FT_CACHE["ok"], _FT_CACHE["version"]
    ok, version = False, None
    try:
        proc = _run([_system_python(), "-c", "import fontTools;print(fontTools.version)"], timeout=15)
        if proc.returncode == 0:
            ok = True
            version = (proc.stdout or "").strip() or None
    except Exception:
        ok = False
    # brotli 独立探测（与 fontTools 分开、独立 try/except）：woff2 压缩静默依赖系统
    # Python 的 brotli/brotlicffi 模块（vendor 未带，见 vendor/fontTools/ttLib/woff2.py:32-40），
    # 原生 SteamOS 大概率没有；缺失时 helper woff2 步骤 rc=1，注入回退 ttf。
    # 导入顺序镜像 woff2.py：先 brotlicffi 后 brotli。
    brotli_ok = False
    try:
        proc = _run(
            [_system_python(), "-c",
             "try:\n    import brotlicffi\nexcept ImportError:\n    import brotli"],
            timeout=15,
        )
        brotli_ok = proc.returncode == 0
    except Exception:
        brotli_ok = False
    _FT_CACHE.update({"at": now, "ok": ok, "version": version, "brotli_ok": brotli_ok})
    return ok, version


def _fonttools_status():
    """只读模块级快照：这里不起进程、不落盘，面板读取一律走这条。"""
    return _FT_CACHE["ok"], _FT_CACHE["version"]


def _require_fonttools_if_needed(faces, subset_needed=False):
    """抽取嵌入时，只有选中集合字体（.ttc/.otc）才强制要求 fontTools。

    subset_needed：分槽注入的西文槽剔字要读 cmap 并跑 fontTools subset，
    与是否集合字体无关，主开关开启时一律要求。
    这个检查在 _prepare 里、拆卸旧样式之前执行，所以失败时旧 CSS 不会被拆掉。
    .ttf/.otf/.woff2 这类单文件在不开剔字时不需要 fontTools，瘦版也能用。
    """
    need = [f for f in faces if f is not None and f.get("collection")]
    reason = "collection"
    if subset_needed and not need:
        need = [f for f in faces if f is not None]
        reason = "subset"
    if not need:
        return
    ok, _version = _fonttools_status()
    if ok:
        return
    name = os.path.basename(need[0]["file"])
    if reason == "subset":
        raise _KeyedError(
            "err.fonttoolsSubset", [name],
            message=(
                f"分槽注入的西文剔字需要 fontTools，当前 Python（{_system_python()}）里没有。\n"
                f"- 本次选的 {name} 需要剔除自带的 CJK/PUA 字形后再注入\n"
                "- 换带 fontTools 的完整版，或关闭「按语言注入」后重试\n"
            ),
        )
    raise _KeyedError(
        "err.fonttoolsMissing", [name],
        message=(
            f"抽取嵌入需要 fontTools，当前 Python（{_system_python()}）里没有。\n"
            f"- 本次选的 {name} 是 .ttc/.otc 集合，必须先抽出单个 face\n"
            "- 换带 fontTools 的完整版，或改选 .ttf/.otf 单文件字体后重试\n"
        ),
    )


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #

def _state_path():
    return STATE_FILE


def read_state():
    try:
        with open(_state_path(), "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict):
            data.pop("cjk_punct", None)  # 迁移：移除旧版标点渲染开关状态
            return data
        return {}
    except Exception:
        return {}


def write_state(data):
    os.makedirs(os.path.dirname(_state_path()) or ".", exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _norm_level(value, default=0):
    """把档位参数收敛到 0/1/2，非法值回退 default。"""
    try:
        level = int(value)
    except (TypeError, ValueError):
        return default
    return level if level in (0, 1, 2) else default


def _norm_preview_cache_mb(value, default=8):
    """预览缓存字节上限（MB）收敛到 1-30，非数字/越界回退 default。"""
    try:
        mb = int(value)
    except (TypeError, ValueError):
        return default
    return mb if 1 <= mb <= 30 else default


def _preview_cache_mb_of(state):
    """预览缓存字节上限（MB）。旧 state 无 preview_cache_mb 键 = 默认 8，无感升级。"""
    return _norm_preview_cache_mb(state.get("preview_cache_mb"), PREVIEW_CACHE_DEFAULT_MB)


def _norm_expand(value, default=0):
    """C 方案开关：0 关 / 1 开；旧 2 档（内嵌）迁移为 1。"""
    try:
        level = int(value)
    except (TypeError, ValueError):
        return default
    return 0 if level <= 0 else 1


def _preview_level_of(state):
    """预览样张档位（0 关闭 / 1 方案 A / 2 方案 B），兼容旧布尔 preview 键。"""
    if state.get("preview_level") is not None:
        return _norm_level(state.get("preview_level"), 2)
    # 旧布尔迁移：preview=false → 0；缺省 / true → 2（现行方案 B 行为）
    return 0 if state.get("preview") is False else 2


def _expand_level_of(state):
    """C 方案聚焦展开开关（0 关闭 / 1 展开四语言样张，旧 2 档并入 1）。"""
    return _norm_expand(state.get("expand_level"), 0)


def _clean_favorites(state):
    """把 state 里的 favorites 规范化（去重、去空）。"""
    favorites = []
    for name in state.get("favorites") or []:
        if isinstance(name, str) and name and name not in favorites:
            favorites.append(name)
    return favorites


def _push_history(state, family):
    """把 family 记入历史：最新在前、去重、无上限。"""
    if not isinstance(family, str) or not family:
        return
    history = state.get("history") or []
    if isinstance(history, list):
        history = [name for name in history if isinstance(name, str)]
    else:
        history = []
    if family in history:
        history.remove(family)
    history.insert(0, family)
    state["history"] = history


def _inject_stamp(at):
    """注入记录的时间部分：同日 HH:MM，跨日 MM-DD HH:MM。

    纯由写入时刻决定，不做任何定时刷新。
    """
    local = time.localtime(at)
    now = time.localtime()
    same_day = (local.tm_year, local.tm_yday) == (now.tm_year, now.tm_yday)
    return time.strftime("%H:%M" if same_day else "%m-%d %H:%M", local)


def _heal_stamp(state):
    """自动补注入的时间戳；没有记录时返回空串（面板据此不显示第二行）。"""
    at = state.get("heal_at") or 0
    return _inject_stamp(at) if at else ""


def _inject_status(state) -> tuple[str, str]:
    """注入记录四态：done / healed / failed / none，返回 (level, stamp)。

    heal_at 比两个注入标记都新 = 最近一次生效注入来自自动补注，返回 healed
    （时间取补注时刻）；其余情况 stamp 只由 inject_at 决定。
    """
    at = state.get("inject_at") or 0
    failed_at = state.get("inject_failed_at") or 0
    heal_at = state.get("heal_at") or 0
    if at and at >= failed_at and at >= heal_at:
        return "done", _inject_stamp(at)
    if heal_at and heal_at > max(at, failed_at):
        return "healed", _inject_stamp(heal_at)
    if failed_at:
        return "failed", ""
    return "none", ""


# --------------------------------------------------------------------------- #
# 插件
# --------------------------------------------------------------------------- #

class Plugin:
    def __init__(self):
        global _LIVE
        _LIVE = self
        self._server = FontServer()
        self._hit = {}  # key -> {"tab":..., "css_id":...}
        self._css = None
        self._family = None
        self._detail = {}
        self._dyn_task = None
        self._uid = read_state().get("uid")
        self._scan_miss_logged = False  # 本次注入周期里是否已经打过「没扫到」，同一事件内只打一条
        self._dead_tabs = set()  # 本注入事件已确认死亡的完整 tab 名（验活续传用，事件开始清空）
        self._uid_lock = None  # asyncio.Lock，串行化 uid 发现，懒创建（__init__ 时还没有事件循环）
        self._window_task = None  # 当前注入窗口（同一时刻最多一个）
        self._window_lock = None  # asyncio.Lock，串行化开窗，懒创建
        self._apply_lock = None  # asyncio.Lock，串行化应用流程，懒创建
        self._toast_task = None  # notificationtoasts_* 弹窗 CEF 事件监听任务
        self._toasts = {}  # toast tab 名 -> css_id（弹窗注入记账）
        self._toast_ids = {}  # CEF targetId -> toast tab 名（destroyed 事件反查用）

    # ---- 生命周期 ----

    async def _main(self):
        plugin = _bind(self)
        state = read_state()
        family = state.get("family")
        # 迁移：local() 已弃用。老 state 里存 local / 缺 mode / 存了无效值，一律纠正成
        # extract 并落盘；下面照常走一次 _apply，等于对当前字体重新注入一次，
        # 升级后的实例不会停在已不存在的模式上。
        stored = state.get("mode")
        if stored != MODE_EXTRACT:
            log(f"投递模式迁移：state 里的 mode={stored!r} → extract（local() 已弃用），重新注入一次")
            state["mode"] = MODE_EXTRACT
            write_state(state)
        log(f"启动（抽取嵌入模式），已保存字体: {family!r} self={type(self).__name__}")
        # fontTools 快照只在插件加载时刷新一次，面板读取只读快照
        await asyncio.to_thread(_fonttools_refresh, True)
        # 启动时清理一次缓存：按 state 反推保护集（双槽 + 剔字产物），其余按最近使用
        # 只留 CACHE_KEEP_FILES 个。反推零抽取（只做 fc-list 选面与键名拼装），
        # 字体已卸载自然跳过；没有它，上限提到 6 后重启也会保留旧文件、双槽首注入也常被误清
        families = await asyncio.to_thread(_family_map)
        protect = _protected_cache_files(state, families)
        t0 = time.monotonic()
        removed = await asyncio.to_thread(_prune_font_cache, tuple(protect))
        log(f"启动缓存清理完成：反推保护 {len(protect)} 个缓存名，删除 {removed} 个，"
            f"耗时 {int((time.monotonic() - t0) * 1000)} ms")
        await asyncio.to_thread(_prune_preview_cache)
        _port, source = await asyncio.to_thread(_cef_port_hint)
        tabs = await asyncio.to_thread(_cef_fetch, True)
        if tabs is None:
            log(f"CEF 调试口尚未就绪：{_CEF_CACHE.get('why')}；注入窗口会重试，弹窗监听自动重连")
        else:
            log(f"CEF 调试口已连接：{source}（端口 {_CEF_CACHE.get('port')}，{len(tabs)} 个 tab）")
        # 弹窗监听：挂浏览器事件流（内部自动重连，失败不影响主流程）
        plugin._start_toast_watcher()
        if family and family not in families:
            log(f"已保存字体 {family!r} 不在系统中，清空西文槽选择")
            state.pop("family", None)
            state.pop("inject_at", None)
            state.pop("inject_failed_at", None)
            state.pop("heal_at", None)
            write_state(state)
            family = None
        # 主开关开时校验 CJK 槽：字体已卸载则清键（与 family 同款），不影响西文槽恢复
        if state.get("cjk_split") and state.get("family_cjk") and state["family_cjk"] not in families:
            log(f"已保存的 CJK 槽字体 {state['family_cjk']!r} 不在系统中，清空 CJK 槽选择")
            state.pop("family_cjk", None)
            write_state(state)
        if not family and not (state.get("cjk_split") and state.get("family_cjk")):
            return
        result = await plugin._apply(family)
        if not result.get("ok"):
            log(f"启动时恢复字体失败: {result.get('error')}")
            return
        # _apply 里的一次性注入假设「UI 已就绪」（用户动作场景成立），启动时未必：
        # 冷开机 / 服务重启时 popup 可能还没回来，扑空后原本只能静等下一次 UI 事件。
        # 接上和模式变化同款的有界窗口，按退避自动补齐；热机场景首轮验活即通过，
        # 没有额外注入。
        await plugin._start_inject_window()

    async def _unload(self):
        plugin = _bind(self)
        await plugin._stop_toast_watcher()
        await plugin._cancel_dyn()
        await plugin._stop_window()
        await plugin._remove_all()
        plugin._server.stop()

    # ---- 前端方法 ----

    async def get_fonts(self):
        # Decky 会把插件方法的返回值原样包成 {success, result}，这里直接返回数组
        families = await asyncio.to_thread(_family_map)
        return list_families(families)

    async def get_preview_font(self, family):
        """列表样张懒加载：为 family 准备一份只含样张字符的 @font-face URL。

        前端只有在实际滚进视口的预览行才调用（IntersectionObserver 驱动），
        URL 由 FontServer 注册，CEF 里 @font-face + 本地 127.0.0.1 真绑定生效；
        不调用就不抽面、不占缓存名额。返回 {ok, url, mime}。
        """
        plugin = _bind(self)
        port = getattr(getattr(plugin, "_server", None), "port", None)
        _key = (port, str(family))
        with _PREVIEW_STATS["lock"]:
            _entry = _PREVIEW_STATS["calls"].setdefault(_key, {"total": 0, "ok": 0, "fail": 0})
            _entry["total"] += 1
            _seq = _entry["total"]

        def _stat(ok: bool, note: str = ""):
            with _PREVIEW_STATS["lock"]:
                _stat_entry = _PREVIEW_STATS["calls"][_key]
                if ok:
                    _stat_entry["ok"] += 1
                else:
                    _stat_entry["fail"] += 1
                _ok, _fail = _stat_entry["ok"], _stat_entry["fail"]
            log(f"[预览统计] port={port} family={str(family)!r} 第{_seq}次 {'ok' if ok else 'fail'} "
                f"累计 ok={_ok}/fail={_fail} {note}".rstrip())

        try:
            pair = await asyncio.to_thread(_prepare_preview_face, family)
        except Exception as exc:
            log(f"预览字体准备失败 {family!r}: {exc}")
            _stat(False, "prepare-exc")
            return {"ok": False, "error": str(exc)}
        if not pair:
            _stat(False, "no-family")
            return {"ok": False, "error": f"系统里没有字体 {family}", "error_key": "err.noFamily", "error_args": [family]}
        path, mime = pair
        try:
            url = plugin._server.register(path, mime)
        except Exception as exc:
            _stat(False, "register-exc")
            return {"ok": False, "error": str(exc)}
        _stat(True, "ok")
        return {"ok": True, "url": url, "mime": mime}

    async def get_font_state(self):
        plugin = _bind(self)
        return plugin._payload()

    async def set_font(self, family):
        plugin = _bind(self)
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", "error_key": "err.noFamily", "error_args": [family], **plugin._payload()}
        state = read_state()
        state["family"] = family
        _push_history(state, family)
        write_state(state)
        return await plugin._apply(family)

    async def set_cjk_font(self, family):
        """CJK 槽位选择（主开关开时前端按语言归属调用）。

        只写 family_cjk，不动西文槽；槽位归属完全由前端按字体语言决定，后端不做判断。
        西文槽为空时只注入中日韩字体（见 _prepare_cjk_only）。
        """
        plugin = _bind(self)
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", "error_key": "err.noFamily", "error_args": [family], **plugin._payload()}
        state = read_state()
        state["family_cjk"] = family
        _push_history(state, family)
        write_state(state)
        # 西文槽为空时 _prepare 会只注入中日韩字体
        return await plugin._apply(state.get("family"))

    async def set_split_options(self, cjk_split=None, synth_weight_none=None,
                                preview_level=None, expand_level=None, preview=None,
                                preview_cache_mb=None):
        """分槽开关入口：包一层计时，实际逻辑在 _set_split_options_inner。"""
        plugin = _bind(self)  # Decky 3.3.1 可能把类本身当 self 传进来，先回落到实例
        t0 = time.monotonic()
        try:
            return await plugin._set_split_options_inner(
                cjk_split=cjk_split, synth_weight_none=synth_weight_none,
                preview_level=preview_level, expand_level=expand_level, preview=preview,
                preview_cache_mb=preview_cache_mb,
            )
        finally:
            log(f"[耗时] 分槽开关调用合计（cjk_split={cjk_split}）: {_elapsed_ms(t0)} ms")

    async def _set_split_options_inner(self, cjk_split=None, synth_weight_none=None,
                                preview_level=None, expand_level=None, preview=None,
                                preview_cache_mb=None):
        """分槽注入的主/子开关（持久化）。参数都可选，None = 不变。

        子开关默认值（主开关开启时）：synth_weight_none 缺省视为开。
        preview_level（预览样张档位，0 关闭 / 1 方案 A / 2 方案 B）与
        expand_level（C 方案聚焦展开开关，0 关闭 / 1 展开；旧 2 档内嵌并入 1）
        都是纯前端渲染开关，不影响注入 CSS：变化时只落盘、不重新注入。
        preview（旧布尔参数）兼容保留：true → 2（方案 B）、false → 0；
        与 preview_level 同时传入时以 preview_level 为准。
        主开关切到关：清空西文 / 中日韩两个槽位（含注入记账）并拆除当前注入，
        重开后两槽均为空、不自动恢复旧选择。
        影响注入 CSS 的开关变化（剔字掩码版本 / Motiva Sans / synthesis 行）
        会按当前字体重新注入一次；没有已选字体时只落盘，不注入。
        preview_cache_mb（预览子集缓存字节上限 1-30MB，默认 8）是纯缓存设置：
        只落盘、不参与 changed，绝不触发按当前字体重新注入。
        """
        plugin = _bind(self)
        state = read_state()
        changed = False
        turned_off = False
        levels_changed = False
        cache_changed = False
        if cjk_split is not None:
            value = bool(cjk_split)
            turned_off = bool(state.get("cjk_split")) and not value
            changed = changed or bool(state.get("cjk_split")) != value
            state["cjk_split"] = value
            if not value:
                # 关闭「按语言注入」即清空两个槽位与注入记账，不留旧选择
                for key in ("family", "family_cjk", "inject_at", "inject_failed_at", "heal_at"):
                    state.pop(key, None)
        if synth_weight_none is not None:
            value = bool(synth_weight_none)
            changed = changed or bool(state.get("synth_weight_none", True)) != value
            state["synth_weight_none"] = value
        if preview_level is not None or preview is not None:
            if preview_level is not None:
                value = _norm_level(preview_level, _preview_level_of(state))
            else:
                # 旧布尔参数兼容：true → 2（方案 B）、false → 0
                value = 0 if not preview else 2
            levels_changed = levels_changed or _preview_level_of(state) != value
            state["preview_level"] = value
            state["preview"] = value != 0  # 旧键同步维护，兼容老前端
        if expand_level is not None:
            value = _norm_expand(expand_level, _expand_level_of(state))
            levels_changed = levels_changed or _expand_level_of(state) != value
            state["expand_level"] = value
        if preview_cache_mb is not None:
            # 纯缓存上限：归一化后落盘，用独立标志，绝不计入 changed（否则会按
            # 当前字体重新注入 CSS，对调缓存大小完全没必要）
            value = _norm_preview_cache_mb(preview_cache_mb, _preview_cache_mb_of(state))
            if _preview_cache_mb_of(state) != value:
                cache_changed = True
            state["preview_cache_mb"] = value
        if not (changed or levels_changed or cache_changed):
            return plugin._payload()
        write_state(state)
        log(f"分槽开关更新：cjk_split={state.get('cjk_split')} "
            f"synth_weight_none={state.get('synth_weight_none', True)} "
            f"preview_level={_preview_level_of(state)} expand_level={_expand_level_of(state)} "
            f"preview_cache_mb={_preview_cache_mb_of(state)}")
        if not changed:
            # 只有预览档位变化：不影响注入 CSS，无需重新注入
            return plugin._payload()
        if turned_off:
            # 两个槽位已清空：拆除现有注入（复用 reset_font 的拆卸顺序；
            # 不停字体服务，重开开关选字时 register 会按需自起）
            removed = await plugin._teardown_all()
            log(f"按语言注入已关闭：西文/中日韩槽位已清空，注入已拆除（{removed} 处）")
            return plugin._payload()
        state_now = read_state()
        family = plugin._family or state_now.get("family")
        if not family and not (state_now.get("cjk_split") and state_now.get("family_cjk")):
            return plugin._payload()
        return await plugin._apply(family)

    async def _teardown_all(self):
        """统一拆卸序列：取消动态注入任务、停注入窗口、移除全部样式并清内存态。
        返回移除的样式条数。不停字体服务（重开开关选字时 register 会按需自起）。"""
        plugin = _bind(self)
        await plugin._cancel_dyn()
        await plugin._stop_window()
        removed = await plugin._remove_all()
        plugin._css = None
        plugin._family = None
        plugin._detail = {}
        return removed

    async def reset_font(self):
        plugin = _bind(self)
        removed = await plugin._teardown_all()
        state = read_state()
        # 恢复默认 = 清空两个槽位；不动任何开关（分槽三开关原样保留）
        state.pop("family", None)
        state.pop("family_cjk", None)
        state.pop("inject_at", None)
        state.pop("inject_failed_at", None)
        state.pop("heal_at", None)
        write_state(state)
        plugin._server.stop()
        payload = plugin._payload()
        payload["removed"] = removed
        return payload

    async def clear_history(self):
        """清空字体使用历史（持久化）。不动任何已选字体与开关。"""
        plugin = _bind(self)
        state = read_state()
        state["history"] = []
        write_state(state)
        log("字体使用历史已清空")
        return {"ok": True, **plugin._payload()}

    async def reapply(self):
        plugin = _bind(self)
        if not plugin._family and not read_state().get("family_cjk"):
            return {"ok": False, "error": "当前没有选择字体", "error_key": "err.noSelection", **plugin._payload()}
        return await plugin._apply(plugin._family)

    async def on_ui_loaded(self):
        """前端模块加载时的空壳回调：只记日志，不做任何注入。"""
        log("前端模块已加载（on_ui_loaded），本步不做任何注入")
        return {"ok": True}

    # ---- 日志 ----

    async def get_recent_log(self):
        """只读返回最新日志尾部内容，供面板「复制日志」写入剪贴板。"""
        return await asyncio.to_thread(_recent_log)

    # ---- 收藏夹 ----

    async def toggle_favorite(self, family):
        plugin = _bind(self)
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", "error_key": "err.noFamily", "error_args": [family], **plugin._payload()}
        state = read_state()
        favorites = _clean_favorites(state)
        added = family not in favorites
        if added:
            favorites.append(family)
        else:
            favorites.remove(family)
        # 只改 favorites，state 里其它键（含已不再使用的 groups）原样保留
        state["favorites"] = favorites
        write_state(state)
        return plugin._payload()

    # ---- 应用 / 注入 ----

    async def _apply(self, family, family_cjk=None):
        """切换字体或重新注入当前字体。

        family_cjk 省略时由 _prepare 读 state（CJK 槽归属由 set_cjk_font 预先写好）。
        顺序固定为「先检查 / 先准备，最后才拆旧样式」：
        前置条件不满足（字体不存在、缺 fontTools、抽取或起服务失败）时直接返回错误，
        已经注入的 CSS 原样保留，不会出现「先 _remove_all() 再抛异常」导致样式被拆掉。
        """
        # 后端并发兜底：前端 busy 单飞正常时不会并发进 _apply；这里防外部直接重复
        # 触发（RPC 重试 / 事件竞态）导致两次准备交错。持锁期间再来直接返回当前
        # 面板状态，不排队、不报错。
        if self._apply_lock is not None and self._apply_lock.locked():
            log("_apply 已在进行中：跳过本次重复触发，返回当前面板状态")
            return self._payload()
        async with self._apply_mutex():
            t_all = time.monotonic()
            async with self._window_mutex():
                await self._stop_window()
            try:
                t_prep = time.monotonic()
                detail = await asyncio.to_thread(self._prepare, family, family_cjk)
                log(f"[耗时] 准备字体 {family!r}: {_elapsed_ms(t_prep)} ms")
            except Exception as exc:
                log(f"准备字体 {family!r} 失败: {exc}")
                self._mark_inject(False)
                payload = self._payload()
                payload.update({"ok": False, "error": str(exc)})
                if isinstance(exc, _KeyedError):
                    payload.update({"error_key": exc.key, "error_args": exc.args_})
                return payload

            await self._remove_all()
            self._css = detail["css"]
            self._family = family
            self._detail = detail
            state = read_state()
            state["mode"] = MODE_EXTRACT
            write_state(state)

            # 主界面同步注入（快），QAM/MainMenu 需要发现 uid，放后台，不阻塞切换
            injected = await self._inject_main()
            self._mark_inject(bool(injected))
            await self._cancel_dyn()
            self._dead_tabs.clear()
            self._dyn_task = asyncio.create_task(self._inject_dynamic_all())
            log(f"[耗时] 应用字体 {family!r} 合计（不含动态 uid 注入）: {_elapsed_ms(t_all)} ms")
            return self._payload()

    def _mark_inject(self, ok, source="user"):
        """记录注入结果，按来源分开记账，两个字段互不覆盖。

        - source="user"：用户动作（换字体 / 重新注入 / 加载时注入）
          → 成功写 inject_at，失败写 inject_failed_at
        - source="heal"：自动补注入（窗口）→ 成功写 heal_at 并清掉失败标记
          （失败已被补注恢复，面板不再挂红字），失败不记账
        """
        if source == "heal" and not ok:
            return
        state = read_state()
        if source == "heal":
            state["heal_at"] = time.time()
            state.pop("inject_failed_at", None)
        elif ok:
            state["inject_at"] = time.time()
            state.pop("inject_failed_at", None)
        else:
            state["inject_failed_at"] = time.time()
        write_state(state)

    async def _cancel_dyn(self):
        task = self._dyn_task
        self._dyn_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _inject_dynamic_all(self):
        self._scan_miss_logged = False  # 一轮用户动作注入：最多一条「没扫到」
        for key, _label, _candidates in TARGETS[1:]:
            if key in self._hit:
                continue
            await self._inject_dynamic(key)

    def _prepare(self, family, family_cjk=None):
        """把选中字体准备成一段 CSS。任何失败都直接抛异常，调用方尚未拆旧样式。

        family 为 None（西文槽为空）时，若中日韩槽有字体，则只注入中日韩字体。
        """
        state = read_state()
        split_on = bool(state.get("cjk_split"))
        # synth_weight_none 已独立于 cjk_split，无论主开关是否开启都读取（缺省=开）
        synth_none = bool(state.get("synth_weight_none", True))
        families = _family_map()
        entry_cjk = None
        if family_cjk is None:
            family_cjk = state.get("family_cjk")
        if split_on and family_cjk:
            entry_cjk = families.get(family_cjk)
            if entry_cjk is None:
                # CJK 槽指向已卸载的字体：按「跟随系统」处理（启动校验负责清键）
                log(f"CJK 槽字体 {family_cjk!r} 不在系统中，本次按跟随系统处理")
                family_cjk = None
        if not family:
            if split_on and entry_cjk is not None:
                return self._prepare_cjk_only(family_cjk, entry_cjk, synth_none)
            raise _KeyedError("err.noSlots", message="没有选择西文字体，中日韩槽也为空")
        entry = families.get(family)
        if entry is None:
            raise _KeyedError("err.noFamily", [family], message=f"系统里没有字体 {family}")
        chain = _fallback_families(
            families, family, entry["zh"], split_on=split_on, cjk_family=family_cjk
        )
        return self._prepare_extract(
            family, entry, chain,
            split_on=split_on, family_cjk=family_cjk, entry_cjk=entry_cjk,
            synth_none=synth_none,
        )

    def _prepare_cjk_only(self, family_cjk, entry_cjk, synth_none):
        """只有中日韩槽、西文槽为空：中日韩字体单独作为主字体注入（不剔字、不回填）。

        字体栈 = [中日韩内部族, Motiva Sans, system-ui, sans-serif]。Motiva Sans 保留
        Steam 自带的 PUA 图标字形，与分槽路径的回退链一致。
        """
        reg, med, bold = _pick_faces(entry_cjk, prefer_zh=True)
        _require_fonttools_if_needed([reg, med, bold])
        faces = []
        paths = []
        details = {}
        for face, weight, slot in ((reg, 400, "regular"), (med, 500, "medium"), (bold, 700, "bold")):
            if face is None:
                details[slot] = None
                continue
            path, mime = _ensure_font_file(face)
            faces.append((self._server.register(path, mime), mime, weight))
            paths.append(path)
            details[slot] = {
                "file": face["file"],
                "index": face["index"],
                "weight": face["weight"],
                "extracted": face["collection"],
                "mime": mime,
                "bytes": os.path.getsize(path),
                "stripped": False,
            }
        inuse = [p for p in paths if _is_cache_file(p)]
        _prune_font_cache(inuse)
        chain = [STEAM_UI_FONT, "system-ui", "sans-serif"]
        css = self._build_css([], faces, [], chain, synth_none)
        log(f"西文槽为空：仅注入中日韩槽字体 {family_cjk!r}（{len(faces)} 条字重声明）")
        return {
            "css": css,
            # 西文槽为空：regular/medium/bold 留给西文，这里只放中日韩字体的信息
            "regular": None,
            "medium": None,
            "bold": None,
            "cjk": details,
            "strip": {"done": False, "fallback": False},
            "full_backfill": False,
            "split": True,
            "synth_none": synth_none,
            "fallback": chain,
            "zh": entry_cjk["zh"],
        }

    def _prepare_extract(self, family, entry, chain, split_on=False, family_cjk=None,
                         entry_cjk=None, synth_none=False):
        """抽取 face 并生成 CSS。

        - 主开关关：单槽 1.4.2 路径（zh 优先选面、不剔字、不注册 medium、
          无 Motiva Sans / synthesis，回退链逐字节同旧版）；
        - 主开关开：西文槽 prefer_zh=False + 方案 D 剔字（固定基础剔字掩码）
          + medium 注册；CJK 槽保持 zh 优先与原抽取流程，不做子集化（实测无收益）。
        """
        regular, medium, bold = _pick_faces(entry, prefer_zh=not split_on)
        # 集合字体 / 西文剔字才需要 fontTools；这一步在拆卸旧样式之前，失败时旧 CSS 不动
        _require_fonttools_if_needed([regular, medium, bold], subset_needed=split_on)

        # 西文槽：主开关开走剔字通路（_ensure_latin_file 内部已含失败回退），
        # 关则与 1.4.2 完全一致
        def latin_file(face):
            if split_on:
                return _ensure_latin_file(face)
            path, mime = _ensure_font_file(face)
            return path, mime, False, False

        def face_info(face, path, mime, stripped=False):
            return {
                "file": face["file"],
                "index": face["index"],
                "weight": face["weight"],
                "extracted": face["collection"],
                "mime": mime,
                "bytes": os.path.getsize(path),
                # 方案 D：该 face 是否以剔字产物注入（仅西文槽可能出现 True）
                "stripped": stripped,
            }

        reg_path, reg_mime, reg_stripped, reg_failed = latin_file(regular)
        reg_url = self._server.register(reg_path, reg_mime)
        latin_faces = [(reg_url, reg_mime, 400)]

        medium_used = medium if split_on else None
        medium_path = medium_mime = None
        med_url = None
        med_stripped = med_failed = False
        if medium_used is not None:
            medium_path, medium_mime, med_stripped, med_failed = latin_file(medium_used)
            med_url = self._server.register(medium_path, medium_mime)
            latin_faces.append((med_url, medium_mime, 500))

        bold_path = bold_mime = None
        bold_url = None
        bold_stripped = bold_failed = False
        if bold is not None:
            bold_path, bold_mime, bold_stripped, bold_failed = latin_file(bold)
            bold_url = self._server.register(bold_path, bold_mime)
            latin_faces.append((bold_url, bold_mime, 700))
        strip_done = reg_stripped or med_stripped or bold_stripped
        strip_failed = reg_failed or med_failed or bold_failed

        # CJK 槽：不做子集化，保持 1.4.2 的选面（zh 优先）与抽取流程
        cjk_detail = None
        cjk_faces = []
        cjk_paths = ()
        if split_on and entry_cjk is not None:
            cjk_reg, cjk_med, cjk_bold = _pick_faces(entry_cjk, prefer_zh=True)
            _require_fonttools_if_needed([cjk_reg, cjk_med, cjk_bold])
            cr_path, cr_mime = _ensure_font_file(cjk_reg)
            cr_url = self._server.register(cr_path, cr_mime)
            cjk_faces.append((cr_url, cr_mime, 400))
            cm_path = cm_mime = None
            cb_path = cb_mime = None
            if cjk_med is not None:
                cm_path, cm_mime = _ensure_font_file(cjk_med)
                cjk_faces.append((self._server.register(cm_path, cm_mime), cm_mime, 500))
            if cjk_bold is not None:
                cb_path, cb_mime = _ensure_font_file(cjk_bold)
                cjk_faces.append((self._server.register(cb_path, cb_mime), cb_mime, 700))
            cjk_detail = {
                "regular": face_info(cjk_reg, cr_path, cr_mime),
                "medium": face_info(cjk_med, cm_path, cm_mime) if cjk_med is not None else None,
                "bold": face_info(cjk_bold, cb_path, cb_mime) if cjk_bold is not None else None,
            }
            cjk_paths = (cr_path, cm_path, cb_path)

        # 兜底补丁（完整版 @font-face 化）：西文槽发生了剔字且 CJK 槽为空时，
        # 把西文字体的完整版 face 注册为内部族 INTERNAL_FAMILY_FULL，写进栈的
        # CJK 位置——「A 的西文 + A 的中文」确定可控（@font-face 真绑定，
        # 不依赖按名系统字体解析；Steam 的 CEF 实测不解析按名系统字体）。
        # 文件复用零新增命名：单文件字体用系统路径，ttc 用既有 {key}.ttf/.woff2
        # （_ensure_font_file 命中缓存即复用，同路径同 FontServer URL）；
        # 字重集合与西文槽一致（同文件多声明）；未剔字时不注册。
        # 原按名条目（自动 CJK / 原始族名）在此场景下被替换，不并存。
        full_faces = []
        full_paths = ()
        full_backfill = False
        if split_on and family_cjk is None and strip_done:
            for face, weight in ((regular, 400), (medium_used, 500), (bold, 700)):
                if face is None:
                    continue
                full_path, full_mime = _ensure_font_file(face)
                full_faces.append((self._server.register(full_path, full_mime), full_mime, weight))
                full_paths += (full_path,)
            # Full 族由 _build_css 放进栈（跟在 CJK 族之后），这里只负责把
            # 自动补的 CJK 族从链里去掉，避免按名条目与 Full 并存/重复
            if chain and chain[0] != STEAM_UI_FONT:
                chain = chain[1:]
            full_backfill = True
            log(f"西文槽已剔字且 CJK 槽为空：完整版 face 以 {INTERNAL_FAMILY_FULL} "
                f"回填栈（{len(full_faces)} 条字重声明）")

        # 行为保持决策:此处保护"刚写入的实际产物"(actual),不换用 cache_files_for_state
        # 的预测集——实际 ⊇ 预测在回退/兜底场景可能破,换用会把预测缺口放大成"刚写就删"。
        # 键名推导与预测共用 _pick_faces/_cache_key,一致性由 _tests_v1420 测试守护。
        # 新抽取写入后清理一次：正在用的这些文件受保护（含 Full 回填件），其余按
        # 最近使用只留 9 个
        inuse = [p for p in (reg_path, medium_path, bold_path) + cjk_paths + full_paths if _is_cache_file(p)]
        _prune_font_cache(inuse)

        css = self._build_css(latin_faces, cjk_faces, full_faces, chain, synth_none)
        return {
            "css": css,
            "regular": face_info(regular, reg_path, reg_mime, reg_stripped),
            "medium": (
                face_info(medium_used, medium_path, medium_mime, med_stripped)
                if medium_used is not None
                else None
            ),
            "bold": (
                face_info(bold, bold_path, bold_mime, bold_stripped)
                if bold is not None
                else None
            ),
            "cjk": cjk_detail,
            # 方案 D 记账：剔字是否发生 / 是否发生失败回退（面板提示用，绝不静默）
            "strip": {"done": strip_done, "fallback": strip_failed},
            # 兜底回填：是否以 INTERNAL_FAMILY_FULL 注册了完整版 face（面板/验收判断用）
            "full_backfill": full_backfill,
            "split": split_on,
            "synth_none": synth_none,
            "fallback": chain,
            "zh": entry["zh"],
        }

    @staticmethod
    def _build_css(latin_faces, cjk_faces, full_faces, chain, synth_none=False):
        """生成注入 CSS（唯一入口）。

        内部族方案：西文槽 @font-face 挂 INTERNAL_FAMILY，CJK 槽挂 INTERNAL_FAMILY_CJK；
        西文槽剔字且 CJK 槽空时，西文字体的完整版 face 额外挂 INTERNAL_FAMILY_FULL
        占据栈的 CJK 位置（@font-face 真绑定，不依赖按名系统字体解析）。
        字体栈 = [西文族, CJK族(仅分槽开), Full族(仅兜底激活), ...chain]。chain 已按
        开关拼好（分槽开时含 "Motiva Sans" —— Steam 自带字体，PUA 图标字形在它里面，
        Steam 改名时这里与 _fallback_families 要跟着调整）。
        族名必须与模块级常量一致，漏一处会出现「payload 说 A、CSS 里叫 B」。
        """
        # mime→format 映射唯一来源（后端）。前端 dist/index.js 的 FP_PREVIEW_MIME_FORMAT
        # 与此对应：新增格式时两处同改。
        fmt_of = {
            "font/woff2": "woff2",
            "font/woff": "woff",
            "font/otf": "opentype",
            "font/ttf": "truetype",
        }

        def face_rule(internal, url, mime, weight):
            return (
                f"@font-face{{font-family:\"{internal}\";font-style:normal;font-weight:{weight};"
                f"font-display:swap;src:url(\"{url}\") format(\"{fmt_of.get(mime, 'truetype')}\");}}"
            )

        lines = [face_rule(INTERNAL_FAMILY, url, mime, weight) for url, mime, weight in latin_faces]
        lines.extend(face_rule(INTERNAL_FAMILY_CJK, url, mime, weight) for url, mime, weight in cjk_faces)
        lines.extend(face_rule(INTERNAL_FAMILY_FULL, url, mime, weight) for url, mime, weight in full_faces)
        stack = []
        if latin_faces:
            stack.append(f'"{INTERNAL_FAMILY}"')
        if cjk_faces:
            stack.append(f'"{INTERNAL_FAMILY_CJK}"')
        if full_faces:
            stack.append(f'"{INTERNAL_FAMILY_FULL}"')
        stack += [
            name if name in ("system-ui", "sans-serif", "serif", "monospace") else f'"{name}"'
            for name in chain
        ]
        # 用 html body / html body * 而不是 body / body *：
        # 与 tvdeck 这类主题的 `html, body, body *` 同为 !important 时，
        # 靠更高的特异性胜出，不依赖样式注入先后顺序。
        rule = ":root, html body, html body * { font-family: " + ", ".join(stack) + " !important;"
        if synth_none:
            # 禁用假加粗（已独立于分槽主开关，无条件生效）：
            # 只关字重合成，不碰斜体合成（font-synthesis-style 一律不动）
            rule += " font-synthesis-weight: none !important;"
        rule += " }"
        lines.append(rule)
        return "\n".join(lines)

    async def _inject_main(self, source="user"):
        """主界面标题稳定，直接按候选注入。返回是否成功。

        source 决定成功时写哪个记账字段，见 _mark_inject。
        """
        if not self._css or "main" in self._hit:
            return "main" in self._hit
        candidates = list(TARGETS[0][2])
        # _cef_titles 内部是同步 urlopen（最多 3 个端口 × 0.5 s），必须挪出事件循环
        titles = await asyncio.to_thread(_cef_titles)
        if titles is None:
            # CEF 是硬前提（Decky Loader 自身就依赖它），拿不到 tab 列表只可能是调试口尚未就绪。
            # 不再按固定标题盲试，交给注入窗口退避重试，避免对不存在的 tab 发注定失败的 RPC。
            return False
        # 只在列表里真实存在的候选里挑，避免对不存在的 tab 发注定失败的 RPC
        candidates = [tab for tab in candidates if tab in titles]
        if "Steam" in candidates and "Steam 大屏幕模式" not in titles:
            log("CEF 列表里只有桌面 Steam tab（未见大屏 tab），按桌面注入")
        for tab in candidates:
            css_id = await asyncio.to_thread(_replace_css, tab, self._css)
            if css_id:
                self._hit["main"] = {"tab": tab, "css_id": css_id, "alive": True}
                log(f"已注入 main -> {tab}")
                self._mark_inject(True, source)
                return True
        return False

    async def _inject_dynamic(self, key, source="user"):
        """QAM / MainMenu：标题带 uid，先按已知 uid 试，不行再扫。"""
        if not self._css or key in self._hit:
            return key in self._hit
        prefix = DYNAMIC_PREFIX[key]
        if self._uid:
            tab = f"{prefix}{self._uid}"
            css_id = await asyncio.to_thread(_replace_css, tab, self._css)
            if css_id:
                self._hit[key] = {"tab": tab, "css_id": css_id, "alive": True}
                log(f"已注入 {key} -> {tab}")
                self._mark_inject(True, source)
                return True

        seen_uid = self._uid
        async with self._uid_mutex():
            if self._uid and self._uid != seen_uid:
                # 另一个目标刚发现新 uid，直接复用
                uid = self._uid
            else:
                uid = await asyncio.to_thread(_scan_uid, prefix)
        if not uid:
            # 同一次注入周期里只保留第一条「没扫到」：随退避重试 / 另一个动态目标失败时不再重复刷
            if not self._scan_miss_logged:
                self._scan_miss_logged = True
                log(f"没扫到 {prefix}* 的 tab（Steam 可能不在大屏模式）")
            return False
        self._uid = uid
        state = read_state()
        state["uid"] = uid
        write_state(state)
        tab = f"{prefix}{uid}"
        css_id = await asyncio.to_thread(_replace_css, tab, self._css)
        if css_id:
            self._hit[key] = {"tab": tab, "css_id": css_id, "alive": True}
            log(f"已注入 {key} -> {tab}（uid 发现 {uid}）")
            self._mark_inject(True, source)
            return True
        return False

    async def _remove_all(self):
        items = list(self._hit.items())
        self._hit = {}
        toasts = list(self._toasts.items())
        self._toasts = {}
        self._toast_ids = {}
        jobs = [
            asyncio.to_thread(_remove_css, item["tab"], item["css_id"])
            for _key, item in items
        ]
        jobs.extend(asyncio.to_thread(_remove_css, tab, css_id, True) for tab, css_id in toasts)
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        return len(items) + len(toasts)

    # ---- notificationtoasts_* 弹窗：CEF 事件监听注入 ----

    def _start_toast_watcher(self):
        if self._toast_task is not None and not self._toast_task.done():
            return
        self._toast_task = asyncio.create_task(self._toast_watcher())

    async def _stop_toast_watcher(self):
        task, self._toast_task = self._toast_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _toast_watcher(self):
        """浏览器级 CEF websocket：弹窗窗口一出现即注入当前字体 CSS。

        纯事件驱动（targetCreated / targetInfoChanged / targetDestroyed），无轮询；
        连接断开按 TOAST_WS_BACKOFF 退避重连，任何失败都不影响主注入流程。
        """
        try:
            import aiohttp
        except Exception:
            log("弹窗监听需要 aiohttp，当前运行环境缺少，已停用（不影响其它注入）")
            return
        session = aiohttp.ClientSession()
        try:
            attempt = 0
            while True:
                port = _CEF_CACHE.get("port") or _cef_port_hint()[0]
                ws_url = None
                if port:
                    try:
                        timeout = aiohttp.ClientTimeout(total=3)
                        async with session.get(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as res:
                            data = await res.json()
                        ws_url = data.get("webSocketDebuggerUrl")
                    except Exception:
                        ws_url = None
                if not ws_url:
                    await asyncio.sleep(TOAST_WS_BACKOFF[min(attempt, len(TOAST_WS_BACKOFF) - 1)])
                    attempt += 1
                    continue
                try:
                    async with session.ws_connect(ws_url, timeout=5) as ws:
                        attempt = 0
                        log("弹窗监听：已连接 CEF 浏览器事件流")
                        await ws.send_json({"id": 1, "method": "Target.setDiscoverTargets",
                                            "params": {"discover": True}})
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                    break
                                continue
                            try:
                                data = msg.json()
                            except Exception:
                                continue
                            await self._on_cef_event(data)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log(f"弹窗监听：连接中断（{exc}），准备重连")
                await asyncio.sleep(TOAST_WS_BACKOFF[min(attempt, len(TOAST_WS_BACKOFF) - 1)])
                attempt += 1
        finally:
            await session.close()

    async def _on_cef_event(self, data):
        # 事件处理自隔离：任何异常只丢这一条事件并记日志，绝不冒泡到 WS 循环
        # （冒泡会被通用 except 误判为"连接中断"而断线重连）。
        try:
            if not isinstance(data, dict):
                return
            method = data.get("method")
            params = data.get("params") or {}
            if method == "Target.targetDestroyed":
                # targetDestroyed 的 targetId 直接挂在 params 下，没有 targetInfo
                destroyed_id = params.get("targetId")
                old = self._toast_ids.pop(destroyed_id, None) if destroyed_id else None
                if old and old in self._toasts:
                    css_id = self._toasts.pop(old)
                    await asyncio.to_thread(_remove_css, old, css_id, True)
                return
            info = params.get("targetInfo") or {}
            target_id = info.get("targetId")
            title = info.get("title") or ""
            if info.get("type") != "page" or not title.startswith(TOAST_TAB_PREFIX):
                return
            # 同一 targetId 的标题换了（如 notificationtoasts_1 → _2）：旧记录拆掉重注
            old_title = self._toast_ids.get(target_id)
            if old_title and old_title != title and old_title in self._toasts:
                css_id = self._toasts.pop(old_title)
                await asyncio.to_thread(_remove_css, old_title, css_id)
            self._toast_ids[target_id] = title
            await self._inject_toast_tab(title)
        except Exception as exc:
            log(f"CEF 事件处理异常（连接保持）: {exc!r}")

    async def _inject_toast_tab(self, tab):
        if not self._css or tab in self._toasts:
            return
        css_id = await asyncio.to_thread(_replace_css, tab, self._css)
        if css_id:
            self._toasts[tab] = css_id
            log(f"已注入弹窗 -> {tab}")

    def _uid_mutex(self):
        if self._uid_lock is None:
            self._uid_lock = asyncio.Lock()
        return self._uid_lock

    # ---- UI 模式事件：有界注入窗口 ----
    # 只有前端的 UI 模式事件（大屏 4 / 桌面 7）和用户动作会开窗口；
    # 窗口之外后台没有任何自己醒来干活的东西。

    async def on_ui_mode_changed(self, mode=None):
        """前端订阅到 Steam UI 模式变化时调用（前端已过滤成 4 / 7）。

        退出大屏幕（UI_MODE_DESKTOP = 7）不做检测 / 自动补注：桌面侧不注入，
        事件直接忽略；进大屏（4，含模式未知）与用户动作的注入行为不变。
        """
        plugin = _bind(self)
        try:
            value = int(mode)
        except (TypeError, ValueError):
            value = None
        if value == UI_MODE_DESKTOP:
            log("收到 UI 模式变化事件：7（退出大屏），已按设置忽略，不做桌面补注入")
            return {"ok": True, "mode": value, "ignored": True}
        log(f"收到 UI 模式变化事件：{value}，{WINDOW_DELAY:.0f} s 后开始补注入")
        await plugin._start_inject_window(value)
        return {"ok": True, "mode": value}

    async def _start_inject_window(self, mode=None):
        """取消旧窗口，按当前模式开一个新的有界注入窗口。

        UI 模式事件可能连发：开窗必须串行，否则两个窗口会各写一份样式并存。
        """
        async with self._window_mutex():
            await self._stop_window()
            if not self._css:
                log("注入窗口：当前没有已选字体，跳过")
                return
            self._uid_mutex()
            self._window_task = asyncio.create_task(self._inject_window(mode))

    def _window_mutex(self):
        if self._window_lock is None:
            self._window_lock = asyncio.Lock()
        return self._window_lock

    def _apply_mutex(self):
        if self._apply_lock is None:
            self._apply_lock = asyncio.Lock()
        return self._apply_lock

    async def _stop_window(self):
        task = self._window_task
        self._window_task = None
        if task is not None and not task.done():
            log("注入窗口：提前结束（新的模式变化 / 用户动作 / 卸载或重置），取消")
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _inject_window(self, mode):
        """一次模式变化后的有界补注入，成功即停，最长 WINDOW_TOTAL 秒。"""
        started = time.monotonic()
        keys = self._window_targets(mode)
        try:
            await asyncio.sleep(WINDOW_DELAY)
            self._scan_miss_logged = False  # 每个窗口内最多一条「没扫到」
            self._dead_tabs.clear()
            attempt = 0
            while True:
                missing = await self._window_pass(keys)
                elapsed = time.monotonic() - started
                if not missing:
                    # 计时基准是本窗口开始那一刻（收到模式变化事件时），不是插件进程启动
                    log(f"{len(keys)} 个目标全部注入完成，用时 {elapsed:.2f} 秒")
                    return
                if elapsed >= WINDOW_TOTAL:
                    why = _CEF_CACHE.get("why") or "无"
                    log(f"注入窗口到期（{elapsed:.1f} s），仍未注入 {','.join(missing)}，本次放弃，调试口：{why}")
                    return
                delay = WINDOW_BACKOFF[min(attempt, len(WINDOW_BACKOFF) - 1)]
                attempt += 1
                if elapsed + delay > WINDOW_TOTAL:
                    delay = max(WINDOW_TOTAL - elapsed, 0.0)
                # 只读 _window_pass 刚写下的快照，不再额外探测（多一次 _cef_fetch 要多耗 0.5~1.5 s）：
                # tabs 为 None 是「CEF 根本不通」，非 None 是「CEF 通但目标 tab 还没建好」，
                # 两种卡法分开记，用户看日志就能判断该去开调试口还是该等一下。
                if _CEF_CACHE.get("tabs") is None:
                    why = _CEF_CACHE.get("why") or "调试端口无响应"
                    log(f"注入窗口：CEF 调试口尚未就绪（{why}），{delay:.1f} s 后重试（第 {attempt} 次）")
                else:
                    log(f"注入窗口：{','.join(missing)} 未注入，{delay:.1f} s 后重试（第 {attempt} 次）")
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"注入窗口异常: {exc}")

    @staticmethod
    def _window_targets(mode):
        """大屏模式（UI_MODE_BIG_PICTURE = 4，含模式未知）三个目标都要；桌面（7）只要主窗口。"""
        if mode == UI_MODE_DESKTOP:
            return ["main"]
        return [key for key, _label, _candidates in TARGETS]

    async def _window_pass(self, keys):
        """一轮全量注入，返回仍未成功的目标 key。"""
        results = await asyncio.gather(
            *(self._window_target(key) for key in keys), return_exceptions=True
        )
        missing = []
        for key, res in zip(keys, results):
            if isinstance(res, Exception):
                log(f"注入窗口：{key} 异常 {res}")
                missing.append(key)
            elif not res:
                missing.append(key)
        return missing

    async def _window_target(self, key):
        """窗口内的单个目标：样式还在就不重复注入，随页面丢了才按当前实例补注入。"""
        item = self._hit.get(key)
        if item is not None:
            # 同上：_cef_titles 是同步 urlopen，放到线程里跑，避免卡住事件循环
            titles = await asyncio.to_thread(_cef_titles)
            if titles is not None and item["tab"] not in titles:
                # tab 已经不在了：不必再发注定超时的 RPC，直接走补注入
                self._hit.pop(key, None)
                item = None
            if item is not None:
                if item["tab"] in self._dead_tabs:
                    # 本事件已确认死亡（execute_in_tab 明确 not found），跳过重复验活，
                    # 省掉重复失败 RPC 的开销
                    self._hit.pop(key, None)
                    item = None
                else:
                    alive = await asyncio.to_thread(_style_exists, item["tab"], item["css_id"])
                    if alive is True:
                        item["alive"] = True
                        return True
                    if alive is None:
                        # 读不到就当作页面还没建完：保留记录、不重复注入，下一轮再验
                        return False
                    # alive is False 意味着 execute_in_tab 失败且文本含 not found，不是猜的：
                    # 记入本事件死亡名单，后续轮次不再重复验活
                    self._dead_tabs.add(item["tab"])
                    log(f"{key} 的样式随页面重建丢了（{item['tab']}），重新注入")
                    self._hit.pop(key, None)
        if key == "main":
            return await self._inject_main("heal")
        return await self._inject_dynamic(key, "heal")

    def _payload(self) -> "StatePayload":
        targets = []
        for key, label, _candidates in TARGETS:
            item = self._hit.get(key)
            # 「已注入」= 有记录 + 上一轮复核仍存活 + 不是兜底 tab
            fallback = bool(item) and item["tab"] in FALLBACK_TABS
            targets.append(
                {
                    "key": key,
                    "label": label,
                    "tab": item["tab"] if item else None,
                    "ok": bool(item) and not fallback and item.get("alive", True),
                    "fallback": fallback,
                }
            )
        cef = {"port": None, "tabs": None, "error": ""}
        # 只读已有快照：抓取由插件加载和注入窗口负责
        tabs = _CEF_CACHE.get("tabs")
        cef["port"] = _CEF_CACHE.get("port")
        if tabs is None:
            cef["error"] = _CEF_CACHE.get("why") or "CEF 调试口尚未就绪"
        else:
            cef["tabs"] = len(tabs)
        state = read_state()
        favorites = _clean_favorites(state)
        inject_level, inject_stamp = _inject_status(state)
        # 分槽开关状态：family_cjk 取 state 原值（主开关关闭时槽位即被清空，重开后为跟随系统），
        # 子开关默认值与 _prepare 同口径（synth_weight_none 缺省=开）
        return {
            "ok": True,
            "inject_level": inject_level,
            "inject_text": inject_stamp,
            "heal_text": _heal_stamp(state),
            "mode": MODE_EXTRACT,
            "family": self._family,
            "family_cjk": state.get("family_cjk"),
            "has_css": bool(self._css),
            "targets": targets,
            "server": {"running": self._server.running, "port": self._server.port},
            "woff2_ok": bool(_FT_CACHE.get("brotli_ok", False)),
            "fallback": self._detail.get("fallback", []),
            "zh": self._detail.get("zh"),
            "regular": self._detail.get("regular"),
            "medium": self._detail.get("medium"),
            "bold": self._detail.get("bold"),
            "cjk": self._detail.get("cjk"),
            "cjk_split": bool(state.get("cjk_split")),
            "synth_weight_none": bool(state.get("synth_weight_none", True)),
            "preview": bool(_preview_level_of(state) != 0),
            "preview_level": _preview_level_of(state),
            "expand_level": _expand_level_of(state),
            "preview_cache_mb": _preview_cache_mb_of(state),
            "strip": self._detail.get("strip") or {"done": False, "fallback": False},
            "full_backfill": bool(self._detail.get("full_backfill")),
            "uid": self._uid,
            "favorites": favorites,
            "history": list(state.get("history") or []),
            "cef": cef,
        }


def _bind(obj):
    """Decky 3.3.1 的生命周期回调可能把类本身当 self 传进来，统一回落到实例。"""
    global _LIVE
    if isinstance(obj, Plugin):
        return obj
    if _LIVE is None:
        _LIVE = Plugin()
    return _LIVE
