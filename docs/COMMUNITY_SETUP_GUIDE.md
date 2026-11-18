# 🤖 Discord Gemini 社群機器人：零基礎小白保母級架設教學

本教學專為**完全沒有寫過程式、不熟悉指令列**的朋友設計！請放輕鬆，跟著底下的步驟一步步點擊，只需約 10 分鐘，就能把這隻擁有「微毒舌常駐群友、TRPG 跑團主持人、遊戲知識庫與生活待辦」的 AI 機器人請進你的 Discord 伺服器！

---

## 📋 準備清單（你需要準備的東西）
1. 一個 **Discord 帳號**（並且在你要加入機器人的伺服器擁有「管理員」權限）。
2. 一個 **Google 帳號**（用來申請免費的 Gemini API Key，不需要綁定信用卡）。
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
     - ✅ Send Messages（發送訊息）
     - ✅ Send Messages in Threads（在討論串發送訊息）
     - ✅ Create Public Threads（建立公開討論串）
     - ✅ Embed Links（嵌入連結）
     - ✅ Attach Files（附加檔案）
     - ✅ Read Message History（讀取歷史訊息）
     - ✅ Add Reactions（新增反應）
   - 頁面最下方會產生一串 **GENERATED URL**，點擊右側的 **「Copy」**。
   - 在瀏覽器開新分頁貼上該網址，選擇你的 Discord 伺服器，點擊「授權」即可將機器人加進伺服器！

---

## 🔑 第二步：取得免費 Google Gemini API Key

1. 前往 [Google AI Studio](https://aistudio.google.com/)。
2. 點擊右上角或中間登入你的 Google 帳號。
3. 進入後，點擊左側或上方的 **「Get API key」** 按鈕。
4. 點擊 **「Create API key」**（若跳出專案選擇，選擇 `Create key in new project` 即可）。
5. 複製產生的那一長串 API Key（開頭通常是 `AIzaSy...`），複製並先貼在記事本備用。
   > 💡 Google 提供充足的每日免費額度，完全不需要綁定信用卡！

---

## 🎯 第三步：取得 Discord 聊天頻道 ID

我們需要告訴機器人它要在哪一個頻道跟你互動：

1. 打開你的電腦版 Discord。
2. 點擊左下角個人頭像旁的 **「使用者設定」（齒輪圖示）**。
3. 往下拉到左側選單的 **「進階」**（英文為 Advanced）。
4. 將 **「開發者模式」**（Developer Mode）開關打開（變綠色）。
5. 關閉設定，回到你的伺服器。
6. 對著**你想讓機器人聊天的文字頻道**點擊滑鼠右鍵，點擊最下方的 **「複製頻道 ID」**。
7. 這是一串 18~19 碼的純數字，先貼在記事本備用。

---

## 💻 第四步：安裝 Python 執行環境

機器人是由 Python 驅動的，需要先在電腦上安裝它：

1. 前往 Python 官方網站下載頁面：[Python 官方下載](https://www.python.org/downloads/)。
2. 點擊黃色按鈕 **「Download Python 3.12.x」** 下載安裝檔。
3. 執行下載下來的安裝程式。
4. 🔥 **【最致命陷阱！請看清楚】**：
   - 在安裝程式的第一個畫面，最下方有兩個勾選框：
     - [ ] Use admin privileges when installing py.exe
     - [ ] **Add python.exe to PATH** 👈 **務必！一定要把這個打勾！**
   - **請務必把 `Add python.exe to PATH` 打勾**，再點擊上方的 **「Install Now」**。
   - 安裝完成後，點擊「Close」關閉安裝程式。

---

## 📦 第五步：設定與一鍵啟動機器人

下載專案資料夾解壓縮後，請打開 `scripts/` 資料夾，裡面有準備好的小幫手：

### 1. 產生並填寫設定檔
- 雙擊執行 **`scripts/1_設定金鑰_Setup_Env.bat`**。
- 它會自動以安全的新手入門範本（`.env.starter.example` + `prompts/general_prompt_starter.txt`）建立 `.env` 與你的專屬人設檔（若尚未存在），並用「記事本」幫你打開 `.env`。
- 請在對應的位置貼上剛才準備好的三串金鑰與 ID（**等號後面不要留空格**）：
  ```env
  GEMINI_API_KEY=貼上你的Gemini_API_Key
  DISCORD_BOT_TOKEN=貼上你的Discord_Bot_Token
  TARGET_CHANNEL_ID=貼上你的純數字頻道ID
  ```
- （選用功能開關）預設新手版只開啟通用群聊與 TRPG 跑團；若想開啟額外模組，可在 `.env` 將對應開關改為 `true`：
  - `ENABLE_GAME_WIKI=true`（開啟《殭屍學園》韓服黑話與圖鑑知識庫）
  - `ENABLE_FLASHCARD_MODE=true`（開啟日語單字卡與搶答）
  - `ENABLE_SAFETY_GUARDRAIL=true`（開啟防 Prompt 注入與濫用安全護欄）
- 按鍵盤 `Ctrl + S` 存檔，然後關閉記事本。

### 2. 一鍵安裝依賴套件（只需執行一次）
- 雙擊執行 **`scripts/2_一鍵安裝_Install.bat`**。
- 黑色視窗會自動為你建立 `.venv` 專屬虛擬環境並安裝 `requirements.txt` 所需的套件（如 discord.py、google-genai 等）。
- 過程中請耐心等待 1~3 分鐘，直到視窗顯示安裝完成後按任意鍵關閉即可。

### 3. 一鍵啟動機器人！
- 雙擊執行 **`scripts/3_一鍵啟動_Start_Bot.bat`**。
- 視窗會顯示正在啟動，並出現：
  ```
  [INFO] 🚀 DiscordGeminiBot 登入成功！已連線身分: ...
  ```
- 此時回到你的 Discord 伺服器，你的機器人頭像旁邊會亮起綠燈（在線上）！
- 在指定頻道傳送訊息（例如：`嗨，你是誰？`），機器人就會回應你囉！
- **想關閉機器人時**：直接關閉該黑色視窗即可。

---

## ❓ 常見問題與排錯指南 (FAQ)

### Q1：點擊 `2_一鍵安裝_Install.bat` 出現 `'python' 不是內部或外部命令`？
- **原因**：安裝 Python 時忘了勾選 `Add python.exe to PATH`。
- **解決方法**：重新執行剛才下載的 Python 安裝檔，選擇「Modify」或解除安裝後重裝，**請務必在第一步勾選最下方的 `Add python.exe to PATH`**！

### Q2：機器人上線了，但我在頻道傳訊息它完全不理我？
- **請檢查以下三點**：
  1. **Message Content Intent 有沒有開？** 回到第一步的 Discord Developer Portal -> Bot -> 檢查 **Message Content Intent** 是否確定已開為藍色並儲存。
  2. **頻道 ID 對不對？** 打開 `.env` 檢查 `TARGET_CHANNEL_ID` 是不是剛好就是你發話的那個頻道。
  3. **身分組權限是否足夠？** 確認機器人在該頻道擁有「讀取訊息」與「傳送訊息」權限。

### Q3：啟動時出現 `Invalid Token` 或登入失敗？
- **原因**：`.env` 中的 `DISCORD_BOT_TOKEN` 複製有誤，或者多複製了空格或換行。
- **解決方法**：回到 Discord Developer Portal 重新點一次 Reset Token，重新複製並小心貼到 `.env` 的 `DISCORD_BOT_TOKEN=` 後方。

### Q4：我想改機器人的說話風格、安全限制或知識庫？
- 機器人的日常說話風格與人設寫在 `.env` 的 `GENERAL_PROMPT_FILE` 指定的檔案中（新手腳本預設使用 `general_prompt.txt`，源自 `general_prompt_starter.txt`）；你可以在開關機前修改 `.env` 中的 `ENABLE_SAFETY_GUARDRAIL` / `DISABLE_SAFETY_SETTINGS` / `ENABLE_GAME_WIKI`。
- 若只修改人設文字或 `data/game_wiki/` 知識庫，直接在頻道輸入 **`!reload`** 就能即時熱重載，不用重開視窗！

---
祝使用愉快！如果有任何問題，歡迎詢問伺服器管理員！
