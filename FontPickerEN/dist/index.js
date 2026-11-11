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
            ? state.targets.filter(function (t) {
                if (!t.ok) return true;
                // local 模式下探测还没落定也要继续轮询
                return state.mode === "local" && (!t.probe || t.probe.status === "pending");
            }).length
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

        // 切换投递模式：只由用户点击触发，绝不自动切换
        function switchMode(next) {
            if (busy) return;
            setBusy(true);
            call("set_mode", { mode: next }).then(function () { setBusy(false); });
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

            var zhExtra = [];
            // Each base category has zh / ja / ko subcategories; priority zh > ja > ko,
            // base keeps fonts with none of the three, so the 16 buckets are exclusive
            var kinds = ["serif", "sans", "mono", "other"];
            var subs = {};
            for (var k = 0; k < kinds.length; k++) {
                subs[kinds[k]] = [];
                subs[kinds[k] + "-zh"] = [];
                subs[kinds[k] + "-ja"] = [];
                subs[kinds[k] + "-ko"] = [];
            }
            for (var j = 0; j < full.length; j++) {
                var font = full[j];
                var monofl = isMono(font);
                var isSerifFont = isSerif(font);
                if (font.zh_extra) zhExtra.push(font);
                // Base category: Mono → Serif → Sans → Others, mutually exclusive
                var kind = monofl ? "mono" : isSerifFont ? "serif" : isSans(font) ? "sans" : "other";
                // Primary language: family region token wins (JP→ja, KR→ko, SC/TC/HK→zh);
                // otherwise fall back to the zh > ja > ko coverage rule
                var lang = font.primary || (font.zh ? "zh" : font.ja ? "ja" : font.ko ? "ko" : "");
                var sub = lang ? "-" + lang : "";
                subs[kind + sub].push(font);
            }

            var BASE_NAME = { serif: "Serif", sans: "Sans", mono: "Mono", other: "Others" };
            var BASE_NOTE = {
                serif: "Heuristic match on the family name (Serif / Song / Ming / Times / Kai, etc.); names containing 'sans' are excluded.",
                sans: "Family name contains an explicit sans keyword (sans / grotesk / gothic / hei, etc.).",
                mono: "Determined by fontconfig's spacing field (100 / 110) or by a family name containing a monospace keyword (mono / monospace).",
                other: "Neither monospace nor matched by a serif / sans keyword.",
            };
            var SUB_SUFFIX = { zh: " (zh)", ja: " (ja)", ko: " (ko)" };
            var SUB_NOTE = {
                zh: "Grouped by primary language: family carries SC / TC / HK, or (without a region token) lang contains Chinese. Mutually exclusive with the base category and the other subcategories.",
                ja: "Grouped by primary language: family carries JP (e.g. Noto Sans CJK JP), or (without a region token) lang only contains Japanese. Mutually exclusive with the base category and the other subcategories.",
                ko: "Grouped by primary language: family carries KR, or (without a region token) lang only contains Korean. Mutually exclusive with the base category and the other subcategories.",
            };
            function catOf(kind, sub) {
                return {
                    id: kind + (sub ? "-" + sub : ""),
                    name: BASE_NAME[kind] + (sub ? SUB_SUFFIX[sub] : ""),
                    items: subs[kind + (sub ? "-" + sub : "")],
                    kind: kind,
                    note: sub ? SUB_NOTE[sub] : "No Chinese, Japanese, or Korean fonts here; see the per-language subcategories. " + BASE_NOTE[kind],
                };
            }
            var cats = [
                {
                    id: "fav", name: "Favorites", items: fav, kind: "fav",
                    // 空态文案去掉，无论 0 款还是有收藏都固定显示这句操作提示
                    note: "Focus a font and press X (gamepad) to add or remove it from favorites",
                },
                {
                    id: "zh-extra", name: "Chinese Coverage", items: zhExtra, kind: "zh-extra",
                    note: "Detected by charset coverage: not primarily a Chinese font, but its charset covers at least 500 code points in the CJK Unified Ideographs ranges (U+3400–U+4DBF, U+4E00–U+9FFF). Fonts that cannot be measured are skipped; currently " + zhExtra.length + " fonts.",
                },
            ];
            // Order: base category → zh → ja → ko
            for (var c = 0; c < kinds.length; c++) {
                cats.push(catOf(kinds[c], ""));
                cats.push(catOf(kinds[c], "zh"));
                cats.push(catOf(kinds[c], "ja"));
                cats.push(catOf(kinds[c], "ko"));
            }
            cats.push({ id: "all", name: "All Fonts", items: full, kind: "all", note: null });

            // 0 款的分类整块隐藏（标题也不渲染）；收藏夹与「全部字体」永远保留
            return cats.filter(function (cat) {
                return cat.id === "fav" || cat.id === "all" || cat.items.length > 0;
            });
        }, [fonts, favorites]);

        // Single-line notice only for global failures; per-target misses are already in the card summary
        var problemText = null;
        if (state && state.error) {
            problemText = String(state.error);
        } else if (state && state.mode === "extract" && state.server && !state.server.running) {
            problemText = "Local font service is not running; fonts cannot be applied";
        }
        // Current delivery mode: local (default) / extract
        var mode = (state && state.mode) === "extract" ? "extract" : "local";
        var extractInfo = (state && state.modes && state.modes.extract) || null;
        var extractUsable = !!(extractInfo && extractInfo.available !== false);
        var extractReason = (extractInfo && extractInfo.reason) || "fontTools missing";
        // Fixed name column, sized for the longest label ("Embedded")
        var modeNameW = "62px";
        var MODE_DESC = {
            local: "browser uses installed system fonts directly; no face extraction, no cache (CSS local())",
            extract: "extracts this font's real face and embeds it; forces .ttc to work too",
        };
        var switchDisabled = busy || (mode === "local" && !extractUsable);
        var switchOKDesc = mode === "local"
            ? (extractUsable ? "Switch to embedded mode" : "embedded mode unavailable")
            : "Switch back to by-name mode";
        // Only an explicit click switches modes; probe results never auto-switch
        function doSwitchMode() {
            if (switchDisabled) {
                // Never fail silently: say why the click did nothing
                if (mode === "local" && !extractUsable) setMessage("Embedded mode unavailable: " + extractReason);
                return;
            }
            switchMode(mode === "local" ? "extract" : "local");
        }

        // The collapsed summary row is all you see when Current Font is closed, so
        // hit / not injected / probe failed must be distinguishable right there
        var summary = (function () {
            var ts = state && Array.isArray(state.targets) ? state.targets : null;
            if (!ts || !ts.length) return null;
            var bad = ts.filter(function (t) { return !t.ok; }).map(function (t) { return t.label; });
            if (bad.length) return { text: "Not injected: " + bad.join(", "), color: "#ff9b90" };
            // Active state uses the mode color (blue) in extract mode, matching local's green
            if (mode === "extract") return { text: "Injected", color: "#8ab4f8" };
            var probes = ts.map(function (t) { return t.probe || null; });
            if (probes.some(function (p) { return p && p.status === "error"; })) {
                return { text: "probe failed (not in effect)", color: "#f0c674" };
            }
            if (probes.some(function (p) { return p && p.status === "miss"; })) {
                return { text: "missed (fell back to system font)", color: "#ff9b90" };
            }
            if (probes.some(function (p) { return p && p.status === "hit_no_cjk"; })) {
                return { text: "Applied (no CJK glyphs)", color: "#f0c674" };
            }
            if (probes.every(function (p) { return p && p.status === "hit"; })) {
                return { text: "Applied", color: "#7ee787" };
            }
            return { text: "probing…", color: "rgba(255,255,255,0.55)" };
        })();

        // Injection state: injected / not injected, plus the local() probe verdict per target
        // Fixed-width label column keeps the three status texts left-aligned
        var TARGET_LABEL_WIDTH = "86px";
        var targetRows = (state && Array.isArray(state.targets) ? state.targets : []).map(function (target) {
            var probe = target.probe || null;
            var statusText;
            var statusColor;
            if (!target.ok) {
                statusText = "Not injected";
                statusColor = "#ff9b90";
            } else if (mode === "extract") {
                statusText = "Injected";
                statusColor = "#8ab4f8";
            } else if (!probe || probe.status === "pending") {
                statusText = "probing…";
                statusColor = "rgba(255,255,255,0.55)";
            } else if (probe.status === "hit") {
                statusText = "Applied";
                statusColor = "#7ee787";
            } else if (probe.status === "hit_no_cjk") {
                statusText = "Applied (no CJK glyphs)";
                statusColor = "#f0c674";
            } else if (probe.status === "miss") {
                statusText = "missed (fell back to system font)";
                statusColor = "#ff9b90";
            } else {
                // Probe failure = this delivery method is not in effect, same bucket as a miss,
                // but the cause is the probe itself (timeout / JS error), not the font
                statusText = "probe failed (not in effect)";
                statusColor = "#f0c674";
            }
            // No extra explanation when everything works; only abnormal states get a reason / hint
            var detailText = null;
            if (!target.ok) {
                detailText = "this region is not injected; pick the font again to re-inject";
            } else if (mode === "local" && probe && probe.status === "error") {
                detailText = "the probe itself failed (timeout / JS error); your font is fine"
                    + (probe.error ? ": " + String(probe.error).slice(0, 60) : "")
                    + "; use \"Delivery\" above to switch to embedded mode";
            } else if (mode === "local" && probe && probe.status === "miss") {
                detailText = "identical to the fallback font, likely not in effect; try embedded mode";
            }
            return h(deckyFrontendLib.PanelSectionRow, { key: target.key },
                h("div", {
                    style: {
                        display: "flex",
                        alignItems: "center",
                        fontSize: "13px",
                        lineHeight: "1.4",
                        // Align with the "Delivery" text above: outer 8px + inner 12px = 20px,
                        // same inset on both sides so the block has the same width
                        padding: "0 20px",
                        boxSizing: "border-box",
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
                    h("span", { style: { display: "flex", flex: "1 1 auto", flexDirection: "column", minWidth: 0, gap: "1px" } },
                        h("span", {
                            style: { color: statusColor, fontWeight: 600, fontSize: "12px" },
                        }, statusText),
                        detailText
                            ? h("span", {
                                style: { color: "rgba(255,255,255,0.4)", fontSize: "10px", lineHeight: "1.45" },
                            }, detailText)
                            : null
                    )
                )
            );
        });

        // One mode description line: fixed-width name column, aligned colon, hanging indent for the text
        function modeDescLine(key, text) {
            var active = mode === key;
            var color = key === "extract" ? "#8ab4f8" : "#7ee787";
            return h("div", {
                key: "fp-mode-desc:" + key,
                style: {
                    display: "grid",
                    gridTemplateColumns: modeNameW + " 1fr",
                    columnGap: "2px",
                },
            },
                h("span", {
                    style: {
                        whiteSpace: "nowrap",
                        // Active keeps the mode color, inactive is plain grey; same weight so neither looks like a link
                        fontWeight: 500,
                        color: active ? color : "rgba(255,255,255,0.45)",
                    },
                }, key === "extract" ? "Embedded" : "By name"),
                h("span", {
                    style: {
                        color: active ? "rgba(255,255,255,0.86)" : "rgba(255,255,255,0.4)",
                    },
                }, ": " + text)
            );
        }

        // Font note now shows lang coverage: Chinese tags first; non-Chinese lists a few and says so
        function isZhLangTag(tag) {
            return tag === "zh" || tag.indexOf("zh-") === 0 || tag === "yue";
        }
        // zh-cn → zh-CN, zh-hans → zh-Hans
        function fmtLangTag(tag) {
            var parts = String(tag).toLowerCase().split("-");
            var out = parts[0];
            for (var i = 1; i < parts.length; i++) {
                var seg = parts[i];
                out += "-" + (seg.length === 2
                    ? seg.toUpperCase()
                    : seg.charAt(0).toUpperCase() + seg.slice(1));
            }
            return out;
        }
        function langLine(font) {
            if (!font) return "lang unknown";
            var langs = Array.isArray(font.langs) ? font.langs : [];
            var total = typeof font.lang_count === "number" ? font.lang_count : langs.length;
            var usable = !!(font.zh || font.zh_extra);
            if (!langs.length) return "lang unknown";
            var zhLangs = langs.filter(isZhLangTag);
            if (zhLangs.length) {
                var shownZh = zhLangs.slice(0, 2).map(fmtLangTag).join(" · ");
                return "lang " + shownZh
                    + (zhLangs.length > 2 ? " · " + zhLangs.length + " langs" : "");
            }
            var shown = langs.slice(0, 3).map(fmtLangTag).join(" · ");
            return "lang " + shown
                + (total > 3 ? " · " + total + " langs" : "")
                + (usable ? " · has Chinese glyphs" : "");
        }

        // Note line under the title: shows lang coverage directly
        var langText = current ? langLine(currentFont) : "UI font not overridden";

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
                    // Font name + status summary, right-aligned; the summary stays visible when collapsed
                    h("div", {
                        className: "fp-current-name",
                        style: {
                            flex: "1 1 auto",
                            minWidth: 0,
                            display: "flex",
                            flexDirection: "column",
                            alignItems: "flex-end",
                            lineHeight: "1.25",
                        },
                    },
                        h("span", {
                            style: {
                                maxWidth: "100%",
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                fontWeight: 400,
                                fontSize: "13px",
                            },
                        }, current || "None selected"),
                        summary
                            ? h("span", {
                                className: "fp-current-summary",
                                style: {
                                    maxWidth: "100%",
                                    // Never truncate: wrap instead and let the card grow
                                    whiteSpace: "normal",
                                    overflow: "visible",
                                    wordBreak: "break-word",
                                    textAlign: "right",
                                    fontSize: "11px",
                                    lineHeight: "1.4",
                                    fontWeight: 600,
                                    color: summary.color,
                                },
                            }, summary.text)
                            : null
                    )
                )
            ),
            currentOpen
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:info" },
                    h("div", {
                        title: langText,
                        style: {
                            fontSize: "11px",
                            lineHeight: "1.5",
                            opacity: 0.72,
                            // Centered; wraps when long and every line stays centered, never truncated
                            textAlign: "center",
                            whiteSpace: "normal",
                            wordBreak: "break-word",
                        },
                    }, langText))
                : null,
            // Delivery stays inside this card: the whole row is focusable/clickable and toggles local ⇄ extract
            currentOpen
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:mode" },
                    // The outer box only reserves spacing between the focus ring and the card edge
                    h("div", { style: { padding: "2px 8px" } },
                    h(deckyFrontendLib.Focusable, {
                        tabIndex: 0,
                        className: "fp-mode-row " + (mode === "extract" ? "fp-mode-extract" : "fp-mode-local"),
                        focusClassName: "fp-row-focus",
                        onActivate: doSwitchMode,
                        onClick: doSwitchMode,
                        onOKActionDescription: switchOKDesc,
                        style: {
                            display: "flex",
                            flexDirection: "column",
                            gap: "4px",
                            boxSizing: "border-box",
                            width: "100%",
                            // Side padding keeps a safe gap after the right-aligned mode name
                            padding: "9px 12px",
                            borderRadius: "8px",
                            outline: "none",
                            textAlign: "left",
                            cursor: switchDisabled ? "default" : "pointer",
                            transition: "background 80ms linear",
                        },
                    },
                        // Only the left label: the active mode is already highlighted below
                        h("div", {
                            style: {
                                display: "flex",
                                alignItems: "baseline",
                                justifyContent: "space-between",
                                gap: "8px",
                                fontSize: "13px",
                                lineHeight: "1.4",
                            },
                        },
                            h("span", { style: { color: "#c3c8cf", flexShrink: 0 } }, "Delivery"),
                            h("span", {
                                "aria-hidden": "true",
                                className: "fp-mode-swap",
                                style: { fontSize: "12px", flexShrink: 0, lineHeight: "1.4" },
                            }, "⇄")
                        ),
                        // Both descriptions always visible: active one brighter + colored name, other greyed
                        h("div", {
                            style: {
                                display: "flex",
                                flexDirection: "column",
                                gap: "2px",
                                fontSize: "11px",
                                lineHeight: "1.45",
                            },
                        },
                            modeDescLine("local", MODE_DESC.local),
                            modeDescLine("extract", extractUsable
                                ? MODE_DESC.extract
                                : MODE_DESC.extract + " (currently unavailable: " + extractReason + ")")
                        )
                    )))
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
                    description: (font.zh ? "Chinese" : "No Chinese, auto fallback") +
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
            ".fp-sec.fp-row-focus .fp-count,.fp-sec:focus .fp-count{color:#e6f3fb!important;}" +
            // The Delivery row itself is clickable: ring on focus, low-key otherwise
            ".fp-mode-row:hover{background:rgba(255,255,255,0.05)!important;}" +
            ".fp-mode-row.fp-row-focus,.fp-mode-row:focus{" +
            "background:rgba(255,255,255,0.07)!important;" +
            "box-shadow:inset 0 0 0 1px #66c0f4!important;" +
            "border-radius:8px;}" +
            // ⇄ stays faint by default, lights up in the active mode color on focus / hover.
            // Transform-only scaling keeps the row height unchanged.
            ".fp-mode-swap{display:inline-block;opacity:0.45;color:rgba(255,255,255,0.7);" +
            "transform-origin:center right;" +
            "transition:opacity 150ms ease,transform 150ms ease,color 150ms ease;}" +
            ".fp-mode-row:hover .fp-mode-swap,.fp-mode-row.fp-row-focus .fp-mode-swap," +
            ".fp-mode-row:focus .fp-mode-swap{" +
            "opacity:1;transform:translateX(-2px) scale(1.12);}" +
            ".fp-mode-local:hover .fp-mode-swap,.fp-mode-local.fp-row-focus .fp-mode-swap," +
            ".fp-mode-local:focus .fp-mode-swap{color:#7ee787!important;}" +
            ".fp-mode-extract:hover .fp-mode-swap,.fp-mode-extract.fp-row-focus .fp-mode-swap," +
            ".fp-mode-extract:focus .fp-mode-swap{color:#8ab4f8!important;}";

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

        // Favourites live on the X button, re-inject on the A button; keep reset here
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
