"""PR-02 剩余：ComfyUI 上传唯一文件名（不同任务同名素材不互相覆盖）。

背景：ComfyUI /upload/image 带 overwrite=true，且 input 目录是共享的——
两个任务都上传 frame_0001.png（各自镜头抽帧的默认名）时，后传者覆盖先传者，
先提交的工作流在执行时读到的是别人的图。修法：未显式指定上传名时自动加
uuid 前缀；显式名不改写（向后兼容）。
"""
import pytest

from app.comfyui import ComfyUIError, ComfyUIClient


class _CaptureResp:
    status_code = 200

    def __init__(self, captured):
        self._captured = captured

    def json(self):
        return {"name": self._captured.get("uploaded_name") or "",
                "subfolder": "", "type": "input"}


@pytest.fixture()
def captured(monkeypatch, tmp_path):
    """替换 requests.post，捕获 multipart 里的文件名；建两张同名临时图。"""
    import app.comfyui as comfyui_mod

    state = {"uploaded_name": None}
    calls = []

    def _fake_post(url, files=None, data=None, timeout=None):
        calls.append(url)
        state["uploaded_name"] = files["image"][0]
        return _CaptureResp(state)

    monkeypatch.setattr(comfyui_mod.requests, "post", _fake_post)

    a = tmp_path / "same" / "frame_0001.png"
    a.parent.mkdir(parents=True)
    a.write_bytes(b"\x89PNG-a")
    b = tmp_path / "other" / "frame_0001.png"
    b.parent.mkdir(parents=True)
    b.write_bytes(b"\x89PNG-b")
    return {"state": state, "a": a, "b": b, "calls": calls}


def test_upload_generates_unique_prefix(captured):
    c = ComfyUIClient()
    r = c.upload_image(captured["a"])
    name = captured["state"]["uploaded_name"]
    assert name.endswith("frame_0001.png"), "保留原始文件名便于辨认"
    assert name != "frame_0001.png", "必须带唯一前缀，不能裸用默认名"
    assert r["name"] == name


def test_upload_two_same_names_never_collide(captured):
    c = ComfyUIClient()
    c.upload_image(captured["a"])
    first = captured["state"]["uploaded_name"]
    c.upload_image(captured["b"])
    second = captured["state"]["uploaded_name"]
    assert first != second, "同名文件两次上传得到不同 input 名（不互相覆盖）"


def test_upload_explicit_name_preserved(captured):
    c = ComfyUIClient()
    c.upload_image(captured["a"], name="explicit.png")
    assert captured["state"]["uploaded_name"] == "explicit.png"


def test_upload_failure_raises(captured, monkeypatch):
    import app.comfyui as comfyui_mod

    class _BadResp:
        status_code = 500
        text = "boom"

        def json(self):
            return {}

    monkeypatch.setattr(comfyui_mod.requests, "post",
                        lambda url, files=None, data=None, timeout=None: _BadResp())
    c = ComfyUIClient()
    with pytest.raises(ComfyUIError):
        c.upload_image(captured["a"])
