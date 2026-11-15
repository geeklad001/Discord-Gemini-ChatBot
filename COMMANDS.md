# 🌸 GeminiBot 全功能指令手冊

本手冊無表格、排版清爽，可直接貼進 Discord 聊天室（控制在 2000 字元內）。
平時直接用自然口語聊天即可！支援看圖、表情貼圖、網址摘要、短影片附件 (.mp4/.mov/.webm) 與 YouTube/Shorts 分析（3分鐘內）。也可口語吩咐：「記住我喜歡抹茶」、「15分鐘後提醒收衣服」、「加待辦買牛奶」。

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
• `!rpg start [主題]`：建立專屬獨立討論串開局
• `!sheet`：查看個人詳細角色卡
• `!sheet [文字/檔名]`：匯入角色卡（支援貼文字、`data/imports/` 或附檔）
• `!rpg`：查看個人冒險狀態卡 ｜ `!party`：查看小隊全員面板
• `!rpg name <名>` / `!rpg class <職業>`：修改名稱或職業
• `!roll [表達式]`：D20 或自訂擲骰檢定（如 `!roll 2d6`）
• `!rpg reset`：重置當前頻道冒險進度

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

🧟 **《殭屍學園》知識庫與系統管理 (!wiki / !reload / !reset / !help)**
• `!wiki [關鍵字]`（`!圖鑑` / `!黑話` / `!좀고`）：查詢《殭屍學園》角色、模式劇情與韓服開房黑話（如 `!wiki ㄷㅋㅈ`、`!wiki 클모`、`!wiki 34퀸`；亦支援 `!wiki add / import / list / del` 與截圖辨識）
• `!reload`：熱重載設定檔、人設 Prompt 與知識庫（修改提示詞免重啟立即生效）
• `!reset`：封存當前頻道上下文，開啟新話題
• `!help`：動態調出當前實例啟用功能的指令手冊
