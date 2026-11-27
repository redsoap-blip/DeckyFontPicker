from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import (CEF_CACHE_TTL, CEF_PORTS, CEF_TAB_RE, CEF_TIMEOUT, DECKY_HOST, DECKY_PORT,
                     _CEF_CACHE, _CEF_LOCK, _OWNED, _TAB_LOCKS, _TAB_LOCKS_GUARD, _TOKEN_CACHE)
from .util import log



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


TOKEN_TTL = 25.0


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
