"""P0-03 受管路径解析回归：目录越界 / 编码穿越 / 盘符 / 反斜杠 /
符号链接逃逸 / 类型白名单，以及 upload_reference_image 与 export 的接入。

背景：审查发现多处 `OUTPUTS_DIR / url[len("/outputs/"):]` 直接拼接，
无法阻止目录越界。现在统一走 managed_paths.resolve_managed_asset，
本文件把四道闸（段检查 / 盘符与绝对拦截 / realpath 边界 / 扩展名白名单）
固化为回归测试。
"""
import os
from pathlib import Path

import pytest

from app.comfyui import (
    ComfyUIError,
    local_path_from_output_url,
    upload_reference_image,
)
from app.export import _exists, _url_to_path
from app.managed_paths import (
    AUDIO_SUFFIXES,
    IMAGE_SUFFIXES,
    VIDEO_SUFFIXES,
    resolve_managed_asset,
)

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture()
def base(tmp_path):
    """隔离的受管根目录 + 若干合法文件 + 目录外目标文件。"""
    out = tmp_path / "outputs"
    (out / "refs").mkdir(parents=True)
    (out / "refs" / "ok.png").write_bytes(PNG)
    (out / "refs" / "UPPER.PNG").write_bytes(PNG)
    (out / "shot.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (out / "voice.mp3").write_bytes(b"ID3")
    (out / "notes.txt").write_text("x", encoding="utf-8")
    # 越界目标：与 base 同级（../outside.png 的落点）
    (tmp_path / "outside.png").write_bytes(PNG)
    (tmp_path / "outside.mp3").write_bytes(b"ID3")
    return out


# =====================================================================
# resolve_managed_asset：合法放行
# =====================================================================
class TestAllowed:
    def test_valid_image(self, base):
        p = resolve_managed_asset("/outputs/refs/ok.png", base_dir=base)
        assert p == (base / "refs" / "ok.png").resolve()

    def test_nested_subdir_ok(self, base):
        p = resolve_managed_asset("/outputs/refs/ok.png", base_dir=base)
        assert p is not None and p.parent == (base / "refs").resolve()

    def test_uppercase_suffix_ok(self, base):
        p = resolve_managed_asset("/outputs/refs/UPPER.PNG", base_dir=base)
        assert p is not None and p.name == "UPPER.PNG"

    def test_video_with_video_suffixes(self, base):
        p = resolve_managed_asset("/outputs/shot.mp4", allowed_types=VIDEO_SUFFIXES,
                                  base_dir=base)
        assert p == (base / "shot.mp4").resolve()

    def test_audio_with_audio_suffixes(self, base):
        p = resolve_managed_asset("/outputs/voice.mp3", allowed_types=AUDIO_SUFFIXES,
                                  base_dir=base)
        assert p == (base / "voice.mp3").resolve()

    def test_query_string_stripped(self, base):
        """带查询串的同机 URL 仍按 path 解析（受管 URL 不应带 query，
        出现了也只取 path，不因查询串放大攻击面）。"""
        p = resolve_managed_asset("/outputs/refs/ok.png?x=1", base_dir=base)
        assert p == (base / "refs" / "ok.png").resolve()


# =====================================================================
# resolve_managed_asset：越界与可疑输入一律拒绝
# =====================================================================
class TestRejected:
    def test_parent_traversal(self, base):
        assert resolve_managed_asset("/outputs/../outside.png", base_dir=base) is None

    def test_nested_parent_traversal(self, base):
        assert resolve_managed_asset("/outputs/refs/../../outside.png",
                                     base_dir=base) is None

    def test_double_encoded_traversal(self, base):
        # %2e%2e%2f → ../ ：解码后穿越，未解码也含 % ——两道都拦
        assert resolve_managed_asset("/outputs/%2e%2e%2foutside.png",
                                     base_dir=base) is None

    def test_backslash_traversal(self, base):
        # Windows 下反斜杠是路径分隔符
        assert resolve_managed_asset("/outputs/..\\outside.png", base_dir=base) is None

    def test_drive_letter(self, base):
        # Windows：pathlib 会用盘符片段替换掉 base
        assert resolve_managed_asset("/outputs/C:/Windows/x.png", base_dir=base) is None

    def test_unc_path(self, base):
        assert resolve_managed_asset("/outputs/\\\\\\\\server\\\\share\\\\x.png",
                                     base_dir=base) is None

    def test_absolute_path_segment(self, base):
        """前导多余斜杠被规范成 base 内相对段（不会逃逸到系统根）。"""
        p = resolve_managed_asset("/outputs//etc/passwd.png", base_dir=base)
        if p is not None:
            assert p == (base / "etc" / "passwd.png").resolve()
            assert p.is_relative_to(base.resolve())

    def test_external_url(self, base):
        assert resolve_managed_asset("https://evil.com/refs/ok.png", base_dir=base) is None

    def test_wrong_type_image_only(self, base):
        assert resolve_managed_asset("/outputs/notes.txt",
                                     allowed_types=IMAGE_SUFFIXES, base_dir=base) is None

    def test_wrong_type_audio_as_video(self, base):
        assert resolve_managed_asset("/outputs/voice.mp3",
                                     allowed_types=VIDEO_SUFFIXES, base_dir=base) is None

    def test_empty(self, base):
        assert resolve_managed_asset("", base_dir=base) is None
        assert resolve_managed_asset("   ", base_dir=base) is None
        assert resolve_managed_asset(None, base_dir=base) is None

    def test_no_managed_prefix(self, base):
        # 裸相对路径（没有 /outputs/ 段）不受管
        assert resolve_managed_asset("refs/ok.png", base_dir=base) is None

    def test_prefix_only(self, base):
        assert resolve_managed_asset("/outputs/", base_dir=base) is None
        assert resolve_managed_asset("/outputs", base_dir=base) is None

    def test_dot_segment(self, base):
        assert resolve_managed_asset("/outputs/./refs/ok.png", base_dir=base) is None

    def test_symlink_escape(self, base, tmp_path):
        """符号链接指向 base 外：realpath 边界拒绝。

        注意：WorkBuddy 沙箱/部分 Windows 环境禁止建链（mklink 拒绝访问），
        建链失败时 skip——本断言只在符号链接真实存在时才执行，不用 vacuous
        pass 冒充通过；真机/CI 上会真实校验 realpath 边界。
        """
        link = base / "escape.png"
        try:
            os.symlink(tmp_path / "outside.png", link)
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不允许创建符号链接（需要管理员/开发者模式）")
        if not link.is_symlink():
            pytest.skip("符号链接未被真实创建（沙箱限制），跳过")
        assert resolve_managed_asset("/outputs/escape.png", base_dir=base) is None

    def test_local_path_from_output_url_rejects(self, base, monkeypatch):
        """local_path_from_output_url（ComfyUI 参考图定位）同样拒绝越界。

        该函数历史上用 app.comfyui 模块级 OUTPUTS_DIR，故 patch 该绑定。
        """
        monkeypatch.setattr("app.comfyui.OUTPUTS_DIR", base)
        assert local_path_from_output_url("/outputs/../outside.png") is None
        assert local_path_from_output_url("https://evil.com/x.png") is None
        assert local_path_from_output_url("/outputs/refs/ok.png") == (
            base / "refs" / "ok.png").resolve()


# =====================================================================
# upload_reference_image：越界不上传
# =====================================================================
def test_upload_reference_image_rejects_traversal(monkeypatch, base):
    """越界 URL → ComfyUIError，且不触达 client.upload_image（不上传）。"""
    monkeypatch.setattr("app.comfyui.OUTPUTS_DIR", base)
    uploaded = []

    class _Client:
        def upload_image(self, p):
            uploaded.append(p)
            raise AssertionError("越界文件不应被上传")

    with pytest.raises(ComfyUIError):
        upload_reference_image(_Client(), "/outputs/../outside.png")
    assert not uploaded, "越界文件被上传了"


def test_upload_reference_image_missing_file(monkeypatch, base):
    """合法 URL 但文件不存在 → ComfyUIError（不静默丢失参考图）。"""
    monkeypatch.setattr("app.comfyui.OUTPUTS_DIR", base)
    with pytest.raises(ComfyUIError):
        upload_reference_image(type("C", (), {"upload_image": lambda *a: {}})(),
                               "/outputs/refs/gone.png")


def test_upload_reference_image_ok(monkeypatch, base):
    """合法受管图片上传成功，返回 input 目录引用名。"""
    monkeypatch.setattr("app.comfyui.OUTPUTS_DIR", base)

    class _Client:
        def upload_image(self, p):
            assert Path(p).name == "ok.png"
            return {"name": "ok.png", "subfolder": "aivideo"}

    assert upload_reference_image(_Client(), "/outputs/refs/ok.png") == "aivideo/ok.png"


# =====================================================================
# export：URL → 路径解析同样受管
# =====================================================================
def test_export_url_to_path_managed(monkeypatch, base):
    """export._url_to_path 历史上用 app.export 模块级 OUTPUTS_DIR。"""
    monkeypatch.setattr("app.export.OUTPUTS_DIR", base)
    assert _url_to_path("/outputs/voice.mp3") == (base / "voice.mp3").resolve()
    assert _exists("/outputs/voice.mp3") is True


def test_export_url_to_path_rejects_traversal(monkeypatch, base):
    monkeypatch.setattr("app.export.OUTPUTS_DIR", base)
    assert _url_to_path("/outputs/../outside.mp3") is None
    assert _exists("/outputs/../outside.mp3") is False
    assert _url_to_path("https://evil.com/voice.mp3") is None
    assert _url_to_path("/outputs/notes.txt") is None  # 非媒体白名单
    assert _exists("") is False
