"""Constants, paths, domain triggers, stopwords, synonyms, and precompiled regexes for game_wiki."""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger("DiscordGeminiBot.GameWiki")

WIKI_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "game_wiki"
CHAR_CSV_PATH = WIKI_DIR / "character_translations_tw.csv"
COMMUNITY_WIKI_PATH = WIKI_DIR / "community_contributions.md"
MAX_COMMUNITY_IMPORT_BATCH = 30
MAX_COMMUNITY_ENTRIES = 200

CATEGORY_LABELS = {
    "slang": "韓服黑話與開房用語",
    "evolution": "進化感染模式專區",
    "cafe_meta": "韓服民間攻略與流派",
    "character": "角色深度設定",
    "character_dict": "台服角色譯名與特徵對照",
    "story_arc": "主線與分部劇情",
    "game_mode": "遊戲模式與職業機制",
    "au_series": "平行世界與活動",
    "community_contrib": "群友補充與改版熱修補",
}

# 判斷是否為《殭屍學園》或韓服相關提問的觸發特徵詞
GAME_DOMAIN_TRIGGERS = (
    "殭屍學園", "殭學", "좀비고", "좀고", "zombie high", "進擊的殭屍", "진좀", "히어로즈", "히즈",
    "디비전", "division", "아포칼립스", "아포", "apo", "啟示錄", "進化感染", "에볼루션", "감염모드", "感染模式", "越獄模式",
    "경찰과 도둑", "경도", "래더", "엘프고", "은하고", "마법고", "픽셀고", "불새재단", "火鳥基金會",
    "火鳥財團", "靈魂石", "소울스톤", "reboot", "리부트", "重製版", "破滅的裂隙", "파멸의 균열",
    "절망의 씨앗", "투지의 불씨", "主角群", "主角", "四人組", "1年a班", "南東進", "李幼莉", "幼莉",
    "劉露美", "青蛙女", "金俊浩", "鄭藝瑟", "鄭東碩", "高娜萊", "郭俊亨", "林賢智", "撒母爾",
    "白雪蓮", "校婆", "倫達", "론다", "Queen博士", "Dr.Q", "닥터 퀸", "클모", "ㄷㅋㅈ", "34퀸",
    "오더", "조건x", "자유킥", "다잡", "올치어", "히2하드", "머신건", "포토존", "部落", "椅子男",
    "紅弓", "綠弓", "軍醫", "揮棒男", "火焰手", "滑板", "房名", "韓服",
    "革命感染", "에볼", "겹스", "燒瓶", "阿修羅", "武器庫", "連體機", "死亡機", "隱藏飲料機",
)

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

# 跨系列重複之章節修飾詞：單獨命中（如僅問 "EP1"）不得直接算入特定系列專有分數，必須搭配系列或版本座標
_MODIFIER_CHAPTER_TOKENS = {
    "ep0", "ep1", "ep2", "ep3", "ep4", "ep.0", "ep.1", "ep.2", "ep.3", "ep.4",
    "第0章", "第1章", "第2章", "第3章", "第4章",
    "第一章", "第二章", "第三章", "第四章",
    "第一關", "第二關", "第三關", "第四關",
    "第1關", "第2關", "第3關", "第4關",
    "1부", "2부", "3부", "第一部", "第二部", "第三部",
    "엪1", "엪2", "엪3", "1라", "2라", "3라",
}

_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "石弓": ("十字弓", "紅弓", "석궁"),
    "藥水": ("藥水瓶", "燒瓶"),
    "擴音器": ("喇叭",),
    "掃帚": ("掃把",),
    "狂氣": ("警衛", "狂氣殭屍"),
    "脈衝": ("雷射槍", "脈衝光束"),
    "技術室": ("武器庫",),
    "破爛的學校": ("教室",),
    "地下監獄": ("監獄",),
    "食堂": ("廚房",),
    "餐廳": ("廚房",),
}
_QUERY_SYNONYMS_LOWER: dict[str, tuple[str, ...]] = {
    k.lower(): tuple(s.lower() for s in v) for k, v in _QUERY_SYNONYMS.items()
}

_CATEGORY_QUERY_ALIASES: dict[str, str] = {
    "角色": "character_dict",
    "角色列表": "character_dict",
    "角色目錄": "character_dict",
    "人物": "character_dict",
    "譯名": "character_dict",
    "角色譯名": "character_dict",
    "台服角色": "character_dict",
    "黑話": "slang",
    "黑話目錄": "slang",
    "韓服黑話": "slang",
    "術語": "slang",
    "縮寫": "slang",
    "開房用語": "slang",
    "進化感染目錄": "evolution",
    "革命感染目錄": "evolution",
    "進化感染專區": "evolution",
    "進化感染列表": "evolution",
    "民間攻略": "cafe_meta",
    "流派": "cafe_meta",
    "meta": "cafe_meta",
    "cafe": "cafe_meta",
    "模式目錄": "game_mode",
    "武器總表": "game_mode",
    "武器目錄": "game_mode",
    "群友補充": "community_contrib",
    "補充條目": "community_contrib",
    "熱修補": "community_contrib",
}

_SHORT_QUALIFIER_STOP_CHARS = set("的了嗎呢吧喔啊是在有和跟或與怎啥誰幾哪要會能可給把被去來打用看說")

_KR_PARTICLES = (
    "인데도", "이라서", "인데", "에서", "으로", "부터", "까지", "한테", "보다",
    "처럼", "이랑", "하고", "이면", "라서",
    "은", "는", "이", "가", "을", "를", "에", "로", "도", "만", "와", "과", "랑", "면", "의", "께",
)

_KR_TRANSLATION_CUES = (
    "翻譯", "翻一下", "翻成中文", "幫我翻", "這句韓文", "韓文意思",
    "韓文是什麼", "看不懂這句", "寫什麼", "在說什麼", "什麼意思", "啥意思", "translate",
)

# Precompiled regular expressions
_WHITESPACE_RE = re.compile(r"\s+")
_MULTI_SPACE_RE = re.compile(r"\s{2,}")
_DELIM_SPLIT_RE = re.compile(r"[\s/、,，]+")
_SUBJECT_SPLIT_RE = re.compile(r"[/（）()、,，&與和及]")
_TITLE_SPLIT_RE = re.compile(r"[/,，、|｜()（）]")

_HANGUL_RE = re.compile(r"[\u3131-\u3163\uac00-\ud7a3]")
_HANGUL_TOKEN_RE = re.compile(r"[\u3131-\u3163\uac00-\ud7a3]{2,}")
_HANGUL_SYLLABLE_ONLY_RE = re.compile(r"[\uac00-\ud7a3]+")
_HANGUL_SYLLABLE_TOKEN_RE = re.compile(r"[\uac00-\ud7a3]{2,}")
_CHOSEONG_JAMO_RE = re.compile(r"[\u3131-\u3163]")
_CJK_HANGUL_CHAR_RE = re.compile(r"[\u4e00-\u9fff\uac00-\ud7a3\u3131-\u3163]")
_NON_CJK_RE = re.compile(r"[^\u4e00-\u9fff]+")
_ZH_X_OF_Y_RE = re.compile(r"([\u4e00-\u9fff]{2,4})的([\u4e00-\u9fff]{2,4})")

# 口語問句與連接詞停用表（保留「的」以利辨識「破滅的裂隙」這類專有名詞）：用於切分核心檢索詞
_ZH_STOP_PHRASES_WITHOUT_DE_RE = re.compile(
    r"(?:什麼時候|什麼地方|哪裡可以|哪裡有|哪裡|怎麼走|怎麼打|怎麼拿|怎麼開|怎麼玩|怎麼用|怎麼辦|怎麼|"
    r"要注意什麼|有什麼用|有什麼|為什麼|是不是|好不好|比較好|比較容易|一定要|如果想要|如果|想要|"
    r"第一階段|第二階段|第三階段|左上角|右上角|左下角|右下角|選的是|記得留|記得|的時候|打爆之後|之後|"
    r"會出特殊的|會出特殊|會出|另外|隊友|還有|比較|容易|遇到|可以疊加|可以|疊加|三次|幾次|裡面|裡|請問|能問|介紹一下|介紹|"
    r"講一下|說明一下|解釋一下|有關|關於|的問題嗎|的問題|是什麼意思|是什麼|是啥|"
    r"這個|那個|哪些|哪個|因為|所以|但是|而且|或者|還是|應該|需要|必須|不要|不能|一下|一個|一些|非常|特別|直接|"
    r"[了嗎呢吧喔啊是在有與和跟或把被去打買開走拿用看說讓給到從對向往上])+"
)

_QUERY_FILLER_RE = re.compile(
    r"(?:你可以|可以|能不能|能問|請問|幫我|跟我|介紹一下|介紹|講一下|說明一下|解釋一下|有關|關於|的問題嗎|的問題|是什麼意思|是什麼|是啥|嗎|呢|吧|喔|啊|系列|殭屍學園|殭學|[?？!！。，,\s])+"
)

_CLAN_RECRUIT_RE = re.compile(r"(\d+)\s*렙\s+([^\n\r()]+?)\s*클모", re.IGNORECASE)
_COND_X_NAMED_RE = re.compile(r"([a-zA-Z0-9가-힣_]{2,15})\s+조건\s*[xX무]", re.IGNORECASE)
_COND_X_RE = re.compile(r"조건\s*[xX무]", re.IGNORECASE)
_STAGE_ABBR_RE = re.compile(r"(히즈?|진좀?|디비전?|디)\s*([0-4])\s*(이지|노말|하드|나메|악몽)?", re.IGNORECASE)
_LEVEL_N_RE = re.compile(r"(\d+)\s*렙", re.IGNORECASE)
_KR_STAGE_TOKEN_RE = re.compile(r"^(히|진|디)\d*(이지|노말|하드|나메)?$")
_GAME_QUEEN_LEVEL_RE = re.compile(r"\d+\s*[퀸렙]")

_FRONTMATTER_BLOCK_RE = re.compile(
    r"(?:^|\n)---\r?\n(id:\s*[^\r\n]+.*?)\r?\n---\r?\n",
    flags=re.DOTALL,
)
_BODY_TERM_EXTRACT_RE = re.compile(r"(?:「([^」]{2,15})」|〈([^〉]{2,15})〉|\*\*([^*：:]{2,15})\*\*)")

_UNMATCHED_ZH_PREFIX_CLEAN_RE = re.compile(r"殭屍(?:學園|高中|高)")
_UNMATCHED_ZH_FILLER_CLEAN_RE = re.compile(
    r"(?:能問|請問|介紹一下|介紹|關於|有關|裡面的|裡的|裡面|出現的|常見的|十種|10種|[的了嗎呢吧是和與跟有打隻種個這那])"
)
_UNMATCHED_ZH_TERM_RE = re.compile(r"([\u4e00-\u9fff]{2,4}(?:殭屍|博士))")

_COORD_JINJOM_RE = re.compile(r"(?<![a-z0-9])진\s*[1-3](?![0-9])")
_COORD_HEROES_RE = re.compile(r"(?<![a-z0-9])(?:h|히즈?)\s*[1-4](?![0-9])")
_COORD_APO_RE = re.compile(r"(?<![a-z0-9])apo(?![a-z0-9])")
_COORD_DIVISION_RE = re.compile(r"(?<![a-z0-9])(?:d|디비전?|디)\s*[1-3]h?(?![0-9])")
_COORD_EP_RES: dict[str, re.Pattern[str]] = {
    "ep0": re.compile(r"(?:ep\.?\s*0(?![0-9])|第\s*0\s*章|序章)"),
    "ep1": re.compile(r"(?:ep\.?\s*1(?![0-9])|엪1(?![0-9])|第\s*[一1]\s*[章關部]|1부(?![0-9])|(?<![a-z0-9])(?:h|히즈?|진좀?|진|d|디비전?|디)\s*1(?![0-9]))"),
    "ep2": re.compile(r"(?:ep\.?\s*2(?![0-9])|엪2(?![0-9])|第\s*[二2]\s*[章關部]|2부(?![0-9])|(?<![a-z0-9])(?:h|히즈?|진좀?|진|d|디비전?|디)\s*2(?![0-9]))"),
    "ep3": re.compile(r"(?:ep\.?\s*3(?![0-9])|엪3(?![0-9])|第\s*[三3]\s*[章關部]|3부(?![0-9])|(?<![a-z0-9])(?:h|히즈?|진좀?|진|d|디비전?|디)\s*3(?![0-9]))"),
    "ep4": re.compile(r"(?:ep\.?\s*4(?![0-9])|엪4(?![0-9])|第\s*[四4]\s*[章關]|(?<![a-z0-9])(?:h|히즈?)\s*4(?![0-9]))"),
}
