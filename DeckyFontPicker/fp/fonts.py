from __future__ import annotations

import os
import re
import time
from typing import TypedDict

from .config import (COLLECTION_EXT, DIRECT_EXT, EMOJI_FALLBACK, FC_FORMAT, PREFERRED_CJK,
                     LATIN_STRIP_MASK_VERSION, STEAM_UI_FONT, USABLE_FORMATS, _FAMILY_CACHE,
                     _FC_WEIGHTS)
from .util import _elapsed_ms, _run, log


def _strip_variant():
    """固定使用基础剔字掩码的缓存后缀。"""
    return f".latin{LATIN_STRIP_MASK_VERSION}"


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
        # face 文件 ctime（单位：秒），用于「最新安装」排序（family 取所有 face 的最小值）
        # ctime = inode 变更时间，反映字体文件实际落到本机的时间，不受包内旧 mtime 影响
        try:
            ctime = int(os.path.getctime(path))
        except OSError:
            ctime = 0
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
                "ctime": ctime,
                "index": index,
                "format": fmt,
                "zh": zh,
                "ja": ja,
                "ko": ko,
                # fontconfig 的 lang（"zh-cn|en|ja" 这种），前端直接展示覆盖范围
                "lang": parts[6].strip(),
                # fontconfig charset 原始区间串：D 预检用它免 fontTools 进程内解析（与
                # zh_extra 判定同源同列），缺失时（len(parts)<=8）为空串，预检走 fontTools 兜底
                "charset": charset,
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
        # 安装时间取所有 face 文件 ctime 的最小值（最早落盘的那个文件，单位秒）；
        # 只有全部 face 都在系统字体目录下才算系统字体
        ctime = face.get("ctime") or 0
        if ctime and (entry["install_mtime"] is None or ctime < entry["install_mtime"]):
            entry["install_mtime"] = ctime
        if not _is_system_face_path(face["file"]):
            entry["is_system"] = False
    return families


# ── fc-list 结果缓存 ──
# fc-list 子进程约 100–300ms，而 _family_map 被 set_font / 面板刷新 / 注入流程高频调用；
# TTL 内直接复用上次结果，装/删系统字体后最多 FAMILY_TTL 秒内可见，面板重开不受影响。
# 模式与 _TOKEN_CACHE / _FT_CACHE 一致：双检 + 锁，避免并发线程重复跑 fc-list。
FAMILY_TTL = 10.0


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


class FaceDict(TypedDict, total=False):
    """_faces() 构造的单个 face（_family_map_build 原样收进 entry["faces"]）。"""

    family: str  # face 自己的派生族名（如 "Fira Sans Light"），映射键
    aliases: list[str]  # 全部族名（含主名），仅用于搜索/显示，不参与选面
    style: str  # fontconfig 的样式名（Regular / Bold / Italic …）
    weight: int  # _css_weight 折算的 CSS 字重（400/500/700 …）
    file: str  # 字体文件绝对路径
    ctime: int  # 文件 ctime（inode 变更时间，即实际落盘时间，秒），「最新安装」排序用，取不到为 0
    index: int  # 集合字体里的 face 序号，单文件为 0
    format: str  # fontconfig 报的格式（TrueType / CFF …）
    zh: bool  # lang 含中文
    ja: bool  # lang 含日文
    ko: bool  # lang 含韩文
    lang: str  # fontconfig 的原始 lang 串（"zh-cn|en|ja" 这种）
    charset: str  # fontconfig charset 原始区间串（"20-7e a0-ff …"），D 预检免 fontTools 解析用
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
    install_mtime: int | None  # 所有 face ctime 的最小值（秒），还没有 face 时 None
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
        # install_mtime = 该 family 所有 face 文件 ctime 的最小值（最早落盘的文件，单位：秒）
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
