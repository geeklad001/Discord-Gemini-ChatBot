"""Rich 75+ conditional event pool, historical era windows, and probabilistic skill checks for 1in8B."""

from __future__ import annotations

import copy
import random
from typing import Optional

from engines.lifesim.constants import HISTORICAL_ERA_EVENTS, INNATE_TRAITS
from engines.lifesim.storage import load_world_demographics

_STAT_KEYS = ("health", "happiness", "knowledge", "wealth", "social")


def _extract_trait_ids(run: dict) -> set[str]:
    result: set[str] = set()
    for t in (run.get("traits") or []):
        if isinstance(t, dict) and t.get("id"):
            result.add(str(t["id"]))
        elif isinstance(t, str) and t:
            result.add(t)
    return result


def compute_choice_success_pct(run: dict, check: dict) -> int:
    """依角色五維數值、先天特質、城鄉與國別階層計算檢定成功率（夾限 12..95%）。"""
    stat_key = str(check.get("stat") or "knowledge")
    stat_val = int(run.get(stat_key, 50))
    difficulty = int(check.get("difficulty", 50))

    pct = 55.0 + (stat_val - difficulty) * 0.9

    trait_ids = _extract_trait_ids(run)
    for tid in (check.get("trait_bonus_ids") or ()):
        if tid in trait_ids:
            pct += 10.0

    # Minor passive trait penalty if player has frail/chronic trait on health check
    if stat_key == "health":
        if "frail_body" in trait_ids:
            pct -= 6.0
        elif "chronic_allergy" in trait_ids:
            pct -= 4.0
    elif stat_key == "social" and "tech_nerd" in trait_ids:
        pct -= 5.0

    urban_rural = run.get("urban_rural", "urban")
    if check.get("urban_bonus") and urban_rural == "urban":
        pct += 5.0
    if check.get("rural_bonus") and urban_rural == "rural":
        pct += 5.0

    if check.get("high_tier_bonus"):
        tier = str(run.get("_country_tier") or "")
        if tier in ("high", "upper_mid"):
            pct += 4.0

    return max(12, min(95, int(round(pct))))


def _render_template(text: str, run: dict, country: dict, age: int) -> str:
    city = run.get("current_city") or run.get("city") or country.get("name_zh", "當地")
    cname = run.get("current_country_name") or run.get("country_name") or country.get("name_zh", "當地")
    year = int(run.get("birth_year", 2000)) + int(age)
    replacements = {
        "{age}": str(age),
        "{year}": str(year),
        "{city}": str(city),
        "{country}": str(cname),
        "{occupation}": str(run.get("occupation") or "居民"),
        "{char_name}": str(run.get("char_name") or "主角"),
        "{family_class}": str(run.get("family_class") or "普通"),
    }
    out = text
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


def _infer_country_tier(country: dict, run: dict) -> str:
    tier = str(country.get("tier") or "").strip()
    if tier in ("low", "lower_mid", "upper_mid", "high"):
        return tier
    gdp = float(country.get("gdp_pc") or 0)
    if gdp >= 25000 or run.get("wealth", 50) >= 68:
        return "high"
    if gdp >= 7000:
        return "upper_mid"
    if gdp >= 2000:
        return "lower_mid"
    return "low"


def _event_matches(ev: dict, run: dict, country: dict, age: int, *, ignore_seen: bool = False) -> bool:
    if not ignore_seen and ev["id"] in set(run.get("seen_events") or []):
        return False
    if age < int(ev.get("min_age", 0)) or age > int(ev.get("max_age", 120)):
        return False

    tiers = ev.get("tiers")
    if tiers:
        tier = _infer_country_tier(country, run)
        if tier not in tiers:
            return False

    ur = ev.get("urban_rural")
    if ur and run.get("urban_rural", "urban") != ur:
        return False

    rel = run.get("relationships") or {}
    is_married = bool(rel.get("married", False))
    if ev.get("requires_married") and not is_married:
        return False
    if ev.get("requires_unmarried") and is_married:
        return False

    min_stats = ev.get("min_stats") or {}
    for sk, sv in min_stats.items():
        if int(run.get(sk, 0)) < int(sv):
            return False

    max_stats = ev.get("max_stats") or {}
    for sk, sv in max_stats.items():
        if int(run.get(sk, 0)) > int(sv):
            return False

    trait_any = ev.get("trait_any")
    if trait_any:
        user_traits = _extract_trait_ids(run)
        if not (user_traits & set(trait_any)):
            return False

    yr_range = ev.get("year_range")
    if yr_range:
        cal_year = int(run.get("birth_year", 2000)) + int(age)
        if not (yr_range[0] <= cal_year <= yr_range[1]):
            return False

    return True


def _materialize_event(ev: dict, run: dict, country: dict, age: int) -> dict:
    run_with_tier = dict(run)
    run_with_tier["_country_tier"] = _infer_country_tier(country, run)

    choices_out: list[dict] = []
    for ch in (ev.get("choices") or [])[:3]:
        c_copy = copy.deepcopy(ch)
        c_copy["label"] = _render_template(str(c_copy.get("label") or "繼續前進"), run, country, age)
        if "check" in c_copy and isinstance(c_copy["check"], dict):
            c_copy["success_pct"] = compute_choice_success_pct(run_with_tier, c_copy["check"])
        choices_out.append(c_copy)

    return {
        "id": ev["id"],
        "stage": ev.get("stage", "人生轉折"),
        "title": _render_template(ev.get("title", "{age}歲・命運的轉折"), run, country, age),
        "story": _render_template(ev.get("story", ""), run, country, age),
        "choices": choices_out,
    }


# ============================================================================
# 72+ 寫實分齡事件池（橫跨 8 大生命階段＋國別/城鄉/特質/婚姻條件＋機率檢定）
# ============================================================================
EVENT_POOL: list[dict] = [
    # --- Stage 1: <= 6 (幼年啟蒙, 8 events) ---
    {
        "id": "s1_urban_early_play",
        "stage": "幼年啟蒙",
        "min_age": 0,
        "max_age": 6,
        "urban_rural": "urban",
        "title": "{age}歲・{city}公寓與社區公園",
        "story": "你住在{country}{city}的高樓社區裡，窗外車水馬龍。父母正在煩惱如何安排你學齡前的日常節奏。",
        "choices": [
            {"label": "在家玩拼圖與積木、聽繪本故事", "deltas": {"knowledge": 8, "happiness": 3}},
            {"label": "每天去社區公園溜滑梯認識小朋友", "deltas": {"health": 6, "social": 7, "happiness": 5}},
            {"label": "安靜待在角落觀察大人做家事", "deltas": {"knowledge": 4, "wealth": 3, "social": -1}},
        ],
    },
    {
        "id": "s1_rural_meadow",
        "stage": "幼年啟蒙",
        "min_age": 0,
        "max_age": 6,
        "urban_rural": "rural",
        "title": "{age}歲・{city}鄉間的泥土與微風",
        "story": "在{country}{city}的鄉間聚落長大，門外就是田埂、老樹與鄰居家的院子。大自然的節奏就是你最初的教室。",
        "choices": [
            {"label": "赤腳在田邊奔跑追蝴蝶、鍛鍊體力", "deltas": {"health": 9, "happiness": 7}},
            {"label": "幫祖父母餵雞挑菜、聽老一輩講古", "deltas": {"social": 7, "wealth": 3, "knowledge": 3}},
            {"label": "拿樹枝在地上畫畫算數", "deltas": {"knowledge": 7, "happiness": 4}},
        ],
    },
    {
        "id": "s1_tight_budget",
        "stage": "幼年啟蒙",
        "min_age": 0,
        "max_age": 6,
        "tiers": ["low", "lower_mid"],
        "title": "{age}歲・油燈下算帳的夜晚",
        "story": "這一年物價有些吃緊，{country}的生活並不容易，父母省吃儉用把最好的食物留給你。",
        "choices": [
            {"label": "乖乖把飯吃完不挑食、不哭鬧", "deltas": {"health": 5, "happiness": 4, "social": 3}},
            {"label": "撿舊報紙與廢紙箱做成自己的玩具", "deltas": {"knowledge": 6, "happiness": 5, "wealth": 2}},
            {"label": "跟著鄰居大孩子出門幫忙跑腿", "deltas": {"social": 7, "wealth": 4, "health": -2}},
        ],
    },
    {
        "id": "s1_clinic_fever",
        "stage": "幼年啟蒙",
        "min_age": 0,
        "max_age": 6,
        "title": "{age}歲・一場來勢洶洶的高燒",
        "story": "換季時你突然發起高燒咳嗽，在{city}的診所排隊候診時，你緊緊抓著大人的衣角。",
        "choices": [
            {
                "label": "勇敢喝完苦藥並配合醫生檢查",
                "check": {
                    "stat": "health",
                    "difficulty": 40,
                    "label_hint": "看健康",
                    "trait_bonus_ids": ["iron_constitution", "optimist"],
                    "crit_success": {"text": "你的抵抗力出乎意料地強，退燒後還長高了一截！", "deltas": {"health": 8, "happiness": 4}},
                    "success": {"text": "按時吃藥睡了兩天，順利恢復活潑元氣。", "deltas": {"health": 4, "happiness": 1}},
                    "fail": {"text": "病拖了好一陣子，體重掉了一點才慢慢好轉。", "deltas": {"health": -6, "happiness": -4}},
                },
            },
            {"label": "家人花積蓄請專科醫師細心調養", "deltas": {"health": 7, "wealth": -5, "happiness": 2}},
            {"label": "多喝溫水休息，趁病中翻看圖畫書", "deltas": {"knowledge": 5, "health": 1}},
        ],
    },
    {
        "id": "s1_old_bookshelf",
        "stage": "幼年啟蒙",
        "min_age": 0,
        "max_age": 6,
        "title": "{age}歲・塵封書櫃裡的圖鑑",
        "story": "你在家裡或親戚家翻出一本泛黃的百科圖鑑，裡頭畫著世界各地的星空、船隻與奇怪文字。",
        "choices": [
            {"label": "每天纏著大人問書裡的每一個為什麼", "deltas": {"knowledge": 10, "social": 3}},
            {"label": "照著書本塗鴉，立志以後要畫出自己的故事", "deltas": {"happiness": 8, "knowledge": 4}},
            {"label": "把書疊起來當城堡和玩伴扮家家酒", "deltas": {"social": 7, "happiness": 6}},
        ],
    },
    {
        "id": "s1_kindergarten_conflict",
        "stage": "幼年啟蒙",
        "min_age": 3,
        "max_age": 6,
        "title": "{age}歲・玩具搶奪大作戰",
        "story": "托兒所／巷口的小霸王搶走了你最喜歡的玩具，其他孩子都圍過來看你會怎麼做。",
        "choices": [
            {"label": "找老師或大人理性講道理把玩具要回來", "deltas": {"knowledge": 5, "social": 5}},
            {"label": "提出輪流玩的規則，反而交到一個新朋友", "deltas": {"social": 9, "happiness": 5}},
            {"label": "不甘示弱搶回來，雖然擦傷但守住底線", "deltas": {"health": 2, "happiness": 3, "social": -2}},
        ],
    },
    {
        "id": "s1_grandma_kitchen",
        "stage": "幼年啟蒙",
        "min_age": 1,
        "max_age": 6,
        "title": "{age}歲・廚房裡的家常香氣",
        "story": "傍晚時分，{city}的廚房飄著當地傳統料理的香氣。你搬張小板凳坐在旁邊幫忙剝菜葉。",
        "choices": [
            {"label": "認真學怎麼分辨食材與幫忙擺碗筷", "deltas": {"health": 4, "happiness": 6, "social": 4}},
            {"label": "偷吃一口剛出鍋的點心，幸福感爆棚", "deltas": {"happiness": 9, "health": 2}},
            {"label": "數著盤子與豆子練加法", "deltas": {"knowledge": 6, "wealth": 2}},
        ],
    },
    {
        "id": "s1_trait_spark",
        "stage": "幼年啟蒙",
        "min_age": 2,
        "max_age": 6,
        "trait_any": ["math_prodigy", "artistic_soul", "polyglot", "precocious"],
        "title": "{age}歲・藏不住的天賦微光",
        "story": "連大人都驚訝地發現，你比同齡孩子更快聽懂複雜的話語或記住圖案與規律。",
        "choices": [
            {"label": "順著興趣大量探索，每天樂此不疲", "deltas": {"knowledge": 9, "happiness": 6}},
            {"label": "在親友聚會上大方展示才藝", "deltas": {"social": 8, "happiness": 5}},
            {"label": "保留體力去戶外曬太陽，均衡長大", "deltas": {"health": 7, "happiness": 4}},
        ],
    },

    # --- Stage 2: 7..13 (小學成長, 9 events) ---
    {
        "id": "s2_science_contest",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "title": "{age}歲・校內數理與創意競賽",
        "story": "{city}的小學舉辦年度競賽，優勝者能代表學校去市區參賽。老師問你要不要報名。",
        "choices": [
            {
                "label": "閉關練習歷屆題目，挑戰第一名",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 46,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["math_prodigy", "scholarly_family", "precocious"],
                    "crit_success": {"text": "你以滿分拿下全校第一，還抱回一筆獎學金！", "deltas": {"knowledge": 12, "wealth": 6, "happiness": 7}},
                    "success": {"text": "順利得獎上台領獎狀，信心大增。", "deltas": {"knowledge": 9, "happiness": 5}},
                    "fail": {"text": "臨場太緊張算錯關鍵一步，雖然落選但學到經驗。", "deltas": {"knowledge": 4, "happiness": -4}},
                },
            },
            {"label": "拉上死黨組隊參加趣味創意組", "deltas": {"social": 9, "happiness": 7, "knowledge": 4}},
            {"label": "把時間省下來繼續練田徑／球類校隊", "deltas": {"health": 9, "social": 5}},
        ],
    },
    {
        "id": "s2_rural_harvest",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "urban_rural": "rural",
        "title": "{age}歲・農忙時節與單車上學路",
        "story": "家鄉{city}進入採收季節，許多同學放學後都得下田或照顧弟妹，而你還要兼顧功課。",
        "choices": [
            {"label": "放學後幫家裡分擔農活、學算收成帳", "deltas": {"wealth": 7, "health": 5, "knowledge": 2}},
            {"label": "借著微弱燈光把功課寫完再去睡", "deltas": {"knowledge": 10, "health": -3, "happiness": -2}},
            {"label": "和同村小孩騎單車去溪邊冒險", "deltas": {"happiness": 8, "social": 7, "health": 4}},
        ],
    },
    {
        "id": "s2_urban_cram",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "urban_rural": "urban",
        "title": "{age}歲・補習班與才藝課的黃昏",
        "story": "傍晚的{city}街頭擠滿揹著大書包的學生。同班同學幾乎都在補英文、數學或音樂。",
        "choices": [
            {"label": "加倍投入課後精進班，打好升學底子", "deltas": {"knowledge": 11, "wealth": -4, "happiness": -3}},
            {"label": "選擇自己最熱愛的一項才藝（畫畫／樂器／程式）", "deltas": {"happiness": 7, "knowledge": 6, "social": 3}},
            {"label": "婉拒補習，下課直接揪同學去球場流汗", "deltas": {"health": 8, "social": 9, "happiness": 6}},
        ],
    },
    {
        "id": "s2_stand_up_bully",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "title": "{age}歲・走廊上的勇氣測驗",
        "story": "班上比較弱小的同學總被高年級生索討零用錢。今天放學後，你剛好撞見他們在牆角。",
        "choices": [
            {
                "label": "挺身而出大聲喝止，並找值日老師處理",
                "check": {
                    "stat": "social",
                    "difficulty": 48,
                    "label_hint": "看人際",
                    "trait_bonus_ids": ["empathetic", "iron_constitution", "charmer"],
                    "crit_success": {"text": "你冷靜化解衝突還讓那位同學成為你的鐵哥們！", "deltas": {"social": 12, "happiness": 7}},
                    "success": {"text": "對方見有老師趕來便散去，大家對你刮目相看。", "deltas": {"social": 8, "happiness": 4}},
                    "fail": {"text": "雖然推擠時挨了一拳，但從此沒人敢再欺負你朋友。", "deltas": {"health": -5, "social": 6, "happiness": 2}},
                },
            },
            {"label": "先記下車牌與名字，聰明地透過導師與家長解決", "deltas": {"knowledge": 6, "social": 5}},
            {"label": "怕惹麻煩快步離開，事後心裡有點愧疚", "deltas": {"happiness": -4, "health": 2}},
        ],
    },
    {
        "id": "s2_library_treasure",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "title": "{age}歲・圖書館裡的平行宇宙",
        "story": "你在{city}市立圖書館（或學校書車）角落發現一排涵蓋天文、歷史與程式邏輯的書架。",
        "choices": [
            {"label": "每週固定借滿上限，建立自己的筆記本", "deltas": {"knowledge": 11, "happiness": 4}},
            {"label": "拉著好朋友一起看小說討論劇情", "deltas": {"social": 8, "happiness": 6, "knowledge": 4}},
            {"label": "只挑漫畫跟冒險故事看個痛快", "deltas": {"happiness": 9, "knowledge": 2}},
        ],
    },
    {
        "id": "s2_first_computer",
        "stage": "小學成長",
        "min_age": 8,
        "max_age": 13,
        "year_range": (1985, 2065),
        "title": "{age}歲・遇見第一台電腦／平板",
        "story": "家裡或學校終於出現了一台能運作的電腦裝置。螢幕亮起的那一刻，你彷彿看見整個世界。",
        "choices": [
            {"label": "拆解設定、研究小遊戲邏輯或寫簡單指令", "deltas": {"knowledge": 10, "wealth": 3, "health": -2}},
            {"label": "用來跟遠方同學連線或聊天交友", "deltas": {"social": 8, "happiness": 6}},
            {"label": "怕眼睛近視，設定鬧鐘只玩半小時就去運動", "deltas": {"health": 6, "knowledge": 4, "happiness": 3}},
        ],
    },
    {
        "id": "s2_family_shock",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "title": "{age}歲・家計的一場小風波",
        "story": "家裡遭遇了一筆意料之外的開銷或工作變動，大人們在餐桌上說話的聲音變小了。",
        "choices": [
            {"label": "主動減少零用錢花費、把文具用到最後一刻", "deltas": {"wealth": 6, "knowledge": 3, "happiness": -2}},
            {"label": "寫張小卡片逗父母開心，全家一起渡過難關", "deltas": {"happiness": 6, "social": 7}},
            {"label": "跟著親戚幫忙整理貨物換點補貼", "deltas": {"wealth": 8, "health": -2, "social": 3}},
        ],
    },
    {
        "id": "s2_sports_team",
        "stage": "小學成長",
        "min_age": 7,
        "max_age": 13,
        "title": "{age}歲・校隊選拔賽的終場哨聲",
        "story": "學校田徑／球類代表隊正在招新。每天早上七點要集合操練，非常耗費體力。",
        "choices": [
            {
                "label": "咬牙撐過體能測試，爭取正選席次",
                "check": {
                    "stat": "health",
                    "difficulty": 50,
                    "label_hint": "看健康",
                    "trait_bonus_ids": ["iron_constitution", "optimist"],
                    "crit_success": {"text": "你拿下比賽關鍵分數，全校為你歡呼！", "deltas": {"health": 10, "social": 9, "happiness": 7}},
                    "success": {"text": "順利入選代表隊，體魄與團隊精神大增。", "deltas": {"health": 8, "social": 6}},
                    "fail": {"text": "肌耐力還差臨門一腳成為候補，但身體結實了不少。", "deltas": {"health": 5, "happiness": -2}},
                },
            },
            {"label": "當球隊紀錄員兼戰術分析，動腦不硬拚", "deltas": {"knowledge": 7, "social": 6}},
            {"label": "專心顧好平時課業，週末再自己慢跑", "deltas": {"knowledge": 6, "health": 4}},
        ],
    },
    {
        "id": "s2_language_spark",
        "stage": "小學成長",
        "min_age": 8,
        "max_age": 13,
        "title": "{age}歲・來自遠方的明信片與外語歌",
        "story": "你在廣播或網路聽到一首陌生語言的歌，忽然好想知道歌詞裡的世界長什麼樣子。",
        "choices": [
            {"label": "開始自學單字與發音，夢想有天出國看看", "deltas": {"knowledge": 9, "happiness": 5}},
            {"label": "找同學一起哼唱編舞，變成班上話題中心", "deltas": {"social": 8, "happiness": 7}},
            {"label": "聽聽就好，還是腳踏實地顧好眼前的事", "deltas": {"wealth": 3, "happiness": 3}},
        ],
    },

    # --- Stage 3: 14..19 (青春抉擇, 10 events) ---
    {
        "id": "s3_entrance_exam",
        "stage": "升學與成年路口",
        "min_age": 14,
        "max_age": 19,
        "title": "{age}歲・決定未來軌道的大考前夕",
        "story": "在{country}{city}，升學考試或專業分流即將登場。這張成績單將影響你能否進入頂尖大學與獎學金名單。",
        "choices": [
            {
                "label": "全力衝刺升學考／申請全額獎學金",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 56,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["math_prodigy", "scholarly_family", "precocious"],
                    "high_tier_bonus": True,
                    "crit_success": {"text": "榜單公布！你名列前茅並拿下獎學金入學資格！", "deltas": {"knowledge": 14, "wealth": 8, "happiness": 7}, "new_occ": "大學獎學金生", "new_edu": "頂尖大學"},
                    "success": {"text": "順利錄取理想科系，踏上專業進修之路。", "deltas": {"knowledge": 11, "happiness": 3, "health": -3}, "new_occ": "大學生", "new_edu": "大學／高等專科"},
                    "fail": {"text": "成績差一點點與夢幻志願擦肩，只能選擇重考或就讀實用專科。", "deltas": {"knowledge": 6, "happiness": -6, "wealth": -2}, "new_occ": "專科學生"},
                },
            },
            {"label": "選擇產學合作／技職學徒制，邊學手藝邊領薪", "deltas": {"wealth": 9, "knowledge": 6, "health": -2}, "new_occ": "技術學徒"},
            {"label": "維持中等成績，把青春留給社團與死黨", "deltas": {"social": 10, "happiness": 8, "knowledge": 3}, "new_occ": "高中畢業生"},
        ],
    },
    {
        "id": "s3_first_love",
        "stage": "青春與自我探索",
        "min_age": 14,
        "max_age": 19,
        "requires_unmarried": True,
        "title": "{age}歲・黃昏操場邊的心跳聲",
        "story": "有一位總是在圖書館或社團遇見的對象，最近常和你聊到傍晚。青春特有的曖昧正在發酵。",
        "choices": [
            {
                "label": "鼓起勇氣真誠告白，牽起彼此的手",
                "check": {
                    "stat": "social",
                    "difficulty": 48,
                    "label_hint": "看人際",
                    "trait_bonus_ids": ["charmer", "empathetic", "optimist"],
                    "crit_success": {"text": "對方其實也喜歡你很久了！你們成為彼此前進的動力。", "deltas": {"happiness": 12, "social": 10}, "set_partner": True},
                    "success": {"text": "你們順利開始交往，一起分擔課業與成長煩惱。", "deltas": {"happiness": 9, "social": 8}, "set_partner": True},
                    "fail": {"text": "對方微笑著說現在想專注升學，你失落幾天後慢慢釋懷。", "deltas": {"happiness": -5, "knowledge": 4}},
                },
            },
            {"label": "維持純粹的知己與戰友關係，互相督促考上好學校", "deltas": {"knowledge": 7, "social": 7, "happiness": 4}},
            {"label": "把心思收回來全力拚自己的未來", "deltas": {"knowledge": 9, "wealth": 3, "happiness": -2}},
        ],
    },
    {
        "id": "s3_part_time_hustle",
        "stage": "青春歷練",
        "min_age": 14,
        "max_age": 19,
        "title": "{age}歲・第一份自己賺來的薪水",
        "story": "週末時街角的餐廳／市集攤位／家教中心正在徵工讀生。提前接觸社會能賺取零用錢，但會壓縮讀書睡眠。",
        "choices": [
            {"label": "接下打工並把每筆收入記帳儲蓄", "deltas": {"wealth": 10, "social": 5, "health": -3}},
            {"label": "當數理或語言小家教，邊教邊複習", "deltas": {"wealth": 7, "knowledge": 7, "social": 4}},
            {"label": "專心照顧健康與課業，打工等成年再說", "deltas": {"knowledge": 6, "health": 6}},
        ],
    },
    {
        "id": "s3_exchange_program",
        "stage": "青春視野",
        "min_age": 15,
        "max_age": 19,
        "title": "{age}歲・海外交換與國際營隊徵選",
        "story": "{country}有個青年國際交流名額，能在異地體驗幾個月的校園生活並結識世界各地的朋友。",
        "choices": [
            {
                "label": "提交英文／外語自傳參加面試選拔",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 58,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["polyglot", "charmer", "risk_taker"],
                    "crit_success": {"text": "流利應答驚豔評審！你獲全額贊助出訪，視野徹底打開。", "deltas": {"knowledge": 11, "social": 11, "happiness": 9}},
                    "success": {"text": "成功入選自費交換名單，雖然花了一筆錢但收穫滿滿。", "deltas": {"knowledge": 9, "social": 8, "wealth": -5}},
                    "fail": {"text": "競爭太激烈而落榜，但準備過程讓你的外語突飛猛進。", "deltas": {"knowledge": 6, "happiness": -3}},
                },
            },
            {"label": "擔任來訪外賓的在地導覽志工，零花費練外語", "deltas": {"social": 9, "knowledge": 6, "happiness": 5}},
            {"label": "留在原地打工存錢更實際", "deltas": {"wealth": 8, "happiness": 2}},
        ],
    },
    {
        "id": "s3_hackathon_craft",
        "stage": "青春潛能",
        "min_age": 14,
        "max_age": 19,
        "title": "{age}歲・青年技能／創客大賽",
        "story": "在{city}舉行的青年技能與程式／工藝比賽吸引了許多高手，聽說知名企業主管也會到場物色人才。",
        "choices": [
            {
                "label": "熬夜做出原創作品上台簡報",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 52,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["tech_nerd", "math_prodigy", "artistic_soul"],
                    "crit_success": {"text": "作品爆紅！直接拿到實習邀請與比賽獎金！", "deltas": {"knowledge": 10, "wealth": 10, "social": 6}},
                    "success": {"text": "獲得評審青睞入圍佳作，履歷添上亮眼一筆。", "deltas": {"knowledge": 8, "social": 5}},
                    "fail": {"text": "上台前作品突然當機失靈，熬夜卻換來遺憾。", "deltas": {"health": -4, "happiness": -4, "knowledge": 4}},
                },
            },
            {"label": "負責團隊溝通與企劃簡報，發揮合作戰力", "deltas": {"social": 9, "knowledge": 5, "happiness": 5}},
            {"label": "當觀眾觀摩學習就好，不給自己太大壓力", "deltas": {"knowledge": 4, "health": 4, "happiness": 3}},
        ],
    },
    {
        "id": "s3_family_shop_dilemma",
        "stage": "家庭與理想",
        "min_age": 15,
        "max_age": 19,
        "tiers": ["low", "lower_mid"],
        "title": "{age}歲・家裡需要多一雙手",
        "story": "父母的體力大不如前，家裡的生計或小攤子急需人手幫忙。你面臨提早出社會還是堅持求學的拉鋸。",
        "choices": [
            {"label": "先接手部分工時幫家裡撐過去，夜晚再自學", "deltas": {"wealth": 9, "social": 5, "health": -3, "knowledge": 3}, "new_occ": "家族幫手／學徒"},
            {"label": "跟家人深談爭取支持，靠貸款與獎學金讀完書", "deltas": {"knowledge": 11, "wealth": -4, "happiness": 2}, "new_occ": "清寒苦讀生"},
            {"label": "靠街頭靈活找批發小生意，幫家裡改良營收", "deltas": {"wealth": 11, "social": 6, "knowledge": 2}, "new_occ": "青年小販／創業者"},
        ],
    },
    {
        "id": "s3_peer_temptation",
        "stage": "青春試煉",
        "min_age": 14,
        "max_age": 19,
        "title": "{age}歲・深夜巷口的誘惑與界線",
        "story": "一群混得風生水起的朋友邀你翹課去參加地下派對或高風險賺快錢的門路，聲稱「不跟著混就是膽小鬼」。",
        "choices": [
            {"label": "果斷拒絕並換個生活圈，守住長遠底線", "deltas": {"knowledge": 6, "health": 5, "social": -2, "happiness": 2}},
            {"label": "用幽默圓滑的方式打圓場脫身，不得罪人", "deltas": {"social": 8, "knowledge": 4, "happiness": 3}},
            {"label": "忍不住跟去冒險一次，差點鬧出警局風波", "deltas": {"happiness": 3, "health": -6, "wealth": -5, "social": 3}},
        ],
    },
    {
        "id": "s3_art_exhibition",
        "stage": "藝術與夢想",
        "min_age": 14,
        "max_age": 19,
        "title": "{age}歲・不被看好的創作夢",
        "story": "你花了好幾個月完成攝影／繪畫／音樂創作，但身邊長輩總說「這不能當飯吃」。",
        "choices": [
            {"label": "把作品投到公開展覽或網路平台讓世界看見", "deltas": {"happiness": 10, "social": 7, "wealth": 2}},
            {"label": "把它當作一生熱愛的精神避風港，白天照常拚現實", "deltas": {"happiness": 8, "knowledge": 5, "wealth": 4}},
            {"label": "收起畫筆與樂器，轉身投入最賺錢的熱門領域", "deltas": {"wealth": 7, "happiness": -5, "knowledge": 4}},
        ],
    },
    {
        "id": "s3_urban_commute",
        "stage": "青春日常",
        "min_age": 15,
        "max_age": 19,
        "urban_rural": "urban",
        "title": "{age}歲・{city}早高峰的捷運與公車",
        "story": "為了讀書或實習，你每天在{city}擠在沙丁魚般的通勤車廂裡，看著手機與窗外飛逝的霓虹燈。",
        "choices": [
            {"label": "利用通勤碎片時間聽外語／有聲書充實自己", "deltas": {"knowledge": 9, "happiness": 2}},
            {"label": "提早一站下車步行，保住體能與好心情", "deltas": {"health": 8, "happiness": 5}},
            {"label": "在車上補眠卻常睡過頭，身心都有點疲憊", "deltas": {"health": -2, "happiness": -2, "knowledge": 3}},
        ],
    },
    {
        "id": "s3_leave_hometown",
        "stage": "離鄉背井",
        "min_age": 16,
        "max_age": 19,
        "urban_rural": "rural",
        "title": "{age}歲・買一張前往大城市的車票",
        "story": "看著家鄉{city}有限的升學與工作機會，你盯著車站時刻表，思考要不要背起行囊去大都市闖蕩。",
        "choices": [
            {"label": "打包行李赴大城市半工半讀闖一番", "deltas": {"knowledge": 7, "wealth": 6, "health": -3, "social": 4}, "new_occ": "異鄉打拚青年"},
            {"label": "留在熟悉的家鄉把農業／地方工藝現代化", "deltas": {"happiness": 7, "health": 6, "wealth": 5}, "new_occ": "地方青年創生者"},
            {"label": "先考取遠距線上課程或公費名額再出發", "deltas": {"knowledge": 9, "wealth": 2}},
        ],
    },

    # --- Stage 4: 20..29 (青年闖蕩, 11 events) ---
    {
        "id": "s4_first_career_crunch",
        "stage": "青年闖蕩",
        "min_age": 20,
        "max_age": 29,
        "title": "{age}歲・{city}職場的深夜燈火",
        "story": "身為{country}的「{occupation}」，你剛進業界不久就遇上一個極具挑戰的大專案，主管暗示撐過去就能快速加薪。",
        "choices": [
            {
                "label": "接下高壓任務全力交付，證明自己的實力",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 54,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["tech_nerd", "precocious", "math_prodigy"],
                    "crit_success": {"text": "專案完美收官！你連跳兩級獲得實質調薪與分紅！", "deltas": {"wealth": 15, "knowledge": 7, "health": -4}, "new_occ": "資深專員／核心工程師"},
                    "success": {"text": "順利完成交付，在團隊內站穩重要位置。", "deltas": {"wealth": 10, "knowledge": 4, "health": -4}, "new_occ": "專業人員"},
                    "fail": {"text": "需求反覆修改讓你累垮住院兩天，考績也受影響。", "deltas": {"health": -9, "happiness": -5, "wealth": 2}},
                },
            },
            {"label": "準時下班顧好健康與副業斜槓", "deltas": {"health": 7, "happiness": 7, "wealth": 5}},
            {"label": "多跟跨部門同事喝咖啡交流，累積人脈資源", "deltas": {"social": 10, "wealth": 5, "happiness": 4}},
        ],
    },
    {
        "id": "s4_startup_offer",
        "stage": "青年冒險",
        "min_age": 21,
        "max_age": 29,
        "title": "{age}歲・朋友揪你一起創業合夥",
        "story": "一位認識多年的朋友帶著商業計畫來{city}找你，邀請你拿出一部分積蓄與他合夥創業，前景誘人但也風險極高。",
        "choices": [
            {
                "label": "押注專業與積蓄加入創始團隊",
                "check": {
                    "stat": "wealth",
                    "difficulty": 56,
                    "label_hint": "看財富",
                    "trait_bonus_ids": ["risk_taker", "street_smart", "math_prodigy"],
                    "crit_success": {"text": "產品精準命中市場痛點，公司拿到天使輪融資！", "deltas": {"wealth": 18, "social": 8, "happiness": 9, "health": -4}, "new_occ": "新創共同創辦人"},
                    "success": {"text": "雖然前半年吃緊，但現金流總算轉正養活團隊。", "deltas": {"wealth": 9, "knowledge": 7, "health": -3}, "new_occ": "創業合夥人"},
                    "fail": {"text": "半年後資金鏈斷裂和平拆夥，賠掉了一筆存款。", "deltas": {"wealth": -11, "happiness": -6, "knowledge": 5}},
                },
            },
            {"label": "幫忙寫顧問企劃收微薄顧問費，保住本業安全網", "deltas": {"wealth": 6, "knowledge": 6, "social": 5}},
            {"label": "禮貌婉拒，把資金放進穩健指數投資", "deltas": {"wealth": 7, "happiness": 3, "knowledge": 3}},
        ],
    },
    {
        "id": "s4_romance_milestone",
        "stage": "情感歸宿",
        "min_age": 21,
        "max_age": 29,
        "requires_unmarried": True,
        "title": "{age}歲・關於兩個人未來的認真對話",
        "story": "夜風微涼的{city}街頭，你與心儀或交往中的對象聊起工作、居住城市與未來藍圖。這可能是決定能否走進婚姻的關鍵一夜。",
        "choices": [
            {
                "label": "坦誠許下承諾，決定攜手步入家庭",
                "check": {
                    "stat": "social",
                    "difficulty": 45,
                    "label_hint": "看人際",
                    "trait_bonus_ids": ["empathetic", "charmer", "optimist"],
                    "crit_success": {"text": "雙方親友都真心祝福！你們在溫馨的婚禮上許下誓言。", "deltas": {"happiness": 14, "social": 10, "wealth": -3}, "set_married": True},
                    "success": {"text": "決定先登記成家、一起分攤生活壓力，幸福踏實。", "deltas": {"happiness": 10, "social": 8, "wealth": -2}, "set_married": True},
                    "fail": {"text": "兩人對未來規劃仍有落差，決定先各自冷靜退回朋友。", "deltas": {"happiness": -6, "social": -2, "knowledge": 3}},
                },
            },
            {"label": "先保持穩定伴侶關係，等經濟再寬裕些", "deltas": {"happiness": 7, "wealth": 5, "social": 5}, "set_partner": True},
            {"label": "認為目前事業與自由更重要，選擇繼續單身衝刺", "deltas": {"wealth": 8, "knowledge": 5, "happiness": 2}},
        ],
    },
    {
        "id": "s4_overseas_work_visa",
        "stage": "跨國機會",
        "min_age": 21,
        "max_age": 29,
        "title": "{age}歲・一封來自海外的駐外／專案邀約",
        "story": "透過國際招募平台或校友牽線，你收到一份與跨國團隊合作或短期駐外歷練的難得機會（若想永久移民別國，也可在副行動申請「跨國移居」）。",
        "choices": [
            {
                "label": "提交外語履歷爭取海外專案主導權",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 60,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["polyglot", "risk_taker", "tech_nerd"],
                    "crit_success": {"text": "流利應答驚豔總部主管！你獲拔擢為跨國核心負責人！", "deltas": {"wealth": 13, "knowledge": 9, "happiness": 8}, "new_occ": "跨國專案負責人"},
                    "success": {"text": "通過繁瑣面試拿下一年的駐外津貼與履歷光環！", "deltas": {"wealth": 8, "knowledge": 7, "happiness": 5}, "new_occ": "跨國商務專員"},
                    "fail": {"text": "最後關頭因語言證明或名額限制未錄取，只好先在本地磨練。", "deltas": {"happiness": -4, "knowledge": 5, "wealth": -2}},
                },
            },
            {"label": "爭取留在{country}接軌跨國遠距業務，不離鄉也能賺外匯", "deltas": {"wealth": 9, "knowledge": 6, "social": 4}},
            {"label": "考量家人都在身邊，決定深耕本地生活圈", "deltas": {"happiness": 7, "social": 8}},
        ],
    },
    {
        "id": "s4_graduate_scholarship",
        "stage": "深造進修",
        "min_age": 20,
        "max_age": 28,
        "title": "{age}歲・研究所／高階證照的敲門磚",
        "story": "你發現若要突破職業天花板，現在考取碩博學位或頂尖專業執照是最好的投資，但也意味著兩三年清貧苦讀。",
        "choices": [
            {
                "label": "報考頂尖研究所或國家級專業執照",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 58,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["scholarly_family", "math_prodigy", "precocious"],
                    "crit_success": {"text": "高分錄取並獲研究津貼，未來身價水漲船高！", "deltas": {"knowledge": 14, "wealth": 5, "happiness": 6}, "new_edu": "碩博士／高階執照", "new_occ": "研究員／專科師"},
                    "success": {"text": "順利取得進修資格，專業履歷大幅加分。", "deltas": {"knowledge": 11, "wealth": -3}, "new_edu": "碩士／專業認證"},
                    "fail": {"text": "差幾分落榜，花了一年補習費卻更看清自己的方向。", "deltas": {"knowledge": 5, "wealth": -5, "happiness": -4}},
                },
            },
            {"label": "邊工作邊修夜間線上課程，不中斷收入", "deltas": {"knowledge": 7, "wealth": 6, "health": -3}},
            {"label": "直接累積實戰工作年資，實務經驗才是最硬底氣", "deltas": {"wealth": 8, "social": 5}},
        ],
    },
    {
        "id": "s4_rent_and_savings",
        "stage": "理財考驗",
        "min_age": 20,
        "max_age": 29,
        "title": "{age}歲・房租漲價與第一桶金",
        "story": "{city}的房租和物價雙雙上漲，月底看著存摺數字，你決定重新調整自己的消費與投資策略。",
        "choices": [
            {"label": "執行極簡記帳與定期定額投資，滾出複利雪球", "deltas": {"wealth": 11, "knowledge": 4, "happiness": -2}},
            {"label": "和幾位合得來的朋友合租大公寓分攤房租", "deltas": {"wealth": 7, "social": 8, "happiness": 5}},
            {"label": "花錢租交通方便的小套房，換取休息品質", "deltas": {"health": 7, "happiness": 6, "wealth": -5}},
        ],
    },
    {
        "id": "s4_burnout_warning",
        "stage": "健康警訊",
        "min_age": 22,
        "max_age": 29,
        "title": "{age}歲・連續熬夜後的胸悶與警鐘",
        "story": "長期外食、咖啡續命和連日趕工，讓你的身體在某個清晨突然發出嚴重疲勞的抗議。",
        "choices": [
            {"label": "請假做全身檢查，從此戒掉熬夜並規律重訓", "deltas": {"health": 12, "happiness": 5, "wealth": -3}},
            {"label": "只吃成藥撐過去，把眼前獎金領到手再說", "deltas": {"wealth": 8, "health": -8, "happiness": -3}},
            {"label": "休個短假回老家或去山海邊放空三天", "deltas": {"happiness": 10, "health": 7, "wealth": -3}},
        ],
    },
    {
        "id": "s4_rural_revival",
        "stage": "青年返鄉",
        "min_age": 21,
        "max_age": 29,
        "urban_rural": "rural",
        "title": "{age}歲・把新點子帶回{city}老家",
        "story": "政府與社區正推動地方創生補助，鼓勵年輕人用電商、設計或智慧農業替家鄉特產開創新市場。",
        "choices": [
            {"label": "申請地方創生計畫，打造特色品牌", "deltas": {"wealth": 10, "happiness": 7, "social": 6}, "new_occ": "地方品牌主理人"},
            {"label": "結合觀光體驗與文化導覽，吸引外地旅人", "deltas": {"social": 10, "happiness": 8, "wealth": 5}},
            {"label": "先留在城市存夠資本再考慮返鄉", "deltas": {"wealth": 7, "knowledge": 4}},
        ],
    },
    {
        "id": "s4_office_politics",
        "stage": "職場智慧",
        "min_age": 22,
        "max_age": 29,
        "title": "{age}歲・部門功勞被搶的難題",
        "story": "你辛苦整理的數據簡報在會議上被一位資深同事搶先拿去報告。全場都在看主管稱讚他。",
        "choices": [
            {"label": "會後冷靜附上原始版本與細節補充寄給主管，優雅拿回主導權", "deltas": {"knowledge": 6, "wealth": 6, "social": 4}},
            {"label": "私下找資深同事坦誠溝通並建立後續分工默契", "deltas": {"social": 9, "happiness": 3}},
            {"label": "默默累積作品集，乾脆準備跳槽去更好的平台", "deltas": {"knowledge": 6, "wealth": 8, "happiness": -2}},
        ],
    },
    {
        "id": "s4_creative_side_gig",
        "stage": "斜槓實驗",
        "min_age": 20,
        "max_age": 29,
        "title": "{age}歲・下班後的第二人生實驗室",
        "story": "你在網路上分享的專業筆記、插畫或技術教學突然獲得不少陌生人轉發，有人私訊問能不能付費委託。",
        "choices": [
            {"label": "認真接案並建立固定收費標準", "deltas": {"wealth": 11, "knowledge": 5, "health": -3}, "new_occ": "自由接案／技術顧問"},
            {"label": "開辦免費線上讀書會或社群交流，先累積影響力", "deltas": {"social": 11, "happiness": 7, "knowledge": 4}},
            {"label": "偶爾客串就好，保持生活的留白與輕鬆", "deltas": {"happiness": 8, "health": 4, "wealth": 3}},
        ],
    },
    {
        "id": "s4_charity_marathon",
        "stage": "公益與友誼",
        "min_age": 20,
        "max_age": 29,
        "title": "{age}歲・公益路跑與跨界友誼",
        "story": "{city}週末將舉辦大型公益馬拉松與社區市集，同事與老朋友都在問你要不要組隊參加。",
        "choices": [
            {"label": "報名半程馬拉松挑戰自己的體能極限", "deltas": {"health": 10, "happiness": 7, "social": 5}},
            {"label": "去當現場志工與物資協調員，認識各行各業的人", "deltas": {"social": 11, "happiness": 7}},
            {"label": "在家補眠並把報名費直接捐作公益", "deltas": {"health": 4, "happiness": 5, "wealth": -2}},
        ],
    },

    # --- Stage 5: 30..42 (成家立業, 10 events) ---
    {
        "id": "s5_marriage_crossroad",
        "stage": "成家立業",
        "min_age": 30,
        "max_age": 42,
        "requires_unmarried": True,
        "title": "{age}歲・三十而立的緣分路口",
        "story": "三十多歲的你經歷過社會洗禮，更清楚自己想要什麼樣的伴侶。一位價值觀契合的對象出現在你的生活裡。",
        "choices": [
            {
                "label": "認真交往並籌備屬於兩人的家",
                "check": {
                    "stat": "social",
                    "difficulty": 46,
                    "label_hint": "看人際",
                    "trait_bonus_ids": ["empathetic", "charmer", "frugal"],
                    "crit_success": {"text": "你們一拍即合迅速步入婚姻，生活既甜蜜又安穩！", "deltas": {"happiness": 14, "social": 9, "wealth": 4}, "set_married": True},
                    "success": {"text": "牽手走進婚姻，在{city}租買了溫暖的小窩。", "deltas": {"happiness": 10, "social": 7, "wealth": -3}, "set_married": True},
                    "fail": {"text": "相處後發現彼此生活節奏難以磨合，體面說再見。", "deltas": {"happiness": -4, "knowledge": 4}},
                },
            },
            {"label": "維持各自獨立的同居／約會關係，不急著領證", "deltas": {"happiness": 8, "social": 6, "wealth": 3}, "set_partner": True},
            {"label": "享受一個人的極致自由，把全部時間投資自己", "deltas": {"wealth": 9, "health": 5, "knowledge": 5}},
        ],
    },
    {
        "id": "s5_new_baby_joy",
        "stage": "親子新篇",
        "min_age": 26,
        "max_age": 42,
        "requires_married": True,
        "title": "{age}歲・迎接新生命的啼哭",
        "story": "家庭迎來了一個重要決定——是否準備好迎接孩子、承擔起下一代的教養與陪伴責任。",
        "choices": [
            {"label": "滿懷期待迎接新生命！全家忙得幸福又充實", "deltas": {"happiness": 13, "social": 8, "wealth": -8, "health": -3}, "add_child": 1},
            {"label": "先存夠育兒基金與買好保險再順其自然", "deltas": {"wealth": 7, "happiness": 6, "knowledge": 4}},
            {"label": "夫妻達成共識專注二人世界與各自理想", "deltas": {"happiness": 8, "wealth": 8, "health": 4}},
        ],
    },
    {
        "id": "s5_promotion_or_home",
        "stage": "職涯高峰",
        "min_age": 30,
        "max_age": 42,
        "title": "{age}歲・管理職升遷與高壓責任",
        "story": "你在{country}累積了紮實資歷，高層開出主管缺額，但條件是每週頻繁出差與扛下整條業務線的業績。",
        "choices": [
            {
                "label": "接下總監／主管重任，站上事業高峰",
                "check": {
                    "stat": "wealth",
                    "difficulty": 52,
                    "label_hint": "看財富",
                    "trait_bonus_ids": ["precocious", "street_smart", "math_prodigy"],
                    "crit_success": {"text": "你帶領團隊業績翻倍，一舉躍升公司核心高管！", "deltas": {"wealth": 16, "social": 7, "health": -5}, "new_occ": "高階主管／總監"},
                    "success": {"text": "頂住壓力穩住部門業績，年薪與話語權同步躍升。", "deltas": {"wealth": 11, "social": 4, "health": -5}, "new_occ": "部門主管"},
                    "fail": {"text": "空降競爭與內部磨合消耗了你大量心力，健康亮黃燈。", "deltas": {"health": -8, "happiness": -5, "wealth": 3}},
                },
            },
            {"label": "選擇不帶人的資深技術／專業專家職，保留生活餘裕", "deltas": {"wealth": 7, "knowledge": 7, "health": 5}, "new_occ": "首席專家／顧問"},
            {"label": "轉入較穩定的機構或教育領域，下班好好陪家人", "deltas": {"happiness": 10, "health": 6, "social": 6}},
        ],
    },
    {
        "id": "s5_industry_layoff",
        "stage": "經濟風暴",
        "min_age": 30,
        "max_age": 42,
        "title": "{age}歲・產業寒冬與組織裁員潮",
        "story": "大環境景氣反轉，{city}不少公司開始縮編，你所在的部門也收到重組與優離通知。",
        "choices": [
            {
                "label": "拿著資遣費進修轉職，趁機跳入更有前景的領域",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 54,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["tech_nerd", "polyglot", "risk_taker"],
                    "crit_success": {"text": "塞翁失馬！你在新產業找到待遇更高、前景更好的位置！", "deltas": {"wealth": 11, "knowledge": 9, "happiness": 7}, "new_occ": "跨界資深專家"},
                    "success": {"text": "經過三個月面試順利無縫銜接新工作。", "deltas": {"wealth": 5, "knowledge": 6}},
                    "fail": {"text": "待業期拉長了半年，靠積蓄硬撐才找到折衷職務。", "deltas": {"wealth": -9, "happiness": -6}},
                },
            },
            {"label": "動用人脈內推，在同業夥伴公司快速卡位", "deltas": {"social": 8, "wealth": 6, "happiness": 2}},
            {"label": "先放慢腳步打零工與整理身心，再思考下一步", "deltas": {"health": 7, "happiness": 5, "wealth": -5}},
        ],
    },
    {
        "id": "s5_headhunter_migrate",
        "stage": "跨國獵頭",
        "min_age": 30,
        "max_age": 42,
        "title": "{age}歲・跨國企業的神秘獵頭電話",
        "story": "憑藉多年累積的履歷與作品，海外獵頭主動聯繫你，開出跨區統籌職務的優渥條件（若打算舉家移居他國，也可於副行動點選「申請跨國移居」）。",
        "choices": [
            {
                "label": "接下跨國高階諮詢／駐區統籌合約",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 58,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["polyglot", "risk_taker", "math_prodigy"],
                    "crit_success": {"text": "終面大獲全勝！你憑稀缺專業談到跨國顧問級年薪！", "deltas": {"wealth": 15, "knowledge": 8, "happiness": 7}, "new_occ": "跨國高階顧問"},
                    "success": {"text": "順利通過背景審查並承接高價值海外專案。", "deltas": {"wealth": 10, "knowledge": 6, "happiness": 4}},
                    "fail": {"text": "預算臨時凍結使專案延期，不過對方付了一筆車馬顧問費。", "deltas": {"wealth": 4, "happiness": -4}},
                },
            },
            {"label": "婉拒外調，但拿這份報價跟現公司談加薪", "deltas": {"wealth": 9, "social": 3}},
            {"label": "覺得現在在{city}的生活步調剛剛好，安居樂業", "deltas": {"happiness": 8, "social": 6}},
        ],
    },
    {
        "id": "s5_mortgage_investment",
        "stage": "資產配置",
        "min_age": 30,
        "max_age": 42,
        "title": "{age}歲・買房安家與市場震盪",
        "story": "累積了一定儲蓄後，你面對人生最大筆的財務決策：扛房貸買下心儀住宅，還是繼續租屋把資金投入市場。",
        "choices": [
            {"label": "買下交通與學區兼顧的自住房，穩紮穩打還貸款", "deltas": {"happiness": 9, "social": 5, "wealth": 5}},
            {"label": "保持租屋彈性，把資金分散配置於全球資產與現金流", "deltas": {"wealth": 11, "knowledge": 5}},
            {"label": "誤信高息投資群組被套牢一筆錢，幸好及時止損", "deltas": {"wealth": -9, "happiness": -6, "knowledge": 5}},
        ],
    },
    {
        "id": "s5_aging_parents",
        "stage": "世代責任",
        "min_age": 32,
        "max_age": 42,
        "title": "{age}歲・父母體力漸衰的牽掛",
        "story": "你忽然注意到父母鬢角的白髮比記憶中多出許多，一次輕微跌倒讓全家開始認真規劃醫療與照護安排。",
        "choices": [
            {"label": "自掏腰包升級長照保險與居家安全設備，親自常回家探望", "deltas": {"happiness": 7, "social": 8, "wealth": -6}},
            {"label": "跟兄弟姊妹／親友協調輪流照料，兼顧工作與孝心", "deltas": {"social": 7, "health": -2, "wealth": -2}},
            {"label": "忙於衝刺事業只能多匯錢回家，心裡總有一絲遺憾", "deltas": {"wealth": 6, "happiness": -4, "social": -2}},
        ],
    },
    {
        "id": "s5_midcareer_mba",
        "stage": "充電再出發",
        "min_age": 30,
        "max_age": 42,
        "title": "{age}歲・工作十年的瓶頸期",
        "story": "同樣的工作流程做了快十年，雖然熟練卻隱約感到成長停滯。你想替自己按下一次系統升級鍵。",
        "choices": [
            {"label": "報讀在職碩士或跨界認證，結識各領域高手", "deltas": {"knowledge": 11, "social": 8, "wealth": -5, "health": -2}, "new_edu": "在職碩士／高階學程"},
            {"label": "爭取內部輪調到完全陌生的新部門從零挑戰", "deltas": {"knowledge": 8, "wealth": 5, "happiness": 4}},
            {"label": "透過長途自助旅行重整心靈，找回生活熱情", "deltas": {"happiness": 11, "health": 5, "wealth": -5}},
        ],
    },
    {
        "id": "s5_chronic_fatigue",
        "stage": "健康轉折",
        "min_age": 31,
        "max_age": 42,
        "title": "{age}歲・體檢單上的紅字警訊",
        "story": "年度健檢報告出現了幾項紅字：血壓偏高、腰椎勞損與睡眠呼吸困擾。醫生嚴肅地叮囑你不能再只靠意志力硬撐。",
        "choices": [
            {"label": "徹底戒糖減鹽、每週三次有氧與肌力訓練", "deltas": {"health": 13, "happiness": 6, "wealth": -2}},
            {"label": "買了高級保健品和人體工學椅，但工作依舊繁忙", "deltas": {"health": 4, "wealth": -4, "knowledge": 2}},
            {"label": "把報告塞進抽屜不管，繼續應酬加班", "deltas": {"wealth": 5, "health": -9, "happiness": -2}},
        ],
    },
    {
        "id": "s5_community_mentor",
        "stage": "人脈與傳承",
        "min_age": 30,
        "max_age": 42,
        "title": "{age}歲・成為年輕後輩眼中的前輩",
        "story": "剛入行的新人遇到你當年也栽過跟頭的難題，誠懇地向你請教經驗。",
        "choices": [
            {"label": "傾囊相授並主動替他牽線資源，建立良性循環", "deltas": {"social": 11, "happiness": 8, "knowledge": 3}},
            {"label": "一起合作完成專案，教學相長互利共贏", "deltas": {"knowledge": 7, "wealth": 6, "social": 6}},
            {"label": "只點到為止，把精力留給自己的核心目標", "deltas": {"wealth": 5, "happiness": 2}},
        ],
    },

    # --- Stage 6: 43..56 (中年砥柱, 9 events) ---
    {
        "id": "s6_midlife_pivot",
        "stage": "中年砥柱",
        "min_age": 43,
        "max_age": 56,
        "title": "{age}歲・產業自動化下的中年轉型",
        "story": "時代技術演進比預期更快，許多重複性業務被軟體與自動化取代。在{city}職場打滾二十年的你必須重新定義自身價值。",
        "choices": [
            {
                "label": "把二十年實戰經驗包裝成獨立顧問或管理培訓",
                "check": {
                    "stat": "social",
                    "difficulty": 55,
                    "label_hint": "看人際",
                    "trait_bonus_ids": ["charmer", "street_smart", "empathetic"],
                    "crit_success": {"text": "多家企業搶著邀約顧問合作，你的影響力更勝從前！", "deltas": {"wealth": 15, "social": 9, "happiness": 7}, "new_occ": "資深管理顧問"},
                    "success": {"text": "順利接軌獨立顧問業務，時間也更自主。", "deltas": {"wealth": 9, "happiness": 5, "social": 5}, "new_occ": "產業顧問"},
                    "fail": {"text": "初期案源不穩，只好同時兼任原本的職務穩定現金流。", "deltas": {"wealth": 2, "happiness": -3, "knowledge": 4}},
                },
            },
            {"label": "咬牙進修新工具與AI協作流程，留在公司成為定海神針", "deltas": {"knowledge": 10, "wealth": 7, "health": -3}},
            {"label": "評估累積資產已足夠，轉入低壓力的半退休節奏", "deltas": {"health": 10, "happiness": 9, "wealth": -3}},
        ],
    },
    {
        "id": "s6_health_checkup",
        "stage": "養生抗老",
        "min_age": 43,
        "max_age": 56,
        "title": "{age}歲・身體與歲月的正面交鋒",
        "story": "步入壯年後期，老花眼與關節痠痛悄悄報到，同齡朋友圈裡開始偶爾傳來心血管或慢性病住院的消息。",
        "choices": [
            {"label": "加入登山社／游泳隊並做精密醫療篩檢", "deltas": {"health": 13, "social": 6, "wealth": -4}},
            {"label": "專注改善飲食與睡眠品質，每天早睡一小時", "deltas": {"health": 10, "happiness": 7}},
            {"label": "趁體力還行再衝一波事業，健康先放第二位", "deltas": {"wealth": 10, "health": -8, "happiness": -2}},
        ],
    },
    {
        "id": "s6_marriage_rekindle",
        "stage": "婚姻歷久",
        "min_age": 43,
        "max_age": 56,
        "requires_married": True,
        "title": "{age}歲・柴米油鹽十數載後的兩人世界",
        "story": "結婚多年後，日常幾乎被帳單、工作與家務填滿。某個週末你們發現兩人已經好久沒有好好說話旅行了。",
        "choices": [
            {"label": "規劃一趟沒有瑣事干擾的兩人重溫小旅行", "deltas": {"happiness": 12, "social": 9, "wealth": -5}},
            {"label": "坐下來坦誠溝通未來十年的分工與退休夢", "deltas": {"happiness": 9, "social": 8, "knowledge": 3}},
            {"label": "覺得老夫老妻不用講求儀式感，繼續各忙各的", "deltas": {"wealth": 4, "happiness": -3, "social": -2}},
        ],
    },
    {
        "id": "s6_friend_loan",
        "stage": "情義與原則",
        "min_age": 43,
        "max_age": 56,
        "title": "{age}歲・老友周轉失靈上門借款",
        "story": "認識二十年的老友生意周轉出了問題，紅著眼眶希望你能借出一筆不小的資金救急救窮。",
        "choices": [
            {"label": "借出不影響自家底線的金額，並簽妥正規借據", "deltas": {"social": 8, "wealth": -5, "happiness": 3}},
            {"label": "不出錢但幫忙介紹買家、律師與銀行方案解決根源", "deltas": {"social": 7, "knowledge": 5, "wealth": 2}},
            {"label": "直接重義氣借出大筆積蓄卻遲遲收不回來", "deltas": {"wealth": -12, "happiness": -6, "social": 4}},
        ],
    },
    {
        "id": "s6_community_leader",
        "stage": "社會聲望",
        "min_age": 43,
        "max_age": 56,
        "title": "{age}歲・地方協會與行業理事長推舉",
        "story": "你在{city}同業與社區間累積了好名聲，大家推舉你出任公益協會／行業公會的召集人。",
        "choices": [
            {"label": "欣然接任，替弱勢與業界發聲並拓展跨界人脈", "deltas": {"social": 12, "happiness": 7, "wealth": 4}},
            {"label": "低調擔任幕後財務或智庫顧問就好", "deltas": {"knowledge": 7, "social": 6, "health": 2}},
            {"label": "婉拒頭銜，把週末還給花園、閱讀與家人", "deltas": {"happiness": 9, "health": 6}},
        ],
    },
    {
        "id": "s6_write_book_legacy",
        "stage": "知識沉澱",
        "min_age": 43,
        "max_age": 56,
        "title": "{age}歲・把半生心得寫成系統作品",
        "story": "出版社或網路平台邀請你把多年累積的專業方法與人生故事整理成書或公開課程。",
        "choices": [
            {"label": "花半年心血完成著作，意外成為長銷佳作", "deltas": {"knowledge": 10, "wealth": 9, "happiness": 8}},
            {"label": "開辦免費講座或開源筆記回饋年輕世代", "deltas": {"social": 10, "happiness": 9, "knowledge": 5}},
            {"label": "先寫日記給自己看就好，不想應付出版壓力", "deltas": {"happiness": 7, "health": 4}},
        ],
    },
    {
        "id": "s6_side_business_boom",
        "stage": "財富機遇",
        "min_age": 43,
        "max_age": 56,
        "title": "{age}歲・沉潛多年的興趣副業意外走紅",
        "story": "你經營許久的手作工藝、投資研究或在地選品小店，突然被媒體或社群大推，訂單湧入。",
        "choices": [
            {"label": "果斷擴編並申請專利／商標，做成第二事業", "deltas": {"wealth": 14, "happiness": 7, "health": -3}, "new_occ": "品牌企業主"},
            {"label": "維持限量精緻接單，不讓商業化毀掉快樂", "deltas": {"happiness": 10, "wealth": 7, "social": 4}},
            {"label": "高價將品牌或配方授權出去，輕鬆收權利金", "deltas": {"wealth": 12, "health": 5}},
        ],
    },
    {
        "id": "s6_countryside_cottage",
        "stage": "生活節奏",
        "min_age": 44,
        "max_age": 56,
        "title": "{age}歲・都市喧囂與鄉間小屋的選擇",
        "story": "看著{city}日益擁擠的街道，你開始思考是否該在郊區或鄉下找一處能種花看雲的居所。",
        "choices": [
            {"label": "週末搬到近郊慢活種菜，身心靈徹底放鬆", "deltas": {"health": 10, "happiness": 10, "wealth": -4}},
            {"label": "留在醫療與交通便利的市區，改裝室內成溫馨書房", "deltas": {"knowledge": 6, "health": 5, "happiness": 6}},
            {"label": "置產小套房出租增加被動收入，自己保持輕簡生活", "deltas": {"wealth": 10, "knowledge": 4}},
        ],
    },
    {
        "id": "s6_low_wealth_resilience",
        "stage": "逆境堅守",
        "min_age": 43,
        "max_age": 56,
        "max_stats": {"wealth": 38},
        "title": "{age}歲・風雨裡的咬牙撐持",
        "story": "人生並不總是一帆風順，手頭依然緊繃，但多年歷練讓你學會用最少資源過出尊嚴。",
        "choices": [
            {"label": "申請政府職業再培訓與社區補助重新站穩", "deltas": {"wealth": 9, "knowledge": 6, "happiness": 4}},
            {"label": "靠好人緣與鄰里換工互助，節流開源", "deltas": {"social": 9, "wealth": 7, "happiness": 5}},
            {"label": "再苦也要維持每天散步與簡單閱讀的習慣", "deltas": {"health": 8, "happiness": 7, "knowledge": 4}},
        ],
    },

    # --- Stage 7: 57..72 (熟齡轉折, 8 events) ---
    {
        "id": "s7_retirement_decision",
        "stage": "熟齡轉折",
        "min_age": 57,
        "max_age": 72,
        "title": "{age}歲・告別全職職場還是退而不休？",
        "story": "你在{country}走過數十年職涯，正式站在退休門檻前。該徹底交棒給年輕人，還是以顧問身分延續熱度？",
        "choices": [
            {"label": "光榮退休！規畫每天散步、看書與含飴弄孫／旅遊生活", "deltas": {"health": 9, "happiness": 11, "knowledge": 4}, "new_occ": "退休長者"},
            {"label": "轉為每週工作兩天的資深顧問，保持腦力與收入", "deltas": {"wealth": 8, "knowledge": 6, "social": 5}, "new_occ": "榮譽顧問"},
            {"label": "投入銀髮創業或非營利組織貢獻管理經驗", "deltas": {"social": 10, "happiness": 8, "health": -2}, "new_occ": "公益機構理事"},
        ],
    },
    {
        "id": "s7_pension_scam_alert",
        "stage": "晚年財富守衛",
        "min_age": 57,
        "max_age": 72,
        "title": "{age}歲・高息退休理財方案的糖衣陷阱",
        "story": "一位衣著體面的理專熱情向你推銷號稱「保本年息 18%」的海外退休基金，還不斷催促你這週內匯款。",
        "choices": [
            {
                "label": "冷靜查核金管會資料並請教懂法律財經的朋友",
                "check": {
                    "stat": "knowledge",
                    "difficulty": 45,
                    "label_hint": "看知識",
                    "trait_bonus_ids": ["frugal", "street_smart", "math_prodigy"],
                    "crit_success": {"text": "一眼識破龐氏騙局還報警救了好幾位鄰居的養老金！", "deltas": {"social": 11, "happiness": 9, "wealth": 5}},
                    "success": {"text": "果斷拒絕可疑推銷，把老本穩穩守在安全定存與公債裡。", "deltas": {"wealth": 5, "happiness": 5}},
                    "fail": {"text": "雖然猶豫了很久最後沒投大錢，但被收了一筆高額手續費。", "deltas": {"wealth": -6, "happiness": -4}},
                },
            },
            {"label": "全部交給信任的親友或銀行信託把關，不自行亂簽約", "deltas": {"social": 6, "wealth": 4, "happiness": 4}},
            {"label": "被高報酬沖昏頭先投進一小筆，半年後對方竟人間蒸發", "deltas": {"wealth": -13, "happiness": -8}},
        ],
    },
    {
        "id": "s7_rehab_comeback",
        "stage": "健康復健",
        "min_age": 57,
        "max_age": 72,
        "title": "{age}歲・手術後漫長的復健晨光",
        "story": "一次關節或心血管的小手術讓你不得不在醫院休養。走出病房後，復健師告訴你唯有持之以恆才能找回行動自由。",
        "choices": [
            {
                "label": "風雨無阻完成每日復健與營養管理",
                "check": {
                    "stat": "health",
                    "difficulty": 48,
                    "label_hint": "看健康",
                    "trait_bonus_ids": ["iron_constitution", "optimist"],
                    "crit_success": {"text": "恢復速度連主治醫師都驚嘆，你甚至能重返健行步道！", "deltas": {"health": 14, "happiness": 9}},
                    "success": {"text": "半年後生活完全自理，氣色比手術前更好。", "deltas": {"health": 10, "happiness": 6}},
                    "fail": {"text": "復健過程反覆痠痛，恢復比預期慢了許多。", "deltas": {"health": 3, "happiness": -3}},
                },
            },
            {"label": "請專業照護與復健教練協助，用金錢換取高品質康復", "deltas": {"health": 11, "wealth": -7, "happiness": 5}},
            {"label": "在家由親友陪伴慢慢休養", "deltas": {"health": 6, "social": 7, "happiness": 5}},
        ],
    },
    {
        "id": "s7_dream_trip",
        "stage": "圓夢之旅",
        "min_age": 57,
        "max_age": 72,
        "title": "{age}歲・打開年輕時寫在筆記本上的旅行清單",
        "story": "翻開舊抽屜，那張畫著極光、古蹟與海岸線的清單還在。身體還走得動，你決定趁現在去看看世界。",
        "choices": [
            {"label": "動用部分積蓄走訪夢想國度，替人生寫下壯麗詩篇", "deltas": {"happiness": 14, "knowledge": 6, "wealth": -8}},
            {"label": "揪一群老朋友進行國內深度慢旅，歡笑不斷", "deltas": {"social": 11, "happiness": 10, "wealth": -4}},
            {"label": "在{city}周邊探索從沒仔細走過的歷史老街與博物館", "deltas": {"knowledge": 7, "health": 5, "happiness": 7}},
        ],
    },
    {
        "id": "s7_community_volunteer",
        "stage": "銀髮志業",
        "min_age": 57,
        "max_age": 72,
        "title": "{age}歲・社區合唱團與圖書館志工",
        "story": "退休後的時間變得無比寬裕，{city}社區中心正在招募故事志工、合唱團員與樂齡數位導師。",
        "choices": [
            {"label": "去小學與圖書館講故事、教孩子手藝", "deltas": {"social": 11, "happiness": 10, "knowledge": 4}},
            {"label": "加入樂齡合唱團或太極班，認識新老友", "deltas": {"health": 8, "social": 10, "happiness": 8}},
            {"label": "當起樂齡反詐與手機小老師，幫助同齡人跟上時代", "deltas": {"knowledge": 8, "social": 9, "happiness": 7}},
        ],
    },
    {
        "id": "s7_late_passion",
        "stage": "第二青春",
        "min_age": 57,
        "max_age": 72,
        "title": "{age}歲・60歲才開始學畫畫／程式／樂器",
        "story": "誰說夢想有保鮮期？你決定重拾年輕時因忙碌而放下的興趣，報名了{city}的成人進階班。",
        "choices": [
            {"label": "全心投入練習，甚至辦了一場小型成果展", "deltas": {"happiness": 12, "knowledge": 9, "social": 6}},
            {"label": "在網路上寫部落格／拍短片分享銀髮樂活日常", "deltas": {"social": 10, "happiness": 9, "wealth": 3}},
            {"label": "只為自己而學，每天沉醉在專注的平靜裡", "deltas": {"happiness": 10, "health": 5, "knowledge": 6}},
        ],
    },
    {
        "id": "s7_estate_planning",
        "stage": "從容交棒",
        "min_age": 60,
        "max_age": 72,
        "title": "{age}歲・整理資產與人生清單",
        "story": "你決定趁頭腦還清楚時，把一輩子打拚下來的資產、保單、照片與遺願坦然整理清楚，不讓身後留下紛爭。",
        "choices": [
            {"label": "找專業律師公正規劃，部分撥作慈善或教育基金", "deltas": {"happiness": 9, "knowledge": 5, "social": 7, "wealth": -3}},
            {"label": "分批把心愛收藏送給真正珍惜它們的晚輩與朋友", "deltas": {"social": 10, "happiness": 9}},
            {"label": "留夠自己高品質養老與醫療的本錢，剩餘隨緣", "deltas": {"health": 6, "wealth": 5, "happiness": 6}},
        ],
    },
    {
        "id": "s7_grandkids_wisdom",
        "stage": "天倫之樂",
        "min_age": 58,
        "max_age": 72,
        "requires_married": True,
        "title": "{age}歲・飯桌上的代間對話",
        "story": "假日家裡熱鬧起來，晚輩帶著好奇心問你年輕時那個年代到底是什麼模樣。",
        "choices": [
            {"label": "拿出泛黃相簿笑著講當年的冒險與傻事", "deltas": {"happiness": 12, "social": 10}},
            {"label": "教他們下棋、做一道拿手家常菜作為家族傳承", "deltas": {"social": 9, "happiness": 9, "knowledge": 4}},
            {"label": "安靜聽年輕人聊最新科技與煩惱，當最溫柔的傾聽者", "deltas": {"social": 10, "knowledge": 6, "happiness": 8}},
        ],
    },

    # --- Stage 8: > 72 (金色晚年, 7 events) ---
    {
        "id": "s8_park_tai_chi",
        "stage": "金色晚年",
        "min_age": 73,
        "max_age": 120,
        "title": "{age}歲・晨曦公園裡的棋局與太極",
        "story": "清晨七點，{city}公園的老樹下已聚集幾位白髮老友。晨光灑在石桌上，今天也是一如既往的溫柔早晨。",
        "choices": [
            {"label": "打完一套養生操再跟老友下幾盤棋喝早茶", "deltas": {"health": 8, "social": 9, "happiness": 8}},
            {"label": "帶著相機拍下四季花鳥的變化寫短詩", "deltas": {"happiness": 10, "knowledge": 5}},
            {"label": "靜靜坐在長椅上曬太陽，享受無憂的慢節奏", "deltas": {"health": 6, "happiness": 9}},
        ],
    },
    {
        "id": "s8_memoir_album",
        "stage": "歲月回響",
        "min_age": 73,
        "max_age": 120,
        "title": "{age}歲・翻閱整個人生的相簿",
        "story": "你打開保存多年的木盒，裡頭有出生證明、車票、成績單、情書與各國紀念幣，每張紙都在訴說你獨一無二的一生。",
        "choices": [
            {"label": "口述或寫下詳細回憶錄留給家族與社區檔案館", "deltas": {"knowledge": 8, "happiness": 10, "social": 6}},
            {"label": "打電話給幾十年沒見的老同學敘舊道謝", "deltas": {"social": 11, "happiness": 10}},
            {"label": "泡一壺熱茶微笑著合上盒子，一切已無遺憾", "deltas": {"happiness": 11, "health": 4}},
        ],
    },
    {
        "id": "s8_gentle_care",
        "stage": "健康守護",
        "min_age": 73,
        "max_age": 120,
        "title": "{age}歲・醫療團隊與安穩的晚年照護",
        "story": "年事已高，身體各項機能如黃昏般緩慢沉落。醫師與家屬討論接下來該如何平衡醫療介入與生活尊嚴。",
        "choices": [
            {"label": "配合溫和復健與精準用藥，保持清醒與行動力", "deltas": {"health": 9, "happiness": 5, "wealth": -4}},
            {"label": "預立溫柔的安寧醫療意願，追求無痛且有尊嚴的日常", "deltas": {"happiness": 11, "health": 4, "social": 5}},
            {"label": "把日常交給專業護理機構，讓家人輕鬆來探望聊天", "deltas": {"social": 8, "health": 6, "wealth": -5}},
        ],
    },
    {
        "id": "s8_donate_library",
        "stage": "無私奉獻",
        "min_age": 73,
        "max_age": 120,
        "title": "{age}歲・把珍藏的書與技能傳給下一代",
        "story": "家中累積了數百本書籍或陪伴一生的專業工具，你希望在自己還有力氣時替它們找到最棒的新主人。",
        "choices": [
            {"label": "全數捐贈給偏鄉學校或青年公益教室", "deltas": {"happiness": 12, "social": 10, "wealth": -3}},
            {"label": "親自挑選有緣的年輕學徒傳授畢生心法", "deltas": {"knowledge": 6, "social": 10, "happiness": 10}},
            {"label": "舉辦一場溫馨的社區舊物分享市集", "deltas": {"social": 9, "happiness": 9, "wealth": 2}},
        ],
    },
    {
        "id": "s8_reconcile_past",
        "stage": "心靈圓滿",
        "min_age": 73,
        "max_age": 120,
        "title": "{age}歲・一場跨越數十年的和解",
        "story": "偶然得知一位年輕時曾與你產生嫌隙的舊識就住在不遠處，歲月早已磨平了當年誰對誰錯的稜角。",
        "choices": [
            {"label": "主動寄出一張寫著「祝好」的問候卡片化解心結", "deltas": {"happiness": 13, "social": 9}},
            {"label": "約出來喝杯清茶聊聊各自這些年的風雨", "deltas": {"social": 10, "happiness": 11}},
            {"label": "在心裡默默祝福對方，把平靜留給現在的生活", "deltas": {"happiness": 8, "health": 4}},
        ],
    },
    {
        "id": "s8_city_sunset",
        "stage": "時代靜好",
        "min_age": 73,
        "max_age": 120,
        "title": "{age}歲・看著{city}黃昏天際線的變化",
        "story": "站在陽台或庭院看著傍晚的雲彩，這座城市從你出生到現在已經換過好幾代建築，而你也成了它故事的一部分。",
        "choices": [
            {"label": "趁體力允許再出門吃一頓最愛的家鄉味美食", "deltas": {"happiness": 11, "health": 3}},
            {"label": "聽著年輕時最愛的老唱片，細細品味記憶", "deltas": {"happiness": 10, "knowledge": 3}},
            {"label": "早早洗漱入睡，期待明天清晨的鳥鳴", "deltas": {"health": 8, "happiness": 7}},
        ],
    },
    {
        "id": "s8_letter_future",
        "stage": "生命遺贈",
        "min_age": 75,
        "max_age": 120,
        "title": "{age}歲・寫給未來陌生人的一封信",
        "story": "社區舉辦「世紀時間膠囊」活動，邀請走過大半世紀的長者寫下一段真正想對後世說的話。",
        "choices": [
            {"label": "寫下：「別忘了抬頭看星星，也別對愛的人吝嗇擁抱。」", "deltas": {"happiness": 12, "social": 8}},
            {"label": "寫下：「保持好奇與閱讀，世界永遠比你以為的更寬廣。」", "deltas": {"knowledge": 8, "happiness": 10}},
            {"label": "寫下：「照顧好身體與睡眠，平凡的一天就是奇蹟。」", "deltas": {"health": 7, "happiness": 10}},
        ],
    },
]

_STAGE_FALLBACK = {
    "id": "fallback_generic",
    "stage": "人生旅途",
    "title": "{age}歲・{city}的日常與抉擇",
    "story": "在{country}{city}的日子平實而流動，身為「{occupation}」的你，正在思考接下來要把心力放在哪裡。",
    "choices": [
        {"label": "充實專業知識並規劃未來", "deltas": {"knowledge": 7, "happiness": 2}},
        {"label": "努力工作打拚、穩健累積資源", "deltas": {"wealth": 8, "health": -2}},
        {"label": "照顧身體健康並陪伴親友", "deltas": {"health": 7, "happiness": 6, "social": 5}},
    ],
}


def _lookup_country_by_code(code: str) -> dict:
    try:
        world = load_world_demographics()
        c_up = (code or "").upper()
        for c in world:
            if str(c.get("code") or "").upper() == c_up:
                return c
    except Exception:
        pass
    return {"code": code or "XX", "name_zh": "世界", "region": "東亞", "tier": "lower_mid", "gdp_pc": 8000, "urban_pct": 55}


def pick_local_event(
    run: dict,
    country: Optional[dict] = None,
    age: Optional[int] = None,
    rng: Optional[random.Random] = None,
) -> dict:
    """從 78 則條件事件＋歷史浪潮中抽取未重複的寫實事件，並預先計算選項檢定勝率。"""
    rand = rng or random
    eff_age = int(run["age"] if age is None else age)
    eff_country = country or _lookup_country_by_code(
        run.get("current_country_code") or run.get("country_code") or ""
    )

    all_events = EVENT_POOL + HISTORICAL_ERA_EVENTS
    seen_set = set(run.get("seen_events") or [])

    # ~24% chance to prioritize matching historical macro event if unseen
    hist_candidates = [ev for ev in HISTORICAL_ERA_EVENTS if _event_matches(ev, run, eff_country, eff_age, ignore_seen=False)]
    if hist_candidates and rand.random() < 0.25:
        chosen = rand.choice(hist_candidates)
        return _materialize_event(chosen, run, eff_country, eff_age)

    candidates = [ev for ev in all_events if _event_matches(ev, run, eff_country, eff_age, ignore_seen=False)]
    if not candidates:
        # Relax seen_events filter only if all age-appropriate events were already seen
        candidates = [ev for ev in all_events if _event_matches(ev, run, eff_country, eff_age, ignore_seen=True)]

    if candidates:
        # Prefer unseen or trait-specific events when available
        trait_candidates = [ev for ev in candidates if ev.get("trait_any") and ev["id"] not in seen_set]
        if trait_candidates and rand.random() < 0.35:
            chosen = rand.choice(trait_candidates)
        else:
            chosen = rand.choice(candidates)
        return _materialize_event(chosen, run, eff_country, eff_age)

    return _materialize_event(_STAGE_FALLBACK, run, eff_country, eff_age)


def _build_local_event(
    run_or_country: dict,
    age: Optional[int] = None,
    rng: Optional[random.Random] = None,
    run: Optional[dict] = None,
) -> dict:
    """向下相容包覆：支援舊版 `_build_local_event(run)` 與新版 `_build_local_event(country, age, rng=..., run=...)`。"""
    if age is None and run is None and "age" in run_or_country and "country_code" in run_or_country:
        eff_run = run_or_country
        eff_country = _lookup_country_by_code(
            eff_run.get("current_country_code") or eff_run.get("country_code") or ""
        )
        return pick_local_event(eff_run, eff_country, eff_run["age"], rng=rng)

    country = run_or_country
    eff_age = int(age if age is not None else (run or {}).get("age", 5))
    if run is not None:
        eff_run = run
    else:
        cities = country.get("cities") or [country.get("name_zh", "城市")]
        eff_run = {
            "id": 0,
            "age": eff_age,
            "birth_year": 2000,
            "country_code": country.get("code", "XX"),
            "country_name": country.get("name_zh", "當地"),
            "current_country_code": country.get("code", "XX"),
            "current_country_name": country.get("name_zh", "當地"),
            "city": cities[0],
            "current_city": cities[0],
            "char_name": "旅人",
            "family_class": "小康中產",
            "occupation": "學生" if eff_age < 20 else "上班族",
            "urban_rural": "urban",
            "health": 65,
            "happiness": 65,
            "knowledge": 55,
            "wealth": 55,
            "social": 60,
            "traits": [],
            "relationships": {"partner": None, "married": False, "children": 0},
            "seen_events": [],
        }
    return pick_local_event(eff_run, country, eff_age, rng=rng)
