"""ComfyUI 客户端：工作流提交 / 进度查询（WS 主推 + 历史轮询兜底）/ 产物下载

标准 ComfyUI HTTP API：
    POST /prompt            {"prompt": <graph>, "client_id": ...} -> {"prompt_id": ...}
    GET  /history/{id}      -> {prompt_id: {outputs, status}}
    GET  /queue             -> {queue_running, queue_pending}
    GET  /view?filename&subfolder&type  -> 文件字节
    WS   /ws?clientId=...   -> progress / executing / executed / execution_error

工作流模板：backend/comfy_workflows/default.json
    {"prompt": {...nodes...}, "_aivideo_map": {"positive": {"node":"2","input":"text"}, ...}}
引擎按 _aivideo_map 把镜头参数注入节点；用户也可在界面里整 JSON 替换成
自己从 ComfyUI 导出的工作流（含 AnimateDiff / VHS_VideoCombine 等自定义节点）。
"""
import json
import os
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Callable, Optional

import requests

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "comfy_workflows"
DEFAULT_WORKFLOW = WORKFLOWS_DIR / "default.json"
PRESETS_DIR = WORKFLOWS_DIR / "presets"


def _default_base_url() -> str:
    """ComfyUI 地址：settings.json 优先，环境变量兜底"""
    try:
        from . import settings as _settings

        url = _settings.get_section("comfy_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
    except Exception:
        pass
    return os.environ.get("AIVIDEO_COMFYUI_URL", "http://127.0.0.1:8188")


def _ws_url(base: str, client_id: str) -> str:
    u = base.replace("http://", "ws://").replace("https://", "wss://")
    return f"{u.rstrip('/')}/ws?clientId={urllib.parse.quote(client_id)}"


class ComfyUIError(RuntimeError):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str = None):
        self.base_url = (base_url or _default_base_url()).rstrip("/")

    # ---------- 连接性 ----------
    def is_available(self, timeout: float = 2.5) -> bool:
        try:
            r = requests.get(f"{self.base_url}/system_stats", timeout=timeout)
            return r.status_code == 200
        except Exception:
            return False

    def system_stats(self) -> dict:
        r = requests.get(f"{self.base_url}/system_stats", timeout=5)
        r.raise_for_status()
        return r.json()

    # ---------- 提交 ----------
    def submit(self, workflow: dict, client_id: str = "aivideo") -> str:
        r = requests.post(
            f"{self.base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=15,
        )
        if r.status_code != 200:
            raise ComfyUIError(f"提交失败 {r.status_code}: {r.text[:400]}")
        data = r.json()
        if data.get("node_errors"):
            raise ComfyUIError(f"工作流节点错误: {json.dumps(data['node_errors'], ensure_ascii=False)[:600]}")
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"响应缺少 prompt_id: {str(data)[:300]}")
        return prompt_id

    def interrupt(self) -> None:
        try:
            requests.post(f"{self.base_url}/interrupt", timeout=5)
        except Exception:
            pass

    # ---------- 进度 / 等待 ----------
    def wait_for_completion(
        self,
        prompt_id: str,
        on_progress: Optional[Callable[[float, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        timeout_s: float = 3600,
    ) -> dict:
        """阻塞直到任务完成，返回 history 条目。

        优先 WebSocket 实时进度；WS 不可用则退化为 2s 轮询 /history。
        """
        deadline = time.time() + timeout_s
        try:
            return self._wait_ws(prompt_id, on_progress, is_cancelled, deadline)
        except ComfyUIError:
            if is_cancelled and is_cancelled():
                raise
            if time.time() >= deadline:
                raise
            return self._wait_poll(prompt_id, on_progress, is_cancelled, deadline)

    def _wait_ws(self, prompt_id, on_progress, is_cancelled, deadline) -> dict:
        import websockets  # 延迟导入，未安装时自动走轮询兜底

        client_id = f"aivideo-{prompt_id[:8]}"
        stage = "提交中"
        with websockets.sync.client.connect(
            _ws_url(self.base_url, client_id), open_timeout=10, close_timeout=5
        ) as ws:
            while True:
                if is_cancelled and is_cancelled():
                    self.interrupt()
                    raise ComfyUIError("已取消")
                if time.time() > deadline:
                    raise ComfyUIError("等待超时")
                try:
                    raw = ws.recv(timeout=5)
                except websockets.TimeoutError:
                    continue
                except Exception as e:
                    raise ComfyUIError(f"WS 连接中断: {e}")
                msg = json.loads(raw)
                mtype = msg.get("type")
                data = msg.get("data") or {}
                if mtype == "progress":
                    cur, total = data.get("value", 0), max(data.get("max", 1), 1)
                    if on_progress:
                        on_progress(min(cur / total, 1.0) * 0.9 + 0.05, f"采样 {cur}/{total}")
                elif mtype == "executing":
                    node = data.get("node")
                    stage = f"执行节点 {node}" if node else "完成中"
                    if on_progress:
                        on_progress(0.95, stage)
                elif mtype == "executed":
                    if on_progress:
                        on_progress(0.97, "节点完成")
                elif mtype in ("execution_error", "execution_interrupted"):
                    raise ComfyUIError(f"ComfyUI 执行失败: {str(data)[:400]}")
                # 完成信号：executing 且 node 为空 + status success
                hist = self.get_history(prompt_id)
                if hist:
                    entry = hist.get(prompt_id, {})
                    status = entry.get("status", {})
                    if status.get("completed") or status.get("status_str") == "success":
                        return entry

    def _wait_poll(self, prompt_id, on_progress, is_cancelled, deadline) -> dict:
        waited = 0.0
        while time.time() <= deadline:
            if is_cancelled and is_cancelled():
                self.interrupt()
                raise ComfyUIError("已取消")
            hist = self.get_history(prompt_id)
            entry = hist.get(prompt_id) if hist else None
            if entry:
                status = entry.get("status", {})
                if status.get("completed") or status.get("status_str") == "success":
                    return entry
                if status.get("status_str") == "error":
                    raise ComfyUIError(f"ComfyUI 执行失败: {str(entry.get('status'))[:400]}")
            # 轮询兜底：按已等待时间给出平滑的假进度（WS 不可用时前端不空转）
            waited += 2
            if on_progress:
                on_progress(min(0.05 + waited / 600.0, 0.9), "ComfyUI 生成中（轮询模式）")
            time.sleep(2)
        raise ComfyUIError("等待超时")

    def get_history(self, prompt_id: str) -> dict:
        r = requests.get(f"{self.base_url}/history/{prompt_id}", timeout=10)
        if r.status_code != 200:
            return {}
        return r.json()

    def get_queue(self) -> dict:
        r = requests.get(f"{self.base_url}/queue", timeout=8)
        r.raise_for_status()
        return r.json()

    # ---------- 产物 ----------
    def download(self, filename: str, subfolder: str = "", ftype: str = "output") -> bytes:
        params = {"filename": filename, "subfolder": subfolder or "", "type": ftype}
        r = requests.get(f"{self.base_url}/view", params=params, timeout=120)
        r.raise_for_status()
        return r.content


# =====================================================================
# 工作流模板管理
# =====================================================================
# =====================================================================
# 内置工作流预设（MiniMax H3 / Wan TI2V / AnimateDiff / 默认 SD1.5 序列）
# =====================================================================
def list_presets() -> list:
    """列出 presets/ 目录内置预设 + 默认模板本身"""
    out = [{
        "id": "default",
        "name": "默认 · SD1.5 帧序列",
        "description": "开箱即用：CheckpointLoaderSimple + KSampler + SaveImage（PNG 序列，后端合成 MP4）",
        "requires": "无（vanilla ComfyUI）",
        "builtin": True,
    }]
    if PRESETS_DIR.exists():
        for f in sorted(PRESETS_DIR.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            meta = data.get("_meta", {})
            out.append({
                "id": f.stem,
                "name": meta.get("name", f.stem),
                "description": meta.get("description", ""),
                "requires": meta.get("requires", ""),
                "mode": meta.get("mode", "t2v"),
                "builtin": True,
            })
    return out


def apply_preset(preset_id: str) -> dict:
    """把内置预设写入当前工作流模板（default.json）"""
    if preset_id == "default":
        data = _default_template()
    else:
        path = PRESETS_DIR / f"{preset_id}.json"
        if not path.exists():
            raise ComfyUIError(f"预设不存在: {preset_id}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            raise ComfyUIError(f"预设读取失败: {e}")
    if "prompt" not in data or not isinstance(data["prompt"], dict):
        raise ComfyUIError("预设缺少 prompt 节点图")
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKFLOW.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def load_workflow_template() -> dict:
    if DEFAULT_WORKFLOW.exists():
        try:
            return json.loads(DEFAULT_WORKFLOW.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return _default_template()


def save_workflow_template(data: dict) -> dict:
    if "prompt" not in data or not isinstance(data["prompt"], dict):
        raise ComfyUIError("工作流缺少 prompt 节点图")
    WORKFLOWS_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKFLOW.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return data


def build_workflow(template: dict, mapping: dict, params: dict) -> dict:
    """按 _aivideo_map 把生成参数注入工作流节点（深拷贝，不改模板）"""
    graph = json.loads(json.dumps(template.get("prompt", {})))
    for key, target in (mapping or {}).items():
        if key not in params:
            continue
        node = graph.get(str(target.get("node")))
        if not isinstance(node, dict):
            continue
        inputs = node.setdefault("inputs", {})
        inputs[target.get("input")] = params[key]
    return graph


def extract_output_files(entry: dict) -> list:
    """从 history 条目提取产物文件（兼容 SaveImage / VHS_VideoCombine / AnimateDiff）"""
    files = []
    for node_out in (entry.get("outputs") or {}).values():
        if not isinstance(node_out, dict):
            continue
        for kind in ("videos", "gifs", "images"):
            for f in node_out.get(kind) or []:
                if isinstance(f, dict) and f.get("filename"):
                    files.append(
                        {
                            "filename": f["filename"],
                            "subfolder": f.get("subfolder", ""),
                            "type": f.get("type", "output"),
                            "kind": kind,
                        }
                    )
    return files


def _default_template() -> dict:
    """vanilla ComfyUI 文生图序列工作流（batch_size=帧数，产出 PNG 序列）"""
    return {
        "_comment": "AI视频工坊默认 ComfyUI 工作流：CheckpointLoaderSimple + KSampler + SaveImage（PNG 序列）。"
                    "可在界面中粘贴自己从 ComfyUI 导出的工作流 JSON 替换，保留 _aivideo_map 即可自动注入镜头参数。",
        "prompt": {
            "1": {"class_type": "CheckpointLoaderSimple",
                  "inputs": {"ckpt_name": "v1-5-pruned-emaonly.safetensors"}},
            "2": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "masterpiece, best quality", "clip": ["1", 0]}},
            "3": {"class_type": "CLIPTextEncode",
                  "inputs": {"text": "worst quality, blurry", "clip": ["1", 0]}},
            "4": {"class_type": "EmptyLatentImage",
                  "inputs": {"width": 512, "height": 512, "batch_size": 16}},
            "5": {"class_type": "KSampler",
                  "inputs": {"seed": 0, "steps": 25, "cfg": 7.5,
                             "sampler_name": "euler", "scheduler": "normal",
                             "denoise": 1.0, "model": ["1", 0],
                             "positive": ["2", 0], "negative": ["3", 0],
                             "latent_image": ["4", 0]}},
            "6": {"class_type": "VAEDecode",
                  "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
            "7": {"class_type": "SaveImage", "inputs": {"filename_prefix": "aivideo",
                                                         "images": ["6", 0]}},
        },
        "_aivideo_map": {
            "prompt": {"node": "2", "input": "text"},
            "negative_prompt": {"node": "3", "input": "text"},
            "width": {"node": "4", "input": "width"},
            "height": {"node": "4", "input": "height"},
            "num_frames": {"node": "4", "input": "batch_size"},
            "seed": {"node": "5", "input": "seed"},
            "steps": {"node": "5", "input": "steps"},
            "guidance": {"node": "5", "input": "cfg"},
        },
    }
