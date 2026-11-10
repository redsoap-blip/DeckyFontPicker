(function (deckyFrontendLib, React) {
    "use strict";

    var h = React.createElement;
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useMemo = React.useMemo;
    var useRef = React.useRef;

    var LIST_LIMIT = 60;

    // DFL.GamepadButton: CANCEL=2(B) / SECONDARY=3(X) / OPTIONS=4(Y)
    var BTN_X = 3;
    var BTN_Y = 4;

    // Decky 会把插件方法返回值包成 {success, result}，个别版本还会多包一层，这里统一剥壳
    function unwrap(res) {
        var value = res;
        var depth = 0;
        while (value && typeof value === "object" && !Array.isArray(value) &&
               value.success === true && Object.prototype.hasOwnProperty.call(value, "result") && depth < 3) {
            value = value.result;
            depth += 1;
        }
        return value;
    }

    // 在主名之外匹配别名，返回命中的那个别名（用于显示「Alias: xxx」）
    function aliasHit(font, q) {
        var aliases = font && Array.isArray(font.aliases) ? font.aliases : [];
        for (var i = 0; i < aliases.length; i++) {
            var alias = aliases[i];
            if (typeof alias === "string" && alias.toLowerCase().indexOf(q) !== -1) return alias;
        }
        return null;
    }

    function buttonOf(evt) {
        return evt && evt.detail ? evt.detail.button : null;
    }

    // Serif / Sans 只能按 family 名启发式判断
    // 判定顺序：Mono → Serif → Sans（明确无衬线关键词）→ 其他，四类互斥
    var SERIF_RE = /serif|song|ming|times|georgia|garamond|baskerville|bookman|palatino|cambria|constantia|mincho|roman|仿宋|宋体|宋|楷/;
    var SANS_RE = /sans|grotesk|grotesque|gothic|humanist|helvetica|arial|roboto|lato|inter|segoe|verdana|tahoma|ubuntu|黑/;
    function isSerif(font) {
        var name = String((font && font.family) || "").toLowerCase();
        if (!SERIF_RE.test(name)) return false;
        if (name.indexOf("sans") !== -1) return false; // "Sans Serif" 之类归 Sans
        return true;
    }

    function isSans(font) {
        var name = String((font && font.family) || "").toLowerCase();
        return SANS_RE.test(name);
    }

    // Mono：spacing 100/110 之外，名字里含 mono / 等宽 的也算（优先于 Serif/Sans/其他）
    function isMono(font) {
        if (font && font.mono) return true;
        var name = String((font && font.family) || "").toLowerCase();
        return name.indexOf("mono") !== -1 || name.indexOf("等宽") !== -1;
    }

    function noteRow(text, key) {
        return h(deckyFrontendLib.PanelSectionRow, { key: key },
            // 下边距把「说明」与第一个字体条目分开
            h("div", { style: { opacity: 0.55, fontSize: "11px", lineHeight: "1.5", marginBottom: "9px" } }, text));
    }

    // 「全部字体」标题与第一条字体之间的纯布局间距：不可聚焦、不参与键盘遍历
    function gapRow(key) {
        return h(deckyFrontendLib.PanelSectionRow, { key: key },
            h("div", { "aria-hidden": "true", style: { height: "9px" } }));
    }

    function aliasLine(alias) {
        return h("div", {
            style: { opacity: 0.55, fontSize: "11px", marginTop: "-4px", paddingLeft: "2px", wordBreak: "break-all" },
        }, "Alias: " + alias);
    }

    class ErrorBoundary extends React.Component {
        constructor(props) {
            super(props);
            this.state = { detail: null };
        }

        static getDerivedStateFromError(err) {
            return { detail: String((err && err.message) || err) };
        }

        render() {
            if (this.state.detail !== null) {
                return React.createElement("div", { style: { padding: "16px", fontSize: "13px", lineHeight: "1.6" } },
                    React.createElement("div", null, "Font Picker UI crashed but was caught; other panels are unaffected."),
                    React.createElement("div", { style: { opacity: 0.6, marginTop: "6px" } }, this.state.detail));
            }
            return this.props.children;
        }
    }

    function FontPickerContent(props) {
        var serverApi = props.serverApi;

        var fontsHook = useState([]);
        var fonts = fontsHook[0];
        var setFonts = fontsHook[1];

        var stateHook = useState(null);
        var state = stateHook[0];
        var setState = stateHook[1];

        var queryHook = useState("");
        var query = queryHook[0];
        var setQuery = queryHook[1];

        var busyHook = useState(false);
        var busy = busyHook[0];
        var setBusy = busyHook[1];

        var messageHook = useState("");
        var message = messageHook[0];
        var setMessage = messageHook[1];

        var collapsedHook = useState({});
        var collapsed = collapsedHook[0];
        var setCollapsed = collapsedHook[1];

        // 「当前字体」这一行的折叠状态单独记忆：不复用 collapsed，也不与任何分区共享；默认折叠
        var currentOpenHook = useState(false);
        var currentOpen = currentOpenHook[0];
        var setCurrentOpen = currentOpenHook[1];

        // 「加载更多」已加载条数：按 key（分区 id / search）记住；搜索词变化时重置
        var loadCountsHook = useState({});
        var loadCounts = loadCountsHook[0];
        var setLoadCounts = loadCountsHook[1];

        // 搜索词一变，已加载数量立即回到初始 60（渲染期同步调整，避免多一次空渲染）
        var lastQueryRef = useRef(query);
        if (lastQueryRef.current !== query) {
            lastQueryRef.current = query;
            setLoadCounts({});
            loadCounts = {};
        }

        function limitOf(key) {
            var n = loadCounts[key];
            return typeof n === "number" && n > LIST_LIMIT ? n : LIST_LIMIT;
        }

        function loadMore(key) {
            setLoadCounts(function (prev) {
                var counts = prev || {};
                var next = Object.assign({}, counts);
                var n = typeof counts[key] === "number" && counts[key] > LIST_LIMIT ? counts[key] : LIST_LIMIT;
                next[key] = n + LIST_LIMIT;
                return next;
            });
        }

        // 每个分区标题的 DOM 节点，用于「在分区内按 Y 收起」后把焦点送回标题
        var headerRefs = useRef({});

        function applyPayload(raw) {
            var payload = unwrap(raw);
            if (!payload || typeof payload !== "object") {
                setMessage("Backend returned no data");
                return;
            }
            setMessage(payload.error || payload.message || "");
            setState(payload);
        }

        function call(method, args) {
            return serverApi.callPluginMethod(method, args || {}).then(function (res) {
                var value = unwrap(res);
                if (res && res.success === false) {
                    setMessage((value && value.error) || "Failed to call " + method);
                    return null;
                }
                applyPayload(value);
                return value;
            }).catch(function (e) {
                setMessage("Failed to call " + method + ": " + e);
                return null;
            });
        }

        function refresh() {
            serverApi.callPluginMethod("get_fonts", {}).then(function (res) {
                var value = unwrap(res);
                if (Array.isArray(value)) setFonts(value);
                else if (value && Array.isArray(value.fonts)) setFonts(value.fonts);
                else setFonts([]);
            }).catch(function () { /* 忽略：下面 get_font_state 会提示 */ });
            call("get_font_state");
        }

        useEffect(function () {
            refresh();
        }, []);

        // QAM / MainMenu 的文档会被 Steam 重建，后端会自动补注入；这里跟着刷新状态
        var pendingCount = state && state.targets
            ? state.targets.filter(function (t) { return !t.ok; }).length
            : -1;

        useEffect(function () {
            if (pendingCount <= 0) return;
            var left = 20;
            var timer = setInterval(function () {
                left -= 1;
                call("get_font_state");
                if (left <= 0) clearInterval(timer);
            }, 2500);
            return function () { clearInterval(timer); };
        }, [pendingCount]);

        function choose(family) {
            if (busy) return;
            setBusy(true);
            call("set_font", { family: family }).then(function () { setBusy(false); });
        }

        function reset() {
            if (busy) return;
            setBusy(true);
            call("reset_font").then(function () { setBusy(false); });
        }

        function toggleFavorite(family) {
            if (!family) return;
            call("toggle_favorite", { family: family });
        }

        // 全部折叠是默认态：collapsed[id] 只有显式 false 才算展开
        function toggleSection(id) {
            setCollapsed(function (prev) {
                var next = {};
                for (var k in prev) { if (Object.prototype.hasOwnProperty.call(prev, k)) next[k] = prev[k]; }
                next[id] = prev[id] === false;
                return next;
            });
        }

        function focusDomNode(node) {
            if (!node) return;
            if (typeof node.scrollIntoView === "function") {
                try { node.scrollIntoView({ block: "center" }); } catch (e) { /* 老 CEF 不支持参数就忽略 */ }
            }
            if (typeof node.focus === "function") node.focus();
        }

        // 重试几次是因为 Steam 重新渲染 + 滚动容器归位需要一两帧
        function focusLater(getNode) {
            var delays = [0, 120, 320];
            for (var i = 0; i < delays.length; i++) {
                (function (delay) {
                    setTimeout(function () { focusDomNode(getNode()); }, delay);
                })(delays[i]);
            }
        }

        // 收起后把焦点送回标题：ref 可能不被 Focusable 转发，所以再按 id 兜底查 DOM
        function focusHeader(id) {
            focusLater(function () {
                var node = headerRefs.current[id];
                if (!node && typeof document !== "undefined") {
                    node = document.getElementById("fp-sec-" + id);
                }
                return node;
            });
        }

        // 在分区内就地收起：焦点回到该分区标题，避免焦点丢失或跳到别处
        function collapseSection(id) {
            setCollapsed(function (prev) {
                var next = {};
                for (var k in prev) { if (Object.prototype.hasOwnProperty.call(prev, k)) next[k] = prev[k]; }
                next[id] = true;
                return next;
            });
            focusHeader(id);
        }

        var current = state ? state.family : null;
        var favorites = state && Array.isArray(state.favorites) ? state.favorites : [];

        var currentFont = useMemo(function () {
            if (!current) return null;
            for (var i = 0; i < fonts.length; i++) {
                if (fonts[i].family === current) return fonts[i];
            }
            return null;
        }, [fonts, current]);

        var searchMode = query.trim().length > 0;

        var filtered = useMemo(function () {
            var q = query.trim().toLowerCase();
            var full = Array.isArray(fonts) ? fonts : [];
            var list = [];
            for (var i = 0; i < full.length; i++) {
                var font = full[i];
                if (!font || typeof font.family !== "string") continue;
                if (!q) {
                    list.push({ font: font, alias: null });
                    continue;
                }
                if (font.family.toLowerCase().indexOf(q) !== -1) {
                    list.push({ font: font, alias: null });
                    continue;
                }
                // 主名没命中时再匹配别名（如 "Fira Sans Light"、"文泉驛正黑"）
                var hit = aliasHit(font, q);
                if (hit !== null) list.push({ font: font, alias: hit });
            }
            return { total: list.length, items: list };
        }, [fonts, query]);

        var categories = useMemo(function () {
            var full = Array.isArray(fonts) ? fonts : [];
            var byName = {};
            for (var i = 0; i < full.length; i++) byName[full[i].family] = full[i];

            var fav = [];
            for (var f = 0; f < favorites.length; f++) {
                if (byName[favorites[f]]) fav.push(byName[favorites[f]]);
            }

            var mono = [];
            var serif = [];
            var sans = [];
            var other = [];
            var zhExtra = [];
            // 四个基础类的 zh 子类：同一判据 + lang 含 zh；
            // 基础类排除 zh 字体，两者互斥，全场合计仍等于原来的基础类数量
            var monoZh = [];
            var serifZh = [];
            var sansZh = [];
            var otherZh = [];
            for (var j = 0; j < full.length; j++) {
                var font = full[j];
                var monofl = isMono(font);
                var isSerifFont = isSerif(font);
                if (font.zh_extra) zhExtra.push(font);
                // 判定顺序 Mono → Serif → Sans → 其他，四类互斥；zh 字体只进对应（zh）子类
                if (monofl) { if (font.zh) monoZh.push(font); else mono.push(font); }
                else if (isSerifFont) { if (font.zh) serifZh.push(font); else serif.push(font); }
                else if (isSans(font)) { if (font.zh) sansZh.push(font); else sans.push(font); }
                else if (font.zh) otherZh.push(font);
                else other.push(font);
            }

            var cats = [
                {
                    id: "fav", name: "Favorites", items: fav, kind: "fav",
                    // 空态文案去掉，无论 0 款还是有收藏都固定显示这句操作提示
                    note: "Focus a font and press X (gamepad) to add or remove it from favorites",
                },
                {
                    id: "zh-extra", name: "CJK Coverage", items: zhExtra, kind: "zh-extra",
                    note: "Detected by charset coverage: not primarily a CJK font, but its charset covers at least 500 code points in the CJK Unified Ideographs ranges (U+3400–U+4DBF, U+4E00–U+9FFF). Fonts that cannot be measured are skipped; currently " + zhExtra.length + " fonts.",
                },
                {
                    id: "serif", name: "Serif", items: serif, kind: "serif",
                    note: "No CJK fonts here; see 'Serif (CJK)'. Heuristic match on the family name (Serif / Song / Ming / Times / Kai, etc.); names containing 'sans' are excluded.",
                },
                {
                    id: "serif-zh", name: "Serif (CJK)", items: serifZh, kind: "serif",
                    note: "CJK only (lang contains zh). Mutually exclusive with 'Serif'; together they cover every serif font.",
                },
                {
                    id: "sans", name: "Sans", items: sans, kind: "sans",
                    note: "No CJK fonts here; see 'Sans (CJK)'. Family name contains an explicit sans keyword (sans / grotesk / gothic / hei, etc.).",
                },
                {
                    id: "sans-zh", name: "Sans (CJK)", items: sansZh, kind: "sans",
                    note: "CJK only (lang contains zh). Mutually exclusive with 'Sans'; together they cover every sans-serif font.",
                },
                {
                    id: "mono", name: "Mono", items: mono, kind: "mono",
                    note: "No CJK fonts here; see 'Mono (CJK)'. Determined by fontconfig's spacing field (100 / 110) or by a family name containing a monospace keyword (mono / monospace / the localised word for 'monospace').",
                },
                {
                    id: "mono-zh", name: "Mono (CJK)", items: monoZh, kind: "mono",
                    note: "CJK only (lang contains zh). Mutually exclusive with 'Mono'; together they cover every monospace font.",
                },
                {
                    id: "other", name: "Others", items: other, kind: "other",
                    note: "No CJK fonts here; see 'Others (CJK)'. Neither monospace nor matched by a serif / sans keyword.",
                },
                {
                    id: "other-zh", name: "Others (CJK)", items: otherZh, kind: "other",
                    note: "CJK only (lang contains zh). Mutually exclusive with 'Others'; together they cover every remaining font.",
                },
                { id: "all", name: "All Fonts", items: full, kind: "all", note: null },
            ];

            // 0 款的分类整块隐藏（标题也不渲染）；收藏夹与「全部字体」永远保留
            return cats.filter(function (cat) {
                return cat.id === "fav" || cat.id === "all" || cat.items.length > 0;
            });
        }, [fonts, favorites]);

        // 异常时才出现的单行提示：字体准备失败 / 本地服务没起来 / 某一路注入失败
        var problemText = null;
        if (state && state.error) {
            problemText = String(state.error);
        } else if (state && state.server && !state.server.running) {
            problemText = "Local font service is not running; fonts cannot be applied";
        } else if (state && Array.isArray(state.targets)) {
            var failedLabels = state.targets.filter(function (t) { return !t.ok; })
                .map(function (t) { return t.label; });
            if (failedLabels.length) problemText = "UI injection incomplete: " + failedLabels.join(", ");
        }
        // 注入状态只保留「已注入 / 未注入」；uid 与目标文档名只留在日志里
        // 标签列固定宽度，保证三行的状态文字左边缘对齐
        var TARGET_LABEL_WIDTH = "90px";
        var targetRows = (state && Array.isArray(state.targets) ? state.targets : []).map(function (target) {
            return h(deckyFrontendLib.PanelSectionRow, { key: target.key },
                h("div", {
                    style: {
                        display: "flex",
                        alignItems: "center",
                        fontSize: "13px",
                        lineHeight: "1.4",
                    },
                },
                    h("span", {
                        style: {
                            display: "inline-block",
                            width: TARGET_LABEL_WIDTH,
                            flexShrink: 0,
                            color: "#c3c8cf",
                        },
                    }, target.label),
                    h("span", {
                        style: {
                            color: target.ok ? "rgba(255, 255, 255, 0.85)" : "#ff9b90",
                            fontWeight: 600,
                            fontSize: "12px",
                        },
                    }, target.ok ? "Injected" : "Not injected")
                )
            );
        });

        // 「当前字体」标题行：与分区标题同一套样式与聚焦实现（fp-sec + focusClassName），
        // 左侧 ▸/▾ + 标签，右侧字体名；默认折叠，折叠时不渲染说明行与注入状态三行
        var currentSection = h(deckyFrontendLib.PanelSection, { title: undefined },
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:header" },
                h(deckyFrontendLib.Focusable, {
                    id: "fp-sec-current",
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    onActivate: function () { setCurrentOpen(function (v) { return !v; }); },
                    onClick: function () { setCurrentOpen(function (v) { return !v; }); },
                    onOKActionDescription: currentOpen ? "Collapse" : "Expand",
                    style: {
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: "8px",
                        padding: "10px 12px",
                        borderRadius: "4px",
                        outline: "none",
                        fontSize: "14px",
                        fontWeight: 600,
                        transition: "background 80ms linear",
                    },
                },
                    h("span", { style: { display: "flex", alignItems: "center", flexShrink: 0 } },
                        h("span", {
                            className: "fp-arrow",
                            style: {
                                display: "inline-block",
                                width: "1em",
                                minWidth: 0,
                                minHeight: 0,
                                textAlign: "center",
                                flexShrink: 0,
                                padding: 0,
                                margin: 0,
                                background: "transparent",
                                border: 0,
                                boxShadow: "none",
                                borderRadius: 0,
                                fontVariantEmoji: "text",
                            },
                        }, currentOpen ? "▾" : "▸"),
                        h("span", { style: { marginLeft: "6px" } }, "Current font")
                    ),
                    // 字体名与标签同一行、垂直居中；过长用省略号截断，不换行；无字体时显示空态文案
                    h("span", {
                        className: "fp-current-name",
                        style: {
                            flex: "1 1 auto",
                            minWidth: 0,
                            textAlign: "right",
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            fontWeight: 400,
                            fontSize: "13px",
                        },
                    }, current || "None selected")
                )
            ),
            currentOpen
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:info" },
                    h("div", { style: { fontSize: "14px", lineHeight: "1.5" } },
                        current
                            ? h("div", { style: { opacity: 0.72 } },
                                (currentFont && currentFont.zh)
                                    ? "CJK glyphs included; safe for UI text"
                                    : "No CJK glyphs in this font; CJK text falls back to " + ((state && state.fallback && state.fallback[0]) || "the system default"))
                            : h("div", { style: { opacity: 0.72 } }, "UI font not overridden")
                    ))
                : null,
            currentOpen ? targetRows : null,
            // 异常提示不属于可折叠内容：任何折叠状态下都常显
            problemText
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:problem" },
                    h("div", {
                        style: {
                            color: "#ff9b90",
                            fontSize: "12px",
                            fontWeight: 600,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                        },
                    }, problemText))
                : null,
            message && message !== problemText
                ? h(deckyFrontendLib.PanelSectionRow, null,
                    h("div", { style: { color: "#ff8a80" } }, message))
                : null
        );

        function fontRow(font, alias, sectionId) {
            var isFav = favorites.indexOf(font.family) !== -1;
            return h(deckyFrontendLib.PanelSectionRow, { key: font.family },
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    disabled: busy,
                    description: (font.zh ? "CJK" : "No CJK, auto fallback") +
                        (font.has_bold ? " · Bold" : " · No bold (synthetic)") +
                        (font.mono ? " · Monospace" : "") +
                        (isFav ? " · Favorite" : "") +
                        (font.family === current ? " · In use" : ""),
                    onClick: function () { choose(font.family); },
                    onSecondaryActionDescription: isFav ? "Remove favorite" : "Add favorite",
                    onOptionsActionDescription: sectionId ? "Collapse section" : undefined,
                    onButtonDown: function (evt) {
                        var btn = buttonOf(evt);
                        if (btn === BTN_X) toggleFavorite(font.family);
                        // 在分区内就地收起，不必滚回分区标题
                        else if (btn === BTN_Y && sectionId) collapseSection(sectionId);
                    },
                }, font.family),
                alias ? aliasLine(alias) : null
            );
        }

        // 焦点视觉由 DOM 类驱动：Focusable 聚焦时由 Steam 加上 focusClassName，
        // 样式写在注入的 <style> 里，不用 React 状态判断（避免 blur 丢失导致状态卡住）
        function sectionHeader(cat) {
            var isCollapsed = collapsed[cat.id] !== false;
            return h(deckyFrontendLib.PanelSectionRow, { key: cat.id + ":header" },
                h(deckyFrontendLib.Focusable, {
                    id: "fp-sec-" + cat.id,
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    ref: function (el) { headerRefs.current[cat.id] = el; },
                    onActivate: function () { toggleSection(cat.id); },
                    onClick: function () { toggleSection(cat.id); },
                    onOKActionDescription: isCollapsed ? "Expand" : "Collapse",
                    style: {
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "space-between",
                        gap: "8px",
                        padding: "10px 12px",
                        borderRadius: "4px",
                        outline: "none",
                        fontSize: "14px",
                        fontWeight: 600,
                        transition: "background 80ms linear",
                    },
                },
                    h("span", null,
                        // 箭头必须是纯文字字形：▶/▼ 会命中 Noto Color Emoji 被画成"带底色的 emoji"，
                        // 换成没有 emoji 变体的 ▸/▾，并把方块相关的样式键全部压掉
                        h("span", {
                            className: "fp-arrow",
                            style: {
                                display: "inline-block",
                                width: "1em",
                                minWidth: 0,
                                minHeight: 0,
                                textAlign: "center",
                                flexShrink: 0,
                                padding: 0,
                                margin: 0,
                                background: "transparent",
                                border: 0,
                                boxShadow: "none",
                                borderRadius: 0,
                                fontVariantEmoji: "text",
                            },
                        }, isCollapsed ? "▸" : "▾"),
                        h("span", { style: { marginLeft: "6px" } }, cat.name)
                    ),
                    h("span", {
                        className: "fp-count",
                        style: {
                            fontSize: "12px",
                            fontWeight: 400,
                            textAlign: "right",
                            lineHeight: 1.35,
                        },
                    }, cat.items.length + " fonts")
                )
            );
        }

        // 原生 gpfocus 落不到自绘的标题 div 上，所以焦点视觉仍由 DOM 类驱动：
        // 基础态负责未聚焦，.fp-row-focus（Steam 加的类）与 :focus 兜底负责聚焦态
        var focusCss =
            ".fp-sec{background:rgba(255,255,255,0.06)!important;" +
            "border-left:3px solid transparent!important;color:#c3c8cf!important;" +
            "box-shadow:none!important;}" +
            ".fp-sec .fp-arrow{color:rgba(255,255,255,0.55)!important;background:transparent!important;" +
            "border:0!important;box-shadow:none!important;}" +
            ".fp-sec .fp-count{color:rgba(255,255,255,0.55)!important;}" +
            ".fp-sec.fp-row-focus,.fp-sec:focus{" +
            "background:rgba(255,255,255,0.16)!important;" +
            "border-left:3px solid #66c0f4!important;" +
            "box-shadow:inset 0 0 0 1px #66c0f4!important;" +
            "color:#ffffff!important;}" +
            ".fp-sec.fp-row-focus .fp-arrow,.fp-sec:focus .fp-arrow{color:#ffffff!important;}" +
            ".fp-sec.fp-row-focus .fp-count,.fp-sec:focus .fp-count{color:#e6f3fb!important;}";

        // 「加载更多」行：沿用标题行的样式与 focusClassName 聚焦方案，只负责加长列表
        function loadMoreRow(remaining, key, moreKey) {
            return h(deckyFrontendLib.PanelSectionRow, { key: key },
                h(deckyFrontendLib.Focusable, {
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    onActivate: function () { loadMore(moreKey); },
                    onClick: function () { loadMore(moreKey); },
                    onOKActionDescription: "Load more",
                    style: {
                        display: "flex",
                        alignItems: "center",
                        padding: "10px 12px",
                        borderRadius: "4px",
                        outline: "none",
                        fontSize: "13px",
                        fontWeight: 600,
                        transition: "background 80ms linear",
                    },
                }, "Load more (" + remaining + " left)")
            );
        }

        function categoryRows(cat) {
            var rows = [sectionHeader(cat)];
            if (collapsed[cat.id] !== false) return rows;
            if (cat.note) rows.push(noteRow(cat.note, cat.id + ":note"));
            // 「全部字体」没有说明行，补一条纯布局间距把标题与第一条字体分开；
            // 不可聚焦、不参与键盘遍历
            if (cat.id === "all" && cat.items.length) rows.push(gapRow("cat:all:gap"));
            if (cat.items.length) {
                var moreKey = "cat:" + cat.id;
                var limit = limitOf(moreKey);
                var shown = cat.items.slice(0, limit);
                for (var i = 0; i < shown.length; i++) rows.push(fontRow(shown[i], null, cat.id));
                if (cat.items.length > limit) {
                    rows.push(loadMoreRow(cat.items.length - limit, cat.id + ":more", moreKey));
                }
            }
            return rows;
        }

        var searchRows = [];
        if (searchMode) {
            var searchLimit = limitOf("search");
            var searchShown = filtered.items.slice(0, searchLimit);
            for (var si = 0; si < searchShown.length; si++) {
                searchRows.push(fontRow(searchShown[si].font, searchShown[si].alias));
            }
            if (filtered.items.length > searchLimit) {
                searchRows.push(loadMoreRow(filtered.items.length - searchLimit, "search:more", "search"));
            }
            if (!filtered.items.length) {
                searchRows.push(noteRow("No matching fonts (searched both names and aliases).", "no-result"));
            }
        }

        var countText = searchMode
            ? filtered.total + " fonts"
            : fonts.length + " fonts";

        // 不再渲染「选择字体」纯文本小标题：搜索框直接接在折叠行之后
        var pickerSection = h(deckyFrontendLib.PanelSection, { title: undefined },
            h(deckyFrontendLib.PanelSectionRow, null,
                h(deckyFrontendLib.TextField, {
                    label: "Search fonts",
                    value: query,
                    onChange: function (e) {
                        setQuery(e && e.target ? e.target.value : e);
                    },
                })
            ),
            h(deckyFrontendLib.PanelSectionRow, null,
                h("div", { style: { opacity: 0.6, fontSize: "12px" } },
                    countText + (searchMode ? " · clear the search to browse categories" : ""))
            ),
            searchMode ? searchRows : []
        );

        var categorySections = searchMode ? [] : categories.map(function (cat) {
            return h(deckyFrontendLib.PanelSection, { key: "cat:" + cat.id, title: undefined },
                categoryRows(cat));
        });

        // 收藏由字体行 X 负责、重新注入由字体行 A 负责，这里只留恢复默认
        var actionSection = h(deckyFrontendLib.PanelSection, { title: "Actions" },
            h(deckyFrontendLib.PanelSectionRow, null,
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    disabled: busy || !current,
                    onClick: reset,
                }, "Restore Steam default font")
            )
        );

        return h("div", null,
            h("style", { key: "fp-focus-css" }, focusCss),
            currentSection, pickerSection, categorySections, actionSection);
    }

    function FontIcon() {
        return h("svg", {
            width: "1em",
            height: "1em",
            viewBox: "0 0 24 24",
            fill: "currentColor",
            "aria-hidden": "true",
        },
            h("path", { d: "M2.5 19 L8.6 5 h2.3 L17 19 h-2.5 l-1.5-3.7 H6.4 L4.9 19 Z M7.2 13.2 h5 L9.7 7.6 Z" }),
            h("path", { d: "M18.2 15.2 h2.3 V19 h-2.3 Z" })
        );
    }

    var index = deckyFrontendLib.definePlugin(function (serverApi) {
        return {
            title: "Font Picker",
            titleView: h("div", { className: deckyFrontendLib.staticClasses.Title }, "Font Picker"),
            content: h(ErrorBoundary, null, h(FontPickerContent, { serverApi: serverApi })),
            icon: h(FontIcon, null),
        };
    });

    return index;
})(DFL, SP_REACT);
