"""Active-skill activation: preflight → pay → settle.

A skill activation is one transactional step.  The caller (UI for the human,
AIController for AI, the host for a LAN client) collects every required input
first and then submits a single ``ActivateSkillAction``.

**引擎边界只有一条规则：任何被拒绝的发动都不得造成任何状态变化。**
所以这里把流程切成三段，顺序固定：

    1. ``plan_activation``  纯校验（技能现在能不能发动 / 目标 / 费用）
                            —— 不移动任何牌、不写任何标记
    2. ``pay_cost``         支付费用（手牌走 MoveCardAtom，装备区走 UnequipAtom）
    3. ``settle_activation``发技能事件并执行技能自己的效果

``SKILL_TRIGGERED`` 默认在第 3 步发出。技能若声明
``ActiveSkillSpec.defer_skill_event``（【缔盟】这类目标 / 费用在流程内部收集、
可能整次取消的技能），事件改由技能在**自己确认成立**时调
``emit_skill_triggered`` —— 被取消的发动一条事件都不发，和"不消耗次数、
不动牌"是同一条原则在事件上的补齐。

校验阶段把"这张牌还在不在、区域对不对、是不是同一张牌被提交了两次"全部
问完，支付阶段只按已经定好的位置执行——所以不存在"弃了一部分才发现剩下的
不合法"。校验失败返回规则原因（`False, message`），不抛异常：

* 远程客户端、脚本、未来的 replay 都能直接提交 ``ActivateSkillAction``，
  提交得再离谱也只能被拒绝，不会把房主的引擎打崩。

费用牌从哪些区域支付由 ``ActiveSkillSpec.allowed_zones`` 声明（默认只有
手牌）。装备区的费用牌走 ``UnequipAtom(reason="discard")``——失去装备事件、
装备技能卸载、装备修正移除照常发生，并且"因弃置进入弃牌堆"的语义如实发出
（【落英】一类技能靠它工作）。
"""

from dataclasses import dataclass
from typing import Any, Tuple

from src.game.atoms_v2 import DISCARD_REASON, MoveCardAtom, UnequipAtom
from src.game.conversion import EQUIPMENT_ZONE, HAND_ZONE

from .definitions import CostZone

#: 装备槽的固定顺序（与 ``card_actions.discovery.EQUIPMENT_SLOTS`` 一致）。
#: 这里不导入那个模块：技能费用层不该反过来依赖卡牌动作发现层。
EQUIPMENT_SLOTS = ("weapon", "armor", "offensive_horse", "defensive_horse")


def spec_of(definition):
    """这次发动的输入契约：普通主动技在 ``active_spec``，授予型在 ``grant``。"""

    spec = definition.active_spec
    if spec is None and definition.grant is not None:
        spec = definition.grant.spec
    return spec


def allowed_zones(spec):
    """这次发动的费用牌可以来自哪些区域（没声明就是只有手牌）。"""

    declared = getattr(spec, "allowed_zones", None) if spec is not None else None
    if not declared:
        return (CostZone.HAND,)
    return tuple(CostZone(zone) for zone in declared)


def equipment_slot_of(player, card):
    """这张牌在自己装备区的哪个槽里（不在装备区返回空串）。"""

    if player is None or card is None:
        return ""
    equipment = getattr(player, "equipment", None) or {}
    for slot in EQUIPMENT_SLOTS:
        if equipment.get(slot) is card:
            return slot
    return ""


def cost_placement(player, card, zones):
    """这张费用牌现在在哪里；不在可用区域时返回 ``None``。

    返回 ``(CostZone, slot)``——手牌的 slot 是空串。校验与支付读的是同一份
    判断，"界面能选中"和"引擎会接受"因此永远一致。
    """

    if card is None or player is None:
        return None
    zones = tuple(zones or ())
    if any(item is card for item in (getattr(player, "hand", ()) or ())):
        return (CostZone.HAND, "") if CostZone.HAND in zones else None
    slot = equipment_slot_of(player, card)
    if slot:
        return (CostZone.EQUIPMENT, slot) if CostZone.EQUIPMENT in zones else None
    return None


def source_zone_name(player, card):
    """给描述层用的区域名（与 ``CardActionDiscovery.zone_of`` 同值）。"""

    if any(item is card for item in (getattr(player, "hand", ()) or ())):
        return HAND_ZONE
    if equipment_slot_of(player, card):
        return EQUIPMENT_ZONE
    return ""


def duplicate_cost_card(cards):
    """费用里重复出现的同一张实体牌（没有则返回 ``None``）。

    为什么必须查：``[sha, sha]`` 这种提交在旧的"先全部定位、再全部支付"里
    两次都能定位成功（两次问的都是**同一张还在手牌里的牌**），于是第一次
    支付真的把它弃掉、第二次抛 ``ValueError``——玩家白丢一张牌，技能没发动。
    去重必须在任何移动之前做，不能只靠界面：远程客户端与脚本也会到引擎入口。

    判据是**实体身份**，两个维度都查：

    * 对象身份（``id(card)``）——同一张牌被提交两次；
    * 牌自己的 ``id``——两个对象、同一个牌子（反序列化或恶意构造的 payload）。

    牌 id 由 ``Card`` 自动分配、进程内唯一，所以第二个维度不会误伤合法提交。
    """

    seen_objects = set()
    seen_ids = set()
    for card in cards:
        if card is None:
            continue
        if id(card) in seen_objects:
            return card
        card_id = str(getattr(card, "id", "") or "")
        if card_id and card_id in seen_ids:
            return card
        seen_objects.add(id(card))
        if card_id:
            seen_ids.add(card_id)
    return None


def cost_candidates(game, player, spec):
    """这次发动中，玩家**可以自己挑**的牌（按 ``allowed_zones`` 收集）。

    区域默认只有手牌，所以没声明区域的技能候选还是"全部手牌"，行为不变；
    【制衡】【举荐】声明了"手牌 + 装备区"，候选里就同时有手牌与装备牌，
    玩家可以只挑手牌、只挑装备、或者混着挑。
    """

    if spec is None:
        return []
    zones = allowed_zones(spec)
    candidates = []
    if CostZone.HAND in zones:
        candidates.extend(list(getattr(player, "hand", ()) or ()))
    if CostZone.EQUIPMENT in zones:
        for slot in EQUIPMENT_SLOTS:
            card = player.get_equipment(slot) if player is not None else None
            if card is not None:
                candidates.append(card)
    predicate = getattr(spec, "cost_candidates", None)
    if predicate is None:
        return candidates
    return [card for card in candidates if predicate(game, player, card)]


def activation_inputs(definition, game, player, *, grant_owners=None):
    """该技能当前需要的输入：目标候选与费用牌数量。

    ``grant_owners`` 给出授予型技能（【黄天】）的"交给谁"候选——那些持有该
    技能的角色。费用候选照旧取自**发动者**：交牌的是他，付费用的也是他。
    """

    spec = spec_of(definition)
    if spec is None:
        return {"needs_target": False, "targets": [], "cost_cards": 0}
    if grant_owners is not None:
        targets = list(grant_owners)
    elif spec.needs_target:
        candidates = spec.target_candidates
        if candidates is not None:
            targets = list(candidates(game, player))
        else:
            targets = [
                other for other in game.get_alive_players()
                if other is not player
            ]
    else:
        targets = []
    return {
        "needs_target": bool(spec.needs_target) or grant_owners is not None,
        "targets": targets,
        "cost_cards": int(spec.cost_cards or 0),
        "variable_cost": bool(spec.variable_cost),
        "max_cost_cards": int(spec.max_cost_cards or 0),
        "transfer_cards": bool(spec.transfer_cards),
        "keep_cards": bool(spec.keep_cards),
        # 候选牌物化成列表：本地 UI 直接高亮它们，远程把它压成 id 下发。
        # 两种界面读的是同一个列表，所以"玩家能点的"和"引擎会接受的"永远一致。
        "cost_candidates": cost_candidates(game, player, spec),
        "allowed_zones": tuple(allowed_zones(spec)),
    }


# ==================================================
# 一次发动的执行计划
# ==================================================

@dataclass(frozen=True)
class ActivationPlan:
    """校验**全部**通过之后才存在的执行计划。

    它存在的意义：把"这次发动要做什么"和"做"彻底分开。支付阶段只读这份
    计划，不再查任何状态，也就不存在"付到一半发现不合法"。
    """

    definition: Any
    spec: Any
    player: Any
    target: Any
    zones: Tuple[Any, ...]
    cards: Tuple[Any, ...]                 # 玩家提交的全部牌（keep_cards 用）
    entries: Tuple[Any, ...]               # ((card, zone, slot), ...) 要支付的
    destination: Any
    keep_cards: bool

    @property
    def paid_cards(self):
        return tuple(entry[0] for entry in self.entries)


def plan_activation(engine, action):
    """校验一次技能发动的全部输入；返回 ``(plan, message)``。

    失败时 plan 为 ``None``、message 是规则原因。整个过程**不改任何状态**：
    不移动牌、不写 used、不扣标记、不发事件。
    """

    game = engine.game
    player = action.actor
    definition = game.skill_registry.get(action.skill_id)
    if definition is None or definition.activate is None:
        # 这是编程错误（技能 id 不存在），不是玩家输入问题：照旧抛。
        raise ValueError("unknown active skill: " + str(action.skill_id))

    # ---- 1) 技能本身现在还能不能发动（时机 / 次数 / 标记 / 阵营）----
    grant_owners = None
    if definition.grant is not None:
        # 授予型（【黄天】）：技能属于 target，发动的是 action.actor。
        # 目标就是"交给谁"，它必须是一个真的持有该技能、且允许此人发动的角色。
        if action.target is None:
            return None, spec_of(definition).target_prompt
        allowed, reason = game.skills.granted_state(
            action.target, player, definition.id)
        if not allowed:
            return None, reason
        grant_owners = [action.target]
    else:
        allowed, reason = game.skills.can_activate(player, action.skill_id)
        if not allowed:
            return None, reason

    inputs = activation_inputs(definition, game, player, grant_owners=grant_owners)

    # ---- 2) 目标 ----
    if inputs["needs_target"] and action.target is None:
        return None, spec_of(definition).target_prompt
    if inputs["needs_target"] and not any(
        action.target is candidate for candidate in inputs["targets"]
    ):
        return None, "目标不合法"

    # ---- 3) 张数：固定张数必须**恰好**，可变费用给区间 ----
    cards = list(action.cards or ())
    spec = spec_of(definition)
    variable = bool(getattr(spec, "variable_cost", False)) if spec else False
    transfer = bool(getattr(spec, "transfer_cards", False)) if spec else False
    keep = bool(getattr(spec, "keep_cards", False)) if spec else False
    cost_cards = int(inputs["cost_cards"] or 0)

    if variable:
        if not cards:
            return None, "至少选择一张牌"
    elif len(cards) != cost_cards:
        if len(cards) < cost_cards:
            return None, (spec.cost_prompt if spec is not None else "需要支付更多牌")
        # 多出来的牌以前被 ``cards[:cost_cards]`` 静默截掉：引擎收下了一份
        # 与输入契约不符的载荷，还照常发动。宁可拒绝。
        return None, ("这次发动需要恰好 %d 张牌" % cost_cards
                      if cost_cards else "这次发动不需要选择牌")

    cap = int(getattr(spec, "max_cost_cards", 0) or 0) if spec else 0
    if variable and cap and len(cards) > cap:
        return None, "最多只能选择 %d 张牌" % cap

    payable = [] if keep else (
        list(cards) if variable else list(cards[: cost_cards]))

    # ---- 4) 实体唯一（在任何移动之前）----
    repeated = duplicate_cost_card(cards)
    if repeated is not None:
        return None, "同一张牌不能重复作为费用"

    # ---- 5) 每一张提交的牌：对象、归属、区域、候选资格 ----
    #
    # 审的是 ``cards``（玩家/客户端提交的**全部**牌），不是 ``payable``：
    # ``keep_cards`` 的技能不代付（去向由技能自己决定），但那不等于它的
    # 素材可以不过规则——【乱击】以前就是从这里漏的：提交别人的两张手牌
    # 也能结算出一张【万箭齐发】，引擎还顺手把对方的牌当素材用掉了。
    zones = tuple(inputs["allowed_zones"])
    candidates = inputs.get("cost_candidates")
    check_candidates = candidates is not None and (cost_cards or variable or keep)
    placements = {}
    for card in cards:
        if card is None:
            return None, "选择的牌不存在"
        placement = cost_placement(player, card, zones)
        if placement is None:
            return None, "选择的牌已经不在可以支付的区域"
        if check_candidates and not any(card is item for item in candidates):
            return None, "这张牌不能用于这次发动"
        placements[id(card)] = placement

    # 真正要代付的那些牌（keep_cards 为空）：位置已经在上一步查过，这里只取。
    entries = tuple((card, placements[id(card)][0], placements[id(card)][1])
                    for card in payable)

    # ---- 6) 组合约束（技能自己声明的费用组合校验，可选）----
    validator = getattr(spec, "cost_validator", None) if spec is not None else None
    if callable(validator):
        checked = [item[0] for item in entries] or list(cards)
        ok, reason = _as_result(validator(game, player, checked))
        if not ok:
            return None, reason

    # 费用牌默认进弃牌堆；【仁德】【黄天】一类技能改成把牌交给目标角色。
    # ``keep_cards`` 的技能不做任何代付——那些牌的去向是技能本身的一部分
    # （交给目标 / 装到装备区 / 当转化素材），引擎替它决定就等于改规则。
    if transfer and action.target is not None:
        destination = action.target.hand
    else:
        destination = game.deck.discard_pile

    return ActivationPlan(
        definition=definition,
        spec=spec,
        player=player,
        target=action.target,
        zones=zones,
        cards=tuple(cards),
        entries=entries,
        destination=destination,
        keep_cards=keep,
    ), ""


def _as_result(value):
    """技能谓词的两种返回形态：bool 或 (bool, reason)。"""

    if isinstance(value, tuple):
        return bool(value[0]), str(value[1]) if len(value) > 1 else ""
    return bool(value), ""


def pay_cost(engine, plan):
    """按计划支付费用（只读计划，不再查状态）。

    手牌走 ``MoveCardAtom``，装备区走 ``UnequipAtom(reason="discard")``——
    装备费用因此带上"因弃置进入弃牌堆"的语义（【落英】一类订阅者靠它工作），
    同时照常发出 ``EQUIPMENT_LOST``、卸载装备技能、移除装备修正。
    """

    for card, zone, slot in plan.entries:
        if zone is CostZone.EQUIPMENT:
            engine.context.apply(UnequipAtom(
                plan.player, slot, plan.destination, reason=DISCARD_REASON))
        else:
            engine.context.apply(MoveCardAtom(
                card,
                source=plan.player.hand,
                destination=plan.destination,
            ))


def emit_skill_triggered(engine, definition, player, targets=()):
    """发一次 ``SKILL_TRIGGERED``（"技能发动了"）。

    这是技能事件的**唯一**公共出口，两个调用时机都走它：

    * ``settle_activation`` 在**激活时**发（默认行为，绝大多数技能）；
    * 声明了 ``ActiveSkillSpec.defer_skill_event`` 的技能在**自己确认成立**时
      发——那些技能的目标 / 费用在流程内部收集（【缔盟】取消选目标、付不起
      费用时整次发动都不成立），技能事件不能比"成立"更早发出去。

    ``targets`` 只为表现层（指向箭头）提供"谁对谁发动了技能"，不参与任何
    规则判定；``None`` 会被剔掉，其余照旧整份进 payload。
    """

    game = engine.game
    from src.game.engine.events import Event, EventType

    from src.game.interaction_presentation import skill_payload

    skill_targets = [item for item in (targets or ()) if item is not None]
    game.context.emit(Event(
        EventType.SKILL_TRIGGERED,
        source=player,
        payload=skill_payload(
            game,
            str(getattr(definition, "id", "") or ""),
            str(getattr(definition, "name", "") or ""),
            targets=skill_targets,
        ),
    ))


def settle_activation(engine, plan):
    """发技能事件并执行技能自己的效果（费用已经支付完毕）。"""

    game = engine.game
    definition = plan.definition

    # 技能事件默认就在这里发（"激活即发"）。声明 ``defer_skill_event`` 的技能
    # 把它推迟到自己确认成立的那一刻（技能内部调 ``emit_skill_triggered``）：
    # 它们的目标与费用都在流程里收集，取消 / 付不起时整次发动都不成立，
    # 不能留下"技能发动过"的痕迹。
    spec = plan.spec
    if not bool(getattr(spec, "defer_skill_event", False)):
        emit_skill_triggered(
            engine, definition, plan.player,
            targets=[plan.target] if plan.target is not None else ())
    # 技能自己知道为什么发动不了（"两张牌的花色必须相同"一类），
    # 那句提示比笼统的"技能未能发动"有用得多：先清掉旧提示，再让技能写。
    game.message = ""
    # ``player`` 始终是**发动这次技能的人**：普通主动技就是拥有者，授予型
    # （【黄天】）是交牌的那名角色，技能拥有者在 ``target`` 里。
    # ``keep_cards`` 的技能拿到玩家选的全部牌（去向由技能自己决定）；
    # 其余技能拿到的是**实际支付成功**的那些牌：摸牌数一类结算按真实支付量
    # 算，不会出现"某张牌没付出去、却按选择时的数量多算一张"。
    return definition.activate(
        game,
        plan.player,
        target=plan.target,
        cards=list(plan.cards) if plan.keep_cards else list(plan.paid_cards),
    )


def resolve_activation(engine, action):
    """执行一次技能发动；返回 ``(ok, message)``。

    顺序固定：**校验全部 → 支付全部 → 结算**。校验拒绝不产生任何状态变化；
    支付之后技能自己的效果失败（返回 False）算"费用已付、效果为空"，与
    "费用失败"不是一回事——技能实现用返回 False 表达的是后者之外的情况。
    """

    plan, reason = plan_activation(engine, action)
    if plan is None:
        game = engine.game
        name = getattr(engine.game.skill_registry.get(action.skill_id),
                       "name", action.skill_id)
        game.message = reason
        game.add_log(action.actor.name + " 未能发动【" + str(name) + "】：" + reason)
        return False, reason

    pay_cost(engine, plan)
    result = settle_activation(engine, plan)
    if result is False:
        game = engine.game
        return False, str(game.message or "技能未能发动")
    return True, ""
