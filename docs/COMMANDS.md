# 🌸 GeminiBot 全功能指令手冊

本手冊無表格，可直接貼入 Discord（< 2000 字元）。
平時直接口語聊天即可！支援看圖、貼圖、網址摘要、短影片/YT 分析（3分鐘內）、日語頻道發語音，亦可吩咐：「記住喜好」、「15分鐘後提醒」、「加待辦」或直接說「建議...」／「這裡有錯」回報。

---

📋 **待辦、提醒與回饋 (!todo / !remind / !feedback)**
• `!todo` / `add <事項>` / `done <編號>` / `del <編號>` / `all`
• `!remind 10m 關火` / `!remind 18:30 倒垃圾` / `!reminds` / `!remind del <編號>`
• `!feedback [分類] <內容>`（`!fb`/`!回報`/`!建議`）：非即時回報或建議（可回覆原訊息帶脈絡）
• `!feedback list` / `view <編號>`（公開看板）｜ **管理員**：`done`/`triage`/`close`/`export`

🧠 **長期記憶 (!memory)**
• `!memory`：查看記憶 ｜ `!remember <事實>`：新增 ｜ `!forget <編號>`：刪除

🗡️ **RPG 跑團與組隊 (!rpg / !party / !sheet / !roll)**
• `!rpg start [主題]`：建討論串開局（AI 會附 **🎲 互動檢定按鈕**，點擊即擲骰並自動續寫！）
• `!rpg join` / `leave` / `!party kick @隊友`：入退隊 ｜ `!party`：小隊視覺儀表板
• `!sheet [文字/檔名]`：視覺化角色卡（修仙/賽博/CoC/奇幻換色）｜ `!rpg`：個人狀態
• `!roll [骰子]`：手動擲骰（`!roll 2d6`） ｜ `!rpg reset`：重置本局

💾 **存讀檔與設定集 (!save / !load / !note / !lore)**
• `!save <名>` / `!load <名>` / `!saves` / `!rpg delsave <名>`：全隊進度快照
• `!note <線索>` / `!journal` / `!rpg delnote <編號>`：冒險手記
• `!lore` / `!lore add 詞 | 內容` / `!lore import` / `!lore del <編號>` / `!lore clear`

🌸 **日語單字、跟讀與搶答 (!card / !shadow / !quiz)**
• `!card [牌組]`：互動字卡（原音/TTS、圖卡、AI 解說、`🎙️ 跟讀練習`、SRS）
• `!shadow [日文句]`：口說跟讀評測（亦可直接於日語頻道發語音訊息練習）
• `!card add <詞>` / `import` / `generate` ｜ `!decks` / `!quiz [牌組] [數]`

🔊 **語音朗讀與聲線 (!join / !voice / !botvoice / !tts)**
• `!join` / `!leave` (`!dc`) / `!say <文字>` / `!skip`：進出語音房、代說與跳過
• `!voice`：個人選聲線 ｜ `!voice bot [jp]` (`!botvoice`)：機器人聲線 ｜ `!voiceclone`：克隆說明
• `!tts status` / `channels` ｜ **管理員**：`channel`、`bind`、`ai`、`proxy`、`vconly`
• **擁有者**：`!tts preload`（預載）、`keepalive [on|off|秒]`、`unload`、`reloadvoices`

🎮 **桌遊大廳與 80 億分之 1 (!play / !life)**
• `!play`：黑白棋/西洋棋/四子棋/五子棋（`!m <座標>`）、`!life [top|atlas]`：80億分之1 深度版

🧟 **知識庫與擁有者管理 (!wiki / !setup / !reload / !models / !reset / !help)**
• `!wiki [詞]`（`!圖鑑`）：查黑話設定，支援 `目錄`/`add`/`import`/`replace`/`list`/`del`
• `!setup` / `check`（擁有者）：免改 `.env` 設定面板 ｜ `!reload` / `!models` / `!reset` / `!help`
