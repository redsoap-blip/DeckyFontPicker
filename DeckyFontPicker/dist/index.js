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

    // 在主名之外匹配别名，返回命中的那个别名（用于显示「别名：xxx」）
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
        }, "别名：" + alias);
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
                    React.createElement("div", null, "字体选择器界面出错，已拦截，未影响其他面板。"),
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
                setMessage("后端返回空数据");
                return;
            }
            setMessage(payload.error || payload.message || "");
            setState(payload);
        }

        function call(method, args) {
            return serverApi.callPluginMethod(method, args || {}).then(function (res) {
                var value = unwrap(res);
                if (res && res.success === false) {
                    setMessage((value && value.error) || "调用 " + method + " 失败");
                    return null;
                }
                applyPayload(value);
                return value;
            }).catch(function (e) {
                setMessage("调用 " + method + " 失败: " + e);
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
            // 四个基础类下各分 zh / ja / ko 子类；归属优先级 zh > ja > ko，
            // 基础类只留三者都不含的字体，16 个分区互斥，合计仍等于全量
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
                // 基础类判定顺序 Mono → Serif → Sans → 其他，四类互斥
                var kind = monofl ? "mono" : isSerifFont ? "serif" : isSans(font) ? "sans" : "other";
                // 主语言：族名地区标记优先（JP→ja、KR→ko、SC/TC/HK→zh），
                // 没有标记再按 zh > ja > ko 的覆盖规则
                var lang = font.primary || (font.zh ? "zh" : font.ja ? "ja" : font.ko ? "ko" : "");
                var sub = lang ? "-" + lang : "";
                subs[kind + sub].push(font);
            }

            var BASE_NAME = { serif: "Serif 衬线", sans: "Sans 无衬线", mono: "Mono 等宽", other: "其他" };
            var BASE_NOTE = {
                serif: "只能按 family 名启发式判断（Serif / Song / Ming / Times / 楷 等），名字里含 sans 的排除。",
                sans: "名字里含明确无衬线关键词（sans / grotesk / gothic / 黑 等）。",
                mono: "按 fontconfig 的 spacing 字段判定（100 / 110），或 family 名里含 mono / 等宽。",
                other: "既非等宽、名字里也没有明确衬线 / 无衬线关键词的字体。",
            };
            var SUB_SUFFIX = { zh: "（zh）", ja: "（ja）", ko: "（ko）" };
            var SUB_NOTE = {
                zh: "按主语言归入：族名带 SC / TC / HK，或没有地区标记时 lang 含中文，与基础类及其它子类互斥。",
                ja: "按主语言归入：族名带 JP（如 Noto Sans CJK JP），或没有地区标记时 lang 只含日文，与基础类及其它子类互斥。",
                ko: "按主语言归入：族名带 KR，或没有地区标记时 lang 只含韩文，与基础类及其它子类互斥。",
            };
            function catOf(kind, sub) {
                return {
                    id: kind + (sub ? "-" + sub : ""),
                    name: BASE_NAME[kind] + (sub ? SUB_SUFFIX[sub] : ""),
                    items: subs[kind + (sub ? "-" + sub : "")],
                    kind: kind,
                    note: sub ? SUB_NOTE[sub] : "不含中文、日文、韩文，见各自子类。" + BASE_NOTE[kind],
                };
            }
            var cats = [
                {
                    id: "fav", name: "收藏夹", items: fav, kind: "fav",
                    // 空态文案去掉，无论 0 款还是有收藏都固定显示这句操作提示
                    note: "聚焦某款字体后按 X（手柄）加入/移出收藏",
                },
                {
                    id: "zh-extra", name: "含中文", items: zhExtra, kind: "zh-extra",
                    note: "按字符集覆盖判定：本身不是中文主字体，但 charset 与 CJK 统一表意文字（U+3400–U+4DBF、U+4E00–U+9FFF）的交集 ≥ 500 个码位。判不出的字体不列入，当前命中 " + zhExtra.length + " 款。",
                },
            ];
            // 顺序：基础类 → zh → ja → ko
            for (var c = 0; c < kinds.length; c++) {
                cats.push(catOf(kinds[c], ""));
                cats.push(catOf(kinds[c], "zh"));
                cats.push(catOf(kinds[c], "ja"));
                cats.push(catOf(kinds[c], "ko"));
            }
            cats.push({ id: "all", name: "全部字体", items: full, kind: "all", note: null });

            // 0 款的分类整块隐藏（标题也不渲染）；收藏夹与「全部字体」永远保留
            return cats.filter(function (cat) {
                return cat.id === "fav" || cat.id === "all" || cat.items.length > 0;
            });
        }, [fonts, favorites]);

        // 只在整体性故障时出现的单行提示；某一路未注入已由卡内摘要行表达，不再重复
        var problemText = null;
        if (state && state.error) {
            problemText = String(state.error);
        } else if (state && state.mode === "extract" && state.server && !state.server.running) {
            problemText = "本地字体服务未启动，字体无法生效";
        }
        // 当前投递模式：local（默认） / extract
        var mode = (state && state.mode) === "extract" ? "extract" : "local";
        var extractInfo = (state && state.modes && state.modes.extract) || null;
        var extractUsable = !!(extractInfo && extractInfo.available !== false);
        var extractReason = (extractInfo && extractInfo.reason) || "缺少 fontTools";
        // 描述行模式名固定列宽，按最长的四字模式名计算
        var modeNameW = "50px";
        var MODE_DESC = {
            local: "浏览器直接取系统里已安装的字体，不抽 face、不缓存（CSS local()）",
            extract: "取出该字体的真实 face 嵌入页面，.ttc 也能强制生效",
        };
        var switchDisabled = busy || (mode === "local" && !extractUsable);
        var switchOKDesc = mode === "local"
            ? (extractUsable ? "切换到抽取嵌入" : "抽取嵌入不可用")
            : "切换回按名引用";
        // 只能由用户主动点，探测结果不触发任何自动切换
        function doSwitchMode() {
            if (switchDisabled) {
                // 点了要给出原因，不能静默无反应
                if (mode === "local" && !extractUsable) setMessage("抽取嵌入不可用：" + extractReason);
                return;
            }
            switchMode(mode === "local" ? "extract" : "local");
        }

        // 「当前字体」收起时只看得到摘要行，所以命中 / 未注入 / 探测失败必须在这里也能分辨
        var summary = (function () {
            var ts = state && Array.isArray(state.targets) ? state.targets : null;
            if (!ts || !ts.length) return null;
            var bad = ts.filter(function (t) { return !t.ok; }).map(function (t) { return t.label; });
            if (bad.length) return { text: "未注入：" + bad.join("、"), color: "#ff9b90" };
            // 抽取模式下生效态用模式色（蓝），与 local 的绿形成对称关系
            if (mode === "extract") return { text: "已注入", color: "#8ab4f8" };
            var probes = ts.map(function (t) { return t.probe || null; });
            if (probes.some(function (p) { return p && p.status === "error"; })) {
                return { text: "探测失败（未生效）", color: "#f0c674" };
            }
            if (probes.some(function (p) { return p && p.status === "miss"; })) {
                return { text: "未命中（已回退系统字体）", color: "#ff9b90" };
            }
            if (probes.some(function (p) { return p && p.status === "hit_no_cjk"; })) {
                return { text: "已命中（无中文字形）", color: "#f0c674" };
            }
            if (probes.every(function (p) { return p && p.status === "hit"; })) {
                return { text: "已命中", color: "#7ee787" };
            }
            return { text: "检测中…", color: "rgba(255,255,255,0.55)" };
        })();

        // 注入状态：已注入 / 未注入 + local 模式下每个目标的命中探测结论
        // 标签列固定宽度，保证三行的状态文字左边缘对齐
        var TARGET_LABEL_WIDTH = "86px";
        var targetRows = (state && Array.isArray(state.targets) ? state.targets : []).map(function (target) {
            var probe = target.probe || null;
            var statusText;
            var statusColor;
            if (!target.ok) {
                statusText = "未注入";
                statusColor = "#ff9b90";
            } else if (mode === "extract") {
                statusText = "已注入";
                // 生效态颜色跟随注入模式：抽取嵌入蓝、按名引用绿
                statusColor = "#8ab4f8";
            } else if (!probe || probe.status === "pending") {
                statusText = "检测中…";
                statusColor = "rgba(255,255,255,0.55)";
            } else if (probe.status === "hit") {
                statusText = "已命中";
                statusColor = "#7ee787";
            } else if (probe.status === "hit_no_cjk") {
                statusText = "已命中（无中文字形）";
                statusColor = "#f0c674";
            } else if (probe.status === "miss") {
                statusText = "未命中（已回退系统字体）";
                statusColor = "#ff9b90";
            } else {
                // 探测失败 = 这种方式没有生效，和「未命中」同属未生效，但原因是探测本身
                statusText = "探测失败（未生效）";
                statusColor = "#f0c674";
            }
            // 正常生效时不补解释；只有异常情况才给原因 / 指引
            var detailText = null;
            if (!target.ok) {
                detailText = "该区域未注入，重新选择字体可触发注入";
            } else if (mode === "local" && probe && probe.status === "error") {
                detailText = "探测本身出错（超时 / JS 异常），与你的字体无关"
                    + (probe.error ? "：" + String(probe.error).slice(0, 60) : "")
                    + "；可在上方「注入方式」切到抽取嵌入";
            } else if (mode === "local" && probe && probe.status === "miss") {
                detailText = "与回退字体无差异，疑似未生效；可切到抽取嵌入";
            }
            return h(deckyFrontendLib.PanelSectionRow, { key: target.key },
                h("div", {
                    style: {
                        display: "flex",
                        alignItems: "center",
                        fontSize: "13px",
                        lineHeight: "1.4",
                        // 与上方「注入方式」文本对齐：外层 8px + 内层 12px = 20px，
                        // 左右同边距，宽度与那一块一致
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

        // 一行模式说明：模式名列固定宽度、冒号对齐，说明文字在第二列悬挂缩进
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
                        // 生效那条用模式色、未生效统一灰，两者字重一致，避免像链接
                        fontWeight: 500,
                        color: active ? color : "rgba(255,255,255,0.45)",
                    },
                }, key === "extract" ? "抽取嵌入" : "按名引用"),
                h("span", {
                    style: {
                        color: active ? "rgba(255,255,255,0.86)" : "rgba(255,255,255,0.4)",
                    },
                }, "：" + text)
            );
        }

        // 字体说明改用 lang 覆盖：中文相关项优先；非中文只列前几项
        function isZhLangTag(tag) {
            return tag === "zh" || tag.indexOf("zh-") === 0 || tag === "yue";
        }
        // zh-cn → zh-CN，zh-hans → zh-Hans
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
            if (!font) return "lang 未知";
            var langs = Array.isArray(font.langs) ? font.langs : [];
            var total = typeof font.lang_count === "number" ? font.lang_count : langs.length;
            var usable = !!(font.zh || font.zh_extra);
            if (!langs.length) return "lang 未知";
            var zhLangs = langs.filter(isZhLangTag);
            if (zhLangs.length) {
                var shownZh = zhLangs.slice(0, 2).map(fmtLangTag).join(" · ");
                return "lang " + shownZh
                    + (zhLangs.length > 2 ? " · 等 " + zhLangs.length + " 种" : "");
            }
            var shown = langs.slice(0, 3).map(fmtLangTag).join(" · ");
            return "lang " + shown
                + (total > 3 ? " · 等 " + total + " 种" : "")
                + (usable ? " · 含中文字形" : "");
        }

        // 标题下的说明行：直接展示 lang 覆盖
        var langText = current ? langLine(currentFont) : "未覆盖界面字体";

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
                    onOKActionDescription: currentOpen ? "收起" : "展开",
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
                        h("span", { style: { marginLeft: "6px" } }, "当前字体")
                    ),
                    // 字体名 + 状态摘要同一列、右对齐；摘要行在收起状态也能看到三态
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
                        }, current || "未选择"),
                        summary
                            ? h("span", {
                                className: "fp-current-summary",
                                style: {
                                    maxWidth: "100%",
                                    // 不截断：目标多 / 名字长时整体换行，卡片高度自适应
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
                            // 水平居中；太长就换行，每一行都居中，不截断
                            textAlign: "center",
                            whiteSpace: "normal",
                            wordBreak: "break-word",
                        },
                    }, langText))
                : null,
            // 注入方式并回本卡：整行可聚焦可点击，点一下就在 local ⇄ face 抽取 间切换
            currentOpen
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:mode" },
                    // 外层只留焦点描边与卡片边缘的留白，本身不可聚焦
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
                            // 左右内边距保证模式名右对齐后仍有安全边距
                            padding: "9px 12px",
                            borderRadius: "8px",
                            outline: "none",
                            textAlign: "left",
                            cursor: switchDisabled ? "default" : "pointer",
                            transition: "background 80ms linear",
                        },
                    },
                        // 只留左侧标签：模式名已在下方两段描述里用同色标出，右侧不再重复
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
                            h("span", { style: { color: "#c3c8cf", flexShrink: 0 } }, "注入方式"),
                            h("span", {
                                "aria-hidden": "true",
                                className: "fp-mode-swap",
                                style: { fontSize: "12px", flexShrink: 0, lineHeight: "1.4" },
                            }, "⇄")
                        ),
                        // 两种模式的说明常显：当前那条更亮 + 模式名同色，另一条统一灰
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
                                : MODE_DESC.extract + "（当前不可用：" + extractReason + "）")
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
                    description: (font.zh ? "含中文" : "无中文，自动回退") +
                        (font.has_bold ? " · 有 Bold" : " · 无 Bold（合成加粗）") +
                        (font.mono ? " · 等宽" : "") +
                        (isFav ? " · ★已收藏" : "") +
                        (font.family === current ? " · 当前使用" : ""),
                    onClick: function () { choose(font.family); },
                    onSecondaryActionDescription: isFav ? "取消收藏" : "收藏",
                    onOptionsActionDescription: sectionId ? "收起本分类" : undefined,
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
                    onOKActionDescription: isCollapsed ? "展开" : "收起",
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
                    }, cat.items.length + " 款")
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
            // 「注入方式」整行本身可点：聚焦时描边，未聚焦时保持低调
            ".fp-mode-row:hover{background:rgba(255,255,255,0.05)!important;}" +
            ".fp-mode-row.fp-row-focus,.fp-mode-row:focus{" +
            "background:rgba(255,255,255,0.07)!important;" +
            "box-shadow:inset 0 0 0 1px #66c0f4!important;" +
            "border-radius:8px;}" +
            // 右侧 ⇄：平时很淡，聚焦 / hover 时用当前模式色点亮；只用 transform 放大，不撑行高
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
                    onOKActionDescription: "加载更多",
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
                }, "加载更多（还剩 " + remaining + " 款）")
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
                searchRows.push(noteRow("没有匹配的字体（主名和别名都查过了）。", "no-result"));
            }
        }

        var countText = searchMode
            ? "共 " + filtered.total + " 款字体"
            : "共 " + fonts.length + " 款字体";

        // 不再渲染「选择字体」纯文本小标题：搜索框直接接在折叠行之后
        var pickerSection = h(deckyFrontendLib.PanelSection, { title: undefined },
            h(deckyFrontendLib.PanelSectionRow, null,
                h(deckyFrontendLib.TextField, {
                    label: "搜索字体",
                    value: query,
                    onChange: function (e) {
                        setQuery(e && e.target ? e.target.value : e);
                    },
                })
            ),
            h(deckyFrontendLib.PanelSectionRow, null,
                h("div", { style: { opacity: 0.6, fontSize: "12px" } },
                    countText + (searchMode ? " · 清空搜索可回到分类浏览" : ""))
            ),
            searchMode ? searchRows : []
        );

        var categorySections = searchMode ? [] : categories.map(function (cat) {
            return h(deckyFrontendLib.PanelSection, { key: "cat:" + cat.id, title: undefined },
                categoryRows(cat));
        });

        // 收藏由字体行 X 负责、重新注入由字体行 A 负责；这里保留恢复默认
        var actionSection = h(deckyFrontendLib.PanelSection, { title: "操作" },
            h(deckyFrontendLib.PanelSectionRow, null,
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    disabled: busy || !current,
                    onClick: reset,
                }, "恢复 Steam 默认字体")
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
            title: "字体选择器",
            titleView: h("div", { className: deckyFrontendLib.staticClasses.Title }, "字体选择器"),
            content: h(ErrorBoundary, null, h(FontPickerContent, { serverApi: serverApi })),
            icon: h(FontIcon, null),
        };
    });

    return index;
})(DFL, SP_REACT);
