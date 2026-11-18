import re
import csv
import asyncio
import logging
from pathlib import Path
from typing import Optional
from google.genai import types

logger = logging.getLogger("DiscordGeminiBot.GameWiki")

WIKI_DIR = Path(__file__).resolve().parent.parent / "data" / "game_wiki"
CHAR_CSV_PATH = WIKI_DIR / "character_translations_tw.csv"
COMMUNITY_WIKI_PATH = WIKI_DIR / "community_contributions.md"

CATEGORY_LABELS = {
    "slang": "韓服黑話與開房用語",
    "character": "角色深度設定",
    "character_dict": "台服角色譯名與特徵對照",
    "story_arc": "主線與分部劇情",
    "game_mode": "遊戲模式與職業機制",
    "au_series": "平行世界與活動",
    "community_contrib": "群友補充資料",
}

# 快取結構：依據各 .md 與 .csv 檔案的最後修改時間 (mtime) 自動熱重載 (Hot-Reload)
_WIKI_CACHE: dict = {
    "mtimes": {},
    "entries": [],          # list[dict]
    "entries_by_id": {},    # dict[str, dict]
}

# 判斷是否為《殭屍學園》或韓服相關提問的觸發特徵詞
GAME_DOMAIN_TRIGGERS = (
    "殭屍學園", "殭學", "좀비고", "좀고", "zombie high", "進擊的殭屍", "진좀", "히어로즈", "히즈",
    "디비전", "division", "아포칼립스", "아포", "apo", "啟示錄", "進化感染", "感染模式", "越獄模式",
    "경찰과 도둑", "경도", "래더", "엘프고", "은하고", "마법고", "픽셀고", "불새재단", "火鳥基金會",
    "火鳥財團", "靈魂石", "소울스톤", "reboot", "리부트", "重製版", "破滅的裂隙", "파멸의 균열",
    "절망의 씨앗", "투지의 불씨", "主角群", "主角", "四人組", "1年a班", "南東進", "李幼莉", "幼莉",
    "劉露美", "青蛙女", "金俊浩", "鄭藝瑟", "鄭東碩", "高娜萊", "郭俊亨", "林賢智", "撒母爾",
    "白雪蓮", "校婆", "倫達", "론다", "Queen博士", "Dr.Q", "닥터 퀸", "클모", "ㄷㅋㅈ", "34퀸",
    "오더", "조건x", "자유킥", "다잡", "올치어", "히2하드", "머신건", "포토존", "部落", "椅子男",
    "紅弓", "綠弓", "軍醫", "揮棒男", "火焰手", "滑板", "房名", "韓服",
)


def _analyze_dynamic_room_patterns(query_text: str) -> tuple[list[str], set[str]]:
    """
    針對韓服大廳/房間列表文字與 YouTube 攻略標題進行實戰語法結構解析：
    1. 區分「自由頻道 (자유 Channel) 的部落招生看板房 (클모 / 조건x)」與「實際副本攻略/練習房 (ㄷㅋㅈ / 34퀸 / 연습)」。
    2. 拆解韓服複合關卡縮寫（如 히2하드、진3하드）與特殊刷分流派（如 올치어 아처 머신건）。
    """
    notes: list[str] = []
    boost_kws: set[str] = set()

    # 1. [N]렙 [部落名稱] 클모 (例如 "11렙 오더 클모", "4렙 귀여운 듀옹 클모")
    clan_recruit_pattern = re.compile(r"(\d+)\s*렙\s+([^\n\r()]+?)\s*클모", re.IGNORECASE)
    clan_matches = clan_recruit_pattern.findall(query_text)
    if clan_matches:
        parsed_items = [f"「{lvl}렙 {cname.strip()} 클모」（＝ {lvl} 等部落【{cname.strip()}】招募部落成員）" for lvl, cname in clan_matches]
        notes.append(
            "💡 **【韓服「部落招生看板房」結構解析（極重要）】**：\n"
            f"  偵測到房名：{', '.join(parsed_items)}。\n"
            "  * 在《殭屍學園》韓服自由頻道（`자유 Channel`）中，因為按下「建立密碼房 (Create Locked Room)」時的**預設模式圖示就是最新劇情章節（如 `Division EP.3`）**，各大**部落（Clan / 클랜）**幹部在招人時不會特地改模式圖示，而是直接開一個鎖房掛在列表當作**「部落招生宣傳看板」**！\n"
            "  * 因此這裡的 `N렙` 指的是**「部落等級（Clan Level，如 11 等部落、4 等部落）」**，中間的詞（如 `오더`、`귀여운 듀옹`）是**「該部落的名稱」**，而字尾的 `클모` 是 **`클랜원 모집`（部落招人／招募部落成員）**！請勿將此類房間誤判為真的要打 Division 副本的指揮房或雙排房。"
        )
        boost_kws.update(["클모", "클랜원 모집", "部落招人", "n렙"])

    # 2. [名稱] 조건x (例如 "prace 조건x")
    cond_x_pattern = re.compile(r"([a-zA-Z0-9가-힣_]{2,15})\s+조건\s*[xX무]", re.IGNORECASE)
    cond_matches = cond_x_pattern.findall(query_text)
    if cond_matches:
        names_str = "、".join(dict.fromkeys(cond_matches))
        notes.append(
            f"💡 **【韓服「名稱 + 조건x」部落招人／無條件房解析】**：\n"
            f"  偵測到房名帶有 `{names_str} 조건x`。\n"
            f"  * `조건x`（或 `무조건`）代表**「無任何條件限制（不看等級、不審核條件）」**。\n"
            f"  * 當前面冠上特定英文或韓文名稱（如 `prace 조건x`）且出現在自由頻道密碼房列表時，通常代表**名為「{names_str}」的部落正在招人（入部無條件限制）**，或是該部落的專屬休閒房（註：韓服玩家若要開副本練習房，只會直接打韓文 `연습`，如 `ㄷㅋㅈ 연습`）。"
        )
        boost_kws.update(["조건x", "무조건", "無條件"])
    elif re.search(r"조건\s*[xX무]", query_text, re.IGNORECASE):
        notes.append("💡 **【動態房名詞彙解析】**：`조건x` 代表「無條件限制（不限等級、不審核條件）」。")
        boost_kws.update(["조건x", "무조건", "無條件"])

    # 3. 韓服模式+章節+難度複合詞拆解 (例如 "히2하드", "진3하드", "디3나메")
    stage_pattern = re.compile(r"(히즈?|진좀?|디비전?|디)\s*([0-4])\s*(이지|노말|하드|나메|악몽)?", re.IGNORECASE)
    stage_matches = stage_pattern.findall(query_text)
    if stage_matches:
        mode_map = {"히": "英雄模式 (Heroes)", "히즈": "英雄模式 (Heroes)", "진": "進擊的殭屍", "진좀": "進擊的殭屍", "디": "Division", "디비전": "Division"}
        diff_map = {"이지": "簡單 (Easy)", "노말": "普通 (Normal)", "하드": "困難 (Hard)", "나메": "惡夢 (Nightmare)", "악몽": "惡夢 (Nightmare)"}
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
    if not clan_matches and re.search(r"(\d+)\s*렙", query_text, re.IGNORECASE):
        lvl_matches = [m.group(0) for m in re.finditer(r"(\d+)\s*렙", query_text, re.IGNORECASE)]
        match_str = " / ".join(dict.fromkeys(lvl_matches))
        notes.append(
            f"💡 **【動態等級標示解析】**：房名中的「{match_str}」代表等級（Level）：在部落招生房指「部落等級」，在一般副本房則指「進房等級門檻或房主等級」。"
        )
        boost_kws.update(["n렙", "렙"])

    return notes, boost_kws


_COMMON_KR_STOPWORDS = {
    "자유", "신나는", "한판", "좀비고", "좀비고등학교", "점수", "실화냐", "연습", "조건",
    "채널", "민멍", "절제", "제혼", "만뇽", "정원", "한멸", "헨니", "냉훈", "하시죠",
    "뭔가", "어디서봤는데", "별클랜", "귀여운",
}

# 泛用遊戲大類詞與條目段落標題詞：單憑命中這些詞不足以證明「知識庫有收錄使用者問的具體事物」
_GENERIC_GAME_KEYWORDS = {
    "殭屍學園", "殭學", "殭屍高", "殭屍高等等學校", "좀비고등학교", "좀비고", "좀고",
    "zhs", "zombie high", "zombie high school", "pve", "pvp", "故事模式", "遊戲模式",
    "韓服", "台服", "模式", "角色", "劇情", "副本", "攻略",
    "詳細說明", "基本規則", "核心機制", "攻擊方式", "實戰定位", "背景衝突", "核心衝突",
    "代表篇章", "排位分段", "競技環境", "生存機制", "機制特徵", "社群熱度", "治療機制",
    "突破性", "實戰地位", "獲取途徑", "配置方式", "分支類型", "戰略意義", "怪物身分",
    "對策", "怪物特徵", "攻略原則", "機制特性", "招牌技能", "攻略機制", "招牌機制",
    "第一階段", "第二階段", "第三階段", "破解", "走位", "攻略要訣", "表面包裝",
    "隱藏真相", "殘酷實驗", "背景起因", "衝突轉折", "關鍵戰鬥", "核心進展", "尾王對決",
    "動機揭密", "悲劇終局", "背景發展", "世界觀擴展", "核心機制設定", "衝突爆發",
    "反派真面目", "高難機制", "強化血清原理", "半感染特例", "靈魂石系統", "實戰意義",
    "常見語境", "絕對沒有",
}

# 口語問句與連接詞停用表：用於將「時間術士的大招什麼時候開比較好？」或無標示長文拆解為「時間術士」、「大招」等核心檢索詞
_ZH_STOP_PHRASES_REGEX = re.compile(
    r"(?:什麼時候|什麼地方|哪裡可以|哪裡有|哪裡|怎麼走|怎麼打|怎麼拿|怎麼開|怎麼玩|怎麼用|怎麼辦|怎麼|"
    r"要注意什麼|有什麼用|有什麼|為什麼|是不是|好不好|比較好|比較容易|一定要|如果想要|如果|想要|"
    r"第一階段|第二階段|第三階段|左上角|右上角|左下角|右下角|選的是|記得留|記得|的時候|打爆之後|之後|"
    r"會出特殊的|會出|另外|隊友|還有|比較|容易|遇到|可以疊加|可以|疊加|三次|裡面|裡|請問|能問|介紹一下|介紹|"
    r"講一下|說明一下|解釋一下|有關|關於|的問題嗎|的問題|是什麼意思|是什麼|是啥|"
    r"這個|那個|哪些|哪個|因為|所以|但是|而且|或者|還是|應該|需要|必須|不要|不能|一下|一個|一些|非常|特別|直接|"
    r"[的了嗎呢吧喔啊是在有與和跟或把被去打買開走拿用看說讓給到從對向往上])+"
)


def _extract_chinese_subterms(text: str) -> list[str]:
    """
    將連續無空格的中文句子（不論是群友丟的「無標示長串觀念」或提問者的「自然口語問句」）
    切分為高辨識度的 2~6 字專有詞片段（並自動為「X的Y」補上「XY」無的字別名）。
    """
    if not text:
        return []
    subterms: list[str] = []

    # 1. 先將口語贅詞（但保留「的」以利辨識「破滅的裂隙」這類專有名詞）替換為空格
    stop_without_de = re.compile(
        r"(?:什麼時候|什麼地方|哪裡可以|哪裡有|哪裡|怎麼走|怎麼打|怎麼拿|怎麼開|怎麼玩|怎麼用|怎麼辦|怎麼|"
        r"要注意什麼|有什麼用|有什麼|為什麼|是不是|好不好|比較好|比較容易|一定要|如果想要|如果|想要|"
        r"第一階段|第二階段|第三階段|左上角|右上角|左下角|右下角|選的是|記得留|記得|的時候|打爆之後|之後|"
        r"會出特殊的|會出特殊|會出|另外|隊友|還有|比較|容易|遇到|可以疊加|可以|疊加|三次|幾次|裡面|裡|請問|能問|介紹一下|介紹|"
        r"講一下|說明一下|解釋一下|有關|關於|的問題嗎|的問題|是什麼意思|是什麼|是啥|"
        r"這個|那個|哪些|哪個|因為|所以|但是|而且|或者|還是|應該|需要|必須|不要|不能|一下|一個|一些|非常|特別|直接|"
        r"[了嗎呢吧喔啊是在有與和跟或把被去打買開走拿用看說讓給到從對向往上])+"
    )
    pre_cleaned = re.sub(r"[^\u4e00-\u9fff]+", " ", text)
    pre_cleaned = stop_without_de.sub(" ", pre_cleaned)

    # 抓取乾淨的「X的Y」複合專有名詞（如「破滅的裂隙」、「絕望的種子」），同時產生「破滅裂隙」、「絕望種子」
    for m in re.finditer(r"([\u4e00-\u9fff]{2,4})的([\u4e00-\u9fff]{2,4})", pre_cleaned):
        p1, p2 = m.group(1).strip("的"), m.group(2).strip("的")
        if len(p1) >= 2 and len(p2) >= 2:
            subterms.extend([f"{p1}的{p2}", f"{p1}{p2}", p1, p2])

    # 2. 再將「的」也作為分隔符切開，提取所有 2~6 字的核心名詞/觀念詞（如「時間術士」、「大招」、「公園路線」、「隱藏商人」、「強化劑」）
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
                    subterms.append(part[i:i + 3])

    dedup: list[str] = []
    seen: set[str] = set()
    for s in subterms:
        sl = s.lower()
        if sl not in seen and sl not in _GENERIC_GAME_KEYWORDS and len(sl) >= 2:
            seen.add(sl)
            dedup.append(s)
    return dedup


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
    kr_tokens = re.findall(r"[\u3131-\u3163\uac00-\ud7a3]{2,}", query_text)
    if not kr_tokens:
        return []

    covered_corpus = " ".join(
        e["body"] + " " + " ".join(e.get("keywords", []))
        for e in (slang_hits + lore_hits + related_hits)
    ).lower()

    unmatched: list[str] = []
    for tok in dict.fromkeys(kr_tokens):
        tok_l = tok.lower()
        if tok_l in _COMMON_KR_STOPWORDS:
            continue
        if re.match(r"^(히|진|디)\d*(이지|노말|하드|나메)?$", tok_l):
            continue
        if tok_l not in covered_corpus:
            unmatched.append(tok)
    return unmatched


def _extract_unmatched_specific_zh_en_terms(
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

    covered_corpus = " ".join(
        e["body"] + " " + " ".join(e.get("keywords", []))
        for e in (slang_hits + lore_hits + related_hits)
    ).lower()

    unmatched: list[str] = []
    ignore_phrases = {
        "殭屍學園", "故事模式", "遊戲模式", "什麼殭屍", "哪些殭屍", "常見殭屍",
        "普通殭屍", "一般殭屍", "特殊殭屍", "那種殭屍", "這種殭屍", "宿主殭屍", "母體殭屍",
        "進擊的殭屍", "進擊殭屍", "所有殭屍", "其他殭屍", "各種殭屍", "幾種殭屍",
    }
    # 先移除「殭屍學園 / 殭屍高中」與口語連接詞，避免「能問殭屍學園」被誤切出「能問殭屍」
    cleaned_q = re.sub(r"殭屍(?:學園|高中|高)", " ", query_text)
    cleaned_q = re.sub(r"(?:能問|請問|介紹一下|介紹|關於|有關|裡面的|裡的|裡面|出現的|常見的|十種|10種|[的了嗎呢吧是和與跟有打隻種個這那])", " ", cleaned_q)
    for m in re.finditer(r"([\u4e00-\u9fff]{2,4}(?:殭屍|博士))", cleaned_q):
        phrase = m.group(1).strip()
        if phrase in ignore_phrases:
            continue
        if phrase.lower() not in covered_corpus:
            unmatched.append(phrase)

    return list(dict.fromkeys(unmatched))


def _parse_list_field(raw_val: str) -> list[str]:
    """解析 YAML 中的簡易陣列欄位，例如 [a, b, c]"""
    val = raw_val.strip()
    if val.startswith("[") and val.endswith("]"):
        val = val[1:-1]
    items = []
    for part in val.split(","):
        cleaned = part.strip().strip("'\"")
        if cleaned:
            items.append(cleaned)
    return items


def _parse_markdown_wiki_file(file_path: Path) -> list[dict]:
    """
    解析單一 data/game_wiki/*.md 檔案中的結構化條目。
    支援標準 YAML Frontmatter，並自動確保每個條目皆具備 Contextual Retrieval 全知脈絡標頭。
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"❌ 讀取知識庫檔案失敗 ({file_path.name}): {e}")
        return []

    pattern = re.compile(
        r"(?:^|\n)---\r?\n(id:\s*[^\r\n]+.*?)\r?\n---\r?\n",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(content))
    if not matches:
        return []

    parsed_entries: list[dict] = []
    for idx, match in enumerate(matches):
        yaml_block = match.group(1)
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        body_text = content[body_start:body_end].strip()

        meta: dict[str, str | list[str]] = {
            "id": "",
            "category": file_path.stem,
            "name_zh": "",
            "name_kr": "",
            "keywords": [],
            "related_ids": [],
            "source_file": file_path.name,
        }

        for line in yaml_block.splitlines():
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip().lower()
            v = v.strip()
            if k in ("keywords", "related_ids"):
                meta[k] = _parse_list_field(v)
            elif k in ("id", "category", "name_zh", "name_kr"):
                meta[k] = v.strip("'\"")

        entry_id = str(meta["id"]).strip()
        if not entry_id:
            continue

        cat = str(meta["category"]).strip()
        cat_label = CATEGORY_LABELS.get(cat, cat)
        name_zh = str(meta["name_zh"]).strip()
        name_kr = str(meta["name_kr"]).strip()

        if not body_text.startswith("【《殭屍學園》"):
            kr_part = f" ({name_kr})" if name_kr and name_kr not in name_zh else ""
            header = f"【《殭屍學園》[{cat_label}] — {name_zh}{kr_part}】"
            body_text = f"{header}\n{body_text}"

        raw_kws = list(meta["keywords"]) if isinstance(meta["keywords"], list) else []
        for extra in (name_zh, name_kr):
            if extra:
                raw_kws.append(extra)
                for sub in re.split(r"[/（）()、,，&]", extra):
                    sub_clean = sub.strip()
                    if len(sub_clean) >= 2:
                        raw_kws.append(sub_clean)

        # 自動從內文 (body_text) 提取粗體專有名詞與引號內專有名詞加入索引，大幅提升子模式與機制的召回率
        for m_term in re.findall(r"(?:「([^」]{2,15})」|〈([^〉]{2,15})〉|\*\*([^*：:]{2,15})\*\*)", body_text):
            for t_part in m_term:
                if t_part:
                    for sub in re.split(r"[/（）()、,，&]", t_part):
                        sc = sub.strip()
                        if 2 <= len(sc) <= 15:
                            raw_kws.append(sc)

        dedup_kws: list[str] = []
        seen_lower: set[str] = set()
        for kw in raw_kws:
            kw_s = kw.strip()
            if not kw_s:
                continue
            kw_l = kw_s.lower()
            if kw_l not in seen_lower:
                seen_lower.add(kw_l)
                dedup_kws.append(kw_s)

        parsed_entries.append({
            "id": entry_id,
            "category": cat,
            "category_label": cat_label,
            "name_zh": name_zh,
            "name_kr": name_kr,
            "keywords": dedup_kws,
            "related_ids": list(meta["related_ids"]) if isinstance(meta["related_ids"], list) else [],
            "body": body_text,
            "source_file": file_path.name,
        })

    return parsed_entries


def _parse_character_csv_file(csv_path: Path) -> list[dict]:
    """
    載入台服官方與社群常用角色譯名對照表 (character_translations_tw.csv)，
    讓全部 105 位角色的韓文原名、台服譯名、外觀特徵與玩家俗稱皆可被 100% 精準檢索。
    """
    if not csv_path.exists():
        return []
    entries: list[dict] = []
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=1):
                kr_raw = (row.get("角色名 (韓文)") or "").strip()
                zh_raw = (row.get("中文翻譯") or "").strip()
                memo = (row.get("備註 (自用)") or "").strip()
                nick = (row.get("稱呼（大眾）") or "").strip()
                role_identity = (row.get("身分") or "").strip()
                if not kr_raw:
                    continue

                # 清理如 "(台服)" 或網址註記，提取乾淨譯名供檢索與顯示
                zh_clean = re.sub(r"\(https?://[^\)]+\)", "", zh_raw).strip()
                kws: list[str] = [kr_raw]
                for part in re.split(r"[/（）()、,，]", f"{kr_raw}/{zh_clean}/{nick}/{memo}"):
                    p = part.strip()
                    if p and p not in ("台服", "韓語", "韓文", "namu", "待考", "無翻譯", "全名待考", "本名待考", "自用"):
                        if len(p) >= 2 or re.search(r"[\uac00-\ud7a3]", p):
                            kws.append(p)

                dedup_kws: list[str] = []
                seen: set[str] = set()
                for k in kws:
                    kl = k.lower()
                    if kl not in seen:
                        seen.add(kl)
                        dedup_kws.append(k)

                disp_zh = zh_clean if "<待考" not in zh_clean else f"{kr_raw}（台服暫無官譯，俗稱：{memo or nick}）"
                body_lines = [
                    f"【《殭屍學園》[台服角色譯名與特徵對照] — {disp_zh}（韓文：{kr_raw}）】",
                    f"- **台服正式譯名**：{disp_zh}",
                ]
                if nick:
                    body_lines.append(f"- **台灣玩家常用稱呼／俗稱**：{nick}")
                if memo:
                    body_lines.append(f"- **外觀特徵與角色定位**：{memo}")
                if role_identity:
                    body_lines.append(f"- **班級／所屬身分**：{role_identity}")

                entries.append({
                    "id": f"char_csv_{idx}",
                    "category": "character_dict",
                    "category_label": CATEGORY_LABELS["character_dict"],
                    "name_zh": disp_zh,
                    "name_kr": kr_raw,
                    "keywords": dedup_kws,
                    "related_ids": [],
                    "body": "\n".join(body_lines),
                    "source_file": csv_path.name,
                })
    except Exception as e:
        logger.error(f"❌ 讀取角色譯名 CSV 失敗 ({csv_path.name}): {e}")
    return entries


def load_wiki_entries(force_reload: bool = False) -> list[dict]:
    """
    載入並快取 data/game_wiki/*.md 與 character_translations_tw.csv 所有知識庫條目。
    當偵測到任何檔案修改時間 (mtime) 變更時自動熱重載，無須重啟 Bot。
    """
    if not WIKI_DIR.exists():
        return []

    md_files = sorted(WIKI_DIR.glob("*.md"))
    current_mtimes = {}
    for f in md_files:
        try:
            current_mtimes[f.name] = f.stat().st_mtime
        except OSError:
            pass
    if CHAR_CSV_PATH.exists():
        try:
            current_mtimes[CHAR_CSV_PATH.name] = CHAR_CSV_PATH.stat().st_mtime
        except OSError:
            pass

    if not force_reload and _WIKI_CACHE["entries"] and current_mtimes == _WIKI_CACHE["mtimes"]:
        return _WIKI_CACHE["entries"]

    all_entries: list[dict] = []
    by_id: dict[str, dict] = {}
    for f in md_files:
        file_entries = _parse_markdown_wiki_file(f)
        for item in file_entries:
            all_entries.append(item)
            by_id[item["id"]] = item

    csv_entries = _parse_character_csv_file(CHAR_CSV_PATH)
    for item in csv_entries:
        all_entries.append(item)
        by_id[item["id"]] = item

    _WIKI_CACHE["mtimes"] = current_mtimes
    _WIKI_CACHE["entries"] = all_entries
    _WIKI_CACHE["entries_by_id"] = by_id
    logger.info(
        f"📚 已載入《殭屍學園》Contextual-LightRAG 知識庫：共 {len(all_entries)} 個條目 "
        f"({len(md_files)} 個 Markdown 檔 + {len(csv_entries)} 筆台服角色譯名)"
    )
    return all_entries


def get_wiki_stats() -> dict:
    """回傳知識庫各分類統計資訊"""
    entries = load_wiki_entries()
    counts: dict[str, int] = {}
    for e in entries:
        cat = e["category"]
        counts[cat] = counts.get(cat, 0) + 1
    return {
        "total": len(entries),
        "by_category": counts,
    }


def search_wiki_entries(
    query_text: str,
    max_slang: int = 8,
    max_lore: int = 4,
    expand_lightrag: bool = True,
) -> dict:
    """
    混合式精準檢索 + LightRAG 1-hop 實體關聯展開 + CRAG 精準度評分：
    1. 區分「泛用遊戲大類詞（如 殭屍學園、좀고、PVE）」與「具體專有名詞（角色、特定模式、特定黑話）」，
       單憑命中泛用詞不計入有效專有命中，防止拿無關條目餵給小模型引發幻覺。
    2. 回傳 max_specific_score 供上層判定是否應直接觸發「不知道就直說 (Selective Abstention)」拒答閘門。
    """
    entries = load_wiki_entries()
    if not entries or not query_text or not query_text.strip():
        return {
            "slang_hits": [],
            "lore_hits": [],
            "related_hits": [],
            "dynamic_notes": [],
            "max_specific_score": 0.0,
        }

    text_lower = query_text.lower()
    text_compact = re.sub(r"\s+", "", text_lower)

    # 萃取去除口語問句贅字後的核心查詢詞 (如「你可以介紹一下進化感染嗎」->「進化感染」、「介紹一下APO」->「apo」)
    core_query = re.sub(
        r"(?:你可以|可以|能不能|能問|請問|幫我|跟我|介紹一下|介紹|講一下|說明一下|解釋一下|有關|關於|的問題嗎|的問題|是什麼意思|是什麼|是啥|嗎|呢|吧|喔|啊|系列|殭屍學園|殭學|[?？!！。，,\s])+",
        " ",
        text_lower,
    ).strip()
    core_tokens = [t for t in re.split(r"[\s/、,，]+", core_query) if len(t) >= 2 and t not in _GENERIC_GAME_KEYWORDS]
    # 針對連續無空格的中文口語問句（如「時間術士的大招什麼時候開比較好？」），拆解出「時間術士」、「大招」等核心詞片段
    query_subterms = _extract_chinese_subterms(core_query)

    # 1. 執行韓服房名實戰語法結構解析（辨識自由頻道部落招生看板房 vs 副本攻略房）
    dynamic_notes, extra_boost_keywords = _analyze_dynamic_room_patterns(query_text)

    scored_slang: list[tuple[float, dict]] = []
    scored_lore: list[tuple[float, dict]] = []
    max_specific_score = 0.0

    for entry in entries:
        specific_score = 0.0
        generic_score = 0.0
        matched_specific_kws = 0

        if entry["id"].lower() in text_lower:
            specific_score += 15.0
            matched_specific_kws += 1

        for kw in entry["keywords"]:
            kw_l = kw.lower()
            kw_compact = re.sub(r"\s+", "", kw_l)
            if not kw_l:
                continue

            if len(kw_l) == 1 and ord(kw_l[0]) < 0xAC00:
                continue

            is_generic_kw = kw_l in _GENERIC_GAME_KEYWORDS

            if kw_l in extra_boost_keywords:
                specific_score += 12.0
                matched_specific_kws += 1
                continue

            if kw_l.isascii() and kw_l.isalnum() and len(kw_l) <= 3:
                if re.search(rf"(?<![a-z0-9]){re.escape(kw_l)}(?![a-z0-9])", text_lower) or kw_l in core_tokens:
                    if is_generic_kw:
                        generic_score += 0.5
                    else:
                        specific_score += 12.0
                        matched_specific_kws += 1
                continue

            if kw_l in text_lower or (len(kw_compact) >= 2 and kw_compact in text_compact):
                if is_generic_kw:
                    generic_score += 0.5
                else:
                    has_hangul_or_choseong = bool(re.search(r"[\u3131-\u3163\uac00-\ud7a3]", kw_l))
                    weight = 14.0 if has_hangul_or_choseong else (8.0 + min(len(kw_l), 6) * 0.8)
                    specific_score += weight
                    matched_specific_kws += 1
            elif not is_generic_kw and any(ct in kw_compact for ct in core_tokens if len(ct) >= 2):
                specific_score += 7.0
                matched_specific_kws += 1

        # 補充：若關鍵字清單未直接命中，檢查脫去贅字後的 core_tokens 與中文斷詞片段 (query_subterms) 是否出現在內文 (body) 中
        if specific_score == 0.0 and (core_tokens or query_subterms):
            body_l = entry["body"].lower()
            body_no_de = body_l.replace("的", "")
            for ct in core_tokens:
                if len(ct) >= 2 and (ct in body_l or ct.replace("的", "") in body_no_de):
                    specific_score += 6.5
                    matched_specific_kws += 1

            if specific_score == 0.0 and query_subterms:
                matched_long_subterms = 0
                matched_short_subterms = 0
                for st in query_subterms:
                    st_l = st.lower()
                    if st_l in body_l or st_l.replace("的", "") in body_no_de:
                        if len(st_l) >= 3:
                            matched_long_subterms += 1
                        else:
                            matched_short_subterms += 1
                # 命中至少 1 個 3 字以上具體詞（如「時間術士」、「公園路線」、「隱藏商人」）或同時命中 2 個以上 2 字詞（如「時間」+「大招」）
                if matched_long_subterms >= 1 or matched_short_subterms >= 2:
                    specific_score += matched_long_subterms * 7.5 + matched_short_subterms * 3.5
                    matched_specific_kws += matched_long_subterms + matched_short_subterms

        # 只有真正命中「具體專有關鍵字 (specific_score > 0)」的條目才納入檢索結果！
        if specific_score > 0:
            total_score = specific_score + generic_score
            if matched_specific_kws >= 2:
                total_score += matched_specific_kws * 2.5
            if total_score > max_specific_score:
                max_specific_score = total_score
            if entry["category"] == "slang":
                scored_slang.append((total_score, entry))
            else:
                scored_lore.append((total_score, entry))

    scored_slang.sort(key=lambda x: x[0], reverse=True)
    scored_lore.sort(key=lambda x: x[0], reverse=True)

    slang_hits = [item[1] for item in scored_slang[:max_slang]]
    lore_hits = [item[1] for item in scored_lore[:max_lore]]

    # LightRAG 1-hop 實體關聯圖譜展開
    related_hits: list[dict] = []
    if expand_lightrag and (slang_hits or lore_hits):
        selected_ids = {e["id"] for e in slang_hits} | {e["id"] for e in lore_hits}
        by_id = _WIKI_CACHE.get("entries_by_id", {})
        candidate_rel_ids: list[str] = []

        for primary_entry in (lore_hits[:2] + slang_hits[:2]):
            for rel_id in primary_entry.get("related_ids", []):
                if rel_id not in selected_ids and rel_id not in candidate_rel_ids and rel_id in by_id:
                    candidate_rel_ids.append(rel_id)

        for rel_id in candidate_rel_ids[:2]:
            related_hits.append(by_id[rel_id])

    return {
        "slang_hits": slang_hits,
        "lore_hits": lore_hits,
        "related_hits": related_hits,
        "dynamic_notes": dynamic_notes,
        "max_specific_score": max_specific_score,
    }


def is_game_or_korean_context(text: str) -> bool:
    """
    偵測文字是否涉及《殭屍學園》或包含韓文遊戲詞彙（包含韓文音節 가-힣 與初聲子音 ㄱ-ㅎ）。
    """
    if not text:
        return False
    t_lower = text.lower()
    if any(trig in t_lower for trig in GAME_DOMAIN_TRIGGERS):
        return True
    if re.search(r"[\u3131-\u3163\uac00-\ud7a3]{2,}", text):
        return True
    if re.search(r"\d+\s*[퀸렙]", text):
        return True
    return False


def build_auto_rag_context(
    query_text: str,
    ocr_extracted_text: str = "",
    recent_context_text: str = "",
    is_vision: bool = False,
) -> str:
    """
    為輕量模型 (gemini-flash-lite) 建構 Contextual-LightRAG 自動預檢索區塊與 CRAG 三段式「不知道就閉嘴」防幻覺閘門：
    1. 優先僅對「當前最新提問 + 當前圖片 OCR」進行檢索，避免前幾輪歷史對話污染檢索結果導致該拒答時沒拒答。
    2. 僅當「當前最新提問」完全未命中專有名詞、且屬於短句代名詞追問（如「那他的技能呢？」）時，才回退參考 recent_context_text。
    3. 依據檢索覆蓋率分為【全拒答模式 (ABSTAIN)】、【部分未知警告 (PARTIAL)】與【閉卷考精準回答 (CLOSED-BOOK)】。
    """
    primary_query = f"{query_text}\n{ocr_extracted_text}".strip()
    if not primary_query and not recent_context_text:
        return ""

    is_group_query = any(
        kw in primary_query.lower()
        for kw in ("主角群", "主角", "四人組", "1年a班", "10種", "十種", "常見", "種類", "哪些殭屍", "reboot", "리부트", "重製")
    )
    result = search_wiki_entries(
        primary_query,
        max_slang=10 if is_vision else 6,
        max_lore=6 if is_group_query else 4,
        expand_lightrag=True,
    )

    # 若當前句子未命中任何專有名詞，且屬於代名詞追問/短句追問，才拿最近上下文輔助檢索
    q_stripped = (query_text or "").strip()
    is_pronoun_followup = (
        not ocr_extracted_text
        and recent_context_text
        and (
            len(q_stripped) <= 14
            or any(p in q_stripped for p in ("他", "她", "它", "這", "那", "剛剛", "上面", "那隻", "那個"))
        )
    )
    if result["max_specific_score"] == 0.0 and not result["dynamic_notes"] and is_pronoun_followup:
        fallback_result = search_wiki_entries(
            f"{recent_context_text}\n{primary_query}".strip(),
            max_slang=6,
            max_lore=4,
            expand_lightrag=True,
        )
        if fallback_result["max_specific_score"] > 0 or fallback_result["dynamic_notes"]:
            result = fallback_result

    slang_hits = result["slang_hits"]
    lore_hits = result["lore_hits"]
    related_hits = result["related_hits"]
    dynamic_notes = result["dynamic_notes"]

    has_hits = bool(slang_hits or lore_hits or dynamic_notes)
    in_domain = is_game_or_korean_context(primary_query) or (
        is_pronoun_followup and is_game_or_korean_context(recent_context_text)
    )

    if not has_hits and not in_domain:
        return ""

    # =========================================================================
    # 🛑 情況 A：屬於《殭屍學園》或韓服提問，但本地知識庫【完全沒有】對應專有條目！
    # =========================================================================
    if not has_hits and in_domain:
        return (
            "\n\n==========================================\n"
            "🛑【CRAG 知識邊界提醒：本地預檢索未直接命中特定條目】\n"
            "- 系統針對當前句子進行初步檢索時，未找到直接對應的專有條目。\n"
            "- 💡 **處理原則**：\n"
            "  1. 若群友只是在閒聊、感嘆（如「這些到底都是啥」、「好複雜」）或詢問「能不能問某個話題」，請自然回應或用大白話解釋上一段剛聊過的內容，**不要死板地拒答**。\n"
            "  2. 若群友確實是在詢問某個特定的《殭屍學園》角色、關卡、怪物或韓服縮寫，你可先嘗試換個關鍵字呼叫 `lookup_game_wiki(query)` 工具查詢；若依然查無資料，請用自然的群友口吻坦白說那個名詞或細節你沒記熟、手邊沒確切資料，**絕對禁止自己瞎掰設定**（且每次回答請自然換句話說，不要像複製貼上一樣重複同一句台詞）。\n"
            "=========================================="
        )

    unmatched_kr = _detect_unmatched_korean_tokens(
        primary_query, slang_hits, lore_hits, related_hits
    )
    unmatched_specific = _extract_unmatched_specific_zh_en_terms(
        query_text, slang_hits, lore_hits, related_hits
    )
    all_unmatched = list(dict.fromkeys(unmatched_kr + unmatched_specific))

    blocks: list[str] = []
    blocks.append("\n\n==========================================")
    blocks.append("🎯【《殭屍學園 (좀비고등학교)》台韓服實戰知識庫參考資料（100% 閉卷考模式）】")

    if ocr_extracted_text.strip():
        blocks.append(
            f"\n📸 **【Pass-1 圖中提取之文字與畫面視覺實體】**：\n"
            f"{ocr_extracted_text.strip()}"
        )

    if dynamic_notes:
        blocks.append("\n⚡ **【韓服實戰語法與複合縮寫結構判定（最高優先）】**：")
        for note in dynamic_notes:
            blocks.append(note)

    if all_unmatched:
        unmatched_str = "、".join(f"`{t}`" for t in all_unmatched[:8])
        blocks.append(
            f"\n🛑 **【CRAG 部分未知攔截（Unknown Token Abstention Gate — 極重要！）】**：\n"
            f"  * 系統偵測到提問或圖片中含有以下**未收錄於本地知識庫**的詞彙／項目：{unmatched_str}。\n"
            f"  * **強制部分拒答規定**：\n"
            f"    1. 針對下方【已命中的條目】，請照實回答。\n"
            f"    2. 針對上述【未收錄的詞彙 `{unmatched_str}`】，**絕對禁止自己亂猜意思，也嚴禁硬湊到旁邊已知的詞彙上**！請直接在回覆中老實說：「至於 `{unmatched_str}` 這部分我手邊沒確切資料／沒印象，就不亂猜了。」"
        )

    if slang_hits:
        blocks.append("\n📖 **【對應之韓服開房術語與縮寫釋義】**：")
        for e in slang_hits:
            blocks.append(e["body"])

    if lore_hits:
        blocks.append("\n🗂️ **【對應之《殭屍學園》台服官方角色譯名／模式／劇情設定】**：")
        for e in lore_hits:
            blocks.append(e["body"])

    if related_hits:
        blocks.append("\n🔗 **【延伸背景補充】**：")
        for e in related_hits:
            compact_body = e["body"]
            if len(compact_body) > 240:
                compact_body = compact_body[:240] + "..."
            blocks.append(compact_body)

    blocks.append(
        "\n🛡️ **【100% 閉卷考防幻覺鐵律（務必遵守）】**：\n"
        "1. **「不知道就直說、有幾分證據講幾分話」（最高核心原則）**：\n"
        "   - 請將所有《殭屍學園》相關問答視為**嚴格的「閉卷考」**：**只有上方【參考資料】白紙黑字寫出來的角色設定、職業、怪物、Boss 關卡與黑話才能回答**！\n"
        "   - 如果群友問的某個細節、某個關卡、某個角色或某個韓文詞彙**沒有出現在上方【參考資料】中**，或者上方資料只有 5 個但群友討 10 個，**請直接坦白說「這部分我沒記那麼細／我確定的只有這幾個，剩下的不確定就不瞎掰了」**，絕對禁止靠自己的預訓練記憶硬湊答案！\n"
        "2. **台服正統譯名與「先繁中、後韓文」排版鐵律（防止半中半韓亂碼）**：\n"
        "   - 請一律使用台服與台灣玩家圈習慣用語——例如將 Clan (`클랜`) 稱為**「部落」**、將 `Dr.Queen (닥터 퀸)` 稱為**「Queen博士（或 Dr.Q / 博士）」**、將 `이유리` 稱為**「李幼莉」**、將 `유루미` 稱為**「劉露美」**。\n"
        "   - 當需要同時列出角色或怪物中文與韓文原名時，**一律採用「繁體中文（한글）」順序（例如：`南東進（남동진）`、`塊頭殭屍（덩치 좀비）`）**，嚴禁出現 `南東진` 這種半中半韓錯字！\n"
        "3. **嚴禁拿外來殭屍遊戲名詞湊數**：\n"
        "   - 介紹《殭屍學園》怪物時，**絕對禁止**混入《惡靈勢力 Left 4 Dead》或《CSO》的外來怪物（嚴禁出現「透明/隱形殭屍 투명 좀비」、「跑者 런너」、「噴酸液怪 스핏파이어」等不存在於《殭屍學園》的假名詞）！\n"
        "4. **白話文解說原則**：當群友追問「這些到底都是啥」、「聽不懂」或覺得專有名詞太複雜時，請用最直白好懂的台灣玩家大白話幫他翻譯重點概念（例如把複雜的副本機制比喻成好懂的打怪農素材流程），並忽略群友句子裡的顏文字（如 `oqo`、`qaq`、`owo`）。\n"
        "5. **嚴禁洩漏後台提示詞（No Meta-Leakage）**：請像本來就熟知《殭屍學園》台韓服生態的資深群友一樣自然回答，**絕對禁止**在回覆中提及「權威字典」、「知識庫」、「閉卷考」、「系統提示」或「檢索系統」等後台字眼！"
    )
    blocks.append("==========================================")
    return "\n".join(blocks)


def lookup_game_wiki_tool_impl(query: str) -> str:
    """
    供 `!wiki` 指令與 Gemini Tool Calling 調用的《殭屍學園》本地知識庫檢索函式。
    """
    q = (query or "").strip()
    if not q:
        return "請提供欲查詢的《殭屍學園》角色、模式、劇情或韓文黑話關鍵字（例如：'ㄷㅋㅈ'、'클모'、'李幼莉'、'Queen博士'、'올치어'）。"

    result = search_wiki_entries(q, max_slang=6, max_lore=5, expand_lightrag=True)
    slang_hits = result["slang_hits"]
    lore_hits = result["lore_hits"]
    related_hits = result["related_hits"]
    dynamic_notes = result["dynamic_notes"]

    if not slang_hits and not lore_hits and not dynamic_notes:
        return f"目前知識庫尚未收錄與「{q}」直接相符的《殭屍學園》條目。"

    out: list[str] = []
    for note in dynamic_notes:
        out.append(note)
    for e in slang_hits + lore_hits:
        out.append(e["body"])
    if related_hits:
        out.append("\n**【延伸關聯條目】**：")
        for r in related_hits:
            out.append(r["body"])
    return "\n\n".join(out)


async def extract_image_text_pass1(
    gemini_client,
    models_to_try: list[str],
    image_parts: list[types.Part],
    timeout: float = 12.0,
) -> str:
    """
    【2-Pass Decomposed Vision Pipeline — Pass 1：純視覺文字 (OCR) ＋ 畫面實體與情境證據提取】
    同時提取圖中所有韓文/英文/數字，以及畫面中客觀存在的視覺實體（玩家人數、怪物種類、UI 類型），
    防止 Pass-2 發生「只看局部文字而無視畫面怪物」的視覺幻覺。
    """
    if not image_parts:
        return ""

    ocr_prompt = (
        "請判斷這張圖片是否為像素風2D遊戲畫面或含有韓文文字：\n"
        "- 若這只是日常迷因梗圖、動漫表情貼圖、貓狗照片或一般 GIF（無韓文也非 2D 像素遊戲畫面），請直接只輸出「NONE」四個英文字母，不要輸出任何其他文字。\n"
        "- 若這是遊戲畫面或含有韓文，請執行【雙重視覺客觀提取】（不要進行主觀翻譯或腦補，只需客觀描述）：\n"
        "  1. 【OCR 文字抄錄】：將圖中出現的所有韓文、英文與數字（包含大字標題如 히2하드 올치어 아처 머신건、分數如 SCORE 259450、對話框如 점수 실화냐..?、玩家頭頂文字如 포토존 민멍 절제、或大廳房間列表如 prace 조건x、11렙 오더 클모 等）原封不動逐行抄錄出來。\n"
        "  2. 【畫面視覺實體】：用 1~2 句話客觀描述畫面中「實際看得到的遊戲場景與物件」（例如：是大廳房間列表 UI，還是實戰戰鬥畫面？畫面中有幾名玩家角色？旁邊包圍著什麼外觀的怪物，例如穿藍白劍道服拿竹刀的劍道殭屍、綁馬尾穿啦啦隊服的啦啦隊殭屍、或是特定 Boss？）。"
    )
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=ocr_prompt)] + list(image_parts),
        )
    ]
    config = types.GenerateContentConfig(
        temperature=0.0,
    )

    for m in models_to_try:
        try:
            resp = await asyncio.wait_for(
                gemini_client.aio.models.generate_content(
                    model=m,
                    contents=contents,
                    config=config,
                ),
                timeout=timeout,
            )
            if resp and resp.text:
                extracted = resp.text.strip()
                if extracted.upper() == "NONE" or extracted.startswith("NONE"):
                    logger.info(f"👁️ (Pass-1 視覺預提取 [{m}]) 判定為一般貼圖/日常動圖，跳過遊戲知識庫檢索。")
                    return ""
                logger.info(f"👁️ (Pass-1 視覺預提取 [{m}]) 成功提取文字與畫面實體: {extracted[:180]}...")
                return extracted
        except Exception as e:
            logger.debug(f"Pass-1 OCR 於模型 {m} 略過: {e}")
            continue
    return ""


def _split_keywords_from_title(raw_title: str) -> tuple[str, list[str]]:
    """從標題或關鍵字字串（例如 '進化感染 / 진화 감염 / 에볼루션'）拆解主標題與索引關鍵字清單"""
    cleaned = raw_title.strip().strip("【】[]#*- \t")
    parts = [p.strip() for p in re.split(r"[/,，、|｜()（）]", cleaned) if p.strip()]
    if not parts:
        return (cleaned or "群友補充條目"), [cleaned] if cleaned else []
    main_title = parts[0]
    kws = list(dict.fromkeys([cleaned] + parts))
    return main_title, kws


def _extract_auto_keywords_from_text(text: str, max_kws: int = 20) -> list[str]:
    """
    針對群友輸入的「無標示純文字觀念」，在完全不依賴特殊符號的情況下，
    自動抽取韓文詞彙、英文/縮寫詞彙與中文專有觀念詞組（含「X的Y」與「XY」雙向索引）。
    """
    if not text:
        return []
    candidates: list[str] = []

    # 1. 韓文詞彙 (如 올치어, 파멸의 균열)
    for kr in re.findall(r"[\u3131-\u3163\uac00-\ud7a3]{2,}", text):
        if kr not in _COMMON_KR_STOPWORDS:
            candidates.append(kr)

    # 2. 英文/數字代號 (如 APO, EP3, H2)
    for en in re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,14}", text):
        if en.lower() not in _GENERIC_GAME_KEYWORDS:
            candidates.append(en)

    # 3. 引號與粗體強調詞
    for m_q in re.findall(r"(?:「([^」]{2,15})」|『([^』]{2,15})』|\*\*([^*]{2,15})\*\*)", text):
        for g in m_q:
            if g:
                candidates.append(g.strip())

    # 4. 連續中文句子的專有觀念詞切分
    candidates.extend(_extract_chinese_subterms(text))

    dedup: list[str] = []
    seen: set[str] = set()
    for c in candidates:
        cl = c.strip().lower()
        if cl and cl not in seen and cl not in _GENERIC_GAME_KEYWORDS:
            seen.add(cl)
            dedup.append(c.strip())
            if len(dedup) >= max_kws:
                break
    return dedup


def parse_community_wiki_blocks(raw_text: str, default_title: str = "") -> list[dict]:
    """
    解析群友透過 `!wiki add` 或 `!wiki import` 貼上／上傳的《殭屍學園》補充文本。
    同時支援 4 種常見格式，並對每個區塊自動補齊內文專有關鍵字索引：
      1. 【標題 / 關鍵字1, 關鍵字2】\n多行內容...
      2. Markdown 標題分段：# 標題 或 ## 標題 或 ### 標題\n多行內容...
      3. 單行或多行「關鍵字 | 內容」或「關鍵字：內容」
      4. 無標示純文字觀念／整份 .txt 筆記（自動切分觀念段落並抽取全文專有名詞作為檢索標籤）
    """
    text = (raw_text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    results: list[dict] = []

    def _merge_auto_kws(base_kws: list[str], body_str: str) -> list[str]:
        merged = list(base_kws)
        seen_l = {k.lower() for k in merged}
        for ak in _extract_auto_keywords_from_text(body_str, max_kws=35):
            if ak.lower() not in seen_l:
                seen_l.add(ak.lower())
                merged.append(ak)
        return merged

    # 格式 1：帶有【標題/關鍵字】區塊
    if "【" in text and "】" in text:
        blocks = re.split(r"(?=【[^】]+】)", text)
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            m = re.match(r"^【([^】]+)】\s*(.+)$", block, flags=re.DOTALL)
            if m:
                raw_kw, content = m.group(1).strip(), m.group(2).strip()
                if raw_kw and content:
                    title, kws = _split_keywords_from_title(raw_kw)
                    results.append({"name_zh": title, "keywords": _merge_auto_kws(kws, content), "body": content})
        if results:
            return results

    # 格式 2：Markdown 標題分段 (# / ## / ###)
    md_heading_matches = list(re.finditer(r"(?:^|\n)(#{1,4})\s+([^\n]+)\n", text))
    if md_heading_matches:
        for idx, m in enumerate(md_heading_matches):
            raw_kw = m.group(2).strip()
            start_pos = m.end()
            end_pos = md_heading_matches[idx + 1].start() if idx + 1 < len(md_heading_matches) else len(text)
            content = text[start_pos:end_pos].strip()
            if raw_kw and content:
                title, kws = _split_keywords_from_title(raw_kw)
                if default_title.strip():
                    _, extra_kws = _split_keywords_from_title(default_title)
                    for ek in extra_kws:
                        if ek not in kws:
                            kws.append(ek)
                results.append({"name_zh": title, "keywords": _merge_auto_kws(kws, content), "body": content})
        if results:
            return results

    # 格式 3：單行「關鍵字 | 內容」
    if "|" in text or "｜" in text:
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        if len(lines) == 1 or all(("|" in ln or "｜" in ln) for ln in lines):
            for ln in lines:
                sep = "|" if "|" in ln else "｜"
                raw_kw, content = ln.split(sep, 1)
                raw_kw, content = raw_kw.strip(), content.strip()
                if raw_kw and content:
                    title, kws = _split_keywords_from_title(raw_kw)
                    results.append({"name_zh": title, "keywords": _merge_auto_kws(kws, content), "body": content})
            if results:
                return results
        else:
            first_sep = "|" if "|" in text.split("\n", 1)[0] else ("｜" if "｜" in text.split("\n", 1)[0] else "")
            if first_sep:
                raw_kw, content = text.split(first_sep, 1)
                raw_kw, content = raw_kw.strip(), content.strip()
                if raw_kw and content:
                    title, kws = _split_keywords_from_title(raw_kw)
                    return [{"name_zh": title, "keywords": _merge_auto_kws(kws, content), "body": content}]

    # 格式 4：無標示純文字觀念／長篇筆記（自動分段 + 自動抽取核心觀念詞作為標題與檢索標籤）
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []

    base_title = ""
    base_kws: list[str] = []
    body_full = text

    if default_title.strip():
        base_title, base_kws = _split_keywords_from_title(default_title)
    elif len(lines) >= 2 and len(lines[0]) <= 32 and not any(p in lines[0] for p in ("，", "。", "！", "？")):
        base_title, base_kws = _split_keywords_from_title(lines[0])
        body_full = "\n".join(lines[1:]).strip()
    else:
        m_colon = re.match(r"^([^:：,，。]{2,24})[:：]\s*(.+)$", text, flags=re.DOTALL)
        if m_colon:
            base_title, base_kws = _split_keywords_from_title(m_colon.group(1))
            body_full = m_colon.group(2).strip()

    # 將無標示長文按段落或句號切分為 ~500 字的獨立觀念區塊，避免多個不同觀念擠在同一大塊
    raw_paragraphs = [p.strip() for p in re.split(r"\n+", body_full) if p.strip()]
    chunks: list[str] = []
    cur_chunk = ""
    for p in raw_paragraphs:
        if len(cur_chunk) + len(p) + 1 <= 550:
            cur_chunk = f"{cur_chunk}\n{p}".strip() if cur_chunk else p
        else:
            if cur_chunk:
                chunks.append(cur_chunk)
            if len(p) <= 600:
                cur_chunk = p
            else:
                # 單一超長無換行段落：按句號「。」切分
                sentences = [s.strip() for s in re.split(r"(?<=[。！？!?])", p) if s.strip()]
                for s in sentences:
                    if len(cur_chunk) + len(s) <= 550:
                        cur_chunk = f"{cur_chunk}{s}" if cur_chunk else s
                    else:
                        if cur_chunk:
                            chunks.append(cur_chunk)
                        cur_chunk = s
    if cur_chunk:
        chunks.append(cur_chunk)

    for idx, ch in enumerate(chunks, start=1):
        auto_kws = _merge_auto_kws(base_kws, ch)
        if base_title:
            part_title = f"{base_title} ({idx})" if len(chunks) > 1 else base_title
        else:
            # 自動取該段落抽出的前 3 個互不包含的代表性專有詞組成標題（如「破滅的裂隙 / 絕望的種子 / 時間術士」）
            top_terms: list[str] = []
            for k in auto_kws:
                if len(k) < 2:
                    continue
                if any((k in existing or existing in k) for existing in top_terms):
                    continue
                top_terms.append(k)
                if len(top_terms) >= 3:
                    break
            part_title = " / ".join(top_terms) if top_terms else re.sub(r"\s+", " ", ch)[:16].strip()
        results.append({
            "name_zh": part_title,
            "keywords": auto_kws if auto_kws else [part_title],
            "body": ch,
            "is_unmarked": not bool(base_title),
        })
    return results


async def enrich_community_entries_with_ai(
    gemini_client,
    models_to_try: list[str],
    parsed_entries: list[dict],
    timeout: float = 8.0,
) -> list[dict]:
    """
    【原文不動、AI 僅輔助補強標籤 (Hybrid Safe Indexing)】：
    針對群友丟進來的「無標示純文字觀念」，條目內文 (`body`) 100% 由純 Python 原封不動保留（絕不讓 LLM 改動半個字），
    僅利用 Gemini 的強制 Schema 結構化輸出 (`response_schema`) 為該段落補充口語同義詞、簡稱與主題標題。
    若 API 超時或失敗，自動無縫退回純 Python 斷詞結果，100% 穩健不壞檔。
    """
    if not gemini_client or not parsed_entries:
        return parsed_entries

    import json as _json

    schema = types.Schema(
        type=types.Type.ARRAY,
        description="每個輸入段落對應的主題標題與搜尋關鍵字清單",
        items=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "index": types.Schema(type=types.Type.INTEGER, description="段落編號 (從 0 開始)"),
                "title": types.Schema(type=types.Type.STRING, description="該段觀念的簡短主題名稱 (6~18字)"),
                "keywords": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="從該段文字提取出的專有名詞、角色、模式、怪物、技能、簡稱與玩家可能搜尋的同義詞 (5~12個)",
                ),
            },
            required=["index", "title", "keywords"],
        ),
    )

    segments_text = "\n\n".join(
        f"[段落 {i}]\n{item['body'][:600]}" for i, item in enumerate(parsed_entries[:10])
    )
    prompt = (
        "請針對以下《殭屍學園 (좀비고등학교)》群友補充的觀念段落，為每個段落提取一個簡短主題標題 (title) "
        "以及 5~12 個搜尋關鍵字 (keywords，包含文中的模式名、角色/職業名、怪物名、機制詞，以及台灣玩家常用的去『的』字簡稱或同義詞)。\n"
        "請注意：只提取文中實際提及的概念與同義搜尋詞，不要捏造文中沒有的設定。\n\n"
        f"{segments_text}"
    )

    config = types.GenerateContentConfig(
        temperature=0.0,
        response_mime_type="application/json",
        response_schema=schema,
    )

    for m in models_to_try[:2]:
        try:
            resp = await asyncio.wait_for(
                gemini_client.aio.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=config,
                ),
                timeout=timeout,
            )
            if resp and resp.text:
                data = _json.loads(resp.text)
                if isinstance(data, list):
                    for row in data:
                        idx = row.get("index")
                        if isinstance(idx, int) and 0 <= idx < len(parsed_entries):
                            ai_title = (row.get("title") or "").strip()
                            ai_kws = row.get("keywords") or []
                            if ai_title and parsed_entries[idx].get("is_unmarked"):
                                parsed_entries[idx]["name_zh"] = ai_title
                            existing_kws = parsed_entries[idx].get("keywords", [])
                            seen_l = {k.lower() for k in existing_kws}
                            for ak in ai_kws:
                                ak_s = str(ak).strip()
                                if ak_s and ak_s.lower() not in seen_l and ak_s.lower() not in _GENERIC_GAME_KEYWORDS:
                                    seen_l.add(ak_s.lower())
                                    existing_kws.append(ak_s)
                            parsed_entries[idx]["keywords"] = existing_kws
                    return parsed_entries
        except Exception as e:
            logger.debug(f"AI 輔助標籤提取於模型 {m} 略過 (已使用純 Python 斷詞兜底): {e}")
            continue

    return parsed_entries


def _get_next_contrib_id() -> int:
    """取得下一個可用的群友補充條目編號 (contrib_N)"""
    entries = load_wiki_entries()
    max_num = 0
    for e in entries:
        m = re.match(r"^contrib_(\d+)$", e.get("id", ""))
        if m:
            num = int(m.group(1))
            if num > max_num:
                max_num = num
    return max_num + 1


def add_community_wiki_entries(entries_to_add: list[dict], author_name: str = "") -> list[dict]:
    """
    將解析後的群友補充條目寫入 data/game_wiki/community_contributions.md，
    並立即觸發 load_wiki_entries(force_reload=True) 熱重載生效。
    """
    if not entries_to_add:
        return []

    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    existing_text = ""
    if COMMUNITY_WIKI_PATH.exists():
        try:
            existing_text = COMMUNITY_WIKI_PATH.read_text(encoding="utf-8").rstrip()
        except Exception as e:
            logger.error(f"讀取 {COMMUNITY_WIKI_PATH.name} 失敗: {e}")

    if not existing_text:
        existing_text = (
            "# 《殭屍學園 (좀비고등학교)》群友補充知識庫 (Community Contributions)\n"
            "> 本檔案由伺服器成員透過 `!wiki add` 與 `!wiki import` 指令共同編輯維護，修改後自動熱重載生效。\n"
        )

    next_num = _get_next_contrib_id()
    added_records: list[dict] = []
    new_blocks: list[str] = []

    for item in entries_to_add:
        entry_id = f"contrib_{next_num}"
        next_num += 1
        name_zh = (item.get("name_zh") or "群友補充條目").strip().replace("\n", " ")
        kws = item.get("keywords") or [name_zh]
        clean_kws = []
        for k in kws:
            ks = str(k).strip().replace(",", " ").replace("[", "").replace("]", "")
            if ks and ks not in clean_kws:
                clean_kws.append(ks)
        body = (item.get("body") or "").strip()
        if author_name:
            body_with_credit = f"{body}\n- **補充提供者**：{author_name}"
        else:
            body_with_credit = body

        kw_str = ", ".join(clean_kws)
        block = (
            f"\n---\n"
            f"id: {entry_id}\n"
            f"category: community_contrib\n"
            f"name_zh: {name_zh}\n"
            f"keywords: [{kw_str}]\n"
            f"related_ids: []\n"
            f"---\n"
            f"【《殭屍學園》[群友補充資料] — {name_zh}】\n"
            f"{body_with_credit}\n"
        )
        new_blocks.append(block)
        added_records.append({
            "id": entry_id,
            "name_zh": name_zh,
            "keywords": clean_kws,
            "body": body,
        })

    updated_content = existing_text + "\n" + "".join(new_blocks)
    COMMUNITY_WIKI_PATH.write_text(updated_content, encoding="utf-8")
    load_wiki_entries(force_reload=True)
    return added_records


def list_community_wiki_entries() -> list[dict]:
    """列出所有由群友補充的《殭屍學園》知識庫條目"""
    entries = load_wiki_entries()
    return [e for e in entries if e.get("category") == "community_contrib" or e.get("source_file") == COMMUNITY_WIKI_PATH.name]


def delete_community_wiki_entry(target_id: str) -> bool:
    """根據條目 ID（如 contrib_1 或 1）刪除群友補充的知識庫條目"""
    if not COMMUNITY_WIKI_PATH.exists():
        return False

    tid = (target_id or "").strip().lower().lstrip("#")
    if tid.isdigit():
        tid = f"contrib_{tid}"

    try:
        content = COMMUNITY_WIKI_PATH.read_text(encoding="utf-8")
    except Exception:
        return False

    pattern = re.compile(
        r"(?:^|\n)---\r?\n(id:\s*[^\r\n]+.*?)\r?\n---\r?\n",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(content))
    if not matches:
        return False

    found = False
    kept_parts: list[str] = [content[:matches[0].start()].rstrip()]

    for idx, match in enumerate(matches):
        yaml_block = match.group(1)
        seg_start = match.start()
        seg_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(content)
        full_segment = content[seg_start:seg_end].strip()

        entry_id = ""
        for line in yaml_block.splitlines():
            if line.strip().lower().startswith("id:"):
                entry_id = line.split(":", 1)[1].strip().strip("'\"").lower()
                break

        if entry_id == tid:
            found = True
            continue
        kept_parts.append("\n\n" + full_segment)

    if not found:
        return False

    new_content = "".join(kept_parts).rstrip() + "\n"
    COMMUNITY_WIKI_PATH.write_text(new_content, encoding="utf-8")
    load_wiki_entries(force_reload=True)
    return True

