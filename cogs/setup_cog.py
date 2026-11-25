"""Owner-only interactive !setup configuration panel & environment health-check Cog."""

from __future__ import annotations

import asyncio
import logging
import shutil
import urllib.parse
from typing import Any, Optional

import discord
import httpx
from discord.ext import commands
from google.genai import types

from core.chat_pipeline import is_target_channel
from core.config import (
    BASE_DIR,
    DEFAULT_FALLBACK_MODELS,
    DEFAULT_FINAL_FALLBACK_MODEL,
    DEFAULT_PRIMARY_MODEL,
    FLASH_LITE_PRESET_MODELS,
    STRICT_FLASH_LITE_FALLBACK_MODELS,
    BotSettings,
    apply_runtime_overrides,
    init_runtime_env,
    is_bot_owner,
    load_settings,
    truncate_embed_text,
)
from core.llm_client import (
    generate_with_fallback,
    get_active_model_cooldowns,
    reset_model_cooldowns,
)
from engines import assistant_db

_FEATURE_TOGGLE_SPECS: tuple[tuple[str, str, str, int], ...] = (
    ("enable_japanese_mode", "🇯🇵 日語教學", "中日雙語教學、雙頻道鏡像與自動朗讀", 1),
    ("enable_flashcard_mode", "🎴 單字測驗", "SRS 單字卡、Anki 匯入與 Kotoba 搶答", 1),
    ("enable_game_wiki", "🧟 學園百科", "《殭屍學園》知識庫與圖片雙階段翻譯", 1),
    ("enable_board_games", "🎲 棋盤桌遊", "黑白棋/西洋棋/五子棋/四子棋 & 80億分之1", 2),
    ("enable_voice_tts", "🔊 語音TTS", "語音頻道朗讀與可選 F5-TTS 聲線克隆", 2),
    ("enable_safety_guardrail", "🛡️ 安全護欄", "防 Prompt 注入、長度限制與防濫用護欄", 2),
)

_TEXT_CHANNEL_PERM_LABELS: tuple[tuple[str, str], ...] = (
    ("send_messages", "發送訊息"),
    ("embed_links", "嵌入連結"),
    ("attach_files", "附加檔案"),
    ("read_message_history", "讀取訊息歷史"),
    ("create_public_threads", "建立公開討論串"),
)

_RESTORABLE_SETTINGS_FIELDS: tuple[str, ...] = (
    "target_channel_id",
    "tts_channel_id",
    "general_channel_id",
    "enable_japanese_mode",
    "enable_flashcard_mode",
    "enable_game_wiki",
    "enable_board_games",
    "enable_voice_tts",
    "enable_safety_guardrail",
    "primary_model",
    "fallback_models",
    "final_fallback_model",
    "tts_ai_auto_read_jp",
    "tts_ai_auto_read_general",
)

_VALID_TABS: tuple[tuple[str, str], ...] = (
    ("features", "🧩 功能開關"),
    ("channels", "📺 頻道綁定"),
    ("models", "🤖 模型策略"),
    ("health", "🩺 環境健檢"),
)


def _resolve_guild_bot_member(bot: Any, guild: Any) -> Any:
    """Resolve the Bot's Member object in a guild without assuming `guild.me` is populated."""
    if guild is None:
        return None
    me = getattr(guild, "me", None)
    if me is not None:
        return me
    bot_user = getattr(bot, "user", None)
    if isinstance(guild, discord.Guild) and bot_user is not None:
        try:
            return guild.get_member(bot_user.id)
        except Exception:
            return None
    return None


def _resolve_channel_obj(bot: Any, guild: Any, fallback_channel: Any, channel_id: int) -> Any:
    """Resolve a configured Discord channel by ID across bot cache, current context, and guild."""
    get_ch = getattr(bot, "get_channel", None)
    if callable(get_ch):
        ch = get_ch(channel_id)
        if ch is not None:
            return ch
    if fallback_channel is not None and getattr(fallback_channel, "id", None) == channel_id:
        return fallback_channel
    if isinstance(guild, discord.Guild):
        ch = guild.get_channel_or_thread(channel_id)
        if ch is not None:
            return ch
    return None


def _check_ffmpeg(settings: BotSettings) -> dict[str, Any]:
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return {
            "name": "FFmpeg 語音轉檔工具",
            "ok": True,
            "detail": f"已就緒（`{ffmpeg_path}`）",
            "fix_hint": "",
        }
    if settings.enable_voice_tts:
        return {
            "name": "FFmpeg 語音轉檔工具",
            "ok": False,
            "detail": "未偵測到 ffmpeg，語音頻道播放與語音克隆無法運作",
            "fix_hint": "請安插 ffmpeg 到系統 PATH，或從 !setup 關閉語音 TTS",
        }
    return {
        "name": "FFmpeg 語音轉檔工具",
        "ok": True,
        "detail": "未偵測到 ffmpeg（目前語音 TTS 已關閉，文字對話不受影響）",
        "fix_hint": "若未來想啟用語音朗讀功能，請再安裝 ffmpeg 至系統 PATH",
    }


def _check_discord_text_channels(
    bot: Any,
    settings: BotSettings,
    guild: Any,
    fallback_channel: Any,
) -> dict[str, Any]:
    targets: list[tuple[str, int]] = [("主聊天頻道", settings.target_channel_id)]
    if settings.tts_channel_id:
        targets.append(("日文朗讀頻道", settings.tts_channel_id))
    if settings.general_channel_id:
        targets.append(("一般群聊頻道", settings.general_channel_id))

    missing_channels: list[str] = []
    perm_issues: list[str] = []
    ok_labels: list[str] = []

    for label, cid in targets:
        ch = _resolve_channel_obj(bot, guild, fallback_channel, cid)
        if ch is None:
            missing_channels.append(f"{label} (`{cid}`)")
            continue

        ch_name = getattr(ch, "name", None) or str(cid)
        ch_guild = guild or getattr(ch, "guild", None)
        bot_member = _resolve_guild_bot_member(bot, ch_guild)
        perms_for = getattr(ch, "permissions_for", None)
        if bot_member is not None and callable(perms_for):
            perms = perms_for(bot_member)
            missing_perms = [
                zh for attr, zh in _TEXT_CHANNEL_PERM_LABELS if not bool(getattr(perms, attr, False))
            ]
            if missing_perms:
                perm_issues.append(f"{label} (#{ch_name}) 缺少：{'、'.join(missing_perms)}")
                continue
        ok_labels.append(f"{label} (#{ch_name})")

    if missing_channels or perm_issues:
        parts: list[str] = []
        hints: list[str] = []
        if missing_channels:
            parts.append("找不到頻道：" + "；".join(missing_channels))
            hints.append("請至 !setup 的「📺 頻道綁定」重新選擇有效頻道（或確認機器人已加入該伺服器）")
        if perm_issues:
            parts.append("權限不足：" + "；".join(perm_issues))
            hints.append("請在 Discord 伺服器或頻道設定中為機器人開啟缺少的文字/Embed/附檔/討論串權限")
        return {
            "name": "Discord 頻道文字與討論串權限",
            "ok": False,
            "detail": " ｜ ".join(parts),
            "fix_hint": "；".join(hints),
        }

    return {
        "name": "Discord 頻道文字與討論串權限",
        "ok": True,
        "detail": f"已驗證 {len(ok_labels)} 個綁定頻道（{'、'.join(ok_labels)}），必要權限皆齊全",
        "fix_hint": "",
    }


def _check_discord_voice_perms(bot: Any, settings: BotSettings, guild: Any) -> Optional[dict[str, Any]]:
    if not settings.enable_voice_tts or guild is None:
        return None

    bot_member = _resolve_guild_bot_member(bot, guild)
    if bot_member is None:
        return {
            "name": "Discord 語音頻道權限",
            "ok": False,
            "detail": "無法取得機器人於此伺服器的成員權限資訊",
            "fix_hint": "請確認機器人已正確加入該伺服器並開啟連線權限",
        }

    voice_channels = list(getattr(guild, "voice_channels", None) or [])
    guild_perms = getattr(bot_member, "guild_permissions", None)

    if voice_channels:
        usable_count = 0
        for vc in voice_channels:
            perms_for = getattr(vc, "permissions_for", None)
            vc_perms = perms_for(bot_member) if callable(perms_for) else guild_perms
            if vc_perms is not None and bool(getattr(vc_perms, "connect", False)) and bool(
                getattr(vc_perms, "speak", False)
            ):
                usable_count += 1
        if usable_count == 0:
            missing: list[str] = []
            sample_perms = (
                voice_channels[0].permissions_for(bot_member)
                if callable(getattr(voice_channels[0], "permissions_for", None))
                else guild_perms
            )
            if not bool(getattr(sample_perms, "connect", False)):
                missing.append("連線 (Connect)")
            if not bool(getattr(sample_perms, "speak", False)):
                missing.append("說話 (Speak)")
            if not missing:
                missing = ["連線 (Connect)", "說話 (Speak)"]
            return {
                "name": "Discord 語音頻道權限",
                "ok": False,
                "detail": f"所有語音頻道皆缺少必要權限：{'、'.join(missing)}",
                "fix_hint": "請在伺服器身分組或語音頻道權限中為機器人勾選「連線」與「說話」",
            }
        return {
            "name": "Discord 語音頻道權限",
            "ok": True,
            "detail": f"具備語音播放權限（可用 {usable_count}/{len(voice_channels)} 個語音頻道）",
            "fix_hint": "",
        }

    if guild_perms is not None:
        missing_guild: list[str] = []
        if not bool(getattr(guild_perms, "connect", False)):
            missing_guild.append("連線 (Connect)")
        if not bool(getattr(guild_perms, "speak", False)):
            missing_guild.append("說話 (Speak)")
        if missing_guild:
            return {
                "name": "Discord 語音頻道權限",
                "ok": False,
                "detail": f"伺服器層級語音權限不足：{'、'.join(missing_guild)}",
                "fix_hint": "請為機器人身分組勾選「連線」與「說話」權限",
            }

    return {
        "name": "Discord 語音頻道權限",
        "ok": True,
        "detail": "伺服器語音連線與說話權限正常",
        "fix_hint": "",
    }


async def _check_gemini_api(
    settings: BotSettings,
    gemini_client: Any,
    logger: logging.Logger,
) -> dict[str, Any]:
    try:
        _resp, used_model = await generate_with_fallback(
            client=gemini_client,
            models_to_try=settings.models_to_try,
            contents="Reply OK",
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=16),
            timeout=10.0,
            context_label="Setup健康檢查",
            logger=logger,
            final_fallback_model=settings.final_fallback_model,
        )
        return {
            "name": "Gemini Flash-Lite API 連線",
            "ok": True,
            "detail": f"連線成功，由 `{used_model}` 回應",
            "fix_hint": "",
        }
    except Exception as exc:
        err_msg = str(exc)
        if settings.gemini_api_key:
            err_msg = err_msg.replace(settings.gemini_api_key, "[REDACTED]")
        return {
            "name": "Gemini Flash-Lite API 連線",
            "ok": False,
            "detail": f"API 測試失敗：{err_msg[:180]}",
            "fix_hint": "請確認 GEMINI_API_KEY 是否有效，或至「🤖 模型策略」切換模型 / 清除 429 冷卻",
        }


def _f5_admin_headers(api_token: str) -> dict[str, str]:
    tok = (api_token or "").strip()
    return {"X-TTS-Admin-Token": tok} if tok else {}


def _probe_f5_http_sync(url: str) -> int:
    resp = httpx.get(url, timeout=2.0, follow_redirects=False)
    return int(resp.status_code)


def _probe_f5_health_sync(url: str, api_token: str = "") -> tuple[int, Optional[dict[str, Any]]]:
    target = f"{url.rstrip('/')}/"
    resp = httpx.get(
        target,
        headers=_f5_admin_headers(api_token),
        timeout=2.0,
        follow_redirects=False,
    )
    status_code = int(resp.status_code)
    payload: Optional[dict[str, Any]] = None
    if status_code < 400 and callable(getattr(resp, "json", None)):
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                payload = parsed
        except Exception:
            payload = None
    return status_code, payload


def _post_f5_admin_sync(
    base_url: str,
    path: str,
    *,
    api_token: str = "",
    data: Optional[dict[str, str]] = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    clean_base = (base_url or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(clean_base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("TTS_API_URL 格式無效")
    resp = httpx.post(
        f"{clean_base}{path}",
        headers=_f5_admin_headers(api_token),
        data=data,
        timeout=timeout,
        follow_redirects=False,
    )
    if int(resp.status_code) >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    if callable(getattr(resp, "json", None)):
        try:
            body = resp.json()
            return body if isinstance(body, dict) else {}
        except Exception:
            return {}
    return {}


async def _check_f5_tts(settings: BotSettings) -> Optional[dict[str, Any]]:
    raw_url = (settings.tts_api_url or "").strip()
    if not settings.enable_voice_tts or not raw_url:
        return None

    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return {
            "name": "本地 F5-TTS 語音克隆服務",
            "ok": False,
            "detail": f"TTS_API_URL 格式無效（僅支援 http/https）：`{raw_url[:80]}`",
            "fix_hint": "請修正 .env 中的 TTS_API_URL（例如 http://127.0.0.1:9880）",
        }

    try:
        status_code, payload = await asyncio.to_thread(
            _probe_f5_health_sync,
            raw_url,
            settings.tts_api_token,
        )
        if status_code < 400:
            if payload:
                gpu_label = payload.get("gpu_name") or payload.get("device") or "unknown"
                used_vram = payload.get("vram_allocated_gb", 0.0)
                total_vram = payload.get("total_vram_gb", 0.0)
                vram_str = (
                    f"{used_vram} / {total_vram} GB"
                    if isinstance(total_vram, (int, float)) and total_vram > 0
                    else f"{used_vram} GB"
                )
                loaded = bool(payload.get("model_loaded", False))
                loading = bool(payload.get("model_loading", False))
                state_txt = "已載入" if loaded else ("載入中..." if loading else "待命 0 VRAM")
                idle_sec = payload.get("idle_unload_seconds")
                residency_txt = (
                    "常駐不卸載"
                    if idle_sec == 0
                    else (f"閒置 {idle_sec}s 卸載" if idle_sec is not None else "按需自動釋放")
                )
                offline_txt = "開啟" if payload.get("offline_first", True) else "關閉"
                fix_hint = ""
                if (
                    isinstance(total_vram, (int, float))
                    and total_vram >= 10
                    and isinstance(idle_sec, int)
                    and idle_sec > 0
                ):
                    fix_hint = "偵測到充裕顯存且目前為閒置卸載模式，可點擊下方「🔥 F5 常駐預載」享零延遲朗讀"
                return {
                    "name": "本地 F5-TTS 語音克隆服務",
                    "ok": True,
                    "detail": (
                        f"已連線（`{gpu_label}` | {state_txt} | VRAM `{vram_str}` | "
                        f"{residency_txt} | 離線快取直讀：{offline_txt}）"
                    ),
                    "fix_hint": fix_hint,
                }
            return {
                "name": "本地 F5-TTS 語音克隆服務",
                "ok": True,
                "detail": f"已連線本地 F5-TTS（`{raw_url}`，HTTP {status_code}）",
                "fix_hint": "",
            }
        return {
            "name": "本地 F5-TTS 語音克隆服務",
            "ok": True,
            "detail": f"本地 F5-TTS 回應 HTTP {status_code}（Edge TTS 仍可正常運作，僅 F5-TTS 未啟動或尚未就緒）",
            "fix_hint": "免顯卡 Edge-TTS 會自動接手一般朗讀；僅需自訂聲音克隆時才需啟動 F5-TTS",
        }
    except Exception:
        return {
            "name": "本地 F5-TTS 語音克隆服務",
            "ok": True,
            "detail": "尚未偵測到本地 F5-TTS（Edge TTS 仍可正常運作，僅 F5-TTS 未啟動）",
            "fix_hint": "若不需本地語音克隆可維持現狀，Edge-TTS 已可免費直接朗讀",
        }


async def _check_sqlite_state() -> dict[str, Any]:
    try:
        db_name = assistant_db.get_db_path().name
        overrides = await asyncio.to_thread(assistant_db.get_runtime_config_overrides)
        return {
            "name": "SQLite 動態設定狀態",
            "ok": True,
            "detail": f"資料庫 `{db_name}` 正常運作，目前共套用 `{len(overrides)}` 項即時覆寫",
            "fix_hint": "若想全部恢復為 .env 預設值，可至「🤖 模型策略」一鍵清除覆寫",
        }
    except Exception as exc:
        return {
            "name": "SQLite 動態設定狀態",
            "ok": False,
            "detail": f"讀取動態設定失敗：{str(exc)[:160]}",
            "fix_hint": "請確認 data/ 目錄的讀寫權限與 SQLite 資料庫狀態",
        }


async def run_environment_health_check(
    bot: commands.Bot,
    settings: BotSettings,
    gemini_client=None,
    guild: Optional[discord.Guild] = None,
    channel: Optional[discord.abc.Messageable] = None,
    *,
    include_api_ping: bool = True,
) -> list[dict]:
    """Run non-destructive environment, permission, and optional Gemini API health checks."""
    check_logger = getattr(bot, "logger", None) or logging.getLogger("DiscordGeminiBot.setup")
    eff_guild = guild or getattr(channel, "guild", None)

    results: list[dict] = [
        _check_ffmpeg(settings),
        _check_discord_text_channels(bot, settings, eff_guild, channel),
    ]

    voice_check = _check_discord_voice_perms(bot, settings, eff_guild)
    if voice_check is not None:
        results.append(voice_check)

    if include_api_ping and gemini_client is not None:
        results.append(await _check_gemini_api(settings, gemini_client, check_logger))

    f5_check = await _check_f5_tts(settings)
    if f5_check is not None:
        results.append(f5_check)

    results.append(await _check_sqlite_state())
    return results


class SetupPanelView(discord.ui.View):
    """Owner-only interactive multi-tab View for runtime feature toggles, channels, models, and health checks."""

    def __init__(
        self,
        cog: "SetupCog",
        *,
        active_tab: str = "features",
        health_results: Optional[list[dict]] = None,
        health_included_api: bool = False,
        status_note: str = "",
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.active_tab = active_tab if active_tab in {t[0] for t in _VALID_TABS} else "features"
        self.health_results = health_results
        self.health_included_api = health_included_api
        self.status_note = status_note
        self.message: Optional[discord.Message] = None
        self._rebuild_items()

    async def on_timeout(self) -> None:
        for child in self.children:
            if hasattr(child, "disabled"):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await is_bot_owner(self.cog.bot, self.cog.settings, interaction.user):
            return True
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "🔒 僅限機器人擁有者（Bot Owner）可操作 `!setup` 設定與健檢面板。",
                ephemeral=True,
            )
        return False

    def _rebuild_items(self) -> None:
        self.clear_items()

        # Row 0: 4 tab navigation buttons
        for tab_key, label in _VALID_TABS:
            btn = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.primary if self.active_tab == tab_key else discord.ButtonStyle.secondary,
                row=0,
            )
            btn.callback = self._make_tab_callback(tab_key)
            self.add_item(btn)

        if self.active_tab == "features":
            self._add_feature_items()
        elif self.active_tab == "channels":
            self._add_channel_items()
        elif self.active_tab == "models":
            self._add_model_items()
        elif self.active_tab == "health":
            self._add_health_items()

    def _add_feature_items(self) -> None:
        for key, short_label, _desc, row_idx in _FEATURE_TOGGLE_SPECS:
            enabled = bool(getattr(self.cog.settings, key, False))
            btn = discord.ui.Button(
                label=f"{short_label}：{'開啟' if enabled else '關閉'}",
                style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,
                row=row_idx,
            )
            btn.callback = self._make_toggle_callback(key, short_label)
            self.add_item(btn)

    def _add_channel_items(self) -> None:
        channel_selects: tuple[tuple[str, str, str, int], ...] = (
            ("target_channel_id", "主聊天頻道", "1️⃣ 選擇主聊天頻道 (TARGET_CHANNEL_ID)...", 1),
            ("tts_channel_id", "日文朗讀頻道", "2️⃣ 選擇日文朗讀頻道 (TTS_CHANNEL_ID，可選)...", 2),
            ("general_channel_id", "一般群聊頻道", "3️⃣ 選擇一般群聊頻道 (GENERAL_CHANNEL_ID，可選)...", 3),
        )
        for key, label, placeholder, row_idx in channel_selects:
            sel = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                placeholder=placeholder,
                min_values=1,
                max_values=1,
                row=row_idx,
            )
            sel.callback = self._make_channel_select_callback(sel, key, label)
            self.add_item(sel)

        clear_tts_btn = discord.ui.Button(
            label="🗑️ 清除朗讀頻道綁定",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        clear_tts_btn.callback = self._make_clear_channel_callback("tts_channel_id", "日文朗讀頻道")
        self.add_item(clear_tts_btn)

        clear_general_btn = discord.ui.Button(
            label="🗑️ 清除一般頻道綁定",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        clear_general_btn.callback = self._make_clear_channel_callback("general_channel_id", "一般群聊頻道")
        self.add_item(clear_general_btn)

    def _add_model_items(self) -> None:
        current_primary = self.cog.settings.primary_model
        options = [
            discord.SelectOption(
                label=model_name,
                value=model_name,
                description="目前首選模型" if model_name == current_primary else "點擊切換為首選模型",
                default=(model_name == current_primary),
            )
            for model_name in FLASH_LITE_PRESET_MODELS
        ]
        model_select = discord.ui.Select(
            placeholder="🤖 選擇首選 Gemini 模型 (Flash-Lite / Flash / Hydrogen)...",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )
        model_select.callback = self._make_model_select_callback(model_select)
        self.add_item(model_select)

        reset_chain_btn = discord.ui.Button(
            label="♻️ 重置預設鏈 (Lite+Flash/H)",
            style=discord.ButtonStyle.primary,
            row=2,
        )
        reset_chain_btn.callback = self._on_reset_model_chain
        self.add_item(reset_chain_btn)

        strict_lite_btn = discord.ui.Button(
            label="🪶 切換純 Flash-Lite 鏈",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        strict_lite_btn.callback = self._on_strict_lite_chain
        self.add_item(strict_lite_btn)

        clear_cd_btn = discord.ui.Button(
            label="❄️ 清除冷卻",
            style=discord.ButtonStyle.secondary,
            row=2,
        )
        clear_cd_btn.callback = self._on_clear_cooldowns
        self.add_item(clear_cd_btn)

        clear_all_btn = discord.ui.Button(
            label="🧹 清除全部 SQLite 覆寫",
            style=discord.ButtonStyle.danger,
            row=2,
        )
        clear_all_btn.callback = self._on_clear_all_overrides
        self.add_item(clear_all_btn)

    def _add_health_items(self) -> None:
        run_btn = discord.ui.Button(
            label="🩺 執行完整健檢 (含 API 測試)",
            style=discord.ButtonStyle.success,
            row=1,
        )
        run_btn.callback = self._on_run_full_health
        self.add_item(run_btn)

        if self.cog.settings.enable_voice_tts and (self.cog.settings.tts_api_url or "").strip():
            resident_btn = discord.ui.Button(
                label="🔥 F5 常駐預載",
                style=discord.ButtonStyle.primary,
                row=2,
            )
            resident_btn.callback = self._on_f5_resident_preload
            self.add_item(resident_btn)

            lazy_btn = discord.ui.Button(
                label="💤 F5 省顯存模式",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            lazy_btn.callback = self._on_f5_lazy_mode
            self.add_item(lazy_btn)

            unload_btn = discord.ui.Button(
                label="🧹 釋放 F5 顯存",
                style=discord.ButtonStyle.secondary,
                row=2,
            )
            unload_btn.callback = self._on_f5_unload
            self.add_item(unload_btn)

    def _make_tab_callback(self, tab_key: str):
        async def _cb(interaction: discord.Interaction) -> None:
            self.active_tab = tab_key
            if tab_key == "health":
                await interaction.response.defer()
                if self.health_results is None:
                    self.health_results = await run_environment_health_check(
                        self.cog.bot,
                        self.cog.settings,
                        gemini_client=self.cog.gemini_client,
                        guild=interaction.guild,
                        channel=interaction.channel,
                        include_api_ping=False,
                    )
                    self.health_included_api = False
                self._rebuild_items()
                await interaction.edit_original_response(embed=self.build_embed(), view=self)
                return

            self._rebuild_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return _cb

    def _make_toggle_callback(self, key: str, label: str):
        async def _cb(interaction: discord.Interaction) -> None:
            new_val = not bool(getattr(self.cog.settings, key, False))
            val_str = "true" if new_val else "false"
            await asyncio.to_thread(assistant_db.set_runtime_config_override, key, val_str)
            apply_runtime_overrides(self.cog.settings, {key: val_str})
            changes = await self.cog.sync_feature_cogs()
            self.health_results = None
            state_txt = "開啟" if new_val else "關閉"
            cog_note = f"（模組同步：`{', '.join(changes)}`）" if changes else ""
            self.status_note = f"✅ 已將 **{label}** 切換為 **{state_txt}**{cog_note}"
            self._rebuild_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return _cb

    def _make_channel_select_callback(self, select: discord.ui.ChannelSelect, key: str, label: str):
        async def _cb(interaction: discord.Interaction) -> None:
            if not select.values:
                await interaction.response.send_message("⚠️ 未偵測到選擇的頻道。", ephemeral=True)
                return
            ch_id = int(select.values[0].id)
            await asyncio.to_thread(assistant_db.set_runtime_config_override, key, str(ch_id))
            apply_runtime_overrides(self.cog.settings, {key: str(ch_id)})
            self.health_results = None
            self.status_note = f"✅ 已將 **{label}** 綁定至 <#{ch_id}> (`{ch_id}`)"
            self._rebuild_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return _cb

    def _make_clear_channel_callback(self, key: str, label: str):
        async def _cb(interaction: discord.Interaction) -> None:
            await asyncio.to_thread(assistant_db.set_runtime_config_override, key, "")
            apply_runtime_overrides(self.cog.settings, {key: ""})
            self.health_results = None
            self.status_note = f"🗑️ 已清除 **{label}** 綁定（目前已停用該頻道）"
            self._rebuild_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return _cb

    def _make_model_select_callback(self, select: discord.ui.Select):
        async def _cb(interaction: discord.Interaction) -> None:
            if not select.values:
                return
            chosen = select.values[0]
            await asyncio.to_thread(assistant_db.set_runtime_config_override, "primary_model", chosen)
            apply_runtime_overrides(self.cog.settings, {"primary_model": chosen})
            self.health_results = None
            self.status_note = f"🤖 已將首選模型更新為 `{chosen}`"
            self._rebuild_items()
            await interaction.response.edit_message(embed=self.build_embed(), view=self)

        return _cb

    async def _on_reset_model_chain(self, interaction: discord.Interaction) -> None:
        fallback_csv = ",".join(DEFAULT_FALLBACK_MODELS)
        await asyncio.to_thread(assistant_db.set_runtime_config_override, "primary_model", DEFAULT_PRIMARY_MODEL)
        await asyncio.to_thread(assistant_db.set_runtime_config_override, "fallback_models", fallback_csv)
        await asyncio.to_thread(
            assistant_db.set_runtime_config_override,
            "final_fallback_model",
            DEFAULT_FINAL_FALLBACK_MODEL,
        )
        apply_runtime_overrides(
            self.cog.settings,
            {
                "primary_model": DEFAULT_PRIMARY_MODEL,
                "fallback_models": fallback_csv,
                "final_fallback_model": DEFAULT_FINAL_FALLBACK_MODEL,
            },
        )
        self.health_results = None
        self.status_note = f"♻️ 已重置為預設混合輪替鏈（首選：`{DEFAULT_PRIMARY_MODEL}`，含 Flash / Hydrogen 高峰備援）"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_strict_lite_chain(self, interaction: discord.Interaction) -> None:
        fallback_csv = ",".join(STRICT_FLASH_LITE_FALLBACK_MODELS)
        await asyncio.to_thread(assistant_db.set_runtime_config_override, "primary_model", DEFAULT_PRIMARY_MODEL)
        await asyncio.to_thread(assistant_db.set_runtime_config_override, "fallback_models", fallback_csv)
        await asyncio.to_thread(
            assistant_db.set_runtime_config_override,
            "final_fallback_model",
            DEFAULT_FINAL_FALLBACK_MODEL,
        )
        apply_runtime_overrides(
            self.cog.settings,
            {
                "primary_model": DEFAULT_PRIMARY_MODEL,
                "fallback_models": fallback_csv,
                "final_fallback_model": DEFAULT_FINAL_FALLBACK_MODEL,
            },
        )
        self.health_results = None
        self.status_note = f"🪶 已切換為純 Flash-Lite 輪替鏈（首選：`{DEFAULT_PRIMARY_MODEL}`）"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_clear_cooldowns(self, interaction: discord.Interaction) -> None:
        active_before = len(get_active_model_cooldowns())
        reset_model_cooldowns()
        self.status_note = (
            f"❄️ 已清除 `{active_before}` 個模型的 429 限流冷卻狀態！"
            if active_before
            else "❄️ 目前沒有冷卻中的模型，所有模型皆已就緒！"
        )
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_clear_all_overrides(self, interaction: discord.Interaction) -> None:
        removed = await asyncio.to_thread(assistant_db.clear_runtime_config_overrides)
        await self.cog.restore_settings_from_env()
        changes = await self.cog.sync_feature_cogs()
        reset_model_cooldowns()
        self.health_results = None
        cog_note = f"｜模組同步：`{', '.join(changes)}`" if changes else ""
        self.status_note = f"🧹 已清除 `{removed}` 筆 SQLite 覆寫並還原為 `.env` 設定{cog_note}"
        self._rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _on_run_full_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.health_results = await run_environment_health_check(
            self.cog.bot,
            self.cog.settings,
            gemini_client=self.cog.gemini_client,
            guild=interaction.guild,
            channel=interaction.channel,
            include_api_ping=True,
        )
        self.health_included_api = True
        passed = sum(1 for r in self.health_results if r["ok"])
        total = len(self.health_results)
        self.status_note = f"🩺 完整環境健檢完成：**{passed} / {total}** 項檢查通過"
        self._rebuild_items()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def _run_f5_control_action(
        self,
        interaction: discord.Interaction,
        action_name: str,
        worker,
    ) -> None:
        await interaction.response.defer()
        try:
            self.status_note = await asyncio.to_thread(worker)
        except Exception as exc:
            self.cog.logger.warning(f"⚠️ Setup 面板執行 {action_name} 失敗: {exc}")
            self.status_note = f"⚠️ {action_name}失敗：本機 F5-TTS 服務未啟動或拒絕連線（請確認 TTS 視窗已開啟）"
        self.health_results = await run_environment_health_check(
            self.cog.bot,
            self.cog.settings,
            gemini_client=self.cog.gemini_client,
            guild=interaction.guild,
            channel=interaction.channel,
            include_api_ping=self.health_included_api,
        )
        self._rebuild_items()
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def _on_f5_resident_preload(self, interaction: discord.Interaction) -> None:
        s = self.cog.settings

        def _work() -> str:
            _post_f5_admin_sync(
                s.tts_api_url,
                "/config",
                api_token=s.tts_api_token,
                data={"idle_unload_seconds": "0", "preload_on_startup": "true"},
                timeout=5.0,
            )
            res = _post_f5_admin_sync(
                s.tts_api_url,
                "/preload",
                api_token=s.tts_api_token,
                timeout=90.0,
            )
            used = res.get("vram_allocated_gb", 0.0)
            total = res.get("total_vram_gb", 0.0)
            vram_str = f"{used} / {total} GB" if total else f"{used} GB"
            load_sec = res.get("load_seconds")
            sec_str = f" | 耗時 {load_sec:.2f}s" if isinstance(load_sec, (int, float)) else ""
            cache_str = "（免連線本機快取直讀）" if res.get("loaded_from_local_cache") else ""
            return f"🔥 已切換為【F5-TTS 常駐預載】並就緒{cache_str}：VRAM `{vram_str}`{sec_str}"

        await self._run_f5_control_action(interaction, "F5 常駐預載", _work)

    async def _on_f5_lazy_mode(self, interaction: discord.Interaction) -> None:
        s = self.cog.settings

        def _work() -> str:
            _post_f5_admin_sync(
                s.tts_api_url,
                "/config",
                api_token=s.tts_api_token,
                data={"idle_unload_seconds": "1800", "preload_on_startup": "false"},
                timeout=5.0,
            )
            return "💤 已切換為【F5-TTS 省顯存按需模式】（閒置 30 分鐘自動釋放 VRAM）"

        await self._run_f5_control_action(interaction, "F5 省顯存模式", _work)

    async def _on_f5_unload(self, interaction: discord.Interaction) -> None:
        s = self.cog.settings

        def _work() -> str:
            res = _post_f5_admin_sync(
                s.tts_api_url,
                "/unload",
                api_token=s.tts_api_token,
                timeout=5.0,
            )
            vram = res.get("vram_allocated_gb", 0.0)
            return f"🧹 已釋放 F5-TTS 顯存（目前 VRAM 佔用：`{vram} GB`）"

        await self._run_f5_control_action(interaction, "釋放 F5 顯存", _work)

    def build_embed(self) -> discord.Embed:
        """Build the Embed matching the active SetupPanelView tab."""
        s = self.cog.settings
        banner = f"{self.status_note}\n\n" if self.status_note else ""

        if self.active_tab == "features":
            lines = [
                f"{'🟢' if getattr(s, key, False) else '⚪'} **{label}**：`{'開啟' if getattr(s, key, False) else '關閉'}` — {desc}"
                for key, label, desc, _row in _FEATURE_TOGGLE_SPECS
            ]
            embed = discord.Embed(
                title=f"⚙️ {s.bot_instance_name} — 互動設定面板｜🧩 功能開關",
                description=truncate_embed_text(
                    banner + "點擊下方按鈕可即時切換功能開關，並自動寫入 SQLite 持久保存與同步掛載/卸載模組：\n\n" + "\n".join(lines)
                ),
                color=0x5865F2,
            )
            embed.set_footer(text=f"設定檔：{s.env_file} | 僅限 Bot Owner 操作（面板 10 分鐘後自動鎖定）")
            return embed

        if self.active_tab == "channels":
            tts_disp = f"<#{s.tts_channel_id}> (`{s.tts_channel_id}`)" if s.tts_channel_id else "`未綁定`"
            gen_disp = f"<#{s.general_channel_id}> (`{s.general_channel_id}`)" if s.general_channel_id else "`未綁定`"
            desc = (
                f"{banner}使用下方選單直接綁定機器人監聽的文字頻道（立即生效並存檔至 SQLite）：\n\n"
                f"• **1️⃣ 主聊天頻道 (`TARGET_CHANNEL_ID`)**：<#{s.target_channel_id}> (`{s.target_channel_id}`)\n"
                f"• **2️⃣ 日文朗讀頻道 (`TTS_CHANNEL_ID`)**：{tts_disp}\n"
                f"• **3️⃣ 一般群聊頻道 (`GENERAL_CHANNEL_ID`)**：{gen_disp}"
            )
            embed = discord.Embed(
                title=f"⚙️ {s.bot_instance_name} — 互動設定面板｜📺 頻道綁定",
                description=truncate_embed_text(desc),
                color=0x38BDF8,
            )
            embed.set_footer(text="💡 若僅使用單一頻道，可點擊「清除」按鈕停用選配的第二/第三頻道")
            return embed

        if self.active_tab == "models":
            cooldowns = get_active_model_cooldowns()
            cd_lines = (
                "\n".join(f"• `{m}`：剩餘 `{sec}s`" for m, sec in cooldowns.items())
                if cooldowns
                else "無（所有模型皆就緒）"
            )
            embed = discord.Embed(
                title=f"⚙️ {s.bot_instance_name} — 互動設定面板｜🤖 模型策略",
                description=truncate_embed_text(
                    f"{banner}預設以 **Gemini Flash-Lite** 為首選與最終保底，高峰限流時自動備援切換至 **Flash / Gemini 4 Hydrogen**；亦可一鍵切回純 Flash-Lite 鏈。"
                ),
                color=0xA855F7,
            )
            embed.add_field(
                name="📌 目前輪替鏈設定",
                value=truncate_embed_text(
                    f"**首選**：`{s.primary_model}`\n"
                    f"**備援**：`{', '.join(s.fallback_models) or '無'}`\n"
                    f"**最終保底**：`{s.final_fallback_model or '已停用'}`",
                    max_len=950,
                ),
                inline=False,
            )
            embed.add_field(
                name="❄️ 429 限流冷卻狀態",
                value=truncate_embed_text(cd_lines, max_len=950),
                inline=False,
            )
            embed.set_footer(text="🧹「清除全部 SQLite 覆寫」會移除面板變更並重新讀取 .env 設定")
            return embed

        # active_tab == "health"
        results = self.health_results or []
        all_ok = all(r["ok"] for r in results) if results else True
        embed = discord.Embed(
            title=f"⚙️ {s.bot_instance_name} — 互動設定面板｜🩺 環境健檢",
            description=truncate_embed_text(
                banner
                + (
                    "以下為最近一次環境與權限診斷結果（含 Gemini API 實測）："
                    if self.health_included_api
                    else "以下為快速離線檢查結果；點擊下方 **「🩺 執行完整健檢 (含 API 測試)」** 可實測 Gemini 連線。"
                )
            ),
            color=0x57F287 if all_ok else 0xF59E0B,
        )
        if not results:
            embed.add_field(
                name="ℹ️ 尚未執行健檢",
                value="請點擊下方 **「🩺 執行完整健檢 (含 API 測試)」** 開始檢查。",
                inline=False,
            )
        else:
            for item in results[:20]:
                icon = "✅" if item["ok"] else "❌"
                val = item["detail"]
                if item.get("fix_hint"):
                    val += f"\n💡 **建議**：{item['fix_hint']}"
                embed.add_field(
                    name=f"{icon} {item['name']}"[:256],
                    value=truncate_embed_text(val, max_len=950),
                    inline=False,
                )
        embed.set_footer(text="💡 快速指令：隨時可輸入 !setup check 直接執行完整健檢")
        return embed


class SetupCog(commands.Cog):
    """Owner-only interactive configuration panel (!setup) & environment diagnostics."""

    def __init__(
        self,
        bot: commands.Bot,
        settings: BotSettings,
        logger: logging.Logger,
        gemini_client=None,
    ):
        self.bot = bot
        self.settings = settings
        self.logger = logger
        self.gemini_client = gemini_client

    async def restore_settings_from_env(self) -> bool:
        """Reload non-secret runtime fields from the configured .env file back onto `self.settings`."""
        env_path = BASE_DIR / self.settings.env_file
        can_reload_dotenv = (
            self.settings.discord_bot_token not in ("", "tok")
            and await asyncio.to_thread(env_path.is_file)
        )
        refreshed: Optional[BotSettings] = None
        if can_reload_dotenv:
            def _reload_sync() -> BotSettings:
                init_runtime_env(argv=[f"--env={self.settings.env_file}"], override=True)
                return load_settings(self.settings.env_file, logger=self.logger)

            refreshed = await asyncio.to_thread(_reload_sync)
        else:
            try:
                refreshed = load_settings(self.settings.env_file, logger=self.logger)
            except ValueError:
                return False

        for field_name in _RESTORABLE_SETTINGS_FIELDS:
            setattr(self.settings, field_name, getattr(refreshed, field_name))
        return True

    async def sync_feature_cogs(self) -> list[str]:
        """Dynamically add/remove optional feature Cogs to match current `self.settings` flags."""
        from cogs.boardgame_cog import BoardGameCog
        from cogs.flashcard_cog import FlashcardCog
        from cogs.lifesim_cog import LifeSimCog
        from cogs.tts_cog import TtsCog
        from cogs.wiki_cog import WikiCog
        from engines import (
            boardgame_engine,
            flashcard_engine,
            game_wiki_engine,
            lifesim_engine,
            tts_engine,
        )

        changes: list[str] = []

        # RPG is core and never removed; check reference safely
        _ = self.bot.get_cog("RpgCog")

        if self.settings.enable_board_games:
            if self.bot.get_cog("BoardGameCog") is None:
                await asyncio.to_thread(boardgame_engine.init_boardgame_tables)
                await self.bot.add_cog(BoardGameCog(self.bot, self.gemini_client, self.settings, self.logger))
                changes.append("+BoardGameCog")
            if self.bot.get_cog("LifeSimCog") is None:
                await asyncio.to_thread(lifesim_engine.init_lifesim_tables)
                await self.bot.add_cog(LifeSimCog(self.bot, self.gemini_client, self.settings, self.logger))
                changes.append("+LifeSimCog")
        else:
            if self.bot.get_cog("BoardGameCog") is not None:
                await self.bot.remove_cog("BoardGameCog")
                changes.append("-BoardGameCog")
            if self.bot.get_cog("LifeSimCog") is not None:
                await self.bot.remove_cog("LifeSimCog")
                changes.append("-LifeSimCog")

        if self.settings.enable_flashcard_mode:
            if self.bot.get_cog("FlashcardCog") is None:
                await asyncio.to_thread(flashcard_engine.init_flashcard_tables)
                await self.bot.add_cog(FlashcardCog(self.bot, self.gemini_client, self.settings, self.logger))
                changes.append("+FlashcardCog")
        else:
            if self.bot.get_cog("FlashcardCog") is not None:
                await self.bot.remove_cog("FlashcardCog")
                changes.append("-FlashcardCog")

        if self.settings.enable_game_wiki:
            if self.bot.get_cog("WikiCog") is None:
                await asyncio.to_thread(game_wiki_engine.load_wiki_entries)
                await self.bot.add_cog(WikiCog(self.bot, self.gemini_client, self.settings, self.logger))
                changes.append("+WikiCog")
        else:
            if self.bot.get_cog("WikiCog") is not None:
                await self.bot.remove_cog("WikiCog")
                changes.append("-WikiCog")

        if self.settings.enable_voice_tts:
            if self.bot.get_cog("TtsCog") is None:
                await asyncio.to_thread(tts_engine.init_tts_tables)
                await self.bot.add_cog(TtsCog(self.bot, self.settings, self.logger))
                changes.append("+TtsCog")
        else:
            if self.bot.get_cog("TtsCog") is not None:
                await self.bot.remove_cog("TtsCog")
                changes.append("-TtsCog")

        return changes

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument)):
            if is_target_channel(ctx.channel, self.settings):
                await ctx.send("❓ 指令格式不正確，請使用 `!setup` 或 `!setup check`。")
            return
        self.logger.error(f"❌ SetupCog 指令執行錯誤 ({ctx.command}): {error}", exc_info=error)

    @commands.command(name="setup", aliases=["設定面板", "面板", "健檢", "config"])
    async def setup_command(self, ctx: commands.Context, action: Optional[str] = None):
        """擁有者專用：開啟互動式設定與環境健檢面板（!setup / !setup check）。"""
        if not await is_bot_owner(self.bot, self.settings, ctx.author):
            if is_target_channel(ctx.channel, self.settings):
                await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可使用 `!setup` 設定與健檢面板。")
            return

        arg_lower = (action or "").strip().lower()
        if arg_lower in ("check", "health", "健檢"):
            typing_cm = ctx.typing() if callable(getattr(ctx, "typing", None)) else None
            if typing_cm is not None and hasattr(typing_cm, "__aenter__"):
                async with typing_cm:
                    results = await run_environment_health_check(
                        self.bot,
                        self.settings,
                        gemini_client=self.gemini_client,
                        guild=getattr(ctx, "guild", None),
                        channel=ctx.channel,
                        include_api_ping=True,
                    )
            else:
                results = await run_environment_health_check(
                    self.bot,
                    self.settings,
                    gemini_client=self.gemini_client,
                    guild=getattr(ctx, "guild", None),
                    channel=ctx.channel,
                    include_api_ping=True,
                )
            view = SetupPanelView(
                self,
                active_tab="health",
                health_results=results,
                health_included_api=True,
            )
        else:
            view = SetupPanelView(self, active_tab="features")

        embed = view.build_embed()
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg
