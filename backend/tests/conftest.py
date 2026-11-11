"""测试环境垫片：仅在缺依赖时补齐「导入期」所需的最小接口。

说明：本轮沙箱到 files.pythonhosted.org 的下载超时，imageio-ffmpeg（含 30MB
静态 ffmpeg 二进制）装不上。本仓库的测试不涉及真实视频合成，只用它的
``get_ffmpeg_exe``；缺失时注入同名桩模块让 ``app.video`` 可导入，
**不代表 ffmpeg 相关功能已验证**。真实环境请按 backend/requirements.txt 安装。
"""
import sys
import types


def _stub_imageio_ffmpeg() -> None:
    try:
        import imageio_ffmpeg  # noqa: F401
        return
    except ImportError:
        pass
    module = types.ModuleType("imageio_ffmpeg")
    module.get_ffmpeg_exe = lambda: "ffmpeg"
    module.get_ffmpeg_version = lambda: "unavailable"
    module.read_frames = lambda *a, **k: iter(())
    module.write_frames = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("测试环境无 ffmpeg"))
    module.count_frames_and_secs = lambda *a, **k: (0, 0.0)
    sys.modules["imageio_ffmpeg"] = module


_stub_imageio_ffmpeg()

# 资源守卫默认关掉：它读的是「本机此刻的余量」，同一份代码在 8GB 笔记本上
# 会被降档、在 24GB 台式机上放行，用例会因为跑在哪台机器而红。需要验证守卫
# 本身的用例（test_resource_guard）自己显式设 AIVIDEO_RESOURCE_GUARD。
import os  # noqa: E402

os.environ.setdefault("AIVIDEO_RESOURCE_GUARD", "off")
