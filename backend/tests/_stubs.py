"""测试用的最小依赖垫片（本机缺第三方包时让 app 模块可导入）。

本机 pip 走 HTTPS 常报 SSL 错，requests / imageio-ffmpeg 装不上；**不代表
相关功能已验证**。这些桩只提供导入期需要的符号，任何真实网络/编码调用都会
抛错，避免测试把「桩跑通了」误当成「功能跑通了」。
"""
import sys
import types


def _missing(name: str) -> bool:
    try:
        __import__(name)
        return False
    except ImportError:
        return True


def _stub_requests() -> None:
    if not _missing("requests"):
        return
    module = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    def _blocked(*_a, **_k):
        raise RequestException("测试环境未安装 requests：不允许真实网络调用")

    module.RequestException = RequestException
    module.get = _blocked
    module.post = _blocked
    module.Session = lambda *a, **k: _blocked()
    sys.modules["requests"] = module


def _stub_imageio_ffmpeg() -> None:
    if not _missing("imageio_ffmpeg"):
        return
    module = types.ModuleType("imageio_ffmpeg")
    module.get_ffmpeg_exe = lambda: "ffmpeg"
    module.get_ffmpeg_version = lambda: "unavailable"
    module.read_frames = lambda *a, **k: iter(())
    module.write_frames = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("测试环境无 ffmpeg")
    )
    module.count_frames_and_secs = lambda *a, **k: (0, 0.0)
    sys.modules["imageio_ffmpeg"] = module


def install() -> None:
    _stub_requests()
    _stub_imageio_ffmpeg()
