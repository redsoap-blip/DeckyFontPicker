from __future__ import annotations

import bisect
import hashlib
import os
import time

from .config import (CACHE_DIR, CACHE_EXTS, CACHE_KEEP_FILES, COLLECTION_EXT, DIRECT_EXT,
LATIN_STRIP_RANGES_BASE, MIME_BY_EXT,
                     PREVIEW_CACHE_SUBDIR, PREVIEW_SAMPLE_CHARS, WOFF2_MAX_BYTES,
                     _PREVIEW_NO_GLYPH_FACES)
from .util import (_cache_key, _elapsed_ms, _helper, _human_size, _run, _system_python,
                   _touch, log)
from .fonts import _family_map, _parse_charset, _pick_faces, _strip_variant
from .state import _preview_cache_mb_of, read_state


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


def _charset_has_sample_glyphs(charset_spec, family):
    """纯区间判定：样张字符 ∪ 族名 是否命中 charset 里至少一个非空格码位。

    数据源 = fontconfig charset（fc-list 第 9 列，_faces 已存进 face），与
    fontTools 读 cmap 是同一覆盖事实的两种描述；纯集合运算微秒级，免去 50ms–2s
    的进程内字体解析（GIL 持有会挤占事件循环与字体 HTTP 服务，真机卡顿主犯）。
    返回 True=有样张字形 / False=无 / None=不可判定（charset 缺失或解析不出区间，
    此时预检降级 fontTools 路径）。区间排序合并后逐样张码位二分，微秒级。
    """
    if not charset_spec:
        return None
    try:
        ranges = _parse_charset(charset_spec)
    except Exception:
        return None
    if not ranges:
        return None
    merged = []
    for lo, hi in sorted(ranges):
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    starts = [lo for lo, _ in merged]
    for ch in set(PREVIEW_SAMPLE_CHARS + family):
        cp = ord(ch)
        if cp == 0x20:
            continue
        i = bisect.bisect_right(starts, cp) - 1
        if i >= 0 and cp <= merged[i][1]:
            return True
    return False


class _NoSampleGlyphs(Exception):
    """字体的 cmap 与「样张字符 + 族名」无交集：注定抽不出可用的样张子集。"""


def _face_has_sample_glyphs(face, family):
    """三级链判定 face 是否覆盖「样张字符 + 族名」中至少一个非空格码位。

    ① 优先用 face["charset"]（fontconfig charset 区间串，_faces 已存进 face）做
    纯区间判定——与 ② 读 cmap 是同一覆盖事实的两种描述，纯集合运算微秒级，免去
    50ms–2s 的 fontTools 进程内解析（持 GIL 挤占同进程事件循环与字体 HTTP 服务，
    真机 QAM/Home/MainMenu 打开卡顿主犯）；charset 缺失（旧 face dict / 裸 dict）
    或解析不出区间时返回 None（不可判定），降级到 ②。② 读原始字体文件的 cmap
    （fontTools lazy=True、仅 .ttc/.otc 传 fontNumber、close-in-finally），直接读
    原始文件而不是子集 ttf：无字形时连子集都不必抽，也能拦下 1.4.23 遗留的空字形
    woff2 缓存（检查先于缓存命中）。⚠️ 判据必须用「cmap/charset 与字符集的交集」，
    「cmap 去掉空格后为空」只对子集产物成立——原始字体（如 Noto Music）cmap 里
    另有一堆专用符号，与样张字符集照样无交集。③ ② 的读取异常一律视为有字形
    （fail-open，走原有链路），不阻塞正常字体。负缓存键 (file, index) 与语义不变：
    True=无字形 / False=有字形，fail-open 按 False（有字形）缓存；① 的判定结果
    同样入缓存。"""
    key = (face["file"], face["index"])
    cached = _PREVIEW_NO_GLYPH_FACES.get(key)
    if cached is not None:
        return not cached
    has = _charset_has_sample_glyphs(face.get("charset"), family)
    if has is None:
        charset = set(PREVIEW_SAMPLE_CHARS) | set(family)
        has = True
        try:
            from fontTools.ttLib import TTFont
            kwargs = {}
            # 仅集合字体（.ttc/.otc）需要 face 序号，单文件字体传了反而多余
            if os.path.splitext(face["file"])[1].lower() in COLLECTION_EXT:
                kwargs["fontNumber"] = face["index"]
            font = TTFont(face["file"], lazy=True, **kwargs)
            try:
                cmap = font.getBestCmap() or {}
                has = any(cp != 0x20 and chr(cp) in charset for cp in cmap)
            finally:
                font.close()
        except Exception:
            has = True
    _PREVIEW_NO_GLYPH_FACES[key] = not has
    return has


def _prepare_preview_face(family):
    """返回 ((path400, mime400), (path700, mime700) 或 None)：只含样张字符的极小子集。

    前端通过 get_preview_font RPC 按需调用，只有真正滚进视口的预览行才会触发；
    不像主注入那样抽整 face、也不会和缓存清理抢名额。子集失败时退回整 face
    （慢但能显示），字体不存在返回 None。
    常规样张用 regular face（400），粗体样张用 bold face（700）：粗体样张与
    Steam 实际渲染一致（注入即按 face 注册多字重 @font-face）。家族没有独立
    Bold face 时第二元为 None——粗样张按 CSS 合成禁用（synthesis:none）显示
    400，同样与 Steam 实际一致。
    """
    entry = _family_map().get(family)
    if entry is None:
        return None
    # 只取 [0]=regular 与 [2]=bold：UI 无 500 需求（600 请求按 CSS 规则匹配到 700 面）
    faces = _pick_faces(entry, prefer_zh=True)
    regular = faces[0]
    if regular is None:
        return None

    # 无样张字形检查放在 woff2 缓存命中之前：1.4.23 遗留的空字形 woff2
    # （CEF 判定不可解析、FontFace.load() 报网络错误的那批）也永远不会再被返回。
    if not _face_has_sample_glyphs(regular, family):
        log(f"预期失败：字体 {family!r} 不含样张字符（cmap 无样张码位），跳过子集与注册")
        raise _NoSampleGlyphs(family)

    def _subset_one(face):
        # 子集缓存键只取决于 face 文件+字符集，与注册 weight 无关；bold 是不同
        # face 文件、键天然不同，regular 既有键盐 #pv2 直接复用（旧缓存照常命中）。
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

    pair400 = _subset_one(regular)

    # Bold face 缺字形或子集失败：静默不带 700（防御，不 raise），粗样张走 400。
    pair700 = None
    bold = faces[2]
    if bold is not None:
        if _face_has_sample_glyphs(bold, family):
            try:
                pair700 = _subset_one(bold)
            except Exception as exc:
                log(f"预览 bold 子集失败（{os.path.basename(bold['file'])}#{bold['index']}）: {exc}，预览不带 700 字重")
        else:
            log(f"预期失败：字体 {family!r} 的 Bold face 不含样张字符，预览不带 700 字重")
    return pair400, pair700


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
