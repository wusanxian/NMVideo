"""LLM 服务：OpenAI 兼容 /chat/completions 客户端

用于 AI 编剧（剧本生成/润色）与分镜提示词优化。
支持任意 OpenAI 兼容端点：DeepSeek / 通义千问 / Kimi / 本地 Ollama(v1) 等。
"""
import json
import urllib.error
import urllib.request
from typing import List, Optional

from . import settings as settings_module


class LLMError(Exception):
    """LLM 调用失败（未配置 / 网络 / 服务端错误）"""


def _cfg() -> dict:
    return settings_module.get_section("llm")


def is_configured() -> bool:
    """base_url + model 齐了就算可用（api_key 本地 Ollama 可空）"""
    c = _cfg()
    return bool(c.get("base_url") and c.get("model"))


def status() -> dict:
    c = _cfg()
    return {
        "configured": is_configured(),
        "base_url": c.get("base_url", ""),
        "model": c.get("model", ""),
        "has_api_key": bool(c.get("api_key")),
    }


def chat(
    messages: List[dict],
    *,
    temperature: float = 0.7,
    max_tokens: int = 2000,
    timeout: float = 120.0,
) -> str:
    """对话补全，返回文本内容。失败抛 LLMError（信息面向用户可直接展示）"""
    c = _cfg()
    if not is_configured():
        raise LLMError(
            "LLM 服务未配置：请到「模型服务」页填写 base_url 和模型名"
            "（支持 DeepSeek / 通义 / Kimi / 本地 Ollama 等 OpenAI 兼容端点）"
        )
    base = c["base_url"].rstrip("/")
    url = f"{base}/chat/completions"
    payload = {
        "model": c["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if c.get("api_key"):
        headers["Authorization"] = f"Bearer {c['api_key']}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "ignore")[:300]
        except Exception:
            pass
        raise LLMError(f"LLM 服务返回 HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise LLMError(f"连不上 LLM 服务（{base}）: {e.reason}")
    except (TimeoutError, OSError) as e:
        raise LLMError(f"LLM 请求超时或网络错误: {e}")

    choices = data.get("choices") or []
    if not choices:
        raise LLMError("LLM 服务未返回内容（choices 为空）")
    content = (choices[0].get("message") or {}).get("content") or ""
    if not content.strip():
        raise LLMError("LLM 返回内容为空")
    return content.strip()


# ---------- 场景化封装 ----------

def write_script(
    premise: str,
    *,
    genre: str = "",
    style: str = "",
    scene_count: int = 2,
    shot_per_scene: int = 2,
) -> str:
    """AI 编剧：按项目剧本格式输出（场景一：/对白/（景别, 运动, 秒数））"""
    system = (
        "你是专业动画编剧。按以下格式输出分镜剧本，不要输出任何解释：\n"
        "每个场景以「场景一：场景名」开头；每个镜头一行画面描述，"
        "可带 (景别, 运动, 秒数) 元信息；对白单独一行「人物：台词」。\n"
        "景别从 远景/全景/中景/近景/特写 选；运动从 固定/推/拉/移/摇 选。"
    )
    user = (
        f"故事梗概：{premise}\n"
        f"类型：{genre or '未指定'}；视觉风格：{style or '未指定'}\n"
        f"请写 {scene_count} 个场景、每场景约 {shot_per_scene} 个镜头。"
    )
    return chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=0.8,
        max_tokens=2000,
    )


def polish_prompt(prompt: str, *, shot_size: str = "", dialogue: str = "") -> str:
    """提示词优化：面向视频模型的英文提示词（主体+场景+运动+光照+风格）"""
    system = (
        "You are a prompt engineer for text-to-video models. "
        "Rewrite the user's shot description into ONE concise English prompt "
        "(subject, scene, camera movement, lighting, style, quality tags). "
        "Output only the prompt itself, no quotes or explanation."
    )
    user = f"分镜描述：{prompt}"
    if shot_size:
        user += f"\n景别：{shot_size}"
    if dialogue:
        user += f"\n对白：{dialogue}"
    return chat(
        [{"role": "system", "content": system},
         {"role": "user", "content": user}],
        temperature=0.4,
        max_tokens=400,
    )


def polish_dialogue(text: str) -> str:
    """字幕/对白润色：更自然的口语，保留原意"""
    return chat(
        [{"role": "system", "content":
          "你是台词编辑。把用户给的台词改得更自然口语化，保留原意和人物感，"
          "只输出润色后的台词本身。"},
         {"role": "user", "content": text}],
        temperature=0.5,
        max_tokens=300,
    )


def test_connection() -> dict:
    """连通性测试：发一条最小请求"""
    try:
        reply = chat(
            [{"role": "user", "content": "ping"}],
            max_tokens=16, timeout=30.0,
        )
        return {"ok": True, "reply": reply[:80]}
    except LLMError as e:
        return {"ok": False, "error": str(e)}
