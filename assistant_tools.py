import contextvars
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
import discord

import assistant_db
import boardgame_engine
import rpg_engine
import flashcard_engine
import game_wiki_engine

# 透過 ContextVar 傳遞當前觸發的 Discord 訊息上下文 (執行緒與非同步安全)
current_discord_msg = assistant_db.current_discord_msg


def record_memory(content: str) -> str:
    """
    記錄重要事情、個人喜好、習慣、重要日期、群組共識或約定到長期記憶庫中。即使聊天記憶重設，此記憶仍會永久保存。
    參數:
      content: 欲記錄的事實、喜好、設定或備忘內容（例如：「某成員喜歡抹茶」、「重要紀念日或活動是 10 月 15 日」）。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "共同"
    mid = assistant_db.add_memory(content=content, user_name=user_name)
    return f"已成功寫入長期記憶庫（編號 #{mid}）：{content}"


def create_todo(task: str) -> str:
    """
    新增一項待辦事項到共同待辦清單中。
    參數:
      task: 待辦事項內容（例如：「訂週六晚上餐廳」、「買衛生紙」）。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "使用者"
    tid = assistant_db.add_todo(content=task, user_name=user_name)
    return f"已成功新增待辦事項（編號 #{tid}）：{task}（由 {user_name} 新增）"


def finish_todo(task_keyword_or_id: str) -> str:
    """
    將待辦事項標記為已完成。
    參數:
      task_keyword_or_id: 待辦編號（如 '1'）或關鍵字（如 '餐廳'）。
    """
    kw = str(task_keyword_or_id).strip()
    completed = None
    if kw.isdigit():
        completed = assistant_db.complete_todo_by_id(int(kw))
    if not completed:
        completed = assistant_db.complete_todo_by_keyword(kw)

    if completed:
        return f"已完成待辦事項 #{completed['id']}「{completed['content']}」！"
    return f"找不到符合「{kw}」的未完成待辦事項。"


def query_todos() -> str:
    """
    查詢目前尚未完成的待辦事項清單。
    """
    todos = assistant_db.list_todos(only_pending=True)
    if not todos:
        return "目前沒有任何未完成的待辦事項。"
    lines = ["目前尚未完成的待辦清單："]
    for t in todos:
        lines.append(f"- #{t['id']} {t['content']} (由 {t['user_name']} 於 {t['created_at']} 新增)")
    return "\n".join(lines)


def set_reminder(target_time_iso: str, reminder_message: str) -> str:
    """
    設定定時提醒。
    參數:
      target_time_iso: 目標提醒時間，必須為 ISO 8601 格式字串（例如 '2026-10-31T15:30:00+08:00'）。必須根據當前台北時間 (UTC+8) 計算。
      reminder_message: 時間到達時要提醒的內容（例如：「出門倒垃圾」、「關火」、「搶票」）。
    """
    msg = current_discord_msg.get()
    channel_id = msg.channel.id if msg else 0
    user_id = msg.author.id if msg else 0
    user_name = msg.author.display_name if msg else "使用者"

    dt = assistant_db.parse_iso_datetime(target_time_iso)
    if not dt:
        return f"時間格式解析失敗（{target_time_iso}），請確認使用正確的時間格式。"

    now = datetime.now(assistant_db.TZ_TAIPEI)
    if dt <= now:
        return f"設定的時間 {target_time_iso} 已經過去了，請設定未來的時間。"

    rid = assistant_db.add_reminder(
        channel_id=channel_id,
        user_id=user_id,
        user_name=user_name,
        remind_at_iso=dt.isoformat(),
        content=reminder_message
    )
    formatted_time = dt.strftime("%Y-%m-%d %H:%M")
    return f"已設定提醒（編號 #{rid}）！我會在 {formatted_time} 提醒 {user_name}：「{reminder_message}」。"


def parse_time_argument(arg: str) -> Optional[datetime]:
    """彈性解析命令列傳入的時間參數（如 10m、2h、30s、15:30 或完整日期）"""
    now = datetime.now(assistant_db.TZ_TAIPEI)
    arg = arg.strip().lower()

    # 相對時間：數字 + 單位 (s, m, h, d)
    m = re.match(r"^(\d+)\s*(s|sec|seconds?|m|min|minutes?|h|hr|hours?|d|days?)$", arg)
    if m:
        num = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("s"):
            return now + timedelta(seconds=num)
        elif unit.startswith("m"):
            return now + timedelta(minutes=num)
        elif unit.startswith("h"):
            return now + timedelta(hours=num)
        elif unit.startswith("d"):
            return now + timedelta(days=num)

    # 鐘點時間：HH:MM (例如 15:30)
    m = re.match(r"^(\d{1,2}):(\d{2})$", arg)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            return target

    # 完整 ISO 或日期時間
    return assistant_db.parse_iso_datetime(arg)


def add_flashcard_card(
    word_or_phrase: str,
    reading: str = "",
    meaning: str = "",
    example_sentence_jp: str = "",
    example_sentence_zh: str = "",
    deck_name: Optional[str] = None
) -> str:
    """
    將欲學習的日語單字、片語、文法或佳句整理成單字卡 (Flashcard) 儲存至資料庫中。
    若未指定牌組名稱 (deck_name)，系統會自動放入預設的「⭐ 兩人的日常收藏」牌組中。
    參數:
      word_or_phrase: 日語漢字或單字/片語（例如：「木漏れ日」、「乾杯」、「美味しい」）。
      reading: 平假名讀音（例如：「こもれび」、「かんぱい」、「おいしい」）。
      meaning: 繁體中文釋義（例如：「從樹葉縫隙透進來的陽光」、「乾杯」、「好吃的」）。
      example_sentence_jp: 實用日語生活例句（若無可由 AI 自然造句）。
      example_sentence_zh: 繁體中文例句翻譯。
      deck_name: 目標牌組名稱（例如：「居酒屋日文」、「動漫常用語」）。若無指定可留空，自動放入預設收集箱。
    """
    msg = current_discord_msg.get()
    user_name = msg.author.display_name if msg else "使用者"
    user_id = msg.author.id if msg else 0

    if deck_name and deck_name.strip():
        deck = flashcard_engine.get_or_create_deck(deck_name.strip(), created_by=user_name)
    else:
        deck = flashcard_engine.get_user_active_deck(user_id) if user_id else flashcard_engine.get_or_create_deck("⭐ 兩人的日常收藏")

    card = flashcard_engine.add_card(
        deck_id=deck["id"],
        front=word_or_phrase,
        reading=reading,
        back=meaning,
        example_jp=example_sentence_jp,
        example_zh=example_sentence_zh
    )

    rd_str = f" ({reading})" if reading else ""
    ex_str = f"\n💬 例句：{example_sentence_jp}（{example_sentence_zh}）" if example_sentence_jp else ""
    return (
        f"🎴 已成功收錄單字卡 #{card['id']}！\n"
        f"單字：【{word_or_phrase}{rd_str}】➔ 釋義：{meaning}\n"
        f"📁 已歸入牌組：【{deck['name']}】{ex_str}"
    )


def lookup_game_wiki(query: str) -> str:
    """
    查詢《殭屍學園 (Zombie High School / 좀비고등학교)》本地結構化知識庫（Contextual-LightRAG）。
    當遇到《殭屍學園》角色、主線/Division/Heroes 劇情、遊戲模式、職業射程機制、AU 活動，
    或韓服房間列表縮寫、韓文初聲縮寫（如 'ㄷㅋㅈ'、'34퀸'、'클모'、'오더'、'조건x'、'prace'）時，
    必須調用此工具查詢官方設定與黑話定義，嚴禁自行猜測韓文縮寫或腦補設定！
    參數:
      query: 欲查詢的關鍵字（支援繁中譯名、韓文原文、韓文初聲縮寫或房名詞彙，可同時傳入多個空白分隔的關鍵字）。
    """
    return game_wiki_engine.lookup_game_wiki_tool_impl(query)


def _queue_board_card_update(game_id: int, note: str, run_ai: bool = False) -> None:
    pending = boardgame_engine.pending_board_card_updates.get()
    if pending is not None:
        pending.append({"game_id": game_id, "note": note, "run_ai": run_ai})


def boardgame_get_state() -> str:
    """
    查詢當前頻道進行中的棋盤桌遊（黑白棋、西洋棋、四子棋、五子棋）權威盤面、棋子座標分布與所有合法著法。
    當使用者用自然語言描述想移動哪顆棋子或想落子在哪裡時，必須先呼叫此工具對照合法座標與盤面，嚴禁自己猜測或編造棋盤狀態！
    """
    msg = current_discord_msg.get()
    channel_id = msg.channel.id if msg else 0
    return boardgame_engine.get_board_status_summary_for_llm(channel_id)


def boardgame_start(
    game_type: str,
    mode: str = "pve",
    difficulty: str = "normal",
) -> str:
    """
    在當前頻道開啟一場新的圖形化棋盤桌遊對局，並自動貼出附帶座標圖與互動按鈕的棋盤卡片。
    參數:
      game_type: 遊戲名稱（支援 'othello'/'黑白棋'、'chess'/'西洋棋'、'connect4'/'四子棋'、'gomoku'/'五子棋'）。
      mode: 對戰模式，'pve'（對戰電腦或 LLM，預設）或 'pvp'（雙人公開挑戰）。
      difficulty: PvE 難度，可填 'easy'（簡單）、'normal'（普通，預設）、'hard'（困難）或 'llm'（與後端 Gemini LLM 對戰且防作弊）。
    """
    msg = current_discord_msg.get()
    if not msg:
        return "❌ 無法取得目前 Discord 頻道上下文。"
    gtype = boardgame_engine.resolve_game_type(game_type)
    if not gtype:
        return f"❓ 找不到遊戲 `{game_type}`。目前支援：黑白棋 (othello)、西洋棋 (chess)、四子棋 (connect4)、五子棋 (gomoku)。"
    clean_mode = "pvp" if (mode or "").strip().lower() in ("pvp", "雙人", "對戰") else "pve"
    clean_diff = boardgame_engine.resolve_difficulty(difficulty)
    try:
        game = boardgame_engine.create_game(
            channel_id=msg.channel.id,
            game_type=gtype,
            mode=clean_mode,
            p1_id=msg.author.id,
            p1_name=msg.author.display_name,
            difficulty=clean_diff,
        )
    except ValueError as e:
        return f"⚠️ 開局失敗：{e}"

    _queue_board_card_update(game["id"], f"由 {msg.author.display_name} 透過對話開啟新局！")
    return (
        f"✅ 已成功開啟【{boardgame_engine.GAME_CATALOG[gtype]['name']}】"
        f"（模式：{clean_mode.upper()} | 對手：{boardgame_engine.DIFFICULTY_LABELS[clean_diff] if clean_mode == 'pve' else '等待好友加入'}）！\n"
        f"系統將在你的回覆下方自動貼出高清座標棋盤卡片與點擊互動選單。\n"
        f"{boardgame_engine.get_board_status_summary_for_llm(msg.channel.id)}"
    )


def boardgame_play_move(move_str: str) -> str:
    """
    依玩家在自然語言中的指令，對當前頻道的棋局執行一步棋並由 Python 引擎進行 100% 合法性校驗。
    若為 PvE 模式，執行完玩家這步後會自動由本地 AI 或後端 LLM 回應下一步並更新圖形棋盤卡。
    參數:
      move_str: 棋步字串（黑白棋/五子棋填座標如 'D3'、'G7'；四子棋填欄號 '1'~'7'；西洋棋填如 'e2e4'、'Nf3'、'O-O'）。
    """
    msg = current_discord_msg.get()
    if not msg:
        return "❌ 無法取得目前 Discord 頻道上下文。"
    active = boardgame_engine.get_active_game(msg.channel.id)
    if not active or active["status"] != "active":
        return "⚠️ 目前此頻道沒有進行中的對局。若玩家想玩，可先呼叫 `boardgame_start` 開局。"
    try:
        updated, note = boardgame_engine.apply_move(active["id"], msg.author.id, move_str)
    except ValueError as e:
        legal_hint = boardgame_engine.get_board_status_summary_for_llm(msg.channel.id)
        return f"⚠️ 該步不合法：{e}\n{legal_hint}"

    if updated["mode"] == "pve" and updated.get("difficulty") == "llm" and updated["status"] == "active":
        _queue_board_card_update(updated["id"], note, run_ai=True)
        return (
            f"✅ 玩家步法已通過校驗並落子：{note}\n"
            f"系統將在回覆後自動由 LLM 對手落子（經防作弊規則裁判）並貼出最新圖形棋盤卡！"
        )

    updated, full_note = boardgame_engine.play_pve_ai_turn_sync(updated, note)
    _queue_board_card_update(updated["id"], full_note, run_ai=False)
    status_tail = "🎉 對局已分出勝負／結束！" if updated["status"] == "finished" else "系統已更新圖形棋盤卡並貼在回覆下方。"
    return (
        f"✅ 已落子並完成規則校驗！\n{full_note}\n{status_tail}\n"
        f"{boardgame_engine.get_board_status_summary_for_llm(msg.channel.id)}"
    )


def boardgame_undo() -> str:
    """
    為當前發言的人類玩家申請或同意「退回上一步（悔棋）」。
    嚴格限制：只有實際參賽的玩家本人可呼叫，且需該局全體參賽玩家皆同意方能執行（PvP 雙人需雙方各自同意後才會回溯）。
    """
    msg = current_discord_msg.get()
    if not msg:
        return "❌ 無法取得目前 Discord 頻道上下文。"
    active = boardgame_engine.get_active_game(msg.channel.id)
    if not active or active["status"] != "active":
        return "⚠️ 目前沒有進行中的對局可退回上一步。"
    try:
        updated, undo_status = boardgame_engine.request_or_approve_undo(active["id"], msg.author.id)
    except ValueError as e:
        return f"⚠️ 無法執行悔棋：{e}"

    if undo_status == "already_approved":
        return "ℹ️ 你已經同意退回上一步，請等待對手點擊卡片上的「✅ 同意上一步」或說他也同意悔棋。"
    if undo_status == "pending":
        _queue_board_card_update(updated["id"], f"{msg.author.display_name} 申請退回上一步（等待全體玩家同意）")
        return "↩️ 已記錄你的悔棋請求（目前同意 1/2）。因為需要全體參賽玩家同意，已貼出含「✅ 同意上一步」按鈕的卡片等待對手確認！"

    _queue_board_card_update(updated["id"], f"↩️ 全體玩家已同意，棋盤已退回上一步（第 {updated['move_count']} 手）！")
    return (
        f"↩️ 全體參賽玩家皆已同意，棋盤已安全退回上一步！\n"
        f"{boardgame_engine.get_board_status_summary_for_llm(msg.channel.id)}"
    )


def boardgame_resign() -> str:
    """讓當前發言玩家在目前進行中的棋盤桌遊認輸／投降或取消等待中挑戰，並更新結束棋盤卡。"""
    msg = current_discord_msg.get()
    if not msg:
        return "❌ 無法取得目前 Discord 頻道上下文。"
    active = boardgame_engine.get_active_game(msg.channel.id)
    if not active:
        return "ℹ️ 目前此頻道沒有進行中的對局。"
    try:
        updated = boardgame_engine.resign_game(active["id"], msg.author.id)
    except ValueError as e:
        return f"⚠️ 無法投降：{e}"
    _queue_board_card_update(updated["id"], f"{msg.author.display_name} 選擇投降。")
    return f"🏳️ 已為 {msg.author.display_name} 辦理認輸／結束對局，最新結果卡片將附在回覆下方。"


def boardgame_refresh_board() -> str:
    """重新將當前頻道進行中的圖形化棋盤卡片與互動按鈕貼到聊天室最下方。"""
    msg = current_discord_msg.get()
    if not msg:
        return "❌ 無法取得目前 Discord 頻道上下文。"
    active = boardgame_engine.get_active_game(msg.channel.id)
    if not active:
        return "🎲 目前此頻道沒有進行中或等待中的棋局，要開一局嗎？"
    _queue_board_card_update(active["id"], "已依請求重新顯示最新棋盤面板")
    return "✅ 已排程將最新棋盤卡片與按鈕重新貼在回覆下方。"


def boardgame_stats() -> str:
    """查詢當前發言玩家的桌遊勝敗和戰績紀錄。"""
    msg = current_discord_msg.get()
    if not msg:
        return "❌ 無法取得目前 Discord 頻道上下文。"
    rows = boardgame_engine.get_user_stats(msg.author.id, limit=15)
    if not rows:
        return f"🏆 {msg.author.display_name} 目前尚無已完成的桌遊戰績紀錄。"
    lines = [f"🏆 {msg.author.display_name} 的桌遊戰績："]
    for r in rows:
        meta = boardgame_engine.GAME_CATALOG.get(r["game_type"], {"short": r["game_type"]})
        lines.append(f"- {meta['short']} [{r['mode'].upper()}]：{r['wins']} 勝 / {r['losses']} 敗 / {r['draws']} 和")
    return "\n".join(lines)


def get_scoped_tools(
    enable_flashcards: bool = True,
    include_rpg: bool = False,
    enable_game_wiki: bool = True,
    include_boardgames: bool = False,
) -> list:
    """
    【小模型 Tool Scoping 動態工具瘦身】
    針對 gemini-flash-lite 等輕量模型，在非 TRPG 跑團情境下自動卸載 RPG 工具，
    並僅在啟用對應模組且處於相關情境時才掛載單字卡、《殭屍學園》知識庫與桌遊管理引擎工具。
    """
    tools = [
        record_memory,
        create_todo,
        finish_todo,
        query_todos,
        set_reminder,
    ]
    if enable_game_wiki:
        tools.insert(0, lookup_game_wiki)
    if include_boardgames:
        tools.extend([
            boardgame_get_state,
            boardgame_start,
            boardgame_play_move,
            boardgame_undo,
            boardgame_resign,
            boardgame_refresh_board,
            boardgame_stats,
        ])
    if include_rpg:
        tools.extend([
            rpg_engine.rpg_roll_check,
            rpg_engine.rpg_update_state,
            rpg_engine.rpg_record_lore,
            rpg_engine.rpg_batch_record_lore,
            rpg_engine.rpg_record_story_clue,
            rpg_engine.rpg_import_character_sheet,
        ])
    if enable_flashcards:
        tools.append(add_flashcard_card)
    return tools


# 工具函式清單建立函式，支援依模式切換 (向下相容保留完整清單)
def get_assistant_tools(
    enable_flashcards: bool = True,
    enable_game_wiki: bool = True,
    enable_board_games: bool = True,
) -> list:
    return get_scoped_tools(
        enable_flashcards=enable_flashcards,
        include_rpg=True,
        enable_game_wiki=enable_game_wiki,
        include_boardgames=enable_board_games,
    )


# 預設工具函式列表，供 google-genai SDK 註冊 (向下相容)
ASSISTANT_TOOLS = get_assistant_tools(enable_flashcards=True, enable_game_wiki=True)
