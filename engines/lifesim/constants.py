"""Static configuration, demographic labels, innate traits, side actions, titles, and era windows for 1in8B."""

from __future__ import annotations

TOTAL_COUNTRIES_COUNT = 197

ERA_CONFIG = {
    "past_1950": {
        "label": "🕰️ 過去 (1950年代)",
        "year_range": (1950, 1965),
        "le_delta": -14.0,
        "gdp_mult": 0.28,
        "edu_delta": -4.5,
        "inf_pen": 0.045,
    },
    "past_1980": {
        "label": "📼 過去 (1980年代)",
        "year_range": (1980, 1995),
        "le_delta": -6.5,
        "gdp_mult": 0.55,
        "edu_delta": -2.0,
        "inf_pen": 0.018,
    },
    "present": {
        "label": "🌍 現代 (2026)",
        "year_range": (2024, 2026),
        "le_delta": 0.0,
        "gdp_mult": 1.0,
        "edu_delta": 0.0,
        "inf_pen": 0.0,
    },
    "future_2050": {
        "label": "🚀 未來 (2050預測)",
        "year_range": (2048, 2055),
        "le_delta": 3.5,
        "gdp_mult": 1.35,
        "edu_delta": 1.5,
        "inf_pen": -0.008,
    },
}

DRAW_METHOD_LABELS = {
    "births": "👶 依全球新生兒出生比例",
    "population": "👥 依全球現存人口比例",
}

FOCUS_LABELS = {
    "balance": "⚖️ 均衡發展",
    "study": "📚 專注學業進修",
    "career": "💼 全力衝刺事業",
    "health": "❤️ 重視健康養生",
    "social": "🤝 經營感情人脈",
}

STORY_MODE_LABELS = {
    "fast": "⚡ 經典深度版（預設）",
    "hybrid": "🎭 精華劇情版（關鍵轉折加演）",
    "llm": "🧠 全程故事版（自由輸入行動）",
}

URBAN_RURAL_LABELS = {
    "urban": "🏙️ 都市",
    "rural": "🌾 鄉村",
}

SIDE_ACTIONS = {
    "study": "📚 進修考證／自學充電",
    "health": "🏥 健檢調養／運動健身",
    "social": "💘 拓展人脈／經營感情",
    "career": "💼 跳槽進階／副業投資",
    "migrate": "✈️ 申請跨國移居（解鎖第2國）",
}

INNATE_TRAITS: dict[str, dict] = {
    "math_prodigy": {
        "id": "math_prodigy",
        "name": "數理奇才",
        "emoji": "🧮",
        "desc": "對數字與邏輯天生敏銳，學業、工程與理財檢定更具優勢。",
        "init_deltas": {"knowledge": 8, "social": -2},
        "bonus_stats": {"knowledge": 10},
        "conflicts": (),
    },
    "polyglot": {
        "id": "polyglot",
        "name": "語言天賦",
        "emoji": "🗣️",
        "desc": "語感極佳，跨文化溝通、外語進修與跨國簽證更具優勢。",
        "init_deltas": {"knowledge": 5, "social": 5},
        "bonus_stats": {"knowledge": 5, "social": 6},
        "conflicts": (),
    },
    "frail_body": {
        "id": "frail_body",
        "name": "體弱過敏",
        "emoji": "🤧",
        "desc": "自幼體質偏弱、易受病痛侵擾，但也養成了細膩思考與避險習慣。",
        "init_deltas": {"health": -10, "knowledge": 4},
        "bonus_stats": {"health": -6, "knowledge": 4},
        "conflicts": ("iron_constitution", "chronic_allergy"),
    },
    "iron_constitution": {
        "id": "iron_constitution",
        "name": "鋼鐵體魄",
        "emoji": "💪",
        "desc": "體能恢復力驚人，耐操抗壓，運動與高強度工作表現突飛猛進。",
        "init_deltas": {"health": 11},
        "bonus_stats": {"health": 10},
        "conflicts": ("frail_body", "chronic_allergy"),
    },
    "optimist": {
        "id": "optimist",
        "name": "樂觀豁達",
        "emoji": "☀️",
        "desc": "心態穩定陽光，逆境中也能快速自我修復並感染身邊的人。",
        "init_deltas": {"happiness": 10, "social": 2},
        "bonus_stats": {"happiness": 8},
        "conflicts": (),
    },
    "street_smart": {
        "id": "street_smart",
        "name": "街頭機靈",
        "emoji": "🦊",
        "desc": "善於察言觀色與市井議價，實戰生存與機會嗅覺格外敏銳。",
        "init_deltas": {"social": 6, "wealth": 5, "knowledge": -2},
        "bonus_stats": {"wealth": 6, "social": 6},
        "conflicts": (),
    },
    "scholarly_family": {
        "id": "scholarly_family",
        "name": "書香門第",
        "emoji": "📜",
        "desc": "成長環境充滿書籍與討論氛圍，考學與研究起步領先一步。",
        "init_deltas": {"knowledge": 7, "happiness": 2},
        "bonus_stats": {"knowledge": 8},
        "conflicts": (),
    },
    "charmer": {
        "id": "charmer",
        "name": "交際達人",
        "emoji": "✨",
        "desc": "談吐自然有魅力，聚會與職場人脈總是無往不利。",
        "init_deltas": {"social": 10, "happiness": 4},
        "bonus_stats": {"social": 10},
        "conflicts": ("tech_nerd",),
    },
    "artistic_soul": {
        "id": "artistic_soul",
        "name": "藝術靈魂",
        "emoji": "🎨",
        "desc": "美感與共情力極強，容易從生活細節找到幸福，但理財偏隨性。",
        "init_deltas": {"happiness": 7, "social": 3, "wealth": -4},
        "bonus_stats": {"happiness": 8},
        "conflicts": (),
    },
    "risk_taker": {
        "id": "risk_taker",
        "name": "冒險家",
        "emoji": "🧭",
        "desc": "敢於離開舒適圈，面對創業、外派與移民機會時膽識過人。",
        "init_deltas": {"happiness": 4, "wealth": 3, "health": -2},
        "bonus_stats": {"wealth": 6},
        "conflicts": ("frugal",),
    },
    "frugal": {
        "id": "frugal",
        "name": "節儉持家",
        "emoji": "🪙",
        "desc": "對金流極為自律，習慣積穀防饑，面對經濟寒冬最有底氣。",
        "init_deltas": {"wealth": 8, "happiness": -3},
        "bonus_stats": {"wealth": 9},
        "conflicts": ("risk_taker",),
    },
    "precocious": {
        "id": "precocious",
        "name": "早慧獨立",
        "emoji": "🌱",
        "desc": "比同齡人更早理解現實分工與長遠規劃，自學能力強。",
        "init_deltas": {"knowledge": 6, "wealth": 3, "happiness": -2},
        "bonus_stats": {"knowledge": 6, "wealth": 4},
        "conflicts": (),
    },
    "chronic_allergy": {
        "id": "chronic_allergy",
        "name": "慢性過敏",
        "emoji": "🌿",
        "desc": "季節交替容易不適，迫使你養成仔細閱讀醫囑與規劃作息的習慣。",
        "init_deltas": {"health": -7, "knowledge": 5, "happiness": -1},
        "bonus_stats": {"health": -4, "knowledge": 5},
        "conflicts": ("frail_body", "iron_constitution"),
    },
    "tech_nerd": {
        "id": "tech_nerd",
        "name": "技術宅",
        "emoji": "💻",
        "desc": "面對機械、工具或程式碼專注力驚人，但繁瑣社交消耗很快。",
        "init_deltas": {"knowledge": 9, "wealth": 4, "social": -7},
        "bonus_stats": {"knowledge": 8, "wealth": 6},
        "conflicts": ("charmer",),
    },
    "empathetic": {
        "id": "empathetic",
        "name": "同理心強",
        "emoji": "🫂",
        "desc": "敏銳捕捉他人情緒，在親密關係、團隊協作與助人領域深受信賴。",
        "init_deltas": {"social": 8, "happiness": 4, "wealth": -2},
        "bonus_stats": {"social": 9, "happiness": 5},
        "conflicts": (),
    },
}

ACHIEVEMENT_TITLES: tuple[dict, ...] = (
    {"id": "comeback_king", "name": "寒門逆襲王", "emoji": "🔥", "desc": "低出生幸運度下翻轉人生"},
    {"id": "centenarian", "name": "百歲人瑞", "emoji": "💯", "desc": "高齡長壽跨越近一世紀"},
    {"id": "global_nomad", "name": "跨國旅人", "emoji": "✈️", "desc": "成功跨國移居展開第二人生"},
    {"id": "tycoon_legend", "name": "億萬傳奇", "emoji": "💎", "desc": "財富累積達到頂峰"},
    {"id": "grand_scholar", "name": "學術泰斗", "emoji": "🎓", "desc": "知識與研究造詣登峰造極"},
    {"id": "warm_family", "name": "溫馨世家", "emoji": "🏡", "desc": "成家育兒且家庭幸福美滿"},
    {"id": "unyielding_fighter", "name": "不屈鬥士", "emoji": "🛡️", "desc": "克服先天困境活出長久人生"},
    {"id": "slash_master", "name": "斜槓達人", "emoji": "🛠️", "desc": "知識、財富與人脈全面開花"},
    {"id": "simple_bliss", "name": "平淡是福", "emoji": "🍀", "desc": "心境平和、五維均衡安穩度日"},
    {"id": "era_witness", "name": "時代見證者", "emoji": "🕰️", "desc": "親歷重大歷史時代浪潮"},
)

HISTORICAL_ERA_EVENTS: list[dict] = [
    {
        "id": "hist_1950_postwar",
        "year_range": (1950, 1969),
        "era_label": "戰後重建與冷戰",
        "stage": "時代浪潮・1950-60s",
        "min_age": 12,
        "max_age": 68,
        "title": "{year}年・戰後重建與冷戰陰影",
        "story": (
            "這個年代的{country}正經歷戰後重建、冷戰對峙與基礎工業擴張。"
            "街頭收音機播報著國際局勢，工廠與夜校同時亮著燈。"
        ),
        "choices": [
            {
                "label": "趁基建與工廠擴張搶進學徒／技術工",
                "check": {
                    "stat": "health",
                    "difficulty": 45,
                    "label_hint": "看健康",
                    "trait_bonus_ids": ["iron_constitution", "frugal"],
                    "crit_success": {
                        "text": "你憑過人體力與精準手藝成為廠區骨幹，薪資與口碑雙雙起飛！",
                        "deltas": {"wealth": 14, "knowledge": 4, "health": -3},
                        "new_occ": "資深技術領班",
                    },
                    "success": {
                        "text": "你在重建潮中穩穩站住腳跟，為家庭存下一筆安身資本。",
                        "deltas": {"wealth": 9, "health": -3},
                        "new_occ": "產業技術員",
                    },
                    "fail": {
                        "text": "高強度輪班與簡陋防護讓身體吃不消，只能先調養休息。",
                        "deltas": {"wealth": 3, "health": -8, "happiness": -3},
                    },
                },
            },
            {
                "label": "白天工作、晚上擠進燈火通明的夜校讀書",
                "deltas": {"knowledge": 11, "health": -4, "happiness": 2},
                "new_edu": "專科／夜校進修",
            },
            {
                "label": "守住鄰里互助網，種菜換物穩穩過日子",
                "deltas": {"social": 9, "happiness": 7, "health": 4},
            },
        ],
    },
    {
        "id": "hist_1970_oil_boom",
        "year_range": (1970, 1985),
        "era_label": "工業起飛與石油震盪",
        "stage": "時代浪潮・1970-85",
        "min_age": 14,
        "max_age": 72,
        "title": "{year}年・工業起飛與石油危機震盪",
        "story": (
            "油價飆漲與通膨震盪席捲全球，但製造業出口與城市化也正在{country}迅速翻轉舊秩序。"
        ),
        "choices": [
            {
                "label": "咬牙加入外銷工廠或貿易行，搶抓出口紅利",
                "check": {
                    "stat": "wealth",
                    "difficulty": 48,
                    "label_hint": "看財富",
                    "trait_bonus_ids": ["street_smart", "risk_taker"],
                    "crit_success": {
                        "text": "你準確嗅到外銷訂單缺口，成為小有名氣的經銷合夥人！",
                        "deltas": {"wealth": 16, "social": 6, "health": -4},
                        "new_occ": "貿易合夥人",
                    },
                    "success": {
                        "text": "你挺過通膨波動，收入跑贏物價上漲。",
                        "deltas": {"wealth": 10, "health": -3},
                    },
                    "fail": {
                        "text": "原料漲價壓垮了利潤，忙了一圈幾乎白忙一場。",
                        "deltas": {"wealth": -5, "happiness": -5},
                    },
                },
            },
            {
                "label": "精簡開銷、把餘錢換成實體資產或黃金保值",
                "deltas": {"wealth": 7, "knowledge": 4, "happiness": -1},
            },
            {
                "label": "參與工會或社區互助社，減輕生活成本壓力",
                "deltas": {"social": 10, "happiness": 6, "wealth": 2},
            },
        ],
    },
    {
        "id": "hist_1986_dotcom",
        "year_range": (1986, 2001),
        "era_label": "網路泡沫與金融風暴",
        "stage": "時代浪潮・1986-2001",
        "min_age": 14,
        "max_age": 74,
        "title": "{year}年・全球化浪潮與網路泡沫",
        "story": (
            "個人電腦與撥接上網開始進入生活，但區域金融風暴與網際網路泡沫破滅也讓無數資產在一夕間重新洗牌。"
        ),
        "choices": [
            {
                "label": "投入資訊科技與早期網路應用學習",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 52,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["tech_nerd", "math_prodigy", "precocious"],
                    "crit_success": {
                        "text": "你避開泡沫垃圾股並掌握真技術，成為搶手的系統工程人才！",
                        "deltas": {"knowledge": 12, "wealth": 14, "social": 3},
                        "new_occ": "資訊系統工程師",
                    },
                    "success": {
                        "text": "新技能替你打開了辦公室與跨國企業的大門。",
                        "deltas": {"knowledge": 9, "wealth": 7},
                    },
                    "fail": {
                        "text": "泡沫破滅後職缺急凍，只能先從基礎維護做起。",
                        "deltas": {"knowledge": 5, "wealth": -5, "happiness": -4},
                    },
                },
            },
            {
                "label": "面對金融震盪保持現金部位，不盲目槓桿",
                "deltas": {"wealth": 6, "happiness": 5, "knowledge": 4},
            },
            {
                "label": "乘國際航線普及之便，聯繫海外親友探尋新機會",
                "deltas": {"social": 8, "knowledge": 5, "happiness": 4},
            },
        ],
    },
    {
        "id": "hist_2002_smartphone",
        "year_range": (2002, 2015),
        "era_label": "智慧手機革命與08海嘯",
        "stage": "時代浪潮・2002-15",
        "min_age": 14,
        "max_age": 76,
        "title": "{year}年・金融海嘯與智慧型手機革命",
        "story": (
            "2008 年次貸海嘯曾讓全球失業率攀升，緊接著智慧型手機與行動網路改寫了每個人的日常與商業模式。"
        ),
        "choices": [
            {
                "label": "順應行動網路浪潮，經營電商或數位內容",
                "check": {
                    "stat": "social",
                    "difficulty": 52,
                    "label_hint": "看人際",
                    "trait_bonus_ids": ["charmer", "artistic_soul", "street_smart"],
                    "crit_success": {
                        "text": "你的內容與口碑迅速擴散，建立起穩定的數位副業與品牌！",
                        "deltas": {"wealth": 13, "social": 11, "happiness": 6},
                        "new_occ": "數位品牌創業者",
                    },
                    "success": {
                        "text": "成功搭上行動時代第一波列車，收入比傳統同儕更靈活。",
                        "deltas": {"wealth": 8, "social": 6, "knowledge": 4},
                    },
                    "fail": {
                        "text": "演算法與海嘯後的緊縮消費讓你投入的成本難以回收。",
                        "deltas": {"wealth": -6, "happiness": -4, "knowledge": 4},
                    },
                },
            },
            {
                "label": "低谷期考取專業執照或穩健公職／大廠職務",
                "deltas": {"knowledge": 9, "wealth": 6, "happiness": 2},
            },
            {
                "label": "放下手機多陪身邊的人，把時間留給真實生活",
                "deltas": {"happiness": 10, "health": 6, "social": 6},
            },
        ],
    },
    {
        "id": "hist_2016_ai_pandemic",
        "year_range": (2016, 2029),
        "era_label": "全球疫情與生成式AI浪潮",
        "stage": "時代浪潮・2016-29",
        "min_age": 13,
        "max_age": 82,
        "title": "{year}年・全球疫情與生成式 AI 浪潮",
        "story": (
            "封城與遠距辦公重塑了工作型態，緊隨其後的生成式 AI 更讓各行各業面臨技能洗牌。"
        ),
        "choices": [
            {
                "label": "積極將 AI 工具融入工作與自學流程",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 50,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["tech_nerd", "math_prodigy", "polyglot"],
                    "crit_success": {
                        "text": "你的生產力翻倍，在裁員潮中反被提拔為跨領域核心顧問！",
                        "deltas": {"knowledge": 12, "wealth": 13, "happiness": 5},
                        "new_occ": "AI 流程整合顧問",
                    },
                    "success": {
                        "text": "順利適應遠距與數位協作新常態，站穩工作位置。",
                        "deltas": {"knowledge": 8, "wealth": 7},
                    },
                    "fail": {
                        "text": "過度依賴未驗證的資訊反而出包，得花雙倍時間善後。",
                        "deltas": {"wealth": -4, "happiness": -5, "knowledge": 3},
                    },
                },
            },
            {
                "label": "重整作息與居家運動，守住身心免疫力",
                "deltas": {"health": 11, "happiness": 7, "wealth": -1},
            },
            {
                "label": "投入社區防疫或線上社群互助，連結孤單的鄰居",
                "deltas": {"social": 11, "happiness": 8, "health": -2},
            },
        ],
    },
    {
        "id": "hist_2030_climate_auto",
        "year_range": (2030, 2065),
        "era_label": "氣候遷徙與自動化時代",
        "stage": "時代浪潮・2030+",
        "min_age": 14,
        "max_age": 90,
        "title": "{year}年・極端氣候與全面自動化浪潮",
        "story": (
            "熱浪與氣候轉型改變了{country}的城市樣貌，機器人與自動駕駛接管了大量例行勞動，"
            "唯有創意、照護與高階決策依然搶手。"
        ),
        "choices": [
            {
                "label": "投身綠能基建或人機協作維護產業",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 54,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["tech_nerd", "math_prodigy", "risk_taker"],
                    "crit_success": {
                        "text": "你主導的微電網／自動化專案大獲成功，成為未來城市不可或缺的專家！",
                        "deltas": {"wealth": 14, "knowledge": 11, "happiness": 5},
                        "new_occ": "永續系統架構師",
                    },
                    "success": {
                        "text": "你順利從傳統職務轉軌至綠能與自動化產業。",
                        "deltas": {"wealth": 8, "knowledge": 7},
                    },
                    "fail": {
                        "text": "證照門檻與高強度測試讓你折戟，暫時領取轉型補助度日。",
                        "deltas": {"wealth": -4, "happiness": -4, "knowledge": 4},
                    },
                },
            },
            {
                "label": "專注機器無法取代的心理陪伴、照護或藝術創作",
                "deltas": {"social": 10, "happiness": 10, "wealth": 4},
            },
            {
                "label": "搬往氣候較宜居的高地或海濱社區建立韌性生活",
                "deltas": {"health": 10, "happiness": 7, "wealth": -4},
            },
        ],
    },
]

REGION_COLORS = {
    "東亞": (235, 95, 130),
    "東南亞": (46, 196, 182),
    "南亞": (255, 159, 28),
    "西亞": (212, 163, 115),
    "中亞": (131, 197, 190),
    "撒哈拉以南非洲": (231, 111, 81),
    "北非": (233, 196, 106),
    "西歐": (88, 129, 242),
    "北歐": (96, 165, 250),
    "南歐": (244, 162, 97),
    "東歐": (155, 93, 229),
    "北美": (67, 170, 139),
    "拉美": (249, 199, 79),
    "大洋洲": (58, 134, 255),
}

REGIONAL_NAMES = {
    "TW": (["子軒", "宇辰", "冠宇", "承翰", "柏翰"], ["品妍", "詠晴", "羽彤", "子晴", "思穎"], ["陳", "林", "黃", "張", "李"]),
    "JP": (["蓮", "陽翔", "湊", "悠真", "蒼"], ["陽葵", "凜", "詩", "芽依", "結衣"], ["佐藤", "鈴木", "高橋", "田中", "渡邊"]),
    "KR": (["瑞俊", "道允", "夏俊", "時宇", "恩宇"], ["瑞妍", "夏允", "智宇", "書雅", "荷允"], ["金", "李", "朴", "崔", "鄭"]),
    "CN": (["浩宇", "子墨", "宇航", "梓軒", "澤宇"], ["欣怡", "梓涵", "詩涵", "語桐", "若曦"], ["王", "李", "張", "劉", "陳"]),
    "IN": (["Aarav", "Vihaan", "Aditya", "Arjun", "Reyansh"], ["Aadhya", "Ananya", "Diya", "Saanvi", "Meera"], ["Sharma", "Patel", "Kumar", "Singh", "Verma"]),
    "東亞": (["浩然", "明軒", "俊傑"], ["雅婷", "佳穎", "心怡"], ["陳", "林", "王", "李"]),
    "東南亞": (["Aditya", "Rizky", "Minh", "Somchai", "Mateo"], ["Siti", "Linh", "Ploy", "Maria", "Putri"], ["Nguyen", "Santos", "Tan", "Wijoyo"]),
    "南亞": (["Aryan", "Bilal", "Rohan", "Kamal"], ["Fatima", "Priya", "Nadia", "Sana"], ["Khan", "Ahmed", "Das", "Rai"]),
    "西亞": (["Omar", "Ali", "Yusuf", "Tariq", "Daniel"], ["Layla", "Noor", "Zainab", "Maya", "Noa"], ["Al-Mansoor", "Hassan", "Kaya", "Levi"]),
    "中亞": (["Timur", "Azamat", "Ruslan", "Nurlan"], ["Amina", "Madina", "Aigerim", "Dilnoza"], ["Alimov", "Nazarov", "Akhmetov"]),
    "撒哈拉以南非洲": (["Chidi", "Kwame", "Samuel", "Tariro", "Kofi", "Abebe"], ["Amara", "Zainab", "Grace", "Amina", "Nia", "Wanjiku"], ["Okafor", "Mensah", "Diallo", "Ndlovu", "Kamau"]),
    "北非": (["Youssef", "Karim", "Mehdi", "Amine"], ["Salma", "Ines", "Yasmine", "Nour"], ["Benali", "Mansouri", "El-Sayed"]),
    "西歐": (["Lucas", "Lukas", "Liam", "Hugo", "Noah"], ["Emma", "Léa", "Mia", "Hannah", "Sophie"], ["Müller", "Bernard", "De Vries", "Weber"]),
    "北歐": (["Oliver", "Oscar", "Elias", "William", "Soren"], ["Freja", "Astrid", "Olivia", "Amelia", "Maja"], ["Jensen", "Lindqvist", "Hansen", "Smith"]),
    "南歐": (["Mateo", "Leonardo", "Marco", "Tiago", "Nikos"], ["Sofia", "Lucia", "Giulia", "Maria", "Elena"], ["Rossi", "García", "Silva", "Papadopoulos"]),
    "東歐": (["Ivan", "Jakub", "Mateusz", "Dmitry", "Andrei"], ["Anastasia", "Zofia", "Elena", "Olena", "Katarina"], ["Nowak", "Kovalenko", "Petrov", "Popescu"]),
    "北美": (["Liam", "Noah", "Mateo", "Ethan", "Santiago"], ["Olivia", "Emma", "Sofia", "Mia", "Camila"], ["Johnson", "Hernández", "Tremblay", "Garcia"]),
    "拉美": (["Gabriel", "Mateo", "Lucas", "Sebastián", "Thiago"], ["Valentina", "Isabella", "Sofía", "Camila", "Mariana"], ["Silva", "Rodríguez", "González", "López"]),
    "大洋洲": (["Jack", "Oliver", "Noah", "Tama"], ["Charlotte", "Amelia", "Isla", "Aroha"], ["Wilson", "Taylor", "Brown", "Tui"]),
}
