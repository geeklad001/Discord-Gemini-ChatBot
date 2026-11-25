"""Guild admin and bot-owner !tts configuration & diagnostics commands."""

from __future__ import annotations

import asyncio
import re
from typing import Optional

import discord
from discord.ext import commands

from engines import tts_engine

_ON_TOKENS = ("on", "true", "開啟")
_OFF_TOKENS = ("off", "false", "關閉")


class TtsAdminMixin:
    """Mixin providing the !tts command hub, channel bindings, toggles, and GPU controls."""

    def _resolve_target_channel(self, ctx: commands.Context, raw_arg: Optional[str]):
        if not ctx.guild:
            return None
        raw = (raw_arg or "").strip()
        if not raw:
            if isinstance(ctx.channel, (discord.DMChannel, discord.GroupChannel)):
                return None
            return ctx.channel

        m = re.match(r"^<#(\d+)>$", raw) or re.match(r"^(\d+)$", raw)
        if m:
            cid = int(m.group(1))
            ch = None
            from_guild_lookup = False
            if hasattr(ctx.guild, "get_channel_or_thread"):
                ch = ctx.guild.get_channel_or_thread(cid)
                from_guild_lookup = ch is not None
            if ch is None and hasattr(ctx.guild, "get_channel"):
                ch = ctx.guild.get_channel(cid)
                from_guild_lookup = ch is not None
            if ch is None:
                ch = self.bot.get_channel(cid)
            if ch is not None and not isinstance(ch, (discord.DMChannel, discord.GroupChannel)):
                ch_guild = getattr(ch, "guild", None)
                if from_guild_lookup:
                    if ch_guild is None or getattr(ch_guild, "id", None) == ctx.guild.id:
                        return ch
                elif ch_guild is not None and getattr(ch_guild, "id", None) == ctx.guild.id:
                    return ch
            return None

        clean_name = raw.lstrip("#").strip().lower()
        for ch in getattr(ctx.guild, "channels", []):
            if getattr(ch, "name", "").lower() == clean_name:
                return ch
        return None

    def _format_channel_list(
        self,
        guild: discord.Guild,
        channel_ids: list[int],
        empty_label: str,
        max_len: int = 700,
    ) -> str:
        if not channel_ids:
            return empty_label
        parts: list[str] = []
        current_len = 0
        for idx, cid in enumerate(channel_ids):
            ch = guild.get_channel(cid) if hasattr(guild, "get_channel") else None
            if ch is None:
                ch = self.bot.get_channel(cid)
            token = f"<#{cid}>" if ch is None else f"{ch.mention} (`{cid}`)"
            sep_len = 1 if parts else 0
            if parts and current_len + sep_len + len(token) > max_len:
                return "、".join(parts) + f"…（另有 {len(channel_ids) - idx} 個頻道）"
            parts.append(token)
            current_len += sep_len + len(token)
        return "、".join(parts)

    @staticmethod
    def _parse_bool_toggle_arg(arg: Optional[str], current_value: bool) -> Optional[bool]:
        token = (arg or "").strip().lower()
        if token in _ON_TOKENS:
            return True
        if token in _OFF_TOKENS:
            return False
        if not token:
            return not current_value
        return None

    async def _require_tts_admin_guild(
        self,
        ctx: commands.Context,
        deny_msg: str,
        *,
        require_guild_msg: Optional[str] = "❌ 此指令僅限在伺服器中使用。",
    ) -> bool:
        if not await self.is_tts_admin(ctx):
            await ctx.send(deny_msg)
            return False
        if not ctx.guild:
            if require_guild_msg:
                await ctx.send(require_guild_msg)
            return False
        return True

    async def _handle_tts_command(
        self,
        ctx: commands.Context,
        action: Optional[str] = None,
        arg: Optional[str] = None,
    ) -> None:
        act = (action or "status").strip().lower()

        if act in ("status", "info", "狀態"):
            await self._handle_tts_status(ctx)
            return
        if act in ("channels", "chlist"):
            await self._handle_tts_channels_view(ctx)
            return
        if act in ("channel", "ch", "頻道"):
            await self._handle_tts_channel_action(ctx, arg)
            return
        if act in ("bind", "綁定"):
            await self._handle_tts_bind(ctx, arg)
            return
        if act in ("unbind", "解綁"):
            await self._handle_tts_unbind(ctx, arg)
            return
        if act in ("ignore", "unignore", "忽略", "取消忽略"):
            await self._handle_tts_ignore(ctx, arg, ignore=act in ("ignore", "忽略"))
            return

        if act == "ai":
            if not await self._require_tts_admin_guild(
                ctx, "🔒 僅限伺服器管理員（或機器人擁有者）可切換 AI 語音自動朗讀。"
            ):
                return
            await self._handle_tts_ai_toggle(ctx, arg)
            return

        if act == "proxy":
            if not await self._require_tts_admin_guild(
                ctx, "🔒 僅限伺服器管理員（或機器人擁有者）可切換真人代說開關。"
            ):
                return
            await self._handle_tts_proxy_toggle(ctx, arg)
            return

        if act in ("vconly", "vc_only", "requirevc", "同房限制", "限語音房"):
            if not await self._require_tts_admin_guild(
                ctx, "🔒 僅限伺服器管理員（或機器人擁有者）可切換同語音房限制。"
            ):
                return
            await self._handle_tts_vconly_toggle(ctx, arg)
            return

        if act in ("clear", "清空"):
            if not await self._require_tts_admin_guild(
                ctx,
                "🔒 僅限伺服器管理員（或機器人擁有者）可清空語音佇列。",
                require_guild_msg=None,
            ):
                return
            pipeline = self.pipelines.get(ctx.guild.id)
            if pipeline:
                pipeline.clear_pending()
            await ctx.send("🧹 已清空目前語音播放佇列。")
            return

        if act in ("preload", "warmup", "預載", "暖機"):
            await self._handle_tts_preload(ctx)
            return

        if act in ("keepalive", "resident", "常駐", "vram", "mode"):
            await self._handle_tts_keepalive(ctx, arg)
            return

        if act in ("unload", "釋放"):
            await self._handle_tts_unload(ctx)
            return

        if act in ("reloadvoices", "reload_voices", "重載聲線"):
            await self._handle_tts_reload_voices(ctx)
            return

        if act in ("clone", "voiceclone", "克隆", "聲音克隆", "複製聲音"):
            await self._handle_voiceclone(ctx, arg)
            return

        await ctx.send(
            "❓ 未知的 `!tts` 子指令。可用項目：\n"
            "• 全體成員：`!tts status`、`!tts channels`、`!voice`、`!voiceclone`\n"
            "• 管理員：`!tts channel add/remove/clear [#頻道]`、`!tts bind`、`!tts unbind`、`!tts ignore`、`!tts ai`、`!tts proxy`、`!tts vconly`、`!tts clear`\n"
            "• 擁有者：`!tts preload`、`!tts keepalive [on|off|秒]`、`!tts unload`、`!tts reloadvoices`"
        )

    async def _handle_tts_bind(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not await self._require_tts_admin_guild(
            ctx, "🔒 僅限伺服器管理員（或機器人擁有者）可設定 TTS 對象頻道。"
        ):
            return
        target_ch = self._resolve_target_channel(ctx, arg)
        if target_ch is None:
            await ctx.send("❌ 找不到指定的文字或語音頻道。")
            return
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        allowed = list(g_cfg["allowed_channels"])
        if target_ch.id not in allowed:
            allowed.append(target_ch.id)
        await self._update_guild_settings(ctx.guild.id, allowed_channels=allowed)
        bound_str = self._format_channel_list(ctx.guild, allowed, target_ch.mention)
        await ctx.send(self._clip_reply(f"🔒 已將 {target_ch.mention} 綁定至代說 TTS 對象頻道！（目前綁定：{bound_str}）"))

    async def _handle_tts_unbind(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not await self._require_tts_admin_guild(
            ctx, "🔒 僅限伺服器管理員（或機器人擁有者）可設定 TTS 對象頻道。"
        ):
            return
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        allowed = list(g_cfg["allowed_channels"])
        raw_unbind = (arg or "").strip().lower()
        if raw_unbind in ("all", "clear", "reset", "全部", "清空"):
            new_allowed: list[int] = []
        elif raw_unbind:
            target_ch = self._resolve_target_channel(ctx, arg)
            if target_ch is None:
                await ctx.send("❌ 找不到指定的文字或語音頻道。")
                return
            new_allowed = [cid for cid in allowed if cid != target_ch.id]
        elif ctx.channel.id in allowed and len(allowed) > 1:
            new_allowed = [cid for cid in allowed if cid != ctx.channel.id]
        else:
            new_allowed = []
        await self._update_guild_settings(ctx.guild.id, allowed_channels=new_allowed)
        if new_allowed:
            bound_str = self._format_channel_list(ctx.guild, new_allowed, "無")
            await ctx.send(self._clip_reply(f"🔓 已解除該頻道的 TTS 綁定！（目前剩餘綁定：{bound_str}）"))
        else:
            await ctx.send("🔓 已解除指定對象頻道，恢復為預設監聽模式。")

    async def _handle_tts_ignore(self, ctx: commands.Context, arg: Optional[str], *, ignore: bool) -> None:
        if not await self._require_tts_admin_guild(
            ctx, "🔒 僅限伺服器管理員（或機器人擁有者）可設定 TTS 忽略頻道。"
        ):
            return
        target_ch = self._resolve_target_channel(ctx, arg)
        if target_ch is None:
            await ctx.send("❌ 找不到指定的頻道。")
            return
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        ignored = list(g_cfg["ignored_channels"])
        if ignore:
            if target_ch.id not in ignored:
                ignored.append(target_ch.id)
            await self._update_guild_settings(ctx.guild.id, ignored_channels=ignored)
            await ctx.send(f"🚫 已將 {target_ch.mention} 加入 TTS 忽略清單。")
        else:
            if target_ch.id in ignored:
                ignored.remove(target_ch.id)
            await self._update_guild_settings(ctx.guild.id, ignored_channels=ignored)
            await ctx.send(f"✅ 已將 {target_ch.mention} 從 TTS 忽略清單移除。")

    async def _handle_tts_preload(self, ctx: commands.Context) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 此指令僅限機器人擁有者（Bot Owner）操作本地 GPU 顯存。")
            return
        if not self.synthesizer.is_f5_configured:
            await ctx.send("ℹ️ 目前未配置進階語音克隆服務，無須預載模型。")
            return
        typing_cm = ctx.typing() if callable(getattr(ctx, "typing", None)) else None
        if typing_cm is not None and hasattr(typing_cm, "__aenter__"):
            async with typing_cm:
                res = await self.synthesizer.preload_f5_model()
        else:
            res = await self.synthesizer.preload_f5_model()
        if not res:
            await ctx.send("⚠️ 語音服務目前未連線或暫時無法回應，請查看後台終端機日誌。")
            return
        used_vram = res.get("vram_allocated_gb", 0.0)
        total_vram = res.get("total_vram_gb", 0.0)
        vram_str = f"{used_vram} / {total_vram} GB" if total_vram else f"{used_vram} GB"
        load_sec = res.get("load_seconds")
        load_str = f"{load_sec:.2f}s" if isinstance(load_sec, (int, float)) else "已載入"
        cache_note = "本機快取・免連線直讀" if res.get("loaded_from_local_cache") else "標準/首次載入"
        prefix = "✅ F5-TTS 模型已在顯存中就緒" if res.get("already_loaded") else "🔥 F5-TTS 模型預載完成"
        await ctx.send(f"{prefix}（{cache_note} | 載入耗時：`{load_str}` | VRAM：`{vram_str}`）。")

    async def _handle_tts_keepalive(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 此指令僅限機器人擁有者（Bot Owner）操作本地 GPU 顯存。")
            return
        if not self.synthesizer.is_f5_configured:
            await ctx.send("ℹ️ 目前未配置進階語音克隆服務。")
            return

        token = (arg or "").strip().lower()
        if not token:
            health = await self.synthesizer.check_f5_health(force=True)
            if not health:
                await ctx.send(
                    "⚠️ 語音服務目前未連線，無法讀取目前顯存策略。\n"
                    "💡 用法：`!tts keepalive [on|off|<秒數>]`"
                )
                return
            idle_sec = health.get("idle_unload_seconds")
            residency = (
                "🔥 常駐不卸載 (`0s`)"
                if idle_sec == 0
                else (f"💤 閒置 `{idle_sec}` 秒自動釋放" if idle_sec is not None else "💤 按需自動釋放")
            )
            preload_txt = "開啟" if health.get("preload_on_startup") else "關閉"
            offline_txt = "開啟" if health.get("offline_first", True) else "關閉"
            await ctx.send(
                f"🎛️ **目前 F5-TTS 顯存駐留策略**\n"
                f"• 駐留模式：{residency}\n"
                f"• 啟動自動預載：`{preload_txt}` | 離線快取直讀：`{offline_txt}`\n"
                "💡 用法：`!tts keepalive [on|off|<秒數>]`"
            )
            return

        if token in ("on", "true", "開啟", "resident", "常駐"):
            res = await self.synthesizer.update_f5_config(idle_unload_seconds=0, preload_on_startup=True)
            if res:
                await ctx.send("🔥 已切換為【顯存常駐 + 啟動自動預載】模式（設定已永久保存）")
            else:
                await ctx.send("⚠️ 語音服務目前未連線或暫時無法回應，請查看後台終端機日誌。")
            return

        if token in ("off", "false", "關閉", "lazy", "省電", "自動釋放"):
            res = await self.synthesizer.update_f5_config(idle_unload_seconds=1800, preload_on_startup=False)
            if res:
                await ctx.send("💤 已切換為【省顯存按需載入】模式（閒置 30 分鐘自動釋放 VRAM）")
            else:
                await ctx.send("⚠️ 語音服務目前未連線或暫時無法回應，請查看後台終端機日誌。")
            return

        if token.isdigit():
            seconds = int(token)
            if seconds < 0 or seconds > 86400:
                await ctx.send("💡 閒置秒數須介於 `0`（常駐不卸載）～ `86400`（24 小時）之間。")
                return
            res = await self.synthesizer.update_f5_config(idle_unload_seconds=seconds)
            if not res:
                await ctx.send("⚠️ 語音服務目前未連線或暫時無法回應，請查看後台終端機日誌。")
                return
            if seconds == 0:
                await ctx.send("🔥 已將 F5-TTS 閒置釋放設為 `0` 秒（顯存常駐不卸載，設定已永久保存）。")
            else:
                await ctx.send(f"⏱️ 已將 F5-TTS 閒置自動釋放設為 `{seconds}` 秒（設定已永久保存）。")
            return

        await ctx.send(
            "💡 用法：`!tts keepalive [on|off|<秒數>]`\n"
            "• `on`：顯存常駐不釋放 ＋ 啟動時自動預載\n"
            "• `off`：省顯存按需載入（閒置 1800 秒自動釋放）\n"
            "• `<秒數>`：`0`～`86400` 自訂閒置釋放秒數"
        )

    async def _handle_tts_unload(self, ctx: commands.Context) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 此指令僅限機器人擁有者（Bot Owner）操作本地 GPU 顯存。")
            return
        if not self.synthesizer.is_f5_configured:
            await ctx.send("ℹ️ 目前未配置進階語音克隆服務，無須釋放資源。")
            return
        res = await self.synthesizer.unload_f5_model()
        if res:
            await ctx.send(
                f"💤 已釋放 F5-TTS GPU 顯存（原本載入狀態：`{res.get('was_loaded')}` | 目前 VRAM：`{res.get('vram_allocated_gb')} GB`）。"
            )
        else:
            await ctx.send("⚠️ 語音服務目前未連線或暫時無法回應，請查看後台終端機日誌。")

    async def _handle_tts_reload_voices(self, ctx: commands.Context) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 此指令僅限機器人擁有者（Bot Owner）重載本地聲線設定。")
            return
        if not self.synthesizer.is_f5_configured:
            await ctx.send("ℹ️ 目前未配置進階語音克隆服務。")
            return
        res = await self.synthesizer.reload_f5_voices()
        if res:
            self.synthesizer.invalidate_voice_cache()
            await ctx.send(
                f"🔄 已重新載入本地 F5-TTS 聲線目錄：公開 `{res.get('public_voice_count')}` 組 / 總計 `{res.get('total_voice_count')}` 組。"
            )
        else:
            await ctx.send("⚠️ 語音服務目前未連線或暫時無法回應，請查看後台終端機日誌。")

    async def _handle_tts_status(self, ctx: commands.Context) -> None:
        vc = self._get_connected_vc(ctx.guild) if ctx.guild else None
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id) if ctx.guild else None
        if vc and vc.channel:
            non_bot_count = self._count_non_bot_users_in_vc(ctx.guild, vc.channel)
            caller_vc = self._resolve_user_voice_channel(ctx.guild, ctx.author, vc_channel=vc.channel)
            vc_ch_id = getattr(vc.channel, "id", None)
            caller_ch_id = getattr(caller_vc, "id", None)
            if caller_vc is not None and caller_ch_id == vc_ch_id:
                caller_state = "✅ 已在此語音房內（可觸發真人代說）"
            elif g_cfg and g_cfg["require_in_vc"]:
                where = f"#{getattr(caller_vc, 'name', caller_ch_id)}" if caller_vc is not None else "未進房"
                caller_state = f"⚠️ 未偵測到此語音房內（目前：{where}，已開啟同房限制）"
            else:
                caller_state = "💬 未在語音房內（目前開放未進房成員於對象頻道代說 ✅）"
            vc_str = (
                f"🔊 已連線：**{vc.channel.name}**\n"
                f"• 偵測在線人數：{non_bot_count} 人 | 你的狀態：{caller_state}"
            )
        else:
            vc_str = "⏸️ 未加入語音頻道（輸入 `!join` 加入）"

        embed = discord.Embed(title="🔊 語音朗讀系統狀態", color=0x10B981)
        embed.add_field(name="語音連線", value=vc_str[:1024], inline=False)

        user_pref = await asyncio.to_thread(
            tts_engine.get_user_voice_pref,
            ctx.author.id,
            self.settings.tts_default_voice,
        )
        user_f5 = await self.synthesizer.fetch_f5_voices(user_id=ctx.author.id)
        user_voice_label = tts_engine.get_voice_display_label(user_pref["voice_id"], user_f5)
        user_mode_note = "已自訂" if user_pref.get("is_custom") else "未設定・自動使用預設 Edge-TTS"
        embed.add_field(
            name="你的個人聲線",
            value=(
                f"• {user_voice_label}（{user_mode_note}）\n"
                f"• 語速 `{tts_engine.format_edge_rate(user_pref['rate_pct'])}` | "
                f"音高 `{tts_engine.format_edge_pitch(user_pref['pitch_hz'])}`"
            )[:1024],
            inline=False,
        )

        if ctx.guild and g_cfg is not None:
            public_f5 = [fv for fv in user_f5 if fv.get("public")]
            target_str = self._format_channel_list(
                ctx.guild, g_cfg["allowed_channels"], "預設：僅語音頻道內建文字區"
            )
            ignore_str = self._format_channel_list(ctx.guild, g_cfg["ignored_channels"], "無")
            ai_gen_str = (
                f"{tts_engine.get_voice_display_label(g_cfg['ai_voice_id'], public_f5)} "
                f"(`{tts_engine.format_edge_rate(g_cfg['ai_rate_pct'])}` / `{tts_engine.format_edge_pitch(g_cfg['ai_pitch_hz'])}`)"
            )
            bot_voice_lines = [f"• 機器人一般聲線：{ai_gen_str}"]
            if self.settings.enable_japanese_mode:
                ai_jp_str = (
                    f"{tts_engine.get_voice_display_label(g_cfg['ai_jp_voice_id'], public_f5)} "
                    f"(`{tts_engine.format_edge_rate(g_cfg['ai_jp_rate_pct'])}` / `{tts_engine.format_edge_pitch(g_cfg['ai_jp_pitch_hz'])}`)"
                )
                bot_voice_lines.append(f"• 機器人日語聲線：{ai_jp_str}")
            ai_gen_state = (
                "開啟"
                if g_cfg["ai_read_general"]
                else ("僅限綁定對象頻道" if g_cfg["allowed_channels"] else "關閉")
            )
            vconly_state = "開啟（僅限同房成員）" if g_cfg["require_in_vc"] else "關閉（未進房也可讀）"
            embed.add_field(
                name="伺服器策略（管理員設定頻道／全體可自訂機器人聲線）",
                value=(
                    f"• 真人代說：**{'開啟' if g_cfg['proxy_enabled'] else '關閉'}** | "
                    f"同語音房限制：**{vconly_state}**\n"
                    f"• AI 日語朗讀：**{'開啟' if g_cfg['ai_read_jp'] else '關閉'}** | "
                    f"AI 一般對話朗讀：**{ai_gen_state}**\n"
                    + "\n".join(bot_voice_lines)
                    + f"\n• 代說對象頻道：{target_str}\n"
                    f"• 忽略頻道：{ignore_str}"
                )[:1024],
                inline=False,
            )

        is_owner = await self.is_bot_owner(ctx.author)
        if not self.synthesizer.is_f5_configured:
            f5_str = "⚡ 標準語音引擎（0 顯存，運作正常）"
        else:
            health = await self.synthesizer.check_f5_health(force=True)
            if health:
                loaded = bool(health.get("model_loaded", False))
                if is_owner:
                    idle_unload = health.get("idle_unload_seconds")
                    if loaded:
                        if idle_unload == 0:
                            state = "🟢 模型常駐中（永不自動釋放）"
                        else:
                            rem = health.get("idle_remaining_seconds")
                            state = f"🟢 模型常駐中（剩餘閒置倒數 `{rem}` 秒）"
                    elif health.get("model_loading"):
                        state = "⏳ 正在載入模型至顯存中..."
                    else:
                        state = "💤 待命省電中（0 VRAM，首次請求自動載入）"

                    vram_used = health.get("vram_allocated_gb", 0.0)
                    total_vram = health.get("total_vram_gb")
                    vram_disp = (
                        f"{vram_used} GB / {total_vram} GB"
                        if isinstance(total_vram, (int, float)) and total_vram > 0
                        else f"{vram_used} GB"
                    )
                    residency_str = (
                        "常駐不卸載"
                        if idle_unload == 0
                        else (f"閒置 {idle_unload}s 釋放" if idle_unload is not None else "按需自動釋放")
                    )
                    preload_str = "開啟" if health.get("preload_on_startup") else "關閉"
                    cache_str = "開啟" if health.get("offline_first", True) else "關閉"
                    f5_str = (
                        f"• 狀態：{state}\n"
                        f"• 裝置：`{health.get('gpu_name') or health.get('device')}` | VRAM：`{vram_disp}`\n"
                        f"• 策略：`{residency_str}` | 啟動預載：`{preload_str}` | 離線快取直讀：`{cache_str}`\n"
                        f"• 聲線：公開 `{health.get('public_voice_count')}` 組 / 總計 `{health.get('total_voice_count')}` 組"
                    )[:1024]
                else:
                    pub_cnt = int(health.get("public_voice_count") or 0)
                    f5_str = f"🟢 特色聲線服務已就緒（目前開放 `{pub_cnt}` 組公開特色聲線）"
            else:
                f5_str = (
                    "⚪ 進階語音服務未啟動（目前自動改用標準語音，運作正常）"
                    if is_owner
                    else "⚡ 目前使用標準語音引擎（運作正常）"
                )

        embed.add_field(name="語音引擎", value=f5_str, inline=False)
        await ctx.send(embed=embed)

    async def _handle_tts_channels_view(self, ctx: commands.Context) -> None:
        if not ctx.guild:
            await ctx.send("❌ 此指令僅限在伺服器中使用。")
            return
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        target_str = self._format_channel_list(
            ctx.guild, g_cfg["allowed_channels"], "預設：僅語音頻道內建文字聊天室"
        )
        ignore_str = self._format_channel_list(ctx.guild, g_cfg["ignored_channels"], "無")
        await ctx.send(
            self._clip_reply(
                f"📋 **目前伺服器 TTS 對象頻道設定**\n"
                f"• **對象頻道**：{target_str}\n"
                f"• **忽略頻道**：{ignore_str}\n"
                "💡 管理員可用 `!tts channel add/remove/clear [#頻道]` 或 `!tts bind` / `!tts unbind` 調整。"
            )
        )

    async def _handle_tts_channel_action(self, ctx: commands.Context, arg: Optional[str]) -> None:
        if not ctx.guild:
            await ctx.send("❌ 此指令僅限在伺服器中使用。")
            return
        tokens = (arg or "").strip().split(maxsplit=1)
        sub = tokens[0].lower() if tokens else "list"
        rest = tokens[1].strip() if len(tokens) > 1 else None

        if sub in ("list", "清單", ""):
            await self._handle_tts_channels_view(ctx)
            return

        if not await self.is_tts_admin(ctx):
            await ctx.send("🔒 僅限伺服器管理員（或機器人擁有者）可修改 TTS 對象頻道。")
            return

        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        allowed = list(g_cfg["allowed_channels"])

        if sub in ("clear", "reset", "清空"):
            await self._update_guild_settings(ctx.guild.id, allowed_channels=[])
            await ctx.send("🔓 已清空指定對象頻道，恢復為僅監聽「語音頻道內建文字聊天室」。")
            return

        if sub in ("add", "新增"):
            target_ch = self._resolve_target_channel(ctx, rest)
            if target_ch is None:
                await ctx.send("❌ 找不到指定的頻道。")
                return
            if target_ch.id not in allowed:
                allowed.append(target_ch.id)
            await self._update_guild_settings(ctx.guild.id, allowed_channels=allowed)
            await ctx.send(f"✅ 已將 {target_ch.mention} 加入代說 TTS 對象頻道！")
            return

        if sub in ("remove", "del", "rm", "移除", "刪除"):
            target_ch = self._resolve_target_channel(ctx, rest)
            if target_ch is None:
                await ctx.send("❌ 找不到指定的頻道。")
                return
            if target_ch.id in allowed:
                allowed.remove(target_ch.id)
            await self._update_guild_settings(ctx.guild.id, allowed_channels=allowed)
            note = "（已無自訂對象頻道，恢復為預設語音頻道內建文字區）" if not allowed else ""
            await ctx.send(f"✅ 已從代說對象頻道移除 {target_ch.mention}。{note}")
            return

        await ctx.send(
            "💡 用法：`!tts channel list`、`!tts channel add [#頻道]`、`!tts channel remove [#頻道]`、`!tts channel clear`"
        )

    async def _handle_tts_ai_toggle(self, ctx: commands.Context, arg: Optional[str]) -> None:
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        tokens = (arg or "").strip().lower().split()

        if not tokens:
            new_jp = not g_cfg["ai_read_jp"]
            await self._update_guild_settings(ctx.guild.id, ai_read_jp=new_jp)
            await ctx.send(f"🤖 AI 日語回覆語音朗讀已切換為：**{'開啟' if new_jp else '關閉'}**。")
            return

        first = tokens[0]
        second = tokens[1] if len(tokens) > 1 else None

        if first in _ON_TOKENS or first in _OFF_TOKENS:
            val = first in _ON_TOKENS
            await self._update_guild_settings(ctx.guild.id, ai_read_jp=val, ai_read_general=val)
            await ctx.send(f"🤖 AI 語音自動朗讀（日語＋一般對話）已全部設為：**{'開啟' if val else '關閉'}**。")
            return

        if first in ("jp", "日語", "日文"):
            val = self._parse_bool_toggle_arg(second, g_cfg["ai_read_jp"])
            if val is None:
                await ctx.send("💡 用法：`!tts ai [on|off|jp [on|off]|general [on|off]]`")
                return
            await self._update_guild_settings(ctx.guild.id, ai_read_jp=val)
            await ctx.send(f"🌸 AI 日語共學朗讀已設為：**{'開啟' if val else '關閉'}**。")
            return

        if first in ("general", "chat", "一般"):
            val = self._parse_bool_toggle_arg(second, g_cfg["ai_read_general"])
            if val is None:
                await ctx.send("💡 用法：`!tts ai [on|off|jp [on|off]|general [on|off]]`")
                return
            await self._update_guild_settings(ctx.guild.id, ai_read_general=val)
            await ctx.send(f"💬 AI 一般群聊朗讀已設為：**{'開啟' if val else '關閉'}**。")
            return

        await ctx.send("💡 用法：`!tts ai [on|off|jp [on|off]|general [on|off]]`")

    async def _handle_tts_bool_toggle(
        self,
        ctx: commands.Context,
        arg: Optional[str],
        *,
        field: str,
        usage: str,
        on_msg: str,
        off_msg: str,
    ) -> None:
        g_cfg = await asyncio.to_thread(self._get_guild_settings, ctx.guild.id)
        val = self._parse_bool_toggle_arg(arg, g_cfg[field])
        if val is None:
            await ctx.send(usage)
            return
        await self._update_guild_settings(ctx.guild.id, **{field: val})
        await ctx.send(on_msg if val else off_msg)

    async def _handle_tts_proxy_toggle(self, ctx: commands.Context, arg: Optional[str]) -> None:
        await self._handle_tts_bool_toggle(
            ctx,
            arg,
            field="proxy_enabled",
            usage="💡 用法：`!tts proxy [on|off]`",
            on_msg="🗣️ 真人打字代說朗讀已設為：**開啟**。",
            off_msg="🗣️ 真人打字代說朗讀已設為：**關閉**。",
        )

    async def _handle_tts_vconly_toggle(self, ctx: commands.Context, arg: Optional[str]) -> None:
        await self._handle_tts_bool_toggle(
            ctx,
            arg,
            field="require_in_vc",
            usage="💡 用法：`!tts vconly [on|off]`",
            on_msg="🔒 已開啟「同語音房限制」：僅會朗讀與機器人在同一語音房內成員的訊息。",
            off_msg="🔓 已關閉「同語音房限制」：未進入語音頻道的成員在 TTS 對象頻道打字也能被朗讀！",
        )
