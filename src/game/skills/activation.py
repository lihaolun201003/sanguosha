"""Active-skill activation: validate → pay → settle.

A skill activation is one transactional step.  The caller (UI for the human,
AIController for AI) collects every required input first and then submits a
single ``ActivateSkillAction``; only after validation succeeds are the cost
cards discarded and the ``can_activate`` gate re-checked.

Because nothing is written before the final commit, cancelling a half-finished
activation leaves no ``used`` mark and no discarded cards behind.

费用牌从哪些区域支付由 ``ActiveSkillSpec.allowed_zones`` 声明（默认只有
手牌）。装备区的费用牌走 ``UnequipAtom``——失去装备事件、装备技能卸载、
装备修正移除全部照常发生，绝不从装备字典里硬删。
"""

from src.game.atoms_v2 import MoveCardAtom, UnequipAtom
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


def cost_candidates(game, player, spec):
    """这次发动中，玩家**可以自己挑**的牌（按 ``allowed_zones`` 收集）。

    区域默认只有手牌，所以没声明区域的技能候选还是"全部手牌"，行为不变；
    【制衡】声明了"手牌 + 装备区"，候选里就同时有手牌与装备牌，玩家可以
    只挑手牌、只挑装备、或者混着挑。
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


def resolve_activation(engine, action):
    """执行一次技能发动；返回 (ok, message)。"""

    game = engine.game
    player = action.actor
    definition = game.skill_registry.get(action.skill_id)
    if definition is None or definition.activate is None:
        raise ValueError("unknown active skill: " + str(action.skill_id))

    grant_owners = None
    if definition.grant is not None:
        # 授予型（【黄天】）：技能属于 target，发动的是 action.actor。
        # 目标就是"交给谁"，它必须是一个真的持有该技能、且允许此人发动的角色。
        if action.target is None:
            return False, spec_of(definition).target_prompt
        allowed, reason = game.skills.granted_state(
            action.target, player, definition.id)
        if not allowed:
            game.message = reason
            game.add_log(player.name + " 未能发动【" + definition.name + "】：" + reason)
            return False, reason
        grant_owners = [action.target]
    else:
        # 真正执行前再次校验：状态可能已经变化。
        allowed, reason = game.skills.can_activate(player, action.skill_id)
        if not allowed:
            game.message = reason
            game.add_log(player.name + " 未能发动【" + definition.name + "】：" + reason)
            return False, reason

    inputs = activation_inputs(definition, game, player, grant_owners=grant_owners)
    if inputs["needs_target"] and action.target is None:
        return False, spec_of(definition).target_prompt
    if inputs["needs_target"] and not any(
        action.target is candidate for candidate in inputs["targets"]
    ):
        return False, "目标不合法"

    cards = list(action.cards or ())
    spec = spec_of(definition)
    variable = bool(getattr(spec, "variable_cost", False)) if spec else False
    transfer = bool(getattr(spec, "transfer_cards", False)) if spec else False

    if variable:
        if not cards:
            return False, "至少选择一张牌"
    elif inputs["cost_cards"] and len(cards) < inputs["cost_cards"]:
        return False, (
            spec.cost_prompt if spec is not None else "需要支付更多牌"
        )

    cap = int(getattr(spec, "max_cost_cards", 0) or 0) if spec else 0
    if variable and cap and len(cards) > cap:
        return False, "最多只能选择 %d 张牌" % cap

    keep = bool(getattr(spec, "keep_cards", False)) if spec else False
    payable = [] if keep else (
        list(cards) if variable else list(cards[: inputs["cost_cards"]]))
    zones = tuple(inputs["allowed_zones"])
    # 校验与支付读同一份判据：先全部定位，一张不合法就整体不执行——
    # 绝不会"弃了一部分才发现剩下的不合法"。
    placements = []
    for card in payable:
        placement = cost_placement(player, card, zones)
        if placement is None:
            return False, "选择的牌已经不在可以支付的区域"
        placements.append(placement)

    # 候选校验：玩家能挑的牌由 SkillDef 声明（红桃手牌 / 【闪】或【闪电】 /
    # 手牌与装备牌…）。界面高亮用的是同一份判断，所以走到这里还不合法的，
    # 只可能是绕过界面的提交（远程客户端 / 脚本）——直接拒绝。
    candidates = inputs.get("cost_candidates")
    if candidates is not None and (inputs["cost_cards"] or variable or keep):
        for card in cards:
            if not any(card is item for item in candidates):
                return False, "这张牌不能用于这次发动"

    # 校验全部通过：先支付费用，再交给技能自己结算。
    # 费用牌默认进弃牌堆；【仁德】【黄天】一类技能改成把牌交给目标角色。
    # ``keep_cards`` 的技能不做任何代付——那些牌的去向是技能本身的一部分
    # （交给目标 / 装到装备区 / 当转化素材），引擎替它决定就等于改规则。
    if transfer and action.target is not None:
        destination = action.target.hand
    else:
        destination = game.deck.discard_pile
    paid = []
    for card, (zone, slot) in zip(payable, placements):
        if zone is CostZone.EQUIPMENT:
            # 装备区的费用牌必须先经过失去装备的规则：事件、装备技能卸载、
            # 修正移除都由这个原子负责（硬删会让它们整条消失）。
            engine.context.apply(UnequipAtom(player, slot, destination))
        else:
            engine.context.apply(MoveCardAtom(
                card,
                source=player.hand,
                destination=destination,
            ))
        paid.append(card)

    from src.game.engine.events import Event, EventType

    # targets 只为表现层（指向箭头）提供"谁对谁发动了技能"，不参与任何规则判定。
    skill_targets = [action.target] if action.target is not None else []
    from src.game.interaction_presentation import skill_payload

    game.context.emit(Event(
        EventType.SKILL_TRIGGERED,
        source=player,
        payload=skill_payload(
            game, definition.id, definition.name, targets=skill_targets),
    ))
    # 技能自己知道为什么发动不了（"两张牌的花色必须相同"一类），
    # 那句提示比笼统的"技能未能发动"有用得多：先清掉旧提示，再让技能写。
    game.message = ""
    # ``player`` 始终是**发动这次技能的人**：普通主动技就是拥有者，授予型
    # （【黄天】）是交牌的那名角色，技能拥有者在 ``target`` 里。
    # ``keep_cards`` 的技能拿到玩家选的全部牌（去向由技能自己决定）；
    # 其余技能拿到的是**实际支付成功**的那些牌：摸牌数一类结算按真实支付量
    # 算，不会出现"某张牌没付出去、却按选择时的数量多算一张"。
    result = definition.activate(
        game,
        player,
        target=action.target,
        cards=list(cards) if keep else list(paid),
    )
    if result is False:
        return False, str(game.message or "技能未能发动")
    return True, ""
