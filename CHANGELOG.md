# 📝 更新日誌 (CHANGELOG)

本文件記錄 DiscordGeminiBot 的重大版本演進與功能更新歷程。

---

## [v2.1.0] - 2026-11-26

本次更新涵蓋 **Flash / Gemini 4 Hydrogen 高峰備援模型輪替與 404 冷卻避讓**，以及 **《殭屍學園》知識庫 Phase 4 擴充（Schema V2.1 多連結導覽、Cafe 用語集、9 週年《魔法高中》、聖誕節活動）**。

### 🌟 重點新功能與升級

* **⚡ Flash / Gemini 4 Hydrogen 高峰備援輪替與 404 冷卻避讓**
  * **預設模型輪替鏈升級**（[core/config.py](core/config.py)、`.env*.example`）：維持 `gemini-flash-lite-latest` 與多代 Flash-Lite 優先，遇尖峰限流（429）時自動接續備援 `gemini-flash-latest`、`gemini-3.8-flash`、`gemini-4-hydrogen`、`gemini-2.5-flash`，並於全鏈限流後回落 `FINAL_FALLBACK_MODEL` 保底。
  * **404 自動冷卻避讓**（[core/llm_client.py](core/llm_client.py)）：新增 `_is_not_found_error()` 與 `not_found_cooldown_seconds=600`（10 分鐘）冷卻機制，避免部分帳號尚未開通的模型每回合重複撞 `404 NOT_FOUND` 延遲。
  * **互動面板與診斷強化**（[cogs/setup_cog.py](cogs/setup_cog.py)、[cogs/assistant_cog.py](cogs/assistant_cog.py)）：`!setup` 模型分頁新增完整備援模型選單與 **`🪶 切換純 Flash-Lite 鏈`** 一鍵切換按鈕；`!models` 新增 `hydrogen` 與標準 Flash 可用模型及冷卻狀態檢視。
* **🧟 《殭屍學園》Wiki Phase 4 擴充與 Schema V2.1 多連結導覽**
  * **三大知識庫正式入庫（新增 `51` 張微知識卡，全庫達 `142` 張）**：
    * [data/game_wiki/cafe_namu_glossary.md](data/game_wiki/cafe_namu_glossary.md)（`26` 卡）：Cafe 攻略用語集補完（帳號／加倍／PVE／PVP 術語，對齊 Division 強化能力、APO 失物招領、Notion 成就與舊台服圖鑑）。
    * [data/game_wiki/anniv9_magic_high.md](data/game_wiki/anniv9_magic_high.md)（`15` 卡）：9 週年《魔法高中》歷年改版演進、9 大元素與魔杖 +-4 機制、副本與 Boss 攻略導覽。
    * [data/game_wiki/christmas_events.md](data/game_wiki/christmas_events.md)（`10` 卡）：2014～2026 聖誕節活動總覽、2025《聖誕工坊》與 2026 前夜祭復刻機制與刷幣效率。
  * **Schema V2.1 `resource_links` 多連結導覽與檢索強化**：升級 [engines/game_wiki/](engines/game_wiki/)（`loader.py`、`rag.py`、`community.py`、`nlp.py`、`constants.py`）、[cogs/wiki_views.py](cogs/wiki_views.py) Embed 多連結呈現、[prompts/general_prompt_community.txt](prompts/general_prompt_community.txt) 攻略導覽原則與 [scripts/wiki_linter.py](scripts/wiki_linter.py) 驗證規則；新增 `anniv9`／`christmas` 查詢座標鎖定與 `平行世界`／`活動` 目錄別名。
* **✅ 測試與驗證**
  * 全套件實測通過 **136 項單元與回歸測試**，並通過 `wiki_linter` 結構／連結檢查。

---

## [v2.0.0] - 2026-11-25

本次為全面升級大版本，涵蓋原生語音頻道 TTS 系統、F5-TTS 快取直讀與顯存策略、新手互動設定面板、四大娛樂／學習模組深度強化、資安防呆加固與模組化架構重構。

### 🌟 重點新功能與升級

* **🔊 語音頻道原生朗讀 & 聲線克隆（免外掛 HornBot）**
  * 內建免顯卡・0 VRAM **Edge-TTS 16 種台灣／中國／日本聲線**（`!join`、`!leave`、`!say`、`!voice`、`!botvoice`），支援個人與機器人分開設定語速與音高。
  * 整合可選配的本地 **F5-TTS 懶加載伺服器**（`services/tts_server.py`）與 `!voiceclone` 成員自助／審核克隆流程。
  * **F5-TTS 離線快取直讀 & GPU 常駐切換**：新增 `F5_OFFLINE_FIRST=true` 直接讀取本機 Hugging Face 快取略過連網檢查，支援 `F5_PRELOAD_ON_STARTUP` 啟動預載與 `F5_IDLE_UNLOAD_SECONDS=0` 顯存常駐不自動卸載（持久化於 `server_prefs.json`），並新增 `!tts preload` 與 `!tts keepalive [on|off|秒]` 即時控制。
* **⚙️ 新手免改 `.env` 的互動設定與健檢面板（`!setup` / `!setup check`）**
  * 僅限機器人擁有者使用，透過非機敏 SQLite 動態覆寫層（`bot_runtime_config`）支援熱切換 6 大功能與動態載入／卸載 Cog、Discord 原生 `ChannelSelect` 下拉綁定頻道、Flash-Lite 主模型切換，以及一鍵檢查 FFmpeg、頻道權限、Gemini API 與本地 F5-TTS 狀態。
* **🌍 《80億分之1 (1in8B)》深度升級（`!life` / `!play`）**
  * 拆分重構為 `engines/lifesim/` 套件，**預設維持 `⚡ 0-Token 深度本地版`**（亦可切換 `🎭 精華混合版` 或 `🧠 全程 AI 劇情版`）。
  * 實作 **78+ 條件與歷史大事件**、真實城鄉出生率（`urban_pct`）、15 種互不衝突先天特質、高風險抉擇機率擲骰檢定、關係網與每階段 5 種副行動（含 `✈️ 跨國移居` 成功解鎖第 2 國圖鑑）、`LifeSimCustomDecisionModal` 自訂決定彈窗、五維軌跡折線＋稱號紀念圖卡與 `!life top` 伺服器排行榜。
* **🗡️ TRPG 體驗升級（`!rpg` / `!sheet` / `!party`）**
  * 新增 Gemini `rpg_request_check` 工具，AI 遇危機時自動貼出 **`🎲 互動檢定按鈕`**，玩家點擊即由本機計算屬性加值＋專長熟練擲骰，並自動喚醒 DM 接續後續劇情。
  * 新增 `engines/rpg_renderer.py`，為 `!sheet` 與 `!party` 自動依世界觀（修仙、賽博、克蘇魯 CoC、奇幻）套色渲染視覺狀態圖卡。
* **🎙️ 日語口說跟讀回饋（`!shadow` / `!card`）**
  * 新增 3 MB / 30 秒音訊護欄（[core/media.py](core/media.py)），支援在日語共學頻道直接傳 Discord 原生語音訊息取得發音診斷，以及單字卡 `🎙️ 跟讀練習` 按鈕與 `!shadow` 指令的 0～100 分跟讀評測與標準發音試聽。
* **⚡ 全 Flash-Lite 模型輪替鏈 & 429 限流冷卻**
  * `core/llm_client.py` 採用純 Flash-Lite 輪替（`gemini-flash-lite-latest` ➔ `3.5` ➔ `3.1` ➔ `2.5` ➔ 保底回落 `FINAL_FALLBACK_MODEL`），內建 45 秒 429 冷卻避讓與跨世代 `GenerateContentConfig` 自適應調整，新增 `!models` 診斷指令。

### 🔒 資安防護與架構重構

* **資安與邊界修復**：修復棋盤模組 `NameError`、`.apkg` 路徑穿越與 Zip Bomb 防護、SSRF 重導向阻擋、YouTube/短影片時長 FAIL-CLOSED 與 `<untrusted_*>` 標籤隔離、F5-TTS 權杖驗證與綁定安全鎖、TTS 管線清理與全數 DB/PIL 動作 `asyncio.to_thread` 非同步卸載。
* **模組化套件架構**：拆解 `engines/tts/`、`cogs/tts/`、`engines/game_wiki/`、`engines/lifesim/` 與獨立 Views / `core/help_embeds.py` 維持 100% 門面相容；改版新手保母級設定教學與指令手冊。
* **完整測試覆蓋**：測試套件擴展至 **134 項測試全數通過**。

---

## [v1.x] - 歷史基礎版本摘要

* **Evolution Mode Wiki 與互動 UX**（`148fbef`）：《殭屍學園》進化感染百科雙階段 OCR＋LightRAG、互動分頁與勘誤熱修補。
* **桌遊大廳 & 《80億分之1》初版**（`edfce04`、`86278c0`、`9b4fef4`）：黑白棋／西洋棋／四子棋／五子棋與 197 國統計人生模擬初版。
* **TRPG 引擎 & 日語單字卡系統**（`f1e649b`、`fe0b481`、`67486b5`）：雙實例隔離架構、SRS 單字卡／Kotoba 搶答與多模組重構。
