"""第二批 REST API：角色资产 / TTS 配音 / 多轨道 / 审核历史 / 质量检查 / 上传"""
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import assets, storyboard as sb, tracks
from ..config import OUTPUTS_DIR
from ..shotcheck import check_design, check_shot, quality_score
from ..tts import list_voices, synthesize, tts_available

router = APIRouter(prefix="/api", tags=["assets"])

UPLOADS_DIR = OUTPUTS_DIR / "uploads"
TTS_DIR = OUTPUTS_DIR / "tts"
ALLOWED_UPLOAD = {".png", ".jpg", ".jpeg", ".webp", ".mp3", ".wav", ".ogg", ".m4a", ".flac"}


# =====================================================================
# 模型
# =====================================================================
class CharacterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    role: str = "配角"
    appearance: str = ""
    outfit: str = ""
    voice_id: str = ""
    voice_name: str = ""
    lora: str = ""
    expressions: list = []
    reference_images: list = []
    notes: str = ""


class CharacterUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=60)
    role: Optional[str] = None
    appearance: Optional[str] = None
    outfit: Optional[str] = None
    voice_id: Optional[str] = None
    voice_name: Optional[str] = None
    lora: Optional[str] = None
    expressions: Optional[list] = None
    reference_images: Optional[list] = None
    notes: Optional[str] = None


class ShotCharacters(BaseModel):
    character_ids: list


class VoiceGenerate(BaseModel):
    text: str = ""
    voice: str = ""
    rate: str = Field("+0%", pattern=r"^[+-]?\d{1,2}%$")
    character_id: str = ""


class AudioTrackCreate(BaseModel):
    kind: str = Field("music", pattern="^(music|sfx)$")
    title: str = ""
    audio_url: str = ""
    start_s: float = Field(0.0, ge=0)
    volume: float = Field(0.8, ge=0.0, le=1.0)


class AudioTrackUpdate(BaseModel):
    title: Optional[str] = None
    start_s: Optional[float] = Field(None, ge=0)
    trim_in_s: Optional[float] = Field(None, ge=0)
    trim_out_s: Optional[float] = Field(None, ge=0)
    volume: Optional[float] = Field(None, ge=0.0, le=1.0)
    position: Optional[int] = None


class SubtitleItem(BaseModel):
    shot_id: str = ""
    text: str = ""
    start_s: float = 0.0
    end_s: float = 0.0


class SubtitlesReplace(BaseModel):
    items: list


class SubtitleUpdate(BaseModel):
    text: Optional[str] = None
    start_s: Optional[float] = Field(None, ge=0)
    end_s: Optional[float] = Field(None, ge=0)
    position: Optional[int] = None


class ReworkBody(BaseModel):
    cascade: bool = False
    comment: str = ""


# =====================================================================
# 上传（角色参考图 / 场景参考图 / 音乐音效素材）
# =====================================================================
@router.post("/uploads")
async def upload_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD:
        raise HTTPException(400, f"不支持的文件类型 {suffix}（允许：图片/音频）")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    import uuid

    dest = UPLOADS_DIR / f"{uuid.uuid4().hex[:10]}{suffix}"
    with dest.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    size_mb = dest.stat().st_size / 1024 / 1024
    if size_mb > 50:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"文件过大（{size_mb:.1f}MB > 50MB）")
    return {"url": f"/outputs/uploads/{dest.name}", "name": file.filename,
            "size": dest.stat().st_size}


# =====================================================================
# 角色资产库
# =====================================================================
@router.get("/projects/{project_id}/characters")
def list_characters(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return assets.list_characters(project_id)


@router.post("/projects/{project_id}/characters", status_code=201)
def create_character(project_id: str, data: CharacterCreate):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return assets.create_character(project_id, **data.model_dump())


@router.get("/characters/{character_id}")
def get_character(character_id: str):
    c = assets.get_character(character_id)
    if not c:
        raise HTTPException(404, "角色不存在")
    return c


@router.patch("/characters/{character_id}")
def update_character(character_id: str, data: CharacterUpdate):
    if not assets.get_character(character_id):
        raise HTTPException(404, "角色不存在")
    return assets.update_character(
        character_id, **{k: v for k, v in data.model_dump(exclude_unset=True).items()}
    )


@router.delete("/characters/{character_id}")
def delete_character(character_id: str):
    if not assets.get_character(character_id):
        raise HTTPException(404, "角色不存在")
    assets.delete_character(character_id)
    return {"ok": True}


@router.put("/shots/{shot_id}/characters")
def set_shot_characters(shot_id: str, data: ShotCharacters):
    """分镜绑定/解绑角色（生成时自动拼入角色外貌服装描述）"""
    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    for cid in data.character_ids:
        if not assets.get_character(cid):
            raise HTTPException(404, f"角色不存在: {cid}")
    return sb.update_shot(shot_id, character_ids=data.character_ids)


# =====================================================================
# TTS 配音
# =====================================================================
@router.get("/tts/voices")
def tts_voices():
    ok, note = tts_available()
    return {"available": ok, "note": note, "voices": list_voices()}


@router.post("/shots/{shot_id}/voice", status_code=201)
def generate_voice(shot_id: str, data: VoiceGenerate):
    """为分镜对白生成配音（edge-tts 主，SAPI5 兜底）"""
    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    ok, note = tts_available()
    if not ok:
        raise HTTPException(400, f"TTS 不可用：{note}")

    text = (data.text or shot.get("dialogue") or "").strip()
    if not text:
        raise HTTPException(400, "对白文本为空：请先填写分镜对白")
    voice = data.voice
    character_name = ""
    if data.character_id:
        c = assets.get_character(data.character_id)
        if c:
            voice = voice or c.get("voice_id") or ""
            character_name = c.get("name") or ""
            # 角色卡没绑音色时用默认女声
            if not voice:
                voice = "zh-CN-XiaoxiaoNeural"
    if not voice:
        voice = "zh-CN-XiaoxiaoNeural"

    import uuid

    TTS_DIR.mkdir(parents=True, exist_ok=True)
    out = TTS_DIR / f"{shot_id}_{uuid.uuid4().hex[:6]}.mp3"
    try:
        info = synthesize(text, voice, out, rate=data.rate)
    except Exception as e:
        out.unlink(missing_ok=True)
        raise HTTPException(400, f"配音生成失败：{e}")

    rec = tracks.create_voice(
        shot_id,
        character_id=data.character_id,
        character_name=character_name,
        voice=voice,
        text=text,
        audio_url=f"/outputs/tts/{out.name}",
        duration_s=info["duration_s"],
        engine=info["engine"],
    )
    return rec


@router.delete("/voices/{voice_id}")
def delete_voice(voice_id: str):
    if not tracks.get_voice(voice_id):
        raise HTTPException(404, "配音不存在")
    tracks.delete_voice(voice_id)
    return {"ok": True}


# =====================================================================
# 音乐 / 音效轨
# =====================================================================
@router.get("/projects/{project_id}/audio-tracks")
def list_audio_tracks(project_id: str, kind: Optional[str] = None):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return tracks.list_audio_tracks(project_id, kind)


@router.post("/projects/{project_id}/audio-tracks", status_code=201)
def create_audio_track(project_id: str, data: AudioTrackCreate):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    if not data.audio_url:
        raise HTTPException(400, "缺少音频文件")
    return tracks.create_audio_track(project_id, **data.model_dump())


@router.patch("/audio-tracks/{track_id}")
def update_audio_track(track_id: str, data: AudioTrackUpdate):
    item = tracks.update_audio_track(
        track_id, **{k: v for k, v in data.model_dump(exclude_unset=True).items()}
    )
    if not item:
        raise HTTPException(404, "音轨不存在")
    return item


@router.delete("/audio-tracks/{track_id}")
def delete_audio_track(track_id: str):
    tracks.delete_audio_track(track_id)
    return {"ok": True}


# =====================================================================
# 字幕轨
# =====================================================================
@router.get("/projects/{project_id}/subtitles")
def list_subtitles(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return tracks.list_subtitles(project_id)


@router.post("/projects/{project_id}/subtitles/auto")
def auto_subtitles(project_id: str):
    """按时间线顺序 + 镜头对白自动生成字幕（覆盖式）"""
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    items = sb.timeline_layout(project_id)
    if not items:
        raise HTTPException(400, "时间线为空：请先同步已通过镜头")
    out = []
    for it in items:
        text = (it.get("dialogue") or "").strip()
        if text:
            out.append({"shot_id": it["shot_id"], "text": text,
                        "start_s": it["start_s"], "end_s": it["end_s"]})
    return tracks.replace_subtitles(project_id, out)


@router.put("/projects/{project_id}/subtitles")
def replace_subtitles(project_id: str, data: SubtitlesReplace):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return tracks.replace_subtitles(project_id, data.items)


@router.patch("/subtitles/{sub_id}")
def update_subtitle(sub_id: str, data: SubtitleUpdate):
    item = tracks.update_subtitle(
        sub_id, **{k: v for k, v in data.model_dump(exclude_unset=True).items()}
    )
    if not item:
        raise HTTPException(404, "字幕不存在")
    return item


@router.delete("/subtitles/{sub_id}")
def delete_subtitle(sub_id: str):
    tracks.delete_subtitle(sub_id)
    return {"ok": True}


@router.get("/projects/{project_id}/subtitles/srt")
def export_srt(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return {"srt": tracks.format_srt(tracks.list_subtitles(project_id))}


# =====================================================================
# 质量检查 / 审核历史 / 返工
# =====================================================================
@router.get("/shots/{shot_id}/quality")
def shot_quality(shot_id: str, stage: str = "video"):
    """质量检查预取。stage=video（默认，兼容旧行为）：成片质检；
    stage=design：设计审核（生成前，只看创作参数）。"""
    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    if stage not in ("video", "design"):
        raise HTTPException(400, f"未知审核阶段: {stage}")
    issues = check_design(shot) if stage == "design" else check_shot(shot)
    return {"issues": issues, "score": quality_score(issues)}


@router.post("/shots/{shot_id}/rework")
def rework_shot(shot_id: str, data: ReworkBody):
    """镜头返工（可级联后续镜头）"""
    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    scene = sb.get_scene(shot["scene_id"])
    if not scene:
        raise HTTPException(404, "场景不存在")
    try:
        result = sb.rework_from(scene["project_id"], shot_id,
                                cascade=data.cascade, comment=data.comment)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return result


@router.get("/projects/{project_id}/history")
def review_history(project_id: str):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return sb.list_review_history(project_id)
