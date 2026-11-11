"""架构口径回归测试（本地保留，backend/tests 已在 .gitignore）。

覆盖：ComfyUI 为唯一默认通道、兜底通道标记、模板路由与 FreeVideo 预设解耦、
生成入口只为 ComfyUI 通道固化工作流快照。
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines import (  # noqa: E402
    DEFAULT_ENGINE_ID,
    ENGINES,
    EXECUTION_COMFY,
    EXECUTION_NATIVE,
    is_comfy_engine,
)


def test_default_engine_is_comfyui():
    assert DEFAULT_ENGINE_ID == "comfyui"
    assert list(ENGINES)[0] == "comfyui", "ComfyUI 通道必须排在注册表首位"


def test_channel_contract():
    assert is_comfy_engine("comfyui") is True
    # h3 归位 ComfyUI 通道（按模式套 freevideo_h3_* 预设），原生实现改名 h3_native
    assert is_comfy_engine("h3") is True
    for fallback in ("h3_native", "anime", "wan22"):
        assert is_comfy_engine(fallback) is False, f"{fallback} 应属兜底通道"
    # 未知引擎 / 空值不能抛异常——重启恢复要能安全处理已下线引擎的历史任务
    assert is_comfy_engine("") is False
    assert is_comfy_engine("no-such-engine") is False


def test_meta_has_execution_channel():
    for engine_id, cls in ENGINES.items():
        assert cls.meta.execution_channel in (EXECUTION_COMFY, EXECUTION_NATIVE)
    assert ENGINES["comfyui"].meta.execution_channel == EXECUTION_COMFY
    assert ENGINES["h3"].meta.execution_channel == EXECUTION_COMFY
    for fallback in ("h3_native", "anime", "wan22"):
        assert ENGINES[fallback].meta.execution_channel == EXECUTION_NATIVE


def test_list_engines_exposes_channel(monkeypatch):
    # 屏蔽真实探测（ComfyUI 连通性 / 显卡），只验证契约字段
    for cls in ENGINES.values():
        monkeypatch.setattr(
            cls, "describe", lambda self: {"id": self.meta.id, "status": "ready"},
            raising=False,
        )
    from app.engines import list_engines

    items = list_engines()
    assert [i["id"] for i in items][0] == "comfyui"
    defaults = [i for i in items if i["is_default"]]
    assert len(defaults) == 1 and defaults[0]["id"] == "comfyui"
    for item in items:
        assert item["execution_channel"] in (EXECUTION_COMFY, EXECUTION_NATIVE)
        assert isinstance(item["supported_modes"], list)


def test_router_default_engine():
    from app.routers.jobs import JobCreate
    from app.routers.novel_submit import SubmitCreate
    from app.routers.storyboard import ShotGenerate

    assert JobCreate(prompt="x").engine == DEFAULT_ENGINE_ID
    assert ShotGenerate().engine == DEFAULT_ENGINE_ID
    assert SubmitCreate().engine == DEFAULT_ENGINE_ID


def test_template_routing_decoupled_from_engine_id(monkeypatch):
    """FreeVideo 预设不再按引擎 id 硬编码：未声明 presets 的引擎用当前模板。"""
    from app import comfyui

    sentinel = {"prompt": {"1": {"class_type": "Fake", "inputs": {}}}}
    monkeypatch.setattr(comfyui, "load_workflow_template", lambda: sentinel)
    monkeypatch.setattr(
        comfyui, "load_preset_template",
        lambda pid: {"prompt": {"9": {"class_type": "Preset"}}, "preset": pid},
    )
    assert comfyui.resolve_template_for("comfyui", "t2v") is sentinel
    # 兜底通道没有声明 presets：沿用用户当前模板
    assert comfyui.resolve_template_for("h3_native", "t2v") is sentinel


def test_engine_declared_presets_route(monkeypatch):
    from app import comfyui
    from app.engines import base

    fake_meta = base.EngineMeta(
        id="fake-comfy", name="fake", description="fake",
        execution_channel="comfyui",
        workflow_presets={"t2v": "freevideo_h3_t2v", "i2v": "freevideo_h3_i2v"},
    )
    FakeEngine = type("FakeEngine", (), {"meta": fake_meta})
    monkeypatch.setitem(ENGINES, "fake-comfy", FakeEngine)
    monkeypatch.setattr(
        comfyui, "load_preset_template", lambda pid: {"prompt": {}, "preset": pid},
    )
    assert comfyui.resolve_template_for("fake-comfy", "i2v")["preset"] == "freevideo_h3_i2v"
    assert comfyui.resolve_template_for("fake-comfy", "first_last")["preset"] == "freevideo_h3_t2v"


def test_create_job_prepares_snapshot_only_for_comfy(monkeypatch):
    """入队前的快照固化只对 ComfyUI 通道触发（兜底通道不产生 ComfyUI 任务）。

    资源守卫关掉：这里验的是「谁该固化快照」，不是本机能不能跑 h3_native
    ——后者在 32GB 机器上必然 409，会让用例随机器配置飘。
    """
    monkeypatch.setenv("AIVIDEO_RESOURCE_GUARD", "off")
    from app.routers import jobs as jobs_router

    calls = []
    monkeypatch.setattr(
        jobs_router, "prepare_comfy_workflow",
        lambda params, output_dir=None, **kw: (calls.append(params["engine"]), ({"1": {}}, {"prompt": {}}))[1],
    )
    monkeypatch.setattr(jobs_router, "persist_prepared_job", lambda *a, **k: {"id": "job-1"})

    async def fake_enqueue(job_id):
        return None

    monkeypatch.setattr(jobs_router.job_queue, "enqueue", fake_enqueue)

    asyncio.run(jobs_router.create_job(jobs_router.JobCreate(engine="comfyui", prompt="x")))
    assert calls == ["comfyui"]
    asyncio.run(jobs_router.create_job(jobs_router.JobCreate(engine="h3", prompt="x")))
    assert calls == ["comfyui", "h3"], "h3 已属 ComfyUI 通道，必须固化快照"
    asyncio.run(jobs_router.create_job(jobs_router.JobCreate(engine="h3_native", prompt="x")))
    assert calls == ["comfyui", "h3"], "兜底引擎不应触发 ComfyUI 快照准备"


def test_job_metadata_uses_channel():
    from app import queue_manager

    class Profile:
        def to_dict(self):
            return {"gpu_name": "RTX", "quant": "fp8", "offload": "none"}

    class Engine:
        _profile = Profile()

    base_job = {
        "engine": "comfyui", "prompt": "p", "negative_prompt": "", "width": 512,
        "height": 512, "num_frames": 16, "fps": 8, "steps": 25, "guidance": 7.5,
        "seed": 1, "preset": "", "preview": False, "preview_kind": "rerun",
        "parent_job_id": "", "generation_inputs": "[]", "characters_snapshot": "[]",
    }
    comfy = queue_manager.JobQueue._job_metadata(base_job, Engine())
    assert comfy["hardware"] == "ComfyUI 外部执行，资源由服务端管理"
    native = queue_manager.JobQueue._job_metadata({**base_job, "engine": "h3_native"}, Engine())
    assert native["hardware"] == "RTX | fp8 | none"


def test_no_hardcoded_comfy_comparison():
    """架构护栏：判断通道一律走 is_comfy_engine，不要再写 engine == "comfyui"。"""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders = [
        str(p.relative_to(app_dir))
        for p in app_dir.rglob("*.py")
        if '== "comfyui"' in p.read_text(encoding="utf-8")
        or "== 'comfyui'" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"发现硬编码引擎判断：{offenders}"


# ---------------- 第二批：H3 归位 ComfyUI 通道 ----------------
def test_h3_declares_comfy_presets_that_exist():
    """h3 走 ComfyUI 通道：四种模式都要有内置预设，且文件真实存在。"""
    from app import comfyui

    meta = ENGINES["h3"].meta
    assert meta.execution_channel == EXECUTION_COMFY
    assert meta.workflow_presets == {
        "t2v": "nmvideo_h3_t2v",
        "i2v": "nmvideo_h3_i2v",
        "continuation": "nmvideo_h3_i2v",
        "first_last": "nmvideo_h3_first_last",
    }
    for preset_id in set(meta.workflow_presets.values()):
        assert (comfyui.PRESETS_DIR / f"{preset_id}.json").exists(), f"缺预设 {preset_id}"
    # 四种模式都有落点，不会掉回「当前模板」
    for mode in ("t2v", "i2v", "continuation", "first_last"):
        assert comfyui.resolve_template_for("h3", mode)["_meta"]["model_id"] == "vdn-minimax-h3"


def test_h3_comfy_engine_flags_missing_freevideo_node(monkeypatch):
    """缺 FreeVideo 节点 → 引擎如实不可用 + 给安装指引（不是悄悄换底座）。"""
    from app.engines.h3_comfy import _NODE_CACHE, H3ComfyEngine

    _NODE_CACHE.clear()
    eng = H3ComfyEngine()
    monkeypatch.setattr(H3ComfyEngine, "is_available_cached", lambda self, ttl=10.0: True)
    monkeypatch.setattr(eng.client, "object_info", lambda node_names=None: {})

    d = eng.describe()
    assert d["status"] == "unavailable"
    # 节点类名保留 FreeVideoGenerate：那是 ComfyUI 侧真实注册的名字（外部契约），
    # 品牌改名只动我们自己的预设 id 与文案
    assert "FreeVideoGenerate" in d["note"]
    assert "H3 插件节点" in d["note"]


def test_h3_comfy_engine_ready_when_node_present(monkeypatch):
    from app.engines.h3_comfy import _NODE_CACHE, H3ComfyEngine

    _NODE_CACHE.clear()
    eng = H3ComfyEngine()
    monkeypatch.setattr(H3ComfyEngine, "is_available_cached", lambda self, ttl=10.0: True)
    monkeypatch.setattr(eng.client, "object_info", lambda node_names=None: {"FreeVideoGenerate": {}})

    d = eng.describe()
    assert d["status"] == "ready" and not d.get("note")


def test_h3_comfy_engine_survives_probe_failure(monkeypatch):
    """object_info 打不通（版本老旧）：不谎报缺节点，保持可用 + 提示。"""
    from app.engines.h3_comfy import _NODE_CACHE, H3ComfyEngine

    _NODE_CACHE.clear()
    eng = H3ComfyEngine()
    monkeypatch.setattr(H3ComfyEngine, "is_available_cached", lambda self, ttl=10.0: True)

    def boom(node_names=None):
        raise RuntimeError("no /object_info")

    monkeypatch.setattr(eng.client, "object_info", boom)
    d = eng.describe()
    assert d["status"] == "ready"
    assert "探测失败" in d["note"]


def _launcher():
    import importlib.util

    path = Path(__file__).resolve().parents[2] / "packaging" / "launcher.py"
    spec = importlib.util.spec_from_file_location("nmvideo_launcher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_launcher_keeps_h3_only_for_high_memory_boxes():
    """H3 自动分支只在 ≥10GB 显存 + ≥80GB 内存时命中（原生兜底，正路是 ComfyUI）。"""
    mod = _launcher()
    huge = mod.HWInfo(gpus=[("RTX 4090", 24.0)], ram_total_gb=128.0, disk_free_gb=600.0)
    assert mod.choose_plan(huge).model == "h3"


def test_launcher_8gb_branch_does_not_steal_a14b():
    """回归护栏：低显存档绝不能抢走本该更高档的机器。

    0.1.6 曾把「vram>=8 且 ram>=52 且磁盘够」放在 A14B 之前，导致
    24GB/64GB 的机器被截去拿 TI2V-5B，A14B 永远选不到。

    现在 H3 插件通道的门槛（8GB 显存 / 16GB 内存 / 100.3GB 磁盘）比 A14B 还低，
    所以能跑 A14B 的机器一定先命中 H3——这是想要的结果（画质更好、素材还少 27GB）。
    A14B 因此退成 `--model wan-a14b` 手动档，auto 不再推它。
    """
    mod = _launcher()
    big = mod.HWInfo(gpus=[("RTX 4090", 24.0)], ram_total_gb=64.0, disk_free_gb=600.0)
    assert mod.choose_plan(big).model == "h3"
    # 磁盘装不下 H3 时给的是低显存档，且明确写了「权重不自动下载」
    short = mod.HWInfo(gpus=[("RTX 4090", 24.0)], ram_total_gb=64.0, disk_free_gb=50.0)
    plan = mod.choose_plan(short)
    assert plan.model == "wan-ti2v"
    assert "不自动下载" in plan.reason


def test_upgrade_falls_back_to_native_for_legacy_h3_preview(monkeypatch):
    """旧 h3 预览（无工作流快照）升级：引擎降级为 h3_native，而不是报「快照缺失」。

    0.1.6 之前 h3 是原生推理，那批 job 没有 workflow_snapshot。若沿用
    is_comfy_engine(source["engine"]) 判断，会在快照校验处直接 400。

    资源守卫关掉：本用例只验引擎降级分支，而 h3_native 固定要 32GB+ 内存，
    在 32GB 笔记本上会被守卫提前拒绝（那是另一条正确行为，不该混进来）。
    """
    monkeypatch.setenv("AIVIDEO_RESOURCE_GUARD", "off")
    from app import upgrades

    seen = []

    def spy(engine_id):
        seen.append(engine_id)
        return engine_id in ("comfyui", "h3")

    monkeypatch.setattr(upgrades, "is_comfy_engine", spy)

    def fake_prepare(params, preset="", *, output_dir=None):
        raise RuntimeError("stop-here")  # 只验证引擎降级与分支选择，不跑真生成

    monkeypatch.setattr(upgrades, "build_shot_workflow", fake_prepare)

    src = {
        "id": 1, "status": "done", "preview": 1, "engine": "h3",
        "prompt": "x", "width": 832, "height": 480, "num_frames": 33,
        "fps": 24, "steps": 8, "guidance": 1.0, "seed": 7,
        "workflow_snapshot": "", "preset": "draft",
        "generation_target": "{}",
    }
    try:
        upgrades.prepare_upgrade(src, "standard")
    except RuntimeError:
        pass
    except ValueError as exc:
        assert "快照" not in str(exc), f"旧 h3 预览不该卡在快照校验: {exc}"

    # 关键断言：分支判断用的是降级后的引擎，不是 source 里的 h3
    assert seen == ["h3_native"], f"应改用 params 里的引擎判断，实际: {seen}"
