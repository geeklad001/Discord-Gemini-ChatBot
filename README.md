# 🌸 Discord 日語共學 & 生活助理機器人 (Gemini 3.8 Flash)

Discord 專屬管家與生活日語學伴機器人。連接 Google AI Studio 最新旗艦 **Gemini 3.8 Flash**（搭配 **Thinking: Medium** 深度思考）與 Discord，具備**日語學習雙頻道音字分離**、**日常三人閒聊**、**情侶長期記憶**、**共同待辦清單**與**背景定時提醒**！

---

## 🌟 核心特色功能

### 1. 📻 日語共學雙頻道（完美整合 HornBot 語音朗讀）
* **頻道 1（`TARGET_CHANNEL_ID`）**：綜合學習頻道，AI 發送純日語正文，下方搭配精美粉色卡片（繁體中文翻譯、重點單字讀音）。
* **頻道 2（`TTS_CHANNEL_ID`）**：HornBot 朗讀專用日文頻道，AI **僅發送純日語**（無中文小卡、無雜音），供 HornBot 專注發音朗讀。
* **雙向小卡鏡像同步**：雙方在任一頻道打字，對向頻道均以 Discord Embed 小卡同步發言（HornBot 絕不朗讀 Embed，既不干擾語音，又能保留完整上下脈絡）。

### 2. 💬 多模態日常閒聊與影音分析（`GENERAL_CHANNEL_ID`）
* 純當朋友放鬆閒聊，支援看圖（Vision）、**短影片理解（.mp4/.mov/.webm 本機上傳）**、**YouTube 影片與 Shorts 串流分析**（具備時長秒數與檔案容量安全過濾）與網址自動摘要。
* 說話自然接地氣、不油腔滑調、不刻意教日文。

### 3. 🧠 情侶長期記憶庫（永久保存）
* 跨越伺服器重啟與 `!reset` 指令。
* 隨時在聊天中記住對方的喜好、過敏原、習慣或重要紀念日（如「我女友喜歡吃抹茶」、「紀念日是 10/15」）。
* AI 每次回答時會自動融入這些記憶，貼心照應。

### 4. 📋 共同待辦清單（To-Do List）
* **自然口語操作**：「幫我把買牛奶加進待辦」、「牛奶買好了」。
* **快捷指令看板**：`!todo`、`!todo done 1`。

### 5. ⏰ 背景定時提醒（Timers & Reminders）
* 時間一到，機器人在 Discord 頻道中直接 **`@提及`** 設定者並推播醒目標籤！
* **相對時間**：「10分鐘後提醒我關火」、「半小時後叫我出門」。
* **絕對時間**：「今天 18:30 提醒倒垃圾」、「明天 15:00 提醒搶票」。
* 後台背景非同步每 10 秒檢查一次，保證準時。

### 6. 🗡️ TRPG 跑團與文字冒險引擎（輕量化本機狀態機）
* **本機權威狀態（Authoritative State）**：SQLite 資料庫嚴格管理血量（HP）、背包道具、當前任務與地點，AI 絕不幻覺、不憑空捏造物品。
* **全宇宙自適應角色卡（`!sheet`）**：支援修仙、賽博龐克、克蘇魯 CoC、D&D 與任意自由劇本，支援多人組隊面板（`!party`）、全隊存讀檔（`!save` / `!load`）與旅程獨立手記（`!note` / `!journal`）。
* **D20 擲骰與難度檢定（Dice Checks）**：行動具備真實隨機性，支援大成功與大失敗，劇情充滿張力與意外！
* **動態世界觀設定集（Lorebook）**：按需關鍵字注入，零額外 Token 浪費。

### 7. 🌸 日語單字卡與 Kotoba 多人搶答測驗（`!card` / `!quiz`）
* **互動式 SRS 單字卡（`!card`）**：支援按鈕翻面、母語者真人原音/HornBot 朗讀、圖卡顯示、AI 家教解說、Leitner 箱體記憶熟練度與一鍵切換牌組。
* **三合一超直覺匯入（`!card import`）**：支援直接在聊天室貼上多行單字文字、從本機 `data/imports/` 直接讀取大型 Anki 牌組（支援 N1~N5 子牌組與高低頻篩選匯入、保留繁體釋義、重點粗體高亮與真人音訊）、或在 Discord 拖曳上傳檔案匯入。
* **Kotoba 限時搶答（`!quiz`）**：多人拼刀限時搶答假名讀音或中文意思，附即時排行榜結算。

### 8. 🌐 單一程式碼庫 × 功能開關與多實例隔離
本專案透過模組化套件（`core/`、`cogs/`、`engines/`、`prompts/`、`scripts/`）與 `.env` 功能開關，一套程式碼即可切換三種開箱模式並完全隔離資料庫：
* **新手自架／好友分享版（`.env.starter.example` + `prompts/general_prompt_starter.txt`）**：搭配 `scripts/1_設定金鑰_Setup_Env.bat` ➔ `scripts/2_一鍵安裝_Install.bat` ➔ `scripts/3_一鍵啟動_Start_Bot.bat` 三步驟快速開張！
* **社群跑團與群聊版（`scripts/start_community_bot.bat` / `.env.community.example`）**：關閉日語與單字卡（`ENABLE_JAPANESE_MODE=false`、`ENABLE_FLASHCARD_MODE=false`），可選擇開啟遊戲知識庫（`ENABLE_GAME_WIKI`）與防注入護欄（`ENABLE_SAFETY_GUARDRAIL`），獨立連結 `data/community.db`。
* **雙語共學管家版（`scripts/start_bot.bat` / `.env.example`）**：開啟日語雙頻道（`ENABLE_JAPANESE_MODE=true`）與 Anki 單字卡（`ENABLE_FLASHCARD_MODE=true`），獨立連結 `data/assistant.db`。
* 常用彈性開關：`ENABLE_GAME_WIKI`、`ENABLE_BOARD_GAMES`、`ENABLE_SAFETY_GUARDRAIL`（本地安全/防注入護欄）、`DISABLE_SAFETY_SETTINGS`（暢聊放鬆 Gemini 安全過濾）、`FALLBACK_MODELS`（自訂備援模型順序）。

---

## 📖 指令大全

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `!help` | 依當前實例啟用的模組動態顯示指令速查表 | `!help` |
| `!reload` | 免重啟即時熱重載人設 Prompt 與知識庫 | `!reload` |
| `!todo` | 查看目前尚未完成的待辦事項 | `!todo` |
| `!todo add <內容>` | 新增待辦事項 | `!todo add 訂週六燒肉` |
| `!todo done <編號/關鍵字>` | 標記待辦為已完成 | `!todo done 1` 或 `!todo done 燒肉` |
| `!todo del <編號>` | 刪除待辦事項 | `!todo del 1` |
| `!todo all` | 查看歷史待辦紀錄 | `!todo all` |
| `!remind <時間> <內容>` | 設定定時提醒 | `!remind 10m 關火`、`!remind 18:30 倒垃圾` |
| `!reminds` | 查看所有尚未觸發的預約提醒 | `!reminds` |
| `!remind del <編號>` | 取消某個預約提醒 | `!remind del 1` |
| `!rpg start [主題]` | 自動建立專屬獨立討論串開啟冒險 | `!rpg start 迷霧森林遺跡` |
| `!rpg join` / `!rpg leave` | 主動加入或單獨退出冒險隊伍（也可 `!party join` / `leave`、`kick`） | `!rpg join`、`!rpg leave` |
| `!rpg` / `!party` | 查看個人冒險狀態卡 / 冒險小隊全員面板 | `!rpg`、`!party` |
| `!sheet [文字/檔名/附檔]` | 查看或匯入自適應角色卡 | `!sheet`、`!sheet char.txt` |
| `!save <名>` / `!load <名>` | 保存或載入全隊冒險快照與手記 | `!save 第一章`、`!load 第一章` |
| `!saves` / `!rpg delsave <名>` | 查看或刪除冒險存檔 | `!saves` |
| `!note <線索>` / `!journal` | 記錄或查看當前冒險旅程手記 | `!note 守衛怕老鼠`、`!journal` |
| `!rpg reset` | 重置冒險狀態，回到初始存檔 | `!rpg reset` |
| `!roll [表達式]` | TRPG 擲骰子 | `!roll`、`!roll 2d6`、`!roll 1d20+3` |
| `!lore` / `!lore import` | 查看或批次匯入世界設定集 (Lorebook) | `!lore`、`!lore import` |
| `!lore add <詞> \| <內容>` | 新增單筆世界設定 | `!lore add 星露草 \| 深山發光解毒藥草` |
| `!lore del <編號>` | 刪除世界設定 | `!lore del 1` |
| `!wiki [關鍵字]` | 互動分頁查詢《殭屍學園》角色、模式與韓服黑話（支援 `目錄`/`browse`、`add`/`import`/`replace`/`list`/`del`） | `!wiki 阿修羅`、`!wiki 燒瓶 2技`、`!wiki 目錄` |
| `!card [牌組名]` / `!decks` | 開啟互動式單字卡學習 / 查看所有牌組 | `!card`、`!decks` |
| `!card add <單字>` | 新增單字（僅打單字時由 AI 自動補齊讀音與例句） | `!card add 桜` |
| `!card import [檔名/等級/all]` | 貼上文字、讀取 `data/imports/`（支援一鍵全分類 `all` 或指定 `N3高頻`） | `!card import all`、`!card import N3高頻` |
| `!card generate <主題> [數]` | 由 AI 量身打造主題單字牌組 | `!card generate 居酒屋菜單 8` |
| `!quiz [牌組名] [題數]` | Kotoba 風格多人限時搶答測驗 | `!quiz N5 5` |
| `!play` / `!game` | 開啟遊戲大廳或開局（黑白棋、西洋棋、四子棋、五子棋 ＆ 🌍 **80億分之1** 197國寫實人生模擬；支援 PvP、本地 AI、🧠 LLM ＆ `!play 圖鑑`） | `!play`、`!play chess llm`、`!play 1in8b llm` |
| `!m <棋步>` | （輔助捷徑）落子座標；遊戲中皆可用卡片按鈕與口語下棋、悔棋、做人生抉擇與查戰績/圖鑑 | `!m d3`、`!m e2e4` |
| `!memory` | 查看所有保存的長期記憶清單 | `!memory` |
| `!remember <事實>` | 直接寫入長期記憶庫 | `!remember 喜歡抹茶拿鐵` |
| `!forget <編號>` | 刪除某一條長期記憶 | `!forget 1` |
| `!reset` | 封存當前頻道的歷史對話（持久化於 SQLite），開啟新話題 | `!reset` |

---

## 🚀 快速啟動

* **第一次新手自架（一鍵引導）**：依序雙擊 `scripts/` 內的 **`1_設定金鑰_Setup_Env.bat`** ➔ **`2_一鍵安裝_Install.bat`** ➔ **`3_一鍵啟動_Start_Bot.bat`**（詳見 [新手架設指南](docs/COMMUNITY_SETUP_GUIDE.md)｜[指令簡表](docs/COMMANDS.md)｜[TRPG 指南](docs/RPG_GUIDE.md)）
* **已有 `.venv` 的雙實例進階啟動**：
  * 主實例：雙擊 **`scripts/start_bot.bat`**（載入 `.env`）
  * 社群實例：雙擊 **`scripts/start_community_bot.bat`**（載入 `.env.community`）
