"""白模替换：素材职责模型 + 九段提示词组装（第二十批）

## 为什么需要这一层

v2v（白模替换）此前已经能跑通：白模视频 + 角色图 → H3 Content Builder → 生成。
但提示词只是 `角色 ‖ 动作 ‖ 场景` 三段拼接，缺了这类任务真正需要的东西——
**每份素材的职责边界**。

后果很具体：模型会把白模的灰色皮肤留在脸上、把参考图的静态站姿当成动作、
把场景参考图里的人一起画进去。不是模型不听话，是没人告诉它"这份素材不负责这一项"。

## 九段结构

对齐「以 @视频1 白模舞蹈为参考，生成替换人物与场景后的完整视频」那套写法：

    01 生成目标        这一版在做什么
    02 参考素材职责     每份素材管什么、不采用什么
    03 人物外观        全片只出现一个身份，跨景别一致
    04 舞蹈与动作继承   起/加/停/衔接/结束跟随原片，不重编不简化
    05 近镜手势与透视   近大远小、手部进出前景的路径与时机
    06 表情与口型      视线/头部角度/眉眼/嘴角，不固定微笑
    07 镜头继承        镜头顺序/景别/机位高度/推进速度/切镜时机
    08 场景与光影融合   连续三维环境，视差透视遮挡变化，同一光源
    09 声音与收束      保留原音轨与画声节拍，不新增对白音效

## 三条设计原则

1. **职责是数据，不是文案。** 谁承担动作、谁承担身份、谁承担环境由参数决定，
   段文案从职责派生。改素材组合不用改文案。
2. **必须写「不采用」。** 这一条是整套写法里真正起作用的那个部分。
   只写"参考什么"模型会连不想要的也一起参考。
3. **缺素材时改写，不留空。** 没有角色图 / 没有场景图时，对应段改写成
   "以提示词描述为准"的形态——留空段等于把这一段交给模型自由发挥。

## 与既有拼装的关系

`assets.compose_prompt_with_characters` / `motion.compose_motion_prompt` /
`assets.compose_prompt_with_scene` 那三段拼装是给 t2v/i2v 用的，保留不动。
本模块只在 **v2v** 模式下替换提示词组装——那个模式才有"多份素材各有职责"的问题。
"""
from typing import Optional

# 段号 → 段名（UI 与文档共用同一份定义，避免两处漂移）
SECTIONS = (
    (1, "生成目标"),
    (2, "参考素材职责"),
    (3, "人物外观"),
    (4, "舞蹈与动作继承"),
    (5, "近镜手势与透视"),
    (6, "表情与口型"),
    (7, "镜头继承"),
    (8, "场景与光影融合"),
    (9, "声音与收束"),
)

DEFAULT_ACTION_REF = "参考视频1"
DEFAULT_IDENTITY_REF = "图1"
DEFAULT_ENVIRONMENT_REF = "图2"


def _clean(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def duties(action_ref: str, identity_ref: str, environment_ref: str,
           has_identity: bool, has_environment: bool) -> dict:
    """职责对照（供 UI 展示与快照存档——"这份素材不负责什么"必须能被人看到）。

    返回 {'action': {...}, 'identity': {...}, 'environment': {...}}，每项含
    ref / owns / not_owns。
    """
    return {
        "action": {
            "ref": action_ref,
            "owns": [
                "动作", "姿态", "运动路径", "表情", "口型", "镜头运动", "构图",
                "剪辑节奏", "镜头顺序与景别", "机位高度与拍摄方向",
                "推进后退", "运动速度", "切镜时机",
            ],
            "not_owns": [
                "白模人物的面容", "光头", "吊带短裤", "白色材质", "空白背景",
            ],
        },
        "identity": {
            "ref": identity_ref,
            "enabled": has_identity,
            "owns": ["面容", "发型", "服装", "配饰", "身体比例", "外观风格"],
            "not_owns": ["图中的静态站姿", "原图背景"],
        },
        "environment": {
            "ref": environment_ref,
            "enabled": has_environment,
            "owns": ["空间结构", "地面", "环境材质", "色调", "光源方向与氛围"],
            "not_owns": ["图中的任何人物"],
        },
    }


def _sec(no: int, name: str, body: str) -> str:
    return f"【{name}】\n{body}".strip()


def build_white_model_prompt(*,
                             action_ref: str = DEFAULT_ACTION_REF,
                             identity_ref: str = DEFAULT_IDENTITY_REF,
                             environment_ref: str = DEFAULT_ENVIRONMENT_REF,
                             has_identity: bool = True,
                             has_environment: bool = True,
                             identity_note: str = "",
                             environment_note: str = "",
                             extra: str = "") -> str:
    """组装白模替换的九段提示词。

    参数
    ----
    action_ref / identity_ref / environment_ref
        素材在提示词里的称呼。保持与用户看到的素材命名一致
        （LibTV 那套用 `@视频1`，本地模板用 `参考视频1`）——名字对不上，
        模型会以为在说另一份素材。
    has_identity / has_environment
        是否真的挂了角色图 / 场景图。**未挂时对应段会改写成"以描述为准"**，
        而不是留一句"见参考图"让模型去找一张不存在的图。
    identity_note / environment_note
        角色外观补充（来自角色卡）与场景补充（来自场景 environment/lighting）。
        只在有对应素材时才有意义，作为该段的收束句。
    extra
        调用方追加的额外约束（例如镜头特有的要求），挂在末尾而不是插进中间段
        ——插进中间会让段职责互相渗透。
    """
    action_ref = _clean(action_ref, DEFAULT_ACTION_REF)
    identity_ref = _clean(identity_ref, DEFAULT_IDENTITY_REF)
    environment_ref = _clean(environment_ref, DEFAULT_ENVIRONMENT_REF)
    identity_note = _clean(identity_note)
    environment_note = _clean(environment_note)
    extra = _clean(extra)

    d = duties(action_ref, identity_ref, environment_ref,
               has_identity, has_environment)

    out = []

    # 01 生成目标
    out.append(_sec(
        1, "生成目标",
        f"以 {action_ref} 的白模舞蹈、表演和镜头结构为参考，生成替换人物与场景后的完整视频。"
        f"保留原片的舞蹈编排、动作顺序、速度变化、表情、视线，以及远近景切换和广角透视效果。",
    ))

    # 02 参考素材职责 —— 逐份写明「管什么 / 不采用什么」
    lines = [f"{action_ref} 只负责" + "、".join(d["action"]["owns"]) + "。",
             f"{action_ref} 不采用" + "、".join(d["action"]["not_owns"]) + "。"]
    if has_identity:
        lines.append(f"{identity_ref} 只负责" + "、".join(d["identity"]["owns"])
                     + "，不采用" + "、".join(d["identity"]["not_owns"]) + "。")
    else:
        lines.append(f"本次没有人物参考图：人物外观完全以本提示词第 03 段描述为准，"
                     f"不要沿用 {action_ref} 里白模人物的任何外观特征。")
    if has_environment:
        lines.append(f"{environment_ref} 只负责" + "、".join(d["environment"]["owns"])
                     + "，不采用" + "、".join(d["environment"]["not_owns"]) + "。")
    else:
        lines.append(f"本次没有场景参考图：场景完全以本提示词第 08 段描述为准，"
                     f"不要沿用 {action_ref} 的空白背景。")
    lines.append("所有图片均为资产参考，不作为固定首帧或尾帧。")
    out.append(_sec(2, "参考素材职责", "\n".join(lines)))

    # 03 人物外观
    body = (f"全片只出现 {identity_ref} 定义的那一个人物。正面、侧面、背面和近景中的"
            f"五官、发型、服装、配饰与身体比例保持一致。"
            if has_identity else
            f"全片只出现一个人物，外观以本段描述为准。")
    if has_identity:
        body += "原白模外观全部替换，不能保留光头、白色皮肤或原白模服装，也不能仅给白模换色。"
        body += f"人物体型以 {identity_ref} 为准，动作以 {action_ref} 为准；" \
                f"根据目标人物的身体比例自然适配关节位置，保持原动作轮廓、幅度、方向和节奏。"
    else:
        # 没挂人物图时**不能**再引用 {identity_ref}——那句会指向一张不存在的图。
        body += f"动作以 {action_ref} 为准；根据本片人物的身体比例自然适配关节位置，" \
                f"保持原动作轮廓、幅度、方向和节奏。"
    if identity_note:
        body += f" 本片人物设定：{identity_note}"
    out.append(_sec(3, "人物外观", body))

    # 04 舞蹈与动作继承
    out.append(_sec(
        4, "舞蹈与动作继承",
        f"从原视频开场姿势直接开始，完整继承 {action_ref} 的伸展、手臂展开与回收、"
        f"手掌靠近镜头、手腕翻转、指尖姿态、抬臂过头、身体侧弯、胸腰起伏、肩膀扭转、"
        f"屈膝、交叉步、抬腿和转身等实际动作。动作的开始、加速、停顿、衔接和结束状态"
        f"均跟随原片，不重新编排、不简化手势、不增加重复动作、不改慢动作。\n"
        f"重点保留手臂与躯干之间的连续传导：肩膀带动上臂、肘部连接前臂、"
        f"手腕与手指自然跟随；腰胯和脚步共同完成重心转换。脚底接触地面时有支撑，"
        f"抬脚和落脚时机跟随原视频，不滑步、不悬浮。\n"
        f"头发、衣摆和配饰随动作产生自然惯性，运动结束后合理回落，不凭空持续飘动。"
        f"服装可以随动作形成褶皱，但不能遮盖或替代关键肢体动作。",
    ))

    # 05 近镜手势与透视
    out.append(_sec(
        5, "近镜手势与透视",
        "保留原视频中手掌、手臂靠近镜头时近大远小的广角效果。手部进入前景、"
        "遮挡面部、退出前景的路径和时机跟随原片，不能为了保持面部完整而缩小手势"
        "或改变机位。\n"
        "前景放大的手掌始终连接对应手腕和手臂；动作产生的拖影只表现运动，"
        "不形成额外手掌、手臂或独立道具。镜头中的身体透视变化应由原有机位和动作产生，"
        "不改变人物实际身体比例。",
    ))

    # 06 表情与口型
    out.append(_sec(
        6, "表情与口型",
        "保留原视频的视线方向、头部角度、眉眼变化、嘴角动作及张闭嘴过程，"
        + (f"使用 {identity_ref} 的五官完成对应表演。" if has_identity
           else "用本片人物的五官完成对应表演。")
        + "表情随动作自然变化，不固定微笑、不全程直视镜头。"
        + f"原视频有发声或对口型的部分，沿用对应口型时点，不新增台词。",
    ))

    # 07 镜头继承
    out.append(_sec(
        7, "镜头继承",
        f"沿用 {action_ref} 的镜头顺序、景别、机位高度、拍摄方向、镜头倾斜、"
        f"推进后退、运动速度与切镜时机。保留全身远景、半身画面、面部近景，"
        f"以及贴近手臂和手掌的广角构图。\n"
        f"人物在画面中的大小、位置、朝向和出入画方式跟随原片，"
        f"不改成固定机位全身展示，不额外加入环绕、慢镜或新转场。"
        f"原视频中被构图裁切的身体部位自然延续，不为完整展示人物而擅自拉远。",
    ))

    # 08 场景与光影融合
    if has_environment:
        body8 = (f"以 {environment_ref} 建立完整、连续的三维环境，地面透视和人物接地位置"
                 f"适配原舞蹈。场景围绕原视频的人物运动区域组织，保留足够活动空间，"
                 f"不因环境物件阻挡而修改舞蹈。")
    else:
        body8 = ("按本段描述建立完整、连续的三维环境，地面透视和人物接地位置适配原舞蹈。"
                 "场景围绕原视频的人物运动区域组织，保留足够活动空间。")
    body8 += ("\n镜头移动时，场景产生对应的视差、透视与遮挡变化，"
              "不能将场景参考图作为静态背景贴入。全片属于同一空间，"
              "不在不同景别间突然更换场景。\n"
              "人物、服装和道具受同一光源照明，受光面、背光面、投影方向及环境反射一致。"
              "面部和身体保持清晰的明暗结构，脚底有自然接触阴影，"
              "避免人物发白、悬浮或与环境脱离。整体风格以参考图片为准，"
              "不继承白模视频的灰白外观。")
    if environment_note:
        body8 += f" 本片场景设定：{environment_note}"
    out.append(_sec(8, "场景与光影融合", body8))

    # 09 声音与收束
    out.append(_sec(
        9, "声音与收束",
        f"保留 {action_ref} 的原音轨及画声节拍关系，不新增对白或音效事件。"
        f"开场与结尾继承原片对应的姿势、表情、构图和运动状态。",
    ))

    if extra:
        out.append(_sec(0, "补充约束", extra))

    return "\n\n".join(out)


def identity_note_from_character(character: Optional[dict]) -> str:
    """角色卡 → 第 03 段的补充句。取不到就返回空（不编内容）。"""
    if not character:
        return ""
    parts = []
    for key, label in (("appearance", "外貌"), ("outfit", "服装")):
        value = _clean(character.get(key))
        if value:
            parts.append(f"{label}：{value}")
    return "；".join(parts)


def environment_note_from_scene(scene: Optional[dict]) -> str:
    """场景卡 → 第 08 段的补充句。取不到就返回空（不编内容）。"""
    if not scene:
        return ""
    parts = []
    for key, label in (("environment", "环境"), ("lighting", "光照"),
                       ("style", "风格"), ("layout", "布局")):
        value = _clean(scene.get(key))
        if value:
            parts.append(f"{label}：{value}")
    return "；".join(parts)
