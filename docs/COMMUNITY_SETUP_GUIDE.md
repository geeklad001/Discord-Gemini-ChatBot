# 🤖 Discord Gemini 社群機器人：零基礎小白保母級架設教學

本教學專為**完全沒有寫過程式、不熟悉指令列**的朋友設計！請放輕鬆，跟著底下的步驟一步步點擊，只需約 10 分鐘，就能把這隻擁有「AI 常駐群友、桌遊與 80 億分之 1 人生模擬、TRPG 跑團主持人、語音房朗讀、遊戲知識庫與生活待辦提醒」的機器人請進你的 Discord 伺服器！

---

## 📋 準備清單（你需要準備的東西）
1. 一個 **Discord 帳號**（並且在你要加入機器人的伺服器擁有「管理員」權限）。
2. 一個 **Google 帳號**（用來申請免費的 Gemini API Key，**完全不需要綁定信用卡**）。
3. 一台 **Windows 電腦**（機器人運作時電腦需開著並連上網路）。

---

## 🚀 第一步：申請 Discord 機器人（取得 Bot Token）

請打開瀏覽器，依照以下步驟建立一隻 Discord 機器人：

1. **進入 Discord 開發者後台**：
   - 前往 [Discord Developer Portal](https://discord.com/developers/applications) 並登入你的 Discord 帳號。
2. **建立應用程式**：
   - 點擊右上角藍色的 **「New Application」** 按鈕。
   - 在 **NAME** 欄位輸入你喜歡的機器人名字（例如：`打工阿吉` 或 `CommunityBot`）。
   - 勾選同意服務條款，點擊右下角 **「Create」**。
3. **取得機器人金鑰（Token）**：
   - 點擊左側選單的 **「Bot」**。
   - 在頁面中間找到 **Build-A-Bot** 區塊，點擊 **「Reset Token」** 按鈕（系統會詢問 Yes, do it!，若有雙重驗證請輸入驗證碼）。
   - 點擊 **「Copy」** 複製那一長串英數混雜的字串。
   > ⚠️ **【重要提醒】**：這串 Token 等同於機器人的身分證與密碼，**千萬不能傳給任何人**！請先貼在記事本備用。
4. **🔥【極度重要！小白最容易漏掉的大魔王】開啟 Privileged Gateway Intents**：
   - 在同一個「Bot」頁面往下拉，找到 **Privileged Gateway Intents** 區塊。
   - **務必將以下三個開關全部切換為開啟（變成藍色開關）**：
     - ✅ **Presence Intent**
     - ✅ **Server Members Intent**
     - ✅ **Message Content Intent**（🔥 沒開這個，機器人就無法看見群組訊息，會完全不理人！）
   - 開啟後，點擊下方彈出的綠色按鈕 **「Save Changes」** 儲存！
5. **將機器人邀請進你的伺服器**：
   - 點擊左側選單的 **「OAuth2」** 裡面的 **「URL Generator」**。
   - 在 **SCOPES** 區塊中，只勾選：
     - ✅ `bot`
   - 下方會出現 **BOT PERMISSIONS** 區塊，請勾選以下權限：
     - **文字與互動權限（必勾）**：
       - ✅ **Send Messages**（發送訊息）
       - ✅ **Send Messages in Threads**（在討論串發送訊息）
       - ✅ **Create Public Threads**（建立公開討論串）
       - ✅ **Embed Links**（嵌入連結／顯示精美卡片）
       - ✅ **Attach Files**（附加檔案／傳送棋盤圖卡）
       - ✅ **Read Message History**（讀取歷史訊息）
       - ✅ **Add Reactions**（新增反應）
     - **語音頻道權限（想用 `!join` 讓機器人說話就要勾）**：
       - ✅ **Connect（連線）**（讓機器人能進入語音頻道）
       - ✅ **Speak（說話）**（讓機器人在語音頻道朗讀聲音）
     > 💡 **小提示**：如果你確定不需要語音朗讀功能（只想文字聊天、玩桌遊或跑團），**Connect** 和 **Speak** 這兩個可以跳過不勾也沒關係。
   - 頁面最下方會產生一串 **GENERATED URL**，點擊右側的 **「Copy」**。
   - 在瀏覽器開新分頁貼上該網址，選擇你的 Discord 伺服器，點擊「授權」即可將機器人加進伺服器！

---

## 🔑 第二步：取得免費 Google Gemini API Key

1. 前往 [Google AI Studio](https://aistudio.google.com/)。
2. 點擊右上角或中間登入你的 Google 帳號。
3. 進入後，點擊左側或上方的 **「Get API key」** 按鈕。
4. 點擊 **「Create API key」**（若跳出專案選擇，選擇 `Create key in new project` 即可）。
5. 複製產生的那一長串 API Key（開頭通常是 `AIzaSy...`），先貼在記事本備用。
   > 💡 **免費安心用**：本機器人預設**全程自動使用免費額度最充沛的 Gemini Flash-Lite 輕量系列**，並且內建**多代 Flash-Lite 自動輪替保底**（`gemini-flash-lite-latest` ➔ `gemini-3.5-flash-lite` ➔ `gemini-3.1-flash-lite` ➔ `gemini-2.5-flash-lite` ➔ 最後回落保底重試），不會誤觸高階或付費模型，**完全不需要綁定信用卡**！

---

## 🎯 第三步：取得 Discord 聊天頻道 ID（順手複製自己的使用者 ID）

我們需要告訴機器人它要在哪一個頻道跟你互動：

1. 打開你的電腦版 Discord。
2. 點擊左下角個人頭像旁的 **「使用者設定」（齒輪圖示）**。
3. 往下拉到左側選單的 **「進階」**（英文為 Advanced）。
4. 將 **「開發者模式」**（Developer Mode）開關打開（變綠色）。
5. 關閉設定，回到你的伺服器。
6. 對著**你想讓機器人聊天的文字頻道**點擊滑鼠右鍵，點擊最下方的 **「複製頻道 ID」**。
7. 這是一串 18～19 碼的純數字，先貼在記事本備用。

### 👑 怎麼取得自己的 Discord 使用者 ID（填進 `BOT_OWNER_IDS`）
既然剛剛已經把「開發者模式」打開了，強烈建議順手拿一下**你自己的使用者 ID**：
1. 在 Discord 裡，對著**你自己左下角的個人頭像**（或在頻道裡對著你自己發的訊息頭像）點擊**滑鼠右鍵**。
2. 點擊最下方的 **「複製使用者 ID」**（這也是一串純數字），貼在記事本備用。
3. **為什麼要填進 `BOT_OWNER_IDS`？**
   - 填上去之後，機器人就會認出你是它的最高管理者（擁有者），可以解鎖 **`!reload`**（免重開即時熱重載人設與設定）、**`!models`**（查看目前 Flash-Lite 模型輪替與限流冷卻狀態）、**`!tts unload`**（進階釋放顯存）等專屬權限！
   - 沒填也能正常聊天，但填上會方便很多喔！

---

## 💻 第四步：安裝 Python 執行環境

機器人是由 Python 驅動的，需要先在電腦上安裝它：

1. 前往 Python 官方網站下載頁面：[Python 官方下載](https://www.python.org/downloads/)。
2. 點擊黃色按鈕 **「Download Python 3.12.x」**（或更新的 3.11+ 版本）下載安裝檔。
3. 執行下載下來的安裝程式。
4. 🔥 **【最致命陷阱！請看清楚】**：
   - 在安裝程式的第一個畫面，最下方有兩個勾選框：
     - `[ ]` Use admin privileges when installing py.exe
     - `[ ]` **Add python.exe to PATH** 👈 **務必！一定要把這個打勾！**
   - **請務必把 `Add python.exe to PATH` 打勾**，再點擊上方的 **「Install Now」**。
   - 安裝完成後，點擊「Close」關閉安裝程式。

---

## 🔊 選配：想讓機器人進語音房說話？安裝 FFmpeg（只需 1 分鐘）

> ✨ **先別緊張！如果你不打算開語音朗讀（只想玩文字聊天、桌遊、80億分之1、TRPG 跑團），這整段都可以直接跳過，完全不用裝 FFmpeg！**

如果你想讓機器人用 `!join` 進語音頻道陪大家說話，Windows 需要一個叫 **FFmpeg** 的免費小工具來幫它轉送聲音。安裝非常簡單，二選一即可：

### 方法 A（最推薦！只要貼一行字，全程 1 分鐘）
1. 按鍵盤上的 **`Windows 鍵 + S`**，輸入 **`PowerShell`**（或「終端機」），點一下打開它。
2. 複製底下這一行，在藍色或黑色視窗裡**按滑鼠右鍵貼上**，然後按 **`Enter`**：
   ```powershell
   winget install Gyan.FFmpeg
   ```
3. 如果畫面跳出詢問是否同意條款，輸入 **`Y`** 再按 `Enter`，接著靜待它自動下載並顯示安裝完成。
4. **🔥 裝完必做一步**：把剛剛開的視窗關掉（如果保險一點，直接**重新開機一次**最穩），讓系統重新讀取 FFmpeg 的位置！

### 方法 B（不想用指令，或想直接關掉語音功能）
* **手動下載版**：前往 [gyan.dev FFmpeg Builds](https://www.gyan.dev/ffmpeg/builds/) 下載 `ffmpeg-release-essentials.zip`，解壓縮後將其中的 `bin` 資料夾路徑加到 Windows 環境變數 `Path` 內。
* **乾脆先不開語音**：直接跳到第五步，在 `.env` 記事本裡把 `ENABLE_VOICE_TTS=true` 改成 **`ENABLE_VOICE_TTS=false`** 存檔，就能完全不需要 FFmpeg，清爽玩文字功能！

---

## 📦 第五步：設定與一鍵啟動機器人

下載專案資料夾解壓縮後，請打開 `scripts/` 資料夾，裡面有準備好的 3 個小幫手：

### 1. 產生並填寫設定檔
- 雙擊執行 **`scripts/1_設定金鑰_Setup_Env.bat`**。
- 它會自動以安全的新手入門範本（`.env.starter.example`）幫你建立 `.env` 設定檔，並直接用「記事本」打開它。
- 請把剛才準備好的金鑰與 ID 貼進對應位置（**等號前後不要留空格**）：
  ```env
  GEMINI_API_KEY=貼上你的Gemini_API_Key
  DISCORD_BOT_TOKEN=貼上你的Discord_Bot_Token
  TARGET_CHANNEL_ID=貼上你的純數字頻道ID
  BOT_OWNER_IDS=貼上你的純數字使用者ID
  ```
- **🎛️ 常用功能開關（想開就寫 `true`，想關就寫 `false`）**：
  - `ENABLE_BOARD_GAMES=true`：桌遊大廳（黑白棋／西洋棋／四子棋／五子棋）與 🌍 **80億分之1** 人生模擬（新手版預設開啟）
  - `ENABLE_VOICE_TTS=true`：語音頻道免費 Edge-TTS 朗讀（新手版預設開啟；不想裝 FFmpeg 可改 `false`）
  - `ENABLE_GAME_WIKI=false`：《殭屍學園》韓服黑話與圖鑑知識庫（有玩這款遊戲再改 `true`）
  - `ENABLE_FLASHCARD_MODE=false`：日語 Anki 單字卡與搶答測驗（想背日文單字再改 `true`）
  - `ENABLE_JAPANESE_MODE=false`：日語雙頻道共學模式（想學日文雙語翻譯小卡再改 `true`）
  - `ENABLE_SAFETY_GUARDRAIL=false`：本地防 Prompt 注入與防白嫖護欄（大型公開社群建議改 `true`）
- > ⚠️ **【超重要！記事本新手防雷提醒】**：在 `.env` 裡面，**只要某一行最前面有 `# `（井字號）**，就代表那一行被「停用（當作註解）」了！如果你改了某個前面有 `# ` 的設定，**一定要把開頭的 `# ` 刪掉**，機器人才會真的讀取它喔！
- 按鍵盤 **`Ctrl + S`** 存檔，然後關閉記事本。

### 2. 一鍵安裝依賴套件（只需執行一次）
- 雙擊執行 **`scripts/2_一鍵安裝_Install.bat`**。
- 黑色視窗會自動建立 `.venv` 專屬虛擬環境，並安裝 `requirements.txt` 所需的全部套件（如 discord.py、google-genai、edge-tts 等）。
- 過程中請耐心等待 1～3 分鐘，看到 `[SUCCESS] Installation Complete!` 後按任意鍵關閉即可。

### 3. 一鍵啟動機器人！
- 雙擊執行 **`scripts/3_一鍵啟動_Start_Bot.bat`**。
- 黑色視窗會開始啟動，看到類似下面這行字就大功告成了：
  ```text
  [INFO] 🚀 DiscordGeminiBot 登入成功！已連線身分: ...
  ```
- 此時回到你的 Discord 伺服器，你的機器人頭像旁邊會亮起**綠燈（在線上）**！
- 在指定頻道傳送一句 `嗨，你是誰？`，機器人就會回應你囉！
- **想關閉機器人時**：直接點右上角 `X` 關閉那個黑色視窗即可。

### 🎉 首次啟動後必試的 5 個指令
成功讓機器人上線後，推薦立刻在指定頻道依序玩玩看這 5 個指令：
1. **`!help`** — 打開彩色指令速查表，看看目前開啟了哪些模組（還能打 `!help rpg`、`!help tts` 看細部教學）。
2. **`!play`** — 召喚互動遊戲大廳！點按鈕就能玩黑白棋／西洋棋／五子棋，或是開一局 🌍 **80億分之1** 看看自己投胎到全球哪一國！
3. **`!rpg start 森林探險`** — 機器人會自動幫你開一個獨立討論串，直接化身地下城主帶你丟骰子展開文字冒險！
4. **`!join` ＋ `!voice`** — 先自己點進伺服器的語音房，接著在頻道打 `!join` 把機器人叫進來，再輸入 `!voice` 用下拉選單挑 16 種免費台灣／日本聲線說話！（需已裝 FFmpeg 且勾選 Connect / Speak 權限）
5. **`!models`**（填過 `BOT_OWNER_IDS` 的擁有者專用）— 檢視目前運作中的 Flash-Lite 模型輪替鏈、是否有模型在冷卻，以及 Google AI Studio 即時可用的模型清單！

---

## ❓ 常見問題與排錯指南 (FAQ)

### Q1：點擊 `2_一鍵安裝_Install.bat` 出現 `'python' 不是內部或外部命令`？
- **原因**：安裝 Python 時忘了勾選 `Add python.exe to PATH`。
- **解決方法**：重新執行剛才下載的 Python 安裝檔，選擇「Modify」或解除安裝後重裝，**請務必在第一步勾選最下方的 `Add python.exe to PATH`**！

### Q2：機器人上線了，但我在頻道傳訊息它完全不理我？
- **請檢查以下三點**：
  1. **Message Content Intent 有沒有開？** 回到第一步的 Discord Developer Portal ➔ Bot ➔ 檢查 **Message Content Intent** 是否確定已切為藍色並按了 Save Changes。
  2. **頻道 ID 對不對？** 打開 `.env` 檢查 `TARGET_CHANNEL_ID` 是不是剛好就是你發話的那個頻道。
  3. **身分組權限是否足夠？** 確認機器人在該頻道擁有「檢視頻道」、「讀取訊息歷史」與「傳送訊息」權限。

### Q3：啟動時出現 `Invalid Token` 或登入失敗？
- **原因**：`.env` 中的 `DISCORD_BOT_TOKEN` 複製有誤，或者多複製了空格或換行。
- **解決方法**：回到 Discord Developer Portal 重新點一次 Reset Token，重新複製並小心貼到 `.env` 的 `DISCORD_BOT_TOKEN=` 後方，存檔重開。

### Q4：語音頻道沒聲音、報錯找不到 FFmpeg，或 `!join` 進不去語音房？
- **請依序檢查這三件事**：
  1. **邀請權限漏勾語音權限**：回到第一步的 OAuth2 ➔ URL Generator，確認有勾選 **Connect（連線）** 和 **Speak（說話）**，重新產生邀請連結再授權一次即可補齊權限（不用踢掉機器人）。
  2. **還沒裝 FFmpeg 或裝完沒重開視窗**：請照上方「🔊 選配：安裝 FFmpeg」用 PowerShell 跑 `winget install Gyan.FFmpeg`，**裝完務必關掉機器人黑視窗（或重開機一次）再重新啟動**！
  3. **開關或頻道設定**：輸入 `!tts status` 看看狀態，確認 `.env` 裡的 `ENABLE_VOICE_TTS=true`，且你本人已經先進入語音頻道再打 `!join`。

### Q5：我在 `.env` 改了某個設定，存檔後為什麼完全沒生效？
- **最常見原因**：那一行最前面留著 **`# `**（代表停用註解），或者忘記按 `Ctrl + S` 存檔！
- **解決方法**：打開 `.env` 檢查該行最前面不能有 `# `，等號前後不要多留空格。改完存檔後：
  - 若只改模型設定（`PRIMARY_MODEL` 等）或語音克隆開關，可在頻道打 **`!reload`** 即時生效。
  - 若改了 `ENABLE_*` 大模組開關、Token 或頻道 ID，請關掉黑色視窗再重新雙擊 **`3_一鍵啟動_Start_Bot.bat`**。

### Q6：免費額度尖峰時段偶爾回覆變慢、卡住，怎麼查看目前 AI 模型狀態？
- 本機器人內建多代 **Flash-Lite 自動輪替＋429 冷卻避讓＋最終保底重試**，遇到 Google 免費額度尖峰限流時會自動切換下一代 Flash-Lite 繼續回答。
- 擁有者（已設定 `BOT_OWNER_IDS`）可以直接在頻道輸入 **`!models`**，立刻看到目前啟用的首選／備援／保底模型、哪些模型正在 429 冷卻中，以及 Google AI Studio 即時可用的模型列表；若遇到全線繁忙，通常等 30～60 秒就會自動恢復。

### Q7：我想改機器人的說話個性（Prompt）或擴充知識庫，每次都要重開機器人嗎？
- **不用重開！**
  - 機器人的日常說話個性寫在 `prompts/` 資料夾中 `.env` 指定的檔案（新手版預設為 `prompts/general_prompt_starter.txt`），你可以直接用記事本修改存檔。
  - 改完人設文字、擴充 `data/game_wiki/` 知識庫，或調整 `.env` 的動態模型開關後，直接在頻道輸入 **`!reload`**（限管理員或擁有者）就能**免重啟立即熱重載**！

---
祝使用愉快！快去跟你的專屬 AI 機器人聊天、跑團、玩桌遊吧！🎉
