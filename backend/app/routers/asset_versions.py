"""资产版本层 REST（第十九批）

角色身份 / 服装 / 幕布三类资产共用一套版本语义，接口也保持同构：

    GET    /{池}/{id}/versions              列版本
    POST   /{池}/{id}/versions              落新版本（改了内容 = 新版本）
    POST   /{池}/{id}/versions/{no}/promote 回滚"当前"指针
    GET    /{池}/{id}/usages                谁在引用
    DELETE /{池}/{id}                       被引用 → 409 + 明细

设计约束（与后端数据层一致）：
- **不提供就地覆盖内容的接口**。内容变了只能落新版本——只要允许就地改，
  历史可复现性就守不住。
- 删除被引用的资产一律 **409 + 引用明细**，不静默摘引用方的指针。
- 所有接口只读写新表/新列，不动 `characters.outfit`、`scene.reference_image`
  等旧字段的语义。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import asset_versions as av, assets, outfits, plates, storyboard as sb

router = APIRouter(prefix="/api", tags=["asset-versions"])


# =====================================================================
# 请求体
# =====================================================================
class VersionNote(BaseModel):
    note: str = ""


class OutfitCreate(BaseModel):
    name: str = Field("", max_length=60)
    character_id: str = ""
    # v1 内容（可选，建池时一并落版）
    reference_assets: list = []
    palette: str = ""
    mesh_asset_id: str = ""
    material: str = ""


class OutfitUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=60)
    character_id: Optional[str] = None


class OutfitVersionCreate(BaseModel):
    note: str = ""
    reference_assets: Optional[list] = None
    palette: Optional[str] = None
    mesh_asset_id: Optional[str] = None
    material: Optional[str] = None


class PlateCreate(BaseModel):
    name: str = Field("", max_length=60)
    form: str = "flat"
    image_url: str = ""
    width: int = Field(0, ge=0)
    height: int = Field(0, ge=0)
    sha256: str = ""
    note: str = ""


class PlateUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=60)
    form: Optional[str] = None
    status: Optional[str] = None


class PlateVersionCreate(BaseModel):
    note: str = ""
    image_url: Optional[str] = None
    width: Optional[int] = Field(None, ge=0)
    height: Optional[int] = Field(None, ge=0)
    sha256: Optional[str] = None


class BindBody(BaseModel):
    """通用绑定体：绑到镜头 / 场景。version_no=0 表示跟随该资产的当前版本。"""

    plate_id: str = ""
    version_no: int = 0
    version_id: str = ""


class CharacterVersionCreate(BaseModel):
    note: str = ""


class PromoteBody(BaseModel):
    version_no: int = Field(..., ge=1)


class ShotRefsBody(BaseModel):
    """镜头级版本锁定：{资产id: 版本号}。版本号 0 = 跟随该资产当前版本。"""

    refs: dict = {}


# =====================================================================
# 角色身份版本
# =====================================================================
@router.get("/characters/{character_id}/versions")
def list_character_versions(character_id: str):
    if not assets.get_character(character_id):
        raise HTTPException(404, "角色不存在")
    return assets.list_character_versions(character_id)


@router.post("/characters/{character_id}/versions", status_code=201)
def create_character_version(character_id: str, data: CharacterVersionCreate):
    rec = assets.create_character_version(character_id, note=data.note)
    if not rec:
        raise HTTPException(404, "角色不存在")
    return rec


@router.post("/characters/{character_id}/versions/promote")
def promote_character_version(character_id: str, data: PromoteBody):
    if not assets.get_character(character_id):
        raise HTTPException(404, "角色不存在")
    try:
        return assets.promote_character_version(character_id, data.version_no)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/characters/{character_id}/usages")
def character_usages(character_id: str):
    if not assets.get_character(character_id):
        raise HTTPException(404, "角色不存在")
    return av.list_usages(av.KIND_CHARACTER, character_id)


# =====================================================================
# 服装
# =====================================================================
@router.get("/projects/{project_id}/outfits")
def list_outfits(project_id: str, character_id: Optional[str] = None):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return outfits.list_outfits(project_id, character_id)


@router.post("/projects/{project_id}/outfits", status_code=201)
def create_outfit(project_id: str, data: OutfitCreate):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    if data.character_id and not assets.get_character(data.character_id):
        raise HTTPException(404, f"角色不存在: {data.character_id}")
    return outfits.create_outfit(
        project_id, name=data.name, character_id=data.character_id,
        reference_assets=data.reference_assets, palette=data.palette,
        mesh_asset_id=data.mesh_asset_id, material=data.material,
    )


@router.get("/outfits/{outfit_id}")
def get_outfit(outfit_id: str):
    o = outfits.get_outfit(outfit_id)
    if not o:
        raise HTTPException(404, "服装不存在")
    return o


@router.patch("/outfits/{outfit_id}")
def update_outfit(outfit_id: str, data: OutfitUpdate):
    if not outfits.get_outfit(outfit_id):
        raise HTTPException(404, "服装不存在")
    return outfits.update_outfit(
        outfit_id, **data.model_dump(exclude_unset=True)
    )


@router.delete("/outfits/{outfit_id}")
def delete_outfit(outfit_id: str):
    if not outfits.get_outfit(outfit_id):
        raise HTTPException(404, "服装不存在")
    ok, usages = outfits.delete_outfit(outfit_id)
    if not ok:
        raise HTTPException(409, {"error": "服装被镜头引用，不能删除",
                                  "usages": usages})
    return {"ok": True}


@router.get("/outfits/{outfit_id}/versions")
def list_outfit_versions(outfit_id: str):
    if not outfits.get_outfit(outfit_id):
        raise HTTPException(404, "服装不存在")
    return av.list_versions(av.KIND_OUTFIT, outfit_id)


@router.post("/outfits/{outfit_id}/versions", status_code=201)
def create_outfit_version(outfit_id: str, data: OutfitVersionCreate):
    if not outfits.get_outfit(outfit_id):
        raise HTTPException(404, "服装不存在")
    overrides = {k: v for k, v in data.model_dump().items()
                 if k != "note" and v is not None}
    try:
        return av.create_version(av.KIND_OUTFIT, outfit_id,
                                 note=data.note, **overrides)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/outfits/{outfit_id}/versions/promote")
def promote_outfit_version(outfit_id: str, data: PromoteBody):
    if not outfits.get_outfit(outfit_id):
        raise HTTPException(404, "服装不存在")
    try:
        return av.promote(av.KIND_OUTFIT, outfit_id, data.version_no)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/outfits/{outfit_id}/usages")
def outfit_usages(outfit_id: str):
    if not outfits.get_outfit(outfit_id):
        raise HTTPException(404, "服装不存在")
    return av.list_usages(av.KIND_OUTFIT, outfit_id)


# =====================================================================
# 幕布
# =====================================================================
@router.get("/projects/{project_id}/plates")
def list_plates(project_id: str, status: Optional[str] = None):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    return plates.list_plates(project_id, status)


@router.post("/projects/{project_id}/plates", status_code=201)
def create_plate(project_id: str, data: PlateCreate):
    if not sb.get_project(project_id):
        raise HTTPException(404, "项目不存在")
    try:
        return plates.create_plate(
            project_id, name=data.name, form=data.form,
            image_url=data.image_url, width=data.width, height=data.height,
            sha256=data.sha256, note=data.note,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/plates/{plate_id}")
def get_plate(plate_id: str):
    p = plates.get_plate(plate_id)
    if not p:
        raise HTTPException(404, "幕布不存在")
    return p


@router.patch("/plates/{plate_id}")
def update_plate(plate_id: str, data: PlateUpdate):
    if not plates.get_plate(plate_id):
        raise HTTPException(404, "幕布不存在")
    try:
        return plates.update_plate(
            plate_id, **data.model_dump(exclude_unset=True)
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/plates/{plate_id}")
def delete_plate(plate_id: str):
    if not plates.get_plate(plate_id):
        raise HTTPException(404, "幕布不存在")
    ok, usages = plates.delete_plate(plate_id)
    if not ok:
        raise HTTPException(409, {"error": "幕布被引用，不能删除", "usages": usages})
    return {"ok": True}


@router.get("/plates/{plate_id}/versions")
def list_plate_versions(plate_id: str):
    if not plates.get_plate(plate_id):
        raise HTTPException(404, "幕布不存在")
    return av.list_versions(av.KIND_PLATE, plate_id)


@router.post("/plates/{plate_id}/versions", status_code=201)
def create_plate_version(plate_id: str, data: PlateVersionCreate):
    """换幕布图 = 落新版本。这是改幕布内容的**唯一**入口。"""
    if not plates.get_plate(plate_id):
        raise HTTPException(404, "幕布不存在")
    overrides = {k: v for k, v in data.model_dump().items()
                 if k != "note" and v is not None}
    try:
        return av.create_version(av.KIND_PLATE, plate_id,
                                 note=data.note, **overrides)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/plates/{plate_id}/versions/promote")
def promote_plate_version(plate_id: str, data: PromoteBody):
    if not plates.get_plate(plate_id):
        raise HTTPException(404, "幕布不存在")
    try:
        return av.promote(av.KIND_PLATE, plate_id, data.version_no)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.get("/plates/{plate_id}/usages")
def plate_usages(plate_id: str):
    if not plates.get_plate(plate_id):
        raise HTTPException(404, "幕布不存在")
    return av.list_usages(av.KIND_PLATE, plate_id)


# =====================================================================
# 绑定与收编
# =====================================================================
@router.post("/scenes/{scene_id}/promote-backdrop", status_code=201)
def promote_scene_backdrop(scene_id: str, data: PlateCreate):
    """把场景现有的 `scene.reference_image` 收编成幕布资产并设为该场景默认幕布。

    只读旧字段、只写新表——不改 `scene.reference_image` 的值。
    """
    scene = sb.get_scene(scene_id)
    if not scene:
        raise HTTPException(404, "场景不存在")
    try:
        return plates.promote_scene_backdrop(
            scene_id, name=data.name, form=data.form, sha256=data.sha256)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.put("/scenes/{scene_id}/plate")
def bind_scene_plate(scene_id: str, data: BindBody):
    """设/清场景默认幕布。version_id 为空 = 清除（回落旧 reference_image）。"""
    if not sb.get_scene(scene_id):
        raise HTTPException(404, "场景不存在")
    if data.version_id:
        ver = av.get_version(av.KIND_PLATE, data.version_id)
        if not ver:
            raise HTTPException(404, "幕布版本不存在")
        if data.plate_id and ver.get("plate_id") != data.plate_id:
            raise HTTPException(400, "版本不属于该幕布")
    plates.bind_scene_plate(scene_id, data.version_id)
    return {"ok": True, "plate_version_id": data.version_id}


@router.put("/shots/{shot_id}/plate")
def bind_shot_plate(shot_id: str, data: BindBody):
    """把幕布绑定到镜头。version_no=0 = 跟随该幕布的当前版本。"""
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    if data.plate_id and not plates.get_plate(data.plate_id):
        raise HTTPException(404, "幕布不存在")
    if data.version_no and not av.get_version_by_no(
            av.KIND_PLATE, data.plate_id, data.version_no):
        raise HTTPException(404, "幕布版本不存在")
    plates.bind_shot_plate(shot_id, data.plate_id, data.version_no)
    return {"ok": True, "plate_id": data.plate_id, "plate_version": data.version_no}


@router.put("/shots/{shot_id}/character-versions")
def set_shot_character_versions(shot_id: str, data: ShotRefsBody):
    """锁定镜头引用的角色版本。版本号 0 = 跟随角色的当前版本。"""
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    for cid in data.refs:
        if not assets.get_character(cid):
            raise HTTPException(404, f"角色不存在: {cid}")
    try:
        return {"refs": av.set_shot_refs(shot_id, av.KIND_CHARACTER, data.refs)}
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.put("/shots/{shot_id}/outfit-refs")
def set_shot_outfit_refs(shot_id: str, data: ShotRefsBody):
    """锁定镜头引用的服装版本。版本号 0 = 跟随服装的当前版本。"""
    if not sb.get_shot(shot_id):
        raise HTTPException(404, "分镜不存在")
    for oid in data.refs:
        if not outfits.get_outfit(oid):
            raise HTTPException(404, f"服装不存在: {oid}")
    try:
        return {"refs": av.set_shot_refs(shot_id, av.KIND_OUTFIT, data.refs)}
    except ValueError as e:
        raise HTTPException(404, str(e))


# =====================================================================
# 冻结预览（只读，不写库）
# =====================================================================
@router.get("/shots/{shot_id}/asset-versions")
def preview_shot_asset_versions(shot_id: str):
    """预览"这个镜头现在入队会冻结出什么"——角色/服装/幕布各自解析到哪一版。

    这是给人看黑盒的：不做这一步，用户永远不知道生成到底用了哪一版资产。
    """
    shot = sb.get_shot(shot_id)
    if not shot:
        raise HTTPException(404, "分镜不存在")
    scene = sb.get_scene(shot.get("scene_id") or "") if shot.get("scene_id") else None
    chars = assets.characters_of_shot(shot)
    snap = av.build_freeze_snapshot(shot, scene, chars)
    return {
        "snapshot": snap,
        # 生成链路会把这个 URL 作为幕布基准图入队（工作流映射了才消费）
        "scene_plate": av.plate_image_for_shot(shot, scene),
    }
