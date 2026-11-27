from __future__ import annotations

import asyncio
import os
import time

from .config import (DYNAMIC_PREFIX, FALLBACK_TABS, INTERNAL_FAMILY, INTERNAL_FAMILY_CJK,
                     INTERNAL_FAMILY_FULL, MODE_EXTRACT, STEAM_UI_FONT, TARGETS,
                     TOAST_TAB_PREFIX, TOAST_WS_BACKOFF, UI_MODE_DESKTOP, WINDOW_BACKOFF,
                     WINDOW_DELAY, WINDOW_TOTAL, _CEF_CACHE, _FT_CACHE, _LIVE, _PREVIEW_STATS)
from .util import (_KeyedError, _elapsed_ms, _recent_log, _require_fonttools_if_needed,
                   _fonttools_refresh, log)
from .fonts import _fallback_families, _family_map, _pick_faces, list_families
from .state import (_clean_favorites, _expand_level_of, _heal_stamp, _inject_status,
                    _norm_expand, _norm_level, _norm_preview_cache_mb, _preview_cache_mb_of,
                    _preview_level_of, _push_history, _read_state_with_actions, read_state,
                    write_state)
from .cache import (_NoSampleGlyphs, _ensure_font_file, _ensure_latin_file, _is_cache_file,
                    _prepare_preview_face, _prune_font_cache, _prune_preview_cache,
                    _protected_cache_files)
from .cef import (FontServer, _cef_fetch, _cef_port_hint, _cef_titles, _remove_css,
                  _replace_css, _scan_uid, _style_exists)



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
        state, actions = _read_state_with_actions()
        family = state.get("family")
        # 迁移：local() 已弃用。老 state 里存 local / 缺 mode / 存了无效值，一律纠正成
        # extract 并落盘；下面照常走一次 _apply，等于对当前字体重新注入一次，
        # 升级后的实例不会停在已不存在的模式上。
        if "mode_from" in actions:
            log(f"投递模式迁移：state 里的 mode={actions['mode_from']!r} → extract（local() 已弃用），重新注入一次")
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
            pairs = await asyncio.to_thread(_prepare_preview_face, family)
        except _NoSampleGlyphs:
            # 预期失败（如 Noto Music 等专用符号字体）：计入 fail 但注明非故障，
            # 前端按 error_key 直接永久回退，不进重试通道
            _stat(False, "预期失败：无样张字形")
            return {"ok": False, "error": f"字体 {family} 不含样张字符，已用回退字体显示",
                    "error_key": "err.noSampleGlyphs", "error_args": [family]}
        except Exception as exc:
            log(f"预览字体准备失败 {family!r}: {exc}")
            _stat(False, "prepare-exc")
            return {"ok": False, "error": str(exc)}
        if not pairs:
            _stat(False, "no-family")
            return {"ok": False, "error": f"系统里没有字体 {family}", "error_key": "err.noFamily", "error_args": [family]}
        pair400, pair700 = pairs
        path, mime = pair400
        try:
            url = plugin._server.register(path, mime)
        except Exception as exc:
            _stat(False, "register-exc")
            return {"ok": False, "error": str(exc)}
        # 纯增量：家族有独立 Bold face 时多注册一条 700 URL；既有字段名与语义不变。
        # bold 注册失败只降级（粗样张显示 400），不影响 400 已注册的常规样张。
        result = {"ok": True, "url": url, "mime": mime}
        if pair700:
            try:
                bold_path, bold_mime = pair700
                result["bold_url"] = plugin._server.register(bold_path, bold_mime)
                result["bold_mime"] = bold_mime
            except Exception as exc:
                log(f"预览 bold 注册失败 {family!r}: {exc}，粗样张降级为 400")
        _stat(True, "ok")
        return result

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
