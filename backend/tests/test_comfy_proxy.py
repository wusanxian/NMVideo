"""ComfyUI 客户端「别把本机服务送去代理」的回归。

坑的来历（2026-11-03）：``requests`` 默认认 ``HTTP_PROXY`` 环境变量。用户开着
Clash 等代理工具时（或宿主启动器带着代理变量拉起本程序），连
``127.0.0.1:8188`` 的请求会被发给代理，实测拿回 **502**——界面上变成
「ComfyUI 未连接」，而 ComfyUI 明明在跑。

判定口径：内网直连、公网照旧走代理（那是用户为连远端 ComfyUI 特意配的）。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import comfyui  # noqa: E402


class IsDirectTest(unittest.TestCase):
    def test_loopback_is_direct(self):
        for url in (
            "http://127.0.0.1:8188",
            "http://127.0.0.1:8188/",
            "http://localhost:8188",
            "http://0.0.0.0:8188",
            "http://[::1]:8188",
        ):
            self.assertTrue(comfyui._is_direct(url), url)

    def test_private_network_is_direct(self):
        for url in (
            "http://192.168.1.20:8188",
            "http://10.0.0.9:8188",
            "http://172.16.5.4:8188",
            "http://169.254.10.10:8188",
        ):
            self.assertTrue(comfyui._is_direct(url), url)

    def test_lan_hostname_is_direct(self):
        # 不带点的主机名是内网 DNS 名（comfyui / gpu01），也该直连
        for url in ("http://comfyui:8188", "http://gpu01:8188"):
            self.assertTrue(comfyui._is_direct(url), url)

    def test_public_host_keeps_proxy(self):
        # 公网地址是用户为连远端 ComfyUI 特意配的代理，替他关掉反而连不上
        for url in (
            "https://comfy.example.com",
            "http://8.8.8.8:8188",
            "http://172.32.0.1:8188",   # 172.16/12 之外
        ):
            self.assertFalse(comfyui._is_direct(url), url)

    def test_bad_url_does_not_raise(self):
        self.assertFalse(comfyui._is_direct("::not a url::"))


class SessionTest(unittest.TestCase):
    def test_local_client_ignores_env_proxy(self):
        c = comfyui.ComfyUIClient("http://127.0.0.1:8188")
        self.assertTrue(c.direct)
        self.assertFalse(c._session.trust_env)

    def test_remote_client_honors_env_proxy(self):
        c = comfyui.ComfyUIClient("https://comfy.example.com")
        self.assertFalse(c.direct)
        self.assertTrue(c._session.trust_env)


if __name__ == "__main__":
    unittest.main()
