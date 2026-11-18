# 🌸 GeminiBot 全功能指令手冊

本手冊無表格、排版清爽，可直接貼進 Discord 聊天室（< 2000 字元）。
平時直接口語聊天即可！支援看圖、貼圖、網址摘要、短影片/YT 分析（3分鐘內），亦可吩咐：「記住喜好」、「15分鐘後提醒」、「加待辦」。

---

📋 **共同待辦事項 (!todo)**
• `!todo`：查看所有未完成事項
• `!todo add <內容>`：新增待辦（或直接 `!todo 買牛奶`）
• `!todo done <編號/關鍵字>`：標記完成
• `!todo del <編號>`：刪除待辦 ｜ `!todo all`：查看歷史紀錄

⏰ **定時提醒 (!remind)**
• `!remind 10m 關火`：相對時間提醒（支援 30s、10m、2h、1d）
• `!remind 18:30 倒垃圾`：指定時間提醒
• `!reminds`：查看預約清單 ｜ `!remind del <編號>`：取消提醒

🧠 **長期記憶庫 (!memory)**
• `!memory`：查看已記錄的事實與設定
• `!remember <事實>`：新增記憶 ｜ `!forget <編號>`：刪除記憶

🗡️ **RPG 文字冒險與組隊 (!rpg / !party / !sheet / !roll)**
• `!rpg start [主題]`：建立討論串開局 ｜ `!rpg join` / `leave`：主動入隊或單獨退隊
• `!party` / `!party kick @隊友`：查看小隊全員面板或請離成員
• `!sheet [文字/檔名]`：查看或匯入角色卡（匯入即入隊） ｜ `!rpg`：查看個人狀態
• `!rpg name <名>` / `!rpg class <職業>`：修改名稱或職業
• `!roll [表達式]`：D20 或自訂擲骰（如 `!roll 2d6`） ｜ `!rpg reset`：重置本局冒險

💾 **冒險存讀檔與手記 (!save / !load / !note / !lore)**
• `!save <名>` / `!load <名>`：保存或載入全隊進度與手記快照
• `!saves`：查看存檔清單 ｜ `!rpg delsave <名>`：刪除存檔
• `!note <線索>`：記錄情報 ｜ `!journal`：查看冒險手記（`!rpg delnote <編號>` 刪除）
• `!lore`：查看世界觀設定 ｜ `!lore add 關鍵字 | 內容`：新增單筆設定
• `!lore import [文字/檔名]`：批次匯入世界觀（`!lore clear` 清空、`!lore del <編號>` 刪除）

🌸 **日語單字卡與搶答 (!card / !decks / !quiz)**
• `!card [牌組名]`：開啟互動翻面單字卡（原音朗讀、圖卡、AI 家教解說、SRS 熟練度）
• `!card add <單字> [讀音] [意思]`：新增單字（AI 自動補完假名與例句）
• `!card import [檔名/等級/all]`：支援貼文字、讀取 `data/imports/`（`all` 自動拆為 N1~N5 牌組）或附檔匯入
• `!card decks`（`!decks`）：查看牌組清單 ｜ `!card deck <名>`：切換預設牌組
• `!card newdeck <名>` / `!card deldeck <名>`：建立或刪除牌組
• `!card generate <主題> [數量]`：AI 生成主題單字庫
• `!quiz [牌組名] [題數]`（`!搶答` / `!kotoba`）：多人限時 20 秒搶答測驗

🎮 **遊戲大廳：棋盤桌遊 ＆ 80億分之1 (!play ＋ 按鈕/口語)**
• `!play`：開啟大廳（黑白棋/西洋棋/四子棋/五子棋，支援 PvE/🧠LLM/PvP；以及 🌍 **80億分之1** 197國投胎模擬，支援 ⚡極速/🧠LLM 劇情 ＆ `!play 圖鑑`）
• 落子、悔棋、投降、A/B/C 抉擇與圖鑑皆可點按鈕或口語操作（輔助：`!m <座標>`）

🧟 **知識庫與系統管理 (!wiki / !reload / !reset / !help)**
• `!wiki [關鍵字]`（`!圖鑑` / `!좀고`）：查詢《殭屍學園》角色、模式與韓服黑話（支援 `add/import/list/del` 與截圖）
• `!reload` / `!reset` / `!help`：熱重載人設與知識庫、封存對話上下文、調出功能速查表
