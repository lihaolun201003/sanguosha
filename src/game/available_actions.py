"""出牌阶段的统一动作查询契约（Phase 15A）。

这个模块**不实现规则**：它把已有的三个规则提供者聚合起来，用同一种描述形状
回答同一个问题——"这名角色现在能做什么"。

============================  ==============================================
规则提供者                     负责什么
============================  ==============================================
``CardActionDiscovery``        实体牌 / 转换牌（普通使用、View-As、素材约束）
``CardEffect``                目标规则与数量（``target_rule_for`` /
                               ``target_bounds_for`` / ``can_use``）
``SkillManager``               主动技能可发动性 + ``activation_inputs`` 输入
============================  ==============================================

改这一层之前先读 Phase 15A 的边界：

* 它**只描述**"当前可发现的行为"，不是第二套规则引擎、不是执行引擎、更不是
  授权凭证。任何提交仍由 ``GameEngine`` / ``UseCardFlow`` / ``CardEffect`` /
  ``resolve_activation`` 按最新状态复核。
* 查询必须**无副作用**：不扣牌、不计技能次数、不发事件、不写日志、不推进
  Flow、不创建持久 VirtualCard。为判定结果牌而临时构造的 ``VirtualCard``
  用完即弃（它的进程内递增 id **不会**被用作 ``action_id``）。
* 覆盖范围是**出牌阶段**：普通使用、View-As、主动技能、重铸、结束出牌阶段，
  以及选择素材与目标的中间查询。响应 / 救援 / 共享无懈 / 嵌套请求属于
  Phase 15B，这里不假装已经统一。
* ``selection`` 是玩家的**交互草稿**，不是第二份权威状态：每次草稿变化重新
  查询即可，不预先枚举"素材组合 × 目标组合"。

稳定标识：``action_id`` 在当前动作上下文中稳定（同一局面、同一素材组合，
重复查询得到同一个值）。它不跨局、不跨进程永久稳定；转换动作用的是**实体
素材牌**的 id，不是临时虚拟牌的 id。
"""

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from src.game.card_actions.context import CardActionContext
from src.game.conversion import PLAY_CONTEXT
from src.game.rules import TargetRule

# ==================================================
# 动作种类
# ==================================================


class ActionType:
    """出牌阶段能描述的五类行为（彼此不能互相冒充）。"""

    PLAY = "play"                    # 普通使用一张实体牌
    VIEW_AS = "view_as"              # 技能把实体牌当另一张牌使用（含装备赋予的视为技）
    ACTIVE_SKILL = "active_skill"    # 发动主动技（可能有费用 / 目标）
    RECAST = "recast"                # 重铸（置入弃牌堆并摸一张牌）
    END_PLAY_PHASE = "end_play_phase"  # 结束出牌阶段


class TargetMode:
    """目标怎么来（由 ``TargetRule`` 归并，界面据此决定要不要让玩家点人）。"""

    NONE = "none"        # 不需要目标
    SELF = "self"        # 目标由规则决定 = 自己
    ALL = "all"          # 目标由规则决定 = 全体（按座次）
    CHOOSE = "choose"    # 玩家自己挑


#: 重铸动作的 metadata 约定：规则层（``TiesuoEffect.can_use`` 一类）读它，
#: 本地 / AI / 远程 / 查询四处共用同一份，不各写一遍。
RECAST_METADATA = {"recast": True, "skip_wuxie": True}

#: 这些 TargetRule 的目标不是"玩家挑出来的"，而是规则算出来的。
RULE_DECIDED_MODES = {
    TargetRule.NO_TARGET: TargetMode.NONE,
    TargetRule.SELF: TargetMode.SELF,
    TargetRule.ALL_PLAYERS: TargetMode.ALL,
    TargetRule.ALL_OTHERS: TargetMode.ALL,
}


# ==================================================
# 描述数据
# ==================================================

@dataclass(frozen=True)
class SourceCandidate:
    """一个可以充当素材的实体牌（含它所在的区域）。"""

    card_id: str
    name: str = ""
    label: str = ""
    zone: str = ""
    slot: str = ""
    owner_id: str = ""

    @property
    def uid(self):
        """素材在 action_id 里用的稳定标识：实体牌 id，不是对象地址。"""

        return self.card_id


@dataclass(frozen=True)
class TargetCandidate:
    """一个候选目标（只带公开身份，不带任何隐藏信息）。"""

    player_id: str
    name: str = ""
    seat: int = 0

    def entry(self):
        """转成现有 Decision / 界面用的条目形状（与 Remote 的 player_entry 同形）。"""

        return {"player_id": self.player_id, "nickname": self.name, "seat": self.seat}


@dataclass(frozen=True)
class TargetProfile:
    """一次动作的目标结论（候选 / 数量 / 模式），全部来自**动态**规则接口。"""

    rule: str = TargetRule.NO_TARGET.value
    mode: str = TargetMode.NONE
    min_targets: int = 0
    max_targets: int = 0
    candidates: Tuple[TargetCandidate, ...] = ()
    requires_targets: bool = False
    usable: bool = True
    reason: str = ""
    #: 与 ``candidates`` 一一对应的真实角色对象，只给同进程的调用方复用
    #: （AI / 控制器需要 Player 而不是描述）。不参与展示与比较。
    players: Tuple[Any, ...] = field(default=(), repr=False, compare=False)

    @property
    def bounds(self):
        return self.min_targets, self.max_targets

    @property
    def needs_choice(self):
        return self.mode == TargetMode.CHOOSE

    def count_within_bounds(self, count):
        return self.min_targets <= int(count) <= self.max_targets


@dataclass(frozen=True)
class AvailableAction:
    """一种"现在可以做的事"的纯描述。

    ``origin`` 是底下的规则对象（``CardActionOption`` / 技能定义），只给执行器
    复用，不参与展示与比较——描述本身是纯数据。
    """

    action_id: str
    kind: str
    actor_id: str
    context: str = PLAY_CONTEXT

    # ---- 来源 ----
    source_skill_id: str = ""
    skill_name: str = ""
    effective_card_name: str = ""
    effective_display: str = ""
    is_conversion: bool = False

    # ---- 素材 ----
    source_candidates: Tuple[SourceCandidate, ...] = ()
    min_sources: int = 0
    max_sources: int = 0

    # ---- 目标 ----
    target_candidates: Tuple[TargetCandidate, ...] = ()
    min_targets: int = 0
    max_targets: int = 0
    target_mode: str = TargetMode.NONE

    # ---- 状态 ----
    enabled: bool = True
    disabled_reason: str = ""
    complete: bool = True
    can_submit: bool = True
    submit_blocker: str = ""

    # ---- 主动技的输入契约（其余动作留空）----
    cost_prompt: str = ""
    target_prompt: str = ""
    variable_cost: bool = False
    transfer_cards: bool = False

    # ---- 展示 ----
    label: str = ""
    detail: str = ""
    prompt: str = ""

    origin: Any = field(default=None, repr=False, compare=False)

    # ---- 便捷查询 ----

    @property
    def source_bounds(self):
        return self.min_sources, self.max_sources

    @property
    def target_bounds(self):
        return self.min_targets, self.max_targets

    @property
    def needs_more_sources(self):
        return self.max_sources > 0 and self.min_sources > 0 and not self.complete

    @property
    def requires_targets(self):
        return self.target_mode == TargetMode.CHOOSE and self.max_targets > 0

    @property
    def source_ids(self):
        return tuple(item.card_id for item in self.source_candidates)

    @property
    def target_ids(self):
        return tuple(item.player_id for item in self.target_candidates)

    def describe(self):
        return "%s[%s] %s sources=%d..%d targets=%d..%d(%s)%s" % (
            self.kind, self.action_id, self.effective_display or self.source_skill_id,
            self.min_sources, self.max_sources,
            self.min_targets, self.max_targets, self.target_mode,
            "" if self.enabled else " disabled=" + (self.disabled_reason or "?"))


# ==================================================
# 聚合查询
# ==================================================

class AvailableActions:
    """出牌阶段的动作聚合查询（无状态；只读权威状态）。"""

    def __init__(self, game):
        self.game = game

    # ---- 底层规则提供者 ----

    @property
    def discovery(self):
        return self.game.card_actions

    @property
    def effects(self):
        return self.game.engine.card_effects

    def effect_of(self, card):
        return None if card is None else self.effects.get(card)

    def effective_card(self, option):
        """一个 CardActionOption 实际要用的牌（转换时现造的虚拟牌）。"""

        if option is None:
            return None
        return self.discovery.effective_card(option)

    def dynamic_target_rule(self, effect, actor, card):
        """目标规则：走 ``CardEffect.target_rule_for``（技能可以改写它）。

        读静态 ``effect.target_rule`` 会让"技能改了目标规则、界面却还按旧规则
        收目标"这类半截能力重现，所以动态查询是本模块唯一的入口。
        """

        if effect is None:
            return TargetRule.NO_TARGET
        query = getattr(effect, "target_rule_for", None)
        if callable(query):
            return query(self.game, actor, card)
        return getattr(effect, "target_rule", TargetRule.NO_TARGET)

    def dynamic_target_bounds(self, effect, actor, card):
        """目标数量范围：走 ``CardEffect.target_bounds_for``（动态）。"""

        if effect is None:
            return 0, 0
        query = getattr(effect, "target_bounds_for", None)
        if callable(query):
            minimum, maximum = query(self.game, actor, card)
            return int(minimum), int(maximum)
        return int(getattr(effect, "min_targets", 0) or 0), int(
            getattr(effect, "max_targets", 0) or 0)

    # ---- 目标 ----

    def target_profile(self, actor, option=None, *, card=None):
        """一个动作现在的目标结论（候选 / 数量 / 模式）。

        ``option`` 给 CardActionOption（转换会先现造结果牌，再问它的效果）；
        ``card`` 直接给实体牌 / 结果牌。两者给一个即可。
        """

        if card is None and option is not None:
            card = self.effective_card(option)
        if card is None:
            return TargetProfile(reason="没有可用的牌")

        effect = self.effect_of(card)
        if effect is None:
            # 【闪】【无懈可击】这类由响应系统处理的牌没有 V2 效果：出牌阶段
            # 本来就不能主动使用，这里如实报告"不需要目标"，不猜。
            return TargetProfile(reason="这张牌没有主动使用规则")

        rule = self.dynamic_target_rule(effect, actor, card)
        minimum, maximum = self.dynamic_target_bounds(effect, actor, card)
        mode = RULE_DECIDED_MODES.get(rule, TargetMode.CHOOSE)

        if mode == TargetMode.NONE:
            return TargetProfile(rule=rule.value, mode=mode, min_targets=0,
                                 max_targets=0, requires_targets=False)
        if mode == TargetMode.SELF:
            players = (actor,) if maximum >= 1 else ()
            return TargetProfile(rule=rule.value, mode=mode, min_targets=minimum,
                                 max_targets=maximum,
                                 candidates=tuple(_target_candidate(p) for p in players),
                                 players=players,
                                 requires_targets=maximum >= 1)
        if mode == TargetMode.ALL:
            ordered = self.game.seats.alive_players_in_order(
                start_after=actor, include_start=True)
            if rule is TargetRule.ALL_OTHERS:
                ordered = [player for player in ordered if player is not actor]
            ordered = tuple(ordered)
            return TargetProfile(
                rule=rule.value, mode=mode, min_targets=minimum, max_targets=maximum,
                candidates=tuple(_target_candidate(player) for player in ordered),
                players=ordered, requires_targets=False)

        # 玩家挑目标：候选来自规则层的座次过滤 + 逐目标的 can_use 探测。
        # 探测通过是"这一个目标单独看是否合法"，是**必要条件**：多个目标之间
        # 还能互相约束，最终仍由 CardEffect.can_use 对整组复核。
        players = []
        for target in self.discovery.target_candidates(actor, rule, card=card):
            if self._target_ok(actor, card, target):
                players.append(target)
        players = tuple(players)
        return TargetProfile(
            rule=rule.value, mode=mode, min_targets=minimum, max_targets=maximum,
            candidates=tuple(_target_candidate(player) for player in players),
            players=players, requires_targets=True,
            usable=bool(players),
            reason="" if players else "现在没有合法目标")

    def _target_ok(self, actor, card, target):
        from src.game.engine import UseCardAction

        effect = self.effect_of(card)
        if effect is None:
            return False
        ok, _reason = effect.can_use(
            self.game, UseCardAction(actor, card, [target]))
        return bool(ok)

    def selectable_targets(self, actor, action, selection=None):
        """这份草稿下还能再选哪些目标（不预先枚举组合）。"""

        chosen = {id(item) for item in _selected_players(selection)}
        return tuple(
            item for item in action.target_candidates
            if not any(_same_player(item, player) for player in chosen)
        )

    # ---- 单卡 ----

    def card_actions(self, actor, card, *, selection=None, context=None):
        """一张实体牌现在的全部动作（普通 / 转换 / 重铸并列）。"""

        context = context or self.discovery.play_context(actor)
        options = list(self.discovery.actions_for_card(actor, card, context))
        sources = _selected_cards(selection)
        if sources and any(item is card for item in sources):
            narrowed = list(self.discovery.actions_for_sources(actor, sources, context))
            if narrowed:
                options = narrowed
        actions = [
            self._from_option(actor, option, context, selection)
            for option in options
        ]
        recast = self._recast_action(actor, card, context, selection)
        if recast is not None:
            actions.append(recast)
        return actions

    def card_actions_for_sources(self, actor, cards, *, context=None, selection=None):
        """已经选好一组素材时收窄后的动作（多素材在选齐之后才出现）。"""

        context = context or self.discovery.play_context(actor)
        actions = []
        for option in self.discovery.actions_for_sources(actor, cards, context):
            actions.append(self._from_option(actor, option, context, selection))
        return actions

    def action_from_option(self, actor, option, *, context=None, selection=None, slot=""):
        """把一个 ``CardActionOption`` 包装成统一描述。

        Remote / UI / AI 都从这里拿描述；它们不再各自读静态目标规则。
        """

        context = context or self.discovery.play_context(actor)
        return self._from_option(actor, option, context, selection, slot=slot)

    def recast_action(self, actor, card):
        """重铸这一条用法（不能重铸时返回 None）。"""

        return self._recast_action(actor, card, None, None)

    def assemblable(self, actor, card, *, context=None):
        """这张牌能不能凑成至少一个**真能支付**的动作（灰化判定用）。"""

        context = context or self.discovery.play_context(actor)
        return bool(self.discovery.is_operable(actor, card, context)) or bool(
            self._recast_action(actor, card, context, None))

    # ---- 主动技能 ----

    def active_skills(self, actor):
        """当前可发动的主动技（含费用 / 目标 / 不可用原因）。"""

        game = self.game
        actions = []
        for skill_id in game.skills.activatable_skills(actor):
            actions.append(self._active_skill(actor, skill_id))
        return [item for item in actions if item is not None]

    def _active_skill(self, actor, skill_id):
        game = self.game
        definition = game.skill_registry.get(skill_id)
        if definition is None:
            return None
        spec = getattr(definition, "active_spec", None)
        name = getattr(definition, "name", "") or skill_id
        action_id = "skill:%s:%s" % (skill_id, _actor_id(actor))

        if getattr(definition, "needs_local_ui", False):
            # 这个技能的选牌还挂在"只有本地真人界面才有"的通道上：如实标记
            # 不支持，不伪装成所有入口都能执行（也不在本阶段迁移它）。
            return AvailableAction(
                action_id=action_id, kind=ActionType.ACTIVE_SKILL,
                actor_id=_actor_id(actor), source_skill_id=skill_id,
                skill_name=name, enabled=False,
                disabled_reason="这个技能的交互还没有远程化（只能在房主电脑上发动）",
                complete=False, can_submit=False, label=name,
                prompt="", origin=definition)

        if spec is None:
            # 已激活但不是"主动技"（例如由技能自己开窗口的形态）：不描述。
            return None

        inputs = self.skill_inputs(actor, skill_id)
        allowed, reason = game.skills.can_activate(actor, skill_id)
        targets = tuple(_target_candidate(player) for player in inputs["targets"])
        cost = int(inputs.get("cost_cards") or 0)
        variable = bool(inputs.get("variable_cost"))
        cost_candidates = ()
        if cost or variable:
            cost_candidates = tuple(
                _source_candidate(card, self.discovery.zone_of(actor, card), "")
                for card in actor.hand)

        enabled = bool(allowed)
        disabled_reason = "" if enabled else str(reason or "")
        if enabled and inputs["needs_target"] and not targets:
            enabled, disabled_reason = False, "现在没有合法目标"

        return AvailableAction(
            action_id=action_id, kind=ActionType.ACTIVE_SKILL,
            actor_id=_actor_id(actor), source_skill_id=skill_id, skill_name=name,
            source_candidates=cost_candidates,
            min_sources=cost, max_sources=(len(cost_candidates) if variable else cost),
            target_candidates=targets,
            min_targets=(1 if inputs["needs_target"] else 0),
            max_targets=(1 if inputs["needs_target"] else 0),
            target_mode=(TargetMode.CHOOSE if inputs["needs_target"] else TargetMode.NONE),
            enabled=enabled, disabled_reason=disabled_reason,
            complete=not inputs["needs_target"],
            can_submit=bool(enabled) and not inputs["needs_target"],
            cost_prompt=str(getattr(spec, "cost_prompt", "") or ""),
            target_prompt=str(getattr(spec, "target_prompt", "") or ""),
            variable_cost=variable,
            transfer_cards=bool(inputs.get("transfer_cards")),
            label=name,
            detail=str(getattr(spec, "cost_prompt", "") or ""),
            prompt=str(getattr(spec, "target_prompt", "") or ""),
            origin=definition,
        )

    def skill_inputs(self, actor, skill_id):
        """主动技需要的输入（目标候选 + 费用张数）。

        直接转发 ``skills.activation.activation_inputs``：技能输入的规则只有
        那一份，这里不重新判断。
        """

        from src.game.skills.activation import activation_inputs

        definition = self.game.skill_registry.get(skill_id)
        if definition is None:
            return {"needs_target": False, "targets": [], "cost_cards": 0}
        return dict(activation_inputs(definition, self.game, actor))

    # ---- 重铸 ----

    def can_recast(self, actor, card):
        """这张牌现在能不能重铸（能力由 CardEffect 声明，合法性由它判定）。"""

        return self._recast_action(actor, card, None, None) is not None

    def _recast_action(self, actor, card, context, selection):
        if card is None or getattr(card, "_virtual", False):
            return None
        if context is not None and not getattr(context, "is_play", True):
            return None
        effect = self.effect_of(card)
        if effect is None or not getattr(effect, "can_recast", False):
            return None
        if not self._recast_usable(actor, card):
            return None
        return AvailableAction(
            action_id="recast:%s" % _card_id(card),
            kind=ActionType.RECAST, actor_id=_actor_id(actor),
            effective_card_name=str(getattr(card, "name", "") or ""),
            effective_display=_display(card),
            source_candidates=(_source_candidate(
                card, self.discovery.zone_of(actor, card), ""),),
            min_sources=1, max_sources=1,
            target_mode=TargetMode.NONE,
            label="重铸【" + _display(card) + "】",
            detail="置入弃牌堆并摸一张牌",
            origin=card,
        )

    def _recast_usable(self, actor, card):
        from src.game.engine import UseCardAction

        effect = self.effect_of(card)
        if effect is None:
            return False
        action = UseCardAction(actor, card, [], metadata=dict(RECAST_METADATA))
        ok, _reason = effect.can_use(self.game, action)
        return bool(ok)

    # ---- 结束出牌阶段 ----

    def end_play_phase(self, actor):
        """结束出牌阶段这一动作（能不能结束由 ``Game.can_end_play_phase`` 说了算）。"""

        allowed, reason = self.game.can_end_play_phase(actor)
        return AvailableAction(
            action_id="end_play_phase:%s" % _actor_id(actor),
            kind=ActionType.END_PLAY_PHASE, actor_id=_actor_id(actor),
            enabled=bool(allowed), disabled_reason="" if allowed else str(reason),
            complete=True, can_submit=bool(allowed),
            label="结束出牌阶段", detail="进入下一个阶段",
            origin=None,
        )

    # ---- 出牌阶段总表 ----

    def play_phase(self, actor, *, selection=None, include_disabled=True):
        """出牌阶段这名角色的全部可发现行为（纯查询）。"""

        actions = list(self.playable_cards(actor, selection=selection))
        actions.extend(self.card_actions_of_equipment(actor, selection=selection))
        actions.extend(self.active_skills(actor))
        actions.append(self.end_play_phase(actor))
        if not include_disabled:
            actions = [item for item in actions if item.enabled]
        return actions

    def playable_cards(self, actor, *, selection=None):
        """手牌上能做的事（普通使用 / 转换 / 重铸）。"""

        actions = []
        for card in list(getattr(actor, "hand", ()) or ()):
            actions.extend(self.card_actions(actor, card, selection=selection))
        return actions

    def card_actions_of_equipment(self, actor, *, selection=None):
        """自己装备区里可以作为素材的牌（转换可以声明吃装备区的牌）。

        装备区里的牌**不能**被"普通使用"，所以这里只看转换候选；"能不能用"
        仍由 CardActionDiscovery / CardEffect 判定。
        """

        context = self.discovery.play_context(actor)
        equipment = getattr(actor, "equipment", None) or {}
        actions = []
        for slot in ("weapon", "armor", "offensive_horse", "defensive_horse"):
            card = equipment.get(slot)
            if card is None:
                continue
            for option in self.discovery.actions_for_card(actor, card, context):
                if not getattr(option, "is_conversion", False):
                    continue
                actions.append(self._from_option(
                    actor, option, context, selection, slot=slot))
        return actions

    # ---- 内部：包装 ----

    def _from_option(self, actor, option, context, selection, slot=""):
        card = self.effective_card(option)
        profile = self.target_profile(actor, option)
        complete = bool(getattr(option, "complete", True))
        enabled = bool(getattr(option, "enabled", True))
        reason = str(getattr(option, "disabled_reason", "") or "")
        if enabled and not profile.usable:
            enabled, reason = False, profile.reason

        can_submit, blocker = self._submit_state(actor, option, selection, profile,
                                                complete, enabled)
        equipment = getattr(actor, "equipment", None) or {}
        candidates = []
        for item in (getattr(option, "source_cards", ()) or ()):
            item_slot = next(
                (name for name, mounted in equipment.items() if mounted is item), "")
            candidates.append(_source_candidate(
                item, self.discovery.zone_of(actor, item),
                item_slot or slot))
        return AvailableAction(
            action_id=str(getattr(option, "action_id", "")),
            kind=(ActionType.VIEW_AS if getattr(option, "is_conversion", False)
                  else ActionType.PLAY),
            actor_id=_actor_id(actor), context=context.context,
            source_skill_id=str(getattr(option, "skill_id", "") or ""),
            skill_name=str(getattr(option, "skill_name", "") or ""),
            effective_card_name=str(getattr(card, "name", "") or ""),
            effective_display=_display(card) if card is not None else "",
            is_conversion=bool(getattr(option, "is_conversion", False)),
            source_candidates=tuple(candidates),
            min_sources=int(getattr(option, "min_sources", 1) or 0),
            max_sources=int(getattr(option, "max_sources", 1) or 0),
            target_candidates=profile.candidates,
            min_targets=profile.min_targets, max_targets=profile.max_targets,
            target_mode=profile.mode,
            enabled=enabled, disabled_reason=reason,
            complete=complete, can_submit=can_submit, submit_blocker=blocker,
            label=str(getattr(option, "label", "") or ""),
            detail=str(getattr(option, "detail", "") or ""),
            origin=option,
        )

    def _submit_state(self, actor, option, selection, profile, complete, enabled):
        """这份草稿离"能提交"还有多远（不报价、不扣牌，只回答能否提交）。"""

        if not enabled:
            return False, str(getattr(option, "disabled_reason", "") or "现在不能这样使用")
        if not complete:
            missing = int(getattr(option, "min_sources", 1) or 1) - len(
                getattr(option, "source_cards", ()) or ())
            return False, "还差 %d 张素材" % max(1, missing)

        targets = _selected_players(selection)
        if profile.needs_choice:
            if not profile.count_within_bounds(len(targets)):
                return False, "需要选择 %d～%d 个目标" % profile.bounds
        elif profile.mode == TargetMode.SELF and profile.requires_targets:
            targets = [actor]

        sources = tuple(getattr(option, "source_cards", ()) or ())
        ok, reason = self.discovery.validate(
            option, sources=sources,
            targets=[item for item in targets] if (profile.max_targets or targets) else None,
            context=CardActionContext(actor=actor, context=PLAY_CONTEXT))
        return bool(ok), "" if ok else str(reason)


# ==================================================
# 便捷入口
# ==================================================

def available_actions(game, actor=None, *, selection=None, include_disabled=True):
    """模块级入口：``available_actions(game, actor)`` 即出牌阶段的动作总表。"""

    actor = actor or game.player
    return AvailableActions(game).play_phase(
        actor, selection=selection, include_disabled=include_disabled)


# ==================================================
# 小工具（纯读）
# ==================================================

def _actor_id(actor):
    return str(getattr(actor, "player_id", "") or "")


def _card_id(card):
    return str(getattr(card, "id", "") or "")


def _display(card):
    if card is None:
        return ""
    label = getattr(card, "display_name", None)
    if label:
        return str(label)
    return str(getattr(card, "name", "") or "")


def _target_candidate(player):
    return TargetCandidate(
        player_id=_actor_id(player),
        name=str(getattr(player, "name", "") or ""),
        seat=int(getattr(player, "seat", 0) or 0),
    )


def _source_candidate(card, zone, slot):
    return SourceCandidate(
        card_id=_card_id(card),
        name=str(getattr(card, "name", "") or ""),
        label=_display(card),
        zone=str(zone or ""),
        slot=str(slot or ""),
        owner_id="",
    )


def _same_player(candidate, player):
    return str(candidate.player_id) == _actor_id(player)


def _selected_cards(selection):
    if not selection:
        return []
    return list(selection.get("sources") or selection.get("cards") or ())


def _selected_players(selection):
    if not selection:
        return []
    return list(selection.get("targets") or ())
