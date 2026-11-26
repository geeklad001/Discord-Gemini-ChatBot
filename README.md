# 🤖 Discord Gemini 全能群友・跑團・語音與共學機器人 (Gemini Flash-Lite)

專為 Discord 好友群、社群跑團與日語共學打造的免費自架 AI 機器人！連接 Google AI Studio 的 **Gemini Flash-Lite（免費額度友善＋多代 Flash-Lite 自動輪替保底）**，免信用卡、不用寫程式，就能一次擁有**多模態聊天、語音頻道朗讀、互動桌遊與 80 億分之 1 人生模擬、TRPG 跑團、日語雙頻道與單字卡、遊戲黑話圖鑑，以及長期記憶與定時提醒**！

> 📖 **相關文件**：[🍼 零基礎小白保母級架設教學](docs/COMMUNITY_SETUP_GUIDE.md) ｜ [📋 Discord 指令簡表 (< 2000 字)](docs/COMMANDS.md) ｜ [🗡️ TRPG 跑團指南](docs/RPG_GUIDE.md) ｜ [📝 更新日誌 (v2.2.0)](CHANGELOG.md)

---

## 🍼 完全不會寫程式？3 步驟快速啟動（全程點滑鼠，不用打指令）

就算從沒碰過程式碼或指令列也沒關係！我們已經把安裝與啟動都做成點兩下就會跑的小工具：

### ✅ 事前準備（4 樣免費東西）
1. 一台 **Windows 電腦**（機器人在線上時電腦需保持開機連網）。
2. 安裝好 **Python 3.11+**：到 [Python 官網](https://www.python.org/downloads/) 下載安裝，**🔥 安裝第一頁最下方務必勾選 `Add python.exe to PATH`**！
3. 一個 **Discord 帳號**（到 [Discord Developer Portal](https://discord.com/developers/applications) 建立 Bot 並開啟 **Message Content Intent**）。
4. 一個 **Google 帳號**（到 [Google AI Studio](https://aistudio.google.com/) 免費拿一組 Gemini API Key，**完全免綁信用卡**）。

### 🖱️ 點 3 次滑鼠就開張（檔案都在 `scripts/` 資料夾內）
1. **雙擊 `scripts/1_設定金鑰_Setup_Env.bat`**
   * 會自動建立設定檔並用**記事本**打開 `.env`，貼入 3 個設定（`GEMINI_API_KEY`、`DISCORD_BOT_TOKEN`、`TARGET_CHANNEL_ID`），順手也可在 `BOT_OWNER_IDS=` 貼上自己的 Discord 使用者 ID，按 `Ctrl + S` 存檔關閉。
2. **雙擊 `scripts/2_一鍵安裝_Install.bat`**
   * 黑色視窗會自動建立獨立環境並裝好所有必要套件（只需執行這一次，約 1～3 分鐘）。
3. **雙擊 `scripts/3_一鍵啟動_Start_Bot.bat`**
   * 看到 `登入成功！`、Discord 裡機器人頭像亮起綠燈，就能直接在頻道打字聊天、輸入 `!help` 或 `!play` 開玩囉！

> 👉 **第一次架設？卡關不知去哪找金鑰或頻道 ID？**
> 請直接跟著超詳細的零術語圖文步驟走：**[docs/COMMUNITY_SETUP_GUIDE.md（零基礎小白保母級架設教學）](docs/COMMUNITY_SETUP_GUIDE.md)**（內含語音權限、選配 FFmpeg 安裝與常見排錯 FAQ）。

---

## 🌟 核心特色功能

### 1. 💬 AI 聊天與多模態看圖／貼圖／語音／短影片／YouTube 3 分鐘短片分析
* **自然口語聊天**：支援看圖片、Discord 貼圖、GIF 動圖、網址自動摘要，還能直接分析 **Discord 上傳短影片（.mp4/.mov/.webm，上限 20 MB）** 與 **YouTube 影片／Shorts（上限 3 分鐘）**；日語模式下還可直接傳 **Discord 原生語音訊息（30 秒／3 MB 內）** 獲得發音微調回饋！
* **Flash-Lite 優先 ＋ Flash / Hydrogen 高峰備援輪替**：預設以免費額度最充沛的 Flash-Lite 輕量系列優先（`gemini-flash-lite-latest` ➔ `3.5/3.1/2.5-flash-lite`），尖峰限流（429）時自動接續備援標準 Flash（`gemini-flash-latest`、`gemini-3.8-flash`、`gemini-2.5-flash`）與 **`gemini-4-hydrogen`**，最後仍回落 `FINAL_FALLBACK_MODEL` 保底重試；若只需純輕量模型，也可於 `!setup` 一鍵切換回「純 Flash-Lite 鏈」。

### 2. 🔊 語音頻道原生朗讀（Edge-TTS 免費免顯卡 16 聲線＋選配本地 F5-TTS 與語音克隆）
* **無須外掛 HornBot，零顯卡免費開聲**：只要輸入 `!join`，機器人就會進入你所在的語音頻道，即時朗讀語音文字區的打字代說，也能自動朗讀日語教學或 AI 回覆！
* **16 種免費聲線自由換**：一般成員可用 `!voice` 下拉挑選台灣／中國／日本 16 種免費 Edge-TTS 聲線並微調語速、音高，也可用 `!voice bot [jp]`（或 `!botvoice`）設定機器人自己說話的聲音。
* **三層權限控管＋選配本地 F5-TTS 快取直讀／常駐選項**：伺服器管理員可用 `!tts channel` / `!tts bind` 指定代說頻道；主機擁有者可選配本地 **F5-TTS**（優先直讀本機 Hugging Face 快取免重複連網檢查，並可透過 `!tts preload`、`!tts keepalive on/off` 或 `!setup` 依顯卡 VRAM 餘裕切換「啟動預載常駐」或「閒置自動卸載」），也能開啟 `ENABLE_USER_VOICE_CLONE` 讓成員以 `!voiceclone` 建立專屬語音克隆。
* 💡 *不需要語音朗讀時完全不必裝 FFmpeg，將 `ENABLE_VOICE_TTS=false`（或進 `!setup` 點一下關閉）即可純文字使用。*

### 3. 🎮 遊戲大廳（`!play` / `!life`：4 款經典桌遊 ＋ 🌍 80億分之1 深度版）
* **一鍵開啟互動大廳（`!play` / `!game`）**：內建 **黑白棋、西洋棋、四子棋、五子棋**，支援**玩家對戰（PvP）**、**本地 AI** 與 **🧠 Gemini LLM 對戰**，支援圖片棋盤即時渲染、按鈕操控、悔棋、戰績統計與口語直接報步下棋。
* **🌍 80億分之1（`!life` / `!play 1in8b`）**：以真實世界人口機率隨機投胎到全球 **197 個國家**，含 **城鄉機率、先天特質、78+ 條件＆歷史大事件、機率檢定擲骰、五種副行動與跨國移居解鎖第 2 國**，支援 **⚡ 0-Token 深度本地版（預設） / 🎭 精華混合版 / 🧠 全程 AI 劇情版**、軌跡折線紀念卡與伺服器排行榜（`!life top`）！

### 4. 🗡️ TRPG 跑團與多人組隊（`!rpg` / `!sheet` / `!save` / `!load`）
* **SQLite 本地權威狀態 ＋ 互動一鍵檢定按鈕**：血量（HP）、背包道具、所在位置與任務全部由本地資料庫嚴格計算；遭遇關鍵檢定時 DM 會自動貼上 **`🎲 擲骰檢定` 互動按鈕**，點擊即自動加值擲骰並喚醒 DM 續寫劇情！
* **全宇宙自適應視覺角色卡（`!sheet` / `!party`）**：修仙、賽博龐克、克蘇魯 CoC、D&D 都能直接貼上或上傳 `.txt`/`.md` 匯入，系統依世界觀自動換色繪製 **高清視覺角色卡** 與 **小隊狀態儀表板圖卡**。
* **完整跑團工具箱**：`!rpg start [主題]` 自動建獨立討論串開局、多人組隊管理（`!rpg join` / `leave` / `kick`）、手動擲骰（`!roll`）、全隊進度存讀檔（`!save` / `!load`）、旅程手記（`!note` / `!journal`）與關鍵字觸發的世界觀設定集（`!lore`）。

### 5. 🌸 日語雙頻道共學、單字卡、口說跟讀與搶答（`!card` / `!shadow` / `!quiz`）
* **日語雙頻道音字分離 ＋ 口說跟讀回饋（`!shadow`）**：在綜合學習頻道輸出純日語正文＋粉色繁中翻譯與重點單字小卡；搭配 `!join` 可自動朗讀純日語。直接於日語頻道發 **Discord 語音訊息**，或輸入 `!shadow [句]` / 點單字卡 **`🎙️ 跟讀練習`**，即可讓 Gemini Flash-Lite 原生評測發音清晰度、音調高低與改進建議！
* **互動式 SRS 單字卡（`!card`）**：支援按鈕翻面、Anki 真人原音或 Edge-TTS 日語朗讀、圖卡顯示、AI 家教解說、跟讀練習與 Leitner 記憶熟練度。
* **三合一匯入＆搶答（`!card import` / `!quiz`）**：支援聊天室貼字、讀取 `data/imports/` 大型 Anki `.apkg` 牌組（支援 N1~N5 分級與高低頻篩選）或拖曳上傳；輸入 `!quiz` 就能展開 Kotoba 風格多人限時搶答！

### 6. 🧟 《殭屍學園》韓服黑話與圖鑑知識庫（`!wiki`）
* **專為輕量模型打造的防幻覺檢索**：內建韓服黑話、Cafe 攻略用語集、9 週年《魔法高中》、聖誕節活動、進擊／進化感染／越獄模式術語與台服角色譯名對照表，支援 `resource_links` 多連結攻略導覽；文字或圖片提問時會先由 Python 自動預檢索並注入精準事實（圖片支援兩階段 OCR 提取韓文與初聲縮寫如 `ㄷㅋㅈ`、`클모`），徹底避免模型亂猜。
* **互動圖鑑＋群友即時共編**：輸入 `!wiki <關鍵字>` 或 `!wiki 目錄` 即可分頁瀏覽；群友可用 `!wiki add` / `!wiki import`（貼文或傳 `.txt`/`.md`）擴充資料，或用 `!wiki replace` 勘誤熱修補，免重啟立即生效。

### 7. 🧠 長期記憶、待辦清單、定時提醒與非即時回饋信箱（`!memory` / `!todo` / `!remind` / `!feedback`）
* **長期記憶庫（`!memory`）**：跨重啟與 `!reset` 永久保存喜好、群規、紀念日或固定團時間，聊天時 AI 會自然記在心裡。
* **待辦清單（`!todo`）**：直接口語說「幫我把買牛奶加進待辦」、「牛奶買好了」，或用指令快速管理。
* **準時背景提醒（`!remind`）**：支援相對時間（「10 分鐘後提醒我關火」）與絕對時間（「今天 18:30 提醒倒垃圾」），背景每 10 秒檢查一次，時間到準時 **`@提及`** 提醒你！
* **非即時回饋與建議信箱（`!feedback` / `!建議`）**：群友直接在對話裡提出錯誤回報或明確建議（如「建議...」、「這裡 Wiki 有錯」）即自動收錄，亦可回覆該訊息搭配 `!feedback` 抓取現場脈絡；**完全不寫入聊天 Prompt、不影響線上回答品質，且公開看板 (`!feedback list`) 與結案回覆皆不點名回報者**。開發者可用 `!feedback export` 或 `scripts/export_feedback.py` 離線檢視處理。

---

## 🎛️ 免改設定檔開關功能（`!setup` 面板 ＋ 記事本兩用）

* **✨ 最簡單方式（推薦）**：如果你已在 `.env` 填入自己的 `BOT_OWNER_IDS`，開機後直接在 Discord 輸入 **`!setup`**，即可用滑鼠點按鈕開關 6 大功能、用下拉選單綁定頻道、切換模型與執行 **`!setup check` 一鍵環境健檢**（變更自動保存在本地 SQLite，不需改動 `.env`）！
* **進階／離線編輯方式**：也可直接用記事本打開你的 `.env` 檔案，把等號右邊改成 `true`（開啟）或 `false`（關閉）存檔重開：

| 設定開關 | 填 `true`（開啟） | 填 `false`（關閉） | 新手版預設 |
| :--- | :--- | :--- | :---: |
| `ENABLE_JAPANESE_MODE` | 開啟日語雙頻道共學（日文正文＋中文翻譯小卡） | 純繁體中文群聊與助理模式 | `false` |
| `ENABLE_FLASHCARD_MODE` | 開啟 `!card` Anki 單字卡與 `!quiz` 日語搶答 | 關閉單字卡功能並隱藏相關指令 | `false` |
| `ENABLE_GAME_WIKI` | 開啟 `!wiki` 《殭屍學園》韓服黑話與圖鑑知識庫 | 關閉專屬遊戲知識庫 | `false` |
| `ENABLE_BOARD_GAMES` | 開啟 `!play` 桌遊大廳（4 款棋類＋80億分之1） | 關閉桌遊與人生模擬模組 | `true` |
| `ENABLE_VOICE_TTS` | 開啟 `!join` / `!voice` 語音頻道朗讀功能 | 關閉語音朗讀（完全免裝 FFmpeg） | `true` |
| `ENABLE_SAFETY_GUARDRAIL` | 開啟本地防 Prompt 注入與防白嫖護欄 | 關閉本地護欄，讓群友自由暢聊 | `false` |

> ⚠️ **修改 `.env` 新手提醒**：如果某一行**最前面有 `# `**，代表它是停用註解，修改它是不會生效的！要啟用請記得**刪掉開頭的 `# `**，且等號前後不要多留空格。
>
> 💡 **三種開箱設定範本（支援同電腦多實例完全隔離資料庫）**：
> * **新手自架／好友分享版**（`.env.starter.example` + `prompts/general_prompt_starter.txt`）：純中文群聊＋桌遊＋80億分之1＋TRPG＋語音朗讀。
> * **社群跑團與知識庫版**（`.env.community.example` + `prompts/general_prompt_community.txt`）：額外開啟《殭屍學園》知識庫與安全護欄，獨立使用 `data/community.db`。
> * **雙語共學管家版**（`.env.example` + `prompts/system_prompt.txt`）：完整開啟日語雙頻道共學與 Anki 單字卡，獨立使用 `data/assistant.db`。

---

## 📖 指令大全

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `!help [主題]` | 依目前啟用模組動態顯示指令速查表（支援 `rpg`、`lore`、`tts`、`wiki`、`system`） | `!help`、`!help tts` |
| `!setup [check]` | **擁有者專用**：免改 `.env` 互動設定面板（動態開關功能、下拉綁定頻道、模型切換與一鍵環境健檢） | `!setup`、`!setup check` |
| `!reload` | **管理員／擁有者**：免重啟熱重載人設 Prompt、知識庫與 `.env` 動態模型開關 | `!reload` |
| `!models` | **擁有者專用**：檢視目前 Flash-Lite 模型輪替鏈、429 冷卻狀態與 AI Studio 即時可用模型 | `!models`、`!模型` |
| `!reset` | 封存當前頻道／討論串的歷史對話（持久化於 SQLite），開啟全新話題 | `!reset` |
| `!todo` | 查看目前未完成的待辦事項（支援 `add` / `done` / `del` / `all`） | `!todo add 訂週六燒肉`、`!todo done 1` |
| `!remind <時間> <內容>` | 設定定時提醒（另可 `!reminds` 查清單、`!remind del <編號>` 取消） | `!remind 10m 關火`、`!remind 18:30 倒垃圾` |
| `!memory` | 查看所有保存的長期記憶清單（`!remember <事實>` 新增、`!forget <編號>` 刪除） | `!memory`、`!remember 喜歡抹茶拿鐵` |
| `!feedback` / `!建議` | 非即時錯誤回報與功能建議信箱（亦支援口語自然觸發；`list` 公開查看看板、`view <編號>`、`done`/`triage`/`close`/`export`） | `!feedback list`、`!建議 新增某功能` |
| `!play` / `!life` | 開啟桌遊大廳或 **80億分之1** 深度版（支援 `!life top` 排行榜、`!life atlas` 圖鑑、混合與 AI 模式） | `!play`、`!play chess llm`、`!life top` |
| `!m <棋步>` | （輔助捷徑）落子座標；遊戲中皆可用卡片按鈕與口語下棋、悔棋、做人生抉擇與查戰績/圖鑑 | `!m d3`、`!m e2e4` |
| `!rpg start [主題]` | 自動建立專屬獨立討論串開啟 TRPG 冒險（遇到檢定會貼 **🎲 互動按鈕**，點擊即由 DM 續寫） | `!rpg start 迷霧森林遺跡` |
| `!rpg join` / `leave` | 主動加入或單獨退出冒險隊伍（也可 `!party join` / `leave` / `kick`） | `!rpg join`、`!rpg leave` |
| `!rpg` / `!party` | 查看個人冒險狀態卡 ／ 視覺化小隊全員儀表板圖卡 | `!rpg`、`!party` |
| `!sheet [文字/檔名/附檔]` | 查看自適應視覺化角色卡（修仙/賽博/CoC/奇幻自動換色）或匯入 `.txt`/`.md` 角色卡 | `!sheet`、`!sheet char.txt` |
| `!save <名>` / `!load <名>` | 保存或載入全隊冒險快照與手記（`!saves` 查看、`!rpg delsave <名>` 刪除） | `!save 第一章`、`!load 第一章` |
| `!note <線索>` / `!journal` | 記錄或查看當前冒險旅程手記（`!rpg delnote <編號>` 刪除） | `!note 守衛怕老鼠`、`!journal` |
| `!roll [表達式]` | TRPG 多面骰手動擲骰檢定 | `!roll`、`!roll 2d6`、`!roll 1d20+3` |
| `!lore` / `!lore import` | 查看或批次匯入世界觀設定集（`!lore add 詞 \| 內容`、`!lore del <編號>`、`!lore clear`） | `!lore`、`!lore add 星露草 \| 解毒藥草` |
| `!wiki [關鍵字]` | 互動分頁查詢《殭屍學園》角色、模式與韓服黑話（支援 `目錄`/`browse`、`add`/`import`/`replace`/`list`/`del`） | `!wiki 阿修羅`、`!wiki 燒瓶 2技`、`!wiki 目錄` |
| `!card [牌組名]` / `!decks` | 開啟互動式單字卡學習（含 `🎙️ 跟讀練習`）／ 查看所有牌組（支援 `add` / `import` / `generate`） | `!card`、`!card add 桜`、`!card import all` |
| `!shadow [日文句]` | 日語口說跟讀評測（亦可直接於日語頻道傳送 Discord 語音訊息取得發音建議） | `!shadow`、`!shadow よろしくお願いします` |
| `!quiz [牌組名] [題數]` | Kotoba 風格多人限時搶答測驗 | `!quiz N5 5` |
| `!join` / `!leave` | 讓機器人加入或退出你所在的語音頻道（別名 `!dc`） | `!join`、`!dc` |
| `!say <文字>` / `!skip` | 立即在語音頻道朗讀文字 ／ 跳過目前正在朗讀的語音 | `!say 大家晚上好` |
| `!voice [參數]` | 開啟個人聲線選單（免費 Edge-TTS 16 聲線＋`rate` / `pitch` / `reset`，全體可用） | `!voice`、`!voice pitch +8` |
| `!voice bot` / `!botvoice` | 設定機器人自己說話的聲線（一般對話／`jp` 日語共學，全體可用） | `!voice bot`、`!botvoice jp rate -15` |
| `!voiceclone` / `!clone` | 申請／管理專屬語音克隆（啟用後安檢通過即用私人聲線，可加 `public` 申請公開；支援 `list`/`public`/`delete`/`cancel`） | `!voiceclone public 溫柔旁白 \| 你好大家好` |
| `!tts [子指令]` | 查看狀態；**管理員**設對象頻道與開關；**擁有者**管理 F5-TTS（`preload`、`keepalive [on\|off\|秒]`、`unload`、`reloadvoices`） | `!tts status`、`!tts keepalive on` |

---

## ⚙️ 進階玩家：雙實例與本地 F5-TTS 啟動方式

若你已經透過 `.venv` 架好環境，並希望在同一台電腦上同時運作兩個完全隔離的機器人實例（例如一隻日語共學、一隻社群跑團）：

* **主實例（`.env` ➔ `data/assistant.db`）**：雙擊 **`scripts/start_bot.bat`**
* **社群實例（`.env.community` ➔ `data/community.db`）**：雙擊 **`scripts/start_community_bot.bat`**
* **選配本地 F5-TTS 語音伺服器**：整合於 `services/tts_server.py` 與 `data/tts_voices/`；若曾執行 `scripts/setup_f5_tts.bat` 建立 `.venv_tts` 且 `127.0.0.1:9880` 尚未啟動，啟動 Bot 時會自動於最小化視窗開啟 `scripts/start_f5_tts_server.bat`，不會重複多開。
