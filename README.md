# 🌸 Discord 情侶日語共學 & 生活助理機器人 (Gemini 3.8 Flash)

專為情侶打造的 Discord 專屬管家與生活日語學伴機器人。連接 Google AI Studio 最新旗艦 **Gemini 3.8 Flash**（搭配 **Thinking: Medium** 深度思考）與 Discord，具備**日語學習雙頻道音字分離**、**日常三人閒聊**、**情侶長期記憶**、**共同待辦清單**與**背景定時提醒**！

---

## 🌟 核心特色功能

### 1. 📻 日語共學雙頻道（完美整合 HornBot 語音朗讀）
* **頻道 1（`TARGET_CHANNEL_ID`）**：綜合學習頻道，AI 發送純日語正文，下方搭配精美粉色卡片（繁體中文翻譯、重點單字讀音）。
* **頻道 2（`TTS_CHANNEL_ID`）**：HornBot 朗讀專用日文頻道，AI **僅發送純日語**（無中文小卡、無雜音），供 HornBot 專注發音朗讀。
* **雙向小卡鏡像同步**：雙方在任一頻道打字，對向頻道均以 Discord Embed 小卡同步發言（HornBot 絕不朗讀 Embed，既不干擾語音，又能保留完整上下脈絡）。

### 2. 💬 日常三人閒聊頻道（`GENERAL_CHANNEL_ID`）
* 純當朋友放鬆閒聊，支援看圖（多模態 Vision）與網址自動摘要。
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

### 8. 🌐 多實例雙開架構（情侶共學版 vs. 社群跑團版）
* **情侶版（`start_bot.bat` / `.env`）**：開啟日語雙頻道與單字卡，連結 `data/assistant.db`。
* **社群版（`start_community_bot.bat` / `.env.community`）**：關閉日語與單字卡，化身為熱情且親切的社群助手與新人招待，日常暢聊並隨選提供 TRPG 跑團服務，獨立連結 `data/community.db`，100% 資料隔離！

---

## 📖 指令大全

| 指令 | 說明 | 範例 |
| :--- | :--- | :--- |
| `!help` | 查看完整指令手冊與說明卡片 | `!help` |
| `!todo` | 查看目前尚未完成的待辦事項 | `!todo` |
| `!todo add <內容>` | 新增待辦事項 | `!todo add 訂週六燒肉` |
| `!todo done <編號/關鍵字>` | 標記待辦為已完成 | `!todo done 1` 或 `!todo done 燒肉` |
| `!todo del <編號>` | 刪除待辦事項 | `!todo del 1` |
| `!todo all` | 查看歷史待辦紀錄 | `!todo all` |
| `!remind <時間> <內容>` | 設定定時提醒 | `!remind 10m 關火`、`!remind 18:30 倒垃圾` |
| `!reminds` | 查看所有尚未觸發的預約提醒 | `!reminds` |
| `!remind del <編號>` | 取消某個預約提醒 | `!remind del 1` |
| `!rpg start [主題]` | 自動建立專屬獨立討論串開啟冒險 | `!rpg start 迷霧森林遺跡` |
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
| `!card [牌組名]` / `!decks` | 開啟互動式單字卡學習 / 查看所有牌組 | `!card`、`!decks` |
| `!card add <單字>` | 新增單字（僅打單字時由 AI 自動補齊讀音與例句） | `!card add 桜` |
| `!card import [檔名/子牌組]` | 貼上文字、讀取 `data/imports/`（支援指定如 `N3高頻`）或上傳匯入 | `!card import`、`!card import N3高頻` |
| `!card generate <主題> [數]` | 由 AI 量身打造主題單字牌組 | `!card generate 居酒屋菜單 8` |
| `!quiz [牌組名] [題數]` | Kotoba 風格多人限時搶答測驗 | `!quiz N5 5` |
| `!memory` | 查看所有保存的長期記憶清單 | `!memory` |
| `!remember <事實>` | 直接寫入長期記憶庫 | `!remember 女友對花生過敏` |
| `!forget <編號>` | 刪除某一條長期記憶 | `!forget 1` |
| `!reset` | 封存當前頻道的歷史對話，重新開啟新話題 | `!reset` |

---

## 🚀 快速啟動

* **情侶共學與管家實例**：雙擊 **`start_bot.bat`**
* **社群跑團與群聊實例**：雙擊 **`start_community_bot.bat`**
