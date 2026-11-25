"""Korean/Chinese NLP helpers, coordinate detection, and abstention token detectors."""

from __future__ import annotations

import re
from functools import lru_cache

from engines.game_wiki.constants import (
    _CLAN_RECRUIT_RE,
    _COMMON_KR_STOPWORDS,
    _COND_X_NAMED_RE,
    _COND_X_RE,
    _COORD_APO_RE,
    _COORD_DIVISION_RE,
    _COORD_EP_RES,
    _COORD_HEROES_RE,
    _COORD_JINJOM_RE,
    _GENERIC_GAME_KEYWORDS,
    _HANGUL_SYLLABLE_ONLY_RE,
    _HANGUL_SYLLABLE_TOKEN_RE,
    _HANGUL_TOKEN_RE,
    _KR_PARTICLES,
    _KR_STAGE_TOKEN_RE,
    _KR_TRANSLATION_CUES,
    _LEVEL_N_RE,
    _NON_CJK_RE,
    _QUERY_FILLER_RE,
    _STAGE_ABBR_RE,
    _UNMATCHED_ZH_FILLER_CLEAN_RE,
    _UNMATCHED_ZH_PREFIX_CLEAN_RE,
    _UNMATCHED_ZH_TERM_RE,
    _ZH_STOP_PHRASES_WITHOUT_DE_RE,
    _ZH_X_OF_Y_RE,
)


@lru_cache(maxsize=2048)
def _boundary_pattern(token: str, full_boundary: bool) -> re.Pattern[str]:
    if full_boundary:
        return re.compile(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])")
    return re.compile(rf"(?<![a-z0-9]){re.escape(token)}")


def _token_in_text(token: str, text: str) -> bool:
    """邊界安全的子字串檢查：避免 '2技' 誤命中 'ep2技巧' 或短英文詞誤中單字內部。"""
    if not token or not text:
        return False
    if token.isascii() and token.isalnum() and len(token) <= 3:
        return bool(_boundary_pattern(token, True).search(text))
    if token[0].isascii() and token[0].isalnum():
        return bool(_boundary_pattern(token, False).search(text))
    return token in text


def _strip_query_filler(text: str) -> str:
    """萃取去除口語問句贅字與標點後的核心查詢詞（小寫）。"""
    if not text:
        return ""
    return _QUERY_FILLER_RE.sub(" ", text.lower()).strip()


def _analyze_dynamic_room_patterns(query_text: str) -> tuple[list[str], set[str]]:
    """
    針對韓服大廳/房間列表文字與 YouTube 攻略標題進行實戰語法結構解析：
    1. 區分「自由頻道 (자유 Channel) 的部落招生看板房 (클모 / 조건x)」與「實際副本攻略/練習房 (ㄷㅋㅈ / 34퀸 / 연습)」。
    2. 拆解韓服複合關卡縮寫（如 히2하드、진3하드）與特殊刷分流派（如 올치어 아처 머신건）。
    """
    notes: list[str] = []
    boost_kws: set[str] = set()

    # 1. [N]렙 [部落名稱] 클모 (例如 "11렙 오더 클모", "4렙 귀여운 듀옹 클모")
    clan_matches = _CLAN_RECRUIT_RE.findall(query_text)
    if clan_matches:
        parsed_items = [
            f"「{lvl}렙 {cname.strip()} 클모」（＝ {lvl} 等部落【{cname.strip()}】招募部落成員）"
            for lvl, cname in clan_matches
        ]
        notes.append(
            "💡 **【韓服「部落招生看板房」結構解析（極重要）】**：\n"
            f"  偵測到房名：{', '.join(parsed_items)}。\n"
            "  * 在《殭屍學園》韓服自由頻道（`자유 Channel`）中，因為按下「建立密碼房 (Create Locked Room)」時的**預設模式圖示就是最新劇情章節（如 `Division EP.3`）**，各大**部落（Clan / 클랜）**幹部在招人時不會特地改模式圖示，而是直接開一個鎖房掛在列表當作**「部落招生宣傳看板」**！\n"
            "  * 因此這裡的 `N렙` 指的是**「部落等級（Clan Level，如 11 等部落、4 等部落）」**，中間的詞（如 `오더`、`귀여운 듀옹`）是**「該部落的名稱」**，而字尾的 `클모` 是 **`클랜원 모집`（部落招人／招募部落成員）**！請勿將此類房間誤判為真的要打 Division 副本的指揮房或雙排房。"
        )
        boost_kws.update(["클모", "클랜원 모집", "部落招人", "n렙"])

    # 2. [名稱] 조건x (例如 "prace 조건x")
    cond_matches = _COND_X_NAMED_RE.findall(query_text)
    if cond_matches:
        names_str = "、".join(dict.fromkeys(cond_matches))
        notes.append(
            f"💡 **【韓服「名稱 + 조건x」部落招人／無條件房解析】**：\n"
            f"  偵測到房名帶有 `{names_str} 조건x`。\n"
            f"  * `조건x`（或 `무조건`）代表**「無任何條件限制（不看等級、不審核條件）」**。\n"
            f"  * 當前面冠上特定英文或韓文名稱（如 `prace 조건x`）且出現在自由頻道密碼房列表時，通常代表**名為「{names_str}」的部落正在招人（入部無條件限制）**，或是該部落的專屬休閒房（註：韓服玩家若要開副本練習房，只會直接打韓文 `연습`，如 `ㄷㅋㅈ 연습`）。"
        )
        boost_kws.update(["조건x", "무조건", "無條件"])
    elif _COND_X_RE.search(query_text):
        notes.append("💡 **【動態房名詞彙解析】**：`조건x` 代表「無條件限制（不限等級、不審核條件）」。")
        boost_kws.update(["조건x", "무조건", "無條件"])

    # 3. 韓服模式+章節+難度複合詞拆解 (例如 "히2하드", "진3하드", "디3나메")
    stage_matches = _STAGE_ABBR_RE.findall(query_text)
    if stage_matches:
        mode_map = {
            "히": "英雄模式 (Heroes)",
            "히즈": "英雄模式 (Heroes)",
            "진": "進擊的殭屍",
            "진좀": "進擊的殭屍",
            "디": "Division",
            "디비전": "Division",
        }
        diff_map = {
            "이지": "簡單 (Easy)",
            "노말": "普通 (Normal)",
            "하드": "困難 (Hard)",
            "나메": "惡夢 (Nightmare)",
            "악몽": "惡夢 (Nightmare)",
        }
        parsed_stages = []
        for m_pre, ep_num, diff_str in stage_matches:
            m_name = mode_map.get(m_pre, m_pre)
            d_name = f" {diff_map.get(diff_str, diff_str)}" if diff_str else ""
            parsed_stages.append(f"`{m_pre}{ep_num}{diff_str}` ＝ **{m_name} 第 {ep_num} 章 (EP.{ep_num}){d_name}**")
        notes.append("💡 **【韓服關卡章節縮寫拆解】**：" + "；".join(dict.fromkeys(parsed_stages)) + "。")
        boost_kws.update(["히즈", "히어로즈", "히2하드"])

    # 4. 올치어 (全開啦啦隊殭屍刷分流) 精準語意防呆
    if "올치어" in query_text or "啦啦隊" in query_text:
        notes.append(
            "💡 **【韓服刷分術語 `올치어` 關鍵解析（嚴禁與玩家職業搞混！）】**：\n"
            "  * `올치어` 是 **All + 치어리더 좀비（全開「啦啦隊殭屍」警報）** 的縮寫！\n"
            "  * 在第 2 章（訓練中心，如 `히2하드`）中，**「啦啦隊殭屍（치어리더 좀비）」是地圖上的特殊警報怪物**，玩家靠近時她會拿大聲公尖叫並召喚出大批身穿藍白劍道服、拿竹刀的**「劍道殭屍（검도 좀비）」**。\n"
            "  * 一般求穩通關會避開她，但在**極限刷分流（점수작）**中，玩家會故意**觸發全部啦啦隊殭屍（`올치어`）**來召喚最大量的劍道殭屍，再由玩家使用 **`아처`（弓箭手）**與 **`머신건`（機關槍手）**等高火力職業站在安全點（如 `포토존`）狂射洗出超高分（如 25 萬分以上）！\n"
            "  * ⚠️ **絕對禁止**將 `올치어 아처` 誤譯為「全弓箭手陣容」，也**絕對禁止**將「啦啦隊殭屍（怪物）」誤認為「玩家選啦啦隊職業」！"
        )
        boost_kws.update(["올치어", "치어리더 좀비", "검도 좀비", "머신건", "포토존"])

    # 5. 一般 N렙 (若未出現在 클모 結構中)
    if not clan_matches and _LEVEL_N_RE.search(query_text):
        lvl_matches = [m.group(0) for m in _LEVEL_N_RE.finditer(query_text)]
        match_str = " / ".join(dict.fromkeys(lvl_matches))
        notes.append(
            f"💡 **【動態等級標示解析】**：房名中的「{match_str}」代表等級（Level）：在部落招生房指「部落等級」，在一般副本房則指「進房等級門檻或房主等級」。"
        )
        boost_kws.update(["n렙", "렙"])

    return notes, boost_kws


def _detect_query_coordinates(query_text: str) -> dict[str, set[str]]:
    """
    從提問文本萃取三維座標 (series / timelines / chapters)，
    用於解決「EP1 跨系列撞車」與「經典舊版 vs. Reboot 重製版」混淆。
    """
    t = (query_text or "").lower()
    series: set[str] = set()
    timelines: set[str] = set()
    chapters: set[str] = set()

    if any(k in t for k in ("進化感染", "革命感染", "감염모드 에볼루션", "에볼루션", "에볼")):
        series.add("evolution")
    if any(
        k in t
        for k in (
            "進擊的殭屍", "進擊殭屍", "진격의 좀비", "진좀",
            "破滅的裂隙", "파멸의 균열", "絕望的種子", "절망의 씨앗", "鬥志的火種", "투지의 불씨",
        )
    ) or _COORD_JINJOM_RE.search(t):
        series.add("jinjom")
    if any(k in t for k in ("英雄模式", "英雄系列", "히어로즈", "히즈")) or _COORD_HEROES_RE.search(t):
        series.add("heroes")
    if any(k in t for k in ("啟示錄", "아포칼립스", "아포", "앞칼")) or _COORD_APO_RE.search(t):
        series.add("apo")
    if any(k in t for k in ("division", "디비전", "퓨리", "닥터 퀸", "34퀸", "ㄷㅋㅈ")) or _COORD_DIVISION_RE.search(t):
        series.add("division")
    if any(k in t for k in ("越獄", "警察抓小偷", "警察與小偷", "경찰과 도둑", "경도")):
        series.add("prison")
    if any(k in t for k in ("天梯", "排位", "래더")):
        series.add("ladder")
    if any(k in t for k in ("精靈高", "엘프고", "銀河高", "은하고", "魔法高", "마법고", "像素高", "픽셀고")):
        series.add("au")

    if any(
        k in t
        for k in (
            "reboot", "리부트", "重製版", "重製", "10週年重製",
            "破滅的裂隙", "파멸의 균열", "絕望的種子", "절망의 씨앗", "鬥志的火種", "투지의 불씨",
        )
    ):
        timelines.add("reboot")
    if any(k in t for k in ("經典版", "經典原版", "舊版", "原版", "구버전", "원작")):
        timelines.add("classic")

    for ep_key, ep_re in _COORD_EP_RES.items():
        if ep_re.search(t):
            chapters.add(ep_key)

    return {"series": series, "timelines": timelines, "chapters": chapters}


def _extract_chinese_subterms(text: str) -> list[str]:
    """
    將連續無空格的中文句子（不論是群友丟的「無標示長串觀念」或提問者的「自然口語問句」）
    切分為高辨識度的 2~6 字專有詞片段（並自動為「X的Y」補上「XY」無的字別名）。
    """
    if not text:
        return []
    subterms: list[str] = []

    # 1. 先將口語贅詞（但保留「的」以利辨識「破滅的裂隙」這類專有名詞）替換為空格
    pre_cleaned = _NON_CJK_RE.sub(" ", text)
    pre_cleaned = _ZH_STOP_PHRASES_WITHOUT_DE_RE.sub(" ", pre_cleaned)

    # 抓取乾淨的「X的Y」複合專有名詞（如「破滅的裂隙」、「絕望的種子」），同時產生「破滅裂隙」、「絕望種子」
    for m in _ZH_X_OF_Y_RE.finditer(pre_cleaned):
        p1, p2 = m.group(1).strip("的"), m.group(2).strip("的")
        if len(p1) >= 2 and len(p2) >= 2:
            subterms.extend([f"{p1}的{p2}", f"{p1}{p2}", p1, p2])

    # 2. 再將「的」也作為分隔符切開，提取所有 2~6 字的核心名詞/觀念詞
    for seg in pre_cleaned.split("的"):
        for part in seg.split():
            part = part.strip()
            if not part or part in _GENERIC_GAME_KEYWORDS:
                continue
            if 2 <= len(part) <= 6:
                subterms.append(part)
                if len(part) >= 4:
                    subterms.append(part[:2])
                    subterms.append(part[-2:])
            elif len(part) > 6:
                for i in range(0, len(part) - 2):
                    subterms.append(part[i : i + 3])

    dedup: list[str] = []
    seen: set[str] = set()
    for s in subterms:
        sl = s.lower()
        if sl not in seen and sl not in _GENERIC_GAME_KEYWORDS and len(sl) >= 2:
            seen.add(sl)
            dedup.append(s)
    return dedup


def _strip_korean_particles(token: str) -> str:
    """
    僅對純韓文音節詞彙 (가-힣) 剝除常見後接助詞／繫詞尾碼；
    若含初聲／相容字母 (ㄱ-ㅎ/ㅏ-ㅣ) 或剩餘詞幹不足 2 字則原樣安全回傳。
    """
    if not token:
        return ""
    if not _HANGUL_SYLLABLE_ONLY_RE.fullmatch(token):
        return token
    for suffix in _KR_PARTICLES:
        if token.endswith(suffix):
            stem = token[: -len(suffix)]
            if len(stem) >= 2 and _HANGUL_SYLLABLE_ONLY_RE.fullmatch(stem):
                return stem
    return token


_strip_korean_particle = _strip_korean_particles


def _build_covered_corpus(
    slang_hits: list[dict],
    lore_hits: list[dict],
    related_hits: list[dict],
) -> str:
    return " ".join(
        e["body"] + " " + " ".join(e.get("keywords", []))
        for e in (slang_hits + lore_hits + related_hits)
    ).lower()


def _detect_unmatched_korean_tokens(
    query_text: str,
    slang_hits: list[dict],
    lore_hits: list[dict],
    related_hits: list[dict],
) -> list[str]:
    """
    找出輸入文字或 OCR 中出現、但未命中任何本地知識庫條目的韓文詞塊，
    用於注入「防錨定腦補警告 (Anti-Anchoring Guard)」，防止小模型把未知韓文硬湊進旁邊的已知詞彙。
    """
    kr_tokens = _HANGUL_TOKEN_RE.findall(query_text)
    if not kr_tokens:
        return []

    covered_corpus = _build_covered_corpus(slang_hits, lore_hits, related_hits)
    unmatched: list[str] = []
    for tok in dict.fromkeys(kr_tokens):
        tok_l = tok.lower()
        stem_l = _strip_korean_particles(tok_l)
        if tok_l in _COMMON_KR_STOPWORDS or (stem_l and stem_l in _COMMON_KR_STOPWORDS):
            continue
        if _KR_STAGE_TOKEN_RE.match(tok_l):
            continue
        is_covered = (
            tok_l in covered_corpus
            or (bool(stem_l) and stem_l != tok_l and stem_l in covered_corpus)
        )
        if not is_covered:
            unmatched.append(tok)
    return unmatched


def _detect_unmatched_chinese_tokens(
    query_text: str,
    slang_hits: list[dict],
    lore_hits: list[dict],
    related_hits: list[dict],
) -> list[str]:
    """
    偵測使用者提問中明確點名詢問、卻完全不在檢索結果中的具體「XX殭屍 / XX博士」（如捏造的透明殭屍），
    協助觸發「不知道就直說」的部分拒答機制。
    （注意：不對一般英文縮寫或顏文字如 oqo / qaq / owo / apo 進行盲目攔截，避免誤傷正常聊天與表情符號！）
    """
    if not query_text:
        return []

    covered_corpus = _build_covered_corpus(slang_hits, lore_hits, related_hits)
    unmatched: list[str] = []
    ignore_phrases = {
        "殭屍學園", "故事模式", "遊戲模式", "什麼殭屍", "哪些殭屍", "常見殭屍",
        "普通殭屍", "一般殭屍", "特殊殭屍", "那種殭屍", "這種殭屍", "宿主殭屍", "母體殭屍",
        "進擊的殭屍", "進擊殭屍", "所有殭屍", "其他殭屍", "各種殭屍", "幾種殭屍",
    }
    # 先移除「殭屍學園 / 殭屍高中」與口語連接詞，避免「能問殭屍學園」被誤切出「能問殭屍」
    cleaned_q = _UNMATCHED_ZH_PREFIX_CLEAN_RE.sub(" ", query_text)
    cleaned_q = _UNMATCHED_ZH_FILLER_CLEAN_RE.sub(" ", cleaned_q)
    for m in _UNMATCHED_ZH_TERM_RE.finditer(cleaned_q):
        phrase = m.group(1).strip()
        if phrase in ignore_phrases:
            continue
        if phrase.lower() not in covered_corpus:
            unmatched.append(phrase)

    return list(dict.fromkeys(unmatched))


_extract_unmatched_specific_zh_en_terms = _detect_unmatched_chinese_tokens


def _is_korean_translation_request(query_text: str, ocr_extracted_text: str = "") -> bool:
    """
    判斷是否為「韓文攻略短句／截圖翻譯請求」：
    1. 提問或 OCR 文字必須至少包含韓文音節詞塊 (가-힣, 長度>=2)；純初聲縮寫不觸發翻譯模式。
    2. 提問出現翻譯／意思關鍵詞，或包含 >= 3 個空白分隔之韓文音節詞塊時，啟動翻譯輔助模式。
    """
    q_text = query_text or ""
    combined = f"{q_text}\n{ocr_extracted_text or ''}"
    if not _HANGUL_SYLLABLE_TOKEN_RE.search(combined):
        return False
    q_lower = q_text.lower()
    if any(cue in q_lower for cue in _KR_TRANSLATION_CUES):
        return True
    if len(_HANGUL_SYLLABLE_TOKEN_RE.findall(q_text)) >= 3:
        return True
    return False
