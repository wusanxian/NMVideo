"""comfy_manager 单测：托管 ComfyUI 的发现 / 状态 / 启停守卫。

不真起 ComfyUI 进程：真实 spawn 的路径（Popen + 就绪等待）需要真机，
本轮只验证决策分支（在线不重复起、远程不接管、找不到不给起、
stop 只认自己 spawn 的进程）。真实启动验证标注：未执行。
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import comfy_manager  # noqa: E402


class _StubClient:
    """替身 ComfyUIClient：可控 base_url 与在线状态"""

    def __init__(self, base_url="http://127.0.0.1:8188", online=False):
        self.base_url = base_url
        self._online = online

    def is_available(self, timeout: float = 2.5) -> bool:
        return self._online


def _stub_client(monkeypatch, base_url="http://127.0.0.1:8188", online=False):
    monkeypatch.setattr(
        comfy_manager, "ComfyUIClient",
        lambda *a, **k: _StubClient(base_url=base_url, online=online))


def _make_comfy_dir(root: Path) -> Path:
    d = root / "ComfyUI"
    (d / "venv" / "Scripts").mkdir(parents=True)
    (d / "main.py").write_text("# stub", encoding="utf-8")
    (d / "venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
    return d


# ---------------------------------------------------------------- discover
def test_discover_settings_valid(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        d = _make_comfy_dir(Path(td))
        monkeypatch.setattr(comfy_manager, "_configured_dir", lambda: str(d))
        got = comfy_manager.discover()
        assert got["dir"] == str(d)
        assert got["source"] == "settings"
        assert got["valid"] is True
        assert got["python"] and Path(got["python"]).is_file()


def test_discover_settings_invalid_dir(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        d = Path(td) / "not-comfy"
        d.mkdir()
        monkeypatch.setattr(comfy_manager, "_configured_dir", lambda: str(d))
        got = comfy_manager.discover()
        # 坏目录要如实带出来（valid=False），不能装作没配置
        assert got["dir"] == str(d)
        assert got["valid"] is False


def test_discover_env_var(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        d = _make_comfy_dir(Path(td))
        monkeypatch.setattr(comfy_manager, "_configured_dir", lambda: None)
        monkeypatch.setenv("AIVIDEO_COMFY_DIR", str(d))
        got = comfy_manager.discover()
        assert got["source"] == "env"
        assert got["valid"] is True


def test_discover_auto_then_none(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        d = _make_comfy_dir(Path(td))
        monkeypatch.setattr(comfy_manager, "_configured_dir", lambda: None)
        monkeypatch.delenv("AIVIDEO_COMFY_DIR", raising=False)
        monkeypatch.setattr(comfy_manager, "AUTO_CANDIDATES", (d,))
        assert comfy_manager.discover()["source"] == "auto"
        monkeypatch.setattr(comfy_manager, "AUTO_CANDIDATES", ())
        got = comfy_manager.discover()
        assert got["dir"] is None
        assert got["valid"] is False


def test_find_python_priority(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        d = _make_comfy_dir(root)  # 自带 venv
        # 上级 envs 与 python_embeded 也都在：venv 优先
        envs_py = root / "envs" / "comfyui-abc" / "Scripts" / "python.exe"
        envs_py.parent.mkdir(parents=True)
        envs_py.write_text("", encoding="utf-8")
        embed_py = d / "python_embeded" / "python.exe"
        embed_py.parent.mkdir()
        embed_py.write_text("", encoding="utf-8")
        got = comfy_manager._find_python(d)
        assert Path(got) == d / "venv" / "Scripts" / "python.exe"
        # 没了 venv：envs 次之
        (d / "venv").rename(d / "venv_bak")
        got = comfy_manager._find_python(d)
        assert Path(got) == envs_py
        # 连 envs 也没有：python_embeded 兜底
        (root / "envs").rename(root / "envs_bak")
        got = comfy_manager._find_python(d)
        assert Path(got) == embed_py


# ---------------------------------------------------------------- is_local
def test_is_local_url(monkeypatch):
    _stub_client(monkeypatch, base_url="http://127.0.0.1:8188")
    assert comfy_manager.is_local_url() is True
    _stub_client(monkeypatch, base_url="http://localhost:8188")
    assert comfy_manager.is_local_url() is True
    # 远程地址不提供启停——那是服务方机器
    _stub_client(monkeypatch, base_url="http://192.168.1.20:8188")
    assert comfy_manager.is_local_url() is False


# ---------------------------------------------------------------- state
def test_state_online(monkeypatch):
    _stub_client(monkeypatch, online=True)
    monkeypatch.setattr(comfy_manager, "_managed_alive", lambda: False)
    monkeypatch.setattr(comfy_manager, "is_local_url", lambda: True)
    s = comfy_manager.state()
    assert s["online"] is True
    assert s["can_start"] is False
    assert s["note"] == ""


def test_state_offline_local_ready_to_start(monkeypatch):
    _stub_client(monkeypatch, online=False)
    monkeypatch.setattr(comfy_manager, "_managed_alive", lambda: False)
    monkeypatch.setattr(comfy_manager, "is_local_url", lambda: True)
    with tempfile.TemporaryDirectory() as td:
        d = _make_comfy_dir(Path(td))
        monkeypatch.setattr(comfy_manager, "discover",
                            lambda: {"dir": str(d), "source": "auto",
                                     "python": "python", "valid": True})
        s = comfy_manager.state()
        assert s["can_start"] is True
        assert s["can_stop"] is False
        assert "一键启动" in s["note"]


def test_state_starting(monkeypatch):
    _stub_client(monkeypatch, online=False)
    monkeypatch.setattr(comfy_manager, "_managed_alive", lambda: True)
    monkeypatch.setattr(comfy_manager, "is_local_url", lambda: True)
    s = comfy_manager.state()
    assert s["starting"] is True
    assert s["can_start"] is False
    assert s["can_stop"] is True


def test_state_remote(monkeypatch):
    _stub_client(monkeypatch, online=False)
    monkeypatch.setattr(comfy_manager, "_managed_alive", lambda: False)
    monkeypatch.setattr(comfy_manager, "is_local_url", lambda: False)
    s = comfy_manager.state()
    # 远程实例：不给启停按钮，说明由服务方管理
    assert s["can_start"] is False
    assert s["can_stop"] is False
    assert "远程" in s["note"]


# ---------------------------------------------------------------- start
def test_start_already_online(monkeypatch):
    _stub_client(monkeypatch, online=True)
    got = comfy_manager.start()
    assert got == {"started": False, "reason": "already_online"}


def test_start_remote_rejected(monkeypatch):
    _stub_client(monkeypatch, base_url="http://10.0.0.5:8188", online=False)
    got = comfy_manager.start()
    assert got["started"] is False
    assert got["reason"] == "remote"


def test_start_not_found(monkeypatch):
    _stub_client(monkeypatch, online=False)
    monkeypatch.setattr(comfy_manager, "is_local_url", lambda: True)
    monkeypatch.setattr(comfy_manager, "_configured_dir", lambda: None)
    monkeypatch.delenv("AIVIDEO_COMFY_DIR", raising=False)
    monkeypatch.setattr(comfy_manager, "AUTO_CANDIDATES", ())
    got = comfy_manager.start()
    assert got["started"] is False
    assert got["reason"] == "not_found"


def test_start_invalid_dir(monkeypatch):
    _stub_client(monkeypatch, online=False)
    monkeypatch.setattr(comfy_manager, "is_local_url", lambda: True)
    with tempfile.TemporaryDirectory() as td:
        bad = Path(td) / "empty"
        bad.mkdir()
        monkeypatch.setattr(comfy_manager, "discover",
                            lambda: {"dir": str(bad), "source": "settings",
                                     "python": None, "valid": False})
        got = comfy_manager.start()
        assert got["started"] is False
        assert got["reason"] == "invalid_dir"


# ---------------------------------------------------------------- stop
def test_stop_not_managed(monkeypatch):
    # 没有本应用 spawn 的进程：明确拒绝，不碰外部 ComfyUI
    got = comfy_manager.stop()
    assert got["stopped"] is False
    assert got["reason"] == "not_managed"
