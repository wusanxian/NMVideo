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
import hashlib
import json
import os
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import OUTPUTS_DIR
from .managed_paths import IMAGE_SUFFIXES, resolve_managed_asset

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


class ComfyUICancelled(ComfyUIError):
    """等待被用户取消：引擎层应转换为 GenerationCancelled 走取消路径
    （而不是失败路径——取消的 job 标 cancelled、镜头回 draft）。"""


class ComfyUIClient:
    def __init__(self, base_url: str = None):
        self.base_url = (base_url or _default_base_url()).rstrip("/")

    @staticmethod
    def new_client_id(prefix: str = "aivideo") -> str:
        """提交与 WS 订阅共用的 client_id。

        ComfyUI 的 WS 只把任务的进度推送给提交时携带同一 client_id 的连接——
        两边不一致就一条进度都收不到（2B 修复：曾用默认 "aivideo" 提交、
        却按 prompt_id 派生 id 订阅）。
        """
        return f"{prefix}-{uuid.uuid4().hex[:8]}"

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
        """请求 ComfyUI 全局中断当前执行。

        注意（HF-06）：这会中断 ComfyUI 上**所有**客户端正在执行的任务，
        共享服务器时误伤风险极高。任务取消路径（_wait_ws/_wait_poll）已
        不再调用它；仅保留给管理用途，默认不应触发。
        """
        try:
            requests.post(f"{self.base_url}/interrupt", timeout=5)
        except Exception:
            pass

    # ---------- 输入上传（2C：i2v/首尾帧参考图） ----------
    def upload_image(self, path, name: str = "") -> dict:
        """上传本地图片到 ComfyUI input 目录。

        返回 {"name": ..., "subfolder": ..., "type": "input"}；
        工作流里引用时用 "subfolder/name"（LoadImage 的 image 输入约定）——
        ComfyUI 只能读自己 input 目录内的文件，本机路径对它没有意义。

        name 为空时自动加 12 位 uuid 前缀（PR-02 素材事务：不同任务上传
        同名文件——如各镜头都导出 frame_0001.png——共享一个 ComfyUI input
        目录 + overwrite=true 会互相覆盖，导致工作流读到别人的图）。
        显式传 name 时不改写（调用方自担唯一性）。
        """
        path = Path(path)
        upload_name = name or f"{uuid.uuid4().hex[:12]}_{path.name}"
        with open(path, "rb") as f:
            files = {"image": (upload_name, f, "application/octet-stream")}
            r = requests.post(
                f"{self.base_url}/upload/image",
                files=files, data={"overwrite": "true"}, timeout=120,
            )
        if r.status_code != 200:
            raise ComfyUIError(f"上传图片失败 {r.status_code}: {r.text[:300]}")
        data = r.json()
        if not data.get("name"):
            raise ComfyUIError(f"上传响应缺少 name: {str(data)[:200]}")
        return data

    # ---------- 进度 / 等待 ----------
    def wait_for_completion(
        self,
        prompt_id: str,
        on_progress: Optional[Callable[[float, str], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        timeout_s: float = 3600,
        client_id: str = "",
    ) -> dict:
        """阻塞直到任务完成，返回 history 条目。

        client_id：提交时使用的 client_id（必须一致才能收到 WS 推送）。
        为空表示 resume 场景——原 client_id 已随上次进程丢失，新 id 订阅不到
        旧任务的推送，直接走历史轮询（2B）。
        WS 不可用/中断时自动降级为轮询兜底。
        """
        deadline = time.time() + timeout_s
        if client_id:
            try:
                return self._wait_ws(prompt_id, client_id, on_progress, is_cancelled, deadline)
            except ComfyUICancelled:
                raise
            except ComfyUIError:
                if is_cancelled and is_cancelled():
                    raise
                if time.time() >= deadline:
                    raise
                return self._wait_poll(prompt_id, on_progress, is_cancelled, deadline)
        return self._wait_poll(prompt_id, on_progress, is_cancelled, deadline)

    def _completed_entry(self, prompt_id: str) -> Optional[dict]:
        """history 里该任务的已完成条目；未完成/不存在返回 None。"""
        try:
            hist = self.get_history(prompt_id)
        except Exception:
            return None
        entry = (hist or {}).get(prompt_id)
        if not entry:
            return None
        status = entry.get("status") or {}
        if status.get("completed") or status.get("status_str") == "success":
            return entry
        return None

    def _wait_ws(self, prompt_id, client_id, on_progress, is_cancelled, deadline) -> dict:
        try:
            import websockets.sync.client  # v14+ 必须显式导入子模块，否则 websockets.sync 不存在
        except ImportError:
            raise ComfyUIError("websockets 未安装，WS 进度不可用（降级轮询）")

        stage = "提交中"
        last_hist_check = 0.0
        try:
            ws = websockets.sync.client.connect(
                _ws_url(self.base_url, client_id), open_timeout=10, close_timeout=5
            )
        except Exception as e:
            # 连不上（ComfyUI 没开/WS 不可用）：转 ComfyUIError 让上层降级轮询
            raise ComfyUIError(f"WS 连接失败: {e}")
        with ws:
            while True:
                if is_cancelled and is_cancelled():
                    # HF-06：/interrupt 是 ComfyUI **全局**中断——共享服务器
                    # 时会误伤其他客户端正在跑的任务。本服务不默认全局打断：
                    # 本地标记取消即可，远端任务可能仍在执行（其产物由
                    # ComfyUI 侧自行清理，或用户在 ComfyUI 界面手动处理）。
                    raise ComfyUICancelled("已取消（远端任务可能仍在执行）")
                if time.time() > deadline:
                    raise ComfyUIError("等待超时")
                # 完成检测不依赖消息：任务可能在 WS 连接建立前就已完成、或推送丢失。
                # 静默期（无消息）定期查历史兜底（2B）
                now = time.time()
                if now - last_hist_check >= 5:
                    last_hist_check = now
                    entry = self._completed_entry(prompt_id)
                    if entry:
                        return entry
                try:
                    raw = ws.recv(timeout=5)
                except TimeoutError:
                    # websockets 17：recv(timeout) 超时抛内置 TimeoutError
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
                    if not node:
                        # 执行收尾：立刻查一次历史确认完成
                        entry = self._completed_entry(prompt_id)
                        if entry:
                            return entry
                elif mtype == "executed":
                    if on_progress:
                        on_progress(0.97, "节点完成")
                elif mtype in ("execution_error", "execution_interrupted"):
                    raise ComfyUIError(f"ComfyUI 执行失败: {str(data)[:400]}")

    def _wait_poll(self, prompt_id, on_progress, is_cancelled, deadline) -> dict:
        waited = 0.0
        while time.time() <= deadline:
            if is_cancelled and is_cancelled():
                # HF-06：同 _wait_ws——不调全局 /interrupt，避免误伤共享
                # ComfyUI 上其他客户端的任务；仅本地标记取消
                raise ComfyUICancelled("已取消（远端任务可能仍在执行）")
            entry = self._completed_entry(prompt_id)
            if entry:
                return entry
            # 失败检测：history 有条目但状态是 error
            try:
                hist = self.get_history(prompt_id)
                raw = (hist or {}).get(prompt_id)
                if raw and (raw.get("status") or {}).get("status_str") == "error":
                    raise ComfyUIError(f"ComfyUI 执行失败: {str(raw.get('status'))[:400]}")
            except ComfyUIError:
                raise
            except Exception:
                pass
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
        # 与 get_history 一致显式查状态码（不依赖 raise_for_status）：
        # 非 200 抛 ComfyUIError，probe 据此判 temporarily_unavailable
        # （HF-04：服务端错误 ≠ 任务不存在）
        r = requests.get(f"{self.base_url}/queue", timeout=8)
        if r.status_code != 200:
            raise ComfyUIError(f"queue 接口状态码 {r.status_code}: {r.text[:200]}")
        return r.json()

    def cancel_prompt(self, prompt_id: str) -> str:
        """尽力停掉**本任务**（不是全局中断）。返回处理方式，仅供日志。

        - 排队中：`POST /queue {"delete": [prompt_id]}` 精确删除，无副作用；
        - 运行中：先确认 queue_running 里跑的确实是本任务，才 `POST /interrupt`。
          `/interrupt` 本身是全局的（HF-06：共享服务器会误伤别人），所以这里
          加了「先核实再打断」的前置条件，并且**只给看门狗的资源告急路径用**
          ——整机已经在换页假死时，留一个 20GB 的任务继续跑是最坏的选择。
        - 查不到：什么都不做（可能刚好跑完，产物照常归档）。
        """
        try:
            q = self.get_queue()
        except Exception:
            q = {}
        running = [it for it in (q.get("queue_running") or [])
                   if it and len(it) > 1 and it[1] == prompt_id]
        pending = [it for it in (q.get("queue_pending") or [])
                   if it and len(it) > 1 and it[1] == prompt_id]
        try:
            if pending:
                requests.post(
                    f"{self.base_url}/queue",
                    json={"delete": [prompt_id]}, timeout=5,
                )
                return "deleted"
            if running:
                requests.post(f"{self.base_url}/interrupt", timeout=5)
                return "interrupted"
        except Exception as e:  # noqa: BLE001 - 停不掉也不能挡住中止流程
            return f"error: {e}"
        return "not_found"

    def object_info(self, node_names: Optional[list] = None) -> dict:
        """GET /object_info——ComfyUI 实际可用节点类型（PR-03 能力探测）。

        node_names 提供时本地过滤只返回这些键（服务端无按名过滤参数）。
        不可达 / 非 200 / 响应非 JSON → 抛 ComfyUIError，由调用方决定
        是「能力查询如实报告不可探测」还是「生成前阻断」。
        """
        try:
            r = requests.get(f"{self.base_url}/object_info", timeout=10)
        except requests.RequestException as e:
            raise ComfyUIError(f"object_info 请求失败: {e}") from e
        if r.status_code != 200:
            raise ComfyUIError(f"object_info 接口状态码 {r.status_code}")
        try:
            data = r.json()
        except ValueError as e:
            raise ComfyUIError(f"object_info 响应不是 JSON: {e}") from e
        if not isinstance(data, dict):
            raise ComfyUIError("object_info 响应不是节点字典")
        if node_names:
            return {k: data[k] for k in node_names if k in data}
        return data

    def probe(self, prompt_id: str) -> str:
        """查询任务在 ComfyUI 侧的状态。

        done / running / queued / error / lost / temporarily_unavailable。

        HF-04：网络异常（连不上/超时/5xx）返回 temporarily_unavailable——
        ComfyUI 临时离线不代表任务已不存在，恢复侧据此进入"待核查"，
        不重新提交、不过早确认失败；只有 history 与队列都明确应答
        "没有这个任务"才判 lost。
        """
        try:
            if self._completed_entry(prompt_id):
                return "done"
            hist = self.get_history(prompt_id)
            raw = (hist or {}).get(prompt_id)
            if raw and (raw.get("status") or {}).get("status_str") == "error":
                return "error"
        except Exception:
            return "temporarily_unavailable"
        try:
            q = self.get_queue()
        except Exception:
            return "temporarily_unavailable"
        for item in (q.get("queue_running") or []):
            if item and len(item) > 1 and item[1] == prompt_id:
                return "running"
        for item in (q.get("queue_pending") or []):
            if item and len(item) > 1 and item[1] == prompt_id:
                return "queued"
        return "lost"

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


def load_preset_template(preset_id: str) -> dict:
    """只读加载内置预设（不改动当前模板 default.json）。

    与 apply_preset 的区别：apply 会覆盖全局当前模板，本函数仅按需读取——
    供引擎按 ``EngineMeta.workflow_presets`` 声明式路由（见 resolve_template_for）。
    """
    path = PRESETS_DIR / f"{preset_id}.json"
    if not path.exists():
        raise ComfyUIError(f"预设不存在: {preset_id}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise ComfyUIError(f"预设读取失败: {e}")
    if not isinstance(data.get("prompt"), dict) or not data["prompt"]:
        raise ComfyUIError(f"预设缺少 prompt 节点图: {preset_id}")
    return data


# 内置预设不再按引擎 id 硬编码路由：引擎用 EngineMeta.workflow_presets 声明
# 自己在各模式下用哪张内置工作流图（见 engines/base.py）。FreeVideo 的
# freevideo_h3_* 只是「可选工作流之一」——用户在「ComfyUI 设置」里 apply 即可，
# 与引擎身份解耦：装了 FreeVideo 插件的人能用，没装的人缺节点时会被
# workflow_capabilities 在提交前如实阻断。


def resolve_template_for(engine_id: str, mode: str = "t2v") -> dict:
    """按引擎声明的 workflow_presets 解析 ComfyUI 工作流模板。

    - 引擎声明了 ``workflow_presets``：按模式取内置预设（只读加载，不覆盖
      用户当前模板），与「当前模板」解耦——用户在 ComfyUI 设置里 apply 了
      别的图也不影响该引擎的契约。
    - 未声明（默认 ComfyUI 引擎就是这种情况）：沿用用户当前模板，历史行为不变。

    延迟导入 ENGINES 以避免 comfyui <-> engines 的循环依赖。
    """
    from .engines import ENGINES

    meta = getattr(ENGINES.get(engine_id or ""), "meta", None)
    presets = getattr(meta, "workflow_presets", None) or {}
    preset_id = presets.get(mode or "t2v") or presets.get("t2v")
    if preset_id:
        return load_preset_template(preset_id)
    return load_workflow_template()


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


def local_path_from_output_url(url: str) -> Optional[Path]:
    """/outputs/... URL → 本机文件路径（2C：参考图上传 ComfyUI 前定位）。

    安全解析（P0-03）：只接受受管 /outputs/ 前缀，规范化后必须仍在
    OUTPUTS_DIR 内（拒绝 ../、绝对路径/盘符、反斜杠、% 编码穿越、
    符号链接逃逸），且扩展名须在图片白名单内；否则返回 None
    （外链图片本机没有副本，也无法上传）。

    base_dir 显式传本模块的 OUTPUTS_DIR：历史上该函数就用 comfyui
    模块级绑定（测试 monkeypatch app.comfyui.OUTPUTS_DIR 隔离）。
    """
    return resolve_managed_asset(url, allowed_types=IMAGE_SUFFIXES,
                                 base_dir=OUTPUTS_DIR)


def upload_reference_image(client: "ComfyUIClient", url: str) -> str:
    """参考图 URL → ComfyUI input 目录里的引用名（2C）。

    上传失败/文件缺失抛 ComfyUIError（调用方决定接口层 400 还是任务失败）——
    工作流确实需要参考图时，静默丢失比明确失败更糟。
    """
    p = local_path_from_output_url(url)
    if not p or not p.is_file():
        raise ComfyUIError(f"参考图文件不存在或非受管路径: {url}")
    data = client.upload_image(p)
    sub = (data.get("subfolder") or "").strip("/")
    name = data.get("name") or ""
    return f"{sub}/{name}" if sub else name


def build_shot_workflow(params: dict, template: dict = None) -> dict:
    """按当前模板把镜头参数注入，返回可直接提交的节点图。

    params 支持 prompt/negative_prompt/width/height/num_frames/seed/steps/
    guidance/reference_image（模板 _aivideo_map 里映射了才注入；
    reference_image 应为 ComfyUI input 目录内的引用名，见
    upload_reference_image）。

    入队时（router）与执行时（引擎兜底）共用：入队时构建并落快照，
    执行时优先用快照——排队期间用户改模板不影响已排队任务（2B）。
    template 可传入已加载的模板，避免重复读盘（router 需要先看
    _aivideo_map 决定是否上传参考图）。
    """
    if template is None:
        template = load_workflow_template()
    return build_workflow(template, template.get("_aivideo_map", {}), params)


# ---------------------------------------------------------------------
# PR-06：工作流快照 schema——入队即固化的不可变存档
# 快照写入方（router 入队 / 引擎兜底补写）统一走 build_snapshot_payload，
# 读取方（引擎执行）统一走 verify_snapshot_payload：
#   - 写入带 schema_version + 节点图规范化 JSON 的 sha256
#   - 读取校验 schema 版本与内容指纹；不符/损坏 → ComfyUIError 需人工
#     处理，绝不静默改用当前模板（同一镜头两次生成必须同一工作流）
#   - 旧快照无 schema_version/sha256 仍放行（兼容历史数据，校验降级为
#     结构检查——用的仍是存档本身，不存在"替换"）
# ---------------------------------------------------------------------
SNAPSHOT_SCHEMA_VERSION = "1.0"


def _graph_sha256(graph: dict) -> str:
    """节点图规范化 JSON 的 sha256（sort_keys 保证跨进程/跨写入方一致）。"""
    canonical = json.dumps(graph, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_snapshot_payload(graph: dict, job_id: str = "", *, template: dict = None) -> dict:
    """构造带 schema_version + sha256 的快照载荷（PR-06：不可变工作流）。"""
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "job_id": job_id,
        "graph": graph,
        "sha256": _graph_sha256(graph),
    }
    if template is not None:
        payload["template"] = template
        payload["template_sha256"] = _graph_sha256(template)
    return payload


def verify_snapshot_payload(data: dict, path) -> dict:
    """校验快照载荷并返回节点图；任何不一致抛 ComfyUIError（需人工处理）。

    - 载荷非 JSON 对象 / graph 缺失或为空 → 明确失败（HF-05 既有行为）
    - schema_version 存在但不为本版本支持的取值 → 明确失败（不静默降级）
    - sha256 存在但与 graph 实际内容不符 → 明确失败（被篡改/损坏）
    - 旧快照无 schema_version/sha256 → 放行（兼容历史存档）
    """
    if not isinstance(data, dict):
        raise ComfyUIError(
            f"工作流快照不是有效的 JSON 对象: {path}。请人工处理后重试；"
            "系统不会静默改用当前模板"
        )
    graph = data.get("graph") or data.get("prompt")
    if not isinstance(graph, dict) or not graph:
        raise ComfyUIError(
            f"工作流快照缺少有效节点图: {path}。请人工处理后重试；"
            "系统不会静默改用当前模板"
        )
    schema_version = data.get("schema_version")
    if schema_version is not None and str(schema_version) != SNAPSHOT_SCHEMA_VERSION:
        raise ComfyUIError(
            f"工作流快照 schema 版本不受支持: {path}（快照 "
            f"{schema_version} ≠ 当前 {SNAPSHOT_SCHEMA_VERSION}）。"
            "请人工处理后重试；系统不会静默改用当前模板"
        )
    digest = data.get("sha256")
    if digest:
        actual = _graph_sha256(graph)
        if actual != digest:
            raise ComfyUIError(
                f"工作流快照 sha256 校验失败: {path}（内容被修改或损坏，"
                f"记录值 {str(digest)[:12]}… ≠ 实际 {actual[:12]}…）。"
                "为保证输出可复现，请人工处理后重试；系统不会静默改用当前模板"
            )
    if "template" in data and data.get("template_sha256") != _graph_sha256(data["template"]):
        raise ComfyUIError(f"工作流模板快照校验失败: {path}，请人工处理后重试")
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
                    # ComfyUI V3 PreviewVideo（FreeVideo 使用）把 MP4 放在 images 字段。
                    # 按真实扩展名分类，不能把原生视频当成 PNG 帧序列处理。
                    suffix = Path(f["filename"]).suffix.lower()
                    actual_kind = ("videos" if suffix in {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi"}
                                   else "gifs" if suffix == ".gif" else kind)
                    files.append(
                        {
                            "filename": f["filename"],
                            "subfolder": f.get("subfolder", ""),
                            "type": f.get("type", "output"),
                            "kind": actual_kind,
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
