from __future__ import annotations

import hashlib
import os
import subprocess
import time

from .config import LOG_DIR, PLUGIN_DIR, VENDOR_DIR, _FT_CACHE


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
