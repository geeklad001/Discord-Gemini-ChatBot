# 📋 《殭屍學園 (좀비고등학교)》知識庫升級與資料整理實作計畫 (Implementation Plan)

本文件為本專案《殭屍學園》知識庫功能（`ENABLE_GAME_WIKI`）的開發架構規範、Gemini 4 Hydrogen 離線閉卷整理 SOP、人工校正流程與執行進度追蹤表。任何接手本工作的 Agent 在開始整理或修改程式前，應先閱讀本文件與 [AGENTS.md](../AGENTS.md)。

---

## 一、 核心設計原則（防幻覺・防過時・防輕量模型分心）

1. **雙模型職責嚴格分離**：
   * **離線整理模型（Gemini 4 Hydrogen / 高階模型）**：僅作為「閉卷萃取與對齊翻譯器」。**嚴禁使用模型預訓練記憶編造任何設定**；所有產出必須 100% 來自輸入的 Namuwiki 或 Naver Cafe 韓文原文。
   * **線上回答模型（`gemini-flash-lite-latest`）**：在「100% 閉卷考模式」下運行，僅讀取 Python 預檢索之 `Top-3 ~ Top-4` 微卡片並套用群友口吻回答。
2. **為 `gemini-flash-lite-latest` 量身打造的微卡片規格**：
   * **單卡字數上限**：每張正式卡片內文（含標頭、不含 YAML）控制在 **150 ～ 380 字**（Linter 上限 450 字），貫徹「一卡一概念」，防止小模型中間遺失（Lost-in-the-Middle）與張冠李戴。
   * **強制脈絡標頭（Contextual Header）**：開頭必帶 `【《殭屍學園》[分類] — 標題｜...】`，確保小模型知道主詞、所屬模式與版本。
   * **內建防呆警語（Negative Guardrails）**：易混淆卡片結尾必附 `⚠️ 防誤判提醒`（例如不可混同職業與怪物、不可將民間刷分法當成標準通關規則）。
   * **白話搜尋鉤子（Search Hooks）**：`keywords` 除韓文原名、初聲縮寫、台服官譯外，應預先補齊 3~5 個「台灣玩家常見白話問法詞組」。
3. **有界權威與時效透明（Bounded Provenance）**：
   * 100% 採用純文字來源（Namuwiki 條目、Naver Cafe 貼文、群友實測文字），不進行高 Token 消耗的影片內容解析。
   * 每張卡片標記 `source`（資料來源）與 `updated_at`（整理基準年月，如 `2026-11`），以及 `freshness`（`stable` 固定設定 / `volatile` 隨版本變動之戰術）。

---

## 二、 YAML Schema V2 規範（[engines/game_wiki_engine.py](../engines/game_wiki_engine.py)）

所有新增與升級的 `data/game_wiki/*.md` 卡片支援以下完整 YAML Frontmatter 欄位（皆向後相容舊版 V1 卡片）：

```yaml
---
id: evo_zombie_example
category: evolution              # slang | evolution | cafe_meta | character | story_arc | game_mode | au_series | community_contrib
timeline: common                 # classic (經典舊版) | reboot (重製版) | common (共通) | au (平行世界)
series: evolution                # evolution | jinjom | heroes | apo | division | prison | ladder | au
chapter: all                     # ep0 | ep1 | ep2 | ep3 | ep4 | all
subject: 宿主殭屍名稱             # 主詞實體（供「主詞 + 子面向」雙層加權）
aspects: [技能, 破點, 進化條件]   # 子面向關鍵字清單
name_zh: 繁中條目標題
name_kr: 한글 원문
source: Namuwiki / Naver Cafe    # 資料來源標記
updated_at: 2026-11              # 整理或最後驗證年月 (YYYY-MM 或 YYYY-MM-DD)
freshness: volatile              # stable (固定設定) | volatile (隨改版變動之戰術/數值)
supersedes: []                   # 欲軟屏蔽取代的舊條目 ID 清單（多用於 Runtime 熱修補）
keywords: [韓文原詞, 台服官譯, 白話問法詞]
related_ids: [關聯條目id]
---
```

### 檢索與防誤判核心機制
1. **防 `EP1` 跨系列撞車與 `Classic vs. Reboot` 消歧義**：
   * 單純章節修飾詞（`EP0~EP4`、`第一章~第四章`、`第一關~第四關`、`1부~3부`）列入 `_MODIFIER_CHAPTER_TOKENS`，單獨命中不計入有效專有分數。
   * 當提問同時包含系列詞（如 `H1`、`進擊`、`Division`、`進化感染`）或版本詞（`經典`、`Reboot`）時，由 `_detect_query_coordinates()` 鎖定 `(series, chapter, timeline)` 座標給予高加成並壓制其他系列同章節卡片。
   * 當提問僅出現模糊章節詞（如「EP1 劇情是什麼？」）且未指明系列與版本時，自動注入「跨系列與新舊版消歧義導覽提示」，引導 AI 先反問確認或分項簡述，禁止自行猜測混拼。
2. **`subject` + `aspects` 雙層加權**：
   * 解決單一角色或單一模式拆成多張微卡片後，因主詞同名而搶佔 Top-4 的排擠問題。
3. **Runtime 新蓋舊熱修補（`!wiki replace` / `!wiki hotfix`）**：
   * 使用者可在 Discord 透過 `!wiki replace <舊條目ID或關鍵字> | <新內容>` 寫入 `community_contributions.md`（自動附帶 `supersedes: [old_id]` 與當日日期）。
   * 引擎於載入時執行「軟屏蔽（Soft-Masking）」，自動隱藏被 `supersedes` 的舊卡；若該筆補充遭 `!wiki del contrib_N` 刪除，舊卡即刻恢復生效，零資料遺失風險。
   * 任何命中之 `community_contrib` 條目於 Prompt 組裝時強制置頂於 `⚡【群友最新實測與改版熱修補（最高優先）】` 區塊。

---

## 三、 離線整理隔離區與 Gemini 4 Hydrogen SOP（`data/wiki_staging/`）

為徹底避免過去「一次寫入正式庫卻充滿幻覺」的問題，所有新資料採 **三階段隔離流水線**：

1. **Step 1：原文放入 `data/wiki_staging/01_raw/`**
   * 將從 Namuwiki 或 Naver Cafe 複製的原始韓文文本存入對應檔案（例如 `evolution_namu_raw.txt`、`evolution_cafe_raw.txt`）。
2. **Step 2：Gemini 4 Hydrogen 閉卷萃取至 `data/wiki_staging/02_review/*.review.md`**
   * **強制鎖定譯名**：角色名稱與俗稱必須查閱並對齊 [character_translations_tw.csv](../data/game_wiki/character_translations_tw.csv)。
   * **強制除草規則**：凡原文提及「改版後已移除/失效」的舊數值或舊 Bug 技巧，不可寫入現行建議；若具代表性則須標註 `【⚠️ 歷史舊制】`。
   * **強制附上原文佐證**：每筆草稿條目內必須附帶：
     * `> 🔍 [原文佐證]：...`（摘錄對應韓文原句，無原句不得寫入設定）
     * `> ⚠️ [待人工確認]：...`（凡 CSV 尚未收錄的新譯名、台服未知俗稱或民間分歧打法，一律標註等待人工定奪）
3. **Step 3：Linter 自動檢查 ＋ 人工校正 ＋ 一鍵發布 (`scripts/wiki_linter.py`)**
   * 執行 `.venv\Scripts\python.exe scripts/wiki_linter.py check data/wiki_staging/02_review/<檔名>.review.md` 檢查字數、YAML 欄位、泛用詞污染與未結案的 `> ⚠️` 標記。
   * 人工確認與修訂完成後，執行 `.venv\Scripts\python.exe scripts/wiki_linter.py promote data/wiki_staging/02_review/<檔名>.review.md --target <目標檔>.md`，自動剝除審核輔助行並正式入庫。

---

## 四、 資料主題分階段建置路線圖

### 🔥 Priority 1：伺服器核心主題 ——《進化感染 (감염모드 에볼루션)》
目標檔案：`data/game_wiki/evolution_mode.md`（結合 Namuwiki 與 Naver Cafe 玩家實戰資料）
* **Batch 1-1：核心規則與進化機制**（回合流程、進化點數/階段切換、分數獎勵、與舊版感染模式差異）
* **Batch 1-2：殭屍方圖鑑與實戰對策**（母體／各特殊進化殭屍韓文原名、俗稱、技能與連招、人類應對法）
* **Batch 1-3：人類方武器／職業生態與民間主流配置**（擊退/緩速/跑酷/破盾/輔助定位、Naver Cafe 主流評價與套路）
* **Batch 1-4：地圖點位（명당 守點）、跑酷路線與專屬黑話**（各圖名稱對照、人類守點與殭屍破點技巧、韓服開房與溝通縮寫）

### 📚 Priority 2 ~ 5：A ~ E 後續擴充順序
* **Priority 2（分類 D）**：第一季（53把）＆第二季（14把）武器技能機制與實戰定位（`data/game_wiki/weapons_detail.md`）
* **Priority 3（分類 C）**：PVE 副本機制與 Naver Cafe 民間打法（Division D1~D3、英雄模式 H2~H4、進擊的殭屍 EP1~3 Classic & Reboot、APO）
* **Priority 4（分類 A & B）**：世界觀核心勢力、主線劇情（Classic vs. Reboot 對照、Heroes、APO、Division）與核心角色深度設定
* **Priority 5（分類 E）**：平行世界（精靈高/銀河高/魔法高/像素高）、其他 PVP 與韓服生態 Q&A

---

## 五、 多 Agent 協作分工規範 (Multi-Agent Orchestration)

針對 **Phase 3《進化感染 (감염모드 에볼루션)》Batch 1-1 ~ 1-4** 及後續大規模知識庫整理，總協調者（Orchestrator）應採**「檔案所有權互斥（Disjoint File Ownership）」＋「人工核准閘門（Human Approval Gate）」**拆分任務，防止多 Agent 並行寫入同一檔案造成覆蓋衝突或將未經佐證的草稿混入正式庫：

1. **Orchestrator（總協調者）**
   * 依主題批次（例如 Batch 1-1 ~ 1-4）派發專屬輸入／輸出檔案路徑，**嚴禁**任何 Subagent 直接修改 `data/game_wiki/*.md`。
   * 彙整各批次 Linter 檢驗結果與待人工確認清單呈報人類；**必須取得人類明確核准**並確認已清除 `> ⚠️` 標記後，始得執行 `promote` 正式合併入庫。
2. **Subagent 1 — Raw Chunker（原文切塊前置員）**
   * **專屬所有權**：僅操作 `data/wiki_staging/01_raw/`（依批次產出如 `evolution_b1_rules_*.txt` ~ `evolution_b4_maps_*.txt`）。
   * **職責**：清理 Namuwiki／Naver Cafe 複製文本之版面雜訊、補齊來源 URL 與擷取日期標頭，並依「一主題一區塊」切分段落；**嚴禁在此階段進行翻譯或改寫**。
3. **Subagent 2 — Hydrogen Grounded Extractor（閉卷萃取與譯名對齊員）**
   * **專屬所有權**：唯讀 `01_raw/` 與 [character_translations_tw.csv](../data/game_wiki/character_translations_tw.csv)，僅寫入對應批次之 `data/wiki_staging/02_review/<batch>.review.md`。
   * **職責**：嚴格閉卷萃取為 YAML V2 微卡片（潔淨內文 `150~380` 字、硬性上限 `450` 字），強制補齊 Contextual Header、`⚠️ 防誤判提醒`、`> 🔍 [原文佐證]：...`；凡遇 CSV 未收錄譯名、舊制差異或民間爭議打法，一律掛上 `> ⚠️ [待人工確認]：...`。
4. **Subagent 3 — Linter & Reviewer（規範驗證與待審彙總員）**
   * **專屬所有權**：唯讀 `02_review/*.review.md` 與對照表，執行 [scripts/wiki_linter.py](../scripts/wiki_linter.py) `check`。
   * **職責**：自動把關必填欄位、重複 ID、字數上限、泛用詞／孤立章節詞污染、中韓黏連錯字與台服譯名一致度，並統計 `unresolved_reviews` 回報 Orchestrator；待人類確認修正且 `check` 零 Error、零未決 `> ⚠️` 後，方由 Orchestrator 核准執行 `scripts/wiki_linter.py promote <review_file> --target <目標檔>.md`。

---

## 📊 執行進度追蹤表 (Execution Progress Tracker)

- [x] **Phase 0：實作計畫建檔**
  - [x] 建立 `docs/WIKI_IMPLEMENTATION_PLAN.md` 並納入雙模型 SOP 與進度追蹤表
- [x] **Phase 1：底層檢索與 Runtime 熱修補引擎升級**
  - [x] 升級 [engines/game_wiki_engine.py](../engines/game_wiki_engine.py)（YAML V2 座標、防 `EP1` 撞車、模糊提問消歧義、`subject+aspects` 加權、`supersedes` 軟屏蔽、熱修補置頂區塊、來源與基準時間顯示）
  - [x] 升級 [cogs/wiki_cog.py](../cogs/wiki_cog.py)（新增 `!wiki replace` / `hotfix` 指令與即時回復機制）
  - [x] 同步更新 [README.md](../README.md)、[docs/COMMANDS.md](../docs/COMMANDS.md)（保持 `< 2000` 字元）與 [cogs/assistant_cog.py](../cogs/assistant_cog.py)（`!help` 面板與 `!help wiki`）
- [x] **Phase 2：離線整理隔離區與防幻覺 Linter 建置**
  - [x] 建立 `data/wiki_staging/01_raw/`、`data/wiki_staging/02_review/` 與審核範本
  - [x] 實作 [scripts/wiki_linter.py](../scripts/wiki_linter.py)（`check` 防幻覺與規範檢驗、`promote` 自動清理並正式入庫）
- [x] **Phase 3：Priority 1 ——《進化感染 (감염모드 에볼루션)》資料整理與人工校正**
  - [x] **伺服器攻略唯讀匯出與站內導覽升級**：完成 [scripts/export_discord_guides.py](../scripts/export_discord_guides.py) 匯出 6 個進化感染攻略頻道（`154` 則訊息、`40` 張原圖附件、`5` 支 YouTube 標題），並於引擎與 Linter 支援 `guide_url` 原文跳轉連結
  - [x] **Naver Cafe 官方公告與民間攻略補完（內建瀏覽器）**：完成 `공지사항`（查核至 `2026-11-18`，確認 `2024-04-25` 後零平衡改動，僅 3 筆小修 bug）、`자율학습 64/65/66` 與 `【우수】／【일반】게임 공략` 原文歸檔至 [evolution_cafe_raw.txt](../data/wiki_staging/01_raw/evolution_cafe_raw.txt)（`4,022` 行），並以此全面消解燒瓶、阿修羅、武器與觀念之版本數值衝突
  - [x] **Wave D（`革命聊天區`＋已登入 Cafe 會員補完）**：擴充 [export_discord_guides.py](../scripts/export_discord_guides.py)（`--text-only` / `--limit` / `--jsonl`），純文字掃描 **`革命聊天區 (1135967383245299924)`**（`9,901` 則人類訊息／`9,892` 則有機對話／`52` 位匿名發言者）產出 [\_term_analysis.md](../data/wiki_staging/01_raw/discord_chat_scan/_term_analysis.md)；並於已登入 Cafe 補齊 `15533219` 教室機率圖、`16854030` 攻略、`13699006` 通行圖與 `13529899`/`16851137` 手電筒 1 秒實證（歸檔至 [evolution_cafe_raw.txt](../data/wiki_staging/01_raw/evolution_cafe_raw.txt) Section C）
  - [x] **Wave E（全量定案、清零與正式入庫）**：Batch 1-1 ~ 1-6（共 9 份審核檔、合計 **64** 張微知識卡）已完成全部 `> ⚠️ [待人工確認]` 消解，並正式透過 `scripts/wiki_linter.py promote`（未使用 `--force`）入庫至 [evolution_mode.md](../data/game_wiki/evolution_mode.md)（`entries=64`, `unresolved_reviews=0`, `errors=0`, `warnings=0`）：
    - [x] **Batch 1-1** (`evolution_b1_rules.review.md`)：核心規則與進化階段機制（`9` 卡 / `0` 待確認 / `errors=0, warnings=0` ➔ 已正式 `promote` 入庫 [evolution_mode.md](../data/game_wiki/evolution_mode.md)）
    - [x] **Batch 1-2** (`evolution_b2_zombies.review.md`, `evolution_b2_zombie_flask.review.md`, `evolution_b2_zombie_asura.review.md`)：殭屍方完整圖鑑與實戰對策（警衛 `6` 卡／燒瓶 `5` 卡／阿修羅 `6` 卡，共 `17` 卡 / `0` 待確認 / `errors=0, warnings=0` ➔ 已正式 `promote` 入庫 [evolution_mode.md](../data/game_wiki/evolution_mode.md)）
    - [x] **Batch 1-3** (`evolution_b3_weapons.review.md`)：人類方主流武器生態與伺服器實戰對策（`7` 卡 / `0` 待確認 / `errors=0, warnings=0` ➔ 已正式 `promote` 入庫 [evolution_mode.md](../data/game_wiki/evolution_mode.md)）
    - [x] **Batch 1-4** (`evolution_b4_maps_school_prison.review.md`, `evolution_b4_maps_slang.review.md`)：地圖守點（명당）、報位圖與專屬黑話（教室與監獄 `5` 卡／廚房餐廳與黑話 `9` 卡，共 `14` 卡 / `0` 待確認 / `errors=0, warnings=0` ➔ 已正式 `promote` 入庫 [evolution_mode.md](../data/game_wiki/evolution_mode.md)）
    - [x] **Batch 1-5** (`evolution_b5_server_concepts.review.md`)：伺服器基礎與進階觀念實測（`10` 卡 / `0` 待確認 / `errors=0, warnings=0` ➔ 已正式 `promote` 入庫 [evolution_mode.md](../data/game_wiki/evolution_mode.md)）
    - [x] **Batch 1-6** (`evolution_b6_cafe_meta.review.md`)：Naver Cafe 改版歷程、2025 主流 Meta、韓服縮寫報位、教室機台機率圖與野房禮儀（`7` 卡 / `0` 待確認 / `errors=0, warnings=0` ➔ 已正式 `promote` 入庫 [evolution_mode.md](../data/game_wiki/evolution_mode.md)）
  - [x] **韓文攻略短句翻譯輔助模式（隨 Phase 3 同步推出）**：配合 [engines/game_wiki_engine.py](../engines/game_wiki_engine.py) 與 [prompts/general_prompt_community.txt](../prompts/general_prompt_community.txt) 納入韓文攻略短句翻譯輔助模式，支援韓文攻略／報位短句與術語對照解析
  - [x] **《進化感染》黃金回歸測試套件（隨 Phase 3 同步推出）**：納入 `tests/test_evolution_wiki_regression.py` 作為 Phase 3 上線同步推出的《進化感染》知識庫回歸驗證套件
- [ ] **Phase 4：Priority 2 —— S1 & S2 武器技能與實戰定位（`weapons_detail.md`）**
- [ ] **Phase 5：Priority 3 —— PVE 副本攻略與 Naver Cafe 民間打法（Division / Heroes / JinJom / APO）**
- [ ] **Phase 6：Priority 4 —— 世界觀、主線劇情（Classic vs. Reboot 對照）與核心角色深度檔案**
- [ ] **Phase 7：Priority 5 —— 平行世界（AU 系列）與韓服實用生態 Q&A**
