"""Dynamic !help overview and topic embed builders."""

from typing import Optional

import discord

from core.config import BotSettings


def build_help_overview_embed(settings: BotSettings) -> discord.Embed:
    """Build the main !help Embed dynamically according to enabled feature flags."""
    if settings.enable_japanese_mode:
        desc = "平時可直接聊天、傳圖片/貼圖互動或進行日語共學。常用指令如下："
    else:
        desc = "平時可直接聊天、傳圖片/貼圖/短影片互動，或隨時開啟 TRPG 跑團。常用指令如下："

    embed = discord.Embed(
        title=f"📖 {settings.bot_instance_name} 指令速查表",
        description=desc,
        color=0x5865F2,
    )

    if settings.enable_game_wiki:
        embed.add_field(
            name="🧟 《殭屍學園》知識庫 (`!wiki` / `!圖鑑`)",
            value=(
                "`!wiki <關鍵字>` — 互動分頁／下拉切換查詢黑話、角色、模式與重點摘錄\n"
                "`!wiki 目錄 [分類]` — 開啟分類目錄瀏覽（也可直接查 `!wiki 角色`、`!wiki 黑話`）\n"
                "`!wiki add <關鍵字> | <內容>` — 單筆補充設定或黑話\n"
                "`!wiki import` — 貼上長文或**夾帶 `.txt`/`.md` 檔**批次擴充資料庫\n"
                "`!wiki replace <ID或詞> | <新內容>` — 改版勘誤覆寫舊條目（熱修補）\n"
                "`!wiki list` / `!wiki del <編號>` — 查看或刪除群友補充／撤銷覆寫"
            ),
            inline=False,
        )

    embed.add_field(
        name="📖 設定集與常駐記憶 (`!lore` / `!memory`)",
        value=(
            "`!lore` / `!lore add <詞> | <內容>` — 查看或新增通用世界觀設定\n"
            "`!lore import` — 貼文或夾帶 `.txt`/`.md` 批次匯入世界觀\n"
            "`!memory` / `!remember <內容>` / `!forget <編號>` — 管理常駐記憶"
        ),
        inline=False,
    )

    embed.add_field(
        name="🗡️ TRPG 跑團冒險 (`!rpg` / `!sheet` / `!roll`)",
        value=(
            "`!rpg start [主題]` — 開啟專屬討論串跑團（遇到檢定會自動貼 **🎲 互動擲骰按鈕**，點擊即由 DM 續寫）\n"
            "`!rpg join` / `!rpg leave` — 主動加入或單獨退出當前冒險隊伍\n"
            "`!rpg` / `!party` — 查看個人狀態卡 ／ 視覺化小隊儀表板圖卡\n"
            "`!sheet [文字或附檔]` — 查看或匯入自適應視覺角色卡（匯入即入隊）\n"
            "`!save <名>` / `!load <名>` / `!saves` — 存檔與讀檔\n"
            "`!note <線索>` / `!journal` / `!roll [1d20]` — 手記與手動擲骰"
        ),
        inline=False,
    )

    if settings.enable_board_games:
        embed.add_field(
            name="🎮 遊戲大廳・桌遊與80億分之1 (`!play` / `!life` ＋ 按鈕控制)",
            value=(
                "`!play` — 開啟遊戲大廳（黑白棋、西洋棋、四子棋、五子棋、🌍 **80億分之1**）\n"
                "`!life` / `!life top` / `!life atlas` — **80億分之1** 深度版（含特質/檢定/副行動/跨國移居/混合與AI模式）與排行榜"
            ),
            inline=False,
        )

    if settings.enable_flashcard_mode:
        embed.add_field(
            name="🌸 日語單字、跟讀與搶答 (`!card` / `!shadow` / `!quiz`)",
            value=(
                "`!card` / `!card add <單字>` — 互動單字卡（含 `🎙️ 跟讀練習` 按鈕）／ 新增單字\n"
                "`!shadow [日文句]` — 口說跟讀發音評測（亦可直接於日語頻道傳語音訊息）\n"
                "`!card decks` / `!quiz [牌組] [題數]` — 查看牌組 ／ 多人搶答"
            ),
            inline=False,
        )

    if settings.enable_voice_tts:
        embed.add_field(
            name="🔊 語音朗讀與多聲線 (`!join` / `!voice` / `!botvoice` / `!tts`)",
            value=(
                "`!join` / `!leave` / `!say <字>` / `!skip` — 進出語音房、即時朗讀與跳過\n"
                "`!voice` — 個人選聲線（免費 Edge-TTS 16 聲線＋語速音高）｜`!voiceclone` 申請專屬聲線說明\n"
                "`!voice bot [jp]` (`!botvoice`) — 設定機器人自己說話的聲線與語速音高（一般／日語）\n"
                "`!tts status` / `!tts channel` / `!tts preload` / `!tts keepalive` — 狀態、頻道與主機 F5-TTS 顯存控制"
            ),
            inline=False,
        )

    embed.add_field(
        name="🛠️ 待辦、提醒、回饋與系統管理 (`!todo` / `!remind` / `!feedback` / `!setup`)",
        value=(
            "`!todo` / `!todo add <事項>` / `!todo done <編號>` — 待辦清單\n"
            "`!remind 10m <事項>` / `!reminds` — 定時提醒與清單\n"
            "`!feedback [分類] <內容>` (`!建議`) / `!feedback list` — 提交建議或問題回報（亦支援直接口語回饋）與查看公開進度看板\n"
            "`!reset` — 清除當前頻道／討論串的對話上下文\n"
            "`!reload` — 重新檢查並載入人設提示詞與知識庫設定（管理員）\n"
            "`!setup` / `!setup check` / `!models` — 擁有者互動設定面板、一鍵環境健檢與模型輪替狀態"
        ),
        inline=False,
    )

    footer_hints = ["!help rpg", "!help lore"]
    if settings.enable_voice_tts:
        footer_hints.append("!help tts")
    if settings.enable_game_wiki:
        footer_hints.insert(0, "!help wiki")
    footer_hints.append("!help system")
    embed.set_footer(text=f"💡 輸入 {'、'.join(footer_hints)} 可查看該分類的詳細教學與範例！")
    return embed


def build_wiki_help_embed() -> discord.Embed:
    """Build the detailed !help wiki Embed."""
    embed = discord.Embed(
        title="🧟 《殭屍學園》知識庫 (`!wiki`) 詳細指南",
        description="查詢韓服黑話與設定，或直接丟文本／傳檔案擴充資料庫（免重啟立即生效）：",
        color=0x2ECC71,
    )
    embed.add_field(
        name="🔍 查詢指令（別名：`!圖鑑`、`!黑話`、`!좀고`）",
        value=(
            "`!wiki` — 查看收錄統計並使用分類下拉選單瀏覽\n"
            "`!wiki <關鍵字>` — 互動分頁查詢黑話、角色、技能與點位（如 `!wiki 燒瓶 2技`、`!wiki ㅎㅇ`）\n"
            "`!wiki 目錄 [分類]` — 瀏覽全分類條目清單（也可直接輸入 `!wiki 角色`、`!wiki 黑話`）"
        ),
        inline=False,
    )
    embed.add_field(
        name="📥 補充資料庫的 4 種方式",
        value=(
            "1️⃣ **直接貼無標示長文**：輸入 `!wiki import` 換行貼上觀念心得，自動分段抽詞建檔\n"
            "2️⃣ **上傳檔案**：輸入 `!wiki import [主題]` 並夾帶 `.txt` / `.md` 檔案上傳\n"
            "3️⃣ **多段標題格式**：輸入 `!wiki import` 換行貼上多段 `【標題】內容` 或 `# 標題`\n"
            "4️⃣ **單筆快速新增**：`!wiki add 關鍵字1, 關鍵字2 | 內容說明`"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛠️ 勘誤覆寫與管理條目",
        value=(
            "`!wiki replace <ID或詞> | <新內容>` — 改版勘誤覆寫並軟屏蔽舊條目\n"
            "`!wiki list` — 查看群友補充／熱修補條目與編號\n"
            "`!wiki del <編號>` — 刪除補充條目或撤銷覆寫還原舊條目（如 `!wiki del contrib_1`）"
        ),
        inline=False,
    )
    return embed


def build_rpg_help_embed() -> discord.Embed:
    """Build the detailed !help rpg Embed."""
    embed = discord.Embed(
        title="🗡️ TRPG 跑團與角色卡 (`!rpg`) 詳細指南",
        color=0xF15BB5,
    )
    embed.add_field(
        name="🎲 開局與互動檢定",
        value=(
            "`!rpg start [主題]` — 建立專屬討論串開局（別名：`!adv`）\n"
            "• 遇到關鍵行動會出現 **🎲 互動檢定按鈕**，點擊即自動加值擲骰並由 DM 續寫！\n"
            "`!roll [骰子]` — 手動擲骰檢定（如 `!roll 1d20`、`!roll 2d6+3`）\n"
            "`!rpg reset` — 重置當前頻道的冒險狀態"
        ),
        inline=False,
    )
    embed.add_field(
        name="👤 角色卡與視覺面板",
        value=(
            "`!rpg` — 查看個人簡要狀態卡（HP、地點、背包）\n"
            "`!sheet` — 查看個人**視覺化角色狀態圖卡**（自動套用修仙/賽博/CoC/奇幻配色）\n"
            "`!sheet <文字或附檔>` — 貼上文字或上傳 `.txt`/`.md` 匯入角色卡\n"
            "`!rpg name <名>` / `!rpg class <職業>` — 修改名字或職業\n"
            "`!party` — 查看**小隊全員視覺儀表板**與血量裝備"
        ),
        inline=False,
    )
    embed.add_field(
        name="💾 存讀檔與手記",
        value=(
            "`!save <名稱>` / `!load <名稱>` — 保存或載入全隊進度\n"
            "`!saves` / `!rpg delsave <名稱>` — 查看或刪除存檔\n"
            "`!note <線索>` / `!journal` / `!rpg delnote <編號>` — 管理冒險手記"
        ),
        inline=False,
    )
    return embed


def build_lore_help_embed() -> discord.Embed:
    """Build the detailed !help lore / !memory Embed."""
    embed = discord.Embed(
        title="📖 通用設定集 (`!lore`) 與常駐記憶 (`!memory`) 指南",
        color=0x00BBF9,
    )
    embed.add_field(
        name="🌍 通用／跑團設定集（提及關鍵字時自動喚醒）",
        value=(
            "`!lore` — 查看所有世界觀設定\n"
            "`!lore add <關鍵字> | <內容>` — 新增單筆設定\n"
            "`!lore import` — 貼上多段 `【標題】內容` 或夾帶 `.txt`/`.md` 批次匯入\n"
            "`!lore del <編號>` — 刪除單筆設定 ｜ `!lore clear` — 清空設定集"
        ),
        inline=False,
    )
    embed.add_field(
        name="🧠 全群常駐記憶（每次對話固定載入，適合簡短共識）",
        value=(
            "`!memory` — 查看常駐記憶清單\n"
            "`!remember <內容>` — 新增一筆常駐記憶\n"
            "`!forget <編號>` — 刪除指定編號的記憶"
        ),
        inline=False,
    )
    return embed


def build_tts_help_embed(settings: BotSettings) -> discord.Embed:
    """Build the detailed !help tts Embed."""
    embed = discord.Embed(
        title="🔊 語音頻道朗讀與多聲線 (`!voice` / `!tts`) 指南",
        description="預設使用 0 顯存 Edge-TTS，可選配進階特色聲線；進房後自動代說並朗讀日語教學與單字卡。",
        color=0x38BDF8,
    )
    if settings.enable_user_voice_clone:
        if settings.tts_clone_approval_mode != "manual":
            clone_help_line = "`!voiceclone [public] [1|2] [名稱|] <逐字稿>` (+附檔) — 自助建立私人克隆（安檢通過立即啟用；加 `public` 可申請公開，支援 `list`/`public`/`delete`）\n"
        else:
            clone_help_line = "`!voiceclone [public] [1|2] [名稱|] <逐字稿>` (+附檔) — 申請專屬克隆（安全檢查 + 擁有者審核；支援 `list`/`delete`/`cancel`）\n"
    else:
        clone_help_line = "`!voiceclone` (`!clone`) — 查看專屬語音克隆申請說明（需聯絡機器人擁有者設定）\n"

    embed.add_field(
        name="👤 全體成員可用（個人聲線、機器人聲線與語音控制）",
        value=(
            "`!join` / `!leave` (`!dc`) — 加入或退出你所在的語音頻道\n"
            "`!say <文字>` / `!skip` — 立即朗讀指定文字 ／ 跳過目前語音\n"
            "`!voice` — 開啟互動下拉選單切換個人聲線（內建 16 種免費 Edge-TTS）\n"
            "`!voice rate <-50..+50>` / `!voice pitch <-30..+30>` / `!voice reset` — 微調個人語速音高或重設\n"
            "`!voice bot [jp]` (`!botvoice`) — 設定機器人 AI 朗讀的聲線（一般對話／日語共學，支援 `rate` / `pitch` / `reset`）\n"
            + clone_help_line
            + "`!tts status` / `!tts channels` — 查看引擎狀態與目前代說對象頻道"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ 伺服器管理員專用（頻道範圍與朗讀策略）",
        value=(
            "`!tts channel add/remove/clear [#頻道]` — 新增、移除或清空代說對象頻道\n"
            "`!tts bind [#頻道]` / `!tts unbind` — 單頻道綁定 ／ 恢復預設語音內建文字區\n"
            "`!tts ignore [#頻道]` / `!tts unignore [#頻道]` — 忽略指定頻道\n"
            "`!tts ai [on|off|jp|general]` / `!tts proxy [on|off]` / `!tts vconly [on|off]` / `!tts clear` — 切換 AI/代說/同房限制與清佇列"
        ),
        inline=False,
    )
    owner_clone_line = (
        "`!voiceclone pending` / `approve` / `reject` / `revoke` / `ban` — 審核、撤銷或封鎖成員克隆申請"
        if settings.enable_user_voice_clone
        else "🔒 私人語音克隆目前僅限機器人擁有者設定，不開放線上申請。"
    )
    embed.add_field(
        name="👑 機器人擁有者專用（主機資源與克隆審核）",
        value=(
            "`!tts preload` / `!tts unload` — 立即預載 F5-TTS 到顯存 ／ 手動釋放 VRAM\n"
            "`!tts keepalive [on|off|<秒>]` / `!tts reloadvoices` — 切換顯存常駐或按需卸載 ／ 重載聲線\n"
            + owner_clone_line
        ),
        inline=False,
    )
    return embed


def build_system_help_embed() -> discord.Embed:
    """Build the !help system / feedback / owner diagnostics Embed."""
    embed = discord.Embed(
        title="🛠️ 待辦、提醒、回饋信箱與系統管理 (`!todo` / `!feedback` / `!models`) 指南",
        color=0x95A5A6,
    )
    embed.add_field(
        name="📋 待辦與定時提醒（全體可用）",
        value=(
            "`!todo` / `!todo add <事項>` / `!todo done <編號>` — 管理待辦清單（`!todo all` 查看歷史）\n"
            "`!remind <時間> <事項>` — 設定提醒（支援 `10m`、`2h`、`18:30`、`2026-10-31 15:00`）\n"
            "`!reminds` / `!remind del <編號>` — 查看或取消預約中的提醒"
        ),
        inline=False,
    )
    embed.add_field(
        name="📮 非即時回饋與建議信箱 (`!feedback` / `!fb` / `!回報` / `!建議`)",
        value=(
            "💬 **口語自然回饋**：在聊天時直接說「建議...」、「希望能新增...」或「這裡 Wiki／功能有錯」即自動收錄（不注入聊天記憶、不點名回報者）。\n"
            "`!feedback [wiki|bug|feature|ux] <內容>` / `!建議 <內容>` — 提交回饋（若以**回覆**訊息方式提交會自動截取原訊息脈絡）\n"
            "`!feedback list [open|all|分類]` / `!feedback view <編號>` — **全體公開**查看處理看板與詳情\n"
            "`!feedback done/triage/close/reopen <編號> [備註]` / `export` / `del` — **管理員／擁有者**管理與匯出"
        ),
        inline=False,
    )
    embed.add_field(
        name="🛡️ 管理員與擁有者系統指令",
        value=(
            "`!reset` (`!clear`) — 清除當前頻道或討論串的對話上下文（社群模式限管理員／擁有者）\n"
            "`!reload` (`!重載`) — 熱重載人設提示詞、知識庫與設定（管理員／擁有者）\n"
            "`!setup` / `!setup check` — **擁有者專用**：免改 `.env` 互動設定面板（開關、頻道選擇、模型與一鍵環境健檢）\n"
            "`!models` (`!模型`) — **擁有者專用**：檢視 Flash-Lite 模型輪替鏈、429 冷卻狀態與 AI Studio 即時可用模型"
        ),
        inline=False,
    )
    return embed


def build_help_topic_embed(
    settings: BotSettings,
    topic: Optional[str] = None,
) -> tuple[Optional[discord.Embed], Optional[str]]:
    """Return `(embed, disabled_notice)` for `!help [topic]`."""
    sub = (topic or "").strip().lower()

    if sub in ("wiki", "圖鑑", "黑話", "좀고", "知識庫", "補充", "import", "replace", "勘誤"):
        if not settings.enable_game_wiki:
            return None, "ℹ️ 此伺服器目前未啟用《殭屍學園》知識庫模組。"
        return build_wiki_help_embed(), None

    if sub in ("rpg", "trpg", "adv", "sheet", "跑團", "冒險", "角色卡"):
        return build_rpg_help_embed(), None

    if sub in ("lore", "memory", "mem", "設定", "世界觀", "記憶"):
        return build_lore_help_embed(), None

    if sub in ("tts", "voice", "voiceclone", "clone", "克隆", "語音", "聲音", "朗讀"):
        if not settings.enable_voice_tts:
            return None, "ℹ️ 此伺服器目前未啟用語音頻道 TTS 模組。"
        return build_tts_help_embed(settings), None

    if sub in ("system", "sys", "owner", "models", "model", "setup", "config", "健檢", "面板", "todo", "remind", "admin", "系統", "擁有者", "模型", "管理", "feedback", "fb", "回報", "建議", "意見"):
        return build_system_help_embed(), None

    return build_help_overview_embed(settings), None
