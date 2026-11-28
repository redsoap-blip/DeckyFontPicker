from __future__ import annotations

import json
import os
import time

from .config import MODE_EXTRACT, PREVIEW_CACHE_DEFAULT_MB, STATE_FILE, STATE_VERSION
from .util import log


# 主 face 缓存字节上限（MB）默认值：A1 字节预算 LRU（64-1024 MB 可调，不 bump _v）
FACE_CACHE_DEFAULT_MB = 256


def _state_path():
    return STATE_FILE


def write_state(data):
    os.makedirs(os.path.dirname(_state_path()) or ".", exist_ok=True)
    data["_v"] = STATE_VERSION  # 兜底：任何落盘都带版本号
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, _state_path())


def _migrate_state(data):
    """把 state.json 迁移到 STATE_VERSION。

    纯函数：只读传入 dict，返回 (新 dict, actions)；不读写磁盘、不触发注入。
    actions 里的 mode_from 供 Plugin._main 保留「重新注入一次」的副作用。
    """
    actions = {}
    if not isinstance(data, dict):
        return {}, actions
    out = dict(data)
    try:
        v = int(out.get("_v", 0))
    except (TypeError, ValueError):
        v = 0
    if v > STATE_VERSION:
        actions["future_version"] = v
        return out, actions
    if v < STATE_VERSION:
        if "cjk_punct" in out:
            out.pop("cjk_punct", None)
            actions["cjk_punct_removed"] = True
        if "preview_level" not in out and "preview" in out:
            out["preview_level"] = 0 if out.get("preview") is False else 2
            out["preview"] = bool(out["preview_level"])
            actions["preview_backfilled"] = True
        if "expand_level" in out:
            value = out["expand_level"]
            norm = _norm_expand(value, 0)
            if norm != value:
                out["expand_level"] = norm
                actions["expand_normalized"] = True
        if out.get("mode") != MODE_EXTRACT:
            actions["mode_from"] = out.get("mode")
            out["mode"] = MODE_EXTRACT
        out["_v"] = STATE_VERSION
    return out, actions


def _read_state_with_actions():
    try:
        with open(_state_path(), "r", encoding="utf-8") as fp:
            raw = json.load(fp)
    except Exception:
        raw = {}
    data, actions = _migrate_state(raw)
    if "future_version" in actions:
        log(f"state 版本 {actions['future_version']} 高于当前 {STATE_VERSION}，按原样保留")
    return data, actions


def read_state():
    data, _actions = _read_state_with_actions()
    return data


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


def _norm_face_cache_mb(value, default=FACE_CACHE_DEFAULT_MB):
    """主 face 缓存字节上限（MB）收敛到 64-1024：越界取边界（63→64、1025→1024），
    非数字/缺失回退 default。"""
    try:
        mb = int(value)
    except (TypeError, ValueError):
        return default
    return min(1024, max(64, mb))


def _face_cache_mb_of(state):
    """主 face 缓存字节上限（MB）。旧 state 无 face_cache_mb 键 = 默认 256，无感升级。"""
    return _norm_face_cache_mb(state.get("face_cache_mb"), FACE_CACHE_DEFAULT_MB)


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
