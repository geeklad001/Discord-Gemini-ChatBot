# 📋 《殭屍學園 (좀비고등학교)》知識庫升級與資料整理實作計畫 (Implementation Plan)

本文件為本專案《殭屍學園》知識庫功能（`ENABLE_GAME_WIKI`）的開發架構規範、Gemini 4 Hydrogen 離線閉卷整理 SOP、人工校正流程與執行進度追蹤表。任何接手本工作的 Agent 在開始整理或修改程式前，應先閱讀本文件與 [AGENTS.md](../AGENTS.md)。

---

## 一、 核心設計原則（防幻覺・防過時・防輕量模型分心）

1. **雙模型職責嚴格分離**：
   * **離線整理模型（Gemini 4 Hydrogen / 高階模型）**：僅作為「閉卷萃取與對齊翻譯器」。**嚴禁使用模型預訓練記憶編造任何設定**；所有產出必須 100% 來自輸入的 Namuwiki 或 Naver Cafe 韓文原文。
   * **線上回答模型（`gemini-flash-lite-latest`）**：在「100% 閉卷考模式」下運行，僅讀取 Python 預檢索之 `Top-3 ~ Top-4` 微卡片並套用群友口吻回答。
2. **為 `gemini-flash-lite-latest` 量身打造的微卡片規格**：
   * **雙卡型設計（Dual Card Archetypes）**：
     * **導航索引微卡（`120 ～ 260` 字）**：適用於以影片／Discord 串／外部攻略貼文導引為主的活動或副本（如 9 週年、聖誕節活動），強調關卡定位、核心流程與 `resource_links` 多連結導覽。
     * **Cafe 翻譯深度微卡（`150 ～ 380` 字）**：適用於純文字韓服攻略、用語對照與數值機制（Linter 硬性上限 `450` 字），貫徹「一卡一概念」，防止小模型中間遺失（Lost-in-the-Middle）與張冠李戴。
   * **強制脈絡標頭（Contextual Header）**：開頭必帶 `【《殭屍學園》[分類] — 標題｜...】`，確保小模型知道主詞、所屬模式與版本。
   * **內建防呆警語（Negative Guardrails）**：易混淆卡片結尾必附 `⚠️ 防誤判提醒`（例如不可混同職業與怪物、不可將民間刷分法當成標準通關規則）。
   * **白話搜尋鉤子（Search Hooks）**：`keywords` 除韓文原名、初聲縮寫、台服官譯外，應預先補齊 3~5 個「台灣玩家常見白話問法詞組」。
3. **有界權威與時效透明（Bounded Provenance）**：
   * 採純文字來源（Namuwiki 條目、Naver Cafe 貼文、FB 社團／Discord 實測文字）為知識骨幹；影片類攻略**不進行高 Token 消耗的逐幀或全文轉錄解析**，改由 oEmbed 標題／描述摘要與 `resource_links` 建立導覽索引。
   * 每張卡片標記 `source`（資料來源）與 `updated_at`（整理基準年月，如 `2026-11`），以及 `freshness`（`stable` 固定設定 / `volatile` 隨版本變動之戰術）。
4. **速刷優先、新舊刷法隔離與限定條件保全（永久通用標準）**：
   * **速刷攻略與資源效率優先（適用於可農資源模式）**：**並非所有模式皆有資源可刷**（例如《進化感染》、越獄模式等競技／純 PvP 模式仍以核心機制、地圖點位、對策與版本 Meta 為主，不強求硬湊速刷攻略）。凡**具備重複農資源機制**之模式與活動（活動幣／盧恩／魔力券／礦物／點數／經驗／任務捷徑），未來採集與建檔時必須優先萃取「**速刷攻略與資源效率指南**」（實戰打法、時薪或單局收益比較與對應影片／貼文導覽）。
   * **嚴格區分改版前後之「新舊刷法差異」**：凡經官方平衡調整、潛在暗改（Submarine Patch）或歷代復刻規則變動之模式（例如：魔法高中 2025 `빛궁` 暗改使 `60사이클` 失效改採 `40사이클`、2026-11-26 擴充與法杖刻印分歧；聖誕活動 2022.12.16 家庭派對修補前後之時限／幣量／減速差異與歷代復刻機制變動），必須明確標記各刷法的**適用年份／版本區間**與**失效／取代原因**，**嚴禁將已失效舊刷法混寫為現行推薦打法**。
   * **限定條件與適用範圍不可截斷**：為適配 `gemini-flash-lite-latest` 壓縮微卡片（上限 `450` 字）時，若某技巧、路線或排序僅適用於特定關卡、特定地圖、特定人數或特定條件（例如 `[僅限第 9 關圖貳]`、`[僅限 1 人單刷]`、`[僅限 4 人固排]`、`[僅限雙倍熱時]`），**必須在句子開頭保留適用範圍前綴**，嚴禁為了省字數而抽掉前提，防止小模型誤當成全模式通用規則。
5. **零臆測與引用核實鐵律 (Zero-Speculation & Citation Fidelity Gate)**：
   * **嚴禁將臆測寫為既定事實**：嚴禁將開服前／維護前玩家猜測、舊版推論、理論試算或單一疑問猜想（例如「好像會／大概不會」）寫成既定事實。
   * **來源 100% 一致與禁混歸／禁反譯**：卡片正文每項數據、期限與陳述必須與 `source` 及 `resource_links` 指向之原文頁／貼文 100% 一致；嚴禁跨來源混歸（如 FB 貼文誤標 `Namuwiki`、玩家猜測誤標官方公告）或跨活動混植（如精靈高中貼文混入魔法高中），更嚴禁反推翻譯不存在的韓文原文或英文名稱。
   * **獨立稽核與時間軸覆核**：`promote` 前必須由獨立稽核員覆核「**正文陳述 ↔ 原文佐證 ↔ 貼文時間戳**」（必須明確檢核該文發表於改版開服之前或之後）。
6. **禁止矯枉過正／反推肯定原則 (No Over-Correction & Epistemic Neutrality)**：
   * **未明載不等於反向成立**：勘誤時若發現原內容的某項斷言缺乏出處或屬臆測（例如發現官方公告並未明載「不能刷幣」），**絕不可因此反向過度改寫成「一定可以刷幣」**（除非確實有官方公告或明確可驗證的開服後實證支持反向結論）。
   * **維持客觀中立標記**：在缺乏正向或反向官方／實機證據時，一律維持百科客觀事實屬性，如實標記「**官方公告未明載／尚待實測確認**」。
7. **導覽連結品質篩選門檻 (`resource_links` vs `> 🔍 [原文佐證]` 分流規範)**：
   * **離線考據留存（`> 🔍 [原文佐證]`）**：專用於審核與溯源留存，可引用問答、Bug 回報、討論回應與貼文時間戳。
   * **線上導覽篩選（`resource_links`）**：面向玩家的 `resource_links` **嚴格只收錄具系統性價值的官方公告、完整圖文／數據攻略、雲端整理或實戰影片**；**嚴禁收錄無實質攻略價值的個人心得／漏箱抱怨文（如 FB `3437176536574704`）、單純徵隊友貼、一句話尋路求助、或單純提問／Bug 閒聊貼文**。

> [!IMPORTANT]
> **永久防幻覺強制鐵律**：以上 **第 5～7 條** 與第 4 條同屬全專案不可妥協的永久規範，所有階段（含 Phase 4 續補勘誤與 Phase 5～9）之文本整理、除錯與稽核放行皆須一體嚴格遵守。

---

## 二、 YAML Schema V2.1 規範（[engines/game_wiki_engine.py](../engines/game_wiki_engine.py) Facade / [engines/game_wiki/](../engines/game_wiki/)）

本模組已拆分為 `engines/game_wiki/` 子套件（`engines/game_wiki_engine.py` 作為相容 Facade，搭配 `cogs/wiki_cog.py` 與 `cogs/wiki_views.py` 提供互動檢視與導覽）。所有新增與升級的 `data/game_wiki/*.md` 卡片支援以下完整 YAML Frontmatter V2.1 欄位（皆向後相容舊版 V1／V2 卡片）：

```yaml
---
id: evo_zombie_example
category: evolution              # slang | evolution | cafe_meta | character | story_arc | game_mode | au_series | community_contrib
timeline: common                 # classic (經典舊版) | reboot (重製版) | common (共通) | au (平行世界)
series: evolution                # evolution | jinjom | heroes | apo | division | prison | ladder | anniv9 | christmas | au
chapter: all                     # ep0 | ep1 | ep2 | ep3 | ep4 | all
subject: 宿主殭屍名稱             # 主詞實體（供「主詞 + 子面向」雙層加權）
aspects: [技能, 破點, 進化條件]   # 子面向關鍵字清單
name_zh: 繁中條目標題
name_kr: 한글 원문
source: Namuwiki / Naver Cafe    # 資料來源標記
updated_at: 2026-11              # 整理或最後驗證年月 (YYYY-MM 或 YYYY-MM-DD)
freshness: volatile              # stable (固定設定) | volatile (隨改版變動之戰術/數值)
supersedes: []                   # 欲軟屏蔽取代的舊條目 ID 清單（多用於 Runtime 熱修補）
guide_url: https://...           # 單一主要攻略來源跳轉連結（選填）
resource_links: ["🎬 攻略標題｜https://...", "📝 討論串｜https://discord.com/channels/..."] # V2.1 多連結導覽清單（選填）
keywords: [韓文原詞, 台服官譯, 白話問法詞]
related_ids: [關聯條目id]
---
```

> [!NOTE]
> **9 週年與聖誕節活動座標規範**：`9 週年（anniv9）` 與 `聖誕節活動（christmas）` 歸類於 `category: au_series`、`timeline: au`（或視條目性質標為 `common`），並分別以 `series: anniv9` 與 `series: christmas` 進行精準座標鎖定與壓制消歧義。

### 檢索與防誤判核心機制
1. **防 `EP1` 跨系列撞車與 `Classic vs. Reboot` 消歧義**：
   * 單純章節修飾詞（`EP0~EP4`、`第一章~第四章`、`第一關~第四關`、`1부~3부`）列入 `_MODIFIER_CHAPTER_TOKENS`，單獨命中不計入有效專有分數。
   * 當提問同時包含系列詞（如 `H1`、`進擊`、`Division`、`進化感染`、`9週年`、`聖誕節`）或版本詞（`經典`、`Reboot`）時，由 `_detect_query_coordinates()` 鎖定 `(series, chapter, timeline)` 座標給予高加成並壓制其他系列同章節卡片。
   * 當提問僅出現模糊章節詞（如「EP1 劇情是什麼？」）且未指明系列與版本時，自動注入「跨系列與新舊版消歧義導覽提示」，引導 AI 先反問確認或分項簡述，禁止自行猜測混拼。
2. **`subject` + `aspects` 雙層加權**：
   * 解決單一角色或單一模式拆成多張微卡片後，因主詞同名而搶佔 Top-4 的排擠問題。
3. **Runtime 新蓋舊熱修補（`!wiki replace` / `!wiki hotfix`）**：
   * 使用者可在 Discord 透過 `!wiki replace <舊條目ID或關鍵字> | <新內容>` 寫入 `community_contributions.md`（自動附帶 `supersedes: [old_id]` 與當日日期）。
   * 引擎於載入時執行「軟屏蔽（Soft-Masking）」，自動隱藏被 `supersedes` 的舊卡；若該筆補充遭 `!wiki del contrib_N` 刪除，舊卡即刻恢復生效，零資料遺失風險。
   * 任何命中之 `community_contrib` 條目於 Prompt 組裝時強制置頂於 `⚡【群友最新實測與改版熱修補（最高優先）】` 區塊。
4. **`resource_links` 多連結導覽整合（V2.1）**：
   * 支援單張卡片並陳多個「`用途標籤｜URL`」連結（YouTube 影片、Discord 攻略串、FB 社團、Naver Cafe 原文），搭配 [scripts/wiki_linter.py](../scripts/wiki_linter.py) 驗證網址與 Discord 跳轉格式，並由 [cogs/wiki_views.py](../cogs/wiki_views.py) 與回答上下文同步提供導覽出口。

---

## 三、 離線整理隔離區與 Gemini 4 Hydrogen SOP（`data/wiki_staging/`）

為徹底避免過去「一次寫入正式庫卻充滿幻覺」的問題，所有新資料採 **三階段隔離流水線**，並強制貫徹「速刷優先 × 新舊版本隔離 × 限定條件不截斷 × 零臆測引用核實 × 禁止矯枉過正 × 導覽連結篩選」永久標準：

1. **Step 1：原文放入 `data/wiki_staging/01_raw/`**
   * 將從 Namuwiki、Naver Cafe、FB 社團或 Discord 攻略區複製／匯出的原始文本存入對應資料夾（例如 `evolution_namu_raw.txt`、`cafe_deep_raw.txt`）。
   * **原文時間戳與來源邊界保存**：每段原文必須完整保留**真實來源平台（Namuwiki / Naver Cafe / FB / Discord）、貼文 ID／URL 與發表時間戳**（可供後續判別該文發表於「改版開服之前」或「改版開服之後」），嚴禁將不同來源或不同活動文本混貼於同一未分段標記區塊（杜絕跨來源混歸與跨活動混植）。
   * **速刷與改版資料必收清單（適用具備可刷資源之模式）**：每個模式／活動除規則公告外，Step 1 必須優先補齊（a）歷年平衡與熱修補公告（供對照新舊差異）；（b）針對可農資源／點數／票券／經驗之模式，補齊高頻實用速刷攻略與效率比較表（如《進化感染》、越獄等無可農刷幣機制之競技／純 PvP 模式則不在此限）；（c）對應 YouTube 速刷與高難實戰影片標題及連結。僅列入 `cafe_index_raw.txt` 而未深抓全文之高價值速刷文（含聊天區貼過卻未解出片名之 YouTube 連結），須於稽核時列為待補缺口。
2. **Step 2：Gemini 4 Hydrogen 閉卷萃取至 `data/wiki_staging/02_review/*.review.md`**
   * **強制鎖定譯名**：角色名稱與俗稱必須查閱並對齊 [character_translations_tw.csv](../data/game_wiki/character_translations_tw.csv)，**嚴禁反推翻譯不存在的韓文原文或英文名稱**。
   * **零臆測與引用核實鐵律（Zero-Speculation & Citation Fidelity）**：
     * 嚴禁將開服前／維護前玩家猜測、舊版推論、理論試算或單一疑問猜想（例如「好像會／大概不會」）寫成既定事實。
     * 卡片正文每項數據、期限與陳述必須與 `source` 及 `resource_links` 指向之原文頁／貼文 100% 一致；嚴禁跨來源混歸（如 FB 貼文誤標 `Namuwiki`、玩家猜測誤標官方公告）或跨活動混植（如精靈高中貼文混入魔法高中）。
   * **禁止矯枉過正／反推肯定原則（No Over-Correction）**：
     * 勘誤或複檢發現原說法缺乏出處或屬臆測時（例如發現官方公告並未明載「不能刷幣」），**絕不可因此反向過度改寫成「一定可以刷幣」**（除非確實有官方公告或明確可驗證的開服後實證支持反向結論）。
     * 在缺乏正向或反向官方／實機證據時，一律維持百科客觀事實屬性，如實標記「**官方公告未明載／尚待實測確認**」。
   * **速刷專卡優先建置**：凡存在重複農資源機制之模式／活動（盧恩、活動幣、礦物、點數、雙倍熱時、任務捷徑），必須獨立拆出或置頂「速刷策略與效率比較」微卡（含人數／時間／收益比較與 `resource_links` 影片導覽）；無可農資源機制之純 PvP／競技模式則維持核心玩法與實戰 Meta 導向。
   * **強制除草與新舊刷法隔離規則**：凡原文提及「改版後已移除／失效」的舊數值、舊 Bug 技巧或舊代刷法，**嚴禁混寫為現行推薦打法**。若需保留作為版本對照或解釋為何被淘汰，必須獨立標註 `【⚠️ 歷史舊制（YYYY.MM 改版失效）】`，並寫明**適用年份／版本區間**與**官方改動／暗改原因**（如 2025 `빛궁` 暗改使 `60사이클` 失效改採 `40사이클`；2022.12.16 `13351862` 修補前後差異；2026-11-26 擴充異動）。
   * **強制保留限定條件前綴**：壓縮為 `<= 450` 字微卡時，任何具限定前提的攻略（特定關卡／特定地圖編號／單人 vs 雙人 vs 4 人固排／僅限雙倍熱時）**必須保留適用範圍前綴**（如 `[僅限 R9 圖貳]`、`[僅限 1 人單刷]`、`[僅限 4 人固排]`），嚴禁為壓縮字數而刪去前提。
   * **證據留存與 `resource_links` 品質分流**：每筆草稿條目內必須遵守內外分流原則：
     * `> 🔍 [原文佐證]：...`（**離線留存考據專用**：摘錄對應韓文／中文原句、來源 ID 與**貼文時間戳**，可引問答／Bug 回報作為考據；無原句與時間戳不得寫入設定）
     * `resource_links`（**面向玩家導覽專用**）：**嚴格只收錄具系統性價值的官方公告、完整圖文／數據攻略、雲端整理或實戰影片**；**嚴禁收錄無實質攻略價值的個人心得／漏箱抱怨文（如 FB `3437176536574704`）、單純徵隊友貼、一句話尋路求助、或單純提問／Bug 閒聊貼文**。
     * `> ⚠️ [待人工確認]：...`（凡 CSV 尚未收錄的新譯名、台服未知俗稱、改版後尚未驗證之舊攻略效度、官方未明載待實測項目、或民間分歧打法，一律標註等待人工定奪；若某模式缺漏逐關地圖／速刷或改版後實測，應於正式卡 `⚠️ 防誤判提醒` 誠實界定未收錄邊界）
3. **Step 3：Linter 自動檢查 ＋ 獨立稽核員覆核 ＋ 人工校正 ＋ 一鍵發布 (`scripts/wiki_linter.py`)**
   * 執行 `.venv\Scripts\python.exe scripts/wiki_linter.py check data/wiki_staging/02_review/<檔名>.review.md` 檢查字數、YAML 欄位、泛用詞污染與未結案的 `> ⚠️` 標記。
   * `promote` 前必須由**獨立稽核員**與人工完成以下六項永久覆核（嚴禁作者自我放行）：
     1. 具備可刷資源之模式是否已優先涵蓋速刷效率與影片導覽（無資源可刷之純競技／PvP 模式例外）；
     2. 改版前後新舊刷法是否已隔離標記年份與失效原因；
     3. 關卡／地圖／人數限定前綴是否未遭截斷；
     4. **零臆測與引用核實閘門**：逐卡覆核「**正文陳述 ↔ `> 🔍 [原文佐證]`／`source` ↔ 貼文時間戳（必須檢核該文發表於改版開服之前或之後）**」，確認無開服前猜測／試算／疑問詞（如「好像會／大概不會」）混為事實、無跨來源混歸／跨活動混植、無反推韓／英文詞彙；
     5. **禁止矯枉過正閘門**：確認勘誤未因原斷言缺出處而反向過度改寫為肯定句，缺乏正／反官方或實機證據者皆維持「**官方公告未明載／尚待實測確認**」；
     6. **導覽連結品質閘門**：確認 `resource_links` 僅含系統性官方公告、完整圖文／數據攻略、雲端整理或實戰影片，已清除個人心得／漏箱抱怨文（如 FB `3437176536574704`）、單純徵隊友貼、一句話尋路求助與純提問／Bug 閒聊貼（此類僅得留存於 `> 🔍 [原文佐證]`）。
   * 確認通過獨立稽核、人類核准且零未結案 `> ⚠️` 後，方得執行 `.venv\Scripts\python.exe scripts/wiki_linter.py promote data/wiki_staging/02_review/<檔名>.review.md --target <目標檔>.md`，自動剝除審核輔助行並正式入庫。

---

## 四、 資料主題分階段建置路線圖

### ✅ Phase 3（Priority 1 —— 已完成）：《進化感染 (감염모드 에볼루션)》
目標檔案：[evolution_mode.md](../data/game_wiki/evolution_mode.md)（共 `64` 張微知識卡入庫，結合 Namuwiki、Naver Cafe 與 Discord 攻略實測）
* **Batch 1-1 ~ 1-6**：核心規則、殭屍圖鑑（警衛／燒瓶／阿修羅）、人類武器生態、地圖守點與報位黑話、伺服器進階觀念、Naver Cafe 改版歷程與野房禮儀（已全部清零並 `promote` 完畢）。

### ✅ Phase 4（Priority 2 —— 已完成）：Cafe 攻略用語集補完 ＋ 9 週年 ＋ 聖誕節活動
* **Phase 4.0（基礎設施升級，已完成）**：Schema V2.1 多連結導覽（`resource_links`）、`anniv9` / `christmas` 座標鎖定、YAML 引號逗號清單解析、Linter 驗證規則與單元測試升級。
* **Phase 4.1（已完成並入庫，含成就譯名對齊）**：Naver Cafe 攻略用語集（含 `16904226` 等 5 篇字典）＋ Namuwiki（`좀비고등학교/용어`）＋ Discord 攻略頻道 `1141581400307400795`（322 張 Division 強化能力圖鑑）＋ Notion APO 失物招領翻譯（63 件失物招領）＋ Notion 成就資料庫／巴哈舊台服圖鑑對照，共 `26` 張微知識卡已完成人工核對並正式 `promote` 入庫至 [cafe_namu_glossary.md](../data/game_wiki/cafe_namu_glossary.md)。
* **Phase 4.2（已完成並入庫）**：9 週年專區（`category: au_series`, `series: anniv9`）—— 彙整 Discord 攻略頻道 `1142064309673939016` / `1142064409733255178`、討論區 `1136916863020974162`、FB 社團 `1755166524775722`、Naver Cafe 韓文攻略與 Notion 成就譯名對照；共 `15` 張微知識卡已完成人工核對並正式 `promote` 入庫至 [anniv9_magic_high.md](../data/game_wiki/anniv9_magic_high.md)。
* **Phase 4.3（已完成並入庫）**：聖誕節活動專區（`category: au_series`, `series: christmas`）—— 彙整 Discord 討論區 `1435932399186870282`（`萬聖or聖誕節聊天區`）＋ FB 社團 `1755166524775722` ＋ Naver Cafe 韓文攻略 ＋ Notion 成就譯名對照；共 `10` 張微知識卡已完成人工核對並正式 `promote` 入庫至 [christmas_events.md](../data/game_wiki/christmas_events.md)。
* **已歸檔外部參考來源（Provenance & 勘誤規範）**：
  * **Notion 成就／徽章雙語資料庫**：已完整擷取並去重歸檔至 [emblems_notion_ref.txt](../data/wiki_staging/01_raw/emblems_notion_ref.txt)（`2171322382528044bd90d628960fcc35` 頁面與連動資料庫 `2121322382528009a5b4e5e78119c77d`，合計 `757` 筆唯一成就、完整 10 欄位＋頁面備註／80 項目錄）。
    * ⚠️ **排序與編號規範**：`特殊成就（沒有數字編號的成就）` 之自訂 `編號`（如 `N009_*`、`X2025_*`）係資料庫作者自行編排，**不可靠且嚴禁引用於知識卡正文或摘要**；時間排序與實裝日期一律以 **`加入日期`（`新增日期`）** 欄位為準。
    * ⚠️ **上游來源規範**：Notion 成就資料庫原始資料來自 **Namuwiki**，後續若需更新或勘誤成就資料，**必須回頭交叉比對 Namuwiki**。
  * **巴哈姆特舊台服成就系統圖鑑**：已完整歸檔至 [tw_legacy_bahamut_ref.txt](../data/wiki_staging/01_raw/tw_legacy_bahamut_ref.txt)（`bsn=30876&snA=54`，含 001–105 編號成就、無編號專區、站內影片連結、編者註記與 B1 回覆，保存「劇情模式」＝進擊的殭屍 2 等早期台服譯名）。

### 📚 Phase 5 ~ 9（Priority 3 ~ 7 後續擴充順序）

> [!IMPORTANT]
> **全模式永久適用標準（含 Phase 4 續補與 Phase 5～9）**：針對**具備重複農資源／幣／票／點數／經驗機制**之模式與活動，後續採集與建檔一律強制套用本文件 **一.4 與 三** 之「**速刷攻略優先 × 新舊改版差異隔離 × 限定條件不截斷**」規範（例如：Phase 6《Reboot》三部之改版前後機制／刷資源效率、Phase 7《Division》D1～D3 速刷礦物／強化組隊打法、Phase 8 第一季進擊／Heroes／APO 歷年改版速刷與武器定位、Phase 9 精靈高／銀河高／魔法高／像素高之刷幣與歷代活動差異）；無可農資源機制之競技／純 PvP 模式（如《進化感染》、越獄模式等）則仍以核心機制、點位與版本 Meta 為主。

* **Phase 5（Priority 3）**：🧭 世界觀與主線劇情（經典總覽、核心勢力、經典進擊與人物關係骨架）
* **Phase 6（Priority 4）**：🔁 《Reboot（重製版）》專區（1部《破滅的裂隙》/ 2部《絕望的種子》/ 3部《鬥志的火種》機制、劇情變更與速刷／攻略導覽）
* **Phase 7（Priority 5）**：⚔️ 《Division》系列（D1～D3 副本攻略、速刷效率、劇情與既有黑話串聯）
* **Phase 8（Priority 6）**：🛡️ 第一季（S1）武器技能定位（拆解 S1/S2 武器總表）與 PVE 副本速刷攻略（Heroes / JinJom Classic / APO）
* **Phase 9（Priority 7）**：🌌 其他平行世界（精靈高／銀河高／魔法高／像素高）與其餘韓服速刷／生態 Q&A

---

## 五、 多 Agent 協作分工規範 (Multi-Agent Orchestration)

針對 **Phase 3《進化感染 (감염모드 에볼루션)》Batch 1-1 ~ 1-4** 及後續大規模知識庫整理（含 Wave 4 勘誤與 Phase 5～9），總協調者（Orchestrator）應採**「檔案所有權互斥（Disjoint File Ownership）」＋「獨立引用稽核閘門（Independent Citation Audit Gate）」＋「人工核准閘門（Human Approval Gate）」**拆分任務，防止多 Agent 並行寫入同一檔案造成覆蓋衝突，或將未經佐證、過度反寫及低品質連結混入正式庫：

1. **Orchestrator（總協調者）**
   * 依主題批次（例如 Batch 1-1 ~ 1-4）派發專屬輸入／輸出檔案路徑，**嚴禁**任何執筆 Subagent 直接修改 `data/game_wiki/*.md` 或繞過獨立稽核執行 `promote`。
   * 彙整各批次 Linter 檢驗結果、**獨立稽核員（Subagent 4）之引用與連結稽核簽核報告**與待人工確認清單呈報人類；**必須同時取得獨立稽核放行與人類明確核准**、並確認已清除 `> ⚠️` 標記後，始得執行 `promote` 正式合併入庫。
2. **Subagent 1 — Raw Chunker（原文切塊前置員）**
   * **專屬所有權**：僅操作 `data/wiki_staging/01_raw/`（依批次產出如 `evolution_b1_rules_*.txt` ~ `evolution_b4_maps_*.txt`）。
   * **職責**：清理 Namuwiki／Naver Cafe／FB／Discord 複製文本之版面雜訊、補齊**真實來源平台、貼文 ID／URL、貼文發表時間戳（須可明確辨別改版開服前／後）**與擷取日期標頭，依「一主題一區塊」切分段落；嚴禁跨來源或跨活動段落混貼而未標註邊界，嚴禁將玩家猜測標為官方公告；針對具可農資源機制之模式優先完整保留**速刷收益數據（幣／盧恩／時間／人數）、改版前後公告差異、以及 YouTube 速刷片名／連結**；**嚴禁在此階段進行翻譯或改寫**。
3. **Subagent 2 — Hydrogen Grounded Extractor（閉卷萃取與譯名對齊員）**
   * **專屬所有權**：唯讀 `01_raw/` 與 [character_translations_tw.csv](../data/game_wiki/character_translations_tw.csv)，僅寫入對應批次之 `data/wiki_staging/02_review/<batch>.review.md`。
   * **職責**：嚴格閉卷萃取為 YAML V2.1 微卡片（潔淨內文 `150~380` 字、硬性上限 `450` 字）；於具備重複農資源機制之模式優先產出**速刷攻略與資源效率微卡**（無資源可刷之競技／PvP 模式則維持核心機制與 Meta 導向），強制補齊 Contextual Header、`⚠️ 防誤判提醒`、`> 🔍 [原文佐證]：...`（須附原文與貼文時間戳）。壓縮微卡時**必須保留關卡／地圖／人數限定前綴**（嚴禁省字數抽掉前提），且**必須明確標記新舊刷法適用年份與改版失效原因**（嚴禁將已失效舊刷法混寫為現行推薦）。同時必須嚴格遵守三大防幻覺寫作鐵律：
     * **零臆測與引用核實**：嚴禁將開服前／維護前玩家猜測、舊版推論、理論試算或疑問猜想（「好像會／大概不會」）寫成既定事實；正文陳述必須與 `source` 及 `resource_links` 原文 100% 一致，嚴禁跨來源混歸（如 FB 貼文誤標 `Namuwiki`、玩家猜測誤標官方公告）、跨活動混植（如精靈高中貼文混入魔法高中）或反推不存在的韓／英文名稱。
     * **禁止矯枉過正／反推肯定**：勘誤或發現原說法缺乏出處時（如官方未明載「不能刷幣」），**絕不可反向過度改寫為「一定可以刷幣」**（除非具備官方公告或明確可驗證的開服後實證支持反向結論）；無明確正／反實證時一律如實寫為「**官方公告未明載／尚待實測確認**」，並掛上 `> ⚠️ [待人工確認]：...`。
     * **導覽連結品質分流**：問答／Bug 回報／時間戳考據僅放入 `> 🔍 [原文佐證]`；`resource_links` **嚴格只收錄具系統性價值的官方公告、完整圖文／數據攻略、雲端整理或實戰影片**，嚴禁放入個人心得／漏箱抱怨文（如 FB `3437176536574704`）、單純徵隊友貼、一句話尋路求助或純提問／Bug 閒聊貼。
4. **Subagent 3 — Linter & Technical Reviewer（技術規範驗證員）**
   * **專屬所有權**：唯讀 `02_review/*.review.md` 與對照表，執行 [scripts/wiki_linter.py](../scripts/wiki_linter.py) `check`。
   * **職責**：自動把關必填欄位、重複 ID、字數上限、泛用詞／孤立章節詞污染、中韓黏連錯字與台服譯名一致度；並複核**「具可刷資源模式之速刷覆蓋度」「新舊刷法年份與改版原因隔離」「關卡／地圖／人數限定前提完整度」**以及「已索引未深抓貼文／缺漏影片連結」，統計 `unresolved_reviews` 回報 Orchestrator。
5. **Subagent 4 — Independent Citation & Link Auditor（獨立引用取證與導覽稽核員）**
   * **專屬所有權**：與執筆員（Subagent 2）角色隔離，唯讀 `01_raw/`、`02_review/*.review.md`、原始頁面／貼文佐證與 Linter 報告（**嚴禁由原執筆 Agent 自我稽核放行**）。
   * **職責**（`promote` 前強制關卡）：
     * **三方引用與時間軸核實**：逐卡覆核「**正文陳述 ↔ `> 🔍 [原文佐證]`／`source` ↔ 貼文時間戳（必須檢核該文發表於改版開服之前或之後）**」，攔截開服前猜測／試算／疑問推測寫成既定事實、跨來源混歸（如 FB 誤標 `Namuwiki`）、跨活動混植（如精靈高中混入魔法高中）與反推不存在的韓／英文譯名。
     * **禁止矯枉過正複檢**：查核所有勘誤條目是否遵守「無證據不反推肯定」原則——缺乏正向或反向官方／實機證據者，強制退回維持「**官方公告未明載／尚待實測確認**」。
     * **`resource_links` 品質過濾守門**：逐筆核驗 `resource_links` 僅收錄具系統性價值的官方公告、完整圖文／數據攻略、雲端整理或實戰影片；凡個人心得／漏箱抱怨文（如 FB `3437176536574704`）、單純徵隊友貼、一句話尋路求助、純提問／Bug 閒聊貼，一律自 `resource_links` 剔除（若有考據價值僅准移入 `> 🔍 [原文佐證]`）。
     * **出具簽核結論**：回報缺失清單予 Orchestrator 退修；僅當獨立稽核全數通過、人類核准且 Linter `check` 零 Error、零未決 `> ⚠️` 後，方由 Orchestrator 執行 `scripts/wiki_linter.py promote <review_file> --target <目標檔>.md`。

---

## 📊 執行進度追蹤表 (Execution Progress Tracker)

- [x] **Phase 0：實作計畫建檔**
  - [x] 建立 `docs/WIKI_IMPLEMENTATION_PLAN.md` 並納入雙模型 SOP 與進度追蹤表
- [x] **Phase 1：底層檢索與 Runtime 熱修補引擎升級**
  - [x] 升級 [engines/game_wiki_engine.py](../engines/game_wiki_engine.py) Facade / [engines/game_wiki/](../engines/game_wiki/)（YAML V2 座標、防 `EP1` 撞車、模糊提問消歧義、`subject+aspects` 加權、`supersedes` 軟屏蔽、熱修補置頂區塊、來源與基準時間顯示）
  - [x] 升級 [cogs/wiki_cog.py](../cogs/wiki_cog.py) 與 [cogs/wiki_views.py](../cogs/wiki_views.py)（新增 `!wiki replace` / `hotfix` 指令、互動檢視面板與即時回復機制）
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
- [x] **Phase 4：Priority 2 —— Cafe 攻略用語集補完 ＋ 9 週年（`anniv9`）＋ 聖誕節活動（`christmas`）**
  - [x] **Phase 4.0**：Schema V2.1 多連結導覽（`resource_links`）、座標鎖定、Linter 驗證與單元測試升級
  - [x] **Phase 4.1**：Cafe 攻略用語集補完 —— 共 `26` 張微知識卡（`glossary_p41_general_events.review.md` 9 卡 ＋ `glossary_p41_pve.review.md` 9 卡 ＋ `glossary_p41_pvp.review.md` 8 卡）已完成人工確認，並對齊 Discord `1141581400307400795`（322 張 Division 強化能力圖鑑）、Notion APO 失物招領翻譯（63 件失物招領，歸檔於 `data/wiki_staging/01_raw/apo_notion_ref.txt`）、Notion 成就資料庫（`emblems_notion_ref.txt`）與巴哈舊台服圖鑑（`tw_legacy_bahamut_ref.txt`），正式透過 `scripts/wiki_linter.py promote`（未使用 `--force`）入庫至 [cafe_namu_glossary.md](../data/game_wiki/cafe_namu_glossary.md)（`entries=26, unresolved_reviews=0, errors=0, warnings=0`）
  - [x] **Phase 4.2**：9 週年專區 —— 共 `15` 張微知識卡（`anniv9_b1_overview_versions.review.md` 7 卡 ＋ `anniv9_b2_quests_bosses_nav.review.md` 8 卡）已完成人工核對並對齊 Notion 成就譯名，正式透過 `scripts/wiki_linter.py promote`（未使用 `--force`）入庫至 [anniv9_magic_high.md](../data/game_wiki/anniv9_magic_high.md)（`entries=15, unresolved_reviews=0, errors=0, warnings=0`）
  - [x] **Phase 4.3**：聖誕節活動專區 —— 共 `10` 張微知識卡（`christmas_b1_recent_2024_2026.review.md` 5 卡 ＋ `christmas_b2_classic_2014_2023.review.md` 5 卡）已完成人工核對並對齊 Notion 成就譯名，正式透過 `scripts/wiki_linter.py promote`（未使用 `--force`）入庫至 [christmas_events.md](../data/game_wiki/christmas_events.md)（`entries=10, unresolved_reviews=0, errors=0, warnings=0`）
  - [x] **外部參考來源歸檔與溯源規範**：Notion 成就／徽章參考頁（`2171322382528044bd90d628960fcc35` / `2121322382528009a5b4e5e78119c77d`，757 筆唯一成就）已歸檔於 [emblems_notion_ref.txt](../data/wiki_staging/01_raw/emblems_notion_ref.txt)（特殊成就自訂 `編號` 不可靠，一律改採 `加入日期`/`新增日期`，且上游來自 Namuwiki 須交叉比對）；巴哈姆特舊台服圖鑑（`bsn=30876&snA=54`）已歸檔於 [tw_legacy_bahamut_ref.txt](../data/wiki_staging/01_raw/tw_legacy_bahamut_ref.txt)
- [ ] **Phase 4.4（2026-11-26 擴充／前夜祭與速刷攻略補遺）**
  - [x] **Wave 3 速刷攻略與 11/26 首日補遺正式入庫**：新增 [anniv9_b3_2026_update_and_farming.review.md](../data/wiki_staging/02_review/anniv9_b3_2026_update_and_farming.review.md)（5 卡併入 [anniv9_magic_high.md](../data/game_wiki/anniv9_magic_high.md) 計 `20` 卡）與 [christmas_b3_express2_and_farming.review.md](../data/wiki_staging/02_review/christmas_b3_express2_and_farming.review.md)（6 卡併入 [christmas_events.md](../data/game_wiki/christmas_events.md) 計 `16` 卡，含《聖誕快車 2》26 關變體／24 公仔配方／前夜祭 100 公仔速刷與歷代聖誕新舊 `코노` 對照）
  - [x] **Wave 4 全量引用取證稽核與勘誤（`62` 卡）＋ 導覽連結品質過濾 ＋ 11/26 深夜開服實況對照**：完成 Phase 4 全部 `62` 張微知識卡（[cafe_namu_glossary.md](../data/game_wiki/cafe_namu_glossary.md) `26` 卡 ＋ [anniv9_magic_high.md](../data/game_wiki/anniv9_magic_high.md) `20` 卡 ＋ [christmas_events.md](../data/game_wiki/christmas_events.md) `16` 卡）之「正文 ↔ 原文佐證 ↔ 貼文時間戳」全面取證稽核與勘誤（貫徹零臆測、禁止矯枉過正／反推肯定原則），完成 `resource_links` 導覽連結品質過濾（嚴格剔除無實質攻略價值之個人抱怨／徵友／閒聊／純提問貼文），並完成 `11/26` 深夜開服實況對照（`드래곤 하트 조각`／`루나디아의 월광`／`용의 관문`／《聖誕快車 2》Express 2 前夜祭首晚回報），同步將三項永久防幻覺稽核鐵律寫入本實作計畫
  - [ ] **後續追蹤**：待開服後進一步實測回報出爐，補齊仍待確認之開服後指標（`블랙 해츨링` 確切盧恩定價、開服後冒險日誌魔力券實測驗證、刻印後 `60사이클` 實戰計時，以及 `12/10` 聖誕商店活動幣與殘幣規則）
- [ ] **Phase 5：Priority 3 —— 世界觀與主線劇情（經典總覽、核心勢力、經典進擊與人物關係骨架）**
- [ ] **Phase 6：Priority 4 —— 《Reboot（重製版）》專區（1部《破滅的裂隙》/ 2部《絕望的種子》/ 3部《鬥志的火種》機制、劇情變更與速刷／攻略導覽）**
- [ ] **Phase 7：Priority 5 —— 《Division》系列（D1～D3 副本攻略、速刷效率、劇情與既有黑話串聯）**
- [ ] **Phase 8：Priority 6 —— 第一季（S1）武器技能定位（拆解 S1/S2 武器總表）與 PVE 副本速刷攻略（Heroes / JinJom Classic / APO）**
- [ ] **Phase 9：Priority 7 —— 其他平行世界（精靈高／銀河高／魔法高／像素高）與其餘韓服速刷／生態 Q&A**
