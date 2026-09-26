"""交互的展示语义：这条请求为什么问、专用界面长什么样、文案由谁写。

与 ``judge_presentation.py`` 是同一套做法，只是对象从"判定"换成"交互请求"：

* **规则层**在这里声明每一种请求意图（``reason``）对应的面板与文案；
* **UI 层**只消费 ``InteractionSchema.payload`` 里的 ``panel`` / ``title`` /
  ``note``，不允许按 reason 字符串白名单自己接管画面，也不允许把规则文案
  （"受到 1 点火焰伤害"这类）写死在绘制代码里。

新增一种需要专用界面的请求，只需要在这里加一条声明——本地与联机同时生效，
因为两侧读的是同一份 ``InteractionSchema``。
"""

from dataclasses import dataclass
from typing import Optional, Tuple

# ==================================================
# 面板名（UI 认识这几个名字；规则层决定用哪一个）
# ==================================================

#: 通用选牌 / 选目标 / 确认界面。没有专用需求时一律用它。
PANEL_DEFAULT = ""
#: "有一张已公开的牌 + 从自己手里按条件挑牌"（火攻的两个阶段）。
PANEL_REVEAL_AND_PICK = "reveal_and_pick"


@dataclass(frozen=True)
class InteractionSourceSpec:
    """一种交互意图的静态声明。"""

    reason: str
    title: str
    panel: str = PANEL_DEFAULT
    #: 同一块专用面板内部的分阶段语义（火攻的 ``reveal`` / ``discard``）。
    #: 面板据此切换标题与两侧内容，不需要再靠请求原因字符串认阶段。
    stage: str = ""
    #: 完整文案（不含参数时用它）。
    note: str = ""
    #: 带参数的文案模板，用 ``str.format(**context)`` 渲染。
    #: 允许的占位符由声明者自己决定，渲染失败时退回 ``note``——
    #: 文案出错绝不能把交互流程打断。
    note_template: str = ""

    def render(self, context) -> str:
        data = dict(context or {})
        if self.note_template:
            try:
                return self.note_template.format(**data)
            except (KeyError, IndexError, ValueError):
                pass
        return self.note


# ==================================================
# 声明表
# ==================================================

INTERACTION_SOURCES = {
    # ---- 判定：改判窗口（鬼才 / 鬼道…）----
    #
    # 通用选牌界面已经够用，不需要专用面板；文案由规则层给。
    "judge_replacement": InteractionSourceSpec(
        reason="judge_replacement",
        title="改判",
        note="选择一张手牌替换当前的判定牌；不选则维持原判定。",
    ),
    # ---- 火攻：阶段一（目标展示）----
    "huogong_reveal": InteractionSourceSpec(
        reason="huogong_reveal",
        title="火攻 · 展示",
        panel=PANEL_REVEAL_AND_PICK,
        stage="reveal",
        note_template=(
            "选择一张手牌展示给对方；对方若能弃置同花色的牌，"
            "你将受到 {damage} 点{nature}伤害。"
        ),
        note="选择一张手牌展示给对方。",
    ),
    # ---- 火攻：阶段二（使用者弃牌）----
    "huogong_discard": InteractionSourceSpec(
        reason="huogong_discard",
        title="火攻 · 弃置",
        panel=PANEL_REVEAL_AND_PICK,
        stage="discard",
        note_template="你需要弃置一张{required_suit_label}手牌。",
        note="你需要弃置一张与展示牌同花色的手牌。",
    ),
    # ---- 共享无懈阶段 ----
    "wuxie_chain": InteractionSourceSpec(
        reason="wuxie_chain",
        title="无懈可击",
        note="可以打出一张【无懈可击】抵消这张锦囊；也可以放弃。",
    ),
    # ---- 濒死求桃 ----
    "dying_rescue": InteractionSourceSpec(
        reason="dying_rescue",
        title="濒死求桃",
        note="有角色濒死，可以打出一张【桃】救援。",
    ),
}

#: 没有声明时的兜底：UI 用通用界面，文案取 ``PendingRequest.prompt``。
FALLBACK_SPEC: Optional[InteractionSourceSpec] = None


def interaction_source(reason) -> Optional[InteractionSourceSpec]:
    """按请求意图取声明；没有声明返回 None（不算错误，走通用界面）。"""

    key = str(reason or "")
    if not key:
        return None
    return INTERACTION_SOURCES.get(key)


def interaction_panels() -> Tuple[str, ...]:
    """全项目用到的专用面板名（便于 UI 自检与报告）。"""

    return tuple(sorted({spec.panel for spec in INTERACTION_SOURCES.values() if spec.panel}))


def interaction_reasons() -> Tuple[str, ...]:
    return tuple(sorted(INTERACTION_SOURCES))


# ==================================================
# 技能类型的中文标签（唯一一份）
#
# 这张表原来在 UI 里有三份逐字重复的实现（技能条 / 判定面板 / 演出队列的
# 类型转中文），改一处就会漂。规则层声明类型，展示名也由规则层给。
# ==================================================

SKILL_KIND_LABELS = {
    "active": "主动技",
    "view_as": "视为技",
    "locked": "锁定技",
    "passive": "触发技",
}


def skill_kind_label(kind) -> str:
    """技能类型 → 中文标签；未知类型返回空串（界面不显示类型 chip）。"""

    key = getattr(kind, "value", kind)
    return SKILL_KIND_LABELS.get(str(key or ""), "")


def skill_payload(game, skill_id, skill_name="", **extra) -> dict:
    """``SKILL_TRIGGERED`` 的统一载荷：技能名 / 类型 / 类型标签 / 说明。

    **所有发射点都从这里取**（触发技、主动技、视为技转化、装备锁定技…），
    界面因此永远不需要为了显示一条提示去查技能表——联网客户端与房主显示的
    是同一段文案，缺某个技能定义也不会显示成空白。

    ``extra`` 里的同名字段优先（例如装备类"锁定技"没有 SkillDef，
    由调用方直接把类型与说明写进来）。
    """

    payload = {
        "skill_id": str(skill_id or ""),
        "skill_name": str(skill_name or skill_id or ""),
    }
    registry = getattr(game, "skill_registry", None)
    definition = None
    if registry is not None and skill_id:
        definition = registry.get(skill_id)
    if definition is not None:
        kind = getattr(getattr(definition, "kind", None), "value", "")
        payload["skill_name"] = str(getattr(definition, "name", "") or payload["skill_name"])
        payload["description"] = str(getattr(definition, "description", "") or "")
        payload["kind"] = str(kind)
        payload["kind_label"] = skill_kind_label(kind)
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    return payload
