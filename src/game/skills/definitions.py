"""Skill definitions: what a skill is, independent of who owns it.

A definition never stores an owner.  The same skill may be bound to several
players at once, so ownership lives in the runtime ``Skill`` instances created
by ``SkillManager``.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Tuple


class SkillKind(str, Enum):
    """Lifecycle classification used by the state helpers."""

    PASSIVE = "passive"      # 触发式：由事件驱动
    LOCKED = "locked"        # 锁定技：无条件生效，不可选择不发动
    ACTIVE = "active"        # 主动技：玩家主动发动，执行技能自己的流程
    VIEW_AS = "view_as"      # 视为技：玩家主动进入"把这张牌当那张牌"的选牌模式


SkillFactory = Callable[[Any], Any]
SkillCondition = Callable[..., bool]
SkillAction = Callable[..., Any]


@dataclass(frozen=True)
class JudgeReplacement:
    """判定替换能力：在判定牌生效前打出一张牌替换它（鬼才 / 鬼道一类）。

    ``candidates`` 返回本次可以使用的替换牌；返回空列表表示这次不能发动。
    """

    candidates: Any                # callable(game, player, judge_context) -> list
    prompt: str = "是否替换判定牌？"
    source_zone: str = "hand"


@dataclass(frozen=True)
class PhaseReplacement:
    """阶段替代（突袭一类）：在自己的某个阶段开始时询问是否改用别的结算。

    纯数据替代用 ``apply``（同步返回是否替代成功）；需要玩家补全输入
    （选目标、选牌）的技能用 ``flow``，它返回一个流程对象，由 TurnFlow
    挂起等待——期间阶段结算已经跳过，流程结束后回合继续。
    """

    phase: Any                                  # TurnPhase
    prompt: str
    can_offer: Any                              # callable(game, player) -> bool
    apply: Any = None                           # callable(game, player) -> bool（True = 已替代）
    flow: Any = None                            # callable(game, player) -> Flow


@dataclass(frozen=True)
class ActiveSkillSpec:
    """主动技能的输入需求。

    真人由 UI 收集这些输入，全部齐备后才提交一次 ActivateSkillAction：
    校验 → 支付费用 → 结算。因此取消时既不会写 used 标记，也不会提前弃牌。
    """

    needs_target: bool = False
    target_candidates: Any = None          # callable(game, player) -> list
    target_prompt: str = "请选择目标"
    cost_cards: int = 0                    # 需要弃置的手牌数
    cost_prompt: str = "请选择要弃置的牌"
    # 费用牌数量可变（制衡）：0 或 1 .. 手牌数，由玩家决定。
    variable_cost: bool = False
    # ``variable_cost`` 时玩家最多能挑几张（0 = 不设上限，按手牌数算）。
    # 【举荐】是"弃置至多三张"，上限来自规则而不是手牌。
    max_cost_cards: int = 0
    # 费用牌不弃置，而是交给目标角色（仁德）。
    transfer_cards: bool = False
    # 费用牌的**合法候选**：``callable(game, player, card) -> bool``。
    # 声明了它，本地界面高亮、引擎校验、远程下发的候选读的都是这一份判断，
    # 玩家挑不出非法牌——也没有任何一条路径能替他挑。
    cost_candidates: Any = None
    # 这些牌**不是费用**：不弃置、也不交给目标，去向完全由技能自己的
    # ``activate`` 决定（把红桃手牌交给目标【眩惑】、把装备牌装进对方装备区
    # 【直谏】、当成转化的实体来源【双雄】）。
    #
    # 为什么必须有这个开关：费用语义是"先支付再结算"，而那类牌的语义是
    # "这张牌就是这次技能动作本身"。把它当费用先弃掉，技能只能凭空造出
    # 结果；把它交给技能、由技能决定去向，才是真实规则。
    keep_cards: bool = False


@dataclass(frozen=True)
class ModifierSpec:
    """A continuous rule modifier a skill contributes while it is bound.

    ``condition`` 是"这条修正现在生效吗"的可选判据（callable(game, query)
    -> bool）：【天义】只有在拼点赢了、且本回合还没结束时才改写攻击范围与
    出杀次数，靠的就是它，而不是让每一条修正自己再判一次。
    """

    kind: Any                      # ModifierKind
    value: Any                     # int 或 callable(game, query) -> int
    roles: Tuple[str, ...] = ()
    priority: int = 0
    condition: Any = None          # 可选 callable(game, query) -> bool


@dataclass(frozen=True)
class SkillDef:
    """One skill as data.

    ``factory`` produces the runtime hook object (a ``Skill`` subclass) for
    triggered skills.  ``activate``/``can_activate`` describe active skills.
    ``modifiers`` declares continuous influences (距离 / 手牌上限 / 摸牌数…).

    A skill may use any combination of the three: 咆哮 is modifier-only,
    集智 is trigger-only, and an active skill may also carry modifiers.
    """

    id: str
    name: str
    description: str = ""
    kind: SkillKind = SkillKind.PASSIVE
    factory: Optional[SkillFactory] = None
    can_activate: Optional[SkillCondition] = None
    activate: Optional[SkillAction] = None
    modifiers: Tuple[ModifierSpec, ...] = field(default_factory=tuple)
    judge_replacement: Optional[JudgeReplacement] = None
    phase_replacement: Optional[PhaseReplacement] = None
    active_spec: Optional[ActiveSkillSpec] = None
    conversions: Tuple[Any, ...] = field(default_factory=tuple)
    general_id: Optional[str] = None
    tags: Tuple[str, ...] = field(default_factory=tuple)
    # 主公技：只有“当前武将是主公”时才启用（身份模式的主公）。FFA 下永不启用。
    is_lord_skill: bool = False
    #: 发动过程需要**只有本地真人界面才有**的选牌通道
    #: （``game.start_card_selection``，见 ``card_selection.py``）。
    #:
    #: 这类技能目前只能由坐在房主电脑前的真人发动：远程真人在出牌阶段不会
    #: 拿到它的入口（房主会明确写出原因）——因为那条通道不会变成
    #: ``DecisionRequest``，远程玩家会看到一个永远等不到答案的窗口。
    #: 把技能的选牌改成 ``PendingRequest``（像【突袭】那样开一个小 Flow）之后
    #: 这个标记就可以去掉；分析见 Phase 11.4 报告“仍存在的限制”。
    needs_local_ui: bool = False

    @property
    def is_lord(self):
        return bool(self.is_lord_skill)

    @property
    def is_active(self):
        return self.kind is SkillKind.ACTIVE

    @property
    def is_view_as(self):
        """视为技（龙胆 / 武圣…）：必须先由玩家点技能，再选择实体牌。"""

        return self.kind is SkillKind.VIEW_AS

    @property
    def is_locked(self):
        return self.kind is SkillKind.LOCKED

    def __post_init__(self):
        if not self.id:
            raise ValueError("SkillDef.id is required")
        if (
            self.factory is None
            and self.activate is None
            and not self.modifiers
            and self.judge_replacement is None
            and self.phase_replacement is None
            and not self.conversions
        ):
            raise ValueError(
                "SkillDef needs a factory, an activate handler, modifiers, "
                "a judge replacement, or conversions: " + self.id
            )


def triggered(skill_id, name, description="", *, kind=SkillKind.PASSIVE, factory,
              tags=(), modifiers=(), is_lord_skill=False):
    """Helper for the common case: a skill that reacts to events."""

    return SkillDef(
        id=skill_id,
        name=name,
        description=description,
        kind=kind,
        factory=factory,
        modifiers=tuple(modifiers),
        tags=tuple(tags),
        is_lord_skill=is_lord_skill,
    )


def active(skill_id, name, description="", *, can_activate, activate, tags=(),
           modifiers=(), spec=None, is_lord_skill=False, needs_local_ui=False):
    """Helper for a skill the player launches on purpose."""

    return SkillDef(
        id=skill_id,
        name=name,
        description=description,
        kind=SkillKind.ACTIVE,
        can_activate=can_activate,
        activate=activate,
        active_spec=spec,
        modifiers=tuple(modifiers),
        tags=tuple(tags),
        is_lord_skill=is_lord_skill,
        needs_local_ui=needs_local_ui,
    )
