(function (deckyFrontendLib, React) {
    "use strict";

    var h = React.createElement;
    var useState = React.useState;
    var useEffect = React.useEffect;
    var useMemo = React.useMemo;
    var useRef = React.useRef;

    var LIST_LIMIT = 60;

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
            "callFailedDetail": "调用 {0} 失败: {1}",
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
            "cat.all": "全部字体",
            "lang.latin": "西文",
            "lang.zh": "中文",
            "lang.ja": "日文",
            "lang.ko": "韩文",
            "lang.empty": "该分类下没有{0}字体。",
            "note.serif": "只能按 family 名启发式判断（Serif / Song / Ming / Times / 楷 等），名字里含 sans 的排除。",
            "note.sans": "名字里含明确无衬线关键词（sans / grotesk / gothic / 黑 等）。",
            "note.mono": "按 fontconfig 的 spacing 字段判定（100 / 110），或 family 名里含 mono / 等宽。",
            "note.other": "既非等宽、名字里也没有明确衬线 / 无衬线关键词的字体。",
            "note.fav": "聚焦某款字体后按 X（手柄）加入/移出收藏",
            "notInjected": "未注入",
            "injected": "已注入",
            "injectDone": "已完成一次注入 · {0}",
            "injectFailed": "注入失败",
            "injectNone": "尚未注入",
            "injectHeal": "自动补注入 · {0}",
            "injectHealed": "已自动补注入 · {0}",
            "serviceDown": "本地字体服务未启动，字体无法生效",
            "reapply": "重新注入当前字体",
            "currentFont": "当前字体",
            "notSelected": "未选择",
            "expand": "展开",
            "collapse": "收起",
            "langUnknown": "lang 未知",
            "langNotOverridden": "未覆盖界面字体",
            "langMore": " · 等 {0} 种",
            "langHasCjk": " · 含中文字形",
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
            "advanced": "高级",
            "cefFastMode": "CEF 快速模式：{0}",
            "cefOn": "已开启",
            "cefOff": "已关闭",
            "cefNote": "开启前需在 Steam 设置 → 开发者 中打开「CEF 远程调试」。端口不可用时自动回落到常规探测，不影响字体注入。",
            "cefNoPort": "未检测到 CEF 调试端口，已回落常规探测。请检查 Steam 设置 → 开发者 → CEF 远程调试。",
            "cefPortInfo": "调试端口 {0} · {1} 个 tab",
            "copyLog": "复制日志",
            "splitMode": "按语言注入：{0}",
            "splitNote": "开启后西文与中日韩分别选择字体：字体行确认键弹出「应用西文 / 应用中文」菜单选择落槽。关闭则回到单槽模式（确认键直接应用），已选的中日韩字体保留，重新开启自动恢复。",
            "subSynth": "禁用假加粗：{0}",
            "subSynthNote": "没有粗体字重的字体不再合成加粗，中粗文字按常规字重显示。",
            "subPunct": "使用 CJK 标点符号：{0}",
            "subPunctOn": "开 = 使用 CJK 标点符号（全角），影响中英文界面。",
            "subPunctOff": "关 = 使用西文标点符号（默认，窄/比例宽度）。",
            "slotLatin": "西文",
            "slotCjk": "中日韩",
            "followSystem": "跟随系统",
            "stripWarn": "西文字体剔字失败，已回退原始字体，可能遮蔽中日韩槽位（换一款纯西文字体可解决）。",
            "noBoldInfo": "该字体没有粗体字重，中粗文字按常规显示（可在高级里改）",
            "menuTitle": "应用字体：{0}",
            "menuLatin": "应用西文",
            "menuCjk": "应用中文",
            "menuCancel": "按 B 返回取消，不做任何更改",
            "descLatinStrip": "仅取其西文字形（自动剔除汉字）",
            "descCjkNone": "该字体不含中文字形",
            "splitHint": "已将当前字体设为西文槽（仅取其西文字形），建议为中日韩槽另选字体"
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
            "cat.all": "All Fonts",
            "lang.latin": "Latin",
            "lang.zh": "Chinese",
            "lang.ja": "Japanese",
            "lang.ko": "Korean",
            "lang.empty": "No {0} fonts in this category.",
            "note.serif": "Heuristic match on the family name (Serif / Song / Ming / Times / Kai, etc.); names containing 'sans' are excluded.",
            "note.sans": "Family name contains an explicit sans keyword (sans / grotesk / gothic / 黑, etc.).",
            "note.mono": "Determined by fontconfig's spacing field (100 / 110), or by a family name containing mono / 等宽.",
            "note.other": "Neither monospace nor matched by a serif / sans keyword.",
            "note.fav": "Focus a font and press X (gamepad) to add or remove it from favorites",
            "notInjected": "Not injected",
            "injected": "Injected",
            "injectDone": "Injection completed · {0}",
            "injectFailed": "Injection failed",
            "injectNone": "Not injected yet",
            "injectHeal": "Auto re-injection · {0}",
            "injectHealed": "Auto re-injected · {0}",
            "serviceDown": "Local font service is not running; fonts cannot be applied",
            "reapply": "Re-inject the current font",
            "currentFont": "Current font",
            "notSelected": "None selected",
            "expand": "Expand",
            "collapse": "Collapse",
            "langUnknown": "lang unknown",
            "langNotOverridden": "UI font not overridden",
            "langMore": " · and {0} more",
            "langHasCjk": " · Chinese glyphs included",
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
            "advanced": "Advanced",
            "cefFastMode": "CEF fast mode: {0}",
            "cefOn": "On",
            "cefOff": "Off",
            "cefNote": "Before enabling, turn on \"CEF Remote Debugging\" in Steam Settings → Developer. If the port is unavailable, detection falls back to the regular method and font injection is unaffected.",
            "cefNoPort": "No CEF debugging port detected; fell back to regular detection. Check Steam Settings → Developer → CEF Remote Debugging.",
            "cefPortInfo": "Debug port {0} · {1} tabs",
            "copyLog": "Copy log",
            "splitMode": "Split by language: {0}",
            "splitNote": "When on, Latin and CJK fonts are picked separately: pressing confirm in the list slots the font by its language. When off, it returns to a single slot; the CJK font you picked is kept and restored when you turn it back on.",
            "subSynth": "Disable synthetic bold: {0}",
            "subSynthNote": "Fonts without a bold face are no longer emboldened synthetically; medium-weight text uses the regular weight.",
            "subPunct": "Use CJK punctuation: {0}",
            "subPunctOn": "On = full-width CJK punctuation; affects both the Chinese and English UI.",
            "subPunctOff": "Off = Western punctuation (default, narrow / proportional).",
            "slotLatin": "Latin",
            "slotCjk": "CJK",
            "followSystem": "Follow system",
            "stripWarn": "Stripping failed for the Latin font, so the original font was used; it may cover the CJK slot (picking a Latin-only font fixes this).",
            "noBoldInfo": "This font has no bold face, so medium-weight text renders at regular weight (change it in Advanced)",
            "menuTitle": "Apply font: {0}",
            "menuLatin": "Apply to Latin",
            "menuCjk": "Apply to Chinese",
            "menuCancel": "Press B to go back and cancel without changing anything",
            "descLatinStrip": "Latin glyphs only (CJK characters stripped)",
            "descCjkNone": "This font has no Chinese glyphs",
            "splitHint": "The current font is now in the Latin slot (Latin glyphs only); pick a separate font for the CJK slot"
        },
        ja: {
            "pluginTitle": "フォント選択ツール",
            "alias": "別名：{0}",
            "crash": "フォント選択ツールの画面でエラーが発生しましたが、捕捉済みです。他のパネルには影響していません。",
            "noData": "バックエンドからデータが返されませんでした",
            "callFailed": "{0} の呼び出しに失敗しました",
            "callFailedDetail": "{0} の呼び出しに失敗しました: {1}",
            "reading": "読み込み中…",
            "copyFail": "コピーに失敗しました：",
            "logReadFail": "ログの読み込みに失敗しました",
            "copied": "{0} 行をコピーしました（{1}）",
            "latestLog": "最新のログ",
            "clipboardDenied": "クリップボードを利用できません（書き込みが拒否されました）",
            "cat.serif": "Serif",
            "cat.sans": "Sans",
            "cat.mono": "Mono",
            "cat.other": "その他",
            "cat.fav": "お気に入り",
            "cat.all": "すべてのフォント",
            "lang.latin": "欧文",
            "lang.zh": "中国語",
            "lang.ja": "日本語",
            "lang.ko": "韓国語",
            "lang.empty": "この分類には{0}のフォントがありません。",
            "note.serif": "ファミリー名によるヒューリスティック判定です（Serif / Song / Ming / Times / 楷 など）。名前に sans を含むものは除外されます。",
            "note.sans": "名前に明示的なサンセリフのキーワード（sans / grotesk / gothic / 黑 など）を含むものです。",
            "note.mono": "fontconfig の spacing フィールド（100 / 110）で判定します。family 名に mono または 等宽 を含むものも対象です。",
            "note.other": "等幅でもなく、名前にセリフ / サンセリフのキーワードも含まないフォントです。",
            "note.fav": "フォントにフォーカスして X（ゲームパッド）を押すとお気に入りに追加／解除できます",
            "notInjected": "未適用",
            "injected": "適用済み",
            "injectDone": "一度適用しました · {0}",
            "injectFailed": "適用に失敗",
            "injectNone": "まだ適用していません",
            "injectHeal": "自動再適用 · {0}",
            "injectHealed": "自動再適用しました · {0}",
            "serviceDown": "ローカルフォントサービスが起動していないため、フォントを反映できません",
            "reapply": "現在のフォントを再適用",
            "currentFont": "現在のフォント",
            "notSelected": "未選択",
            "expand": "展開",
            "collapse": "折りたたむ",
            "langUnknown": "lang 不明",
            "langNotOverridden": "UI フォントは変更されていません",
            "langMore": " · 他 {0} 言語",
            "langHasCjk": " · 中国語グリフを含む",
            "fontChinese": "中国語対応",
            "fontNoChinese": "中国語なし（自動フォールバック）",
            "boldYes": " · Bold あり",
            "boldNo": " · Bold なし（合成太字）",
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
            "advanced": "詳細設定",
            "cefFastMode": "CEF 高速モード：{0}",
            "cefOn": "オン",
            "cefOff": "オフ",
            "cefNote": "有効にする前に、Steam の設定 → 開発者 で「CEF リモートデバッグ」をオンにしてください。ポートが使用できない場合は通常の検出に自動でフォールバックし、フォントの適用には影響しません。",
            "cefNoPort": "CEF デバッグポートが見つからないため、通常の検出にフォールバックしました。Steam の設定 → 開発者 → CEF リモートデバッグ を確認してください。",
            "cefPortInfo": "デバッグポート {0} · {1} 個のタブ",
            "copyLog": "ログをコピー",
            "splitMode": "言語別に適用：{0}",
            "splitNote": "オンにすると西文と中日韓を別々に選べます。リストの決定キーは、選んだフォントの言語に応じて自動でスロットに入ります。オフにすると単一スロットに戻り、選択済みの中日韓フォントは保持され、再度オンにすると復元されます。",
            "subSynth": "疑似ボールドを無効化：{0}",
            "subSynthNote": "ボールドウェイトを持たないフォントは合成ボールドされず、中太の文字は標準ウェイトで表示されます。",
            "subPunct": "CJK 句読点を使用：{0}",
            "subPunctOn": "オン = CJK 句読点（全角）を使用します。中国語・英語の両 UI に影響します。",
            "subPunctOff": "オフ = 西文句読点（既定、狭い／プロポーショナル幅）。",
            "slotLatin": "西文",
            "slotCjk": "中日韓",
            "followSystem": "システムに従う",
            "stripWarn": "西文フォントのグリフ削除に失敗したため、元のフォントに戻しました。中日韓スロットを覆い隠す可能性があります（西文専用フォントに変えると解決します）。",
            "noBoldInfo": "このフォントにはボールドウェイトがないため、中太の文字は標準ウェイトで表示されます（詳細設定で変更できます）",
            "menuTitle": "フォントを適用：{0}",
            "menuLatin": "西文に適用",
            "menuCjk": "中国語に適用",
            "menuCancel": "B で戻ってキャンセル。変更は行われません",
            "descLatinStrip": "西文字形のみ使用（漢字は自動的に除外）",
            "descCjkNone": "このフォントには中国語字形がありません",
            "splitHint": "現在のフォントを西文スロットに設定しました（西文字形のみ）。中日韓スロットには別のフォントを選ぶことをおすすめします"
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
            "clipboardDenied": "클립보드를 사용할 수 없습니다(쓰기가 거부됨)",
            "cat.serif": "Serif",
            "cat.sans": "Sans",
            "cat.mono": "Mono",
            "cat.other": "기타",
            "cat.fav": "즐겨찾기",
            "cat.all": "모든 글꼴",
            "lang.latin": "라틴",
            "lang.zh": "중국어",
            "lang.ja": "일본어",
            "lang.ko": "한국어",
            "lang.empty": "이 분류에는 {0} 글꼴이 없습니다.",
            "note.serif": "패밀리 이름으로 휴리스틱 판정합니다(Serif / Song / Ming / Times / 楷 등). 이름에 sans가 들어가면 제외됩니다.",
            "note.sans": "이름에 명확한 산세리프 키워드(sans / grotesk / gothic / 黑 등)가 들어간 글꼴입니다.",
            "note.mono": "fontconfig의 spacing 필드(100 / 110)로 판정하며, family 이름에 mono 또는 等宽이 들어간 글꼴도 포함합니다.",
            "note.other": "고정폭도 아니고 이름에 세리프 / 산세리프 키워드도 없는 글꼴입니다.",
            "note.fav": "글꼴에 포커스한 뒤 X(게임패드)를 눌러 즐겨찾기에 추가/해제할 수 있습니다",
            "notInjected": "적용 안 됨",
            "injected": "적용됨",
            "injectDone": "한 번 적용함 · {0}",
            "injectFailed": "적용 실패",
            "injectNone": "아직 적용 안 됨",
            "injectHeal": "자동 재적용 · {0}",
            "injectHealed": "자동 재적용함 · {0}",
            "serviceDown": "로컬 글꼴 서비스가 실행되지 않아 글꼴을 적용할 수 없습니다",
            "reapply": "현재 글꼴 다시 적용",
            "currentFont": "현재 글꼴",
            "notSelected": "선택 안 됨",
            "expand": "펼치기",
            "collapse": "접기",
            "langUnknown": "lang 알 수 없음",
            "langNotOverridden": "UI 글꼴이 변경되지 않았습니다",
            "langMore": " · 외 {0}개 언어",
            "langHasCjk": " · 중국어 글리프 포함",
            "fontChinese": "중국어 지원",
            "fontNoChinese": "중국어 없음, 자동 대체",
            "boldYes": " · Bold 있음",
            "boldNo": " · Bold 없음(합성 굵게)",
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
            "noResult": "일치하는 글꼴이 없습니다(이름과 별칭 모두 검색함).",
            "loadMore": "더 불러오기",
            "loadMoreRemaining": "더 불러오기({0}개 남음)",
            "restoreDefault": "Steam 기본 글꼴로 복원",
            "advanced": "고급",
            "cefFastMode": "CEF 고속 모드: {0}",
            "cefOn": "켜짐",
            "cefOff": "꺼짐",
            "cefNote": "사용하기 전에 Steam 설정 → 개발자에서 'CEF 원격 디버깅'을 켜야 합니다. 포트를 사용할 수 없으면 자동으로 일반 감지로 대체되며 글꼴 적용에는 영향을 주지 않습니다.",
            "cefNoPort": "CEF 디버깅 포트를 찾지 못해 일반 감지로 대체했습니다. Steam 설정 → 개발자 → CEF 원격 디버깅을 확인하세요.",
            "cefPortInfo": "디버그 포트 {0} · 탭 {1}개",
            "copyLog": "로그 복사",
            "splitMode": "언어별 적용: {0}",
            "splitNote": "켜면 라틴 문자와 CJK를 각각 선택합니다. 목록에서 확인 키를 누르면 선택한 글꼴의 언어에 맞는 슬롯에 자동으로 들어갑니다. 끄면 단일 슬롯으로 돌아가며, 선택한 CJK 글꼴은 유지되고 다시 켜면 복원됩니다.",
            "subSynth": "가짜 굵게 사용 안 함: {0}",
            "subSynthNote": "굵은 자획이 없는 글꼴은 합성 굵게 처리되지 않으며, 중간 굵기 글자는 일반 굵기로 표시됩니다.",
            "subPunct": "CJK 문장 부호 사용: {0}",
            "subPunctOn": "켬 = CJK 문장 부호(전각)를 사용합니다. 중국어·영어 UI 모두에 영향을 줍니다.",
            "subPunctOff": "끔 = 서양 문장 부호(기본, 좁은/가변 폭).",
            "slotLatin": "라틴",
            "slotCjk": "CJK",
            "followSystem": "시스템 따름",
            "stripWarn": "라틴 글꼴의 글리프 제거에 실패해 원래 글꼴로 되돌렸습니다. CJK 슬롯을 가릴 수 있습니다(라틴 전용 글꼴로 바꾸면 해결됩니다).",
            "noBoldInfo": "이 글꼴에는 굵은 자획이 없어 중간 굵기 글자가 일반 굵기로 표시됩니다(고급에서 변경 가능)",
            "menuTitle": "글꼴 적용: {0}",
            "menuLatin": "라틴에 적용",
            "menuCjk": "중국어에 적용",
            "menuCancel": "B로 돌아가 취소하며 변경 사항은 없습니다",
            "descLatinStrip": "라틴 글리프만 사용(한자 자동 제거)",
            "descCjkNone": "이 글꼴에는 중국어 글리프가 없습니다",
            "splitHint": "현재 글꼴을 라틴 슬롯으로 설정했습니다(라틴 글리프만). CJK 슬롯에는 다른 글꼴을 선택하는 것이 좋습니다"
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
            focusLater(function () {
                var node = toCjk ? menuCjkRef.current : menuLatinRef.current;
                if (!node && typeof document !== "undefined") {
                    node = document.getElementById(toCjk ? "fp-menu-cjk" : "fp-menu-latin");
                }
                return node;
            });
        }, [menuFont]);

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
                setMessage(t("noData"));
                return;
            }
            setMessage(payload.error || payload.message || "");
            setState(payload);
        }

        function call(method, args) {
            return serverApi.callPluginMethod(method, args || {}).then(function (res) {
                var value = unwrap(res);
                if (res && res.success === false) {
                    setMessage((value && value.error) || t("callFailed", method));
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
            if (busy || !current) return;
            setBusy(true);
            stopSettle();
            call("reapply").then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
        }

        // CEF 快速模式：默认关闭；开启后从调试端口一次列出全部 tab，失败自动回落
        var cefInfo = (state && state.cef) || { enabled: false, port: null, tabs: null, error: "" };
        var cefEnabled = !!cefInfo.enabled;

        // 分槽注入（按语言注入）开关状态：主开关 + 两个子开关，全部来自后端 payload；
        // 子开关默认值由后端口径统一（主开关开时 synth_weight_none 缺省=开、cjk_punct 缺省=关）
        var splitOn = !!(state && state.cjk_split);
        var synthNone = !!(state && state.synth_weight_none);
        var punctOn = !!(state && state.cjk_punct);

        function toggleCef() {
            if (busy) return;
            setBusy(true);
            call("set_cef_fast", { enabled: !cefEnabled }).then(function () { setBusy(false); });
        }

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

        function togglePunct() {
            if (busy) return;
            setBusy(true);
            stopSettle();
            call("set_split_options", { cjk_punct: !punctOn }).then(function (value) {
                setBusy(false);
                if (value && value.ok !== false) startSettle();
            });
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

        // 方案 D：剔字失败回退原始 face 时给可见警告（绝不静默）；
        // 禁用假加粗开启且当前西文字体没有 ≥600 face 时的信息提示（不弹窗）
        var stripFallback = !!(state && state.strip && state.strip.fallback);
        var noBoldInfo = !!(state && state.cjk_split && state.synth_weight_none &&
            state.bold === null && current);

        var currentFont = useMemo(function () {
            if (!current) return null;
            for (var i = 0; i < fonts.length; i++) {
                if (fonts[i].family === current) return fonts[i];
            }
            return null;
        }, [fonts, current]);

        // 首开提示：主开关开启、西文槽是 zh/ja/ko 字体且中日韩槽还没选时提示。
        // 纯信息行，不改任何语义；选了 CJK 槽或换成西文字体后自然消失
        var splitHint = !!(splitOn && current && !state.family_cjk &&
            currentFont && primaryLang(currentFont) !== "latin");

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
                {
                    id: "fav", name: t("cat.fav"), items: fav, kind: "fav",
                    // 空态文案去掉，无论 0 款还是有收藏都固定显示这句操作提示
                    note: t("note.fav"),
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
            cats.push({ id: "all", name: t("cat.all"), items: full, kind: "all", note: null });

            // 四个基础分类与「全部」带语言胶囊；收藏不带。
            // 计数跟分类一起算好并缓存，点胶囊时只做渲染期过滤，不重跑分类
            for (var p = 0; p < cats.length; p++) {
                var pillCat = cats[p];
                if (pillCat.id !== "serif" && pillCat.id !== "sans" && pillCat.id !== "mono" &&
                    pillCat.id !== "other" && pillCat.id !== "all") continue;
                var langCounts = { latin: 0, zh: 0, ja: 0, ko: 0 };
                for (var q = 0; q < pillCat.items.length; q++) langCounts[primaryLang(pillCat.items[q])] += 1;
                pillCat.langCounts = langCounts;
            }

            // 0 款的分类整块隐藏（标题也不渲染）；收藏夹与「全部字体」永远保留
            return cats.filter(function (cat) {
                return cat.id === "fav" || cat.id === "all" || cat.items.length > 0;
            });
        }, [fonts, favorites]);

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
        } else if (state && state.server && !state.server.running) {
            problemText = t("serviceDown");
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

        // 注入状态：三行各一个目标，只按后端 targets[].ok 判断
        // 标签列固定宽度，保证三行的状态文字左边缘对齐
        var TARGET_LABEL_WIDTH = "86px";
        var targetRows = (state && Array.isArray(state.targets) ? state.targets : []).map(function (target) {
            var statusText = target.ok ? t("injected") : t("notInjected");
            // 补注即最近生效注入（healed）时，三行「已注入」跟随主行的绿色
            var statusColor = target.ok ? (state && state.inject_level === "healed" ? "#8fd4a8" : "#8ab4f8") : "#ff9b90";
            return h(deckyFrontendLib.PanelSectionRow, { key: target.key },
                h("div", {
                    style: {
                        display: "flex",
                        alignItems: "center",
                        fontSize: "13px",
                        lineHeight: "1.4",
                        // 左右留白 20px，与卡片内容对齐（与原始版本完全一致的行结构）
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
                    h("span", {
                        style: { color: statusColor, fontWeight: 600, fontSize: "12px", minWidth: 0 },
                    }, statusText)
                )
            );
        });

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
            if (!font) return t("langUnknown");
            var langs = Array.isArray(font.langs) ? font.langs : [];
            var total = typeof font.lang_count === "number" ? font.lang_count : langs.length;
            var usable = !!(font.zh || font.zh_extra);
            if (!langs.length) return t("langUnknown");
            var zhLangs = langs.filter(isZhLangTag);
            if (zhLangs.length) {
                var shownZh = zhLangs.slice(0, 2).map(fmtLangTag).join(" · ");
                return "lang " + shownZh
                    + (zhLangs.length > 2 ? t("langMore", zhLangs.length) : "");
            }
            var shown = langs.slice(0, 3).map(fmtLangTag).join(" · ");
            return "lang " + shown
                + (total > 3 ? t("langMore", total) : "")
                + (usable ? t("langHasCjk") : "");
        }

        // 标题下的说明行：直接展示 lang 覆盖
        var langText = current ? langLine(currentFont) : t("langNotOverridden");

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
                    onOKActionDescription: currentOpen ? t("collapse") : t("expand"),
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
                        h("span", { style: { marginLeft: "6px" } }, t("currentFont"))
                    ),
                    // 双行槽位（西文 / 中日韩，CJK 槽未选显示「跟随系统」）+ 状态摘要
                    // 同一列、右对齐；摘要行在收起状态也能看到三态
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
                                fontSize: "12px",
                            },
                        }, t("slotLatin") + "：" + (current || t("notSelected"))),
                        h("span", {
                            style: {
                                maxWidth: "100%",
                                whiteSpace: "nowrap",
                                overflow: "hidden",
                                textOverflow: "ellipsis",
                                fontWeight: 400,
                                fontSize: "12px",
                                opacity: 0.72,
                            },
                        }, t("slotCjk") + "：" + (state && state.family_cjk ? state.family_cjk : t("followSystem"))),
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
                            : null,
                        healSummary
                            ? h("span", {
                                className: "fp-current-heal",
                                style: {
                                    maxWidth: "100%",
                                    whiteSpace: "normal",
                                    overflow: "visible",
                                    wordBreak: "break-word",
                                    textAlign: "right",
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
            // 三行状态：说明行已移除，「重新注入当前字体」触发迁到状态卡上
            // 状态卡：三行目标 + 右侧刷新图标包成一个可聚焦容器，
            // 确认键 / 点击 = 重新注入当前字体（原说明行的触发迁到这里）
            currentOpen && targetRows.length
                ? h("div", { key: "fp-current:targets" },
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
                        h("div", { style: { flex: "1 1 auto", minWidth: 0 } }, targetRows),
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
                                viewBox: "0 0 24 24", width: "20", height: "20",
                                fill: "#c3c8cf", "aria-hidden": true,
                            },
                                h("path", { d: "M17.65 6.35A7.958 7.958 0 0 0 12 4c-4.42 0-7.99 3.58-7.99 8s3.57 8 7.99 8c3.73 0 6.84-2.55 7.73-6h-2.08A5.99 5.99 0 0 1 12 18c-3.31 0-6-2.69-6-6s2.69-6 6-6c1.66 0 3.14.69 4.22 1.78L13 11h7V4l-2.35 2.35z" }))
                        )
                    ))
                : null,
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
            // 禁用假加粗 + 当前西文字体没有 ≥600 face：信息提示（不弹窗）
            !stripFallback && noBoldInfo
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:nobold" },
                    h("div", {
                        style: {
                            color: "#8ab4f8",
                            fontSize: "11px",
                            whiteSpace: "normal",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                            lineHeight: "1.45",
                        },
                    }, t("noBoldInfo")))
                : null,
            // 主开关开启且西文槽是 CJK 字体、CJK 槽未选：首开提示（纯信息，不改语义）
            splitHint
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-current:splithint" },
                    h("div", {
                        style: {
                            color: "#8ab4f8",
                            fontSize: "11px",
                            whiteSpace: "normal",
                            overflowWrap: "anywhere",
                            wordBreak: "break-word",
                            lineHeight: "1.45",
                        },
                    }, t("splitHint")))
                : null,
            message && message !== problemText
                ? h(deckyFrontendLib.PanelSectionRow, null,
                    h("div", { style: { color: "#ff8a80", whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" } }, message))
                : null
        );

        function fontRow(font, alias, sectionId) {
            var isFav = favorites.indexOf(font.family) !== -1;
            // 一款字体可同时命中两个槽位（西文 + 中日韩），v1 合并显示一个「使用中」
            var inUse = font.family === current || !!(state && state.family_cjk === font.family);
            return h(deckyFrontendLib.PanelSectionRow, { key: font.family },
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    disabled: busy,
                    description: (font.zh ? t("fontChinese") : t("fontNoChinese")) +
                        (font.has_bold ? t("boldYes") : t("boldNo")) +
                        (font.mono ? t("monoTag") : "") +
                        (isFav ? t("favTag") : "") +
                        (inUse ? t("inUse") : ""),
                    onClick: function () { choose(font.family, font); },
                    onSecondaryActionDescription: isFav ? t("unfavorite") : t("favorite"),
                    onOptionsActionDescription: sectionId ? t("collapseSection") : undefined,
                    onButtonDown: function (evt) {
                        var btn = buttonOf(evt);
                        // 字体行上按左右键，语义与分类标题一致：切换本分区语言并消费事件。
                        // 切完列表会重排，焦点一律回本分区标题；空档位同样回标题，焦点不会进空列表。
                        if (btn === BTN_LEFT || btn === BTN_RIGHT) {
                            if (!hasLangPills(sectionId)) return false;
                            cycleLang(sectionId, btn === BTN_RIGHT ? 1 : -1);
                            focusHeader(sectionId);
                            evt.stopPropagation();
                            return true;
                        }
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
        function sectionHeader(cat, count) {
            var isCollapsed = collapsed[cat.id] !== false;
            var total = typeof count === "number" ? count : cat.items.length;
            return h(deckyFrontendLib.PanelSectionRow, { key: cat.id + ":header" },
                h(deckyFrontendLib.Focusable, {
                    id: "fp-sec-" + cat.id,
                    tabIndex: 0,
                    className: "fp-sec",
                    focusClassName: "fp-row-focus",
                    ref: function (el) { headerRefs.current[cat.id] = el; },
                    onActivate: function () { toggleSection(cat.id); },
                    onClick: function () { toggleSection(cat.id); },
                    // 左右键切换本分类的语言：只在展开态且本行确实有胶囊时才接管，
                    // 其余情况返回 false 且不 stopPropagation，把方向键交还 Steam 的焦点导航
                    onButtonDown: function (evt) {
                        if (!cat.langCounts || isCollapsed) return false;
                        var btn = buttonOf(evt);
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
                    }, total + t("fontsCount"))
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
            ".fp-lang-pill .fp-lang-count{display:block;white-space:nowrap;font-size:9px;line-height:11px;" +
            "font-weight:500;opacity:0.7;font-variant-numeric:tabular-nums;}" +
            ".fp-lang-pill:hover{background:rgba(255,255,255,0.12);color:rgba(255,255,255,0.82);}" +
            // 选中态只借用面板现有的中性高亮
            ".fp-lang-pill.fp-lang-on,.fp-lang-pill.fp-lang-on:hover{" +
            "background:rgba(255,255,255,0.22)!important;color:#ffffff!important;font-weight:600;}" +
            ".fp-lang-pill.fp-lang-on .fp-lang-count{opacity:0.95;font-weight:600;}";

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
            : t("totalFonts", fonts.length);

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
                    disabled: busy || !current,
                    onClick: reset,
                }, t("restoreDefault"))
            )
        );

        var advancedSection = h(deckyFrontendLib.PanelSection, { title: t("advanced") },
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:btn" },
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    disabled: busy,
                    onClick: toggleCef,
                }, t("cefFastMode", cefEnabled ? t("cefOn") : t("cefOff")))
            ),
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:note" },
                h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5" } }, t("cefNote"))
            ),
            cefEnabled && cefInfo.error
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:error" },
                    h("div", { style: { color: "#ff9b90", fontSize: "10px", lineHeight: "1.5", whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" } }, t("cefNoPort"))
                )
                : null,
            cefEnabled && !cefInfo.error
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-cef:ok" },
                    h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5" } },
                        t("cefPortInfo", cefInfo.port || "?", cefInfo.tabs == null ? "?" : cefInfo.tabs))
                )
                : null,
            // 分槽注入主开关：开启后西文 / 中日韩两槽位各自选字体，确认键按语言归属落槽
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:btn" },
                h(deckyFrontendLib.ButtonItem, {
                    layout: "below",
                    disabled: busy,
                    onClick: toggleSplit,
                }, t("splitMode", splitOn ? t("cefOn") : t("cefOff")))
            ),
            h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:note" },
                h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5" } }, t("splitNote"))
            ),
            // 两个子开关仅在主开关开启时显示 / 生效
            splitOn
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:synth" },
                    h(deckyFrontendLib.ButtonItem, {
                        layout: "below",
                        disabled: busy,
                        onClick: toggleSynth,
                    }, t("subSynth", synthNone ? t("cefOn") : t("cefOff")))
                )
                : null,
            splitOn
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:synthnote" },
                    h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5" } }, t("subSynthNote"))
                )
                : null,
            splitOn
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:punct" },
                    h(deckyFrontendLib.ButtonItem, {
                        layout: "below",
                        disabled: busy,
                        onClick: togglePunct,
                    }, t("subPunct", punctOn ? t("cefOn") : t("cefOff")))
                )
                : null,
            splitOn
                ? h(deckyFrontendLib.PanelSectionRow, { key: "fp-split:punchnote" },
                    h("div", { style: { opacity: 0.55, fontSize: "10px", lineHeight: "1.5" } },
                        punctOn ? t("subPunctOn") : t("subPunctOff"))
                )
                : null,
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
                        // 定位修复：左移为左锚定（left/right 双 24px 安全边距），
                        // 修复 QAM 下 fixed 坐标空间偏宽导致的右缘溢出/裁切
                        position: "absolute", left: "24px", right: "24px", top: "38%",
                        transform: "translateY(-50%)",
                        width: "420px", maxWidth: "92%", boxSizing: "border-box",
                        background: "#1b2027", borderRadius: "8px", padding: "12px",
                        boxShadow: "0 8px 28px rgba(0,0,0,0.55)",
                    },
                },
                    h("div", {
                        title: family,
                        style: { fontSize: "13px", fontWeight: 600, marginBottom: "8px", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" },
                    }, t("menuTitle", family)),
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

        return h("div", null,
            h("style", { key: "fp-focus-css" }, focusCss),
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
