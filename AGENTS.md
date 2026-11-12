# 🤖 DiscordGeminiBot 開發 Agent 行為準則與防幻覺規範 (AGENTS.md)

本文件為 **DiscordGeminiBot** 專案的最高開發指導原則。任何參與本專案開發、重構、除錯或擴充功能的 AI Coding Agent，在動手修改任何程式碼之前，**必須完整閱讀並嚴格遵守本規範**。

---

## 🚨 一、 防範開發幻覺鐵律 (Anti-Hallucination Rules for Coding Agents)

### 1. 嚴禁猜測函式簽章與回傳結構 (No Signature / Return-Key Guessing)
- **修改或呼叫跨模組函式前，必須先 `read_file` 確認目標函式的真實定義與 `return` 字典鍵名。**
- ❌ **反面案例（歷史真實 Bug）**：在 `bot.py` 呼叫 `flashcard_engine.import_anki_apkg_bytes()` 時，未查閱實作便幻覺回傳值含有 `res["success"]` 與 `res["imported_cards"]`，而實際回傳為 `{"deck_name": ..., "deck_id": ..., "imported_count": ...}`，導致執行期 `KeyError`。
- ✅ **強制規範**：凡涉及 `assistant_db.py`、`assistant_tools.py`、`rpg_engine.py`、`flashcard_engine.py` 與 `bot.py` 之間的資料傳遞，必須雙向核對參數名稱、型別與回傳 Dict keys。

### 2. 嚴禁幻覺外部 SDK 語法與模型名稱 (Google GenAI & Discord.py)
- **Google GenAI SDK**：本專案使用新版 **`google-genai`** (`from google import genai`, `from google.genai import types`)，**嚴禁**混用已棄用的舊版 `google-generativeai` (`genai.configure`, `GenerativeModel`) 語法。
- **非同步呼叫規範**：在 Discord 事件迴圈 (`async def`) 中呼叫 Gemini，必須使用非同步介面 `await gemini_client.aio.models.generate_content(...)`，並具備 timeout 與 `FALLBACK_MODELS` 降級保護，**嚴禁**在 async 函式中使用同步阻塞呼叫。
- **模型名稱驗證**：不得擅自編造未經驗證的 Gemini 模型名稱；所有 AI 呼叫點（包含主對話、單字卡家教解說、AI 自動造卡）皆應共用統一的 fallback 容錯呼叫封裝。

### 3. 資料庫連線與環境變數載入時序 (Initialization Order)
- 本專案支援多實例隔離（透過 `--profile=community` 或 `--env=...` 載入不同 `.env` 與 `DB_NAME`）。
- **嚴禁**在模組頂層（Import 階段）將環境變數或資料庫路徑快取為固定常數（例如舊的 `DB_PATH = get_db_path()` 在 `load_dotenv()` 前就求值會抓到錯誤預設值）。所有依賴環境變數的設定必須在 `load_dotenv()` 執行後讀取，或透過動態函式取得。
- SQLite 建表 (`init_*_tables()`) 應在啟動時執行一次，**禁止**在每個高頻率查詢函式內重複執行 `CREATE TABLE` 或 `ALTER TABLE`。

### 4. 修改後強制驗證 (Mandatory Post-Edit Verification)
- 每次修改完 Python 檔案後，Agent **必須**執行語法與匯入檢查（例如 `.venv\Scripts\python.exe -m py_compile bot.py assistant_db.py assistant_tools.py rpg_engine.py flashcard_engine.py`），確認零語法錯誤後才算完成。

---

## 🧠 二、 防範機器人執行期幻覺規範 (Preventing Runtime LLM Hallucinations)

開發者在設計 Prompt 與 Function Calling 工具時，必須確保底層架構不會誘導 Gemini 產生敘事或人設幻覺：

### 1. 權威狀態單一真實來源 (Authoritative State in SQLite)
- **AI 不做算術與隨機數**：擲骰檢定 (`rpg_roll_check`)、HP 增減、背包物品變更 (`rpg_update_state`) 必須 100% 由本地 Python 與 SQLite 計算。
- **避免同回合新舊狀態衝突**：當模型在單一回合呼叫工具更新狀態（如扣血、獲得道具、匯入角色卡）時，工具回傳字串必須明確標示「⚡【最新權威狀態已更新，請以此覆蓋 System Prompt 中的舊狀態】」，防止模型被 System Instruction 裡未更新的舊快照誤導。

### 2. 雙實例人設與工具隔離 (Couple Mode vs. Community Mode)
- 本專案同時服務兩種截然不同的場景：
  1. **情侶私密共學管家 (`.env`)**：啟用日語雙頻道 (`ENABLE_JAPANESE_MODE=true`)、單字卡 (`ENABLE_FLASHCARD_MODE=true`)，語氣為情侶摯友與學伴。
  2. **社群公開跑團與群聊 (`.env.community`)**：關閉日語與單字卡，語氣為社群好友與 TRPG 地下城主 (DM)。
- **嚴禁硬編碼特定身分詞彙**：在共用的 `assistant_db.py`、`assistant_tools.py`（包含 Tool Docstrings！）與 `rpg_engine.py` 中，**不得將「情侶」寫死在共用提示詞或工具描述中**（應根據環境變數或使用中性詞彙如「長期記憶庫」、「共同待辦清單」），否則社群版 AI 會在讀取 Tool Schema 時產生「你們是情侶」的嚴重幻覺。

### 3. 工具掛載與 System Prompt 一致性 (Tool-Prompt Alignment)
- 凡是掛載進 `GenerateContentConfig(tools=...)` 的工具，其使用時機與限制必須在該頻道對應的 System Prompt 中有明確定義；該頻道不應使用的工具（例如在純日語發音練習頻道中不需使用的工具，或停用 Flashcard 時的 `add_flashcard_card`），應從 `tools` 清單中移除，避免模型亂呼叫做白工。
- **HornBot 語音頻道防護 (`TTS_CHANNEL_ID`)**：在日語雙頻道模式下，必須確保只有純日語區段 (`[JP]`) 會送往 `TTS_CHANNEL_ID`。若模型因呼叫工具而未輸出 `[ZH]` 分隔標籤，必須有防呆機制避免將整段中文工具回報直接丟進 TTS 頻道朗讀。

### 4. 歷史上下文建構過濾 (Context History Hygiene)
- 在 `build_gemini_contents` 與 `build_general_contents` 抓取 Discord 歷史訊息時，**必須嚴格區分「雙頻道鏡像轉發小卡」與「Bot 系統指令回覆 Embed（如 `!help`、`!rpg`、`!card`）」**。
- 只有帶有鏡像標記（如 `embed.author.name` 包含 `(來自綜合頻道)` 或 `(來自日文頻道)`）的 Embed 才可還原為 `role="user"` 對話，嚴禁將系統狀態看板 Embed 誤當成使用者發言塞進歷史上下文。

---

## 🔒 三、 資安與 Discord 邊界防護規範 (Security & Boundary Rules)

1. **外部網址抓取防護 (SSRF & DoS Prevention)**：
   - `fetch_url_preview` 必須驗證 URL scheme 僅限 `http`/`https`，並解析 hostname 阻擋內網/私網與本機位址（`localhost`, `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `::1`）。
   - 必須限制下載回應大小（例如最多讀取前 256KB）與檢查 `Content-Type`（僅處理 `text/*`、`application/json`、`application/xhtml+xml`），防止大檔塞爆記憶體。
   - 外部網頁文字注入 Prompt 時，必須加上明確的不可信資料邊界標籤（如 `<untrusted_web_content>...</untrusted_web_content>`），聲明其中的文字不可作為系統指令或觸發工具調用（防範 Indirect Prompt Injection）。
2. **上傳檔案安全限制**：
   - 處理 `.apkg`、`.txt`、`.md`、圖片附件時，必須先檢查 `attachment.size`（例如文字檔上限 2MB、`.apkg` 上限 20MB），解壓縮 `.apkg` 時須檢查壓縮檔內成員大小與路徑，防止 Zip Bomb 與 Path Traversal。
3. **Discord API 限制與 Mention 安全**：
   - 一般訊息必須經過 `send_split_messages`（單則 <= 1900 字元）。
   - Discord `Embed.description` 嚴格限制 <= 4096 字元、單一 `Embed.add_field` 的 `value` <= 1024 字元、每個 Embed 最多 25 個 fields。所有列表型指令（`!todo`, `!memory`, `!lore`, `!journal`, `!saves`, `!party`）都必須做長度截斷或分頁保護。
   - 機器人發送訊息應預設關閉 `@everyone` 與 `@here` 權限（設定 `AllowedMentions(everyone=False, roles=False)`），防止被誘導群體標註。
4. **敏感資訊隔離（最高資安鐵律）**：
   - `.env`、`.env.*`（除 `.example` 外）與 `data/*.db` 嚴禁加入 Git 版本控制。
   - **嚴禁在任何終端機指令（如 `Get-Content`、`cat`、`type`）或檔案讀取工具中開啟或印出真實的 `.env` 與 `.env.community` 內容！** 若需檢查環境變數鍵名，**只能讀取 `.env.example` 或 `.env.community.example`**，避免將真實 API Key 與 Bot Token 洩漏至系統日誌。

---

## ⚡ 三之二、 輕量模型 (`gemini-flash-lite`) 強化架構規範 (`game_wiki_engine.py`)

為確保在使用免費/高頻額度的輕量模型（如 `gemini-flash-lite-latest`）時仍具備高準確度與零幻覺表現，必須維持以下四大機制：

1. **Contextual-LightRAG 自動預檢索 (`game_wiki_engine.build_auto_rag_context`)**：
   - 本地知識庫位於 `data/game_wiki/*.md`（`slang.md`、`characters.md`、`story.md`、`modes.md`、`au_events.md`），支援依檔案 `mtime` 自動熱重載。
   - 每個條目皆包含全知脈絡標頭（Contextual Header）、雙語與初聲縮寫 `keywords`、以及 1-Hop 實體關聯 `related_ids`（LightRAG）。
   - 文字或圖片訊息進入時，由 Python 先行精準檢索並自動注入 System Prompt（韓服黑話最多 8~10 筆、長篇設定嚴格限制 `Top-K <= 3` 以避免小模型分心效應 Distraction Effect）。
2. **兩階段視覺微流水線 (`game_wiki_engine.extract_image_text_pass1`)**：
   - 當訊息包含圖片附件時，先執行無工具、無人設、`temperature=0.0` 的 **Pass-1 純視覺 OCR 預提取**，將圖上的韓文、初聲縮寫（如 `ㄷㅋㅈ`、`클모`、`34퀸`）與數字抄錄下來，再交由 Python 查表注入 **Pass-2 最終回覆**，解決小模型「邊看圖邊忘記查工具」的硬傷。
3. **CRAG (Corrective RAG) 防幻覺煞車**：
   - 當偵測到《殭屍學園》或韓文專有名詞提問，強制要求模型遵循「**先列事實、後套人設**」的輸出結構；若遇到知識庫未收錄之縮寫，嚴禁以英文諧音（如把 `클모` 猜成 Clan Mode）自行腦補。
4. **動態工具瘦身 (`assistant_tools.get_scoped_tools`)**：
   - 在非 TRPG 冒險情境（`is_rpg_context == False`）下，自動卸載 6 個 RPG 專用工具，僅保留常態核心工具（含 `lookup_game_wiki`），避免過多 Tool Schema 稀釋小模型注意力。

---

## 📚 四、 強制文件同步維護規範 (Mandatory Documentation Maintenance)

當 Agent 新增、修改或刪除任何功能時，**禁止只改程式碼而不改文件**。在完成任務前，必須依據下表同步更新對應文件：

| 變更類型 | 必須同步更新的文件清單 |
| :--- | :--- |
| **新增 / 修改 / 刪除 `!` 指令或別名** | 1. `README.md`（指令大全表格）<br>2. `COMMANDS.md`（全功能無表格指令手冊，注意總字數須保持 < 2000 字以便貼入 Discord）<br>3. `bot.py` 中的 `help_command` (`!help` Embed 面板，含情侶版與社群版分支) |
| **新增 / 修改 RPG 機制、角色卡欄位、存讀檔或擲骰規則** | 1. `RPG_GUIDE.md`（RPG 專屬指南與速查表）<br>2. `COMMANDS.md` & `README.md`<br>3. `general_prompt.txt` & `general_prompt_community.txt` |
| **新增 / 修改 Gemini Function Calling 工具 (`assistant_tools.py` / `rpg_engine.py`)** | 1. 對應的函數 Docstring（Gemini 直接讀取 Docstring 作為 Tool Schema）<br>2. `general_prompt.txt`、`general_prompt_community.txt`、`system_prompt.txt`<br>3. `assistant_tools.py` 的 `get_assistant_tools()` 註冊清單 |
| **新增 / 修改環境變數 (`.env` 開關或設定)** | 1. `.env.example`（情侶/預設版範本）<br>2. `.env.community.example`（社群版範本）<br>3. `README.md` |
| **新增外部 Python 套件 `import`** | 1. `requirements.txt`（必須明確列出所有直接 import 的第三方套件與版本下限） |

---

## ✅ 五、 Agent 每次提交變更前的自我檢查清單 (Pre-Completion Checklist)

在向使用者回報「已完成」之前，請逐一確認：
- [ ] **無幻覺檢查**：所有跨檔案呼叫的函式名稱、參數、回傳 Dict keys 是否皆已透過 `read_file` 實地核對？
- [ ] **語法編譯檢查**：是否已執行 `py_compile` 確認所有 `.py` 檔案無語法與縮排錯誤？
- [ ] **雙模式相容檢查**：修改的功能在 `ENABLE_JAPANESE_MODE=true`（情侶版）與 `ENABLE_JAPANESE_MODE=false`（社群版）下是否都能正常運作且無詞彙/人設污染？
- [ ] **邊界與資安檢查**：Embed 字數是否有限制？外部輸入/檔案大小是否有防護？
- [ ] **文件同步檢查**：`README.md`、`COMMANDS.md`、`RPG_GUIDE.md`、`!help` 與 `.env*.example` 是否已同步更新至最新狀態？
