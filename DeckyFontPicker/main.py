"""字体选择器（纯 Decky 注入版，抽取嵌入）

- 字体枚举：fc-list 按 face 读取（family/style/weight/file/index/format/lang）
- 投递方式只有抽取嵌入（extract）：
  @font-face + 本地 HTTP 服务（127.0.0.1 + 随机端口 + 随机 token），
  .ttc/.otc 用 fontTools 抽出实际用到的 face，缓存到插件自己的缓存目录。
- local() 按名引用模式已完全弃用：不再生成按名引用的 @font-face，也不再做命中探测。
- 样式注入：调用 Decky 自身的 legacy 方法 inject_css_into_tab / remove_css_from_tab
  （不写 ~/homebrew/themes，不依赖 CSS Loader，不直接连 CEF 调试口）
"""

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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
# 子开关②「使用 CJK 标点符号」开启时的追加段：这些标点从中文字体全角渲染。
# 只改这里这一份常量列表即可调整标点归属，不做运行时计算。
LATIN_STRIP_RANGES_PUNCT = LATIN_STRIP_RANGES_BASE + [
    (0x2018, 0x201F),  # 弯引号
    (0x2026, 0x2026),  # 省略号
    (0x00B7, 0x00B7),  # 间隔号 ·
]


# 剔字掩码版本：掩码内容变更（如 v2 补入谚文字母区）时 +1，产物文件名随之
# 变化，旧产物自然失效并按陈旧缓存清理，不需要手工清缓存
LATIN_STRIP_MASK_VERSION = 2


def _strip_variant(cjk_punct):
    """剔字产物的文件名变体后缀：掩码内容或版本变更即新文件，
    切子开关②/升级掩码后自动重剔一次。"""
    return f".latinp{LATIN_STRIP_MASK_VERSION}" if cjk_punct else f".latin{LATIN_STRIP_MASK_VERSION}"

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
_LIVE = None  # 当前 Plugin 实例，供 Decky 以类为 self 回调时回落
UID_SCAN_MAX = 320
UID_SCAN_AHEAD = 60
# UI 模式事件驱动的有界注入窗口：前端订阅 Steam 的 UI 模式变化（大屏 4 / 桌面 7），
# 变化那一刻三个页面的文档都会销毁重建，只在这个时刻补注入；窗口之外后台完全静默。
UI_MODE_BIG_PICTURE = 4
UI_MODE_DESKTOP = 7
WINDOW_DELAY = 2.0  # 模式切换后先等页面回调建完
WINDOW_BACKOFF = (0.5, 1.0, 2.0, 4.0, 8.0)  # 单次窗口内的重试退避，用尽后按 8 s 继续
WINDOW_TOTAL = 60.0  # 单次窗口总时长上限
WINDOW_UID_SCAN_LIMIT = 2  # 每个动态目标在单个窗口内最多做 2 次 uid 扫描（首发 + 随退避再试一次）
# CEF 快速模式（默认关闭）：直接读 Steam 的 CEF 调试端口 /json/list，一次列出全部 tab。
# 前置条件：Steam 设置 → 开发者 → 「CEF 远程调试」（底层是给 steamwebhelper 加
# --remote-debugging-port）。该设置关闭、端口连不上或列表异常时，一律静默回落到上面的
# get_tab_id 逐号探测，行为与未开启时完全一致。
CEF_PORTS = (8080, 8081)
CEF_TIMEOUT = 0.5
CEF_CACHE_TTL = 0.3
CEF_TAB_RE = re.compile(r"^(MainMenu|QuickAccess)_uid(\d+)$")
_CEF_FAST = {"on": False}
_CEF_CACHE = {"at": 0.0, "tabs": None, "port": None, "why": "", "state": None}
_CEF_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# fontconfig 读取
# --------------------------------------------------------------------------- #

def _run(args, timeout=20):
    env = None
    if os.path.isdir(VENDOR_DIR):
        env = dict(os.environ)
        prev = env.get("PYTHONPATH")
        env["PYTHONPATH"] = VENDOR_DIR + (os.pathsep + prev if prev else "")
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def _css_weight(fc_weight):
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


def _parse_charset(spec):
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


def _cjk_coverage(ranges):
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
                "index": index,
                "format": fmt,
                "zh": zh,
                "ja": ja,
                "ko": ko,
                # fontconfig 的 lang（"zh-cn|en|ja" 这种），前端直接展示覆盖范围
                "lang": parts[6].strip(),
                "zh_extra": zh_extra,
                # fontconfig 的 spacing：100 = mono，110 = charcell，0/其它 = 比例字体
                "mono": spacing in (100, 110),
                "collection": ext in COLLECTION_EXT,
            }
        )
    return faces


def _family_map():
    families = {}
    for face in _faces():
        entry = families.setdefault(
            face["family"],
            {"name": face["family"], "aliases": [], "faces": [], "langs": [], "zh": False, "ja": False, "ko": False, "primary": _primary_lang(face["family"]), "zh_extra": False, "mono": False},
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
    return families


def _lang_display_order(tags):
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


def list_families(families=None):
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


def _pick_faces(entry, prefer_zh=True):
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
    chain.append("Motiva Sans")
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
    """
    protected = {os.path.abspath(p) for p in protect if p}
    try:
        names = os.listdir(CACHE_DIR)
    except OSError:
        return 0
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
    if removed:
        log(f"缓存清理：原有 {len(entries)} 个，保留 {len(keepers)} 个，删除 {removed} 个")
    return removed


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
        proc = _run(
            [_system_python(), _helper(), "extract", path, str(face["index"]), ttf],
            timeout=180,
        )
        if proc.returncode != 0 or not os.path.exists(ttf):
            raise RuntimeError(f"抽取 {os.path.basename(path)}#{face['index']} 失败: {proc.stderr.strip()[:200]}")

    if os.path.getsize(ttf) <= WOFF2_MAX_BYTES:
        proc = _run([_system_python(), _helper(), "woff2", ttf, woff2], timeout=180)
        if proc.returncode == 0 and os.path.exists(woff2) and os.path.getsize(woff2) > 0:
            try:
                os.remove(ttf)
            except OSError:
                pass
            return woff2, "font/woff2"

    return ttf, "font/ttf"


def _ensure_latin_file(face, cjk_punct):
    """西文槽的剔字版 face（方案 D）。返回 (路径, MIME, 剔字发生, 剔字失败)。

    - 读 face cmap 与剔除掩码求交：交集为空 → 原样使用（绝大多数西文字体，
      helper 退出码 0 且不产出文件，无剔字开销）；
    - 交集非空 → fontTools subset 剔字，产物按掩码版本独立命名缓存
      （{key}.latin.ttf/.woff2 或 {key}.latinp.ttf/.woff2，切子开关②自动重剔）；
    - 失败（退出码≠0 或产物缺失/为空）→ 回退原始 face，strip_fallback=True，
      调用方据此给面板警告，绝不静默。剔除掩码常量在模块级，区间串传给 helper。
    """
    variant = _strip_variant(cjk_punct)
    key = _cache_key(face)
    ttf = os.path.join(CACHE_DIR, f"{key}{variant}.ttf")
    woff2 = os.path.join(CACHE_DIR, f"{key}{variant}.woff2")
    ranges = LATIN_STRIP_RANGES_PUNCT if cjk_punct else LATIN_STRIP_RANGES_BASE
    strip_arg = ",".join(f"{lo:X}-{hi:X}" if hi > lo else f"{lo:X}" for lo, hi in ranges)

    if os.path.exists(woff2) and os.path.getsize(woff2) > 0:
        _touch(woff2)  # 命中缓存 = 一次使用，刷新 LRU 时间
        return woff2, "font/woff2", True, False
    if os.path.exists(ttf) and os.path.getsize(ttf) > 0:
        _touch(ttf)
    if not (os.path.exists(ttf) and os.path.getsize(ttf) > 0):
        try:
            proc = _run(
                [_system_python(), _helper(), "subset", face["file"], str(face["index"]), ttf, strip_arg],
                timeout=180,
            )
            rc, err = proc.returncode, proc.stderr.strip()[:200]
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

    if os.path.getsize(ttf) <= WOFF2_MAX_BYTES:
        proc = _run([_system_python(), _helper(), "woff2", ttf, woff2], timeout=180)
        if proc.returncode == 0 and os.path.exists(woff2) and os.path.getsize(woff2) > 0:
            try:
                os.remove(ttf)
            except OSError:
                pass
            return woff2, "font/woff2", True, False

    return ttf, "font/ttf", True, False


def _protected_cache_files(state, families):
    """启动清理的保护集：按 state 反推当前注入会用到的缓存文件名（零抽取）。

    family / family_cjk → _pick_faces 选面 → _cache_key → {key}.ttf/.woff2；
    主开关开启时西文槽的剔字产物 {key}.{latin|latinp}.ttf/.woff2 一并保护
    （掩码版本取当前子开关②状态，另一版本的产物视为陈旧缓存照常清理）。
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
                variant = _strip_variant(bool(state.get("cjk_punct")))
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


def _remove_css_locked(tab, css_id, quiet=False):
    res = _decky_method("remove_css_from_tab", {"tab": tab, "css_id": css_id})
    if not res.get("success") and not quiet:
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


def _remove_css(tab, css_id):
    with _tab_lock(tab):
        ok = _remove_css_locked(tab, css_id)
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
                why = f"端口 {candidate} 不可用（{exc}）"
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
        log(f"CEF 快速模式恢复：{reason}")
    else:
        log(f"CEF 快速模式回落（{reason}），改用 get_tab_id 探测")


def _cef_titles():
    """快速模式下返回当前 tab 标题集合；不可用时返回 None（调用方回落旧路径）。"""
    tabs = _cef_fetch()
    if tabs is None:
        _cef_log_state(False, _CEF_CACHE.get("why") or "调试端口无响应")
        return None
    _cef_log_state(True, _CEF_CACHE.get("why") or "")
    return {t.get("title") for t in tabs}


def _tab_exists(tab):
    if _CEF_FAST["on"]:
        titles = _cef_titles()
        if titles is not None:
            return tab in titles
    return bool(_decky_method("get_tab_id", {"name": tab}, timeout=2).get("success"))


def _eval_in_tab(tab, code):
    return _decky_method("execute_in_tab", {"tab": tab, "run_async": False, "code": code}, timeout=2)


def _style_exists(tab, css_id):
    """返回 True / False / None(无法判断)。

    execute_in_tab 对已消失的 tab 返回 HTTP 500，文本里没有 "not found"，
    单凭它分不清「tab 没了」和「RPC 出问题」；这种情况再用 get_tab_id 问一次：
    明确 not found 才判 False，明确成功仍是 None（tab 活着但读不到样式，下轮再验），
    get_tab_id 自己也失败或超时也记 None，不猜。
    """
    res = _eval_in_tab(tab, f"document.getElementById({json.dumps(css_id)}) !== null")
    if not res.get("success"):
        text = str(res.get("result") or "")
        if "not found" in text:
            return False
        probe = _decky_method("get_tab_id", {"name": tab}, timeout=2)
        if not probe.get("success") and "not found" in str(probe.get("result") or ""):
            log(f"判定 {tab} 已消失（get_tab_id 明确 not found）")
            return False
        return None
    inner = res.get("result")
    # legacy 路由会再包一层 {success, result}
    if isinstance(inner, dict):
        return bool(inner.get("success")) and inner.get("result") is True
    return inner is True


# 并发探测参数(实测:Decky 服务端对 legacy RPC 基本串行,8 线程 ~1.5×,
# 32 线程只有 1.7×,边际递减;16 的在途上限对 Decky 无压力,64×32 压测无错)
_SCAN_WORKERS = 8
_SCAN_CHUNK = 16


def _scan_uid(hint, prefix, missed=None):
    """按目标前缀扫出当前 uid（menu 扫 MainMenu_uid*，qam 扫 QuickAccess_uid*）。

    先试上次的 uid，再往上扫 60 个，最后回落到 1..UID_SCAN_MAX。
    missed：本注入事件里已确认 miss 的 uid 集合（按 prefix 各自记账，调用方持有），
    传 None 表示不做续传。开启 CEF 快速模式时直接从 /json/list 的标题里取
    最大的 uid，不再逐个探测。
    """
    if _CEF_FAST["on"]:
        titles = _cef_titles()
        if titles is not None:
            best = None
            for title in titles:
                match = CEF_TAB_RE.match(title or "")
                if match and match.group(1) + "_uid" == prefix:
                    value = int(match.group(2))
                    best = value if best is None else max(best, value)
            return best
    order = []
    if isinstance(hint, int) and hint > 0:
        order.append(hint)
        order.extend(range(hint + 1, min(hint + UID_SCAN_AHEAD, UID_SCAN_MAX) + 1))
    seen = set(order)
    for value in range(1, UID_SCAN_MAX + 1):
        if value not in seen:
            order.append(value)
    # 续传：本事件已确认 miss 的号不再探测。uid 在会话内单调自增不复用，
    # 所以事件内「此刻不存在」的号到重试时依然不存在，跳过是安全的。
    if missed:
        order = [value for value in order if value not in missed]
    probed = []
    result = None

    def _probe(value):
        return _tab_exists(f"{prefix}{value}")

    with ThreadPoolExecutor(max_workers=_SCAN_WORKERS) as pool:
        for start in range(0, len(order), _SCAN_CHUNK):
            block = order[start:start + _SCAN_CHUNK]
            # 严格按序读结果：优先级（先 hint、再 +60、再全量）不因并发改变；
            # 命中后本批在途的剩余探测（≤15 个只读 miss）会跑完但不采纳。
            for value, ok in zip(block, pool.map(_probe, block)):
                if ok:
                    result = value
                    break
                probed.append(value)
            if result is not None:
                break
    # 记账：本次确实探测过且未命中的 uid 存入 missed，供同事件后续扫描续传
    if missed is not None:
        missed.update(probed)
        if result is None:
            missed.update(order)
    log(f"{prefix}* 扫描完成：探测 {len(probed)} 次"
        f"{f'，命中 uid {result}' if result is not None else '，未命中'}")
    return result


# --------------------------------------------------------------------------- #
# 前置条件：抽取嵌入在需要 fontTools 时才强制校验
# --------------------------------------------------------------------------- #

_FT_CACHE = {"at": 0.0, "ok": False, "version": None}


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
    _FT_CACHE.update({"at": now, "ok": ok, "version": version})
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
        raise RuntimeError(
            f"分槽注入的西文剔字需要 fontTools，当前 Python（{_system_python()}）里没有。\n"
            f"- 本次选的 {name} 需要剔除自带的 CJK/PUA 字形后再注入\n"
            "- 关闭「按语言注入」可回到不需要 fontTools 的单槽模式，或手动安装后重试：\n"
            f"  {_system_python()} -m pip install --user fonttools\n"
        )
    raise RuntimeError(
        f"抽取嵌入需要 fontTools，当前 Python（{_system_python()}）里没有。\n"
        f"- 本次选的 {name} 是 .ttc/.otc 集合，必须先抽出单个 face\n"
        "- 换成带 fontTools 的完整版，或手动安装后重试：\n"
        f"  {_system_python()} -m pip install --user fonttools\n"
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
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_state(data):
    os.makedirs(os.path.dirname(_state_path()) or ".", exist_ok=True)
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _clean_favorites(state):
    """把 state 里的 favorites 规范化（去重、去空）。"""
    favorites = []
    for name in state.get("favorites") or []:
        if isinstance(name, str) and name and name not in favorites:
            favorites.append(name)
    return favorites


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


def _inject_status(state):
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
        _CEF_FAST["on"] = bool(read_state().get("cef_fast"))
        self._uid_scan_budget = {}  # key -> 本窗口剩余 uid 扫描次数（窗口外为空，用户动作注入不限次）
        self._scan_miss_logged = False  # 本次注入周期里是否已经打过「没扫到」，同一事件内只打一条
        self._scan_miss = {}   # prefix -> set[int]：本注入事件已确认 miss 的 uid（续传用，事件开始清空）
        self._dead_tabs = set()  # 本注入事件已确认死亡的完整 tab 名（验活续传用，事件开始清空）
        self._uid_lock = None  # asyncio.Lock，串行化 uid 发现，懒创建（__init__ 时还没有事件循环）
        self._window_task = None  # 当前注入窗口（同一时刻最多一个）
        self._window_lock = None  # asyncio.Lock，串行化开窗，懒创建
        self._cache_inuse = set()  # 当前注入正在用的缓存文件（清理时保护）

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
        if _CEF_FAST["on"]:
            _port, source = await asyncio.to_thread(_cef_port_hint)
            tabs = await asyncio.to_thread(_cef_fetch, True)
            if tabs is None:
                _CEF_CACHE["state"] = False
                log(f"CEF 快速模式不可用：{_CEF_CACHE.get('why')}；已回落 get_tab_id。"
                    "请检查 Steam 设置 → 开发者 → CEF 远程调试")
            else:
                _CEF_CACHE["state"] = True
                log(f"CEF 快速模式生效：{source}（端口 {_CEF_CACHE.get('port')}，{len(tabs)} 个 tab）")
        if not family:
            return
        if family not in families:
            log(f"已保存字体 {family!r} 不在系统中，清空选择")
            state.pop("family", None)
            state.pop("inject_at", None)
            state.pop("inject_failed_at", None)
            state.pop("heal_at", None)
            write_state(state)
            return
        # 主开关开时校验 CJK 槽：字体已卸载则清键（与 family 同款），不影响西文槽恢复
        if state.get("cjk_split") and state.get("family_cjk") and state["family_cjk"] not in families:
            log(f"已保存的 CJK 槽字体 {state['family_cjk']!r} 不在系统中，清空 CJK 槽选择")
            state.pop("family_cjk", None)
            write_state(state)
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
        await plugin._cancel_dyn()
        await plugin._stop_window()
        await plugin._remove_all()
        plugin._server.stop()

    # ---- 前端方法 ----

    async def get_fonts(self):
        # Decky 会把插件方法的返回值原样包成 {success, result}，这里直接返回数组
        families = await asyncio.to_thread(_family_map)
        return list_families(families)

    async def get_font_state(self):
        plugin = _bind(self)
        return plugin._payload()

    async def set_font(self, family):
        plugin = _bind(self)
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", **plugin._payload()}
        state = read_state()
        state["family"] = family
        write_state(state)
        return await plugin._apply(family)

    async def set_cjk_font(self, family):
        """CJK 槽位选择（主开关开时前端按语言归属调用）。

        只写 family_cjk，不动西文槽；槽位归属完全由前端按字体语言决定，后端不做判断。
        西文槽还没有字体时先拒绝：注入栈以西文槽为锚，先选西文再选中日韩。
        """
        plugin = _bind(self)
        state = read_state()
        if not state.get("family"):
            return {"ok": False, "error": "请先选择西文字体，再设置中日韩槽位", **plugin._payload()}
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", **plugin._payload()}
        state = read_state()
        state["family_cjk"] = family
        write_state(state)
        return await plugin._apply(state["family"])

    async def set_split_options(self, cjk_split=None, synth_weight_none=None, cjk_punct=None):
        """分槽注入的主/子开关（持久化）。三个参数都可选，None = 不变。

        子开关默认值（主开关开启时）：synth_weight_none 缺省视为开、cjk_punct 缺省视为关。
        任一开关变化都会改变注入 CSS（剔字掩码版本 / Motiva Sans / synthesis 行），
        因此按当前字体重新注入一次；没有已选字体时只落盘，不注入。
        """
        plugin = _bind(self)
        state = read_state()
        changed = False
        if cjk_split is not None:
            value = bool(cjk_split)
            changed = changed or bool(state.get("cjk_split")) != value
            state["cjk_split"] = value
        if synth_weight_none is not None:
            value = bool(synth_weight_none)
            changed = changed or bool(state.get("synth_weight_none", True)) != value
            state["synth_weight_none"] = value
        if cjk_punct is not None:
            value = bool(cjk_punct)
            changed = changed or bool(state.get("cjk_punct")) != value
            state["cjk_punct"] = value
        if not changed:
            return plugin._payload()
        write_state(state)
        log(f"分槽开关更新：cjk_split={state.get('cjk_split')} "
            f"synth_weight_none={state.get('synth_weight_none', True)} cjk_punct={state.get('cjk_punct')}")
        family = plugin._family or read_state().get("family")
        if not family:
            return plugin._payload()
        return await plugin._apply(family)

    async def reset_font(self):
        plugin = _bind(self)
        await plugin._cancel_dyn()
        await plugin._stop_window()
        removed = await plugin._remove_all()
        plugin._css = None
        plugin._family = None
        plugin._detail = {}
        state = read_state()
        # 恢复默认 = 清空两个槽位；不动任何开关（cef_fast 与分槽三开关原样保留）
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

    async def reapply(self):
        plugin = _bind(self)
        if not plugin._family:
            return {"ok": False, "error": "当前没有选择字体", **plugin._payload()}
        return await plugin._apply(plugin._family)

    async def on_ui_loaded(self):
        """前端模块加载时的空壳回调：只记日志，不做任何注入。"""
        log("前端模块已加载（on_ui_loaded），本步不做任何注入")
        return {"ok": True}

    # ---- CEF 快速模式 ----

    async def set_cef_fast(self, enabled):
        """CEF 快速模式开关（持久化）。开启失败不报错，只回落到 get_tab_id 探测。"""
        plugin = _bind(self)
        enabled = bool(enabled)
        _CEF_FAST["on"] = enabled
        state = read_state()
        state["cef_fast"] = enabled
        write_state(state)
        if enabled:
            _port, source = await asyncio.to_thread(_cef_port_hint)
            tabs = await asyncio.to_thread(_cef_fetch, True)
            if tabs is None:
                _CEF_CACHE["state"] = False
                log(f"CEF 快速模式开启失败：{_CEF_CACHE.get('why')}；已回落 get_tab_id。"
                    "请检查 Steam 设置 → 开发者 → CEF 远程调试")
            else:
                _CEF_CACHE["state"] = True
                log(f"CEF 快速模式已开启：{source}（端口 {_CEF_CACHE.get('port')}，{len(tabs)} 个 tab）")
        else:
            _CEF_CACHE["state"] = None
            log("CEF 快速模式已关闭，改用 get_tab_id 探测")
        # 探测路径变了，按新路径重新补注入一次（用户动作，开一次有界窗口）
        await plugin._start_inject_window()
        return plugin._payload()

    # ---- 日志 ----

    async def get_recent_log(self):
        """只读返回最新日志尾部内容，供面板「复制日志」写入剪贴板。"""
        return await asyncio.to_thread(_recent_log)

    # ---- 收藏夹 ----

    async def toggle_favorite(self, family):
        plugin = _bind(self)
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", **plugin._payload()}
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
        payload = plugin._payload()
        payload["message"] = "已加入收藏夹" if added else "已移出收藏夹"
        return payload

    # ---- 应用 / 注入 ----

    async def _apply(self, family, family_cjk=None):
        """切换字体或重新注入当前字体。

        family_cjk 省略时由 _prepare 读 state（CJK 槽归属由 set_cjk_font 预先写好）。
        顺序固定为「先检查 / 先准备，最后才拆旧样式」：
        前置条件不满足（字体不存在、缺 fontTools、抽取或起服务失败）时直接返回错误，
        已经注入的 CSS 原样保留，不会出现「先 _remove_all() 再抛异常」导致样式被拆掉。
        """
        async with self._window_mutex():
            await self._stop_window()
        try:
            detail = await asyncio.to_thread(self._prepare, family, family_cjk)
        except Exception as exc:
            log(f"准备字体 {family!r} 失败: {exc}")
            self._mark_inject(False)
            payload = self._payload()
            payload.update({"ok": False, "error": str(exc)})
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
        self._scan_miss.clear()
        self._dead_tabs.clear()
        self._dyn_task = asyncio.create_task(self._inject_dynamic_all())
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
        """把选中字体准备成一段 CSS。任何失败都直接抛异常，调用方尚未拆旧样式。"""
        state = read_state()
        split_on = bool(state.get("cjk_split"))
        # 子开关默认值仅在主开关开启时生效：synth_weight_none 缺省=开、cjk_punct 缺省=关
        synth_none = split_on and bool(state.get("synth_weight_none", True))
        cjk_punct = split_on and bool(state.get("cjk_punct"))
        families = _family_map()
        entry = families.get(family)
        if entry is None:
            raise RuntimeError(f"系统里没有字体 {family}")
        entry_cjk = None
        if family_cjk is None:
            family_cjk = state.get("family_cjk")
        if split_on and family_cjk:
            entry_cjk = families.get(family_cjk)
            if entry_cjk is None:
                # CJK 槽指向已卸载的字体：按「跟随系统」处理（启动校验负责清键）
                log(f"CJK 槽字体 {family_cjk!r} 不在系统中，本次按跟随系统处理")
                family_cjk = None
        chain = _fallback_families(
            families, family, entry["zh"], split_on=split_on, cjk_family=family_cjk
        )
        return self._prepare_extract(
            family, entry, chain,
            split_on=split_on, family_cjk=family_cjk, entry_cjk=entry_cjk,
            synth_none=synth_none, cjk_punct=cjk_punct,
        )

    def _prepare_extract(self, family, entry, chain, split_on=False, family_cjk=None,
                         entry_cjk=None, synth_none=False, cjk_punct=False):
        """抽取 face 并生成 CSS。

        - 主开关关：单槽 1.4.2 路径（zh 优先选面、不剔字、不注册 medium、
          无 Motiva Sans / synthesis，回退链逐字节同旧版）；
        - 主开关开：西文槽 prefer_zh=False + 方案 D 剔字（掩码按子开关②选版本）
          + medium 注册；CJK 槽保持 zh 优先与原抽取流程，不做子集化（实测无收益）。
        """
        regular, medium, bold = _pick_faces(entry, prefer_zh=not split_on)
        # 集合字体 / 西文剔字才需要 fontTools；这一步在拆卸旧样式之前，失败时旧 CSS 不动
        _require_fonttools_if_needed([regular, medium, bold], subset_needed=split_on)

        # 西文槽：主开关开走剔字通路（_ensure_latin_file 内部已含失败回退），
        # 关则与 1.4.2 完全一致
        def latin_file(face):
            if split_on:
                return _ensure_latin_file(face, cjk_punct)
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
            if chain and chain[0] != "Motiva Sans":
                chain = chain[1:]
            full_backfill = True
            log(f"西文槽已剔字且 CJK 槽为空：完整版 face 以 {INTERNAL_FAMILY_FULL} "
                f"回填栈（{len(full_faces)} 条字重声明）")

        # 新抽取写入后清理一次：正在用的这些文件受保护（含 Full 回填件），其余按
        # 最近使用只留 9 个
        inuse = [p for p in (reg_path, medium_path, bold_path) + cjk_paths + full_paths if _is_cache_file(p)]
        self._cache_inuse = set(inuse)
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
            "cjk_punct": cjk_punct,
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
        stack = [f'"{INTERNAL_FAMILY}"']
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
            # 禁用假加粗（子开关①，只随主开关+子开关都开时出现）：
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
        if _CEF_FAST["on"]:
            titles = _cef_titles()
            if titles is not None:
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

        budget = self._uid_scan_budget.get(key)
        if budget is not None and budget <= 0:
            log(f"{key}: 本次注入窗口的扫描机会已用完，等下一次 UI 模式变化")
            return False
        if budget is not None:
            self._uid_scan_budget[key] = budget - 1
        seen_uid = self._uid
        async with self._uid_mutex():
            if self._uid and self._uid != seen_uid:
                # 另一个目标刚扫到新 uid，直接复用，省一次全量扫描
                uid = self._uid
            else:
                uid = await asyncio.to_thread(
                    _scan_uid, self._uid, prefix, self._scan_miss.setdefault(prefix, set())
                )
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
        if items:
            await asyncio.gather(
                *(
                    asyncio.to_thread(_remove_css, item["tab"], item["css_id"])
                    for _key, item in items
                ),
                return_exceptions=True,
            )
        return len(items)

    def _uid_mutex(self):
        if self._uid_lock is None:
            self._uid_lock = asyncio.Lock()
        return self._uid_lock

    # ---- UI 模式事件：有界注入窗口 ----
    # 只有前端的 UI 模式事件（大屏 4 / 桌面 7）和用户动作会开窗口；
    # 窗口之外后台没有任何自己醒来干活的东西。

    async def on_ui_mode_changed(self, mode=None):
        """前端订阅到 Steam UI 模式变化时调用（前端已过滤成 4 / 7）。"""
        plugin = _bind(self)
        try:
            value = int(mode)
        except (TypeError, ValueError):
            value = None
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
            self._uid_scan_budget = {key: WINDOW_UID_SCAN_LIMIT for key in DYNAMIC_PREFIX}
            self._scan_miss_logged = False  # 每个窗口内最多一条「没扫到」
            self._scan_miss.clear()
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
                    log(f"注入窗口到期（{elapsed:.1f} s），仍未注入 {','.join(missing)}，本次放弃")
                    return
                delay = WINDOW_BACKOFF[min(attempt, len(WINDOW_BACKOFF) - 1)]
                attempt += 1
                if elapsed + delay > WINDOW_TOTAL:
                    delay = max(WINDOW_TOTAL - elapsed, 0.0)
                log(f"注入窗口：{','.join(missing)} 未注入，{delay:.1f} s 后重试（第 {attempt} 次）")
                await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"注入窗口异常: {exc}")
        finally:
            self._uid_scan_budget = {}

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
            if _CEF_FAST["on"]:
                titles = _cef_titles()
                if titles is not None and item["tab"] not in titles:
                    # tab 已经不在了：不必再发注定超时的 RPC，直接走补注入
                    self._hit.pop(key, None)
                    item = None
            if item is not None:
                if item["tab"] in self._dead_tabs:
                    # 本事件已确认死亡（get_tab_id 明确 not found），跳过重复验活，
                    # 省掉「execute_in_tab 500 + get_tab_id 补探」≈3.5 ms 的重复账
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
                    # alive is False 意味着 get_tab_id 明确 not found，不是猜的：
                    # 记入本事件死亡名单，后续轮次不再重复验活
                    self._dead_tabs.add(item["tab"])
                    log(f"{key} 的样式随页面重建丢了（{item['tab']}），重新注入")
                    self._hit.pop(key, None)
        if key == "main":
            return await self._inject_main("heal")
        return await self._inject_dynamic(key, "heal")

    def _payload(self):
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
        cef = {"enabled": _CEF_FAST["on"], "port": None, "tabs": None, "error": ""}
        if _CEF_FAST["on"]:
            # 只读已有快照：抓取由插件加载、set_cef_fast 和注入窗口负责
            tabs = _CEF_CACHE.get("tabs")
            cef["port"] = _CEF_CACHE.get("port")
            if tabs is None:
                cef["error"] = _CEF_CACHE.get("why") or "未检测到 CEF 调试端口"
            else:
                cef["tabs"] = len(tabs)
        state = read_state()
        favorites = _clean_favorites(state)
        inject_level, inject_stamp = _inject_status(state)
        # 分槽开关状态：family_cjk 取 state 原值（主开关关闭时仍保留已选、重开自动恢复），
        # 子开关默认值与 _prepare 同口径（synth_weight_none 缺省=开、cjk_punct 缺省=关）
        return {
            "ok": True,
            "inject_level": inject_level,
            "inject_text": inject_stamp,
            "heal_text": _heal_stamp(state),
            "mode": MODE_EXTRACT,
            "internal": INTERNAL_FAMILY,
            "family": self._family,
            "family_cjk": state.get("family_cjk"),
            "has_css": bool(self._css),
            "targets": targets,
            "server": {"running": self._server.running, "port": self._server.port},
            "fallback": self._detail.get("fallback", []),
            "zh": self._detail.get("zh"),
            "regular": self._detail.get("regular"),
            "medium": self._detail.get("medium"),
            "bold": self._detail.get("bold"),
            "cjk": self._detail.get("cjk"),
            "cjk_split": bool(state.get("cjk_split")),
            "synth_weight_none": bool(state.get("synth_weight_none", True)),
            "cjk_punct": bool(state.get("cjk_punct")),
            "strip": self._detail.get("strip") or {"done": False, "fallback": False},
            "full_backfill": bool(self._detail.get("full_backfill")),
            "uid": self._uid,
            "favorites": favorites,
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
