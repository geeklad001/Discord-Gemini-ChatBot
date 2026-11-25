"""Modular Voice TTS Cog components (pipeline, UI views, voice settings, clone service, admin commands)."""

from __future__ import annotations

from cogs.tts.admin_commands import TtsAdminMixin
from cogs.tts.clone_service import CloneServiceMixin
from cogs.tts.pipeline import (
    GuildVoicePipeline,
    TtsJob,
    count_non_bot_users_in_vc,
    resolve_user_voice_channel,
    sweep_orphaned_temp_files,
)
from cogs.tts.views import (
    BotVoiceSelectView,
    CloneAuditView,
    CloneReviewView,
    VoiceSelectView,
    build_voice_select_options,
    send_voice_preview,
)
from cogs.tts.voice_settings import VoiceSettingsMixin

__all__ = [
    "BotVoiceSelectView",
    "CloneAuditView",
    "CloneReviewView",
    "CloneServiceMixin",
    "GuildVoicePipeline",
    "TtsAdminMixin",
    "TtsJob",
    "VoiceSelectView",
    "VoiceSettingsMixin",
    "build_voice_select_options",
    "count_non_bot_users_in_vc",
    "resolve_user_voice_channel",
    "send_voice_preview",
    "sweep_orphaned_temp_files",
]
