"""第三批 E2E：模型服务（LLM / 图像生成 / ComfyUI 预设）

内置 mock OpenAI 兼容服务器，真实走通：
  LLM 配置 → AI 编剧 → 提示词优化 → 图像生成(b64) → 预设应用 → 状态汇总
"""
import base64
import json
import threading
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

BASE = "http://127.0.0.1:8000/api"

# 1x1 红色 PNG
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

MOCK_SCRIPT = (
    "场景一：雨夜街道\n"
    "少年独自走在霓虹灯下的长街，雨水打湿肩头 (全景, 移, 4秒)\n"
    "少年：这天要变了。\n\n"
    "场景二：旧书店\n"
    "橱窗灯光昏黄，少年推开积灰的木门 (远景, 推, 3秒)\n"
    "少女：你终于来了。\n"
)
MOCK_PROMPT = (
    "1boy walking alone on a neon-lit street at night, rain wetting his shoulders, "
    "panoramic shot, camera panning, reflections on wet ground, cinematic lighting, "
    "anime style, masterpiece, best quality"
)


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/chat/completions"):
            content = MOCK_PROMPT if "prompt engineer" in payload["messages"][0]["content"] else MOCK_SCRIPT
            self._json({
                "choices": [{"message": {"role": "assistant", "content": content}}],
            })
        elif self.path.endswith("/images/generations"):
            self._json({"data": [{"b64_json": base64.b64encode(TINY_PNG).decode()}]})
        else:
            self._json({"error": "unknown path"}, 404)

    def do_GET(self):
        if self.path.startswith("/v1/models"):
            self._json({"data": [{"id": "mock-model"}]})
        else:
            self._json({"error": "unknown path"}, 404)


def call(method, path, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def main():
    # 幂等：先清空可能残留的服务配置
    call("PUT", "/services/llm", {"base_url": "", "api_key": "", "model": ""})
    call("PUT", "/services/imagegen", {"base_url": "", "api_key": "", "model": ""})

    mock = HTTPServer(("127.0.0.1", 0), MockHandler)
    port = mock.server_address[1]
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    print(f"[mock] OpenAI-compatible server on 127.0.0.1:{port}")

    # 1) 初始状态：全部未配置
    st, s = call("GET", "/services/status")
    assert st == 200
    assert s["llm"]["configured"] is False, s
    assert s["imagegen"]["configured"] is False, s
    assert "tts" in s and "comfyui" in s
    print("[1] 初始状态 OK：LLM/图像生成未配置，TTS/ComfyUI 状态在位")

    # 2) 未配置时调用 → 400 且信息可操作
    st, r = call("POST", "/llm/script", {"premise": "测试"})
    assert st == 400 and "模型服务" in r["detail"], r
    st, r = call("POST", "/imagegen/generate", {"prompt": "测试"})
    assert st == 400 and "模型服务" in r["detail"], r
    print("[2] 未配置调用返回 400 + 引导信息 OK")

    # 3) 配置 LLM / 图像生成（指向 mock）
    st, _ = call("PUT", "/services/llm", {
        "base_url": f"http://127.0.0.1:{port}/v1", "api_key": "mock-key", "model": "mock-model",
    })
    assert st == 200
    st, _ = call("PUT", "/services/imagegen", {
        "base_url": f"http://127.0.0.1:{port}/v1", "api_key": "mock-key", "model": "mock-image",
    })
    assert st == 200
    st, s = call("GET", "/services/status")
    assert s["llm"]["configured"] and s["imagegen"]["configured"], s
    st, cfg = call("GET", "/services/config")
    assert "****" in cfg["llm"]["api_key"], cfg  # key 打码
    print("[3] 配置保存 + 状态刷新 + key 打码 OK")

    # 4) AI 编剧（真实 HTTP 走 mock）
    st, r = call("POST", "/llm/script", {
        "premise": "少年雨夜遇猫妖", "genre": "奇幻", "style": "日系动画",
        "scene_count": 2, "shot_per_scene": 2,
    })
    assert st == 200 and "场景一" in r["script"] and "场景二" in r["script"], r
    print("[4] AI 编剧生成剧本 OK：", r["script"].splitlines()[0])

    # 5) 提示词优化（英文输出）
    st, r = call("POST", "/llm/polish", {"prompt": "少年雨夜独行", "shot_size": "全景"})
    assert st == 200 and "neon" in r["prompt"], r
    print("[5] 提示词优化 OK：", r["prompt"][:50], "...")

    # 6) LLM 连通测试
    st, r = call("POST", "/services/llm/test")
    assert st == 200 and r["ok"], r
    print("[6] LLM 测试连接 OK")

    # 7) 图像生成（b64 → 落盘 URL）
    st, r = call("POST", "/imagegen/generate", {"prompt": "1boy, reference sheet", "size": "768x1024"})
    assert st == 200 and len(r["urls"]) == 1, r
    url = r["urls"][0]
    assert url.startswith("/outputs/assets/")
    local = Path("outputs") / url[len("/outputs/"):]
    assert local.exists() and local.stat().st_size > 0, local
    print("[7] 图像生成 OK：", url, f"{local.stat().st_size} bytes")
    local.unlink(missing_ok=True)

    # 8) 图像生成测试连接
    st, r = call("POST", "/services/imagegen/test")
    assert st == 200 and r["ok"], r
    (Path("outputs") / r["url"][len("/outputs/"):]).unlink(missing_ok=True)
    print("[8] 图像生成测试连接 OK")

    # 9) ComfyUI 预设：列表 / 应用 / 落盘
    st, r = call("GET", "/comfy/presets")
    ids = [p["id"] for p in r["presets"]]
    assert ids == ["default", "anime_diff", "minimax_h3_i2v", "minimax_h3_t2v", "wan_ti2v"], ids
    st, wf = call("POST", "/comfy/presets/minimax_h3_t2v/apply")
    assert st == 200 and "MiniMaxTextToVideo" in json.dumps(wf["prompt"]), wf
    st, cur = call("GET", "/comfy/workflow")
    assert any(n.get("class_type") == "MiniMaxTextToVideo" for n in cur["prompt"].values()), cur
    st, _ = call("POST", "/comfy/presets/wan_ti2v/apply")
    st, cur = call("GET", "/comfy/workflow")
    assert any(n.get("class_type") == "UNETLoader" for n in cur["prompt"].values()), cur
    assert cur["_aivideo_map"]["num_frames"]["input"] == "length", cur
    st, _ = call("POST", "/comfy/presets/default/apply")
    print("[9] ComfyUI 预设 列表/应用/落盘/映射 OK：", ids)

    # 10) SPA 与静态资源可达
    import urllib.request as _u
    for path in ("/services", "/comfy", "/projects"):
        try:
            with _u.urlopen(f"http://127.0.0.1:8000{path}", timeout=10) as resp:
                assert resp.status == 200, path
        except urllib.error.HTTPError as e:
            raise AssertionError(f"SPA {path} -> {e.code}")
    print("[10] SPA 页面 /services /comfy /projects 均 200")

    # 清理：重置配置与模板
    call("PUT", "/services/llm", {"base_url": "", "api_key": "", "model": ""})
    call("PUT", "/services/imagegen", {"base_url": "", "api_key": "", "model": ""})
    mock.shutdown()
    print("\nALL PASSED ✅")


if __name__ == "__main__":
    main()
