"""字体选择器（纯 Decky 注入版，两种投递模式）

- 字体枚举：fc-list 按 face 读取（family/style/weight/file/index/format/lang）
- 两种投递模式，同一个插件里切换，默认 local：
  * local   —— @font-face + `src: local("<族名>")`，浏览器自己从系统字体里取；
               不抽 face、不写缓存、不起本地 HTTP 服务；注入后做三态命中探测。
  * extract —— @font-face + 本地 HTTP 服务（127.0.0.1 + 随机端口 + 随机 token），
               .ttc/.otc 用 fontTools 抽出实际用到的 face，缓存到插件自己的缓存目录。
- 模式只由用户点击切换，探测到未命中也不会静默改用另一种模式。
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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

# 注入模式
MODE_LOCAL = "local"
MODE_EXTRACT = "extract"
MODES = (MODE_LOCAL, MODE_EXTRACT)
# 内部 family 名：两种模式共用同一个名字，切换模式时旧的 @font-face 会被整体替换
INTERNAL_FAMILY = "DeckyFontPicker"

# 三个注入目标。
# 主界面标题是稳定的；QuickAccess / MainMenu 在 Steam 里永远带动态后缀
# （实测 QuickAccess_uid2 … QuickAccess_uid236，随 CEF 上下文创建自增），
# 所以这两个目标需要先发现当前 uid，再用 "QuickAccess_uid<uid>" 精确注入。
TARGETS = [
    ("main", "Home", ["Steam 大屏幕模式", "Steam Big Picture Mode", "Steam", "SP", "SharedJSContext"]),
    ("menu", "MainMenu", []),
    ("qam", "QuickAccess", []),
]
DYNAMIC_PREFIX = {"menu": "MainMenu_uid", "qam": "QuickAccess_uid"}
_LIVE = None  # 当前 Plugin 实例，供 Decky 以类为 self 回调时回落
UID_SCAN_MAX = 320
UID_SCAN_AHEAD = 60


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


def _pick_faces(entry):
    """挑 Regular(≈80) 与 Bold(≈200)；有中文字形的 face 优先，斜体靠后。"""
    faces = entry["faces"]
    zh_faces = [f for f in faces if f["zh"]]
    pool = zh_faces or faces

    def closest(target, minimum=None):
        candidates = [f for f in pool if minimum is None or f["weight"] >= minimum]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda f: (abs(f["weight"] - target),) + _face_score(f) + (f["collection"],),
        )

    regular = closest(400)
    bold = closest(700, minimum=600)
    if bold is not None and regular is not None and bold["file"] == regular["file"] and bold["index"] == regular["index"]:
        bold = None  # 同一个 face 不需要重复注册
    return regular, bold


def _fallback_families(families, family, has_zh):
    chain = []
    if not has_zh:
        for name in PREFERRED_CJK:
            if name in families and name != family:
                chain.append(name)
                break
        if not chain:
            for name, entry in families.items():
                if entry["zh"] and name != family:
                    chain.append(name)
                    break
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
        return woff2, "font/woff2"
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

def _decky_method(name, payload, timeout=15):
    base = f"http://{DECKY_HOST}:{DECKY_PORT}"
    try:
        with urlopen(f"{base}/auth/token", timeout=3) as res:
            token = res.read().decode("utf-8").strip()
        request = Request(
            f"{base}/methods/{name}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Decky-Auth": token},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except Exception as exc:
        return {"success": False, "result": str(exc)}


def _inject_css(tab, css):
    res = _decky_method("inject_css_into_tab", {"tab": tab, "style": css})
    if res.get("success") and res.get("result"):
        return str(res["result"])
    log(f"注入 {tab} 失败: {res.get('result')}")
    return None


def _remove_css(tab, css_id):
    res = _decky_method("remove_css_from_tab", {"tab": tab, "css_id": css_id})
    if not res.get("success"):
        log(f"移除 {tab}/{css_id} 失败: {res.get('result')}")
    return bool(res.get("success"))


def _tab_exists(tab):
    return bool(_decky_method("get_tab_id", {"name": tab}, timeout=8).get("success"))


def _eval_in_tab(tab, code):
    return _decky_method("execute_in_tab", {"tab": tab, "run_async": False, "code": code}, timeout=8)


def _style_exists(tab, css_id):
    res = _eval_in_tab(tab, f"document.getElementById({json.dumps(css_id)}) !== null")
    if not res.get("success"):
        return False
    inner = res.get("result")
    # legacy 路由会再包一层 {success, result}
    if isinstance(inner, dict):
        return bool(inner.get("success")) and inner.get("result") is True
    return inner is True


def _scan_uid(hint):
    """QuickAccess / MainMenu 的 tab 标题带自增 uid，扫出当前值。

    先试上次的 uid，再往上扫 60 个，最后回落到 1..UID_SCAN_MAX。
    """
    order = []
    if isinstance(hint, int) and hint > 0:
        order.append(hint)
        order.extend(range(hint + 1, min(hint + UID_SCAN_AHEAD, UID_SCAN_MAX) + 1))
    seen = set(order)
    for value in range(1, UID_SCAN_MAX + 1):
        if value not in seen:
            order.append(value)
    for value in order:
        if _tab_exists(f"QuickAccess_uid{value}"):
            return value
    return None


# --------------------------------------------------------------------------- #
# 模式前置条件：抽取模式在需要用 fontTools 时才强制校验
# --------------------------------------------------------------------------- #

_FT_CACHE = {"at": 0.0, "ok": False, "version": None}


def _fonttools_status(refresh=False):
    """检查系统 Python（与 face_extract.py 同一个解释器）能否 import fontTools。

    插件目录下带 vendor/ 的完整版也算有。结果缓存 60 秒，避免每次刷新面板都起进程。
    """
    now = time.time()
    if not refresh and now - _FT_CACHE["at"] < 60:
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


def _require_fonttools_if_needed(faces):
    """抽取模式下，只有选中集合字体（.ttc/.otc）才强制要求 fontTools。

    这个检查在 _prepare 里、拆卸旧样式之前执行，所以失败时旧 CSS 不会被拆掉。
    .ttf/.otf/.woff2 这类单文件不需要 fontTools，瘦版也能用。
    """
    need = [f for f in faces if f is not None and f.get("collection")]
    if not need:
        return
    ok, _version = _fonttools_status()
    if ok:
        return
    name = os.path.basename(need[0]["file"])
    raise RuntimeError(
        f"抽取嵌入需要 fontTools，当前 Python（{_system_python()}）里没有。\n"
        f"- 本次选的 {name} 是 .ttc/.otc 集合，必须先抽出单个 face\n"
        "- 换成带 fontTools 的完整版，或手动安装后重试：\n"
        f"  {_system_python()} -m pip install --user fonttools\n"
        "按名引用不受影响，可以先用按名引用。"
    )


# --------------------------------------------------------------------------- #
# 命中探测（仅 local 模式）：在被注入的 tab 里跑 document.fonts.load + canvas.measureText
# --------------------------------------------------------------------------- #

PROBE_KEY = "__deckyFontPickerProbe"
# 中文用轮廓差异大的字，拉丁用宽窄悬殊的字：每个字符都取宽 + 上下左右四缘做签名
PROBE_CJK_SAMPLE = ("直", "骨", "海")
PROBE_LATIN_SAMPLE = ("W", "g", "@", "%")
# 显式指定的回退族：不靠隐式 fallback，和它逐项比对才说明目标字体有没有真的画字
PROBE_FALLBACK = "sans-serif"
PROBE_ROUNDS = 8
PROBE_INTERVAL = 0.35


def _css_string(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _probe_js(family):
    """返回一段自包含 JS：用 FontFace 试 local()，再逐字符比对目标字体与回退字体的签名。

    单个包围盒高度太容易撞值（不同字体可能都是 55.0），所以改成多维签名：
      每个字符量 width / actualBoundingBoxAscent / Descent / Left / Right 五项。
      目标字体与显式回退族（sans-serif）逐项比对：
        有任何一项不同 → 说明这个字符确实由目标字体绘制
        逐项完全相同   → 说明这个字符仍走回退
    document.fonts.check 与 new FontFace('local(...)') 只作为辅助信号保留，
    它们成功不代表渲染时真的用了这个字体。
    结果写进 window[PROBE_KEY]，由 _probe_read 以 JSON 字符串取回。
    """
    key = json.dumps(PROBE_KEY)
    cjk = json.dumps(list(PROBE_CJK_SAMPLE), ensure_ascii=False)
    latin = json.dumps(list(PROBE_LATIN_SAMPLE), ensure_ascii=False)
    target = json.dumps(family, ensure_ascii=False)
    fallback = json.dumps(PROBE_FALLBACK)
    return (
        "(function(){var K=" + key + ",CJK=" + cjk + ",LAT=" + latin
        + ",T=" + target + ",FB=" + fallback + ";"
        "var st={phase:'pending',family:T,loaded:null,check:null};"
        "try{window[K]=st;"
        "var cv=document.createElement('canvas'),cx=cv.getContext('2d');"
        "function sig(f,ch){try{cx.font='64px '+f;var m=cx.measureText(ch);"
        "return [+m.width.toFixed(2),+(m.actualBoundingBoxAscent||0).toFixed(2),"
        "+(m.actualBoundingBoxDescent||0).toFixed(2),"
        "+(m.actualBoundingBoxLeft||0).toFixed(2),"
        "+(m.actualBoundingBoxRight||0).toFixed(2)];}catch(e){return null;}}"
        "function sigs(f){var o={},all=CJK.concat(LAT);"
        "for(var i=0;i<all.length;i++){o[all[i]]=sig(f,all[i]);}return o;}"
        "st.sigFallback=sigs(FB);"
        "st.sigMissing=sigs('\"__fp_missing_family__\"');"
        "var name='__fp_local_'+Math.random().toString(36).slice(2);"
        "var F=T.replace(/[\"\\\\]/g,'');"
        "function fin(n){st.loaded=n;st.phase='done';st.t=Date.now();}"
        "if(!window.FontFace){st.error='no-FontFace-API';fin(0);return true;}"
        "try{st.check=document.fonts.check('64px \"'+F+'\"',CJK.join(''));}catch(e){st.check=null;}"
        "var face;"
        "try{face=new FontFace(name,'local(\"'+F+'\")');}"
        "catch(e){st.error='bad-family: '+e;fin(0);return true;}"
        "face.load().then(function(f){"
        "try{document.fonts.add(f);}catch(e){}"
        "st.sigTarget=sigs('\"'+name+'\"');"
        "try{document.fonts.delete(f);}catch(e){}"
        "fin(1);})"
        ".catch(function(e){st.error=String((e&&e.message)||e);fin(0);});"
        "}catch(e){window[K]={phase:'done',family:T,loaded:0,error:String(e)};}"
        "return true;})()"
    )


def _probe_start(tab, family):
    res = _eval_in_tab(tab, _probe_js(family))
    inner = res.get("result")
    ok = bool(res.get("success")) and not (isinstance(inner, dict) and inner.get("success") is False)
    if not ok:
        log(f"探测脚本启动失败（{tab}）: {str(res)[:500]}")
    return ok


def _probe_read(tab):
    # Decky 的 execute_in_tab 只回传原始值，对象会被转成 null，
    # 所以这里必须让页面自己 JSON.stringify，回到 Python 再 parse。
    res = _eval_in_tab(tab, f"JSON.stringify(window[{json.dumps(PROBE_KEY)}] || null)")
    if not res.get("success"):
        log(f"探测读取失败（{tab}）: {str(res)[:400]}")
        return None
    inner = res.get("result")
    if isinstance(inner, dict) and "success" in inner and "result" in inner:
        if inner.get("success") is False:
            log(f"探测读取失败（{tab}）: {str(inner)[:400]}")
            return None
        inner = inner.get("result")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except ValueError:
            log(f"探测读取解析失败（{tab}）: {inner[:200]}")
            return None
    return inner if isinstance(inner, dict) else None


SIG_EPS = 0.15  # 签名逐项比对的容差：同一次渲染的浮点抖动远小于此


def _sig_gap(a, b):
    """两个签名数组的最大绝对差；任一侧缺失返回 None。"""
    if not isinstance(a, list) or not isinstance(b, list) or len(a) != len(b):
        return None
    gap = 0.0
    for x, y in zip(a, b):
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        gap = max(gap, abs(float(x) - float(y)))
    return gap


def _char_diffs(target, fallback, chars):
    """逐字符算目标字体与回退字体的签名差，缺失的字符直接跳过。"""
    diffs = {}
    if not isinstance(target, dict) or not isinstance(fallback, dict):
        return diffs
    for ch in chars:
        gap = _sig_gap(target.get(ch), fallback.get(ch))
        if gap is not None:
            diffs[ch] = round(gap, 2)
    return diffs


def _sig_text(sig, chars):
    """把签名压成一行日志：直[100.0/50.0/10.0/0.0/100.0]"""
    out = []
    if not isinstance(sig, dict):
        return "—"
    for ch in chars:
        arr = sig.get(ch)
        if not isinstance(arr, list):
            out.append(ch + "[—]")
            continue
        out.append(ch + "[" + "/".join(f"{float(x):.1f}" for x in arr if isinstance(x, (int, float))) + "]")
    return " ".join(out)


def _verdict(raw, has_cjk=False):
    """把页面里的原始探测数据翻译成面板能直接显示的状态。

    判定（签名逐项比对，不再看单个数值）：
      - 中文字符签名有任何一项不同 → 目标字体真的画了中文 → hit
      - 中文签名与回退完全一致：
          lang 声称含中文 → 仍走回退 → miss
          lang 不含中文、但拉丁字符签名不同 → 字体生效但缺中文字形 → hit_no_cjk
          连拉丁也完全一致 → 整体回退 → miss
    """
    if not isinstance(raw, dict):
        return {"status": "pending"}
    if raw.get("phase") != "done":
        return {"status": "pending"}
    if raw.get("status") == "error":
        return {"status": "error", "error": raw.get("error"), "loaded": 0}
    try:
        loaded = int(raw.get("loaded") or 0)
    except (TypeError, ValueError):
        loaded = 0
    target = raw.get("sigTarget") if isinstance(raw.get("sigTarget"), dict) else {}
    fallback = raw.get("sigFallback") if isinstance(raw.get("sigFallback"), dict) else {}
    missing = raw.get("sigMissing") if isinstance(raw.get("sigMissing"), dict) else {}
    cjk_diffs = _char_diffs(target, fallback, PROBE_CJK_SAMPLE) if loaded > 0 else {}
    latin_diffs = _char_diffs(target, fallback, PROBE_LATIN_SAMPLE) if loaded > 0 else {}
    cjk_changed = any(v > SIG_EPS for v in cjk_diffs.values())
    latin_changed = any(v > SIG_EPS for v in latin_diffs.values())
    # 回退族与「必然不存在的族」应当渲染一致，不一致说明回退选择本身不稳，仅用于调试
    fb_gap = None
    if fallback and missing:
        gaps = [g for g in (_sig_gap(fallback.get(ch), missing.get(ch)) for ch in fallback) if g is not None]
        fb_gap = round(max(gaps), 2) if gaps else None
    if loaded <= 0:
        status = "miss"
    elif cjk_changed:
        status = "hit"
    elif has_cjk:
        status = "miss"
    elif latin_changed:
        status = "hit_no_cjk"
    else:
        status = "miss"
    return {
        "status": status,
        "loaded": loaded,
        "check": raw.get("check"),
        "has_cjk": bool(has_cjk),
        "cjk_diff": round(max(cjk_diffs.values()), 2) if cjk_diffs else None,
        "latin_diff": round(max(latin_diffs.values()), 2) if latin_diffs else None,
        "fallback_gap": fb_gap,
        "chars": {"cjk": cjk_diffs, "latin": latin_diffs},
        "sig": {"target": target, "fallback": fallback},
        "error": raw.get("error"),
    }


_LANG_CACHE = {"at": 0.0, "map": None}


def _family_has_zh(family):
    """该 family 的 lang 是否声称含中文（60 秒缓存，避免每个目标都跑一次 fc-list）。"""
    now = time.time()
    if _LANG_CACHE["map"] is None or now - _LANG_CACHE["at"] > 60:
        try:
            _LANG_CACHE["map"] = _family_map()
        except Exception:
            _LANG_CACHE["map"] = {}
        _LANG_CACHE["at"] = now
    entry = (_LANG_CACHE["map"] or {}).get(family) or {}
    return bool(entry.get("zh"))


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


# --------------------------------------------------------------------------- #
# 插件
# --------------------------------------------------------------------------- #

class Plugin:
    def __init__(self):
        global _LIVE
        _LIVE = self
        self._server = FontServer()
        self._hit = {}  # key -> {"tab":..., "css_id":...}
        self._probe = {}  # key -> verdict（仅 local 模式）
        self._probe_tasks = {}  # key -> asyncio.Task
        self._mode = MODE_LOCAL
        self._css = None
        self._family = None
        self._detail = {}
        self._retry_task = None
        self._dyn_task = None
        self._uid = read_state().get("uid")
        self._next_scan_at = 0

    # ---- 生命周期 ----

    async def _main(self):
        plugin = _bind(self)
        state = read_state()
        family = state.get("family")
        mode = state.get("mode")
        self._mode = mode if mode in MODES else MODE_LOCAL
        log(f"启动（{self._mode} 模式），已保存字体: {family!r} self={type(self).__name__}")
        if not family:
            return
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            log(f"已保存字体 {family!r} 不在系统中，清空选择")
            state.pop("family", None)
            write_state(state)
            return
        result = await plugin._apply(family)
        if not result.get("ok"):
            log(f"启动时恢复字体失败: {result.get('error')}")

    async def _unload(self):
        plugin = _bind(self)
        await plugin._cancel_dyn()
        await plugin._stop_retry()
        await plugin._cancel_probes()
        await plugin._remove_all()
        plugin._server.stop()

    # ---- 前端方法 ----

    async def get_fonts(self):
        # Decky 会把插件方法的返回值原样包成 {success, result}，这里直接返回数组
        families = await asyncio.to_thread(_family_map)
        return list_families(families)

    async def get_font_state(self):
        return _bind(self)._payload()

    async def set_font(self, family):
        plugin = _bind(self)
        families = await asyncio.to_thread(_family_map)
        if family not in families:
            return {"ok": False, "error": f"系统里没有字体 {family}", **plugin._payload()}
        state = read_state()
        state["family"] = family
        write_state(state)
        return await plugin._apply(family)

    async def reset_font(self):
        plugin = _bind(self)
        await plugin._cancel_dyn()
        await plugin._stop_retry()
        await plugin._cancel_probes()
        removed = await plugin._remove_all()
        plugin._css = None
        plugin._family = None
        plugin._detail = {}
        state = read_state()
        state.pop("family", None)
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

    async def set_mode(self, mode):
        """切换投递模式。只由用户点击触发，失败时保持原模式、原样式不动。"""
        plugin = _bind(self)
        if mode not in MODES:
            return {"ok": False, "error": f"未知模式 {mode!r}", **plugin._payload()}
        if mode == MODE_EXTRACT:
            ok, _version = await asyncio.to_thread(_fonttools_status)
            if not ok:
                # 只是提示：单文件字体仍可用，集合字体在 _prepare 里会被拦下
                log("抽取模式：系统里没有 fontTools，集合字体将不可用")
        state = read_state()
        if not plugin._family:
            plugin._mode = mode
            state["mode"] = mode
            write_state(state)
            return plugin._payload()
        # 有选中字体时先应用，成功后才在 _apply 里落盘模式
        return await plugin._apply(plugin._family, mode)

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

    async def _apply(self, family, mode=None):
        """切换字体或投递模式。

        顺序固定为「先检查 / 先准备，最后才拆旧样式」：
        前置条件不满足（字体不存在、缺 fontTools、抽取或起服务失败）时直接返回错误，
        已经注入的 CSS 原样保留，不会出现「先 _remove_all() 再抛异常」导致样式被拆掉。
        """
        await self._stop_retry()
        await self._cancel_probes()
        mode = mode if mode in MODES else self._mode
        try:
            detail = await asyncio.to_thread(self._prepare, family, mode)
        except Exception as exc:
            log(f"准备字体 {family!r}（{mode}）失败: {exc}")
            payload = self._payload()
            payload.update({"ok": False, "error": str(exc)})
            return payload

        await self._remove_all()
        if mode == MODE_LOCAL:
            # local 用不到本地字体服务，切回来时顺手关掉
            self._server.stop()

        self._mode = mode
        self._css = detail["css"]
        self._family = family
        self._detail = detail
        state = read_state()
        state["mode"] = mode
        write_state(state)

        # 主界面同步注入（快），QAM/MainMenu 需要发现 uid，放后台，不阻塞切换
        await self._inject_main()
        self._ensure_loop()
        await self._cancel_dyn()
        self._dyn_task = asyncio.create_task(self._inject_dynamic_all())
        return self._payload()

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
        for key, _label, _candidates in TARGETS[1:]:
            if key in self._hit:
                continue
            await self._inject_dynamic(key)

    def _prepare(self, family, mode):
        """把「选中字体 + 模式」准备成一段 CSS。任何失败都直接抛异常，调用方尚未拆旧样式。"""
        families = _family_map()
        entry = families.get(family)
        if entry is None:
            raise RuntimeError(f"系统里没有字体 {family}")
        chain = _fallback_families(families, family, entry["zh"])
        if mode == MODE_LOCAL:
            return self._prepare_local(family, entry, chain)
        return self._prepare_extract(family, entry, chain)

    def _prepare_local(self, family, entry, chain):
        return {
            "css": self._build_css_local(family, chain),
            "local_family": family,
            "internal": INTERNAL_FAMILY,
            "fallback": chain,
            "zh": entry["zh"],
        }

    def _prepare_extract(self, family, entry, chain):
        regular, bold = _pick_faces(entry)
        # 集合字体才需要 fontTools；这一步在拆卸旧样式之前，失败时旧 CSS 不动
        _require_fonttools_if_needed([regular, bold])

        reg_path, reg_mime = _ensure_font_file(regular)
        reg_url = self._server.register(reg_path, reg_mime)
        bold_url = None
        bold_path = None
        if bold is not None:
            bold_path, bold_mime = _ensure_font_file(bold)
            bold_url = self._server.register(bold_path, bold_mime)

        css = self._build_css(reg_url, reg_mime, bold_url, bold_mime if bold else None, chain)
        return {
            "css": css,
            "regular": {
                "file": regular["file"],
                "index": regular["index"],
                "weight": regular["weight"],
                "extracted": regular["collection"],
                "mime": reg_mime,
                "bytes": os.path.getsize(reg_path),
            },
            "bold": (
                {
                    "file": bold["file"],
                    "index": bold["index"],
                    "weight": bold["weight"],
                    "extracted": bold["collection"],
                    "mime": bold_mime,
                    "bytes": os.path.getsize(bold_path),
                }
                if bold is not None
                else None
            ),
            "fallback": chain,
            "zh": entry["zh"],
        }

    @staticmethod
    def _build_css_local(family, chain):
        lines = [
            f'@font-face{{font-family:"{INTERNAL_FAMILY}";font-style:normal;font-weight:400;'
            f'font-display:block;src:local("{_css_string(family)}");}}'
        ]
        stack = [f'"{INTERNAL_FAMILY}"'] + [
            name if name in ("system-ui", "sans-serif", "serif", "monospace") else f'"{name}"'
            for name in chain
        ]
        lines.append(":root, html body, html body * { font-family: " + ", ".join(stack) + " !important; }")
        return "\n".join(lines)

    @staticmethod
    def _build_css(reg_url, reg_mime, bold_url, bold_mime, chain):
        fmt_of = {
            "font/woff2": "woff2",
            "font/woff": "woff",
            "font/otf": "opentype",
            "font/ttf": "truetype",
        }
        lines = [
            "@font-face{font-family:\"DeckyFontPicker\";font-style:normal;font-weight:400;"
            f"font-display:swap;src:url(\"{reg_url}\") format(\"{fmt_of.get(reg_mime, 'truetype')}\");}}"
        ]
        if bold_url:
            lines.append(
                "@font-face{font-family:\"DeckyFontPicker\";font-style:normal;font-weight:700;"
                f"font-display:swap;src:url(\"{bold_url}\") format(\"{fmt_of.get(bold_mime, 'truetype')}\");}}"
            )
        stack = ["\"DeckyFontPicker\""] + [
            name if name in ("system-ui", "sans-serif", "serif", "monospace") else f'"{name}"'
            for name in chain
        ]
        # 用 html body / html body * 而不是 body / body *：
        # 与 tvdeck 这类主题的 `html, body, body *` 同为 !important 时，
        # 靠更高的特异性胜出，不依赖样式注入先后顺序。
        lines.append(":root, html body, html body * { font-family: " + ", ".join(stack) + " !important; }")
        return "\n".join(lines)

    async def _inject_main(self):
        """主界面标题稳定，直接按候选注入。"""
        if not self._css or "main" in self._hit:
            return
        for tab in TARGETS[0][2]:
            css_id = await asyncio.to_thread(_inject_css, tab, self._css)
            if css_id:
                self._hit["main"] = {"tab": tab, "css_id": css_id}
                log(f"已注入 main -> {tab}")
                if self._mode == MODE_LOCAL:
                    self._start_probe("main", tab)
                return

    async def _inject_dynamic(self, key):
        """QAM / MainMenu：标题带 uid，先按已知 uid 试，不行再扫。"""
        if not self._css or key in self._hit:
            return False
        prefix = DYNAMIC_PREFIX[key]
        if self._uid:
            tab = f"{prefix}{self._uid}"
            css_id = await asyncio.to_thread(_inject_css, tab, self._css)
            if css_id:
                self._hit[key] = {"tab": tab, "css_id": css_id}
                log(f"已注入 {key} -> {tab}")
                if self._mode == MODE_LOCAL:
                    self._start_probe(key, tab)
                return True

        now = time.time()
        if now < self._next_scan_at:
            return False
        uid = await asyncio.to_thread(_scan_uid, self._uid)
        if not uid:
            self._next_scan_at = now + 60
            log("没扫到 QuickAccess/MainMenu 的 tab（Steam 可能不在大屏模式）")
            return False
        self._uid = uid
        self._next_scan_at = 0
        state = read_state()
        state["uid"] = uid
        write_state(state)
        tab = f"{prefix}{uid}"
        css_id = await asyncio.to_thread(_inject_css, tab, self._css)
        if css_id:
            self._hit[key] = {"tab": tab, "css_id": css_id}
            log(f"已注入 {key} -> {tab}（uid 发现 {uid}）")
            if self._mode == MODE_LOCAL:
                self._start_probe(key, tab)
            return True
        return False

    # ---- 命中探测（仅 local 模式） ----

    def _start_probe(self, key, tab):
        old = self._probe_tasks.pop(key, None)
        if old is not None and not old.done():
            old.cancel()
        self._probe[key] = {"status": "pending"}
        self._probe_tasks[key] = asyncio.create_task(self._probe_target(key, tab))

    async def _probe_target(self, key, tab):
        family = self._family
        try:
            has_cjk = await asyncio.to_thread(_family_has_zh, family)
            await asyncio.to_thread(_probe_start, tab, family)
            raw = None
            for _ in range(PROBE_ROUNDS):
                await asyncio.sleep(PROBE_INTERVAL)
                raw = await asyncio.to_thread(_probe_read, tab)
                if isinstance(raw, dict) and raw.get("phase") == "done":
                    break
            if self._family != family:
                return  # 期间切换了字体，旧结果作废
            if raw is None:
                verdict = {"status": "error", "error": "execute_in_tab 没有返回结果"}
            else:
                verdict = _verdict(raw, has_cjk)
            verdict["family"] = family
            self._probe[key] = verdict
            log(
                f"探测 {key} ({tab}) -> {verdict.get('status')} "
                f"loaded={verdict.get('loaded')} cjk_diff={verdict.get('cjk_diff')} "
                f"latin_diff={verdict.get('latin_diff')} has_cjk={verdict.get('has_cjk')}"
            )
            sig = verdict.get("sig") or {}
            if sig:
                chars = list(PROBE_CJK_SAMPLE) + list(PROBE_LATIN_SAMPLE)
                log(
                    f"探测签名 {key} 目标 {_sig_text(sig.get('target'), chars)}"
                    f" | 回退 {_sig_text(sig.get('fallback'), chars)}"
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"探测 {key} 失败: {exc}")
            if self._family == family:
                self._probe[key] = {"status": "error", "error": str(exc)}
        finally:
            if self._probe_tasks.get(key) is asyncio.current_task():
                self._probe_tasks.pop(key, None)

    async def _cancel_probes(self):
        tasks = list(self._probe_tasks.values())
        self._probe_tasks = {}
        for task in tasks:
            if not task.done():
                task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._probe = {}

    async def _remove_all(self):
        items = list(self._hit.items())
        self._hit = {}
        for _key, item in items:
            await asyncio.to_thread(_remove_css, item["tab"], item["css_id"])
        return len(items)

    async def _stop_retry(self):
        task = self._retry_task
        self._retry_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _ensure_loop(self):
        if self._retry_task is None or self._retry_task.done():
            self._retry_task = asyncio.create_task(self._reconcile_loop())

    async def _reconcile_loop(self):
        """QAM/MainMenu 的文档会被 Steam 重建，样式跟着丢；定期复核并补注入。"""
        try:
            while True:
                await asyncio.sleep(10)
                if not self._css:
                    continue
                try:
                    await self._reconcile_once()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log(f"复核异常: {exc}")
        except asyncio.CancelledError:
            raise

    async def _reconcile_once(self):
        for key, _label, _candidates in TARGETS:
            item = self._hit.get(key)
            if item is not None:
                alive = await asyncio.to_thread(_style_exists, item["tab"], item["css_id"])
                if alive:
                    # local 模式下探测没跑成的，借复核间隔再试一次（每 10s 最多一次）
                    if self._mode == MODE_LOCAL:
                        probe = self._probe.get(key) or {}
                        if probe.get("status") in ("pending", "error"):
                            self._start_probe(key, item["tab"])
                    continue
                self._hit.pop(key, None)
                self._probe.pop(key, None)
            if key == "main":
                await self._inject_main()
            else:
                await self._inject_dynamic(key)

    def _payload(self):
        targets = []
        for key, label, _candidates in TARGETS:
            item = self._hit.get(key)
            targets.append(
                {
                    "key": key,
                    "label": label,
                    "tab": item["tab"] if item else None,
                    "ok": item is not None,
                    "probe": self._probe.get(key) if self._mode == MODE_LOCAL else None,
                }
            )
        favorites = _clean_favorites(read_state())
        ft_ok, ft_version = _fonttools_status()
        return {
            "ok": True,
            "mode": self._mode,
            "internal": INTERNAL_FAMILY,
            "modes": {
                "local": {"available": True},
                "extract": {
                    "available": True,
                    "fonttools": ft_ok,
                    "fonttools_version": ft_version,
                    "reason": "" if ft_ok else "系统 Python 里没有 fontTools：.ttc/.otc 无法抽取，.ttf/.otf 仍可用",
                },
            },
            "family": self._family,
            "has_css": bool(self._css),
            "targets": targets,
            "server": {"running": self._server.running, "port": self._server.port},
            "fallback": self._detail.get("fallback", []),
            "zh": self._detail.get("zh"),
            "regular": self._detail.get("regular"),
            "bold": self._detail.get("bold"),
            "uid": self._uid,
            "favorites": favorites,
        }


def _bind(obj):
    """Decky 3.3.1 的生命周期回调可能把类本身当 self 传进来，统一回落到实例。"""
    global _LIVE
    if isinstance(obj, Plugin):
        return obj
    if _LIVE is None:
        _LIVE = Plugin()
    return _LIVE
