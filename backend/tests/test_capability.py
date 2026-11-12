"""capability 单测：机型分档 + 模型可用性判定。

不连真 ComfyUI：``_probe_hardware`` 用桩替换，节点表直接传参。
真实服务端探测（``h3_server.probe`` / ``hw_probe.probe``）另有实测，
见 docs/reports/20261112-comfy-only-boundary-and-capacity.md 第 2 节。
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from app import capability, h3_freevideo, h3_protocol  # noqa: E402


def _stub_hw(monkeypatch, vram=8.0, ram=32.0, source="server"):
    monkeypatch.setattr(capability, "_probe_hardware", lambda url=None: {
        "vram_gib": vram, "ram_gib": ram, "gpu_name": "RTX 4060", "source": source,
    })


def _by_id(result, model_id):
    return next(m for m in result["models"] if m["id"] == model_id)


def test_pick_tier_boundaries():
    # 8188MiB 会报成 7.996GB —— 容差必须兜住，否则 8GB 卡被判成无档
    assert capability.pick_tier(7.996, 32.0).id == "s"
    assert capability.pick_tier(8.0, 32.0).id == "s"
    assert capability.pick_tier(11.0, 32.0).id == "s"
    # 容差是有意的：12GB 卡（3060）实际常报 11.9x，按 11.9 降档等于白降
    assert capability.pick_tier(11.9, 32.0).id == "m"
    assert capability.pick_tier(12.0, 32.0).id == "m"
    # 16GB 卡实报 15.9 同理，不能掉回 m
    assert capability.pick_tier(15.9, 48.0).id == "l"
    assert capability.pick_tier(24.0, 64.0).id == "xl"
    # 显存再低也退到最低档而不是返回 None
    assert capability.pick_tier(0.0, 0.0).id == "s"


def test_limits_follow_protocol():
    """画布上限不许在本模块另写一份，必须与 h3_protocol.LIMITS 一致。"""
    _stub = None
    for min_vram, pixels, frames, label in h3_protocol.LIMITS:
        got = capability._limits_for(min_vram)
        assert got["max_pixels"] == pixels
        assert got["max_frames"] == frames
        assert got["label"] == label
    assert capability._limits_for(8.0)["max_size"] == (1344, 768)
    assert capability._limits_for(24.0)["max_size"] == (1920, 1088)


def test_h3_blocked_when_node_missing(monkeypatch):
    _stub_hw(monkeypatch)
    r = capability.assess(disk_free_gib=200.0,
                          nodes=["KSampler", "CLIPLoader"])
    item = _by_id(r, "h3-video")
    assert item["availability"] == "blocked"
    assert "FreeVideoGenerate" in item["availability_reason"]
    assert "G3" in item["availability_reason"]


def test_offline_is_unknown_not_blocked(monkeypatch):
    """ComfyUI 没连上 ≠ 插件没装。错报成 blocked 会让用户去重装插件。"""
    _stub_hw(monkeypatch)
    r = capability.assess(disk_free_gib=200.0, nodes=None)
    assert r["comfy_connected"] is False
    assert _by_id(r, "h3-video")["availability"] == "unknown"


def test_h3_available_with_node_and_space(monkeypatch):
    _stub_hw(monkeypatch, vram=8.0, ram=32.0)
    r = capability.assess(disk_free_gib=200.0,
                          nodes=["FreeVideoGenerate", "SAM3DBody_Loader"])
    assert _by_id(r, "h3-video")["availability"] == "available"
    assert _by_id(r, "white-model")["availability"] == "available"
    assert r["disk"]["state"] == "full"
    assert r["limits"]["max_size"] == [1344, 768]


def test_disk_insufficient_blocks_video(monkeypatch):
    _stub_hw(monkeypatch)
    need = h3_freevideo.need_disk_gib()
    r = capability.assess(disk_free_gib=need - 10,
                          nodes=["FreeVideoGenerate"])
    assert r["disk"]["state"] == "insufficient"
    item = _by_id(r, "h3-video")
    assert item["availability"] == "blocked"
    assert "磁盘不足" in item["availability_reason"]
    # 装了视频链路但没装生图：video_only
    r2 = capability.assess(disk_free_gib=need + 2, nodes=["FreeVideoGenerate"])
    assert r2["disk"]["state"] == "video_only"


def test_low_ram_is_degraded_not_blocked(monkeypatch):
    """16GB 内存能起，只是慢。把「慢」说成「不能跑」是错的。"""
    _stub_hw(monkeypatch, vram=8.0, ram=16.0)
    r = capability.assess(disk_free_gib=200.0, nodes=["FreeVideoGenerate"])
    item = _by_id(r, "h3-video")
    assert item["availability"] == "degraded"
    assert "回读" in item["availability_reason"]


def test_low_vram_blocks(monkeypatch):
    _stub_hw(monkeypatch, vram=6.0, ram=32.0)
    r = capability.assess(disk_free_gib=200.0, nodes=["FreeVideoGenerate"])
    assert _by_id(r, "h3-video")["availability"] == "blocked"
    assert "显存" in _by_id(r, "h3-video")["availability_reason"]


def test_structural_blocks_do_not_depend_on_machine(monkeypatch):
    """H3 Image / prompt-vlm 在任何机器上都不可用——缺的是入口，不是资源。"""
    _stub_hw(monkeypatch, vram=80.0, ram=256.0)
    r = capability.assess(disk_free_gib=2000.0,
                          nodes=["FreeVideoGenerate", "SAM3DBody_Loader"])
    for mid in ("h3-image", "prompt-vlm"):
        item = _by_id(r, mid)
        assert item["availability"] == "blocked"
        assert item["entry"] is None
        assert item["gap"] in ("G4", "G5")


def test_qwen_image_stays_unverified(monkeypatch):
    """权重在、节点在，但没真机跑通，不能对外说支持。"""
    _stub_hw(monkeypatch)
    r = capability.assess(disk_free_gib=2000.0,
                          nodes=["FreeVideoGenerate", "UNETLoader"])
    item = _by_id(r, "qwen-image-2.1")
    assert item["availability"] == "unverified"
    assert item["gap"] == "G6"


def test_disk_totals_are_consistent():
    """全量磁盘口径 = 视频链路 + 生图 + H3 Image + 提示词模型 + 输出余量。"""
    r = capability.assess(vram_gib=8.0, ram_gib=32.0)
    expect = round(h3_freevideo.need_disk_gib() + capability.QWEN_IMAGE_GIB
                   + capability.H3_IMAGE_GIB + capability.PROMPT_VLM_GIB
                   + capability.OUTPUT_HEADROOM_GIB, 1)
    assert r["disk"]["full_gib"] == expect
    # 视频-only 必须等于既有的 H3 门槛，不许另算
    assert r["disk"]["video_only_gib"] == round(h3_freevideo.need_disk_gib(), 1)
