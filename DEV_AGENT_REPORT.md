# 🛠️ 專案重構、防幻覺與資安強化執行報告 (給 Development Agent 的交接通知)

> **致 Development Agent：**
> 本專案剛完成一次全面的資安審查、防幻覺修復與匯入功能重構。在你接手進行任何後續開發前，請務必詳讀本報告與 [`AGENTS.md`](./AGENTS.md)，切勿將已修復的防呆與安全機制改回舊寫法。

---

## 一、 本次已修復之「歷史開發 Agent 程式碼幻覺」與 Crash Bugs

1. **`bot.py` 漏匯入標準庫 (`json`, `random`) 導致 `NameError`**：
   - 先前在 `bot.py` 實作 `!card add`（AI JSON 解析）、`!card generate` 與 `!quiz`（`random.sample`）時，漏掉了頂層 `import json` 與 `import random`。現已在 `bot.py` 頂部補齊。
2. **`!card import` 回傳字典鍵名幻覺導致 `KeyError: 'success'`**：
   - 先前 `flashcard_engine.py` 的 `import_anki_apkg_bytes()` 與 `import_text_cards()` 僅回傳 `{"deck_name", "deck_id", "imported_count"}`，但 `bot.py` 卻讀取 `res["success"]` 與 `res["imported_cards"]`。
   - **已修正**：`flashcard_engine.py` 現統一回傳 `{"success": bool, "deck_name": str, "deck_id": int, "imported_count": int, "imported_cards": int, "error": Optional[str]}`，向下相容雙鍵名。
3. **環境變數 `.env` 載入與模組匯入時序顛倒**：
   - 先前 `bot.py` 先 `import assistant_db` 後才執行 `load_dotenv(dotenv_path=env_file)`，導致 `--profile=community` 在 import 階段抓到錯誤的預設值。
   - **已修正**：`bot.py` 現強制於最頂層完成命令列解析與 `load_dotenv()` 後，才匯入 `assistant_db`、`assistant_tools`、`rpg_engine`、`flashcard_engine`，並移除了易造成誤用的靜態 `DB_PATH` 常數。
4. **單字卡 AI 功能缺乏備援模型降級 (`FALLBACK_MODELS`)**：
   - **已修正**：在 `bot.py` 新增共用非同步函式 `generate_with_fallback(contents, config, timeout, context_label)`，將 `FlashcardStudyView.on_ai`、`!card add` 與 `!card generate` 全面納入 `[PRIMARY_MODEL] + FALLBACK_MODELS` 自動降級保底機制。

---

## 二、 本次已修復之「Bot 執行期 AI (Gemini) 幻覺與上下文污染」

1. **修正「系統 Embed 被誤判為 `[學員]` 發言」之上下文污染 (`bot.py: build_gemini_contents`)**：
   - **原因**：舊邏輯將 `msg.author == bot.user` 且「無文字、有 Embed」的所有訊息皆當作對向頻道的鏡像小卡並標記為 `role="user"`, `[學員]: ...`，導致 `!help`、`!card`、`!rpg`、`!todo` 等系統面板全部被當成使用者發言塞進 Gemini 歷史記憶。
   - **已修正**：現在嚴格檢查 `embed.author.name` 必須包含 `(來自綜合頻道)` 或 `(來自日文頻道)` 才會還原為 `user` 訊息，其餘系統 Embed 一律跳過不進入對話歷史。
2. **消除社群版 (`CommunityBot`) 的「情侶」詞彙人設幻覺 (`assistant_db.py`, `assistant_tools.py`)**：
   - **原因**：`assistant_db.format_memories_for_prompt()` 與 `assistant_tools.py` 的 Tool Docstrings 原本寫死「情侶長期記憶庫」、「兩人的共同待辦清單」，導致社群版 AI 讀取 Tool Schema 時誤以為群友是情侶。
   - **已修正**：Tool Docstrings 已改為中性描述；`format_memories_for_prompt()` 與 `!memory` Embed 標題現會根據 `ENABLE_JAPANESE_MODE` 自動切換為「情侶長期記憶庫」或「群組長期記憶與共識庫」。
3. **消除 RPG 同回合狀態更新的「新舊狀態衝突」幻覺 (`rpg_engine.py`)**：
   - **已修正**：在 `rpg_update_state` 與 `rpg_import_character_sheet` 的工具回傳字串末端，加上 `⚡【權威狀態覆蓋提示】`，明確指示模型以本次 Tool 結算的新數值覆蓋 System Prompt 中的舊快照。
4. **防止日語頻道呼叫工具後中文外洩至 HornBot (`system_prompt.txt`, `bot.py: send_synchronized_response`)**：
   - **已修正**：在 `system_prompt.txt` 強化規範「即使呼叫工具，最終回覆仍須嚴格遵守 `[JP]` 與 `[ZH]` 分段」；同時在 `send_synchronized_response` 加入防呆，若模型回覆未帶 `[ZH]` 且不含任何日文假名（純中文回報），自動轉為 Embed 顯示於主頻道，**絕不發送至 `TTS_CHANNEL_ID`**。

---

## 三、 本次已強化之「資安防護、權限隔離與 Discord 邊界保護」

1. **外部網址預覽 SSRF、大檔 OOM 與間接提示詞注入防護 (`bot.py: is_safe_public_url`, `fetch_url_preview`)**：
   - 新增 `is_safe_public_url(url)`：解析 DNS 並阻擋 `localhost`、Loopback (`127.0.0.0/8`)、私網 (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)、Link-Local (`169.254.0.0/16`) 等內部網路位址。
   - 改用 `client.stream("GET", url)` 串流讀取，檢查 `Content-Type` 並限制最多讀取 **256 KB**。
   - 將外部網頁摘要包覆於 `<untrusted_web_preview>` 標籤內並附帶系統安全聲明，防止外部網頁文字觸發 Indirect Prompt Injection 亂呼叫資料庫工具。
2. **上傳檔案大小限制與 Zip Bomb 防護 (`flashcard_engine.py`, `bot.py`)**：
   - 檢查 Discord 附件大小：圖片上限 10MB、文字設定檔上限 2MB、Anki `.apkg` 上限 25MB。
   - 解壓縮 `.apkg` 時檢查內部 SQLite 資料庫解壓後大小（上限 50MB），並確保 Cursor 與 Connection 於 `finally` 區塊安全關閉，避免 Windows 檔案鎖定。
3. **社群模式權限隔離 (`assistant_db.py`, `rpg_engine.py`, `bot.py`)**：
   - `!remind del`：改呼叫 `assistant_db.cancel_reminder(rid, user_id)`，確認提醒存在且在社群模式下僅允許建立者本人取消。
   - `!rpg delsave`：在社群模式下禁止 fallback 刪除其他成員建立的同名存檔。
   - `!lore clear`：在社群模式下限制需具備伺服器 `manage_messages` 權限方可清空全服設定集。
4. **Discord API 長度限制與 Mention 防護 (`bot.py`)**：
   - `commands.Bot` 已設定 `allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True)`，徹底杜絕 `@everyone` / `@here` 提及攻擊。
   - 新增 `truncate_embed_text()` 並套用至所有清單型指令（`!todo`, `!remind`, `!memory`, `!saves`, `!journal`, `!lore`, `!card decks`, `!party`），防止超過 Discord Embed 4096 / 1024 字元上限。
5. **SQLite 效能與外鍵強化 (`assistant_db.py`, `rpg_engine.py`, `flashcard_engine.py`)**：
   - `get_conn()` 現自動啟用 `PRAGMA foreign_keys = ON;` 與 `PRAGMA journal_mode = WAL;`。
   - `init_rpg_tables()` 與 `init_flashcard_tables()` 加入 `_initialized_dbs` 快取，每個資料庫實例僅在首次連線時執行一次建表與檢查，消除每則訊息重複執行數十次 DDL 的效能損耗。

---

## 四、 全面升級之「三合一 Import 匯入體驗」

為了解決使用者「不知道怎麼用 import、難道一定要在 Discord 上傳檔案」的痛點，`!card import`、`!lore import` 與 `!sheet` 現全面支援 **3 種匯入管道**：
1. **直接在 Discord 對話框貼上多行文字**（免準備檔案，最直覺）。
2. **本機 `data/imports/` 資料夾直接讀取**：使用者只要把 `.apkg`、`.txt`、`.csv`、`.md` 放進專案目錄下的 `data/imports/` 資料夾，在 Discord 輸入 `!card import 檔名.apkg`、`!lore import 檔名.txt` 或 `!sheet 檔名.txt` 即可直接匯入；單打 `!card import` 時更會自動列出 `data/imports/` 內偵測到的檔案。
3. **Discord 拖曳上傳檔案附件**：修復原本的 `KeyError` 後已可正常運作。

---

## 五、 2026-11-08 新增：《殭屍學園》Contextual-LightRAG 知識庫與輕量模型 (`gemini-flash-lite`) 強化架構

為了解決 `gemini-flash-lite-latest` 在面對《殭屍學園 (좀비고등학교)》韓服房間列表截圖（如 `ㄷㅋㅈ`、`34퀸`、`11렙 오더 클모`、`prace 조건x`）及遊戲設定時，「看圖忘記呼叫工具」與「過多工具造成注意力稀釋」的問題，已完成以下架構升級（請勿回退或覆蓋）：

1. **新增 [`game_wiki_engine.py`](./game_wiki_engine.py)（Contextual-LightRAG + 動態熱重載檢索器）**：
   - 自動載入 `data/game_wiki/*.md`（`slang.md`、`characters.md`、`story.md`、`modes.md`、`au_events.md` 共 91+ 筆條目），並依檔案 `mtime` 自動熱重載。
   - 支援 **Contextual Retrieval 全知脈絡標頭**、韓文初聲縮寫（`ㄷㅋㅈ`）與房名動態正則解析（`N렙`、`듀옹`、`prace`、`조건x`）。
   - 支援 **LightRAG 1-Hop 實體關聯圖譜展開 (`related_ids`)**，且針對長篇設定嚴格限制 `Top-K <= 3`（防止小模型分心效應 Distraction Effect），同時完整保留所有命中的韓服開房黑話。
2. **新增「兩階段視覺微流水線 (2-Pass Vision Pipeline)」(`game_wiki_engine.extract_image_text_pass1` + `bot.py`)**：
   - 當訊息包含圖片附件時，`handle_general_chat` 與 `handle_japanese_learning_chat` 會先執行無工具、無人設、`temperature=0.0` 的 **Pass-1 純視覺 OCR 預提取**，將圖中韓文與縮寫抄出，再由 Python 自動查出 `data/game_wiki/` 標準答案注入 **Pass-2 最終回覆**。
3. **新增 CRAG (Corrective RAG) 防幻覺煞車與「先列事實、後套人設」約束**：
   - 在 System Prompt 動態注入區塊與 `general_prompt_community.txt` 中，強制要求模型先條列字典與設定事實，再套用群組微毒舌人設收尾；遇到未收錄的韓文縮寫嚴禁以英文諧音（如 Clan Mode）瞎掰。
4. **新增動態工具瘦身 (`assistant_tools.get_scoped_tools`) 與 `!wiki` 快捷指令**：
   - 在非 TRPG 冒險情境下，自動卸載 6 個 RPG 專用工具，僅保留常態核心工具（含新增的 `lookup_game_wiki`），大幅提升 `flash-lite` 的工具遵循率。
   - 在 `bot.py` 新增 `!wiki [關鍵字]`（別名：`!圖鑑`、`!黑話`、`!좀고`）指令，可隨時檢視知識庫統計或直接查詢條目。

---

## 六、 後續開發注意事項（含最高資安警告）

1. 🚨 **嚴禁讀取真實 `.env` 或 `.env.community`**：請絕對不要在終端機執行 `Get-Content .env.community` 或讀取真實 `.env` 檔案，以免將真實 API Key 與 Discord Token 印在對話紀錄中！檢查設定鍵名請一律讀取 `.env.example` 或 `.env.community.example`。
2. 請在後續每次修改程式碼前閱讀 [`AGENTS.md`](./AGENTS.md)，並於修改後執行：
```powershell
.\.venv\Scripts\python.exe -m py_compile bot.py assistant_db.py assistant_tools.py rpg_engine.py flashcard_engine.py video_engine.py game_wiki_engine.py
```
確認所有模組編譯通過，並同步維護 `README.md`、`COMMANDS.md` 與 `RPG_GUIDE.md`。
