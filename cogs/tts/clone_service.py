"""Voice clone submission, approval/audit moderation, revocation, and owner DM workflows."""

from __future__ import annotations

import asyncio
import io
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import discord
from discord.ext import commands

from cogs.tts.views import CloneAuditView, CloneReviewView
from engines import assistant_db, tts_engine


class CloneServiceMixin:
    """Mixin providing !voiceclone commands and persistent review/audit interaction handlers."""

    def _get_clone_user_lock(self, user_id: int) -> asyncio.Lock:
        return self._get_keyed_lock("_clone_user_locks", user_id)

    def _get_clone_review_lock(self, req_id: int) -> asyncio.Lock:
        return self._get_keyed_lock("_clone_review_locks", req_id)

    def _get_clone_global_lock(self) -> asyncio.Lock:
        lock = getattr(self, "_clone_global_lock", None)
        if lock is None:
            lock = asyncio.Lock()
            self._clone_global_lock = lock
        return lock

    def _get_clone_ffmpeg_sem(self) -> asyncio.Semaphore:
        sem = getattr(self, "_clone_ffmpeg_sem", None)
        if sem is None:
            sem = asyncio.Semaphore(2)
            self._clone_ffmpeg_sem = sem
        return sem

    def _format_owner_contact(self) -> str:
        if self.settings.bot_owner_ids:
            return "、".join(f"<@{uid}>" for uid in self.settings.bot_owner_ids)
        return "機器人擁有者（Bot Owner）"

    def _build_voice_clone_notice_embed(self) -> discord.Embed:
        owner_contact = self._format_owner_contact()
        embed = discord.Embed(
            title="🔒 專屬角色聲線申請說明",
            description=(
                "專屬語音克隆目前**不開放線上自助上傳或註冊**。\n"
                f"如欲新增專屬角色音色或個人聲音克隆，請直接聯絡 {owner_contact} 協助設定。"
            ),
            color=0xF59E0B,
        )
        embed.add_field(
            name="📩 申請前請準備",
            value=(
                "• 5～15 秒清晰無背景音樂的語音乾音檔（`.wav` / `.mp3`）與對應逐字稿\n"
                "• 告知希望設為「公開聲線（大家都能選）」或「僅限本人使用的私人聲線」"
            ),
            inline=False,
        )
        embed.add_field(
            name="⚡ 現在就能立刻使用的免費聲線",
            value=(
                "• 輸入 `!voice` 可直接切換內建 16 種標準聲線並微調語速／音高\n"
                "• 輸入 `!voice bot` 可設定機器人自己的說話聲音"
            ),
            inline=False,
        )
        embed.set_footer(text="此指令僅供申請說明，不會上傳或建立任何語音檔案")
        return embed

    async def _send_voice_clone_notice(self, ctx: commands.Context) -> None:
        await ctx.send(
            embed=self._build_voice_clone_notice_embed(),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _handle_voiceclone(self, ctx: commands.Context, raw_arg: Optional[str] = None) -> None:
        raw = (raw_arg or "").strip()
        tokens = raw.split()
        first = tokens[0].lower() if tokens else ""
        rest_after_first = raw.split(maxsplit=1)[1] if len(tokens) > 1 else ""

        # 擁有者專用的審核、撤銷與封鎖指令（不論實例開關皆可供擁有者維運與緊急撤銷）
        if first in ("pending", "queue", "待審", "審核"):
            await self._handle_clone_pending(ctx)
            return
        if first in ("approve", "核准", "通過"):
            await self._handle_clone_approve_cmd(ctx, rest_after_first)
            return
        if first in ("reject", "deny", "拒絕"):
            await self._handle_clone_reject_cmd(ctx, rest_after_first)
            return
        if first in ("revoke", "撤銷"):
            await self._handle_clone_revoke_cmd(ctx, rest_after_first)
            return
        if first in ("ban", "封鎖"):
            await self._handle_clone_ban_cmd(ctx, rest_after_first, ban=True)
            return
        if first in ("unban", "解封"):
            await self._handle_clone_ban_cmd(ctx, rest_after_first, ban=False)
            return
        if first in ("on", "off", "enable", "disable", "開啟", "關閉"):
            if not await self.is_bot_owner(ctx.author):
                await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可切換全服語音克隆開放狀態。")
                return
            enable_flag = first in ("on", "enable", "開啟")
            self.settings.enable_user_voice_clone = enable_flag
            state_str = (
                "🟢 **已開放**成員自助語音克隆"
                if enable_flag
                else "🔒 **已關閉**成員自助語音克隆（僅擁有者可使用）"
            )
            await ctx.send(f"⚙️ 本實例語音克隆狀態已更新：{state_str}！")
            return

        if not self.settings.enable_user_voice_clone:
            await self._send_voice_clone_notice(ctx)
            return

        if first in ("list", "ls", "slots", "清單"):
            await self._handle_clone_list(ctx, arg=tokens[1] if len(tokens) > 1 else None)
            return
        if first in ("delete", "del", "rm", "remove", "刪除"):
            await self._handle_clone_delete(ctx, tokens[1] if len(tokens) > 1 else None)
            return
        if first in ("cancel", "取消"):
            await self._handle_clone_cancel(ctx)
            return

        attachments = list(getattr(ctx.message, "attachments", None) or [])
        if attachments:
            await self._handle_clone_submission(ctx, raw, attachments[0])
            return

        if first in ("public", "pub", "公開", "share", "共享"):
            await self._handle_clone_request_public(ctx, tokens[1] if len(tokens) > 1 else None)
            return

        await ctx.send(embed=await asyncio.to_thread(self._build_voice_clone_guide_embed, ctx.author.id))

    @staticmethod
    def _parse_user_id_token(token: Optional[str]) -> Optional[int]:
        raw = (token or "").strip()
        m = re.match(r"^<@!?(\d+)>$", raw) or re.match(r"^(\d+)$", raw)
        return int(m.group(1)) if m else None

    def _build_voice_clone_guide_embed(self, user_id: int) -> discord.Embed:
        clones = tts_engine.list_user_clones(user_id=user_id)
        pending = tts_engine.list_pending_clone_requests(user_id=user_id)
        max_slots = self.settings.tts_clone_max_per_user
        total_clones = tts_engine.count_total_user_clones()

        is_auto = self.settings.tts_clone_approval_mode != "manual"
        mode_desc = (
            "上傳音檔通過安全檢查與音訊處理後，將**立即啟用為你的私人專屬聲線**（僅限本人可用，無須等待審核）；"
            "若希望分享給全服大家使用，可加上 `public`（或 `公開`）一併申請成為公開聲線！"
            if is_auto
            else "上傳音檔後會先經過安全檢查與音訊處理，並送交機器人擁有者一鍵審核通過即可使用！"
        )

        embed = discord.Embed(
            title="🎙️ 專屬語音克隆（自助建立）" if is_auto else "🎙️ 專屬語音克隆（申請審核）",
            description=(
                f"目前你的專屬槽位：**{len(clones)} / {max_slots}**｜"
                f"伺服器總額度：**{total_clones} / {self.settings.tts_clone_max_total}**\n"
                + mode_desc
            ),
            color=0xA855F7,
        )
        embed.add_field(
            name="📤 如何建立聲線（夾帶音檔傳送）",
            value=(
                "格式：`!voiceclone [public|公開] [槽位1或2] [聲線名稱 |] <音檔實際唸出的逐字稿>`\n"
                "• **私人自用（預設）**：`!voiceclone 溫柔旁白 | 大家好，今天一起來聊天吧！`\n"
                "• **同時申請成為公開聲線**：`!voiceclone public 溫柔旁白 | 大家好，今天一起來聊天吧！`\n"
                "• 音檔要求：**3～15 秒**清晰人聲（`.wav/.mp3/.m4a/.ogg/.flac`，≤ 5MB），逐字稿 5～120 字"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛠️ 管理我的聲線",
            value=(
                "`!voiceclone list` — 查看我的克隆槽位與公開／私人狀態\n"
                "`!voiceclone public <1|2>` — 將已建立的私人聲線申請開放為全服公開聲線\n"
                "`!voiceclone delete <1|2>` — 刪除指定槽位並釋放名額\n"
                "`!voiceclone cancel` — 取消待審申請（僅手動審核模式）\n"
                + (f"⏳ 目前有待審核申請 **#{pending[0]['id']}**（槽位 {pending[0]['slot']}）" if pending else "")
            ),
            inline=False,
        )
        embed.set_footer(text="成員克隆預設僅限本人使用並受隱私權限隔離；申請公開須經擁有者允許")
        return embed

    def _parse_clone_submission_args(
        self,
        raw: str,
        user_clones: list[dict],
        fallback_display_name: str,
    ) -> tuple[int, str, str, bool]:
        text = (raw or "").strip()
        max_slots = self.settings.tts_clone_max_per_user
        chosen_slot: Optional[int] = None
        request_public = False

        pub_tokens = {"public", "pub", "公開", "共享", "[公開]", "(公開)"}
        priv_tokens = {"private", "priv", "私人", "自用", "[私人]", "(私人)"}

        for _ in range(2):
            parts = text.split(maxsplit=1)
            if not parts:
                break
            head = parts[0].strip()
            head_low = head.lower()
            if chosen_slot is None and head.isdigit():
                cand = int(head)
                if 1 <= cand <= max_slots:
                    chosen_slot = cand
                    text = parts[1].strip() if len(parts) > 1 else ""
                    continue
            if head_low in pub_tokens:
                request_public = True
                text = parts[1].strip() if len(parts) > 1 else ""
                continue
            if head_low in priv_tokens:
                request_public = False
                text = parts[1].strip() if len(parts) > 1 else ""
                continue
            break

        if chosen_slot is None:
            used_slots = {int(c["slot"]) for c in user_clones}
            for s in range(1, max_slots + 1):
                if s not in used_slots:
                    chosen_slot = s
                    break
            if chosen_slot is None:
                chosen_slot = 1

        if "|" in text:
            raw_name, raw_prompt = text.split("|", 1)
        else:
            raw_name, raw_prompt = "", text

        voice_name = tts_engine.sanitize_clone_voice_name(
            raw_name,
            fallback=f"{fallback_display_name} #{chosen_slot}",
        )
        return chosen_slot, voice_name, raw_prompt.strip(), request_public

    async def _collect_owner_ids(self) -> list[int]:
        ids = list(self.settings.bot_owner_ids)
        if not ids:
            try:
                app_info = await self.bot.application_info()
                team = getattr(app_info, "team", None)
                owner_obj = getattr(team, "owner", None) or getattr(app_info, "owner", None)
                oid = getattr(owner_obj, "id", None)
                if isinstance(oid, int):
                    ids.append(oid)
            except Exception:
                pass
        return ids

    async def _notify_owners_embed(
        self,
        embed: discord.Embed,
        *,
        view: discord.ui.View,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        log_label: str = "",
    ) -> int:
        owner_ids = await self._collect_owner_ids()
        if not owner_ids:
            return 0
        sent = 0
        for oid in owner_ids:
            try:
                owner_user = self.bot.get_user(oid) or await self.bot.fetch_user(oid)
                if owner_user is None:
                    continue
                send_kwargs: dict = {
                    "embed": embed,
                    "view": view,
                    "allowed_mentions": discord.AllowedMentions.none(),
                }
                if file_bytes and filename:
                    send_kwargs["file"] = discord.File(io.BytesIO(file_bytes), filename=filename)
                await owner_user.send(**send_kwargs)
                sent += 1
            except Exception as exc:
                self.logger.warning(f"⚠️ 無法私訊擁有者 {oid} {log_label}: {exc}")
        return sent

    async def _notify_owners_for_clone_review(
        self,
        req: dict,
        wav_bytes: bytes,
        *,
        request_public: bool = False,
    ) -> int:
        scope_req_str = (
            "🌐 **申請成為全服公開聲線**"
            if request_public
            else "🔒 **僅申請本人私人自用（未申請公開）**"
        )
        embed = discord.Embed(
            title=f"🎙️ 語音克隆審核申請 #{req['id']}",
            description=(
                f"成員 <@{req['user_id']}>（`{req['user_name']}`）申請建立 F5-TTS 聲線（{scope_req_str}）。\n"
                "音檔已通過本機 FFmpeg 淨化轉檔（24kHz 單聲道 WAV），請試聽上方附件後點選下方按鈕決定："
            ),
            color=0x38BDF8,
        )
        embed.add_field(
            name="申請資訊",
            value=(
                f"• 槽位：**#{req['slot']}**（`u_{req['user_id']}_s{req['slot']}`）\n"
                f"• 聲線名稱：**{req['voice_name']}**｜申請範圍：{scope_req_str}\n"
                f"• 音檔時長：`{req['duration_sec']:.2f}` 秒"
            ),
            inline=False,
        )
        embed.add_field(name="📝 提交逐字稿（Prompt Text）", value=req["prompt_text"][:1024], inline=False)
        embed.add_field(
            name="⌨️ 指令備援（重啟後亦有效）",
            value=(
                f"`!voiceclone approve {req['id']} private` ／ "
                f"`!voiceclone approve {req['id']} public` ／ "
                f"`!voiceclone reject {req['id']} [原因]`"
            ),
            inline=False,
        )
        return await self._notify_owners_embed(
            embed,
            view=CloneReviewView(req["id"], request_public=request_public),
            file_bytes=wav_bytes,
            filename=f"clone_review_{req['id']}.wav",
            log_label=f"審核語音克隆 #{req['id']}",
        )

    async def _notify_owners_clone_auto_audit(
        self,
        *,
        voice_id: str,
        user_id: int,
        user_name: str,
        slot: int,
        voice_name: str,
        prompt_text: str,
        duration_sec: float = 0.0,
        wav_bytes: Optional[bytes] = None,
        request_public: bool = False,
    ) -> int:
        if request_public:
            title = f"🌐 成員申請公開語音克隆審核（{voice_name}）"
            desc = (
                f"成員 <@{user_id}>（`{user_name}`）新增了語音克隆（目前已先啟用供其本人使用），"
                "並**申請將此聲線開放為全服皆可選用的公開聲線**！\n"
                "請試聽音檔後，點選下方按鈕決定是否允許成為公開聲線："
            )
            color = 0x38BDF8
            scope_note = "🌐 申請成為公開聲線（等待擁有者允許）"
        else:
            title = f"🔔 成員新增私人語音克隆通知（{voice_name}）"
            desc = (
                f"成員 <@{user_id}>（`{user_name}`）已通過安全檢查並新增一組**私人克隆聲線**（僅限本人使用，未申請公開）。\n"
                "此通知供擁有者知悉與安全備查；若發現違規內容可點擊下方按鈕撤銷刪除："
            )
            color = 0x10B981
            scope_note = "🔒 僅限本人私人使用（未申請公開）"

        dur_note = f" | 時長：`{duration_sec:.2f}` 秒" if duration_sec > 0 else ""
        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(
            name="聲線資訊",
            value=(
                f"• 槽位：**#{slot}**（`{voice_id}`）\n"
                f"• 聲線名稱：**{voice_name}**{dur_note}\n"
                f"• 狀態：{scope_note}"
            ),
            inline=False,
        )
        embed.add_field(name="📝 提交逐字稿", value=prompt_text[:1024], inline=False)
        embed.add_field(
            name="⌨️ 指令管理",
            value=f"`!voiceclone revoke {user_id} {slot}` ／ `!voiceclone ban {user_id} [原因]`",
            inline=False,
        )
        return await self._notify_owners_embed(
            embed,
            view=CloneAuditView(voice_id, request_public=request_public),
            file_bytes=wav_bytes,
            filename=f"audit_{voice_id}.wav",
            log_label=f"備查語音克隆 {voice_id}",
        )

    async def _dm_user_safe(self, user_id: int, message_text: str) -> None:
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user is not None:
                await user.send(message_text)
        except Exception:
            pass

    async def _notify_clone_applicant(self, req: dict, message_text: str) -> None:
        uid = int(req["user_id"])
        ch_id = req.get("channel_id")
        if ch_id:
            ch = self.bot.get_channel(int(ch_id))
            if ch is not None and hasattr(ch, "send"):
                try:
                    await ch.send(
                        f"<@{uid}> {message_text}",
                        allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
                    )
                    return
                except Exception:
                    pass
        await self._dm_user_safe(uid, message_text)

    async def _revoke_managed_clone(
        self,
        voice_id: str,
        *,
        requester_user_id: Optional[int] = None,
        log_context: str = "",
    ) -> bool:
        """Delete a managed clone from F5 backend (when configured), DB records, and user/guild prefs."""
        if self.synthesizer.is_f5_configured:
            try:
                await self.synthesizer.delete_f5_voice(voice_id, requester_user_id=requester_user_id)
            except Exception as exc:
                ctx_note = f"{log_context}, " if log_context else ""
                self.logger.error(
                    f"❌ 撤銷/刪除克隆聲線失敗 ({ctx_note}voice_id={voice_id}): {exc}",
                    exc_info=True,
                )
                return False
        await asyncio.to_thread(tts_engine.delete_user_clone_record, voice_id)
        await asyncio.to_thread(tts_engine.reset_prefs_using_voice, f"f5:{voice_id}")
        self.synthesizer.invalidate_voice_cache(voice_id)
        return True

    @staticmethod
    async def _update_interaction_card(
        interaction: discord.Interaction,
        *,
        view: discord.ui.View,
        footer_text: Optional[str] = None,
    ) -> None:
        msg = getattr(interaction, "message", None)
        if msg is None:
            return
        try:
            embeds = list(getattr(msg, "embeds", None) or [])
            if footer_text and embeds:
                embeds[0].set_footer(text=footer_text)
            await msg.edit(embeds=embeds, view=view)
        except Exception:
            pass

    async def _clear_pending_requests(
        self,
        user_id: int,
        *,
        status: str,
        reviewer_id: int,
        review_note: str = "",
    ) -> int:
        pending = await asyncio.to_thread(tts_engine.list_pending_clone_requests, user_id=user_id)
        for p in pending:
            await asyncio.to_thread(
                tts_engine.update_clone_request_status,
                p["id"],
                status,
                reviewer_id=reviewer_id,
                review_note=review_note,
            )
            await asyncio.to_thread(tts_engine.delete_pending_clone_wav, p["sanitized_audio_path"])
        return len(pending)

    async def _handle_clone_review_decision(
        self,
        req_id: int,
        action: str,
        *,
        reviewer_id: int,
        review_note: str = "",
    ) -> tuple[bool, str, Optional[dict]]:
        async with self._get_clone_review_lock(req_id):
            req = await asyncio.to_thread(tts_engine.get_clone_request, req_id)
            if req is None:
                return False, f"❌ 找不到申請編號 `#{req_id}`。", None
            if req["status"] != "pending":
                return False, f"ℹ️ 申請 `#{req_id}` 已結案（目前狀態：`{req['status']}`）。", req

            if action in ("approve_priv", "approve_pub"):
                is_public = action == "approve_pub"
                pending_dir = tts_engine.get_pending_clones_dir()
                try:
                    wav_path = Path(req["sanitized_audio_path"]).resolve()
                except OSError as exc:
                    self.logger.error(
                        f"❌ 審核音檔路徑解析異常 (req=#{req_id}, path={req.get('sanitized_audio_path')}): {exc}"
                    )
                    return False, "❌ 審核音檔無法讀取，請通知申請人重新提交。", None
                if not wav_path.is_relative_to(pending_dir) or not await asyncio.to_thread(wav_path.is_file):
                    self.logger.error(f"❌ 暫存審核音檔遺失或越權 (req=#{req_id}, path={wav_path})")
                    return False, "❌ 審核音檔無法讀取，請通知申請人重新提交。", None

                wav_bytes = await asyncio.to_thread(wav_path.read_bytes)
                async with self._get_clone_global_lock():
                    try:
                        reg = await self.synthesizer.register_f5_voice(
                            audio_bytes=wav_bytes,
                            owner_user_id=req["user_id"],
                            slot=req["slot"],
                            name=req["voice_name"],
                            prompt_text=req["prompt_text"],
                            public=is_public,
                            max_per_user=self.settings.tts_clone_max_per_user,
                            max_total=self.settings.tts_clone_max_total,
                        )
                    except ConnectionError as exc:
                        self.logger.error(f"❌ 核准語音克隆 #{req_id} 失敗（語音後端未連線）: {exc}")
                        return (
                            False,
                            f"⚠️ 語音克隆服務暫時無法使用，申請 **#{req_id}** 仍保留於待審佇列，請稍後再試。",
                            None,
                        )
                    except Exception as exc:
                        self.logger.error(f"❌ 核准語音克隆 #{req_id} 註冊失敗: {exc}", exc_info=True)
                        return False, f"❌ 語音克隆註冊失敗，申請 **#{req_id}** 尚未變更，請稍後再試。", None

                    voice_id = reg["voice_id"]
                    await asyncio.to_thread(
                        tts_engine.upsert_user_clone,
                        voice_id=voice_id,
                        user_id=req["user_id"],
                        slot=req["slot"],
                        voice_name=req["voice_name"],
                        prompt_text=req["prompt_text"],
                        is_public=is_public,
                        approved_by=reviewer_id,
                    )
                    new_status = "approved_public" if is_public else "approved_private"
                    updated = await asyncio.to_thread(
                        tts_engine.update_clone_request_status,
                        req_id,
                        new_status,
                        reviewer_id=reviewer_id,
                        review_note=review_note,
                    )
                    self.synthesizer.invalidate_voice_cache(voice_id)

                await asyncio.to_thread(tts_engine.delete_pending_clone_wav, req["sanitized_audio_path"])

                scope_label = "公開聲線（全服成員皆可選）" if is_public else "私人聲線（僅限本人可使用）"
                await self._notify_clone_applicant(
                    req,
                    f"✅ 你的語音克隆申請 **#{req_id}**（槽位 {req['slot']}：**{req['voice_name']}**）已獲核准為 **{scope_label}**！\n"
                    f"現在輸入 `!voice {req['voice_name']}` 或 `!voice` 開啟下拉選單即可切換使用。",
                )
                return (
                    True,
                    f"✅ 已核准申請 **#{req_id}**（<@{req['user_id']}>｜槽位 {req['slot']}：`{voice_id}`）為 **{scope_label}**。",
                    updated,
                )

            if action == "reject":
                updated = await asyncio.to_thread(
                    tts_engine.update_clone_request_status,
                    req_id,
                    "rejected",
                    reviewer_id=reviewer_id,
                    review_note=review_note,
                )
                await asyncio.to_thread(tts_engine.delete_pending_clone_wav, req["sanitized_audio_path"])
                reason_suffix = f"\n原因：{review_note[:200]}" if review_note else ""
                await self._notify_clone_applicant(
                    req,
                    f"❌ 你的語音克隆申請 **#{req_id}**（**{req['voice_name']}**）未通過審核。{reason_suffix}",
                )
                return True, f"❌ 已拒絕申請 **#{req_id}**（<@{req['user_id']}>）。{reason_suffix}", updated

            return False, "❌ 未知的審核動作。", None

    async def _dispatch_clone_review_interaction(self, interaction: discord.Interaction) -> None:
        if getattr(interaction, "type", None) != discord.InteractionType.component:
            return
        data = getattr(interaction, "data", None) or {}
        cid = str(data.get("custom_id") or "")
        if cid.startswith(("vc_approve_priv:", "vc_approve_pub:", "vc_reject:")):
            prefix, _, raw_id = cid.partition(":")
            if not raw_id.isdigit():
                return
            action_map = {
                "vc_approve_priv": "approve_priv",
                "vc_approve_pub": "approve_pub",
                "vc_reject": "reject",
            }
            await self._handle_clone_review_button(interaction, action_map[prefix], int(raw_id))
            return

        if cid.startswith(("vc_promote_pub:", "vc_keep_priv:", "vc_audit_revoke:")):
            prefix, _, voice_id = cid.partition(":")
            if not re.match(r"^u_[0-9]{1,25}_s[1-5]$", voice_id):
                return
            audit_action_map = {
                "vc_promote_pub": "promote_pub",
                "vc_keep_priv": "keep_priv",
                "vc_audit_revoke": "revoke",
            }
            await self._handle_clone_audit_button(interaction, audit_action_map[prefix], voice_id)

    async def _handle_clone_audit_button(
        self,
        interaction: discord.Interaction,
        action: str,
        voice_id: str,
    ) -> None:
        if not await self.is_bot_owner(interaction.user):
            await interaction.response.send_message(
                "🔒 僅限機器人擁有者（Bot Owner）可操作語音克隆備查權限。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        existing = await asyncio.to_thread(tts_engine.get_user_clone, voice_id)
        if existing is None:
            await self._update_interaction_card(interaction, view=CloneAuditView(voice_id, all_disabled=True))
            await interaction.followup.send(f"ℹ️ 聲線 `{voice_id}` 已不存在或已被刪除。", ephemeral=True)
            return

        if action == "promote_pub":
            try:
                await self.synthesizer.set_f5_voice_visibility(voice_id, public=True)
            except Exception as exc:
                self.logger.error(f"❌ 升級公開聲線失敗 (voice_id={voice_id}): {exc}", exc_info=True)
                await interaction.followup.send(
                    "❌ 升級公開聲線失敗，語音服務暫時無法回應，請稍後再試。",
                    ephemeral=True,
                )
                return
            await asyncio.to_thread(tts_engine.update_user_clone_visibility, voice_id, True)
            self.synthesizer.invalidate_voice_cache(voice_id)
            await self._update_interaction_card(
                interaction,
                footer_text=f"已由擁有者允許成為公開聲線 • {voice_id}",
                view=CloneAuditView(voice_id, request_public=True, promote_disabled=True),
            )
            await self._dm_user_safe(
                existing["user_id"],
                f"🌐 你的語音克隆 **「{existing['voice_name']}」**（槽位 #{existing['slot']}）"
                "已獲機器人擁有者允許成為**全服公開聲線**！現在所有成員與機器人皆可選用此聲線。",
            )
            await interaction.followup.send(
                f"🌐 已允許 <@{existing['user_id']}> 的克隆聲線 `{voice_id}`（**{existing['voice_name']}**）成為公開聲線！",
                ephemeral=True,
            )
            return

        if action == "keep_priv":
            await self._update_interaction_card(
                interaction,
                footer_text=f"已由擁有者設定維持私人聲線（不公開） • {voice_id}",
                view=CloneAuditView(voice_id, request_public=True, promote_disabled=True),
            )
            await self._dm_user_safe(
                existing["user_id"],
                f"🔒 你的語音克隆 **「{existing['voice_name']}」**（槽位 #{existing['slot']}）"
                "未開放為全服公開，仍保留為你的**私人專屬聲線**供你本人使用。",
            )
            await interaction.followup.send(
                f"🔒 已維持 <@{existing['user_id']}> 的聲線 `{voice_id}`（**{existing['voice_name']}**）為私人專屬（不公開）。",
                ephemeral=True,
            )
            return

        if action == "revoke":
            if not await self._revoke_managed_clone(voice_id, requester_user_id=None, log_context="audit"):
                await interaction.followup.send(
                    "❌ 撤銷聲線失敗，語音服務暫時無法回應，請稍後再試。",
                    ephemeral=True,
                )
                return
            await self._update_interaction_card(
                interaction,
                footer_text=f"已由擁有者事後撤銷刪除 • {voice_id}",
                view=CloneAuditView(voice_id, all_disabled=True),
            )
            await interaction.followup.send(
                f"🗑️ 已事後撤銷並刪除 <@{existing['user_id']}> 的克隆聲線 `{voice_id}`。",
                ephemeral=True,
            )

    async def _handle_clone_review_button(
        self,
        interaction: discord.Interaction,
        action: str,
        req_id: int,
    ) -> None:
        if not await self.is_bot_owner(interaction.user):
            await interaction.response.send_message(
                "🔒 僅限機器人擁有者（Bot Owner）可審核語音克隆申請。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        ok, summary, updated = await self._handle_clone_review_decision(
            req_id, action, reviewer_id=interaction.user.id
        )
        if ok:
            status_text = (updated or {}).get("status", action)
            await self._update_interaction_card(
                interaction,
                footer_text=f"已由擁有者處理完成 • 狀態：{status_text}",
                view=CloneReviewView(req_id, disabled=True),
            )
        await interaction.followup.send(summary, ephemeral=True)

    async def _handle_clone_request_public(self, ctx: commands.Context, slot_arg: Optional[str]) -> None:
        if not self.synthesizer.is_f5_configured:
            self.logger.warning("⚠️ 成員嘗試申請公開語音克隆，但 TTS_API_URL 未設定。")
            await ctx.send("⚠️ 語音克隆服務目前尚未開放，請聯絡管理員。")
            return

        ban_row = await asyncio.to_thread(tts_engine.get_clone_ban, ctx.author.id)
        if ban_row is not None:
            reason_txt = f"（原因：{ban_row['reason']}）" if ban_row.get("reason") else ""
            await ctx.send(self._clip_reply(f"🚫 你已被限制申請語音克隆{reason_txt}。"))
            return

        is_owner = await self.is_bot_owner(ctx.author)
        cooldowns = getattr(self, "_clone_public_cooldowns", None)
        if cooldowns is None:
            cooldowns = {}
            self._clone_public_cooldowns = cooldowns
        if not is_owner and self.settings.tts_clone_cooldown_seconds > 0:
            last_pub = cooldowns.get(ctx.author.id)
            if last_pub is not None:
                elapsed = time.monotonic() - last_pub
                if 0 <= elapsed < self.settings.tts_clone_cooldown_seconds:
                    rem = max(1, int(self.settings.tts_clone_cooldown_seconds - elapsed))
                    await ctx.send(f"⏱️ 提交公開申請過於頻繁，請稍候 **{rem} 秒** 後再試。")
                    return

        clones = await asyncio.to_thread(tts_engine.list_user_clones, user_id=ctx.author.id)
        if not clones:
            await ctx.send(
                "ℹ️ 你目前還沒有已建立的語音克隆。若要建立並同時申請公開，請夾帶音檔傳送：\n"
                "`!voiceclone public [聲線名稱 |] <音檔逐字稿>`"
            )
            return

        chosen_slot: Optional[int] = None
        if slot_arg and slot_arg.strip().isdigit():
            chosen_slot = int(slot_arg.strip())
        elif len(clones) == 1:
            chosen_slot = int(clones[0]["slot"])
        else:
            await ctx.send("💡 你有多組克隆聲線，請指定槽位編號：`!voiceclone public <1|2>`")
            return

        target = next((c for c in clones if int(c["slot"]) == chosen_slot), None)
        if target is None:
            await ctx.send(f"❌ 你的槽位 **{chosen_slot}** 目前沒有已啟用的克隆聲線。")
            return
        if target.get("is_public"):
            await ctx.send(f"🌐 你的槽位 **{chosen_slot}**（**{target['voice_name']}**）已經是公開聲線囉！")
            return

        voice_id = str(target["voice_id"])

        def _read_local_clone_wav() -> Optional[bytes]:
            if not tts_engine.MANAGED_VOICE_ID_RE.match(voice_id):
                return None
            clones_dir = (assistant_db.DATA_DIR / "tts_voices" / "user_clones").resolve()
            candidate = (clones_dir / f"{voice_id}.wav").resolve()
            if not candidate.is_relative_to(clones_dir) or not candidate.is_file():
                return None
            return candidate.read_bytes()

        try:
            wav_bytes = await asyncio.to_thread(_read_local_clone_wav)
        except Exception:
            wav_bytes = None

        cooldowns[ctx.author.id] = time.monotonic()
        await self._notify_owners_clone_auto_audit(
            voice_id=voice_id,
            user_id=ctx.author.id,
            user_name=getattr(ctx.author, "display_name", str(ctx.author.id)),
            slot=chosen_slot,
            voice_name=target["voice_name"],
            prompt_text=target["prompt_text"],
            duration_sec=0.0,
            wav_bytes=wav_bytes,
            request_public=True,
        )
        await ctx.send(
            f"📨 已將你的槽位 **#{chosen_slot}**（**{target['voice_name']}**）送出「🌐 公開聲線」申請，待機器人擁有者允許後即開放全服成員選用！"
        )

    async def _submit_clone_auto(
        self,
        ctx: commands.Context,
        *,
        slot: int,
        voice_name: str,
        clean_prompt: str,
        wav_bytes: bytes,
        duration_sec: float,
        overwriting: bool,
        request_public: bool,
    ) -> None:
        async with self._get_clone_global_lock():
            if not overwriting:
                total_clones = await asyncio.to_thread(tts_engine.count_total_user_clones)
                if total_clones >= self.settings.tts_clone_max_total:
                    await ctx.send(
                        f"⚠️ 伺服器成員語音克隆總數已達上限（{total_clones}/{self.settings.tts_clone_max_total}），暫無法新增新槽位。"
                    )
                    return
            try:
                reg = await self.synthesizer.register_f5_voice(
                    audio_bytes=wav_bytes,
                    owner_user_id=ctx.author.id,
                    slot=slot,
                    name=voice_name,
                    prompt_text=clean_prompt,
                    public=False,
                    max_per_user=self.settings.tts_clone_max_per_user,
                    max_total=self.settings.tts_clone_max_total,
                )
            except ConnectionError as exc:
                self.logger.error(
                    f"❌ 語音克隆自動註冊失敗（後端未連線, user={ctx.author.id}, slot={slot}）: {exc}"
                )
                await ctx.send("⚠️ 語音克隆服務暫時無法使用，請稍後再試或通知管理員。")
                return
            except Exception as exc:
                self.logger.error(
                    f"❌ 語音克隆自動註冊失敗 (user={ctx.author.id}, slot={slot}): {exc}",
                    exc_info=True,
                )
                await ctx.send("❌ 語音克隆註冊失敗，請稍後再試或通知管理員。")
                return

            voice_id = reg["voice_id"]
            await asyncio.to_thread(
                tts_engine.upsert_user_clone,
                voice_id=voice_id,
                user_id=ctx.author.id,
                slot=slot,
                voice_name=voice_name,
                prompt_text=clean_prompt,
                is_public=False,
                approved_by=ctx.author.id,
            )
            await asyncio.to_thread(
                tts_engine.create_clone_request,
                guild_id=ctx.guild.id if ctx.guild else None,
                channel_id=ctx.channel.id if ctx.channel else None,
                user_id=ctx.author.id,
                user_name=getattr(ctx.author, "display_name", str(ctx.author.id)),
                slot=slot,
                voice_name=voice_name,
                prompt_text=clean_prompt,
                sanitized_audio_path="",
                duration_sec=duration_sec,
                status="auto_approved",
            )
            self.synthesizer.invalidate_voice_cache(voice_id)

        overwrite_note = "（已覆蓋此槽位舊聲線）" if overwriting else ""
        if request_public:
            pub_status_note = "🔒 目前已先啟用供你本人使用｜⏳ 已同步送出「🌐 公開聲線」申請，待擁有者允許後即開放全服使用"
            share_tip = f"• 你現在就可以先輸入 `!voice {voice_name}` 或 `!voice` 切換自己使用！"
        else:
            pub_status_note = "🔒 僅限本人使用（未申請公開）"
            share_tip = (
                f"• 現在輸入 `!voice {voice_name}` 或 `!voice` 即可切換使用！"
                f"（日後若想分享給大家用，可輸入 `!voiceclone public {slot}` 申請公開）"
            )
        await ctx.send(
            self._clip_reply(
                f"✅ 你的專屬語音克隆已通過安全檢查並**立即啟用**！{overwrite_note}\n"
                f"• 槽位：**#{slot}**（`{voice_name}`｜ID:`{voice_id}`｜{pub_status_note}｜時長 `{duration_sec:.2f}` 秒）\n"
                + share_tip
            )
        )
        await self._notify_owners_clone_auto_audit(
            voice_id=voice_id,
            user_id=ctx.author.id,
            user_name=getattr(ctx.author, "display_name", str(ctx.author.id)),
            slot=slot,
            voice_name=voice_name,
            prompt_text=clean_prompt,
            duration_sec=duration_sec,
            wav_bytes=wav_bytes,
            request_public=request_public,
        )

    async def _submit_clone_manual(
        self,
        ctx: commands.Context,
        *,
        slot: int,
        voice_name: str,
        clean_prompt: str,
        wav_bytes: bytes,
        duration_sec: float,
        overwriting: bool,
        request_public: bool,
    ) -> None:
        async with self._get_clone_global_lock():
            wav_path = await asyncio.to_thread(tts_engine.save_pending_clone_wav, wav_bytes)
            req = await asyncio.to_thread(
                tts_engine.create_clone_request,
                guild_id=ctx.guild.id if ctx.guild else None,
                channel_id=ctx.channel.id if ctx.channel else None,
                user_id=ctx.author.id,
                user_name=getattr(ctx.author, "display_name", str(ctx.author.id)),
                slot=slot,
                voice_name=voice_name,
                prompt_text=clean_prompt,
                sanitized_audio_path=str(wav_path),
                duration_sec=duration_sec,
            )

        dm_cnt = await self._notify_owners_for_clone_review(req, wav_bytes, request_public=request_public)
        overwrite_note = "（審核通過後將覆蓋該槽位舊聲線）" if overwriting else ""
        dm_note = (
            "已透過私訊傳送試聽與一鍵審核卡片給機器人擁有者。"
            if dm_cnt > 0
            else "已寫入待審佇列（擁有者可使用 `!voiceclone pending` 查看與審核）。"
        )
        scope_tag = "🌐 申請公開聲線" if request_public else "🔒 申請私人自用"
        await ctx.send(
            self._clip_reply(
                f"📨 已送出語音克隆申請 **#{req['id']}**（{scope_tag}）！{overwrite_note}\n"
                f"• 槽位：**#{slot}**（`{voice_name}`） | 時長：`{duration_sec:.2f}` 秒\n"
                f"• {dm_note}"
            )
        )

    async def _handle_clone_submission(
        self,
        ctx: commands.Context,
        raw: str,
        attachment: discord.Attachment,
    ) -> None:
        async with self._get_clone_user_lock(ctx.author.id):
            if not self.synthesizer.is_f5_configured:
                self.logger.warning("⚠️ 成員嘗試申請語音克隆，但 TTS_API_URL 未設定。")
                await ctx.send("⚠️ 語音克隆服務目前尚未開放，請聯絡管理員。")
                return

            ban_row = await asyncio.to_thread(tts_engine.get_clone_ban, ctx.author.id)
            if ban_row is not None:
                reason_txt = f"（原因：{ban_row['reason']}）" if ban_row.get("reason") else ""
                await ctx.send(self._clip_reply(f"🚫 你已被限制申請語音克隆{reason_txt}。"))
                return

            pending = await asyncio.to_thread(tts_engine.list_pending_clone_requests, user_id=ctx.author.id)
            if pending:
                await ctx.send(
                    f"⏳ 你目前已有待審核的申請 **#{pending[0]['id']}**（槽位 {pending[0]['slot']}），"
                    "請等待審核完成或輸入 `!voiceclone cancel` 取消後再重新提交。"
                )
                return

            is_owner = await self.is_bot_owner(ctx.author)
            if not is_owner and self.settings.tts_clone_cooldown_seconds > 0:
                last_ts = await asyncio.to_thread(tts_engine.get_user_last_clone_request_ts, ctx.author.id)
                if last_ts is not None:
                    elapsed = (datetime.now(tts_engine.TZ_TAIPEI) - last_ts).total_seconds()
                    if 0 <= elapsed < self.settings.tts_clone_cooldown_seconds:
                        rem = int(self.settings.tts_clone_cooldown_seconds - elapsed)
                        await ctx.send(f"⏱️ 提交過於頻繁，請稍候 **{rem} 秒** 後再提交新的語音克隆申請。")
                        return

            user_clones = await asyncio.to_thread(tts_engine.list_user_clones, user_id=ctx.author.id)
            slot, voice_name, raw_prompt, request_public = self._parse_clone_submission_args(
                raw, user_clones, getattr(ctx.author, "display_name", f"User{ctx.author.id}")
            )
            overwriting = any(int(c["slot"]) == slot for c in user_clones)
            if not overwriting:
                total_clones = await asyncio.to_thread(tts_engine.count_total_user_clones)
                if total_clones >= self.settings.tts_clone_max_total:
                    await ctx.send(
                        f"⚠️ 伺服器成員語音克隆總數已達上限（{total_clones}/{self.settings.tts_clone_max_total}），暫無法新增新槽位。"
                    )
                    return

            ok_prompt, clean_prompt_or_err = tts_engine.validate_clone_prompt_text(raw_prompt)
            if not ok_prompt:
                await ctx.send(f"❌ {clean_prompt_or_err}")
                return

            att_size = int(getattr(attachment, "size", 0) or 0)
            if att_size > tts_engine.CLONE_MAX_UPLOAD_BYTES:
                await ctx.send(
                    f"❌ 附件檔案過大（{att_size / (1024 * 1024):.1f} MB），上限為 {tts_engine.CLONE_MAX_UPLOAD_BYTES // (1024 * 1024)} MB。"
                )
                return

            att_name = str(getattr(attachment, "filename", "") or "")
            ext = Path(att_name).suffix.lower()
            if ext not in tts_engine.CLONE_ALLOWED_EXTENSIONS:
                await ctx.send(
                    f"❌ 不支援的音訊副檔名 `{ext or '未知'}`，僅接受 `wav / mp3 / m4a / ogg / flac`。"
                )
                return

            try:
                async with self._get_clone_ffmpeg_sem():
                    raw_bytes = await attachment.read()
                    wav_bytes, duration_sec = await tts_engine.sanitize_clone_audio_bytes(raw_bytes, att_name)
            except ValueError as exc:
                self.logger.info(f"⏭️ [VoiceClone] 成員 {ctx.author.id} 音檔檢查未通過: {exc}")
                await ctx.send(f"❌ 音檔檢查未通過：{exc}")
                return
            except Exception as exc:
                self.logger.error(f"❌ 語音克隆音訊處理異常 (user={ctx.author.id}, file={att_name}): {exc}", exc_info=True)
                await ctx.send("❌ 音訊處理暫時發生問題，請稍後再試或通知管理員。")
                return

            submit_fn = (
                self._submit_clone_auto
                if self.settings.tts_clone_approval_mode != "manual"
                else self._submit_clone_manual
            )
            await submit_fn(
                ctx,
                slot=slot,
                voice_name=voice_name,
                clean_prompt=clean_prompt_or_err,
                wav_bytes=wav_bytes,
                duration_sec=duration_sec,
                overwriting=overwriting,
                request_public=request_public,
            )

    async def _handle_clone_list(self, ctx: commands.Context, arg: Optional[str] = None) -> None:
        show_all = (arg or "").strip().lower() in ("all", "全部") and await self.is_bot_owner(ctx.author)
        clones = await asyncio.to_thread(tts_engine.list_user_clones, user_id=None if show_all else ctx.author.id)
        pending = await asyncio.to_thread(
            tts_engine.list_pending_clone_requests, user_id=None if show_all else ctx.author.id
        )

        title = (
            "🎙️ 全伺服器成員克隆清單"
            if show_all
            else f"🎙️ {str(ctx.author.display_name)[:64]} 的語音克隆槽位"
        )
        lines: list[str] = []
        if clones:
            for c in clones[:20]:
                vis = "🌐 公開" if c["is_public"] else "🔒 私人"
                owner_tag = f" (<@{c['user_id']}>)" if show_all else ""
                lines.append(f"• 槽位 **{c['slot']}**{owner_tag}：`{c['voice_name']}` ({vis}｜ID:`{c['voice_id']}`)")
        else:
            lines.append("• 目前尚無已啟用的克隆聲線。")

        if pending:
            lines.append("\n**⏳ 待審核申請**")
            for p in pending[:10]:
                owner_tag = f" <@{p['user_id']}>" if show_all else ""
                lines.append(f"• **#{p['id']}**{owner_tag} 槽位 {p['slot']}：`{p['voice_name']}` ({p['duration_sec']:.1f}s)")

        embed = discord.Embed(title=title, description="\n".join(lines)[:3900], color=0xA855F7)
        total_clones = await asyncio.to_thread(tts_engine.count_total_user_clones)
        embed.set_footer(
            text=f"個人上限：{self.settings.tts_clone_max_per_user} 組｜全服上限：{total_clones}/{self.settings.tts_clone_max_total}"
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _handle_clone_delete(self, ctx: commands.Context, slot_arg: Optional[str]) -> None:
        if not slot_arg or not slot_arg.strip().isdigit():
            await ctx.send("💡 用法：`!voiceclone delete <1|2>`（請指定要刪除的槽位編號）。")
            return
        slot = int(slot_arg.strip())
        voice_id = f"u_{ctx.author.id}_s{slot}"
        clones = await asyncio.to_thread(tts_engine.list_user_clones, user_id=ctx.author.id)
        target = next((c for c in clones if c["voice_id"] == voice_id), None)
        if target is None:
            await ctx.send(f"❌ 你的槽位 **{slot}** 目前沒有已註冊的克隆聲線。")
            return

        if not await self._revoke_managed_clone(
            voice_id,
            requester_user_id=ctx.author.id,
            log_context=f"user={ctx.author.id}",
        ):
            await ctx.send("⚠️ 暫時無法移除此聲線，語音服務目前無法回應，請稍後再試。")
            return

        await ctx.send(f"🗑️ 已刪除你的槽位 **{slot}** 克隆聲線：**{target['voice_name']}**。")

    async def _handle_clone_cancel(self, ctx: commands.Context) -> None:
        count = await self._clear_pending_requests(ctx.author.id, status="canceled", reviewer_id=ctx.author.id)
        if count == 0:
            await ctx.send("ℹ️ 你目前沒有待審核中的語音克隆申請。")
            return
        await ctx.send(f"🚫 已取消你的待審語音克隆申請（共 {count} 筆）並清除暫存音檔。")

    async def _handle_clone_pending(self, ctx: commands.Context) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可查看全服待審語音克隆清單。")
            return
        pending = await asyncio.to_thread(tts_engine.list_pending_clone_requests)
        if not pending:
            await ctx.send("✅ 目前沒有待審核的語音克隆申請。")
            return
        lines = [
            f"• **#{p['id']}** <@{p['user_id']}>｜槽位 {p['slot']}：`{p['voice_name']}` ({p['duration_sec']:.1f}s) — 「{p['prompt_text'][:35]}」"
            for p in pending[:15]
        ]
        lines.append("\n👉 使用 `!voiceclone approve <ID> [private|public]` 或 `!voiceclone reject <ID> [原因]` 審核")
        embed = discord.Embed(title="📋 待審核語音克隆申請", description="\n".join(lines)[:3900], color=0x38BDF8)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def _handle_clone_approve_cmd(self, ctx: commands.Context, arg: str) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可核准語音克隆申請。")
            return
        tokens = arg.strip().split()
        if not tokens or not tokens[0].isdigit():
            await ctx.send("💡 用法：`!voiceclone approve <申請編號> [private|public]`")
            return
        req_id = int(tokens[0])
        scope = tokens[1].lower() if len(tokens) > 1 else "private"
        action = "approve_pub" if scope in ("public", "pub", "公開") else "approve_priv"
        _, summary, _ = await self._handle_clone_review_decision(req_id, action, reviewer_id=ctx.author.id)
        await ctx.send(self._clip_reply(summary), allowed_mentions=discord.AllowedMentions.none())

    async def _handle_clone_reject_cmd(self, ctx: commands.Context, arg: str) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可拒絕語音克隆申請。")
            return
        tokens = arg.strip().split(maxsplit=1)
        if not tokens or not tokens[0].isdigit():
            await ctx.send("💡 用法：`!voiceclone reject <申請編號> [原因]`")
            return
        req_id = int(tokens[0])
        reason = tokens[1].strip() if len(tokens) > 1 else ""
        _, summary, _ = await self._handle_clone_review_decision(
            req_id, "reject", reviewer_id=ctx.author.id, review_note=reason
        )
        await ctx.send(self._clip_reply(summary), allowed_mentions=discord.AllowedMentions.none())

    async def _handle_clone_revoke_cmd(self, ctx: commands.Context, arg: str) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可強制撤銷成員克隆聲線。")
            return
        tokens = arg.strip().split()
        target_uid = self._parse_user_id_token(tokens[0] if tokens else None)
        if target_uid is None:
            await ctx.send("💡 用法：`!voiceclone revoke <@成員或UserID> [1|2|all]`")
            return
        slot_filter = int(tokens[1]) if len(tokens) > 1 and tokens[1].isdigit() else None

        clones = await asyncio.to_thread(tts_engine.list_user_clones, user_id=target_uid)
        targets = [c for c in clones if slot_filter is None or int(c["slot"]) == slot_filter]
        if not targets:
            await ctx.send(f"ℹ️ 找不到 <@{target_uid}> 符合條件的託管克隆聲線。", allowed_mentions=discord.AllowedMentions.none())
            return

        removed: list[str] = []
        for c in targets:
            vid = c["voice_id"]
            if not await self._revoke_managed_clone(vid, requester_user_id=None, log_context=f"target={target_uid}"):
                await ctx.send(f"⚠️ 暫時無法撤銷聲線 `{vid}`，語音服務目前無法回應，請稍後再試。")
                return
            removed.append(f"`{vid}` ({c['voice_name']})")

        await ctx.send(
            self._clip_reply(f"🗑️ 已強制撤銷 <@{target_uid}> 的克隆聲線：" + "、".join(removed)),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _handle_clone_ban_cmd(self, ctx: commands.Context, arg: str, *, ban: bool) -> None:
        if not await self.is_bot_owner(ctx.author):
            await ctx.send("🔒 僅限機器人擁有者（Bot Owner）可設定語音克隆黑名單。")
            return
        tokens = arg.strip().split(maxsplit=1)
        target_uid = self._parse_user_id_token(tokens[0] if tokens else None)
        if target_uid is None:
            cmd = "ban" if ban else "unban"
            await ctx.send(f"💡 用法：`!voiceclone {cmd} <@成員或UserID>{' [原因]' if ban else ''}`")
            return

        if ban:
            reason = tokens[1].strip()[:150] if len(tokens) > 1 else ""
            await asyncio.to_thread(tts_engine.ban_clone_user, target_uid, ctx.author.id, reason)
            await self._clear_pending_requests(
                target_uid, status="rejected", reviewer_id=ctx.author.id, review_note="已封鎖申請權限"
            )
            await ctx.send(
                self._clip_reply(
                    f"🚫 已禁止 <@{target_uid}> 提交語音克隆申請{f'（原因：{reason}）' if reason else ''}，並清空其待審請求。"
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            unbanned = await asyncio.to_thread(tts_engine.unban_clone_user, target_uid)
            msg = f"✅ 已解除 <@{target_uid}> 的語音克隆申請限制。" if unbanned else f"ℹ️ <@{target_uid}> 目前不在黑名單中。"
            await ctx.send(msg, allowed_mentions=discord.AllowedMentions.none())
