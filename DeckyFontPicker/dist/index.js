// @ts-check
(function (deckyFrontendLib, React) {
    "use strict";

    /**
     * 后端 payload 里单个字体族条目(与 main.py list_families 输出逐字段对齐)。
     * @typedef {Object} FontFamilyItem
     * @property {string} family
     * @property {string[]} aliases
     * @property {boolean} zh
     * @property {number[]} weights
     * @property {boolean} has_bold
     * @property {number} faces
     * @property {string[]} langs
     * @property {number} lang_count
     * @property {boolean} [mono]
     * @property {boolean} [ja]
     * @property {boolean} [ko]
     * @property {string} [primary]
     * @property {boolean} [zh_extra]
     * @property {number} [install_mtime]
     * @property {boolean} [is_system]
     */
    /**
     * 后端 _payload() 返回的面板状态(与 main.py Plugin._payload 返回 dict 逐字段对齐;
     * 条件键用可选标注)。错误路径另有 ok=false + error/error_key/error_args,
     * 正常路径恒有 ok=true,这里按正常路径标注、异常键用可选。
     * @typedef {Object} StatePayload
     * @property {boolean} ok
     * @property {string} inject_level
     * @property {string} inject_text
     * @property {string} heal_text
     * @property {string} mode
     * @property {string|null} family
     * @property {string|null} family_cjk
     * @property {boolean} has_css
     * @property {Array<{key: string, label: string, tab: (string|null), ok: boolean, fallback: boolean}>} targets
     * @property {{running: boolean, port: number|null}} server
     * @property {boolean} woff2_ok
     * @property {string[]} fallback
     * @property {boolean|null} zh
     * @property {Object|null} regular
     * @property {Object|null} medium
     * @property {Object|null} bold
     * @property {Object|null} cjk
     * @property {boolean} cjk_split
     * @property {boolean} synth_weight_none
     * @property {boolean} preview
     * @property {number} preview_level
     * @property {number} expand_level
     * @property {number} preview_cache_mb  预览子集缓存字节上限（MB），1-30，默认 8
     * @property {{done: boolean, fallback: boolean}} strip
     * @property {boolean} full_backfill
     * @property {string|null} uid
     * @property {string[]} favorites
     * @property {string[]} history
     * @property {{port: (number|null), tabs: (number|null), error: string}} cef
     */

    var h = React.createElement;
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useMemo = React.useMemo;
    var useRef = React.useRef;

    var LIST_LIMIT = 60;

    // 预览样张的 @font-face 内部族名。Steam CEF 在 pressure-vessel 沙箱里按 family
    // 名引用系统字体一律 fallback（实测与不存在的族名同宽），只有 @font-face 真绑定
    // 才生效。族名需按 family 稳定推导：行内样式先写好这个名字，get_preview_font 拿到
    // URL 后再注入 @font-face，字体到位浏览器会自动重排，无需 React 重渲染。
    function fpPreviewSlot(family) {
        var s = String(family);
        var hash = 5381;
        for (var i = 0; i < s.length; i++) hash = ((hash << 5) + hash + s.charCodeAt(i)) >>> 0;
        return "FpPrev" + hash.toString(36);
    }
    // mime→format 映射:唯一来源在后端 main.py _build_css 内 fmt_of;新增格式两处同改。
    var FP_PREVIEW_MIME_FORMAT = {
        "font/woff2": "woff2",
        "font/woff": "woff",
        "font/otf": "opentype",
        "font/ttf": "truetype",
    };

    // 样张文本单一来源(键 exEn/exZh/exJa/exKo = 展开块短样张;prevEn/prevZh = 行内长样张)。
    // 改任何一条必须 bump 后端缓存键 #pv2(main.py _prepare_preview_face 的 sha1 盐)
    // 并同步 PREVIEW_SAMPLE_CHARS(main.py:164 附近)字符表。
    var SAMPLES = {
        exEn: "The quick brown fox 0123",
        exZh: "永和九年 岁在癸丑 汉字预览",
        exJa: "いろはにほへと 漢字カタカナ",
        exKo: "다람쥐 헌 쳇바퀴 한글",
        prevEn: "The quick brown fox jumps over 0123",
        prevZh: "永和九年 岁在癸丑 汉字预览"
    };

    // DFL.GamepadButton: CANCEL=2(B) / SECONDARY=3(X) / OPTIONS=4(Y)
    var BTN_B = 2;
    var BTN_X = 3;
    var BTN_Y = 4;
    // DFL.GamepadButton: DIR_LEFT=11 / DIR_RIGHT=12
    var BTN_LEFT = 11;
    var BTN_RIGHT = 12;

    // ===== 运行时文案：自建字典，不接 Decky 官方 i18n、不引入构建链 =====
    // 语言只在脚本加载时读一次；Steam 切换语言会重载界面，插件重新加载后自然取到新值。
    // 这里没有任何定时器或轮询。
    function detectLang() {
        var raw = "";
        try {
            var lm = typeof window !== "undefined" ? window.LocalizationManager : null;
            var list = lm && lm.m_rgLocalesToUse;
            if (list && list.length) raw = list[0];
        } catch (e) {
            raw = "";
        }
        if (!raw && typeof navigator !== "undefined" && navigator.language) raw = navigator.language;
        return String(raw || "");
    }

    // schinese / tchinese / zh* → 中文，japanese / ja* → 日文，koreana / ko* → 韩文，其余英文
    function pickLang(raw) {
        var tag = String(raw || "").toLowerCase().replace(/_/g, "-");
        if (tag === "schinese" || tag === "tchinese" || tag.indexOf("zh") === 0) return "zh";
        if (tag === "japanese" || tag.indexOf("ja") === 0) return "ja";
        if (tag === "koreana" || tag.indexOf("ko") === 0) return "ko";
        return "en";
    }

    var DICTS = {
        zh: {
            "pluginTitle": "字体选择器",
            "alias": "别名：{0}",
            "crash": "字体选择器界面出错，已拦截，未影响其他面板。",
            "noData": "后端返回空数据",
            "callFailed": "调用 {0} 失败",
            "callFailedDetail": "调用 {0} 失败：{1}",
            "reading": "正在读取…",
            "copyFail": "复制失败：",
            "logReadFail": "读取日志失败",
            "copied": "已复制 {0} 行（{1}）",
            "latestLog": "最新日志",
            "clipboardDenied": "剪贴板不可用（写入被拒绝）",
            "cat.serif": "Serif 衬线",
            "cat.sans": "Sans 无衬线",
            "cat.mono": "Mono 等宽",
            "cat.other": "其他",
            "cat.fav": "收藏夹",
            "cat.history": "历史字体",
            "cat.all": "全部字体",
            "lang.latin": "西文",
            "lang.zh": "中文",
            "lang.ja": "日文",
            "lang.ko": "韩文",
            "lang.empty": "该分类下没有{0}字体。",
            "note.serif": "只能按 family 名启发式判断（Serif / Song / Ming / Times / 楷 等），名字里含 sans 的排除。",
            "note.sans": "名字里含明确无衬线关键词（sans / grotesk / gothic / 黑 等）。",
            "note.mono": "按 fontconfig 的 spacing 字段判定（90 / 100 / 110），或族名里含 mono / 等宽 / 等寬 / 等幅 / 고정폭 / fixed 等关键词。",
            "note.other": "既非等宽、名字里也没有明确衬线 / 无衬线关键词的字体。",
            "note.fav": "聚焦某款字体后按 X（手柄）加入/移出收藏",
            "note.history": "按最近使用时间排列，无上限，卸载的字体自动移除",
            "colon": "：",
            "injectDone": "手动注入 · {0}",
            "injectFailed": "注入失败",
            "injectNone": "尚未注入",
            "injectHeal": "自动注入 · {0}",
            "injectHealed": "自动注入 · {0}",
            "reapply": "重新注入当前字体",
            "notSelected": "未选择",
            "expand": "展开",
            "collapse": "收起",
            "fontChinese": "含中文",
            "fontNoChinese": "无中文，自动回退",
            "boldYes": " · 有 Bold",
            "boldNo": " · 无 Bold（合成加粗）",
            "monoTag": " · 等宽",
            "favTag": " · ★已收藏",
            "inUse": " · 当前使用",
            "unfavorite": "取消收藏",
            "favorite": "收藏",
            "collapseSection": "收起本分类",
            "fontsCount": " 款",
            "totalFonts": "共 {0} 款字体",
            "clearSearchHint": " · 清空搜索可回到分类浏览",
            "searchFonts": "搜索字体",
            "noResult": "没有匹配的字体（主名和别名都查过了）。",
            "loadMore": "加载更多",
            "loadMoreRemaining": "加载更多（还剩 {0} 款）",
            "restoreDefault": "恢复 Steam 默认字体",
            "err.noFamily": "系统里没有字体 {0}",
            "err.noSelection": "当前没有选择字体",
            "err.noSlots": "没有选择西文字体，中日韩槽也为空",
            "err.fonttoolsMissing": "抽取 {0} 需要 fontTools，当前 Python 没有安装",
            "err.fonttoolsSubset": "西文剔字需要 fontTools（{0}），可关闭「按语言注入」",
            "err.noSampleGlyphs": "字体 {0} 不含样张字符，已使用回退字体",
            "advanced": "高级",
            "cefNoPort": "CEF 调试口尚未就绪（Steam 启动或重载中），插件会在注入窗口内自动重试。",
            "cefPortInfo": "调试端口 {0} · {1} 个 tab",
            "woff2Fallback": "woff2 不可用，将注入 ttf（体积更大）",
            "previewCacheTitle": "预览缓存上限",
            "previewCacheNote": "预览子集的磁盘缓存上限（MB），默认 8MB，约可容纳 700 个以上字体预览。调大减少重复裁剪，调小更省磁盘。1–30MB。",
            "copyLog": "复制日志",
            "history.empty": "暂无使用记录",
            "clearHistory": "清除历史字体记录",
            "historyCleared": "已清除历史记录",
            "splitMode": "按语言注入",
            "splitNote": "开启后西文与中日韩分别选择字体",
            "subSynth": "禁用假加粗",
            "subSynthNote": "没有粗体字重的字体不再合成加粗，中粗文字按常规字重显示。",
            "previewSamples": "字体预览样张",
            "previewNote": "单行：紧凑的单行样张\n双行：常规与粗体两行样张\n低内存设备建议使用关闭或单行。",
            "levelOff": "关闭",
            "notchA": "单行",
            "notchB": "双行",
            "previewExpand": "聚焦展开预览",
            "previewExpandNote": "开启后聚焦字体行时展开四语言大样张（西 / 中 / 日 / 韩）。",
            "exLatin": "西",
            "exZh": "中",
            "exJa": "日",
            "exKo": "韩",
            "slotLatin": "西文",
            "slotCjk": "中日韩",
            "followSystem": "跟随系统",
            "stripWarn": "西文字体剔字失败，已回退原始字体，可能遮蔽中日韩槽位（换一款纯西文字体可解决）。",
            "menuLatin": "应用西文",
            "menuCjk": "应用中文",
            "menuCancel": "按 B 返回取消，不做任何更改",
            "descLatinStrip": "仅取其西文字形（自动剔除汉字）",
            "descCjkNone": "该字体不含中文字形"
        },
        en: {
            "pluginTitle": "Font Picker",
            "alias": "Alias: {0}",
            "crash": "Font Picker UI crashed but was caught; other panels are unaffected.",
            "noData": "Backend returned no data",
            "callFailed": "Failed to call {0}",
            "callFailedDetail": "Failed to call {0}: {1}",
            "reading": "Reading…",
            "copyFail": "Copy failed: ",
            "logReadFail": "Failed to read the log",
            "copied": "Copied {0} lines ({1})",
            "latestLog": "latest log",
            "clipboardDenied": "Clipboard unavailable (write was denied)",
            "cat.serif": "Serif",
            "cat.sans": "Sans",
            "cat.mono": "Mono",
            "cat.other": "Others",
            "cat.fav": "Favorites",
            "cat.history": "History",
            "cat.all": "All Fonts",
            "lang.latin": "Latin",
            "lang.zh": "Chinese",
            "lang.ja": "Japanese",
            "lang.ko": "Korean",
            "lang.empty": "No {0} fonts in this category.",
            "note.serif": "Heuristic match on the family name (Serif / Song / Ming / Times / Kai, etc.); names containing 'sans' are excluded.",
            "note.sans": "Family name contains an explicit sans keyword (sans / grotesk / gothic / 黑, etc.).",
            "note.mono": "Determined by fontconfig's spacing field (90 / 100 / 110), or by a family name containing keywords such as mono, 等宽, 等寬, 等幅, 고정폭 or fixed.",
            "note.other": "Neither monospace nor matched by a serif / sans keyword.",
            "note.fav": "Focus a font and press X (gamepad) to add or remove it from favorites",
            "note.history": "Ordered by most recent use, unlimited; uninstalled fonts are removed automatically",
            "colon": ": ",
            "injectDone": "Manual injection · {0}",
            "injectFailed": "Injection failed",
            "injectNone": "Not injected yet",
            "injectHeal": "Auto injection · {0}",
            "injectHealed": "Auto injection · {0}",
            "reapply": "Re-inject the current font",
            "notSelected": "None selected",
            "expand": "Expand",
            "collapse": "Collapse",
            "fontChinese": "Chinese",
            "fontNoChinese": "No Chinese, auto fallback",
            "boldYes": " · Bold",
            "boldNo": " · No bold (synthetic)",
            "monoTag": " · Monospace",
            "favTag": " · Favorite",
            "inUse": " · In use",
            "unfavorite": "Remove favorite",
            "favorite": "Add favorite",
            "collapseSection": "Collapse section",
            "fontsCount": " fonts",
            "totalFonts": "{0} fonts",
            "clearSearchHint": " · clear the search to browse categories",
            "searchFonts": "Search fonts",
            "noResult": "No matching fonts (searched both names and aliases).",
            "loadMore": "Load more",
            "loadMoreRemaining": "Load more ({0} left)",
            "restoreDefault": "Restore Steam default font",
            "err.noFamily": "Font not found on system: {0}",
            "err.noSelection": "No font selected",
            "err.noSlots": "No Latin or CJK font selected",
            "err.fonttoolsMissing": "Extracting {0} requires fontTools, which is not installed",
            "err.fonttoolsSubset": "Latin-slot stripping requires fontTools ({0}); you can turn off per-language injection",
            "err.noSampleGlyphs": "Font {0} has no sample glyphs; using fallback font.",
            "advanced": "Advanced",
            "cefNoPort": "CEF debug port not ready yet (Steam is starting or reloading). The plugin keeps retrying within its injection window.",
            "cefPortInfo": "Debug port {0} · {1} tabs",
            "woff2Fallback": "woff2 unavailable, injecting ttf (larger files)",
            "previewCacheTitle": "Preview cache limit",
            "previewCacheNote": "Disk cache limit for font preview subsets (MB). Default 8MB holds roughly 700+ previews. Larger = fewer re-subsets; smaller = less disk. 1-30MB.",
            "copyLog": "Copy log",
            "history.empty": "No history yet",
            "clearHistory": "Clear font history",
            "historyCleared": "History cleared",
            "splitMode": "Split by language",
            "splitNote": "When on, Latin and CJK fonts are picked separately",
            "subSynth": "Disable synthetic bold",
            "subSynthNote": "Fonts without a bold face are no longer emboldened synthetically; medium-weight text uses the regular weight.",
            "previewSamples": "Font preview samples",
            "previewNote": "Compact: single-line sample\nDetailed: regular and bold samples\nLow-memory devices should use Off or Compact.",
            "levelOff": "Off",
            "notchA": "Compact",
            "notchB": "Detailed",
            "previewExpand": "Focus-expand preview",
            "previewExpandNote": "When enabled, focusing a font row expands the four-language sample (EN / ZH / JA / KO).",
            "exLatin": "EN",
            "exZh": "ZH",
            "exJa": "JA",
            "exKo": "KO",
            "slotLatin": "Latin",
            "slotCjk": "CJK",
            "followSystem": "Follow system",
            "stripWarn": "Stripping failed for the Latin font, so the original font was used; it may cover the CJK slot (picking a Latin-only font fixes this).",
            "menuLatin": "Apply to Latin",
            "menuCjk": "Apply to Chinese",
            "menuCancel": "Press B to go back and cancel without changing anything",
            "descLatinStrip": "Latin glyphs only (CJK characters stripped)",
            "descCjkNone": "This font has no Chinese glyphs"
        },
        ja: {
            "pluginTitle": "フォント選択ツール",
            "alias": "別名：{0}",
            "crash": "フォント選択ツールの画面でエラーが発生しましたが、捕捉済みです。他のパネルには影響していません。",
            "noData": "バックエンドからデータが返されませんでした",
            "callFailed": "{0} の呼び出しに失敗しました",
            "callFailedDetail": "{0} の呼び出しに失敗しました：{1}",
            "reading": "読み込み中…",
            "copyFail": "コピーに失敗しました：",
            "logReadFail": "ログの読み込みに失敗しました",
            "copied": "{0} 行をコピーしました（{1}）",
            "latestLog": "最新のログ",
            "clipboardDenied": "クリップボードを利用できません（書き込みが拒否されました）",
            "cat.serif": "Serif セリフ",
            "cat.sans": "Sans サンセリフ",
            "cat.mono": "Mono 等幅",
            "cat.other": "その他",
            "cat.fav": "お気に入り",
            "cat.history": "履歴",
            "cat.all": "すべてのフォント",
            "lang.latin": "欧文",
            "lang.zh": "中国語",
            "lang.ja": "日本語",
            "lang.ko": "韓国語",
            "lang.empty": "この分類には{0}のフォントがありません。",
            "note.serif": "ファミリー名によるヒューリスティック判定です（Serif / Song / Ming / Times / 楷 など）。名前に sans を含むものは除外されます。",
            "note.sans": "名前に明示的なサンセリフのキーワード（sans / grotesk / gothic / 黑 など）を含むものです。",
            "note.mono": "fontconfig の spacing フィールド（90 / 100 / 110）で判定します。family 名に mono・等宽・等寬・等幅・고정폭・fixed などのキーワードを含むものも対象です。",
            "note.other": "等幅でもなく、名前にセリフ / サンセリフのキーワードも含まないフォントです。",
            "note.fav": "フォントにフォーカスして X（ゲームパッド）を押すとお気に入りに追加／解除できます",
            "note.history": "最近使用した順に並び、上限なし。アンインストールしたフォントは自動的に外れます",
            "colon": "：",
            "injectDone": "手動適用 · {0}",
            "injectFailed": "適用に失敗",
            "injectNone": "まだ適用していません",
            "injectHeal": "自動適用 · {0}",
            "injectHealed": "自動適用 · {0}",
            "reapply": "現在のフォントを再適用",
            "notSelected": "未選択",
            "expand": "展開",
            "collapse": "折りたたむ",
            "fontChinese": "中国語対応",
            "fontNoChinese": "中国語なし（自動フォールバック）",
            "boldYes": " · 太字あり",
            "boldNo": " · 太字なし（合成太字）",
            "monoTag": " · 等幅",
            "favTag": " · ★お気に入り",
            "inUse": " · 使用中",
            "unfavorite": "お気に入りから解除",
            "favorite": "お気に入りに追加",
            "collapseSection": "この分類を折りたたむ",
            "fontsCount": " 件",
            "totalFonts": "全 {0} 件",
            "clearSearchHint": " · 検索をクリアすると分類表示に戻ります",
            "searchFonts": "フォントを検索",
            "noResult": "一致するフォントがありません（ファミリー名と別名の両方を検索しました）。",
            "loadMore": "さらに読み込む",
            "loadMoreRemaining": "さらに読み込む（残り {0} 件）",
            "restoreDefault": "Steam の既定フォントに戻す",
            "err.noFamily": "システムにフォント {0} がありません",
            "err.noSelection": "フォントが選択されていません",
            "err.noSlots": "欧文・中日韓のフォントがいずれも選択されていません",
            "err.fonttoolsMissing": "{0} の抽出には fontTools が必要ですが、インストールされていません",
            "err.fonttoolsSubset": "西文スロットの削字には fontTools が必要です（{0}）。「言語別注入」をオフにできます",
            "err.noSampleGlyphs": "フォント {0} にサンプル文字の字形がなく、代替フォントで表示します",
            "advanced": "詳細設定",
            "cefNoPort": "CEF デバッグポートがまだ準備できていません（Steam の起動中または再読み込み中）。プラグインは注入ウィンドウ内で自動的に再試行します。",
            "cefPortInfo": "デバッグポート {0} · {1} 個のタブ",
            "woff2Fallback": "woff2 が利用できないため ttf を注入します（サイズ大）",
            "previewCacheTitle": "プレビューキャッシュ上限",
            "previewCacheNote": "プレビュー用サブセットのディスクキャッシュ上限（MB）。既定 8MB で 700 個以上のプレビューを保持。大きくすると再サブセットが減り、小さくするとディスクを節約。1–30MB。",
            "copyLog": "ログをコピー",
            "history.empty": "記録はまだありません",
            "clearHistory": "フォント履歴を消去",
            "historyCleared": "履歴を消去しました",
            "splitMode": "言語別に適用",
            "splitNote": "オンにすると欧文と日中韓を別々に選べます",
            "subSynth": "疑似太字を無効化",
            "subSynthNote": "太字ウェイトを持たないフォントは合成太字されず、中太の文字は標準ウェイトで表示されます。",
            "previewSamples": "フォントプレビュー",
            "previewNote": "コンパクト：1 行のサンプル\n詳細：通常と太字のサンプル\nメモリの少ない環境ではオフかコンパクトを推奨します。",
            "levelOff": "オフ",
            "notchA": "コンパクト",
            "notchB": "詳細",
            "previewExpand": "フォーカス展開プレビュー",
            "previewExpandNote": "オンにすると、フォント行にフォーカスしたときに 4 言語（英 / 中 / 日 / 韓）の大きなサンプルを展開表示します。",
            "exLatin": "英",
            "exZh": "中",
            "exJa": "日",
            "exKo": "韓",
            "slotLatin": "欧文",
            "slotCjk": "日中韓",
            "followSystem": "システムに従う",
            "stripWarn": "欧文フォントのグリフ削除に失敗したため、元のフォントに戻しました。日中韓スロットを覆い隠す可能性があります（欧文専用フォントに変えると解決します）。",
            "menuLatin": "欧文に適用",
            "menuCjk": "中国語に適用",
            "menuCancel": "B で戻ってキャンセル。変更は行われません",
            "descLatinStrip": "欧文字形のみ使用（漢字は自動的に除外）",
            "descCjkNone": "このフォントには中国語字形がありません"
        },
        ko: {
            "pluginTitle": "글꼴 선택기",
            "alias": "별칭: {0}",
            "crash": "글꼴 선택기 화면에서 오류가 발생했지만 차단되었습니다. 다른 패널에는 영향을 주지 않습니다.",
            "noData": "백엔드에서 데이터를 반환하지 않았습니다",
            "callFailed": "{0} 호출에 실패했습니다",
            "callFailedDetail": "{0} 호출에 실패했습니다: {1}",
            "reading": "읽는 중…",
            "copyFail": "복사 실패: ",
            "logReadFail": "로그를 읽지 못했습니다",
            "copied": "{0}행을 복사했습니다 ({1})",
            "latestLog": "최신 로그",
            "clipboardDenied": "클립보드를 사용할 수 없습니다 (쓰기가 거부됨)",
            "cat.serif": "Serif",
            "cat.sans": "Sans",
            "cat.mono": "Mono",
            "cat.other": "기타",
            "cat.fav": "즐겨찾기",
            "cat.history": "기록",
            "cat.all": "모든 글꼴",
            "lang.latin": "라틴",
            "lang.zh": "중국어",
            "lang.ja": "일본어",
            "lang.ko": "한국어",
            "lang.empty": "이 분류에는 {0} 글꼴이 없습니다.",
            "note.serif": "패밀리 이름으로 휴리스틱 판정합니다 (Serif / Song / Ming / Times / 楷 등). 이름에 sans가 들어가면 제외됩니다.",
            "note.sans": "이름에 명확한 sans-serif 키워드(sans / grotesk / gothic / 黑 등)가 들어간 글꼴입니다.",
            "note.mono": "fontconfig의 spacing 필드(90 / 100 / 110)로 판정하며, family 이름에 mono, 等宽, 等寬, 等幅, 고정폭, fixed 등의 키워드가 들어간 글꼴도 포함합니다.",
            "note.other": "monospace도 아니고 이름에 serif / sans-serif 키워드도 없는 글꼴입니다.",
            "note.fav": "글꼴에 포커스한 뒤 X (게임패드)를 눌러 즐겨찾기에 추가/해제할 수 있습니다",
            "note.history": "최근 사용 순으로 정렬되며 제한 없음, 삭제된 글꼴은 자동으로 제외됩니다",
            "colon": ": ",
            "injectDone": "수동 적용 · {0}",
            "injectFailed": "적용 실패",
            "injectNone": "아직 적용 안 됨",
            "injectHeal": "자동 적용 · {0}",
            "injectHealed": "자동 적용 · {0}",
            "reapply": "현재 글꼴 다시 적용",
            "notSelected": "선택 안 됨",
            "expand": "펼치기",
            "collapse": "접기",
            "fontChinese": "중국어 지원",
            "fontNoChinese": "중국어 없음, 자동 대체",
            "boldYes": " · Bold 있음",
            "boldNo": " · Bold 없음 (합성 굵게)",
            "monoTag": " · 고정폭",
            "favTag": " · ★즐겨찾기",
            "inUse": " · 사용 중",
            "unfavorite": "즐겨찾기 해제",
            "favorite": "즐겨찾기 추가",
            "collapseSection": "이 분류 접기",
            "fontsCount": "개",
            "totalFonts": "총 {0}개 글꼴",
            "clearSearchHint": " · 검색을 지우면 분류 보기로 돌아갑니다",
            "searchFonts": "글꼴 검색",
            "noResult": "일치하는 글꼴이 없습니다 (이름과 별칭 모두 검색함).",
            "loadMore": "더 불러오기",
            "loadMoreRemaining": "더 불러오기 ({0}개 남음)",
            "restoreDefault": "Steam 기본 글꼴로 복원",
            "err.noFamily": "시스템에 글꼴 {0}이(가) 없습니다",
            "err.noSelection": "선택된 글꼴이 없습니다",
            "err.noSlots": "라틴 또는 CJK 글꼴이 선택되지 않았습니다",
            "err.fonttoolsMissing": "{0} 추출에 fontTools가 필요하지만 설치되어 있지 않습니다",
            "err.fonttoolsSubset": "라틴 슬롯 정리에 fontTools가 필요합니다 ({0}). 언어별 주입을 끌 수 있습니다",
            "err.noSampleGlyphs": "글꼴 {0}에 샘플 문자가 없어 대체 글꼴로 표시합니다",
            "advanced": "고급",
            "cefNoPort": "CEF 디버깅 포트가 아직 준비되지 않았습니다(Steam 시작 또는 다시 로드 중). 플러그인은 주입 창 안에서 자동으로 재시도합니다.",
            "cefPortInfo": "디버그 포트 {0} · 탭 {1}개",
            "woff2Fallback": "woff2 사용 불가, ttf 주입(용량 커짐)",
            "previewCacheTitle": "미리보기 캐시 상한",
            "previewCacheNote": "미리보기 서브셋의 디스크 캐시 상한(MB). 기본 8MB는 700개 이상의 미리보기를 담을 수 있습니다. 크게 하면 재생성이 줄고, 작게 하면 디스크를 절약합니다. 1–30MB.",
            "copyLog": "로그 복사",
            "history.empty": "기록이 아직 없습니다",
            "clearHistory": "폰트 기록 지우기",
            "historyCleared": "기록을 지웠습니다",
            "splitMode": "언어별 적용",
            "splitNote": "켜면 라틴 문자와 CJK를 각각 선택합니다",
            "subSynth": "가짜 굵게 사용 안 함",
            "subSynthNote": "굵은 자획이 없는 글꼴은 합성 굵게 처리되지 않으며, 중간 굵기 글자는 일반 굵기로 표시됩니다.",
            "previewSamples": "글꼴 미리보기",
            "previewNote": "컴팩트: 한 줄 샘플\n상세: 일반과 굵게 샘플\n메모리가 적은 기기에서는 끄기 또는 컴팩트를 권장합니다.",
            "levelOff": "끄기",
            "notchA": "컴팩트",
            "notchB": "상세",
            "previewExpand": "포커스 확장 미리보기",
            "previewExpandNote": "켜면 글꼴 행에 포커스할 때 4개 언어(영 / 중 / 일 / 한)의 큰 샘플이 펼쳐집니다.",
            "exLatin": "영",
            "exZh": "중",
            "exJa": "일",
            "exKo": "한",
            "slotLatin": "라틴",
            "slotCjk": "CJK",
            "followSystem": "시스템 따름",
            "stripWarn": "라틴 글꼴의 글리프 제거에 실패해 원래 글꼴로 되돌렸습니다. CJK 슬롯을 가릴 수 있습니다(라틴 전용 글꼴로 바꾸면 해결됩니다).",
            "menuLatin": "라틴에 적용",
            "menuCjk": "중국어에 적용",
            "menuCancel": "B로 돌아가 취소하며 변경 사항은 없습니다",
            "descLatinStrip": "라틴 글리프만 사용 (한자 자동 제거)",
            "descCjkNone": "이 글꼴에는 중국어 글리프가 없습니다"
        }
    };

    var LANG = pickLang(detectLang());

    // {0} / {1} 按位置替换，方便各语言调整语序；缺词回退英文，再缺就显示 key 本身
    function t(key) {
        var dict = DICTS[LANG] || DICTS.en;
        var text = dict[key];
        if (text === undefined) text = DICTS.en[key];
        if (text === undefined) return key;
        var args = arguments;
        return String(text).replace(/\{(\d)\}/g, function (m, i) {
            var value = args[Number(i) + 1];
            return value === undefined ? m : value;
        });
    }

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

    // 档位滑块行组件：改用 DFL 原生 SliderField（Steam CommonUIModule 原生控件）。
    // 原生组件自带手柄 ←→ 逐档调节、点击/拖拽及 Steam 原生聚焦态与外观。
    // 不再自绘轨道 + onButtonDown：DFL ButtonItem 的 ItemProps 根本不声明
    // onButtonDown，此前依赖该 prop 做方向键调档，实际被 DFL 忽略（滑块无法调节）。
    // 保留在模块层，函数引用稳定，避免父组件重渲染触发整行重挂载。
    function TierSliderRow(props) {
        var label = props.label;
        var note = props.note;
        var level = props.level;
        var setLevel = props.setLevel;
        var notches = props.notches;
        var max = notches.length - 1;
        return h(deckyFrontendLib.PanelSectionRow, { key: props.key },
            h(deckyFrontendLib.SliderField, {
                value: level,
                min: 0,
                max: max,
                step: 1,
                notchCount: notches.length,
                notchLabels: notches.map(function (txt, i) {
                    return { notchIndex: i, label: txt };
                }),
                notchTicksVisible: true,
                showValue: false,
                validValues: "steps",
                minimumDpadGranularity: 1,
                label: label,
                description: (typeof note === "string" && note.indexOf("\n") !== -1)
                    ? note.split("\n").map(function (line, i) {
                        return h("span", { key: "ln" + i, style: { display: "block" } }, line);
                    })
                    : note,
                layout: "below",
                onChange: function (v) {
                    setLevel(v);
                },
            })
        );
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

    // Mono：spacing 90/100/110 之外，族名含下列关键词的也算（优先于 Serif/Sans/其他）。
    // 等宽（简）/ 等寬（繁）/ 等幅（日）/ 고정폭（韩）覆盖三种 CJK 说法；
    // fixed 对应 Misc Fixed、Sony Fixed，monospace 是通用写法。
    var MONO_KEYWORDS = ["mono", "等宽", "等寬", "等幅", "고정폭", "fixed", "monospace"];
    function isMono(font) {
        if (font && font.mono) return true;
        var name = String((font && font.family) || "").toLowerCase();
        for (var i = 0; i < MONO_KEYWORDS.length; i++) {
            if (name.indexOf(MONO_KEYWORDS[i]) !== -1) return true;
        }
        return false;
    }

    // 语言胶囊：顺序固定为 西文 / 中文 / 日文 / 韩文，也是左右键循环的唯一序列
    // 没有「全部」态：未记录选择时按第一项（西文）处理，任何路径都不会回到不筛选
    var LANG_KEYS = ["latin", "zh", "ja", "ko"];

    // 主语言判定与分类计算完全一致（族名地区标记优先，其次 zh > ja > ko），
    // 三者都不含的归「西文」，因此同一款字体只会落在一个胶囊里
    function primaryLang(font) {
        var lang = font && (font.primary || (font.zh ? "zh" : font.ja ? "ja" : font.ko ? "ko" : ""));
        return lang || "latin";
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
        }, t("alias", alias));
    }

    // ---- 字体行（P3：React.memo 化）------------------------------------------
    // 行组件提到模块层：组件类型必须跨渲染稳定，定义在父组件内部会让 memo 失效
    // （每次渲染都是新类型 → React 全量卸载重挂）。行内容与父组件一样是纯渲染，
    // 需要的数据全部由 props 传入；事件回调统一经 props.api（ref）转发到父组件
    // 最新闭包，避免 memo 跳过重渲染时读到旧状态。

    // 行内容：档位 0=纯名 / 1=方案 A 单行样张 / 2=方案 B 常规+粗体；expandLevel>0 追加 C 方案展开块。
    // 展开块 DOM 常驻但 display:none——Chromium 对隐藏内容不加载字体，聚焦时才真正渲染（懒加载）
    function fpRowContent(font, sample, boldLabel, previewLevel, expandLevel) {
        var slot = fpPreviewSlot(font.family);
        var familyCss = '"' + slot + '"';
        // data-* 只给 IO 用：滚进视口的预览行才去后端取 @font-face URL（懒加载）
        var loadAttrs = { "data-fp-font": font.family, "data-fp-slot": slot };
        // 名字永远用自身字体渲染：fp-nm 挂同样的 data-* 与 --fp-family，进 IO 懒加载
        // 集合（与预览档位无关，预览关闭时也生效）；@font-face 未到位前 CSS 里
        // "slot", inherit 兜底显示默认字体，到位后浏览器按 cmap 逐字符换字自动重排。
        var kids = [h("div", { className: "fp-fname fp-nm", style: { "--fp-family": familyCss }, ...loadAttrs }, font.family)];
        if (previewLevel === 1) {
            kids.push(h("div", { className: "fp-prev fp-prev-a", style: { "--fp-family": familyCss }, ...loadAttrs }, sample));
        } else if (previewLevel === 2) {
            kids.push(h("div", { className: "fp-prev", style: { "--fp-family": familyCss }, ...loadAttrs }, sample));
            kids.push(h("div", { className: "fp-prev fp-prev-b", style: { "--fp-family": familyCss }, ...loadAttrs },
                h("span", { className: "fp-badge" }, boldLabel + "  "), sample));
        }
        if (expandLevel > 0) {
            var lines = [
                h("div", { className: "fp-ex-line fp-ex-lat", style: { "--fp-family": familyCss }, ...loadAttrs },
                    h("span", { className: "fp-ex-label" }, t("exLatin")), SAMPLES.exEn)
            ];
            if (font.zh || font.zh_extra) {
                lines.push(h("div", { className: "fp-ex-line", style: { "--fp-family": familyCss }, ...loadAttrs },
                    h("span", { className: "fp-ex-label" }, t("exZh")), SAMPLES.exZh));
            }
            if (font.ja) {
                lines.push(h("div", { className: "fp-ex-line", style: { "--fp-family": familyCss }, ...loadAttrs },
                    h("span", { className: "fp-ex-label" }, t("exJa")), SAMPLES.exJa));
            }
            if (font.ko) {
                lines.push(h("div", { className: "fp-ex-line", style: { "--fp-family": familyCss }, ...loadAttrs },
                    h("span", { className: "fp-ex-label" }, t("exKo")), SAMPLES.exKo));
            }
            kids.push(h("div", { className: "fp-ex" }, lines));
        }
        // fp-font-row 标识类：只给字体行（ButtonItem 聚焦时 Steam 画白底），
        // 与档位滑块行（自绘 Focusable、深底聚焦）区分，避免聚焦态配色串味
        return h("div", { className: "fp-frow fp-font-row", style: { width: "100%", textAlign: "left" } }, kids);
    }

    function FontRowBase(props) {
        if (typeof window !== "undefined") {
            // 调试计数：验证 memo 生效（父组件重渲染而行 props 未变时此数不增）
            window.__fpRowRenders = (window.__fpRowRenders || 0) + 1;
        }
        var font = props.font;
        var alias = props.alias;
        var sectionId = props.sectionId;
        var busy = props.busy;
        var isFav = props.isFav;
        var inUse = props.inUse;
        var api = props.api;

        // 方案 B：每行常显「常规 + 粗体」双样张，粗体行带 BOLD / SYNTH 标签
        // 无样张字形（预期失败）且预览档位开启时：主样张文本换成错误文案，
        // DOM 结构与 class 不变，文案渲染在回退字体上；展开块四语样张不动。
        var noGlyph = !!props.noGlyph;
        var sampleEn = SAMPLES.prevEn;
        var sampleZh = SAMPLES.prevZh;
        if (props.previewLevel > 0 && noGlyph) {
            var noGlyphText = t("err.noSampleGlyphs", font.family);
            sampleEn = noGlyphText;
            sampleZh = noGlyphText;
        }
        var sample = (font.zh || font.zh_extra) ? sampleZh : sampleEn;
        var boldLabel = font.has_bold ? "BOLD" : "SYNTH";

        return h(deckyFrontendLib.PanelSectionRow, null,
            h(deckyFrontendLib.ButtonItem, {
                layout: "below",
                disabled: busy,
                description: (font.zh ? t("fontChinese") : t("fontNoChinese")) +
                    (font.has_bold ? t("boldYes") : t("boldNo")) +
                    (font.mono ? t("monoTag") : "") +
                    (isFav ? t("favTag") : "") +
                    (inUse ? t("inUse") : ""),
                onClick: function () { api.current.choose(font.family, font); },
                onSecondaryActionDescription: isFav ? t("unfavorite") : t("favorite"),
                onOptionsActionDescription: sectionId ? t("collapseSection") : undefined,
                onButtonDown: function (evt) {
                    var btn = buttonOf(evt);
                    // 切完筛选把焦点送回本分区标题行（立即一次，不延迟补焦），保证左右键连续切换
                    if (btn === BTN_LEFT || btn === BTN_RIGHT) {
                        if (!api.current.hasLangPills(sectionId)) return false;
                        api.current.cycleLang(sectionId, btn === BTN_RIGHT ? 1 : -1);
                        api.current.focusHeaderNow(sectionId);
                        evt.stopPropagation();
                        return true;
                    }
                    if (btn === BTN_X) api.current.toggleFavorite(font.family);
                    // 在分区内就地收起，不必滚回分区标题
                    else if (btn === BTN_Y && sectionId) api.current.collapseSection(sectionId);
                },
            },
                // ↓ 档位化预览：0=纯字体名（原 1.4.2 行为）/ 1=方案 A / 2=方案 B；
                //   C 方案展开块由开关控制（expandLevel>0）
                props.previewOn || props.expandLevel > 0
                    ? fpRowContent(font, sample, boldLabel, props.previewLevel, props.expandLevel)
                    : font.family
            ),
            alias ? aliasLine(alias) : null
        );
    }

    // 比较器只比数据 props：font 引用来自 fonts state（不重载时引用稳定），
    // 其余全是原始值；api 是 ref（恒定引用），函数内容经 current 取最新。
    // t() 依赖的 LANG 模块级常量（加载时定死），不随 state 变化，无需参与比较。
    function fpRowEqual(a, b) {
        return a.font === b.font &&
            a.alias === b.alias &&
            a.sectionId === b.sectionId &&
            a.busy === b.busy &&
            a.isFav === b.isFav &&
            a.inUse === b.inUse &&
            a.previewOn === b.previewOn &&
            a.previewLevel === b.previewLevel &&
            a.expandLevel === b.expandLevel &&
            a.noGlyph === b.noGlyph &&
            a.api === b.api;
    }

    var FontRow = React.memo(FontRowBase, fpRowEqual);

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
                    React.createElement("div", null, t("crash")),
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

        // 预览缓存上限（MB）：本地 state 立即反映滑块，避免回弹；payload 到达后
        // 再同步为后端权威值。refs 记录待发送值与写入确认状态，供防抖与同步守卫用。
        var cacheMbHook = useState(8);
        var previewCacheMb = cacheMbHook[0];
        var setPreviewCacheMb = cacheMbHook[1];
        var cacheMbTimerRef = useRef(null);
        var cacheMbValueRef = useRef(8);
        var cacheMbPendingRef = useRef(false);

        // 无样张字形字体表（family -> true）：收到 err.noSampleGlyphs 后标记，
        // 触发重渲染让对应行换文案。会话内每个 family 至多触发一次状态更新，
        // 重复标记直接 return；端口变更时整体清空（与 ok/error 状态同生命周期）。
        var noGlyphHook = useState({});
        var noGlyphState = noGlyphHook[0];
        var setNoGlyphState = noGlyphHook[1];

        var messageHook = useState("");
        var message = messageHook[0];
        var setMessage = messageHook[1];

        var collapsedHook = useState({});
        var collapsed = collapsedHook[0];
        var setCollapsed = collapsedHook[1];

        // 各分区各自记住所选语言；没有记录的分区按西文处理（不再有「不筛选」态）
        var langPickHook = useState({});
        var langPick = langPickHook[0];
        var setLangPick = langPickHook[1];

        function applyLangPick(sectionId, key) {
            setLangPick(function (prev) {
                var next = {};
                for (var k in prev) { if (Object.prototype.hasOwnProperty.call(prev, k)) next[k] = prev[k]; }
                next[sectionId] = key;
                return next;
            });
        }

        // 本分区当前的语言：未记录 / 空串一律算西文，展开即进入西文筛选
        function pickedLang(sectionId) {
            return langPick[sectionId] || LANG_KEYS[0];
        }

        // 分类标题与字体行上的左右键都走这里：四态首尾相接循环，
        // 顺序为「西文 → 中文 → 日文 → 韩文 → 西文」，左键反向。
        function cycleLang(sectionId, step) {
            var idx = LANG_KEYS.indexOf(pickedLang(sectionId));
            if (idx < 0) idx = 0;
            applyLangPick(sectionId, LANG_KEYS[(idx + step + LANG_KEYS.length) % LANG_KEYS.length]);
        }

        // 确认键菜单（主开关开时）：字体行 A / 点击 → 弹出「应用西文 / 应用中文」小菜单，
        // 按语言智能归属预选高亮（zh/ja/ko 预选中文，其余预选西文），A→A 两下完成；
        // B 或点空白处关闭且不写任何配置。主开关关闭时不弹菜单，确认键直接应用（1.4.2 行为）
        var menuFontHook = useState(null);
        var menuFont = menuFontHook[0];
        var setMenuFont = menuFontHook[1];
        var menuLatinRef = useRef(null);
        var menuCjkRef = useRef(null);

        useEffect(function () {
            if (!menuFont) return;
            var toCjk = primaryLang(menuFont) !== "latin";
            // force：菜单是新开的弹层，焦点要从字体行主动移进菜单选项，不受防抢占守卫约束
            focusLater(function () {
                var node = toCjk ? menuCjkRef.current : menuLatinRef.current;
                if (!node && typeof document !== "undefined") {
                    node = document.getElementById(toCjk ? "fp-menu-cjk" : "fp-menu-latin");
                }
                return node;
            }, true);
        }, [menuFont]);

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

        // 后端面向用户的消息带 key：按当前语言翻译；没有 key 时回退到原文（日志类文本）
        function textOf(obj) {
            if (!obj) return "";
            if (obj.error_key) return t.apply(null, [obj.error_key].concat(obj.error_args || []));
            return obj.error || obj.message || "";
        }

        function applyPayload(raw) {
            var payload = unwrap(raw);
            if (!payload || typeof payload !== "object") {
                setMessage(t("noData"));
                return;
            }
            setMessage(textOf(payload));
            setState(payload);
        }

        function call(method, args) {
            return serverApi.callPluginMethod(method, args || {}).then(function (res) {
                var value = unwrap(res);
                if (res && res.success === false) {
                    setMessage(textOf(value) || t("callFailed", method));
                    return null;
                }
                applyPayload(value);
                return value;
            }).catch(function (e) {
                setMessage(t("callFailedDetail", method, String(e)));
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

        // 打开面板只读一次：get_fonts + get_font_state。
        // 状态一次取回；注入后只做短轮询确认三行 ok，不做任何命中探测
        // 前端不再做任何定时轮询；QAM / MainMenu 的补注入由后端注入窗口负责。
        useEffect(function () {
            refresh();
        }, []);

        // ---- 列表样张字体懒加载 ------------------------------------------------
        // 行内样张 / 展开块样张先用 fpPreviewSlot(family) 作内部族名写进 --fp-family；
        // 真正滚进视口时才向后端取该字体的极小子集 URL，注入 @font-face 后浏览器自动
        // 换字。隐藏的展开块（display:none）不会进入视口，因此不抽面、不加载字体。
        // @font-face 规则直接写专用 <style> 节点（同步注入，写完即可接着 fonts.load），
        // 不走 React state：一次滚动可新增数十条规则，走 state 会触发整面板重渲染（jank）。
        var previewStyleRef = useRef(null);
        var previewRulesRef = useRef({}); // slot -> rule 字符串，按 slot 去重
        var previewFontsRef = useRef({});
        var previewRetryRef = useRef({});
        var rootRef = useRef(null);
        var ioRef = useRef(null);
        var previewPortRef = useRef(null);
        var previewGenRef = useRef(0); // 字体服务代数:仅真实重启(端口真变更)时 +1

        // 字体行回调经 ref 转发（每次渲染更新为最新闭包）：FontRow 已 memo 化，
        // memo 跳过重渲染时子组件持有的还是上次渲染的闭包，直接传函数会读到旧状态
        // （favorites/current/categories 等）。ref 只存引用，行组件调用时取 current，
        // 永远是最新一次渲染的函数。函数声明在组件内被提升，此处赋值合法。
        var rowApiRef = useRef(null);
        rowApiRef.current = {
            choose: choose,
            toggleFavorite: toggleFavorite,
            collapseSection: collapseSection,
            cycleLang: cycleLang,
            hasLangPills: hasLangPills,
            focusHeaderNow: focusHeaderNow,
        };

        // 把 @font-face 规则增量追加进专用 style 节点（不走 state，避免全量重写 jank）。
        // 同一 slot 只写一次；用 CSSStyleSheet.insertRule 只追加新规则，不碰已加载字体的
        // 规则——旧版整段 textContent 重写会销毁全部 @font-face 触发宽度重排（放大器：
        // 每次新字体滚入都全量重建，24 条→60 条）。CEF 126 已实测 insertRule 可用，
        // 保留 textContent 写法作兜底（sheet 不可用时回退）。
        function commitPreviewRule(slot, rule) {
            if (previewRulesRef.current[slot]) return;
            previewRulesRef.current[slot] = rule;
            var node = previewStyleRef.current;
            if (!node) return;
            var sheet = node.sheet;
            if (sheet && typeof sheet.insertRule === "function") {
                try {
                    sheet.insertRule(rule, sheet.cssRules.length);
                    return;
                } catch (e) {
                    // insertRule 抛错（老引擎/无效规则）时回退到全量 textContent 写法
                }
            }
            node.textContent = Object.keys(previewRulesRef.current).map(function (k) {
                return previewRulesRef.current[k];
            }).join("");
        }

        // 标记无样张字形字体：触发重渲染让对应行换文案。不可变更新；
        // 已标记过的 family 直接复用旧对象（React 同引用即跳过渲染），会话内
        // 每个 family 至多触发一次状态更新。
        function markNoGlyphFont(family) {
            setNoGlyphState(function (prev) {
                if (prev && prev[family]) return prev;
                var next = {};
                for (var k in prev) { if (Object.prototype.hasOwnProperty.call(prev, k)) next[k] = prev[k]; }
                next[family] = true;
                return next;
            });
        }

        function ensurePreviewFont(family, slot) {
            if (!family || !slot) return;
            var seen = previewFontsRef.current;
            // 已就绪/在途/已判定永久回退：直接返回，不重复发 RPC（预热与 IO 懒加载天然去重；
            // permanent-fallback 为会话级缓存，命中后连 RPC 都不发）
            if (seen[family] === "ok" || seen[family] === "loading" || seen[family] === "permanent-fallback") return;
            // 失败态：走原有最多 1 次重试；已重试过则放弃（显示 fallback）
            var retries = previewRetryRef.current;
            if (seen[family] === "error") {
                if ((retries[family] || 0) >= 1) return;
                retries[family] = (retries[family] || 0) + 1;
            }
            seen[family] = "loading";
            var gen = previewGenRef.current;
            // 返回 promise 供全量预热队列限流；IO/rescan 等调用点忽略返回值即可
            return serverApi.callPluginMethod("get_preview_font", { family: family }).then(function (res) {
                if (gen !== previewGenRef.current) return; // 字体服务已重启（代数变化），丢弃旧代数的 URL
                var value = unwrap(res);
                if (value && value.error_key === "err.noSampleGlyphs") {
                    // 预期失败（字体不含样张字符）：永久 fallback，不进重试通道
                    seen[family] = "permanent-fallback";
                    markNoGlyphFont(family);
                    return;
                }
                if (!value || value.ok !== true || !value.url) {
                    seen[family] = "error";
                    return;
                }
                var fmt = FP_PREVIEW_MIME_FORMAT[value.mime] || "truetype";
                var rule = '@font-face{font-family:"' + slot + '";font-style:normal;' +
                    'font-weight:400;font-display:swap;src:url("' + value.url + '") ' +
                    'format("' + fmt + '");}\n';
                seen[family] = "ok";
                commitPreviewRule(slot, rule);
                // 触发浏览器真正下载字体子集（全量预热依赖它）；不再挂任何加载期门控回调。
                // 门控对横向跳无效且会造成字体名短暂消失的闪烁，已全撤（用户明确要求）。
                try {
                    var loaded = document.fonts.load('15px "' + slot + '"');
                    if (loaded && typeof loaded.catch === "function") loaded.catch(function () {});
                } catch (e) {}
            }).catch(function () {
                seen[family] = "error";
            });
        }

        // 全量预热：面板打开即把当前列表所有字体的子集预取并加载，不等滚动触发。
        // 复用 ensurePreviewFont（含去重/RPC/commitPreviewRule/fonts.load/状态机），
        // 本函数只负责「收集目标 + 限流并发」，不重写任何字体加载逻辑。
        function prewarmAllFonts() {
            var root = rootRef.current;
            if (!root) return;
            var nodes = root.querySelectorAll(".fp-nm[data-fp-font]");
            var families = [];
            var seen = {};
            for (var i = 0; i < nodes.length; i++) {
                var fam = nodes[i].getAttribute("data-fp-font");
                var slt = nodes[i].getAttribute("data-fp-slot");
                if (!fam || !slt || seen[fam]) continue;
                seen[fam] = 1;
                families.push({ family: fam, slot: slt });
            }
            if (!families.length) return;
            var LIMIT = 6; // 并发上限：60 个字体也不会瞬间打满 60 个 RPC
            var idx = 0;
            var active = 0;
            function pump() {
                while (idx < families.length && active < LIMIT) {
                    var item = families[idx++];
                    active++;
                    var p;
                    try {
                        p = ensurePreviewFont(item.family, item.slot);
                    } catch (e) {
                        p = null;
                    }
                    if (p && typeof p.then === "function") {
                        p.then(finish, finish);
                    } else {
                        Promise.resolve().then(finish); // 去重早退项也走异步，避免深递归
                    }
                }
                if (idx >= families.length && typeof window !== "undefined") {
                    window.__fpPrewarmDone = true; // 预热队列已全部入队（供探针观察）
                }
            }
            function finish() {
                active--;
                pump();
            }
            pump();
        }

        // 视口可见判定：嵌入式 CEF 的 about:blank webview 里，IntersectionObserver 对
        // 初始/新挂载即已可见的节点首帧回调不来，故用几何直接判定作同步补调依据。
        function isFpNodeVisible(el) {
            var r = el.getBoundingClientRect();
            return r.bottom > 0 && r.top < window.innerHeight;
        }

        // 视口补扫:IO 挂载与字体服务端口变更两个 useEffect 共用的节点遍历。
        // __fpObserved 保证每节点只同步补调一次;端口变更路径先清零该标记再走这里。
        function rescanNodes() {
            var io = ioRef.current;
            if (!io || !rootRef.current) return;
            var nodes = rootRef.current.querySelectorAll(".fp-nm[data-fp-font],.fp-prev[data-fp-font],.fp-ex-line[data-fp-font]");
            for (var i = 0; i < nodes.length; i++) {
                var node = nodes[i];
                if (node.__fpObserved) continue;
                node.__fpObserved = 1;
                if (isFpNodeVisible(node)) {
                    ensurePreviewFont(node.getAttribute("data-fp-font"), node.getAttribute("data-fp-slot"));
                }
                io.observe(node);
            }
        }

        useEffect(function () {
            var root = rootRef.current;
            if (!root) return;
            if (typeof IntersectionObserver === "undefined") {
                // 老引擎兜底：只取行内样张，隐藏的展开样张等聚焦后再说
                var rows = root.querySelectorAll(".fp-nm[data-fp-font],.fp-prev[data-fp-font]");
                for (var j = 0; j < rows.length; j++) {
                    ensurePreviewFont(rows[j].getAttribute("data-fp-font"), rows[j].getAttribute("data-fp-slot"));
                }
                return;
            }
            var io = new IntersectionObserver(function (entries) {
                for (var i = 0; i < entries.length; i++) {
                    if (!entries[i].isIntersecting) continue;
                    var el = entries[i].target;
                    // error 态的行不解除观察：滚出再滚回视口会再次触发本回调，
                    // ensurePreviewFont 内的重试闸门（每族整个会话一次）兜住请求频率
                    if (previewFontsRef.current[el.getAttribute("data-fp-font")] !== "error") {
                        io.unobserve(el);
                    }
                    ensurePreviewFont(el.getAttribute("data-fp-font"), el.getAttribute("data-fp-slot"));
                }
            }, { rootMargin: "300px 0px" });
            ioRef.current = io;
            var mo = new MutationObserver(function () { rescanNodes(); });
            mo.observe(root, { childList: true, subtree: true });
            rescanNodes();
            prewarmAllFonts(); // 面板打开即全量预热，不等滚动触发（异步进行，不阻塞 effect 返回）
            return function () { mo.disconnect(); io.disconnect(); ioRef.current = null; };
        }, []);

        // 字体服务重启（reset_font 后再选字）端口/token 全变，旧 @font-face URL 会 404。
        // 端口 effect 区分首次同步与真实变更：首次（挂载后首个 payload）只记端口，
        // 不清缓存——挂载 effect 的 rescanNodes 已经补调过，这里再清空会造成同族
        // 二次请求（mount 双请求）；仅真实变更才清空并重扫。
        useEffect(function () {
            var port = state && state.server && state.server.port;
            var io = ioRef.current;
            if (!port || !io || !rootRef.current) return;
            if (previewPortRef.current === port) return;
            if (previewPortRef.current === null) {
                previewPortRef.current = port; // 首次同步:只记端口
                return;
            }
            // 真实变更：旧 URL 已失效，清空重扫；代数 +1 使在途旧响应失效
            previewPortRef.current = port;
            previewGenRef.current += 1;
            previewFontsRef.current = {};
            previewRetryRef.current = {}; // 新端口 = 新机会，重试计数一并清零
            previewRulesRef.current = {}; // 同 slot 去重表一并清，允许写入新 URL 的规则
            setNoGlyphState({}); // 无字形标记与 ok/error 状态同生命周期；重开面板/端口变化后重新 RPC 会再次命中 error_key 并重新标记
            // insertRule 追加的规则挂在 sheet 上：光清 textContent 不足以删除它们，
            // 必须逐条 deleteRule，否则新端口的旧 URL 规则残留会 404。
            if (previewStyleRef.current) {
                var psheet = previewStyleRef.current.sheet;
                if (psheet && typeof psheet.deleteRule === "function") {
                    try {
                        while (psheet.cssRules.length) psheet.deleteRule(0);
                    } catch (e) {
                        previewStyleRef.current.textContent = "";
                    }
                } else {
                    previewStyleRef.current.textContent = "";
                }
            }
            // reset 后同样同步补调当前可见行：端口变了要重新取新 URL，
            // 只靠 IO 首帧回调同样会漏加载。
            var nodes = rootRef.current.querySelectorAll(".fp-nm[data-fp-font],.fp-prev[data-fp-font],.fp-ex-line[data-fp-font]");
            for (var i = 0; i < nodes.length; i++) {
                nodes[i].__fpObserved = 0;
            }
            rescanNodes();
            prewarmAllFonts(); // 端口全变、旧 URL 全部失效：重新全量预热
        }, [state && state.server && state.server.port]);

        // ---- P4 展开滚动稳定 ----------------------------------------------------
        // C 方案展开块 display:none→block 会让聚焦行高度突增（约 +110px），
        // Steam 原生滚动是在展开前算的，聚焦行可能被顶得半截可见或整个出视口
        // （表现为「往下按跳回分类」的残留场景）。
        // focusin 捕获 gpfocus 到达时机后连调三次 scrollIntoView({block:"nearest"})：
        //   同步一次（类已挂则布局立即生效）+ 0ms/120ms 各兜底一次，
        //   覆盖「Steam 先发事件后挂类」的时序差与 QAM overlay 页 rAF 节流
        //   （实测 rAF 回调在 overlay 里可能不跑，2026-11-18 探针实证）。
        // block:"nearest" 幂等：只在行不完整可见时微调，不与 Steam 自身滚动叠加。
        useEffect(function () {
            function onFpFocusIn(evt) {
                var el = evt.target;
                if (!el || !el.classList || !el.classList.contains("gpfocus")) return;
                if (!el.querySelector || !el.querySelector(".fp-font-row .fp-ex")) return;
                el.scrollIntoView({ block: "nearest" });
                setTimeout(function () { el.scrollIntoView({ block: "nearest" }); }, 0);
                setTimeout(function () { el.scrollIntoView({ block: "nearest" }); }, 120);
            }
            document.addEventListener("focusin", onFpFocusIn, true);
            return function () {
                document.removeEventListener("focusin", onFpFocusIn, true);
            };
        }, []);

        // ---- 动作后的短轮询 -----------------------------------------------------
        // 会改注入状态的动作，返回值是在「main 已注入、后台动态注入刚起步」时算出来的中间态，
        // 之后没人再取，面板就会停在中间态（必须重开 QAM 才刷新）。
        // 这里在动作后立刻取一次，再每 0.5 秒补取，最多 10 跳（约 5 秒）；
        // 三个目标全部 ok 立即收手，到上限也安静停下（不报错、不改文案）。
        // 上一跳没回来就跳过这一跳（单飞）；同一时刻只允许一个轮询。
        var SETTLE_INTERVAL = 500;
        var SETTLE_MAX_TICKS = 10;
        var settleRef = useRef({ timer: null, tick: 0, inFlight: false, token: 0 });
        var aliveRef = useRef(true);

        useEffect(function () {
            aliveRef.current = true;
            return function () {
                aliveRef.current = false;
                stopSettle();
                if (cacheMbTimerRef.current) clearTimeout(cacheMbTimerRef.current);
            };
        }, []);

        function stopSettle() {
            var s = settleRef.current;
            s.token += 1;
            if (s.timer !== null) {
                clearTimeout(s.timer);
                s.timer = null;
            }
            s.tick = 0;
        }

        function allInjected(payload) {
            if (!payload || !Array.isArray(payload.targets) || !payload.targets.length) return false;
            for (var i = 0; i < payload.targets.length; i++) {
                if (!payload.targets[i] || payload.targets[i].ok !== true) return false;
            }
            return true;
        }

        function settleFetch(token) {
            var s = settleRef.current;
            if (s.inFlight) return; // 上一跳还没回来，这一跳跳过，不叠加
            s.inFlight = true;
            serverApi.callPluginMethod("get_font_state", {}).then(function (res) {
                s.inFlight = false;
                if (!aliveRef.current || token !== s.token) return;
                if (!res || res.success === false) return;
                var value = unwrap(res);
                if (!value || typeof value !== "object") return;
                applyPayload(value);
                if (allInjected(value)) stopSettle();
            }).catch(function () { s.inFlight = false; });
        }

        function settleTick(token) {
            var s = settleRef.current;
            s.timer = null;
            if (!aliveRef.current || token !== s.token) return;
            settleFetch(token);
            s.tick += 1;
            if (s.tick < SETTLE_MAX_TICKS) {
                s.timer = setTimeout(function () { settleTick(token); }, SETTLE_INTERVAL);
            }
        }

        function startSettle() {
            stopSettle(); // 新动作开始前先停掉旧的轮询
            var s = settleRef.current;
            var token = s.token;
            s.tick = 0;
            settleFetch(token); // 动作后立刻取一次，不等第一个间隔
            s.timer = setTimeout(function () { settleTick(token); }, SETTLE_INTERVAL);
        }

        // 写入槽位：复用现有 set_font / set_cjk_font，动作后照常短轮询确认三行状态
        function applyToSlot(slot, family) {
            if (busy) return;
            setBusy(true);
            stopSettle();
            call(slot === "cjk" ? "set_cjk_font" : "set_font", { family: family }).then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        // 确认键入口：主开关开 → 弹两选项菜单（按语言预选）；
        // 主开关关 → 直接应用为西文槽（set_font），与 1.4.2 行为完全一致
        function choose(family, font) {
            if (busy) return;
            if (splitOn) {
                setMenuFont(font || { family: family });
                return;
            }
            applyToSlot("latin", family);
        }

        function reset() {
            if (busy) return;
            setBusy(true);
            stopSettle();
            call("reset_font").then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        // 抽取嵌入说明行 = 重新注入当前字体：复用后端已有的 reapply，不另起流程。
        // 没选字体时安静地什么都不做（不发请求、不弹提示）
        function reapplyFont() {
            if (busy || (!current && !(state && state.family_cjk))) return;
            setBusy(true);
            stopSettle();
            call("reapply").then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        // CEF 调试口：无 UI 开关、缺省常开；cefInfo 仅用于渲染端口状态/错误提示行
        var cefInfo = (state && state.cef) || { port: null, tabs: null, error: "" };

        // 分槽注入（按语言注入）开关状态：主开关 + 子开关（禁用假加粗），全部来自后端 payload；
        // 子开关默认值由后端口径统一（synth_weight_none 缺省=开）
        var splitOn = !!(state && state.cjk_split);
        var synthNone = !!(state && state.synth_weight_none);
        // 字体预览样张档位：0 关闭 / 1 方案 A / 2 方案 B（兼容旧布尔 preview 键，缺省视为 2）
        var previewLevel = 2;
        var expandLevel = 0;
        if (state) {
            if (typeof state.preview_level === "number") {
                previewLevel = Math.max(0, Math.min(2, state.preview_level | 0));
            } else {
                previewLevel = state.preview === false ? 0 : 2;
            }
            if (typeof state.expand_level === "number") {
                // C 方案已改为开关：旧 2 档（内嵌）迁移为开启，只保留 0 关 / 1 开
                expandLevel = (state.expand_level | 0) > 0 ? 1 : 0;
            }
        }
        // 预览缓存字节上限（MB）：payload 为后端权威值，收敛到 1-30
        var payloadCacheMb = 8;
        if (state && typeof state.preview_cache_mb === "number") {
            payloadCacheMb = Math.max(1, Math.min(30, state.preview_cache_mb | 0));
        }
        // payload 更新（含 RPC 回包 applyPayload）后同步本地滑块值；写入未确认期间
        // 暂停同步，避免拖动中被稍后到达的旧 payload 拉回（回弹）。
        useEffect(function () {
            if (!cacheMbPendingRef.current) setPreviewCacheMb(payloadCacheMb);
        }, [payloadCacheMb]);
        var previewOn = previewLevel !== 0; // 兼容旧引用

        // 档位滑块行组件 TierSliderRow 已提到模块层（buttonOf 之后），
        // 内部改用原生 SliderField：方向键一次一档、点击拖拽即可调档。
        // 必须以 h(TierSliderRow, {...}) 调用保持实例稳定。

        // 开关变化会改变注入 CSS（剔字掩码版本 / Motiva Sans / synthesis 行），
        // 后端会按当前字体重新注入一次，所以动作后照常走一次有界轮询确认三行状态
        function toggleSplit() {
            if (busy) return;
            setBusy(true);
            stopSettle();
            call("set_split_options", { cjk_split: !splitOn }).then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        function toggleSynth() {
            if (busy) return;
            setBusy(true);
            stopSettle();
            call("set_split_options", { synth_weight_none: !synthNone }).then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        // 预览样张档位 / C 方案开关：纯前端渲染开关，不改注入 CSS；后端只落盘不重新注入。
        // 档位由原生 SliderField 的 onChange 落盘，C 方案开关由 ToggleField 落盘（setLevels）。
        function setLevels(patch) {
            if (busy) return;
            setBusy(true);
            stopSettle();
            call("set_split_options", patch).then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        // 预览缓存上限滑块：纯缓存设置，后端只落盘、不重新注入 CSS，因此不占用
        // busy、也不走 startSettle（call 内部已 applyPayload 回写新值）。连续拖动会
        // 高频 onChange，用短防抖只送最终值，避免走 setLevels 的 busy 互斥把中间的
        // onChange 丢掉、滑块被旧 payload 拉回。本地 state 在 onChange 当下即更新。
        function setCacheMb(v) {
            var next = Math.max(1, Math.min(30, v | 0));
            cacheMbValueRef.current = next;
            cacheMbPendingRef.current = true;
            setPreviewCacheMb(next);
            if (cacheMbTimerRef.current) clearTimeout(cacheMbTimerRef.current);
            cacheMbTimerRef.current = setTimeout(function () {
                cacheMbTimerRef.current = null;
                call("set_split_options", { preview_cache_mb: cacheMbValueRef.current }).then(function () {
                    // 若期间又拖动产生了新值，保护继续有效，直到最后一次写入确认
                    if (!cacheMbTimerRef.current) cacheMbPendingRef.current = false;
                });
            }, 100);
        }

        // 复制日志：后端只读返回裁剪后的最新日志；前端写剪贴板。不占用 busy，也不动其它面板状态
        var copyHook = useState("");
        var copyMsg = copyHook[0];
        var setCopyMsg = copyHook[1];

        // clipboard API 不可用或被拒时，退回隐藏 textarea + execCommand
        function copyViaTextarea(text) {
            var area = document.createElement("textarea");
            area.value = text;
            area.style.position = "fixed";
            area.style.opacity = "0";
            document.body.appendChild(area);
            area.focus();
            area.select();
            var ok = false;
            try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
            document.body.removeChild(area);
            return ok;
        }

        function copyLog() {
            setCopyMsg(t("reading"));
            serverApi.callPluginMethod("get_recent_log", {}).then(function (res) {
                var value = unwrap(res);
                if (!res || res.success === false || !value || !value.ok) {
                    setCopyMsg(t("copyFail") + ((value && value.error) || t("logReadFail")));
                    return;
                }
                var text = value.text || "";
                var done = function () {
                    setCopyMsg(t("copied", value.lines || 0, value.file || t("latestLog")));
                };
                var fallback = function () {
                    if (copyViaTextarea(text)) done();
                    else setCopyMsg(t("copyFail") + t("clipboardDenied"));
                };
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(done).catch(fallback);
                } else {
                    fallback();
                }
            }).catch(function (e) {
                setCopyMsg(t("copyFail") + (e || t("logReadFail")));
            });
        }

        // 清除历史字体记录：后端清空 state["history"] 并随 payload 返回新 state，
        // call() 内部已经 applyPayload 刷新面板，这里只补一条反馈提示
        function clearHistory() {
            if (busy) return;
            setBusy(true);
            call("clear_history").then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) setCopyMsg(t("historyCleared"));
            }).catch(function () { setBusy(false); });
        }

        function toggleFavorite(family) {
            if (!family) return;
            if (busy) return; // 其它批量动作进行中不响应收藏切换,与上方各动作一致
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

        // 只 focus 不滚屏：标题行位于当前视口附近，focus 可见元素不触发滚动。
        // opts.force=true：主动移焦（切语言筛选），无视当前焦点直接落标题；
        // 默认（兜底补焦）：若此刻用户已把焦点挪到插件内其它元素，则放弃抢占——
        // 否则延迟补焦会在用户紧接着按 ↓ 浏览列表时把焦点拽回分类标题，
        // 实测表现为「往下按的时候焦点有时跳回分类」。
        function focusDomNode(node, opts) {
            if (!node) return;
            if (!(opts && opts.force)) {
                var act = typeof document !== "undefined" ? document.activeElement : null;
                if (act && act !== document.body && rootRef.current &&
                    rootRef.current.contains(act) && act !== node) return;
            }
            if (typeof node.focus === "function") node.focus();
        }

        // 重试几次是因为收起分区后 Steam 重新渲染 + 滚动容器归位需要一两帧。
        // force=true 传给 focusDomNode：主动移焦场景（如打开确认键菜单）无视守卫。
        function focusLater(getNode, force) {
            var delays = [0, 120, 320];
            for (var i = 0; i < delays.length; i++) {
                (function (delay) {
                    setTimeout(function () {
                        focusDomNode(getNode(), force ? { force: true } : undefined);
                    }, delay);
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

        // 切换语言筛选后把焦点移到本分区标题（立即一次，无延迟重试）：
        // 之前走 focusLater 的 120/320ms 补焦，会在用户切完筛选紧接着按 ↓ 时
        // 把焦点抢回标题行（「往下按跳回分类」的根因）。标题行此刻必然存在，
        // 一次 focus 即可，无需兜底重试。
        function focusHeaderNow(id) {
            var node = headerRefs.current[id];
            if (!node && typeof document !== "undefined") {
                node = document.getElementById("fp-sec-" + id);
            }
            focusDomNode(node, { force: true });
        }

        var current = state ? state.family : null;
        var favorites = state && Array.isArray(state.favorites) ? state.favorites : [];
        var history = state && Array.isArray(state.history) ? state.history : [];

        // 方案 D：剔字失败回退原始 face 时给可见警告（绝不静默）
        var stripFallback = !!(state && state.strip && state.strip.fallback);

        // 字体列表排序（固定为「最新安装」，无切换）：
        // 非系统字体（is_system 缺省 false）按 install_mtime（face 文件 mtime 最小值，秒）新→旧排在前，
        // 同 mtime 按族名字母序；系统字体（/usr/share、/usr/local/share）垫底并保持后端默认序
        // （后端已按 zh 优先+字母排好，Array.sort 稳定，相等元素顺序不变）。
        var sortedFonts = useMemo(function () {
            var list = Array.isArray(fonts) ? fonts.slice() : [];
            list.sort(function (a, b) {
                var sa = a && a.is_system ? 1 : 0;
                var sb = b && b.is_system ? 1 : 0;
                if (sa !== sb) return sa - sb;
                var ta = (a && a.install_mtime) || 0;
                var tb = (b && b.install_mtime) || 0;
                if (ta !== tb) return tb - ta;
                var na = String((a && a.family) || "").toLowerCase();
                var nb = String((b && b.family) || "").toLowerCase();
                return na < nb ? -1 : na > nb ? 1 : 0;
            });
            return list;
        }, [fonts]);

        var searchMode = query.trim().length > 0;

        var filtered = useMemo(function () {
            var q = query.trim().toLowerCase();
            var full = Array.isArray(sortedFonts) ? sortedFonts : [];
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
        }, [sortedFonts, query]);

        var categories = useMemo(function () {
            var full = Array.isArray(sortedFonts) ? sortedFonts : [];
            var byName = {};
            for (var i = 0; i < full.length; i++) byName[full[i].family] = full[i];

            var fav = [];
            for (var f = 0; f < favorites.length; f++) {
                if (byName[favorites[f]]) fav.push(byName[favorites[f]]);
            }

            // 历史字体：后端按最近使用时间排好序（最新在前），这里只做映射；
            // 已卸载的字体跳过（byName 里没有就不进 hist）
            var hist = [];
            for (var hi = 0; hi < history.length; hi++) {
                if (byName[history[hi]]) hist.push(byName[history[hi]]);
            }

            // 四个基础分类只按字形风格判定（Mono → Serif → Sans → 其他），四类互斥；
            // CJK 字体不再从基础类里剔除，语言归属完全交给语言胶囊处理
            var kinds = ["serif", "sans", "mono", "other"];
            var buckets = {};
            for (var k = 0; k < kinds.length; k++) buckets[kinds[k]] = [];
            for (var j = 0; j < full.length; j++) {
                var font = full[j];
                var monofl = isMono(font);
                var isSerifFont = isSerif(font);
                var kind = monofl ? "mono" : isSerifFont ? "serif" : isSans(font) ? "sans" : "other";
                buckets[kind].push(font);
            }

            var BASE_NAME = { serif: t("cat.serif"), sans: t("cat.sans"), mono: t("cat.mono"), other: t("cat.other") };
            var BASE_NOTE = {
                serif: t("note.serif"),
                sans: t("note.sans"),
                mono: t("note.mono"),
                other: t("note.other"),
            };
            var cats = [
                { id: "all", name: t("cat.all"), items: full, kind: "all", note: null },
                {
                    id: "fav", name: t("cat.fav"), items: fav, kind: "fav",
                    // 空态文案去掉，无论 0 款还是有收藏都固定显示这句操作提示
                    note: t("note.fav"),
                },
                {
                    id: "history", name: t("cat.history"), items: hist, kind: "history",
                    note: hist.length > 0 ? t("note.history") : t("history.empty"),
                },
            ];
            for (var c = 0; c < kinds.length; c++) {
                cats.push({
                    id: kinds[c],
                    name: BASE_NAME[kinds[c]],
                    items: buckets[kinds[c]],
                    kind: kinds[c],
                    note: BASE_NOTE[kinds[c]],
                });
            }

            // 四个基础分类与「全部」带语言胶囊；收藏与历史不带（胶囊条件天然跳过这两个 id）。
            // 计数跟分类一起算好并缓存，点胶囊时只做渲染期过滤，不重跑分类
            for (var p = 0; p < cats.length; p++) {
                var pillCat = cats[p];
                if (pillCat.id !== "serif" && pillCat.id !== "sans" && pillCat.id !== "mono" &&
                    pillCat.id !== "other" && pillCat.id !== "all") continue;
                var langCounts = { latin: 0, zh: 0, ja: 0, ko: 0 };
                for (var q = 0; q < pillCat.items.length; q++) langCounts[primaryLang(pillCat.items[q])] += 1;
                pillCat.langCounts = langCounts;
            }

            // 0 款的分类整块隐藏（标题也不渲染）；历史、收藏夹与「全部字体」永远保留
            return cats.filter(function (cat) {
                return cat.id === "fav" || cat.id === "history" || cat.id === "all" || cat.items.length > 0;
            });
        }, [sortedFonts, favorites, history]);

        // 该分区是否真的有可切的胶囊：收藏没有 langCounts，搜索行没有 sectionId，
        // 折叠态不渲染胶囊行，这几种情况都要把左右键交还 Steam
        function hasLangPills(sectionId) {
            if (!sectionId || collapsed[sectionId] !== false) return false;
            for (var i = 0; i < categories.length; i++) {
                if (categories[i].id === sectionId) return !!categories[i].langCounts;
            }
            return false;
        }

        // 只在整体性故障时出现的单行提示；某一路未注入已由卡内摘要行表达，不再重复
        var problemText = null;
        if (state && state.error) {
            problemText = String(state.error);
        }

        // 注入历史摘要：只读后端记录的「最近一次注入」结果，不做任何实时检测
        var summary = (function () {
            if (!state) return null;
            var level = state.inject_level || "none";
            var stamp = state.inject_text || "";
            if (level === "done") return { text: t("injectDone", stamp), color: "#8ab4f8" };
            if (level === "healed") return { text: t("injectHealed", stamp), color: "#8fd4a8" };
            if (level === "failed") return { text: t("injectFailed"), color: "#ff9b90" };
            return { text: t("injectNone"), color: "rgba(255,255,255,0.55)" };
        })();

        // 自动补注入（UI 模式变化触发的窗口注入）单独记账，只有后端有记录时才显示第二行；
        // 主行已是 healed（补注即最近生效注入）时不重复显示
        var healSummary = (state && state.heal_text && state.inject_level !== "healed")
            ? { text: t("injectHeal", state.heal_text) }
            : null;

        // 「当前字体」卡片：标题 / 槽位摘要 / 三行状态 / 刷新图标合并为一个可聚焦容器，
        // 不再折叠、所有信息常显；确认键 / 点击 = 重新注入当前字体
        var currentSection = h(deckyFrontendLib.PanelSection, { title: undefined },
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:card" },
                h(deckyFrontendLib.Focusable, {
                    id: "fp-targets-reapply",
                    tabIndex: 0,
                    className: "fp-flat",
                    focusClassName: "fp-row-focus",
                    onActivate: reapplyFont,
                    onClick: reapplyFont,
                    onOKActionDescription: t("reapply"),
                    style: {
                        display: "flex",
                        alignItems: "center",
                        boxSizing: "border-box",
                        width: "100%",
                        // 不加水平留白与底色：三行 PanelSectionRow 与原始版本逐字节相同，
                        // 这层 Focusable 只做透明的点击/聚焦命中区；上下 6px 给聚焦效果留出呼吸空间
                        padding: "6px 0",
                        borderRadius: "4px",
                        outline: "none",
                        cursor: current ? "pointer" : "default",
                        transition: "background 80ms linear",
                    },
                },
                    // 左侧纵向列：顶部行（标题 + 槽位摘要）+ 三行状态
                    h("div", {
                        style: {
                            flex: "1 1 auto",
                            minWidth: 0,
                            display: "flex",
                            flexDirection: "column",
                        },
                    },
                        h("div", {
                            style: {
                                display: "flex",
                                alignItems: "center",
                                gap: "8px",
                                padding: "0 12px",
                            },
                        },
                            // 左侧：字体名块（原右侧纵列整体挪入，左对齐；字号粗细不变）
                            h("div", {
                                className: "fp-current-name",
                                style: {
                                    flex: "1 1 auto",
                                    minWidth: 0,
                                    display: "flex",
                                    flexDirection: "column",
                                    alignItems: "flex-start",
                                    lineHeight: "1.25",
                                },
                            },
                                // 槽位区：splitOn 开启时显示「西文 / 中日韩」两行带前缀；
                                // 关闭时只渲染一行纯字体名（无前缀、无置灰）
                                splitOn ? h("span", {
                                    style: {
                                        maxWidth: "100%",
                                        whiteSpace: "nowrap",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        fontWeight: 400,
                                        fontSize: "12px",
                                    },
                                }, t("slotLatin") + t("colon") + (current || t("notSelected"))) : null,
                                splitOn ? h("span", {
                                    style: {
                                        maxWidth: "100%",
                                        whiteSpace: "nowrap",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        fontWeight: 400,
                                        fontSize: "12px",
                                        opacity: 0.72,
                                    },
                                }, t("slotCjk") + t("colon") + (state && state.family_cjk ? state.family_cjk : t("followSystem"))) : h("span", {
                                    style: {
                                        maxWidth: "100%",
                                        whiteSpace: "nowrap",
                                        overflow: "hidden",
                                        textOverflow: "ellipsis",
                                        fontWeight: 400,
                                        fontSize: "12px",
                                    },
                                }, current || t("notSelected"))
                            ),
                        ),
                        // 状态块：三行状态与摘要纵列横向排列，摘要与三行垂直居中
                        h("div", { style: { display: "flex", alignItems: "center", justifyContent: "flex-start", padding: "0 12px" } },
                            // 摘要纵列：居中显示自动/手动注入的提示
                            h("div", {
                                style: {
                                    flex: "0 1 auto",
                                    minWidth: 0,
                                    display: "flex",
                                    flexDirection: "column",
                                    alignItems: "flex-start",
                                    textAlign: "left",
                                    lineHeight: "1.4",
                                },
                            },
                                summary
                                    ? h("span", {
                                        className: "fp-current-summary",
                                        style: {
                                            maxWidth: "100%",
                                            // 不截断：目标多 / 名字长时整体换行，卡片高度自适应
                                            whiteSpace: "normal",
                                            overflow: "visible",
                                            wordBreak: "break-word",
                                            textAlign: "left",
                                            fontSize: "11px",
                                            lineHeight: "1.4",
                                            fontWeight: 600,
                                            color: summary.color,
                                        },
                                    }, summary.text)
                                    : null,
                                healSummary
                                    ? h("span", {
                                        className: "fp-current-heal",
                                        style: {
                                            maxWidth: "100%",
                                            whiteSpace: "normal",
                                            overflow: "visible",
                                            wordBreak: "break-word",
                                            textAlign: "left",
                                            fontSize: "11px",
                                            lineHeight: "1.4",
                                            fontWeight: 400,
                                            color: "rgba(255,255,255,0.55)",
                                        },
                                    }, healSummary.text)
                                    : null
                            )
                        )
                    ),
                            // 刷新图标：卡片右侧，相对整张卡片垂直居中，放大到 26px
                            h("span", {
                                title: t("reapply"),
                                style: {
                                    flexShrink: 0,
                                    display: "inline-flex",
                                    alignItems: "center",
                                    marginLeft: "8px",
                                    // 距右缘留出空隙，避免图标贴边被裁
                                    marginRight: "14px",
                                    opacity: 0.8,
                                },
                            },
                                h("svg", {
                                    viewBox: "0 0 24 24", width: "26", height: "26",
                                    fill: "#c3c8cf", "aria-hidden": true,
                                },
                                    h("path", { d: "M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" }))
                            )
                )
            ),
            // 异常提示不属于可折叠内容：任何折叠状态下都常显
            problemText
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:problem" },
                    h("div", {
                        style: {
                            color: "#ff9b90",
                            fontSize: "12px",
                            fontWeight: 600,
                            // 长错误信息完整换行显示，不截断、不溢出
                            whiteSpace: "normal",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                            lineHeight: "1.45",
                        },
                    }, problemText))
                : null,
            // 方案 D 剔字失败回退：可见警告，绝不静默
            stripFallback
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:strip" },
                    h("div", {
                        style: {
                            color: "#ff9b90",
                            fontSize: "11px",
                            whiteSpace: "normal",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                            lineHeight: "1.45",
                        },
                    }, t("stripWarn")))
                : null,
            message && message !== problemText
                ? h(deckyFrontendLib.PanelSectionRow, null,
                    h("div", { style: { color: "#ff8a80", whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" } }, message))
                : null
        );

        // 字体行：渲染逻辑已提到模块层 FontRow（React.memo 化），此处只组 props。
        // key 移到 FontRow 元素上；isFav/inUse 在父级算好传原始值，让比较器保持浅比较。
        function fontRow(font, alias, sectionId) {
            return h(FontRow, {
                key: font.family,
                font: font,
                alias: alias,
                sectionId: sectionId,
                busy: busy,
                isFav: favorites.indexOf(font.family) !== -1,
                // 一款字体可同时命中两个槽位（西文 + 中日韩），v1 合并显示一个「使用中」
                inUse: font.family === current || !!(state && state.family_cjk === font.family),
                previewOn: previewOn,
                previewLevel: previewLevel,
                expandLevel: expandLevel,
                noGlyph: !!(noGlyphState && noGlyphState[font.family]),
                api: rowApiRef,
            });
        }

        // 焦点视觉由 DOM 类驱动：Focusable 聚焦时由 Steam 加上 focusClassName，
        // 样式写在注入的 <style> 里，不用 React 状态判断（避免 blur 丢失导致状态卡住）
        function sectionHeader(cat, count) {
            var isCollapsed = collapsed[cat.id] !== false;
            var total = typeof count === "number" ? count : cat.items.length;
            // 只在用户显式点过语言胶囊时才在标题右侧显示筛选名；
            // pickedLang 有默认值（未选也算西文），不能直接用，否则永远带筛选名
            var picked = (cat.langCounts && langPick[cat.id]) ? langPick[cat.id] : "";
            return h(deckyFrontendLib.PanelSectionRow, { key: cat.id + ":header" },
                h(deckyFrontendLib.Focusable, {
                    id: "fp-sec-" + cat.id,
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    ref: function (el) { headerRefs.current[cat.id] = el; },
                    onActivate: function () { toggleSection(cat.id); },
                    onClick: function () { toggleSection(cat.id); },
                    onButtonDown: function (evt) {
                        var btn = buttonOf(evt);
                        // 左右键切换本分类的语言：只在展开态且本行确实有胶囊时才接管，
                        // 其余情况返回 false 且不 stopPropagation，把方向键交还 Steam 的焦点导航
                        if (!cat.langCounts || isCollapsed) return false;
                        if (btn !== BTN_LEFT && btn !== BTN_RIGHT) return false;
                        cycleLang(cat.id, btn === BTN_RIGHT ? 1 : -1);
                        evt.stopPropagation();
                        return true;
                    },
                    onOKActionDescription: isCollapsed ? t("expand") : t("collapse"),
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
                    }, (picked ? t("lang." + picked) + " · " : "") + total + t("fontsCount"))
                )
            );
        }

        // 原生 gpfocus 落不到自绘的标题 div 上，所以焦点视觉仍由 DOM 类驱动：
        // 基础态负责未聚焦，.fp-row-focus（Steam 加的类）与 :focus 兜底负责聚焦态
        var focusCss =
            ".fp-sec{background:rgba(255,255,255,0.06)!important;" +
            "border-left:3px solid transparent!important;color:#c3c8cf!important;" +
            "box-shadow:none!important;}" +
            // 状态卡变体：无常驻底色，聚焦时才出现反馈
            ".fp-flat{background:transparent!important;" +
            "border-left:3px solid transparent!important;color:#c3c8cf!important;" +
            "box-shadow:none!important;}" +
            ".fp-sec .fp-arrow{color:rgba(255,255,255,0.55)!important;background:transparent!important;" +
            "border:0!important;box-shadow:none!important;}" +
            ".fp-sec .fp-count{color:rgba(255,255,255,0.55)!important;}" +
            ".fp-sec.fp-row-focus,.fp-sec:focus," +
            ".fp-flat.fp-row-focus,.fp-flat:focus{" +
            "background:rgba(255,255,255,0.16)!important;" +
            "border-left:3px solid #66c0f4!important;" +
            "box-shadow:inset 0 0 0 1px #66c0f4!important;" +
            "color:#ffffff!important;}" +
            ".fp-sec.fp-row-focus .fp-arrow,.fp-sec:focus .fp-arrow{color:#ffffff!important;}" +
            ".fp-sec.fp-row-focus .fp-count,.fp-sec:focus .fp-count{color:#e6f3fb!important;}" +
            // 语言胶囊行：四等分、窄屏不换行；分隔线若由伪元素画出来，只在本行内压掉
            ".fp-lang-row:before,.fp-lang-row:after," +
            ".fp-lang-row>:first-child:before,.fp-lang-row>:first-child:after{" +
            "content:none!important;display:none!important;height:0!important;" +
            "border:0!important;background:transparent!important;box-shadow:none!important;}" +
            // 胶囊行整体下移 10px，和分类标题拉开距离
            ".fp-lang-bar{display:flex;flex-wrap:nowrap;gap:6px;width:100%;min-width:0;margin-top:10px;}" +
            // 竖排两行：标签在上、计数在下；不再用省略号截断
            ".fp-lang-pill{flex:1 1 0;min-width:0;box-sizing:border-box;height:36px;" +
            "padding:3px 4px;border-radius:13px;background:rgba(255,255,255,0.07);" +
            "color:rgba(255,255,255,0.62);font-size:11px;font-weight:500;text-align:center;" +
            "display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;" +
            "cursor:pointer;user-select:none;transition:background 80ms linear,color 80ms linear;}" +
            ".fp-lang-pill .fp-lang-label{display:block;white-space:nowrap;font-size:11px;line-height:14px;}" +
            ".fp-lang-pill .fp-lang-count{display:block;white-space:nowrap;font-size:10px;line-height:12px;" +
            "font-weight:500;opacity:0.7;font-variant-numeric:tabular-nums;}" +
            ".fp-lang-pill:hover{background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.82);}" +
            // 选中态只借用面板现有的中性高亮
            ".fp-lang-pill.fp-lang-on,.fp-lang-pill.fp-lang-on:hover{" +
            "background:rgba(255,255,255,0.22)!important;color:#ffffff!important;font-weight:600;}" +
            ".fp-lang-pill.fp-lang-on .fp-lang-count{opacity:0.95;font-weight:600;}" +
            // 方案 B：常规 + 粗体双样张
            // 关键：选择器 html body .fp-prev 特异性 (0,1,2) 高于后端 html body * (0,0,2)，
            // 同为 !important 时本规则胜出，不依赖注入先后顺序。
            ".fp-frow{width:100%;text-align:left!important;}" +
            ".fp-fname{font-size:13px!important;font-weight:600!important;color:#ffffff!important;" +
            "line-height:1.35!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important;}" +
            // 视觉层级（方案 B）：统一左对齐轴 + 字体名做主角。字体名放大到 16px 纯白、
            // 样张退到 15px 灰（见下），让「哪个是名字 / 哪个是预览」一眼可分。
            // 只作用于字体列表行的字体名；档位标签已由原生 SliderField 渲染，不走这里。
            // 字体名自动换行：覆盖基线 nowrap/省略号，长名可折行（样张仍保持单行省略）。
            "html body .fp-font-row .fp-fname{font-size:16px!important;letter-spacing:0.3px!important;text-align:left!important;" +
            "white-space:normal!important;overflow:visible!important;text-overflow:clip!important;overflow-wrap:anywhere!important;}" +
            // 名字渲染自身字体：@font-face（极小子集）按需注入后逐字符生效；缺字形
            // 字符回退后面的 UI 默认字体链（= 继承链，实测 Motiva Sans…），观感与
            // 旧版一致，无豆腐。注意 fallback 不能写 inherit——CSS-wide 关键字混进
            // 族名列表会让整条声明在计算值阶段作废（首个实现踩坑：名字不换字体）。
            // 特异性 (0,1,2) 高于后端注入的 html body * (0,0,2)，与 .fp-prev 同策略。
            "html body .fp-nm{font-family:var(--fp-family),Motiva Sans,Arial,Helvetica,sans-serif!important;}" +
            "html body .fp-prev,html body .fp-prev-b{" +
            "font-family:var(--fp-family)!important;font-size:15px!important;line-height:1.4!important;" +
            "color:rgba(195,200,207,0.72)!important;white-space:nowrap!important;overflow:hidden!important;" +
            "text-overflow:ellipsis!important;}" +
            "html body .fp-prev{margin-top:9px!important;}" +
            "html body .fp-prev-b{font-weight:700!important;font-size:14px!important;" +
            "color:rgba(195,200,207,0.6)!important;margin-top:2px!important;" +
            "border-top:1px solid rgba(255,255,255,0.06)!important;padding-top:5px!important;}" +
            "html body .fp-badge{font-size:11px!important;font-weight:600!important;" +
            "letter-spacing:0.5px!important;color:rgba(138,180,248,0.95)!important;}" +
            // 聚焦态配色（方案 A）：不再迁就 ButtonItem 的白底去写深色字，而是直接把白底
            // 压成深灰蓝半透明高亮 + #66c0f4 蓝框，与分区标题行 .fp-sec/.fp-flat 的聚焦
            // 样式（见 1649-1654 行）完全统一；字恢复默认浅色，在深底上清晰可读、不再刺眼。
            //
            // 【为什么直接选中 gpfocus 按钮自身，且为什么一定命中的是那一个按钮】
            // DFL 源码 ButtonItem.ts：ButtonItem 从 Steam CommonUIModule 取出，其
            // ItemProps（Item.ts）带 highlightOnFocus——聚焦时由 Steam 给该按钮元素加
            // .gpfocus 类并画高亮白底（即 Gaming Mode 的 "hover"；社区实证如 decky-nexus
            // 注释 "gpfocus is Steam's gamepad-focus class"，同平台通用压法就是
            // .xxx.gpfocus{background:...!important}）。所以白底是 gpfocus 按钮【自身】的
            // background，必须压它本身，而不是改后代文字颜色。
            // ButtonItem 不接受 focusClassName、不转发 onFocus/onBlur（只有 Focusable 支持），
            // 且按钮是 .fp-font-row 的【祖先】而非自身/后代 → 旧的
            // :focus-within 写法永不命中，已废弃；上一版仅把字改深色是"看得见但刺眼"，
            // 真正的问题是这块白底本身。
            // :has(.fp-font-row) 只命中含对应行的那一个按钮，
            // 不波及列表其它行或其它控件；Steam CEF 支持 :has（项目早有 :has 先例）。
            // 两个档位滑块已改用原生 SliderField，自带 Steam 聚焦态与高亮，
            // 不再需要本规则压白底，故移除旧自绘档位滑块的匹配分支。
            "html body .gpfocus:has(.fp-font-row){" +
            "background:rgba(255,255,255,0.16)!important;" +
            "border-left:3px solid #66c0f4!important;" +
            "box-shadow:inset 0 0 0 1px #66c0f4!important;" +
            "color:#ffffff!important;}" +
            // 兜底：若某些 Steam 版本把高亮画在按钮伪元素上，一并透明化，确保白底彻底消失
            "html body .gpfocus:has(.fp-font-row):before," +
            "html body .gpfocus:has(.fp-font-row):after{" +
            "background:transparent!important;box-shadow:none!important;}" +
            // 方案 A：单行紧凑样张
            "html body .fp-prev-a{font-size:15px!important;margin-top:4px!important;}" +
            // C 方案展开块：默认隐藏（display:none 不触发字体加载），聚焦按钮时展开；
            // 用同一套 gpfocus 祖先机制（旧的 .fp-frow:focus-within 从未触发，已移除）。
            // 纯 CSS 驱动，不经过 React 状态，避免 blur 丢失导致展开状态卡住
            "html body .fp-ex{display:none!important;margin-top:6px!important;" +
            "border-top:1px solid rgba(255,255,255,0.06)!important;padding-top:7px!important;}" +
            "html body .gpfocus .fp-font-row .fp-ex{display:block!important;}" +
            "html body .fp-ex-line{font-family:var(--fp-family)!important;font-size:18px!important;" +
            "line-height:1.4!important;color:rgba(195,200,207,0.92)!important;white-space:nowrap!important;" +
            "overflow:hidden!important;text-overflow:ellipsis!important;}" +
            "html body .fp-ex-lat{font-size:16px!important;}" +
            "html body .fp-ex-label{display:inline-block;width:30px!important;" +
            "font-family:\"Fira Sans\",\"Noto Sans CJK SC\",sans-serif!important;" +
            "font-size:11px!important;font-weight:600!important;letter-spacing:0.5px!important;" +
            "color:rgba(138,180,248,0.95)!important;}" +
            // 描述行注脚化：ButtonItem layout=below 的 description 由 Steam Item 组件渲染在
            // 卡片体外、Field 内，原生样式悬在卡下显得游离。调成左缩进 18px（对齐卡片水平
            // 内边距）、12px、弱化灰，成为卡片注脚。
            "html body .gamepaddialog_Field:has(.fp-font-row) .gamepaddialog_FieldDescription{" +
            "margin:6px 0 0 18px!important;font-size:12px!important;" +
            "color:rgba(235,235,245,0.62)!important;}" +
            // 行间呼吸感：字体行上下各 6px。
            "html body .gamepaddialog_Field:has(.fp-font-row){" +
            "margin-top:6px!important;margin-bottom:6px!important;}";

        // 「加载更多」行：沿用标题行的样式与 focusClassName 聚焦方案，只负责加长列表
        function loadMoreRow(remaining, key, moreKey) {
            return h(deckyFrontendLib.PanelSectionRow, { key: key },
                h(deckyFrontendLib.Focusable, {
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    onActivate: function () { loadMore(moreKey); },
                    onClick: function () { loadMore(moreKey); },
                    onOKActionDescription: t("loadMore"),
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
                }, t("loadMoreRemaining", remaining))
            );
        }

        // 语言胶囊行：四个语言等宽小胶囊横向铺满，数量取分类时算好的计数。
        // 选中只影响本分区；再点已选中的胶囊保持选中（没有「取消筛选」这个空态）。
        // 计数超过 99 一律显示 99+，避免两位数以上把竖排胶囊撑破
        function fmtCount(n) {
            return n > 99 ? "99+" : String(n);
        }

        function langPill(cat, key, picked, count) {
            return h("div", {
                key: key,
                className: "fp-lang-pill" + (picked === key ? " fp-lang-on" : ""),
                role: "button",
                "aria-pressed": picked === key ? "true" : "false",
                onClick: function () { applyLangPick(cat.id, key); },
            },
                h("span", { className: "fp-lang-label" }, t("lang." + key)),
                h("span", { className: "fp-lang-count" }, fmtCount(count))
            );
        }

        function langRow(cat) {
            var picked = pickedLang(cat.id);
            var counts = cat.langCounts || {};
            var pills = [];
            for (var i = 0; i < LANG_KEYS.length; i++) {
                var key = LANG_KEYS[i];
                pills.push(langPill(cat, key, picked, counts[key] || 0));
            }
            return h(deckyFrontendLib.PanelSectionRow, { key: cat.id + ":lang", className: "fp-lang-row" },
                h("div", { className: "fp-lang-bar" }, pills));
        }

        function categoryRows(cat) {
            // 过滤放在渲染期：不进入 categories 的 useMemo 依赖，点胶囊不重算分类
            // 有胶囊的分区默认就是西文，没有「不筛选」的分支
            var picked = cat.langCounts ? pickedLang(cat.id) : "";
            var items = cat.items;
            if (picked) {
                items = [];
                for (var fi = 0; fi < cat.items.length; fi++) {
                    if (primaryLang(cat.items[fi]) === picked) items.push(cat.items[fi]);
                }
            }
            var rows = [sectionHeader(cat, items.length)];
            if (collapsed[cat.id] !== false) return rows;
            if (cat.note) rows.push(noteRow(cat.note, cat.id + ":note"));
            // 「全部字体」没有说明行，补一条纯布局间距把标题与第一条字体分开；
            // 不可聚焦、不参与键盘遍历
            if (cat.id === "all" && cat.items.length) rows.push(gapRow("cat:all:gap"));
            if (cat.langCounts) rows.push(langRow(cat));
            if (items.length) {
                var moreKey = "cat:" + cat.id;
                var limit = limitOf(moreKey);
                var shown = items.slice(0, limit);
                for (var i = 0; i < shown.length; i++) rows.push(fontRow(shown[i], null, cat.id));
                if (items.length > limit) {
                    rows.push(loadMoreRow(items.length - limit, cat.id + ":more", moreKey));
                }
            } else if (picked) {
                // 该语言在本分类下为 0：给一行灰字提示，而不是空白列表
                rows.push(noteRow(t("lang.empty", t("lang." + picked)), cat.id + ":lang-empty"));
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
                searchRows.push(noteRow(t("noResult"), "no-result"));
            }
        }

        var countText = searchMode
            ? t("totalFonts", filtered.total)
            : t("totalFonts", sortedFonts.length);

        // 不再渲染「选择字体」纯文本小标题：搜索框直接接在折叠行之后
        var pickerSection = h(deckyFrontendLib.PanelSection, { title: undefined },
            h(deckyFrontendLib.PanelSectionRow, null,
                h(deckyFrontendLib.TextField, {
                    label: t("searchFonts"),
                    value: query,
                    onChange: function (e) {
                        setQuery(e && e.target ? e.target.value : e);
                    },
                })
            ),
            h(deckyFrontendLib.PanelSectionRow, null,
                h("div", { style: { opacity: 0.6, fontSize: "12px" } },
                    countText + (searchMode ? t("clearSearchHint") : ""))
            ),
            searchMode ? searchRows : []
        );

        var categorySections = searchMode ? [] : categories.map(function (cat) {
            return h(deckyFrontendLib.PanelSection, { key: "cat:" + cat.id, title: undefined },
                categoryRows(cat));
        });

        // 收藏由字体行 X 负责、重新注入由字体行 A 负责；这里保留恢复默认
        var actionSection = h(deckyFrontendLib.PanelSection, null,
            h(deckyFrontendLib.PanelSectionRow, null,
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    // 西文或中日韩任一槽有字体即可恢复；只有两槽都空时才禁用
                    disabled: busy || (!current && !(state && state.family_cjk)),
                    onClick: reset,
                }, t("restoreDefault"))
            )
        );

        // 高级分区三个开关统一用 Decky 原生 ToggleField：标题在左、开关在右，
        // 说明文字作为 description 挂在标题下方，行间分隔线由 PanelSectionRow 负责。
        // useToggleRail 让开关显示为 Steam 新 UI 的小胶囊滑块（非旧版 On/Off 分段按钮）。
        // 状态后缀（：已开启 / ：已关闭）不再写进标题，由开关本身表达。
        var advancedSection = h(deckyFrontendLib.PanelSection, { title: t("advanced") },
            cefInfo.error
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:error" },
                    h("div", { style: { color: "#ff9b90", fontSize: "10px", lineHeight: "1.5", whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" } }, t("cefNoPort"))
                )
                : null,
            !cefInfo.error
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:ok" },
                    h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5" } },
                        t("cefPortInfo", cefInfo.port || "?", cefInfo.tabs == null ? "?" : cefInfo.tabs))
                )
                : null,
            // woff2 不可用提示：后端探测系统 Python 缺 brotli 时（payload.woff2_ok === false），
            // woff2 压缩步骤会失败并自动回退注入 ttf。信息行样式沿用 cefPortInfo 行，
            // 换行属性取自 cefNoPort 错误行（长文案在窄面板能折行）。
            state && state.woff2_ok === false
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-woff2:fallback" },
                    h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5", whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" } }, t("woff2Fallback"))
                )
                : null,
            // 预览缓存字节上限（MB）：原生数值滑块 1-30，默认 8，显示实际数值（非档位 notch 模式）。
            // 纯缓存设置，走 setCacheMb（本地 state 立即更新，拖动不回弹）。
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-cache:row" },
                h(deckyFrontendLib.SliderField, {
                    value: previewCacheMb, min: 1, max: 30, step: 1,
                    showValue: true, validValues: "steps", minimumDpadGranularity: 1,
                    label: t("previewCacheTitle"),
                    description: t("previewCacheNote"),
                    layout: "below",
                    onChange: function (v) { setCacheMb(v); },
                })
            ),
            // 字体预览样张档位滑块：0 关闭 / 1 方案 A / 2 方案 B
            h(TierSliderRow, { key: "fp-preview:row", label: t("previewSamples"), note: t("previewNote"), level: previewLevel,
                setLevel: function (v) { setLevels({ preview_level: v }); },
                notches: [t("levelOff"), t("notchA"), t("notchB")] }),
            // C 方案聚焦展开开关：0 关闭 / 1 叠加（旧 2 档内嵌已移除，旧值迁移为开启）
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-expand:row" },
                h(deckyFrontendLib.ToggleField, {
                    label: t("previewExpand"),
                    description: t("previewExpandNote"),
                    checked: expandLevel > 0,
                    disabled: busy,
                    useToggleRail: true,
                    onChange: function () { setLevels({ expand_level: expandLevel > 0 ? 0 : 1 }); },
                })
            ),
            // 禁用假加粗：已独立于分槽主开关，无条件显示
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:synthrow" },
                h(deckyFrontendLib.ToggleField, {
                    label: t("subSynth"),
                    description: t("subSynthNote"),
                    checked: synthNone,
                    disabled: busy,
                    useToggleRail: true,
                    onChange: toggleSynth,
                })
            ),
            // 分槽注入主开关：开启后西文 / 中日韩两槽位各自选字体，确认键按语言归属落槽
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:row" },
                h(deckyFrontendLib.ToggleField, {
                    label: t("splitMode"),
                    description: t("splitNote"),
                    checked: splitOn,
                    disabled: busy,
                    useToggleRail: true,
                    onChange: toggleSplit,
                })
            ),
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-history:clear" },
                h("div", { style: { marginTop: "10px" } },
                    h(deckyFrontendLib.ButtonItem, { layout: "below", onClick: clearHistory }, t("clearHistory")))
            ),
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:copy" },
                // 与上方说明行拉开 10px，固定值，不随反馈文字出现/消失变化
                h("div", { style: { marginTop: "10px" } },
                    h(deckyFrontendLib.ButtonItem, {
                        layout: "below",
                        onClick: copyLog,
                    }, t("copyLog"))
                )
            ),
            copyMsg
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:copymsg" },
                    h("div", {
                        style: copyMsg.indexOf(t("copyFail")) === 0
                            ? { color: "#ff9b90", fontSize: "11px", lineHeight: "1.5" }
                            : { opacity: 0.55, fontSize: "11px", lineHeight: "1.5" },
                    }, copyMsg)
                )
                : null
        );

        // 确认键菜单：标题 + 「应用西文 / 应用中文」两选项 + 取消提示。
        // 覆盖提示：不含 CJK 字形的字体「应用中文」标注「该字体不含中文字形」（不禁用）；
        // 自带 CJK 的字体「应用西文」标注「仅取其西文字形（自动剔除汉字）」。
        // B 键 / 点空白处关闭，不写任何配置
        function confirmMenu() {
            if (!menuFont) return null;
            var family = menuFont.family;
            var hasCjk = !!(menuFont.zh || menuFont.zh_extra || menuFont.ja || menuFont.ko);
            var latinDesc = hasCjk ? t("descLatinStrip") : undefined;
            var cjkDesc = hasCjk ? undefined : t("descCjkNone");
            var option = function (key, id, refSetter, label, desc, slot) {
                return h(deckyFrontendLib.Focusable, {
                    key: key,
                    id: id,
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    ref: refSetter,
                    onActivate: function () { setMenuFont(null); applyToSlot(slot, family); },
                    onClick: function () { setMenuFont(null); applyToSlot(slot, family); },
                    onOKActionDescription: label,
                    onButtonDown: function (evt) {
                        // B = 取消：关闭菜单且不写任何配置
                        if (buttonOf(evt) === BTN_B) {
                            setMenuFont(null);
                            evt.stopPropagation();
                            return true;
                        }
                        return false;
                    },
                    style: {
                        display: "block",
                        padding: "9px 12px",
                        borderRadius: "4px",
                        outline: "none",
                        fontSize: "13px",
                        lineHeight: "1.4",
                        transition: "background 80ms linear",
                    },
                },
                    h("div", null, label),
                    desc
                        ? h("div", { style: { opacity: 0.55, fontSize: "10px", marginTop: "2px" } }, desc)
                        : null
                );
            };
            return h("div", {
                key: "fp-confirm-menu",
                onClick: function () { setMenuFont(null); },
                style: {
                    position: "fixed", left: 0, top: 0, right: 0, bottom: 0,
                    zIndex: 2147483647, background: "rgba(0,0,0,0.55)",
                },
            },
                h("div", {
                    style: {
                        // 定位：左锚定 left 44px；宽度自适应内容，上限 92% 防右缘溢出
                        position: "absolute", left: "44px", top: "38%",
                        transform: "translateY(-50%)",
                        maxWidth: "92%", boxSizing: "border-box",
                        background: "#1b2027", borderRadius: "8px", padding: "12px",
                        boxShadow: "0 8px 28px rgba(0,0,0,0.55)",
                    },
                },
                    h("div", {
                        title: family,
                        style: { fontSize: "13px", fontWeight: 600, marginBottom: "8px", whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" },
                    }, family),
                    option("latin", "fp-menu-latin", function (el) { menuLatinRef.current = el; },
                        t("menuLatin"), latinDesc, "latin"),
                    option("cjk", "fp-menu-cjk", function (el) { menuCjkRef.current = el; },
                        t("menuCjk"), cjkDesc, "cjk"),
                    h("div", {
                        style: { opacity: 0.5, fontSize: "10px", marginTop: "8px", textAlign: "center" },
                    }, t("menuCancel"))
                )
            );
        }

        return h("div", { ref: function (el) { rootRef.current = el; } },
            h("style", { key: "fp-focus-css" }, focusCss),
            h("style", { key: "fp-preview-font-css", ref: previewStyleRef }),
            currentSection, pickerSection, categorySections, actionSection, advancedSection,
            confirmMenu());
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

    // 模块加载时通知后端一次：只记日志，不做注入（顶层调用，不放进 useEffect）
    try {
        if (window.DeckyBackend && window.DeckyBackend.call) {
            window.DeckyBackend
                .call("loader/call_legacy_plugin_method", "Font Picker", "on_ui_loaded", {})
                .catch(function () {});
        }
    } catch (e) { /* 加载阶段后端未就绪也不影响插件 */ }

    // ===== UI 模式事件：大屏(4) / 桌面(7) 切换时让后端开一次有界注入窗口 =====
    // 只在模块顶层订阅一次（本模块活在 SharedJSContext 里，不会因页面重建重新求值），
    // 没有任何定时器：窗口之外的等待全部由后端的一次性 sleep 承担。
    // RegisterForUIModeChanged 在注册时会立刻回调一次当前值，那一次必须丢掉。
    (function subscribeUiMode() {
        var MODE_GAMEPAD = 4;
        var MODE_DESKTOP = 7;
        var primed = false;
        var last = null;
        try {
            var UI = window.SteamClient && window.SteamClient.UI;
            if (!UI || !UI.RegisterForUIModeChanged) return;
            UI.RegisterForUIModeChanged(function (mode) {
                var value = Number(mode);
                if (!primed) {
                    // 注册时的首次回调就是当前模式，不是变化
                    primed = true;
                    last = value;
                    return;
                }
                if (value !== MODE_GAMEPAD && value !== MODE_DESKTOP) return;
                if (value === last) return;
                last = value;
                try {
                    if (window.DeckyBackend && window.DeckyBackend.call) {
                        window.DeckyBackend
                            .call("loader/call_legacy_plugin_method", "Font Picker", "on_ui_mode_changed", { mode: value })
                            .catch(function () {});
                    }
                } catch (e) { /* 后端未就绪时忽略 */ }
            });
        } catch (e) { /* 订阅失败不影响插件其它功能 */ }
    })();

    var index = deckyFrontendLib.definePlugin(function (serverApi) {
        return {
            title: t("pluginTitle"),
            titleView: h("div", { className: deckyFrontendLib.staticClasses.Title }, t("pluginTitle")),
            content: h(ErrorBoundary, null, h(FontPickerContent, { serverApi: serverApi })),
            icon: h(FontIcon, null),
        };
    });

    return index;
})(DFL, SP_REACT);
