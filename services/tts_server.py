"""Self-hosted lazy-load F5-TTS voice server with idle VRAM auto-unload and clone management."""

from __future__ import annotations

import asyncio
import gc
import hmac
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _disable_windows_quickedit() -> None:
    """Disable Windows Console QuickEdit Mode so clicking the terminal never freezes the server."""
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        h_stdin = kernel32.GetStdHandle(-10)  # STD_INPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(h_stdin, ctypes.byref(mode)):
            # Clear ENABLE_QUICK_EDIT_MODE (0x0040), set ENABLE_EXTENDED_FLAGS (0x0080)
            new_mode = (mode.value & ~0x0040) | 0x0080
            kernel32.SetConsoleMode(h_stdin, new_mode)
    except Exception:
        pass


_disable_windows_quickedit()

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("MPLCONFIGDIR", os.path.join(tempfile.gettempdir(), "matplotlib"))

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _register_torch_cuda_dlls() -> None:
    """Ensure Windows PyTorch CUDA DLL directories are discoverable regardless of cwd."""
    candidates = [
        Path(sys.prefix) / "Lib" / "site-packages" / "torch" / "lib",
        PROJECT_ROOT / ".venv_tts" / "Lib" / "site-packages" / "torch" / "lib",
    ]
    seen: set[Path] = set()
    for lib_dir in candidates:
        resolved = lib_dir.resolve()
        if resolved in seen or not resolved.is_dir():
            continue
        seen.add(resolved)
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(str(resolved))
            except Exception:
                pass
        lib_str = str(resolved)
        if lib_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = lib_str + os.pathsep + os.environ.get("PATH", "")


_register_torch_cuda_dlls()

import soundfile as sf
import torch
import uvicorn
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import Response

try:
    from huggingface_hub import try_to_load_from_cache
except Exception:
    try_to_load_from_cache = None  # type: ignore[assignment]

try:
    from huggingface_hub import _CACHED_NO_EXIST
except Exception:
    _CACHED_NO_EXIST = object()


def _parse_bool_value(raw: Any, default: Optional[bool]) -> Optional[bool]:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        return bool(raw)
    if not isinstance(raw, str):
        return default
    clean = raw.split("#", 1)[0].strip().lower()
    if not clean:
        return default
    if clean in ("true", "1", "yes", "on"):
        return True
    if clean in ("false", "0", "no", "off"):
        return False
    return default


def _env_bool(name: str, default: bool) -> bool:
    res = _parse_bool_value(os.getenv(name, ""), default)
    return default if res is None else res


def _env_int(name: str, default: int, min_val: int = 0, max_val: int = 86400) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    return max(min_val, min(max_val, val))


def get_default_data_dir() -> Path:
    """Resolve the F5-TTS voice data directory dynamically (supports F5_TTS_DATA_DIR override)."""
    raw = os.getenv("F5_TTS_DATA_DIR", "").strip()
    if raw:
        return Path(raw).resolve()
    return (PROJECT_ROOT / "data" / "tts_voices").resolve()


F5_TTS_HOST = os.getenv("F5_TTS_HOST", "127.0.0.1").strip() or "127.0.0.1"
F5_TTS_PORT = _env_int("F5_TTS_PORT", 9880, min_val=1, max_val=65535)
F5_IDLE_UNLOAD_SECONDS = _env_int("F5_IDLE_UNLOAD_SECONDS", 1800, min_val=0, max_val=86400)
F5_NFE_STEP = _env_int("F5_NFE_STEP", 16, min_val=8, max_val=64)
F5_OFFLINE_FIRST = _env_bool("F5_OFFLINE_FIRST", True)
F5_PRELOAD_ON_STARTUP = _env_bool("F5_PRELOAD_ON_STARTUP", False)

SERVER_PREFS_FILENAME = "server_prefs.json"


def _resolve_local_hf_cache() -> tuple[Optional[str], Optional[str]]:
    """Resolve locally cached F5-TTS checkpoint and Vocos directory without Hugging Face HTTP checks."""
    if try_to_load_from_cache is None:
        return None, None
    try:
        ckpt = try_to_load_from_cache("SWivid/F5-TTS", "F5TTS_v1_Base/model_1250000.safetensors")
        vocos_cfg = try_to_load_from_cache("charactr/vocos-mel-24khz", "config.yaml")
        vocos_pt = try_to_load_from_cache("charactr/vocos-mel-24khz", "pytorch_model.bin")
        for item in (ckpt, vocos_cfg, vocos_pt):
            if item is None or item is _CACHED_NO_EXIST or not isinstance(item, (str, Path)):
                return None, None
            if not str(item).strip():
                return None, None
        ckpt_path = Path(ckpt)
        vocos_cfg_path = Path(vocos_cfg)
        vocos_pt_path = Path(vocos_pt)
        if not (ckpt_path.is_file() and vocos_cfg_path.is_file() and vocos_pt_path.is_file()):
            return None, None
        if vocos_cfg_path.parent != vocos_pt_path.parent:
            return None, None
        return str(ckpt_path), str(vocos_cfg_path.parent)
    except Exception:
        return None, None


def _sanitize_server_prefs(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    if "idle_unload_seconds" in raw:
        val = raw["idle_unload_seconds"]
        if not isinstance(val, bool):
            try:
                clean["idle_unload_seconds"] = max(0, min(86400, int(val)))
            except (TypeError, ValueError):
                pass
    for key in ("preload_on_startup", "offline_first"):
        if key in raw:
            parsed = _parse_bool_value(raw[key], None)
            if parsed is not None:
                clean[key] = parsed
    return clean


def load_server_prefs(data_dir: Path) -> dict[str, Any]:
    """Load non-secret runtime preferences from data_dir/server_prefs.json."""
    prefs_file = Path(data_dir) / SERVER_PREFS_FILENAME
    if not prefs_file.is_file():
        return {}
    try:
        with open(prefs_file, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        print(f"⚠️ 讀取伺服器偏好設定失敗 ({prefs_file.name}): {exc}")
        return {}
    return _sanitize_server_prefs(data)


def save_server_prefs(data_dir: Path, prefs: dict[str, Any]) -> None:
    """Atomically persist non-secret runtime preferences into data_dir/server_prefs.json."""
    target_dir = Path(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    prefs_file = target_dir / SERVER_PREFS_FILENAME
    clean = _sanitize_server_prefs(prefs)
    payload = json.dumps(clean, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".server_prefs_",
        suffix=".json.tmp",
        dir=str(target_dir),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, prefs_file)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)

MANAGED_VOICE_ID_RE = re.compile(r"^u_[0-9]{1,25}_s[1-5]$")
CLONE_MAX_BYTES = 5 * 1024 * 1024
CLONE_MIN_SEC = 3.0
CLONE_MAX_SEC = 15.0
MAX_TTS_TEXT_CHARS = 1000


def _resolve_env_file(argv: Optional[list[str]] = None) -> str:
    """Resolve target .env filename from BOT_ENV_FILE or CLI flags (--profile / --env)."""
    env_file = os.getenv("BOT_ENV_FILE")
    args = sys.argv[1:] if argv is None else argv
    for arg in args:
        if arg.startswith("--profile="):
            prof = Path(arg.split("=", 1)[1].strip()).name
            if prof:
                env_file = f".env.{prof}"
        elif arg.startswith("--env="):
            candidate = Path(arg.split("=", 1)[1].strip()).name
            if candidate:
                env_file = candidate
    return env_file or ".env"


def _verify_admin_token(x_tts_admin_token: Optional[str]) -> None:
    """驗證可選的共用意圖管理權杖 (TTS_API_TOKEN)，防範未授權本機寫入或卸載。"""
    expected = os.getenv("TTS_API_TOKEN", "").strip()
    if not expected:
        return
    provided = (x_tts_admin_token or "").strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="無效或缺少 TTS 管理權杖 (X-TTS-Admin-Token)")


def _validate_wav_bytes(audio_bytes: bytes) -> float:
    """嚴格驗證上傳音檔為合法 WAV、取樣率正常且時長介於 3～15 秒。"""
    if len(audio_bytes) <= 1024 or len(audio_bytes) > CLONE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="音檔大小超出限制（須介於 1KB～5MB）")
    try:
        info = sf.info(io.BytesIO(audio_bytes))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"無法解析 WAV 音訊格式: {exc}") from exc
    if str(getattr(info, "format", "")).upper() != "WAV":
        raise HTTPException(status_code=400, detail="僅接受標準 PCM WAV 參考音檔")
    channels = int(getattr(info, "channels", 0) or 0)
    if channels not in (1, 2):
        raise HTTPException(status_code=400, detail=f"不支援的聲道數 ({channels})，僅接受單聲道或立體聲 WAV")
    subtype = str(getattr(info, "subtype", "") or "").upper()
    if subtype not in ("PCM_16", "PCM_24", "PCM_32", "FLOAT"):
        raise HTTPException(status_code=400, detail=f"不支援的 WAV 編碼格式 ({subtype or '未知'})")
    sr = int(getattr(info, "samplerate", 0) or 0)
    if sr < 8000 or sr > 48000:
        raise HTTPException(status_code=400, detail=f"不支援的取樣率 ({sr} Hz)")
    duration = float(getattr(info, "duration", 0.0) or 0.0)
    if duration < CLONE_MIN_SEC or duration > CLONE_MAX_SEC:
        raise HTTPException(
            status_code=400,
            detail=f"音檔長度不符規定（{duration:.2f} 秒，需介於 {CLONE_MIN_SEC:.0f}～{CLONE_MAX_SEC:.0f} 秒）",
        )
    return duration


def _strip_control_chars(raw_text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", str(raw_text or "")).strip()


def is_voice_allowed(entry: dict[str, Any], user_id: Optional[str]) -> bool:
    """檢查指定 Discord User ID 是否可使用此聲線（公開聲線全體可用，私人克隆嚴格隔離）。"""
    if bool(entry.get("public", False)):
        return True
    uid = (user_id or "").strip()
    if not uid:
        return False
    allowed = {str(u).strip() for u in (entry.get("allowed_users") or []) if str(u).strip()}
    return uid in allowed


class VoiceCatalog:
    """管理本地 F5-TTS 聲線目錄，支援依檔案修改時間熱重載與隱私權限隔離。"""

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        *,
        server_dir: Optional[Path] = None,
        voices_file: Optional[Path] = None,
    ):
        base_dir = Path(data_dir or server_dir or get_default_data_dir()).resolve()
        self.data_dir = base_dir
        self.server_dir = base_dir
        self.voices_file = Path(voices_file) if voices_file else (self.data_dir / "voices.json")
        self._lock = threading.Lock()
        self._voices: dict[str, dict[str, Any]] = {}
        self._source_path: Optional[Path] = None
        self._last_mtime: Optional[float] = None
        self.reload()

    def _resolve_safe_audio_path(self, raw_path: str) -> Optional[Path]:
        raw = (raw_path or "").strip()
        if not raw:
            return None
        p = Path(raw)
        try:
            candidate = p.resolve() if p.is_absolute() else (self.data_dir / p).resolve()
            if not candidate.is_relative_to(self.data_dir):
                return None
            return candidate
        except (OSError, ValueError):
            return None

    def _pick_source_file(self) -> Optional[Path]:
        if self.voices_file.is_file():
            return self.voices_file
        return None

    def _load_unlocked(self) -> None:
        src = self._pick_source_file()
        self._source_path = src
        if src is None:
            self._last_mtime = None
            self._voices = {}
            return

        try:
            mtime = src.stat().st_mtime
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            print(f"⚠️ 讀取聲線設定檔失敗 ({src.name}): {exc}（保留先前記憶體聲線目錄）")
            return

        if not isinstance(data, dict):
            print(f"⚠️ 聲線設定檔格式錯誤 ({src.name})（保留先前記憶體聲線目錄）")
            return

        self._last_mtime = mtime
        parsed: dict[str, dict[str, Any]] = {}
        raw_voices = data.get("voices") or {}
        if isinstance(raw_voices, dict):
            for vid, entry in raw_voices.items():
                if not isinstance(entry, dict):
                    continue
                safe_path = self._resolve_safe_audio_path(str(entry.get("prompt_audio") or ""))
                if safe_path is None:
                    print(f"⚠️ 略過不安全或空白路徑的聲線: {vid}")
                    continue
                voice_key = str(vid).strip()
                if not voice_key:
                    continue
                try:
                    speed = float(entry.get("speed", 1.0))
                except (TypeError, ValueError):
                    speed = 1.0
                if not math.isfinite(speed):
                    speed = 1.0
                raw_seed = entry.get("seed", 1234)
                try:
                    seed = int(raw_seed) if raw_seed is not None else None
                except (TypeError, ValueError):
                    seed = 1234
                parsed[voice_key] = {
                    "id": voice_key,
                    "name": str(entry.get("name") or voice_key),
                    "description": str(entry.get("description") or ""),
                    "prompt_audio_path": safe_path,
                    "prompt_text": str(entry.get("prompt_text") or "").strip(),
                    "speed": max(0.7, min(1.5, speed)),
                    "seed": seed,
                    "public": bool(entry.get("public", False)),
                    "allowed_users": [
                        str(u).strip() for u in (entry.get("allowed_users") or []) if str(u).strip()
                    ],
                    "owner_user_id": str(entry.get("owner_user_id") or "").strip(),
                    "managed": bool(entry.get("managed", False)),
                }

        self._voices = parsed

    def _load_if_stale_unlocked(self) -> None:
        src = self._pick_source_file()
        try:
            mtime = src.stat().st_mtime if src else None
        except OSError:
            mtime = None
        if src != self._source_path or mtime != self._last_mtime:
            self._load_unlocked()

    def ensure_fresh(self) -> None:
        with self._lock:
            self._load_if_stale_unlocked()

    def _serialize_voices_unlocked(self) -> dict[str, Any]:
        serialized: dict[str, dict[str, Any]] = {}
        for vid, entry in self._voices.items():
            audio_path: Path = entry["prompt_audio_path"]
            try:
                rel_audio = audio_path.resolve().relative_to(self.data_dir).as_posix()
            except ValueError:
                continue
            item: dict[str, Any] = {
                "name": entry["name"],
                "description": entry.get("description", ""),
                "prompt_audio": rel_audio,
                "prompt_text": entry.get("prompt_text", ""),
                "speed": entry.get("speed", 1.0),
                "seed": entry.get("seed", 1234),
                "public": bool(entry.get("public", False)),
                "allowed_users": list(entry.get("allowed_users") or []),
            }
            if entry.get("owner_user_id"):
                item["owner_user_id"] = str(entry["owner_user_id"])
            if entry.get("managed"):
                item["managed"] = True
            serialized[vid] = item
        return {"voices": serialized}

    def _write_voices_atomic_unlocked(self) -> None:
        self.voices_file.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._serialize_voices_unlocked(), ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".voices_",
            suffix=".json.tmp",
            dir=str(self.voices_file.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_path, self.voices_file)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
        self._source_path = self.voices_file
        try:
            self._last_mtime = self.voices_file.stat().st_mtime
        except OSError:
            self._last_mtime = None

    def register_user_clone(
        self,
        *,
        owner_user_id: str,
        slot: int,
        name: str,
        prompt_text: str,
        audio_bytes: bytes,
        description: str = "",
        public: bool = False,
        max_per_user: int = 2,
        max_total: int = 40,
    ) -> dict[str, Any]:
        uid = _strip_control_chars(owner_user_id)
        if not uid.isdigit() or len(uid) > 25:
            raise HTTPException(status_code=400, detail="無效的使用者 ID")

        per_user_limit = max(1, min(5, int(max_per_user)))
        total_limit = max(1, min(200, int(max_total)))
        slot_num = int(slot)
        if slot_num < 1 or slot_num > per_user_limit:
            raise HTTPException(status_code=400, detail=f"槽位編號必須介於 1～{per_user_limit}")

        voice_id = f"u_{uid}_s{slot_num}"
        if not MANAGED_VOICE_ID_RE.match(voice_id):
            raise HTTPException(status_code=400, detail="非法的聲線識別碼格式")

        clean_name = _strip_control_chars(name)[:32].strip()
        if not clean_name:
            raise HTTPException(status_code=400, detail="聲線名稱不可為空")

        clean_prompt = _strip_control_chars(prompt_text)
        if len(clean_prompt) < 5 or len(clean_prompt) > 120:
            raise HTTPException(status_code=400, detail="參考音檔逐字稿長度須介於 5～120 字元")

        clean_desc = _strip_control_chars(description)[:100].strip() or (
            "社群成員公開分享克隆聲線" if public else "已核准之個人專屬克隆聲線"
        )
        duration_sec = _validate_wav_bytes(audio_bytes)

        clones_dir = (self.data_dir / "user_clones").resolve()
        clones_dir.mkdir(parents=True, exist_ok=True)
        target_wav = (clones_dir / f"{voice_id}.wav").resolve()
        if not target_wav.is_relative_to(clones_dir):
            raise HTTPException(status_code=400, detail="音檔儲存路徑異常")

        with self._lock:
            self._load_if_stale_unlocked()
            existing = self._voices.get(voice_id)
            user_clones = [
                v for v in self._voices.values() if v.get("managed") and str(v.get("owner_user_id") or "") == uid
            ]
            all_managed = [v for v in self._voices.values() if v.get("managed")]

            if existing is None:
                if len(user_clones) >= per_user_limit:
                    raise HTTPException(
                        status_code=409,
                        detail=f"個人克隆聲線已達上限（{per_user_limit} 組）",
                    )
                if len(all_managed) >= total_limit:
                    raise HTTPException(
                        status_code=409,
                        detail=f"伺服器成員克隆總數已達上限（{total_limit} 組）",
                    )

            backup_wav: Optional[Path] = None
            if target_wav.is_file():
                fd_bak, bak_name = tempfile.mkstemp(
                    prefix=f".{voice_id}_bak_",
                    suffix=".wav.bak",
                    dir=str(clones_dir),
                )
                os.close(fd_bak)
                backup_wav = Path(bak_name)
                try:
                    shutil.copyfile(target_wav, backup_wav)
                except Exception as exc:
                    backup_wav.unlink(missing_ok=True)
                    print(f"❌ 備份既有聲線音檔失敗 ({voice_id}): {exc}")
                    raise HTTPException(status_code=500, detail="備份聲線音檔失敗") from exc

            fd, tmp_wav_name = tempfile.mkstemp(prefix=f".{voice_id}_", suffix=".wav.tmp", dir=str(clones_dir))
            tmp_wav = Path(tmp_wav_name)
            prev_snapshot = dict(self._voices)
            try:
                try:
                    with os.fdopen(fd, "wb") as fh:
                        fh.write(audio_bytes)
                    os.replace(tmp_wav, target_wav)
                finally:
                    if tmp_wav.exists():
                        tmp_wav.unlink(missing_ok=True)

                self._voices[voice_id] = {
                    "id": voice_id,
                    "name": clean_name,
                    "description": clean_desc,
                    "prompt_audio_path": target_wav,
                    "prompt_text": clean_prompt,
                    "speed": 1.0,
                    "seed": (int(uid) % 100000) + slot_num,
                    "public": bool(public),
                    "allowed_users": [] if public else [uid],
                    "owner_user_id": uid,
                    "managed": True,
                }
                self._write_voices_atomic_unlocked()
            except Exception as exc:
                self._voices = prev_snapshot
                if backup_wav is not None and backup_wav.is_file():
                    try:
                        os.replace(backup_wav, target_wav)
                    except OSError as restore_exc:
                        print(f"⚠️ 還原聲線音檔備份失敗 ({voice_id}): {restore_exc}")
                else:
                    try:
                        target_wav.unlink(missing_ok=True)
                    except OSError:
                        pass
                print(f"❌ 寫入聲線設定檔失敗 ({voice_id}): {exc}")
                raise HTTPException(status_code=500, detail="寫入聲線設定檔失敗") from exc
            finally:
                if backup_wav is not None and backup_wav.exists():
                    backup_wav.unlink(missing_ok=True)

            user_cnt = sum(
                1 for v in self._voices.values() if v.get("managed") and str(v.get("owner_user_id") or "") == uid
            )
            total_cnt = sum(1 for v in self._voices.values() if v.get("managed"))

        print(f"🎙️ [Register] 已寫入聲線 {voice_id} (public={bool(public)}, duration={duration_sec:.2f}s)")
        return {
            "status": "registered",
            "voice_id": voice_id,
            "name": clean_name,
            "public": bool(public),
            "duration_sec": round(duration_sec, 2),
            "user_clone_count": user_cnt,
            "total_user_clones": total_cnt,
        }

    def delete_user_clone(self, voice_id: str, requester_user_id: Optional[str] = None) -> dict[str, Any]:
        key = (voice_id or "").strip()
        if not MANAGED_VOICE_ID_RE.match(key):
            raise HTTPException(status_code=403, detail="僅可透過 API 刪除成員申請建立的託管克隆聲線")

        req_uid = _strip_control_chars(requester_user_id) if requester_user_id is not None else None
        with self._lock:
            self._load_if_stale_unlocked()
            entry = self._voices.get(key)
            if entry is None:
                raise HTTPException(status_code=404, detail=f"找不到聲線: {key}")
            if not entry.get("managed"):
                raise HTTPException(status_code=403, detail="主機手動設定之聲線受保護，無法經 API 刪除")
            if req_uid and str(entry.get("owner_user_id") or "") != req_uid:
                raise HTTPException(status_code=403, detail="無權限刪除其他成員的克隆聲線")

            prev_snapshot = dict(self._voices)
            audio_path: Path = entry["prompt_audio_path"]
            del self._voices[key]
            try:
                self._write_voices_atomic_unlocked()
            except Exception as exc:
                self._voices = prev_snapshot
                print(f"❌ 更新聲線設定檔失敗 ({key}): {exc}")
                raise HTTPException(status_code=500, detail="更新聲線設定檔失敗") from exc

            clones_dir = (self.data_dir / "user_clones").resolve()
            try:
                resolved_wav = audio_path.resolve()
                if resolved_wav.is_relative_to(clones_dir) and resolved_wav.is_file():
                    resolved_wav.unlink(missing_ok=True)
            except OSError:
                pass

        print(f"🗑️ [Delete] 已移除成員克隆聲線 {key}")
        return {"status": "deleted", "voice_id": key}

    def set_user_clone_visibility(self, voice_id: str, public: bool) -> dict[str, Any]:
        key = (voice_id or "").strip()
        if not MANAGED_VOICE_ID_RE.match(key):
            raise HTTPException(status_code=403, detail="僅可透過 API 調整成員申請建立的託管克隆聲線權限")

        with self._lock:
            self._load_if_stale_unlocked()
            entry = self._voices.get(key)
            if entry is None:
                raise HTTPException(status_code=404, detail=f"找不到聲線: {key}")
            if not entry.get("managed"):
                raise HTTPException(status_code=403, detail="主機手動設定之聲線受保護，無法經 API 修改權限")

            prev_public = bool(entry.get("public", False))
            prev_allowed = list(entry.get("allowed_users") or [])
            prev_desc = str(entry.get("description") or "")
            uid = str(entry.get("owner_user_id") or "").strip()

            entry["public"] = bool(public)
            entry["allowed_users"] = [] if public else ([uid] if uid else [])
            if prev_desc in ("已核准之個人專屬克隆聲線", "成員自助申請之私人克隆聲線", "社群成員公開分享克隆聲線"):
                entry["description"] = "社群成員公開分享克隆聲線" if public else "已核准之個人專屬克隆聲線"
            try:
                self._write_voices_atomic_unlocked()
            except Exception as exc:
                entry["public"] = prev_public
                entry["allowed_users"] = prev_allowed
                entry["description"] = prev_desc
                print(f"❌ 更新聲線權限失敗 ({key}): {exc}")
                raise HTTPException(status_code=500, detail="更新聲線權限失敗") from exc

        print(f"🔄 [Visibility] 聲線 {key} 已切換為 public={bool(public)}")
        return {"status": "updated", "voice_id": key, "public": bool(public)}

    def reload(self) -> tuple[int, int]:
        with self._lock:
            self._load_unlocked()
            public_cnt = sum(1 for v in self._voices.values() if v.get("public"))
            return public_cnt, len(self._voices)

    def counts(self) -> tuple[int, int]:
        self.ensure_fresh()
        with self._lock:
            public_cnt = sum(1 for v in self._voices.values() if v.get("public"))
            return public_cnt, len(self._voices)

    def list_voices(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        self.ensure_fresh()
        with self._lock:
            result: list[dict[str, Any]] = []
            for entry in self._voices.values():
                if not is_voice_allowed(entry, user_id):
                    continue
                result.append(
                    {
                        "id": entry["id"],
                        "name": entry["name"],
                        "description": entry["description"],
                        "public": entry["public"],
                        "speed": entry["speed"],
                        "owner_user_id": entry.get("owner_user_id", ""),
                        "managed": bool(entry.get("managed", False)),
                    }
                )
            return result

    def get_voice(self, voice_id: str) -> Optional[dict[str, Any]]:
        self.ensure_fresh()
        key = (voice_id or "").strip()
        with self._lock:
            entry = self._voices.get(key)
            return dict(entry) if entry else None


class F5ModelManager:
    """單引擎 F5-TTS 按需載入、啟動預載與可調式顯存駐留管理器。"""

    def __init__(
        self,
        idle_unload_seconds: int = F5_IDLE_UNLOAD_SECONDS,
        default_nfe_step: int = F5_NFE_STEP,
        preload_on_startup: bool = F5_PRELOAD_ON_STARTUP,
        offline_first: bool = F5_OFFLINE_FIRST,
        prefs_dir: Optional[Path] = None,
    ):
        self.idle_unload_seconds = max(0, min(86400, int(idle_unload_seconds)))
        self.default_nfe_step = max(8, min(64, int(default_nfe_step)))
        self.preload_on_startup = bool(preload_on_startup)
        self.offline_first = bool(offline_first)
        self.prefs_dir: Optional[Path] = Path(prefs_dir).resolve() if prefs_dir is not None else None
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.gpu_name = torch.cuda.get_device_name(0) if self.device == "cuda" else None
        self.total_vram_gb: float = 0.0
        if self.device == "cuda":
            try:
                self.total_vram_gb = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 2)
            except Exception:
                self.total_vram_gb = 0.0
        self._lock = threading.Lock()
        self._engine: Any = None
        self._loading: bool = False
        self._loaded_from_local_cache: bool = False
        self._last_load_seconds: Optional[float] = None
        self._last_used: float = 0.0
        self._loaded_at: float = 0.0
        self._last_vram_gb: float = 0.0
        self._stop_event = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None

    def start_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._stop_event.clear()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, name="f5-idle-watchdog", daemon=True)
        self._watchdog_thread.start()

    def stop_watchdog(self) -> None:
        self._stop_event.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=2.0)

    def _watchdog_loop(self) -> None:
        while not self._stop_event.wait(30.0):
            if self.idle_unload_seconds <= 0:
                continue
            if self._engine is None:
                continue
            if not self._lock.acquire(timeout=1.0):
                continue
            try:
                if self._engine is not None and self._last_used > 0:
                    idle_for = time.monotonic() - self._last_used
                    if idle_for >= self.idle_unload_seconds:
                        print(f"💤 F5-TTS 已閒置 {idle_for:.0f} 秒，正在自動卸載並釋放 VRAM...")
                        self._unload_unlocked()
            finally:
                self._lock.release()

    def _ensure_loaded_unlocked(self) -> None:
        if self._engine is not None:
            return
        self._loading = True
        t0 = time.monotonic()
        try:
            from f5_tts.api import F5TTS

            ckpt_file: Optional[str] = None
            vocoder_dir: Optional[str] = None
            if self.offline_first:
                ckpt_file, vocoder_dir = _resolve_local_hf_cache()

            if ckpt_file and vocoder_dir:
                print(f"📦 正在從本機快取直讀載入 F5-TTS 至 {self.device}（免連線 Hugging Face）...")
                self._engine = F5TTS(
                    device=self.device,
                    ckpt_file=ckpt_file,
                    vocoder_local_path=vocoder_dir,
                )
                self._loaded_from_local_cache = True
            else:
                if self.offline_first:
                    print(
                        f"📦 尚無完整本機快取，正在首次連線下載／載入 F5-TTS 至 {self.device}（之後將自動本機快取直讀）..."
                    )
                else:
                    print(f"📦 正在載入 F5-TTS 至 {self.device}...")
                self._engine = F5TTS(device=self.device)
                self._loaded_from_local_cache = False

            now = time.monotonic()
            load_secs = round(max(0.0, now - t0), 2)
            self._loaded_at = now
            self._last_used = now
            self._last_load_seconds = load_secs
            vram = round(torch.cuda.memory_allocated() / 1024**3, 2) if self.device == "cuda" else 0.0
            self._last_vram_gb = vram
            print(f"✅ F5-TTS 載入完成（耗時 {load_secs:.2f}s | VRAM 佔用: {vram} GB）")
        finally:
            self._loading = False

    def _unload_unlocked(self) -> bool:
        was_loaded = self._engine is not None
        self._engine = None
        self._last_used = 0.0
        self._loaded_at = 0.0
        self._last_vram_gb = 0.0
        self._loaded_from_local_cache = False
        self._last_load_seconds = None
        gc.collect()
        if torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
        return was_loaded

    def preload_now(self) -> dict[str, Any]:
        with self._lock:
            already_loaded = self._engine is not None
            if already_loaded:
                self._last_used = time.monotonic()
            else:
                self._ensure_loaded_unlocked()
            st = self.status()
            return {
                "status": "loaded",
                "already_loaded": already_loaded,
                "model_loaded": True,
                "loaded_from_local_cache": self._loaded_from_local_cache,
                "load_seconds": self._last_load_seconds,
                "vram_allocated_gb": st["vram_allocated_gb"],
                "total_vram_gb": self.total_vram_gb,
                "idle_unload_seconds": self.idle_unload_seconds,
                "idle_remaining_seconds": st["idle_remaining_seconds"],
                "preload_on_startup": self.preload_on_startup,
                "offline_first": self.offline_first,
            }

    def update_runtime_config(
        self,
        *,
        idle_unload_seconds: Optional[int] = None,
        preload_on_startup: Optional[bool] = None,
        offline_first: Optional[bool] = None,
    ) -> dict[str, Any]:
        with self._lock:
            if idle_unload_seconds is not None:
                if isinstance(idle_unload_seconds, bool):
                    raise ValueError("idle_unload_seconds 必須為整數")
                self.idle_unload_seconds = max(0, min(86400, int(idle_unload_seconds)))
                if self._engine is not None:
                    self._last_used = time.monotonic()
            if preload_on_startup is not None:
                self.preload_on_startup = bool(preload_on_startup)
            if offline_first is not None:
                self.offline_first = bool(offline_first)
            if self.prefs_dir is not None:
                save_server_prefs(
                    self.prefs_dir,
                    {
                        "idle_unload_seconds": self.idle_unload_seconds,
                        "preload_on_startup": self.preload_on_startup,
                        "offline_first": self.offline_first,
                    },
                )
            return self.status()

    def unload_now(self) -> dict[str, Any]:
        with self._lock:
            was_loaded = self._unload_unlocked()
            vram = round(torch.cuda.memory_allocated() / 1024**3, 2) if self.device == "cuda" else 0.0
            self._last_vram_gb = vram
            print(f"🧹 手動卸載 F5-TTS 完成 (was_loaded={was_loaded} | VRAM={vram} GB)")
            return {
                "status": "unloaded",
                "was_loaded": was_loaded,
                "model_loaded": False,
                "vram_allocated_gb": vram,
                "total_vram_gb": self.total_vram_gb,
            }

    def status(self) -> dict[str, Any]:
        """Return lock-free health status so / never blocks while GPU inference is running."""
        loaded = self._engine is not None
        last_used = self._last_used
        idle_remaining: Optional[int] = None
        if loaded and self.idle_unload_seconds > 0 and last_used > 0:
            rem = max(0.0, self.idle_unload_seconds - (time.monotonic() - last_used))
            idle_remaining = int(round(rem))
        vram = self._last_vram_gb if loaded else 0.0
        return {
            "status": "online",
            "engine": "f5tts",
            "model_loaded": loaded,
            "model_loading": self._loading,
            "device": self.device,
            "gpu_name": self.gpu_name,
            "vram_allocated_gb": vram,
            "total_vram_gb": self.total_vram_gb,
            "idle_unload_seconds": self.idle_unload_seconds,
            "idle_remaining_seconds": idle_remaining,
            "preload_on_startup": self.preload_on_startup,
            "offline_first": self.offline_first,
            "loaded_from_local_cache": self._loaded_from_local_cache,
            "last_load_seconds": self._last_load_seconds,
            "nfe_step": self.default_nfe_step,
        }

    def infer_wav_bytes(
        self,
        ref_path: Path,
        ref_text: str,
        gen_text: str,
        speed: float = 1.0,
        seed: Optional[int] = 1234,
        nfe_step: Optional[int] = None,
    ) -> bytes:
        effective_speed = max(0.7, min(1.5, float(speed)))
        steps = max(8, min(64, int(nfe_step if nfe_step is not None else self.default_nfe_step)))
        try:
            with self._lock:
                self._ensure_loaded_unlocked()
                wav, sr, _ = self._engine.infer(
                    ref_file=str(ref_path),
                    ref_text=ref_text,
                    gen_text=gen_text,
                    show_info=lambda *a, **k: None,
                    progress=None,
                    nfe_step=steps,
                    speed=effective_speed,
                    seed=seed,
                    file_wave=None,
                )
                self._last_used = time.monotonic()
                if self.device == "cuda":
                    self._last_vram_gb = round(torch.cuda.memory_allocated() / 1024**3, 2)
        except torch.cuda.OutOfMemoryError as exc:
            print(f"💥 [F5-TTS] CUDA OutOfMemoryError，正在清理 GPU 快取: {exc}")
            gc.collect()
            if torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
            if self.device == "cuda":
                try:
                    self._last_vram_gb = round(torch.cuda.memory_allocated() / 1024**3, 2)
                except Exception:
                    self._last_vram_gb = 0.0
            raise HTTPException(status_code=503, detail="GPU 記憶體不足，請稍後再試或縮短文字") from exc

        buf = io.BytesIO()
        sf.write(buf, wav, sr, format="WAV")
        return buf.getvalue()


catalog = VoiceCatalog(data_dir=get_default_data_dir())
manager = F5ModelManager(
    idle_unload_seconds=F5_IDLE_UNLOAD_SECONDS,
    default_nfe_step=F5_NFE_STEP,
    preload_on_startup=F5_PRELOAD_ON_STARTUP,
    offline_first=F5_OFFLINE_FIRST,
)
_infer_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_infer_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = id(loop)
    sem = _infer_semaphores.get(key)
    if sem is None:
        if len(_infer_semaphores) > 16:
            _infer_semaphores.clear()
        sem = asyncio.Semaphore(2)
        _infer_semaphores[key] = sem
    return sem


@asynccontextmanager
async def lifespan(_app: FastAPI):
    pub_cnt, tot_cnt = await asyncio.to_thread(catalog.counts)
    startup_mode = "🔥 啟動自動預載" if manager.preload_on_startup else "💤 按需載入"
    residency_mode = (
        "常駐不卸載"
        if manager.idle_unload_seconds <= 0
        else f"閒置 {manager.idle_unload_seconds}s 自動釋放"
    )
    cache_mode = "開啟" if manager.offline_first else "關閉"
    gpu_desc = f"{manager.gpu_name} | 總顯存 {manager.total_vram_gb:.2f} GB" if manager.device == "cuda" else "CPU"

    print("==================================================")
    print(f"🎙️ 單引擎 F5-TTS 本地語音服務已就緒（{startup_mode}）")
    print(f"🖥️ 推論裝置: {manager.device} ({gpu_desc})")
    print(f"⚙️ 顯存策略: {residency_mode} | 離線快取直讀: {cache_mode} | nfe_step: {manager.default_nfe_step}")
    print(f"📂 聲線資料目錄: {catalog.data_dir}")
    print(f"🎵 已載入聲線數: 公開 {pub_cnt} 組 / 總計 {tot_cnt} 組")
    print("==================================================")
    manager.start_watchdog()
    preload_task: Optional[asyncio.Task[Any]] = None
    if manager.preload_on_startup:

        async def _run_startup_preload() -> None:
            try:
                await asyncio.to_thread(manager.preload_now)
            except Exception as exc:
                print(f"⚠️ 啟動自動預載 F5-TTS 失敗（將保留按需載入）: {exc}")

        preload_task = asyncio.create_task(_run_startup_preload())
    try:
        yield
    finally:
        if preload_task is not None and not preload_task.done():
            preload_task.cancel()
            try:
                await preload_task
            except (asyncio.CancelledError, Exception):
                pass
        manager.stop_watchdog()
        await asyncio.to_thread(manager.unload_now)


app = FastAPI(title="F5-TTS Lazy-Load Voice Server", lifespan=lifespan)


@app.get("/")
async def health_check() -> dict[str, Any]:
    info = manager.status()
    pub_cnt, tot_cnt = await asyncio.to_thread(catalog.counts)
    info["public_voice_count"] = pub_cnt
    info["total_voice_count"] = tot_cnt
    return info


@app.get("/voices")
async def list_voices(
    user_id: Optional[str] = Query(default=None),
    x_tts_admin_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    voices = await asyncio.to_thread(catalog.list_voices, user_id=user_id)
    return {"voices": voices}


@app.post("/tts_by_voice")
async def tts_by_voice(
    tts_text: str = Form(...),
    voice_id: str = Form(...),
    user_id: Optional[str] = Form(default=None),
    speed: Optional[float] = Form(default=None),
    nfe_step: Optional[int] = Form(default=None),
    x_tts_admin_token: Optional[str] = Header(default=None),
) -> Response:
    _verify_admin_token(x_tts_admin_token)
    clean_text = tts_text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="tts_text 不能為空")
    if len(clean_text) > MAX_TTS_TEXT_CHARS:
        raise HTTPException(status_code=400, detail=f"tts_text 長度不可超過 {MAX_TTS_TEXT_CHARS} 字元")

    raw_speed = speed
    if raw_speed is not None:
        try:
            parsed_speed = float(raw_speed)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="無效的語速參數") from exc
        if not math.isfinite(parsed_speed):
            raise HTTPException(status_code=400, detail="語速參數必須為有限數值")
    else:
        parsed_speed = None

    entry = await asyncio.to_thread(catalog.get_voice, voice_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"找不到指定聲線: {voice_id}")

    if not is_voice_allowed(entry, user_id):
        raise HTTPException(status_code=403, detail="無權限使用此私人聲線")

    ref_path: Path = entry["prompt_audio_path"]
    if not await asyncio.to_thread(ref_path.is_file):
        raise HTTPException(status_code=404, detail=f"找不到聲線參考音檔: {entry['id']}")

    use_speed = float(entry["speed"]) if parsed_speed is None else parsed_speed
    if not math.isfinite(use_speed):
        raise HTTPException(status_code=400, detail="語速參數必須為有限數值")

    sem = _get_infer_semaphore()
    try:
        await asyncio.wait_for(sem.acquire(), timeout=30.0)
    except asyncio.TimeoutError as exc:
        raise HTTPException(status_code=503, detail="語音合成服務目前繁忙，請稍後再試") from exc

    try:
        t0 = time.monotonic()
        print(f"🎙️ [F5-TTS] 聲線={entry['id']} | 文字='{clean_text[:40]}'")
        wav_bytes = await asyncio.to_thread(
            manager.infer_wav_bytes,
            ref_path=ref_path,
            ref_text=entry["prompt_text"],
            gen_text=clean_text,
            speed=use_speed,
            seed=entry.get("seed", 1234),
            nfe_step=nfe_step,
        )
        print(f"✅ [F5-TTS] 完成 聲線={entry['id']} ({len(wav_bytes)}B, {time.monotonic() - t0:.2f}s)")
        return Response(content=wav_bytes, media_type="audio/wav")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"❌ F5-TTS 推論失敗: {exc!r}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="語音合成推論失敗，請稍後再試") from exc
    finally:
        sem.release()


@app.post("/unload")
async def unload_model(x_tts_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    return await asyncio.to_thread(manager.unload_now)


@app.post("/preload")
async def preload_model(x_tts_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    try:
        return await asyncio.to_thread(manager.preload_now)
    except HTTPException:
        raise
    except Exception as exc:
        print(f"❌ F5-TTS 預載失敗: {exc!r}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="F5-TTS 模型預載失敗，請查看主機日誌") from exc


@app.post("/config")
async def update_config(
    idle_unload_seconds: Optional[int] = Form(default=None),
    preload_on_startup: Optional[bool] = Form(default=None),
    offline_first: Optional[bool] = Form(default=None),
    x_tts_admin_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    return await asyncio.to_thread(
        manager.update_runtime_config,
        idle_unload_seconds=idle_unload_seconds,
        preload_on_startup=preload_on_startup,
        offline_first=offline_first,
    )


@app.post("/reload_voices")
async def reload_voices(x_tts_admin_token: Optional[str] = Header(default=None)) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    pub_cnt, tot_cnt = await asyncio.to_thread(catalog.reload)
    return {
        "status": "reloaded",
        "public_voice_count": pub_cnt,
        "total_voice_count": tot_cnt,
    }


@app.post("/register_voice")
async def register_voice(
    audio: UploadFile = File(...),
    owner_user_id: str = Form(...),
    slot: int = Form(default=1),
    name: str = Form(...),
    prompt_text: str = Form(...),
    description: str = Form(default=""),
    public: bool = Form(default=False),
    max_per_user: int = Form(default=2),
    max_total: int = Form(default=40),
    x_tts_admin_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    try:
        audio_bytes = await audio.read(CLONE_MAX_BYTES + 1)
    finally:
        await audio.close()
    if len(audio_bytes) > CLONE_MAX_BYTES:
        raise HTTPException(status_code=400, detail="音檔超過 5MB 大小上限")
    server_max_per_user = _env_int("TTS_CLONE_MAX_PER_USER", 5, min_val=1, max_val=5)
    server_max_total = _env_int("TTS_CLONE_MAX_TOTAL", 200, min_val=1, max_val=200)
    eff_max_per_user = min(max(1, min(5, int(max_per_user))), server_max_per_user)
    eff_max_total = min(max(1, min(200, int(max_total))), server_max_total)
    return await asyncio.to_thread(
        catalog.register_user_clone,
        owner_user_id=owner_user_id,
        slot=slot,
        name=name,
        prompt_text=prompt_text,
        audio_bytes=audio_bytes,
        description=description,
        public=public,
        max_per_user=eff_max_per_user,
        max_total=eff_max_total,
    )


@app.delete("/voices/{voice_id}")
async def delete_voice(
    voice_id: str,
    requester_user_id: Optional[str] = Query(default=None),
    x_tts_admin_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    return await asyncio.to_thread(
        catalog.delete_user_clone,
        voice_id=voice_id,
        requester_user_id=requester_user_id,
    )


@app.post("/voices/{voice_id}/visibility")
async def update_voice_visibility(
    voice_id: str,
    public: bool = Form(...),
    x_tts_admin_token: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    _verify_admin_token(x_tts_admin_token)
    return await asyncio.to_thread(
        catalog.set_user_clone_visibility,
        voice_id=voice_id,
        public=public,
    )


def main() -> None:
    global catalog, F5_TTS_HOST, F5_TTS_PORT, F5_IDLE_UNLOAD_SECONDS, F5_NFE_STEP, F5_OFFLINE_FIRST, F5_PRELOAD_ON_STARTUP
    try:
        from dotenv import load_dotenv

        env_file = _resolve_env_file()
        env_path = PROJECT_ROOT / env_file
        if env_path.is_file():
            load_dotenv(env_path, override=False)
    except ImportError:
        pass

    host = os.getenv("F5_TTS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = _env_int("F5_TTS_PORT", 9880, min_val=1, max_val=65535)
    idle_unload = _env_int("F5_IDLE_UNLOAD_SECONDS", 1800, min_val=0, max_val=86400)
    nfe_step = _env_int("F5_NFE_STEP", 16, min_val=8, max_val=64)
    offline_first = _env_bool("F5_OFFLINE_FIRST", True)
    preload_on_startup = _env_bool("F5_PRELOAD_ON_STARTUP", False)
    F5_TTS_HOST = host
    F5_TTS_PORT = port
    F5_IDLE_UNLOAD_SECONDS = idle_unload
    F5_NFE_STEP = nfe_step
    F5_OFFLINE_FIRST = offline_first
    F5_PRELOAD_ON_STARTUP = preload_on_startup
    manager.idle_unload_seconds = max(0, idle_unload)
    manager.default_nfe_step = max(8, min(64, nfe_step))
    manager.offline_first = offline_first
    manager.preload_on_startup = preload_on_startup

    token = os.getenv("TTS_API_TOKEN", "").strip()
    if host.lower() not in ("127.0.0.1", "::1", "localhost"):
        if len(token) < 16:
            print(
                f"❌ [安全阻斷] 拒絕將 F5-TTS 綁定於非本機位址 ({host})："
                "必須設定長度至少 16 字元的 TTS_API_TOKEN。"
            )
            sys.exit(1)
    elif not token:
        print(
            "⚠️ [安全警告] 目前未設定 TTS_API_TOKEN，F5-TTS 將於本機 loopback 介面無權杖啟動；"
            "建議設定 TTS_API_TOKEN 以防範本機未授權呼叫。"
        )

    data_dir = get_default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    manager.prefs_dir = data_dir
    saved_prefs = load_server_prefs(data_dir)
    if "idle_unload_seconds" in saved_prefs:
        manager.idle_unload_seconds = max(0, min(86400, int(saved_prefs["idle_unload_seconds"])))
    if "preload_on_startup" in saved_prefs:
        manager.preload_on_startup = bool(saved_prefs["preload_on_startup"])
    if "offline_first" in saved_prefs:
        manager.offline_first = bool(saved_prefs["offline_first"])
    if data_dir != catalog.data_dir:
        catalog = VoiceCatalog(data_dir=data_dir)
    else:
        catalog.reload()

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
