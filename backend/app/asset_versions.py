"""资产版本层：池 + 不可变版本 + 引用方冻结（第十九批）

## 为什么要有这一层

改造前，三类资产都是**就地编辑**：

- 角色卡：`PATCH /characters/{id}` 直接把 `appearance` / `outfit` / `reference_images`
  覆盖掉，旧值不可追回；
- 服装：只存在于 `characters.outfit` 的一个字符串里，没有独立资产；
- 幕布：`scene.reference_image` 一张图，重生成即覆盖。

后果不是"不好看"，而是**不可复现**：一个已通过审核的镜头，只要有人改了角色卡，
它再跑一遍就得到不同结果，而界面上没有任何痕迹说明为什么变了。

本层给三类资产同一套语义：**池 + 不可变版本 + 引用方冻结**。

## 三条硬规则

1. **版本不可变。** 改资产 = 新建版本（`create_version`），永远不 UPDATE 版本行。
2. **`current_version == 0` 表示"未版本化"。** 此时一切回落旧行为（读池行当前值）。
   因此**旧库升级后生成结果与改造前逐字一致**——这是本层能被安全引入的前提。
3. **引用方在入队时冻结。** `build_freeze_snapshot()` 产出本任务实际用到的版本快照，
   随 `jobs.asset_versions_snapshot` 落库；之后改资产不影响本任务的复现语义。

## 版本解析优先级

角色：`shot.character_version_refs[角色id]`  >  `characters.current_version`  >  0（未版本化）
服装：`shot.outfit_refs[服装id]`          >  `outfits.current_version`    >  0
幕布：`shot.plate_version`                >  `scenes.plate_version_id`    >  旧 `scene.reference_image`

## 与既有模块的关系

- 池的字段仍留在原表（`characters` / `outfits` / `plates`），本模块不改旧列语义。
- 角色快照与 PR-04 的 `jobs.characters_snapshot` 同语义、不冲突：
  后者记"生成时 compose_prompt 用的角色数据"，本层记"这些数据来自哪个版本"。
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import db

KIND_CHARACTER = "character"
KIND_OUTFIT = "outfit"
KIND_PLATE = "plate"

# 声明式登记：三类资产的版本表结构不同，但机制完全一致，用一张表驱动，
# 避免三份几乎相同的 CRUD 各自漂移。
_SPECS = {
    KIND_CHARACTER: {
        "pool": "characters",
        "fk": "character_id",
        "versions": "character_versions",
        "id_prefix": "chv",
        # 建版本时从池行快照的列（不可变副本）
        "snapshot_cols": (
            "name", "appearance", "outfit", "lora",
            "expressions", "reference_images", "identity_assets",
        ),
        # 需要 JSON 化的列（读时解码、写时编码）
        "json_cols": ("expressions", "reference_images", "identity_assets"),
        # 池行上有没有 identity_assets 列（老库可能没有，取不到就当空）
        "optional_cols": ("identity_assets",),
        # 引用方所在的列：shots.<col> 是 JSON 映射或标量
        "ref_col": "character_ids",
        "ref_kind": "json_list",
    },
    KIND_OUTFIT: {
        "pool": "outfits",
        "fk": "outfit_id",
        "versions": "outfit_versions",
        "id_prefix": "otv",
        "snapshot_cols": (
            "reference_assets", "palette", "mesh_asset_id", "material",
        ),
        "json_cols": ("reference_assets",),
        "optional_cols": (),
        "ref_col": "outfit_refs",
        "ref_kind": "json_map",
    },
    KIND_PLATE: {
        "pool": "plates",
        "fk": "plate_id",
        "versions": "plate_versions",
        "id_prefix": "plv",
        "snapshot_cols": ("image_url", "width", "height", "sha256"),
        "json_cols": (),
        "optional_cols": (),
        "ref_col": "plate_id",
        "ref_kind": "scalar",
    },
}

SCHEMA_VERSION = 1


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _spec(kind: str) -> dict:
    spec = _SPECS.get(kind)
    if not spec:
        raise ValueError(f"未知资产类型: {kind}（可选：{'/'.join(_SPECS)}）")
    return spec


def _decode(spec: dict, row: Optional[dict]) -> Optional[dict]:
    if not row:
        return row
    for key in spec["json_cols"]:
        try:
            row[key] = json.loads(row.get(key) or "[]")
        except (ValueError, TypeError):
            row[key] = []
    return row


def _json_map(raw) -> dict:
    """把 shots 上的 {id: version} 映射解出来；脏值按空处理，读取不抛异常。"""
    if isinstance(raw, dict):
        return raw
    try:
        out = json.loads(raw or "{}")
        return out if isinstance(out, dict) else {}
    except (ValueError, TypeError):
        return {}


# =====================================================================
# 池
# =====================================================================
def get_pool(kind: str, pool_id: str) -> Optional[dict]:
    spec = _spec(kind)
    return db.fetch_one(
        f"SELECT * FROM {spec['pool']} WHERE id = ?", (pool_id,)
    )


def get_current_version(kind: str, pool_id: str) -> int:
    """池行上的 current_version；0 = 未版本化（回落旧行为）。"""
    row = db.fetch_one(
        f"SELECT current_version FROM {_spec(kind)['pool']} WHERE id = ?",
        (pool_id,),
    )
    try:
        return int((row or {}).get("current_version") or 0)
    except (TypeError, ValueError):
        return 0


# =====================================================================
# 版本
# =====================================================================
def list_versions(kind: str, pool_id: str) -> list:
    """版本列表，版本号倒序（最新在前）。"""
    spec = _spec(kind)
    rows = db.fetch_all(
        f"SELECT * FROM {spec['versions']} WHERE {spec['fk']} = ? "
        "ORDER BY version DESC",
        (pool_id,),
    )
    return [_decode(spec, r) for r in rows]


def get_version(kind: str, version_id: str) -> Optional[dict]:
    spec = _spec(kind)
    return _decode(
        spec,
        db.fetch_one(f"SELECT * FROM {spec['versions']} WHERE id = ?", (version_id,)),
    )


def get_version_by_no(kind: str, pool_id: str, version_no: int) -> Optional[dict]:
    spec = _spec(kind)
    return _decode(
        spec,
        db.fetch_one(
            f"SELECT * FROM {spec['versions']} WHERE {spec['fk']} = ? AND version = ?",
            (pool_id, version_no),
        ),
    )


def _next_version_no(kind: str, pool_id: str) -> int:
    spec = _spec(kind)
    row = db.fetch_one(
        f"SELECT COALESCE(MAX(version), 0) AS m FROM {spec['versions']} "
        f"WHERE {spec['fk']} = ?",
        (pool_id,),
    )
    return int((row or {}).get("m") or 0) + 1


def create_version(kind: str, pool_id: str, note: str = "", **overrides) -> dict:
    """从池行当前值打一个**不可变**版本，并把池的 current_version 指向它。

    overrides 里给出的列覆盖池行取值（用于「改了某些字段再落版本」）；
    note 记录这版为什么建（例如"改了发型"），便于人回溯。

    不允许对同一池重复落内容完全一样的版本吗？——允许。版本号递增是时间线，
    不是去重机制；判断"有没有变"由调用方决定（见 assets.update_character）。
    """
    spec = _spec(kind)
    pool = get_pool(kind, pool_id)
    if not pool:
        raise ValueError(f"{kind} 不存在: {pool_id}")

    row = {}
    for col in spec["snapshot_cols"]:
        if col in overrides:
            row[col] = overrides[col]
        elif col in pool:
            row[col] = pool.get(col)
        elif col in spec["optional_cols"]:
            # 老库缺列（未迁移）——当空值处理，不报错
            row[col] = "[]" if col in spec["json_cols"] else ""
        else:
            row[col] = "[]" if col in spec["json_cols"] else ""
    for col in spec["json_cols"]:
        if not isinstance(row.get(col), str):
            row[col] = json.dumps(row.get(col) or [], ensure_ascii=False)

    version_no = _next_version_no(kind, pool_id)
    version_id = _new_id(spec["id_prefix"])
    cols = ["id", spec["fk"], "version", *spec["snapshot_cols"], "note", "created_at"]
    vals = (version_id, pool_id, version_no,
            *[row.get(c) for c in spec["snapshot_cols"]], note, _utcnow())

    rec = db.insert_and_get(spec["versions"], cols, vals, version_id)
    # 池的当前版本随之前移——"改了就成新版"，与用户直觉一致
    db.execute(
        f"UPDATE {spec['pool']} SET current_version = ? WHERE id = ?",
        (version_no, pool_id),
    )
    return _decode(spec, rec)


def promote(kind: str, pool_id: str, version_no: int) -> dict:
    """把池的 current_version 回指到某个历史版本（回滚）。

    不修改版本内容——版本恒不可变；只移动"当前"指针。
    """
    target = get_version_by_no(kind, pool_id, version_no)
    if not target:
        raise ValueError(f"版本不存在: {kind}/{pool_id} v{version_no}")
    if not get_pool(kind, pool_id):
        raise ValueError(f"{kind} 不存在: {pool_id}")
    db.execute(
        f"UPDATE {_spec(kind)['pool']} SET current_version = ? WHERE id = ?",
        (version_no, pool_id),
    )
    return target


# =====================================================================
# 版本解析（读侧唯一入口）
# =====================================================================
def character_ref_version(shot: dict, character_id: str) -> int:
    """镜头对某角色的锁定版本号；0 = 跟随角色当前版本。"""
    refs = _json_map((shot or {}).get("character_version_refs"))
    return _as_int(refs.get(character_id), 0)


def outfit_ref_version(shot: dict, outfit_id: str) -> int:
    """镜头对某服装的锁定版本号；0 = 跟随服装当前版本。"""
    refs = _json_map((shot or {}).get("outfit_refs"))
    return _as_int(refs.get(outfit_id), 0)


def resolve_version_no(kind: str, pool_id: str, ref: int = 0) -> int:
    """解析实际生效的版本号。

    返回 0 表示**未版本化**——调用方必须回落"读池行当前值"的旧行为。
    """
    try:
        ref = int(ref or 0)
    except (TypeError, ValueError):
        ref = 0
    if ref > 0:
        return ref
    return get_current_version(kind, pool_id)


def resolve_data(kind: str, pool_id: str, ref: int = 0) -> Optional[dict]:
    """解析出该引用实际生效的**数据**（而不是版本号）。

    - 版本号 > 0：读不可变版本行；
    - 版本号 0：读池行当前值（旧行为，兼容未版本化数据）。

    返回 {'version': n, 'data': {...}}；池不存在返回 None。
    """
    pool = get_pool(kind, pool_id)
    if not pool:
        return None
    no = resolve_version_no(kind, pool_id, ref)
    if no > 0:
        ver = get_version_by_no(kind, pool_id, no)
        if ver:
            return {"version": no, "data": ver}
        # 版本号指向一个不存在的版本（数据被手改过）——回落当前值并在数据里
        # 标注，让调用方与界面能看出"这不是我要的那一版"，而不是静默用错版本。
        return {"version": 0, "data": _decode(_spec(kind), pool),
                "missing_version": no}
    return {"version": 0, "data": _decode(_spec(kind), pool)}


# =====================================================================
# 镜头侧版本锁定（写侧入口）
# =====================================================================
_REF_COLUMN = {
    KIND_CHARACTER: "character_version_refs",
    KIND_OUTFIT: "outfit_refs",
}


def set_shot_refs(shot_id: str, kind: str, mapping: dict) -> dict:
    """设置镜头对某类资产的版本锁定映射 `{资产id: 版本号}`。

    版本号 0 = 跟随该资产的 current_version（不锁）。**版本号 > 0 时校验该版本
    真实存在**——否则会写出一个指向空版本的引用，生成时只能静默回落，
    用户却以为锁住了。

    只写新列，不动 `shots.character_ids` 等旧字段。
    """
    col = _REF_COLUMN.get(kind)
    if not col:
        raise ValueError(f"{kind} 不支持镜头级版本锁定")
    clean = {}
    for pool_id, ver in (mapping or {}).items():
        pool_id = str(pool_id)
        n = _as_int(ver, 0)
        if n > 0 and not get_version_by_no(kind, pool_id, n):
            raise ValueError(f"版本不存在: {kind}/{pool_id} v{n}")
        clean[pool_id] = n
    db.execute(
        f"UPDATE shots SET {col} = ? WHERE id = ?",
        (json.dumps(clean, ensure_ascii=False), shot_id),
    )
    return clean


def shot_refs(shot: dict, kind: str) -> dict:
    """读镜头对某类资产的版本锁定映射（脏值按空处理）。"""
    col = _REF_COLUMN.get(kind)
    if not col:
        return {}
    return _json_map((shot or {}).get(col))


# =====================================================================
# 引用与删除保护
# =====================================================================
def list_usages(kind: str, pool_id: str) -> list:
    """列出引用该资产的分镜/场景。返回 [{shot_id, scene_id, kind, detail}]。"""
    spec = _spec(kind)
    out = []
    if kind == KIND_CHARACTER or kind == KIND_OUTFIT:
        for shot in db.fetch_all("SELECT id, scene_id, character_ids, outfit_refs FROM shots"):
            if kind == KIND_CHARACTER:
                try:
                    ids = json.loads(shot.get("character_ids") or "[]")
                except (ValueError, TypeError):
                    ids = []
                if pool_id in (ids or []):
                    out.append({"shot_id": shot["id"], "scene_id": shot.get("scene_id") or "",
                                "kind": kind, "detail": "镜头角色"}
                               )
            else:
                refs = _json_map(shot.get("outfit_refs"))
                if pool_id in refs:
                    out.append({"shot_id": shot["id"], "scene_id": shot.get("scene_id") or "",
                                "kind": kind, "detail": f"镜头服装 v{refs[pool_id]}"})
    elif kind == KIND_PLATE:
        for shot in db.fetch_all("SELECT id, scene_id, plate_id, plate_version FROM shots"):
            if shot.get("plate_id") == pool_id:
                out.append({"shot_id": shot["id"], "scene_id": shot.get("scene_id") or "",
                            "kind": kind,
                            "detail": f"镜头幕布 v{shot.get('plate_version') or 0}"})
        ver_ids = {
            r["id"] for r in db.fetch_all(
                "SELECT id FROM plate_versions WHERE plate_id = ?", (pool_id,))
        }
        if ver_ids:
            placeholders = ",".join("?" for _ in ver_ids)
            for scene in db.fetch_all(
                f"SELECT id, title, plate_version_id FROM scenes "
                f"WHERE plate_version_id IN ({placeholders})",
                tuple(ver_ids),
            ):
                out.append({"shot_id": "", "scene_id": scene["id"], "kind": kind,
                            "detail": f"场景默认幕布（{scene.get('title') or scene['id']}）"})
    return out


def delete_guard(kind: str, pool_id: str) -> tuple:
    """删除前检查：被引用则不许删。

    返回 (ok, usages)。ok=False 时调用方应返回 409 + usages 明细，
    **不得静默改掉引用方的指针**（对齐 3A 数据保护门禁）。
    """
    usages = list_usages(kind, pool_id)
    return (not usages), usages


# =====================================================================
# 入队冻结快照
# =====================================================================
def build_freeze_snapshot(shot: dict, scene: Optional[dict] = None,
                          characters: Optional[list] = None) -> dict:
    """组装本任务实际引用的资产版本快照（落到 `jobs.asset_versions_snapshot`）。

    关键点：**无论资产有没有版本化，这里都记下实际生效的数据**。
    - 版本号 > 0：记版本快照（不可变，天然可复现）；
    - 版本号 = 0：记入队那一刻的池行当前值（未版本化资产的可复现性由这份
      内联快照兜住——这正是 PR-04 `characters_snapshot` 在做的事，本层把它
      统一到角色/服装/幕布三类，并补上"来自哪个版本"的信息）。

    只读，不写库；不抛异常（任何解析失败都降级为空，不能因为快照失败拦住生成）。
    """
    snap = {
        "schema_version": SCHEMA_VERSION,
        "characters": [],
        "outfits": [],
        "plate": None,
    }

    # ---- 角色：引用顺序以 shot.character_ids 为准 ----
    char_refs = _json_map(shot.get("character_version_refs"))
    for c in (characters or []):
        cid = c.get("id")
        if not cid:
            continue
        entry = {
            "id": cid,
            "name": c.get("name") or "",
            "ref_version": _as_int(char_refs.get(cid), 0),
        }
        resolved = resolve_data(KIND_CHARACTER, cid, entry["ref_version"])
        if resolved:
            entry["version"] = resolved["version"]
            # 版本化时用版本快照覆盖；未版本化时用入队那一刻的当前值
            data = resolved["data"] or {}
            entry["data"] = {
                k: data.get(k) for k in
                ("appearance", "outfit", "lora", "expressions", "reference_images")
            }
            if resolved.get("missing_version"):
                entry["missing_version"] = resolved["missing_version"]
        else:
            entry["version"] = 0
            entry["data"] = {
                k: c.get(k) for k in
                ("appearance", "outfit", "lora", "expressions", "reference_images")
            }
        snap["characters"].append(entry)

    # ---- 服装：shot.outfit_refs 是 {outfit_id: version} 映射 ----
    for outfit_id, ref in _json_map(shot.get("outfit_refs")).items():
        resolved = resolve_data(KIND_OUTFIT, outfit_id, _as_int(ref, 0))
        if not resolved:
            continue
        data = resolved["data"] or {}
        snap["outfits"].append({
            "id": outfit_id,
            "name": data.get("name") or "",
            "ref_version": _as_int(ref, 0),
            "version": resolved["version"],
            "data": {k: data.get(k) for k in
                     ("reference_assets", "palette", "mesh_asset_id", "material")},
        })

    # ---- 幕布：镜头级 > 场景默认 > 旧 scene.reference_image ----
    plate = _resolve_plate_for_shot(shot, scene)
    if plate:
        snap["plate"] = plate

    return snap


def _resolve_plate_for_shot(shot: dict, scene: Optional[dict]) -> Optional[dict]:
    """幕布解析：镜头绑定 > 场景默认 > 旧 reference_image（未收编的字符串 URL）。

    返回 {'id', 'version', 'image_url', 'sha256'}；都没有则 None。
    """
    plate_id = (shot.get("plate_id") or "").strip()
    if plate_id:
        resolved = resolve_data(KIND_PLATE, plate_id, _as_int(shot.get("plate_version"), 0))
        if resolved:
            data = resolved["data"] or {}
            return {
                "id": plate_id,
                "version": resolved["version"],
                "image_url": data.get("image_url") or "",
                "sha256": data.get("sha256") or "",
                "form": data.get("form") or "",
                "source": "shot",
            }
    if scene:
        legacy = (scene.get("reference_image") or "").strip()
        ver_id = (scene.get("plate_version_id") or "").strip()
        if ver_id:
            ver = get_version(KIND_PLATE, ver_id)
            if ver:
                return {
                    "id": ver.get("plate_id") or "",
                    "version": ver.get("version") or 0,
                    "image_url": ver.get("image_url") or "",
                    "sha256": ver.get("sha256") or "",
                    "form": "",
                    "source": "scene",
                }
        if legacy:
            # 未收编进幕布库的旧数据：如实记录成"未版本化幕布"，
            # 不伪造 plate_id（否则会给界面一个不存在的池记录）
            return {
                "id": "",
                "version": 0,
                "image_url": legacy,
                "sha256": "",
                "form": "",
                "source": "scene_legacy",
            }
    return None


def plate_image_for_shot(shot: dict, scene: Optional[dict]) -> str:
    """给生成链路用：取本镜头实际生效的幕布图 URL；无则空串。"""
    plate = _resolve_plate_for_shot(shot, scene)
    return (plate or {}).get("image_url") or ""


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sha256_file(path) -> str:
    """计算受管素材的 sha256（版本登记用）。读失败返回空串，不抛。"""
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""
