"""Tests for F5-TTS fast-boot local HF cache loading, startup preload, GPU residency, and UI controls."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cogs.setup_cog import SetupCog, SetupPanelView, _check_f5_tts
from cogs.tts_cog import TtsCog
from core.config import BotSettings
from engines import assistant_db, tts_engine


def _make_settings(**overrides) -> BotSettings:
    defaults = dict(
        env_file=".env.test_missing",
        bot_instance_name="FastBootTestBot",
        discord_bot_token="tok",
        gemini_api_key="secret_gemini_key",
        target_channel_id=1001,
        tts_channel_id=1002,
        general_channel_id=1003,
        enable_voice_tts=True,
        tts_api_url="http://127.0.0.1:9880",
        tts_api_token="admin_secret_tok",
        bot_owner_ids=(9000,),
    )
    defaults.update(overrides)
    return BotSettings(**defaults)


def _assert_discord_view_limits(testcase: unittest.TestCase, view: discord.ui.View, label: str) -> None:
    testcase.assertLessEqual(len(view.children), 25, f"{label}: exceeds 25 components")
    rows: dict[int, list[discord.ui.Item]] = {}
    for child in view.children:
        testcase.assertIsNotNone(child.row, f"{label}: missing row")
        testcase.assertIn(child.row, (0, 1, 2, 3, 4), f"{label}: invalid row {child.row}")
        rows.setdefault(child.row, []).append(child)
    for row_idx, items in rows.items():
        testcase.assertLessEqual(len(items), 5, f"{label} row {row_idx}: >5 items")
        total_width = sum(getattr(it, "width", 1) for it in items)
        testcase.assertLessEqual(total_width, 5, f"{label} row {row_idx}: width > 5")
    testcase.assertLessEqual(len(view.to_components()), 5, f"{label}: action rows > 5")


class TestF5ServerFastBootAndResidency(unittest.TestCase):
    """Run services.tts_server tests in-process (.venv_tts) or via .venv_tts subprocess (.venv)."""

    def _run_server_script(self, script: str) -> None:
        has_native_deps = all(
            importlib.util.find_spec(pkg) is not None for pkg in ("fastapi", "torch", "soundfile", "uvicorn")
        )
        if has_native_deps:
            exec(compile(script, "<f5_fast_boot_test>", "exec"), {})
            return

        tts_venv_py = ROOT / ".venv_tts" / "Scripts" / "python.exe"
        if not tts_venv_py.is_file():
            self.skipTest(".venv_tts environment not present")

        res = subprocess.run(
            [str(tts_venv_py), "-c", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
        )
        self.assertEqual(res.returncode, 0, msg=f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}")

    def test_resolve_local_hf_cache_and_model_manager_fast_boot(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import sys, types
from pathlib import Path
sys.path.insert(0, r"{str(ROOT)}")
from services import tts_server

tmp = Path(r"{tmp}")
ckpt = tmp / "hub" / "f5" / "F5TTS_v1_Base" / "model_1250000.safetensors"
ckpt.parent.mkdir(parents=True, exist_ok=True)
ckpt.write_bytes(b"fake_ckpt")

vocos_dir = tmp / "hub" / "vocos"
vocos_dir.mkdir(parents=True, exist_ok=True)
v_cfg = vocos_dir / "config.yaml"
v_pt = vocos_dir / "pytorch_model.bin"
v_cfg.write_text("dummy: true\\n", encoding="utf-8")
v_pt.write_bytes(b"fake_vocos")

orig_try = tts_server.try_to_load_from_cache
try:
    # 1. Full local cache hit
    def fake_hit(repo_id, filename, **kwargs):
        if repo_id == "SWivid/F5-TTS":
            return str(ckpt)
        if filename == "config.yaml":
            return str(v_cfg)
        if filename == "pytorch_model.bin":
            return str(v_pt)
        return None

    tts_server.try_to_load_from_cache = fake_hit
    got_ckpt, got_vocos = tts_server._resolve_local_hf_cache()
    assert got_ckpt == str(ckpt), got_ckpt
    assert got_vocos == str(vocos_dir), got_vocos

    # 2. Missing or _CACHED_NO_EXIST returns (None, None)
    tts_server.try_to_load_from_cache = lambda repo_id, filename, **kw: (
        tts_server._CACHED_NO_EXIST if filename == "pytorch_model.bin" else str(ckpt)
    )
    assert tts_server._resolve_local_hf_cache() == (None, None)

    tts_server.try_to_load_from_cache = lambda *a, **kw: None
    assert tts_server._resolve_local_hf_cache() == (None, None)

    # 3. Mismatched vocoder parent dirs returns (None, None)
    other_dir = tmp / "hub" / "vocos_other"
    other_dir.mkdir(parents=True, exist_ok=True)
    v_pt_other = other_dir / "pytorch_model.bin"
    v_pt_other.write_bytes(b"fake")
    tts_server.try_to_load_from_cache = lambda repo_id, filename, **kw: (
        str(ckpt) if repo_id == "SWivid/F5-TTS" else (str(v_cfg) if filename == "config.yaml" else str(v_pt_other))
    )
    assert tts_server._resolve_local_hf_cache() == (None, None)

    # 4. F5ModelManager passes ckpt_file & vocoder_local_path when cached, falls back when uncached
    calls = []
    class FakeF5TTS:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    fake_f5_api = types.ModuleType("f5_tts.api")
    fake_f5_api.F5TTS = FakeF5TTS
    fake_f5_pkg = types.ModuleType("f5_tts")
    fake_f5_pkg.api = fake_f5_api
    orig_f5_pkg = sys.modules.get("f5_tts")
    orig_f5_api = sys.modules.get("f5_tts.api")
    sys.modules["f5_tts"] = fake_f5_pkg
    sys.modules["f5_tts.api"] = fake_f5_api
    try:
        tts_server.try_to_load_from_cache = fake_hit
        mgr = tts_server.F5ModelManager(idle_unload_seconds=0, preload_on_startup=True, offline_first=True, prefs_dir=tmp)
        res1 = mgr.preload_now()
        assert res1["loaded_from_local_cache"] is True
        assert res1["already_loaded"] is False
        assert res1["model_loaded"] is True
        assert calls[-1]["ckpt_file"] == str(ckpt)
        assert calls[-1]["vocoder_local_path"] == str(vocos_dir)
        assert calls[-1]["device"] == mgr.device

        # Subsequent preload_now reports already_loaded=True and skips re-init
        res1b = mgr.preload_now()
        assert res1b["already_loaded"] is True
        assert len(calls) == 1

        # Unload resets state
        unloaded = mgr.unload_now()
        assert unloaded["was_loaded"] is True
        assert mgr.status()["loaded_from_local_cache"] is False
        assert mgr.status()["last_load_seconds"] is None

        # Fallback to online F5TTS(device=...) when cache misses
        tts_server.try_to_load_from_cache = lambda *a, **kw: None
        res2 = mgr.preload_now()
        assert res2["loaded_from_local_cache"] is False
        assert calls[-1] == {{"device": mgr.device}}
    finally:
        if orig_f5_pkg is None:
            sys.modules.pop("f5_tts", None)
        else:
            sys.modules["f5_tts"] = orig_f5_pkg
        if orig_f5_api is None:
            sys.modules.pop("f5_tts.api", None)
        else:
            sys.modules["f5_tts.api"] = orig_f5_api
finally:
    tts_server.try_to_load_from_cache = orig_try
"""
            self._run_server_script(script)

    def test_server_prefs_persistence_and_fastapi_preload_config_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = f"""
import json, os, sys, types
from pathlib import Path
sys.path.insert(0, r"{str(ROOT)}")
from fastapi.testclient import TestClient
from services import tts_server

tmp = Path(r"{tmp}")

# 1. save_server_prefs / load_server_prefs sanitize secrets & persist cleanly
tts_server.save_server_prefs(
    tmp,
    {{
        "idle_unload_seconds": 999999,
        "preload_on_startup": True,
        "offline_first": False,
        "tts_api_token": "MUST_NOT_BE_SAVED",
    }},
)
raw_saved = json.loads((tmp / "server_prefs.json").read_text(encoding="utf-8"))
assert "tts_api_token" not in raw_saved
assert raw_saved["idle_unload_seconds"] == 86400
loaded = tts_server.load_server_prefs(tmp)
assert loaded == {{"idle_unload_seconds": 86400, "preload_on_startup": True, "offline_first": False}}

# 2. FastAPI TestClient endpoints with X-TTS-Admin-Token enforcement
orig_mgr = tts_server.manager
orig_cat = tts_server.catalog
orig_try = tts_server.try_to_load_from_cache
orig_f5_pkg = sys.modules.get("f5_tts")
orig_f5_api = sys.modules.get("f5_tts.api")

class FakeF5TTS:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

fake_f5_api = types.ModuleType("f5_tts.api")
fake_f5_api.F5TTS = FakeF5TTS
fake_f5_pkg = types.ModuleType("f5_tts")
fake_f5_pkg.api = fake_f5_api
sys.modules["f5_tts"] = fake_f5_pkg
sys.modules["f5_tts.api"] = fake_f5_api

os.environ["TTS_API_TOKEN"] = "fast_boot_secret_tok"
try:
    tts_server.try_to_load_from_cache = lambda *a, **kw: None
    tts_server.catalog = tts_server.VoiceCatalog(data_dir=tmp)
    tts_server.manager = tts_server.F5ModelManager(
        idle_unload_seconds=1800,
        preload_on_startup=False,
        offline_first=True,
        prefs_dir=tmp,
    )
    client = TestClient(tts_server.app)

    # Unauthorized calls rejected with 401
    assert client.post("/preload").status_code == 401
    assert client.post("/config", data={{"idle_unload_seconds": "0"}}).status_code == 401

    headers = {{"X-TTS-Admin-Token": "fast_boot_secret_tok"}}
    r_cfg = client.post(
        "/config",
        headers=headers,
        data={{"idle_unload_seconds": "0", "preload_on_startup": "true", "offline_first": "true"}},
    )
    assert r_cfg.status_code == 200, r_cfg.text
    cfg_json = r_cfg.json()
    assert cfg_json["idle_unload_seconds"] == 0
    assert cfg_json["preload_on_startup"] is True
    assert cfg_json["offline_first"] is True
    assert tts_server.load_server_prefs(tmp)["idle_unload_seconds"] == 0

    r_pre = client.post("/preload", headers=headers)
    assert r_pre.status_code == 200, r_pre.text
    pre_json = r_pre.json()
    assert pre_json["status"] == "loaded"
    assert pre_json["model_loaded"] is True
    assert pre_json["idle_unload_seconds"] == 0

    r_health = client.get("/").json()
    assert r_health["model_loaded"] is True
    assert r_health["model_loading"] is False
    assert r_health["idle_unload_seconds"] == 0
    assert r_health["idle_remaining_seconds"] is None
    assert r_health["preload_on_startup"] is True
    assert "total_vram_gb" in r_health
    assert "last_load_seconds" in r_health

    # 3. Lifespan startup preload when preload_on_startup=True
    tts_server.manager = tts_server.F5ModelManager(
        idle_unload_seconds=0,
        preload_on_startup=True,
        offline_first=True,
        prefs_dir=tmp,
    )
    with TestClient(tts_server.app) as life_client:
        # Wait briefly for background preload task inside lifespan
        import time as _t
        for _ in range(30):
            st = life_client.get("/").json()
            if st["model_loaded"]:
                break
            _t.sleep(0.05)
        assert life_client.get("/").json()["model_loaded"] is True
    assert tts_server.manager._engine is None  # cleanly unloaded on shutdown
finally:
    os.environ.pop("TTS_API_TOKEN", None)
    tts_server.manager = orig_mgr
    tts_server.catalog = orig_cat
    tts_server.try_to_load_from_cache = orig_try
    if orig_f5_pkg is None:
        sys.modules.pop("f5_tts", None)
    else:
        sys.modules["f5_tts"] = orig_f5_pkg
    if orig_f5_api is None:
        sys.modules.pop("f5_tts.api", None)
    else:
        sys.modules["f5_tts.api"] = orig_f5_api
"""
            self._run_server_script(script)


class TestF5ClientAndUiControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_db_dir = assistant_db.DB_DIR
        self.orig_data_dir = assistant_db.DATA_DIR
        assistant_db.DB_DIR = Path(self.tmpdir.name)
        assistant_db.DATA_DIR = Path(self.tmpdir.name)
        assistant_db._initialized_dbs.clear()
        tts_engine._initialized_tts_dbs.clear()
        self.env_patch = patch.dict(os.environ, {"DB_NAME": "test_f5_fast_boot.db"})
        self.env_patch.start()
        assistant_db.init_db()
        tts_engine.init_tts_tables()

    async def asyncTearDown(self):
        self.env_patch.stop()
        assistant_db.DB_DIR = self.orig_db_dir
        assistant_db.DATA_DIR = self.orig_data_dir
        assistant_db._initialized_dbs.clear()
        tts_engine._initialized_tts_dbs.clear()
        self.tmpdir.cleanup()

    async def test_f5_client_and_synthesizer_preload_and_config(self):
        synth = tts_engine.HybridTtsSynthesizer(
            tts_api_url="http://127.0.0.1:9880",
            api_token="secret_tok",
        )
        req_mock = AsyncMock(
            side_effect=[
                (200, {"status": "loaded", "model_loaded": True, "vram_allocated_gb": 2.4}),
                (200, {"status": "online", "idle_unload_seconds": 0, "preload_on_startup": True}),
                (500, "internal error"),
            ]
        )
        with patch.object(synth.f5_client, "_f5_request", new=req_mock):
            pre_res = await synth.preload_f5_model()
            self.assertEqual(pre_res["status"], "loaded")
            self.assertEqual(req_mock.await_args_list[0].args, ("POST", "/preload"))
            self.assertEqual(req_mock.await_args_list[0].kwargs["timeout_total"], 90.0)

            cfg_res = await synth.update_f5_config(
                idle_unload_seconds=0,
                preload_on_startup=True,
                offline_first=True,
            )
            self.assertEqual(cfg_res["idle_unload_seconds"], 0)
            self.assertEqual(req_mock.await_args_list[1].args, ("POST", "/config"))
            self.assertIsNotNone(req_mock.await_args_list[1].kwargs["data"])
            self.assertEqual(req_mock.await_args_list[1].kwargs["timeout_total"], 5.0)

            err_res = await synth.preload_f5_model()
            self.assertIsNone(err_res)

        with patch.object(synth.f5_client, "_f5_request", new=AsyncMock(side_effect=ConnectionError("down"))):
            self.assertIsNone(await synth.preload_f5_model())
            self.assertIsNone(await synth.update_f5_config(idle_unload_seconds=1800))

        unconfigured = tts_engine.HybridTtsSynthesizer(tts_api_url="")
        self.assertIsNone(await unconfigured.preload_f5_model())
        self.assertIsNone(await unconfigured.update_f5_config(idle_unload_seconds=0))
        await synth.close()
        await unconfigured.close()

    async def test_tts_cog_preload_keepalive_and_status(self):
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        bot.voice_clients = []
        bot.get_channel.return_value = None
        settings = _make_settings(bot_owner_ids=(9000,))
        cog = TtsCog(bot, settings, MagicMock())
        cog.synthesizer.fetch_f5_voices = AsyncMock(return_value=[])
        cog.synthesizer.preload_f5_model = AsyncMock(
            return_value={
                "status": "loaded",
                "already_loaded": False,
                "loaded_from_local_cache": True,
                "load_seconds": 2.34,
                "vram_allocated_gb": 2.5,
                "total_vram_gb": 16.0,
            }
        )
        cog.synthesizer.update_f5_config = AsyncMock(return_value={"status": "online", "idle_unload_seconds": 0})
        cog.synthesizer.check_f5_health = AsyncMock(
            return_value={
                "status": "online",
                "model_loaded": True,
                "model_loading": False,
                "device": "cuda",
                "gpu_name": "NVIDIA RTX 4080 SUPER",
                "vram_allocated_gb": 2.5,
                "total_vram_gb": 16.0,
                "idle_unload_seconds": 0,
                "idle_remaining_seconds": None,
                "preload_on_startup": True,
                "offline_first": True,
                "public_voice_count": 1,
                "total_voice_count": 2,
            }
        )

        def make_ctx(uid: int):
            ctx = MagicMock()
            ctx.author = SimpleNamespace(id=uid, display_name=f"U{uid}", guild_permissions=SimpleNamespace(administrator=True))
            ctx.guild = SimpleNamespace(id=500, voice_client=None)
            ctx.channel = SimpleNamespace(id=1001, name="chat")
            ctx.typing = MagicMock(return_value=None)
            ctx.send = AsyncMock()
            return ctx

        # Non-owner is blocked even with guild administrator permission
        non_owner = make_ctx(222)
        await cog.tts_command.callback(cog, non_owner, action="preload")
        self.assertIn("🔒", non_owner.send.call_args.args[0])
        non_owner.send.reset_mock()
        await cog.tts_command.callback(cog, non_owner, action="keepalive", arg="on")
        self.assertIn("🔒", non_owner.send.call_args.args[0])
        cog.synthesizer.preload_f5_model.assert_not_awaited()
        cog.synthesizer.update_f5_config.assert_not_awaited()

        # Owner runs preload, keepalive on/off/seconds, and status
        owner = make_ctx(9000)
        await cog.tts_command.callback(cog, owner, action="preload")
        cog.synthesizer.preload_f5_model.assert_awaited_once()
        preload_msg = owner.send.call_args.args[0]
        self.assertIn("免連線直讀", preload_msg)
        self.assertIn("2.34s", preload_msg)
        self.assertIn("2.5 / 16.0 GB", preload_msg)

        await cog.tts_command.callback(cog, owner, action="keepalive", arg="on")
        cog.synthesizer.update_f5_config.assert_awaited_with(idle_unload_seconds=0, preload_on_startup=True)
        self.assertIn("顯存常駐 + 啟動自動預載", owner.send.call_args.args[0])

        await cog.tts_command.callback(cog, owner, action="keepalive", arg="off")
        cog.synthesizer.update_f5_config.assert_awaited_with(idle_unload_seconds=1800, preload_on_startup=False)
        self.assertIn("省顯存按需載入", owner.send.call_args.args[0])

        await cog.tts_command.callback(cog, owner, action="keepalive", arg="600")
        cog.synthesizer.update_f5_config.assert_awaited_with(idle_unload_seconds=600)

        await cog.tts_command.callback(cog, owner, action="keepalive", arg=None)
        self.assertIn("常駐不卸載", owner.send.call_args.args[0])

        await cog.tts_command.callback(cog, owner, action="status")
        status_embed = owner.send.call_args.kwargs["embed"]
        fields = {f.name: f.value for f in status_embed.fields}
        self.assertIn("永不自動釋放", fields["語音引擎"])
        self.assertNotIn("None 秒", fields["語音引擎"])
        self.assertIn("16.0 GB", fields["語音引擎"])
        self.assertIn("啟動預載", fields["語音引擎"])
        self.assertIn("離線快取直讀", fields["語音引擎"])

    async def test_setup_panel_health_tab_f5_buttons_and_check(self):
        settings = _make_settings()
        bot = MagicMock()
        bot.is_owner = AsyncMock(return_value=False)
        cog = SetupCog(bot, settings, MagicMock(), gemini_client=None)

        # 1. Health tab with tts_api_url configured stays strictly within Discord component limits
        h_view = SetupPanelView(cog, active_tab="health")
        _assert_discord_view_limits(self, h_view, "health_with_f5")
        btn_labels = [c.label for c in h_view.children if isinstance(c, discord.ui.Button)]
        self.assertIn("🔥 F5 常駐預載", btn_labels)
        self.assertIn("💤 F5 省顯存模式", btn_labels)
        self.assertIn("🧹 釋放 F5 顯存", btn_labels)

        # 2. _check_f5_tts parses rich JSON & recommends resident preload on >=10GB VRAM with lazy unload
        fake_health_resp = MagicMock()
        fake_health_resp.status_code = 200
        fake_health_resp.json.return_value = {
            "status": "online",
            "model_loaded": False,
            "model_loading": False,
            "device": "cuda",
            "gpu_name": "NVIDIA RTX 4080 SUPER",
            "vram_allocated_gb": 0.0,
            "total_vram_gb": 16.0,
            "idle_unload_seconds": 1800,
            "offline_first": True,
        }
        with patch("cogs.setup_cog.httpx.get", return_value=fake_health_resp):
            item = await _check_f5_tts(settings)
        self.assertTrue(item["ok"])
        self.assertIn("16.0 GB", item["detail"])
        self.assertIn("閒置 1800s 卸載", item["detail"])
        self.assertIn("F5 常駐預載", item["fix_hint"])

        # 3. Clicking Row 2 F5 buttons triggers POST /config, /preload, /unload and refreshes embed
        owner_inter = MagicMock()
        owner_inter.user = SimpleNamespace(id=9000)
        owner_inter.guild = None
        owner_inter.channel = None
        owner_inter.response = MagicMock()
        owner_inter.response.defer = AsyncMock()
        owner_inter.edit_original_response = AsyncMock()

        def fake_post(url, headers=None, data=None, timeout=5.0, follow_redirects=False):
            resp = MagicMock()
            resp.status_code = 200
            if url.endswith("/preload"):
                resp.json.return_value = {
                    "status": "loaded",
                    "vram_allocated_gb": 2.4,
                    "total_vram_gb": 16.0,
                    "load_seconds": 1.85,
                    "loaded_from_local_cache": True,
                }
            elif url.endswith("/unload"):
                resp.json.return_value = {"status": "unloaded", "vram_allocated_gb": 0.0}
            else:
                resp.json.return_value = {"status": "online", "idle_unload_seconds": 0}
            return resp

        with (
            patch("cogs.setup_cog.httpx.post", side_effect=fake_post) as mock_post,
            patch("cogs.setup_cog.httpx.get", return_value=fake_health_resp),
            patch("cogs.setup_cog.shutil.which", return_value="ffmpeg"),
        ):
            resident_btn = [c for c in h_view.children if isinstance(c, discord.ui.Button) and c.label == "🔥 F5 常駐預載"][0]
            await resident_btn.callback(owner_inter)
            self.assertIn("常駐預載", h_view.status_note)
            self.assertTrue(any(call.args[0].endswith("/config") for call in mock_post.call_args_list))
            self.assertTrue(any(call.args[0].endswith("/preload") for call in mock_post.call_args_list))

            lazy_btn = [c for c in h_view.children if isinstance(c, discord.ui.Button) and c.label == "💤 F5 省顯存模式"][0]
            await lazy_btn.callback(owner_inter)
            self.assertIn("省顯存", h_view.status_note)

            unload_btn = [c for c in h_view.children if isinstance(c, discord.ui.Button) and c.label == "🧹 釋放 F5 顯存"][0]
            await unload_btn.callback(owner_inter)
            self.assertIn("釋放 F5-TTS 顯存", h_view.status_note)


if __name__ == "__main__":
    unittest.main()
