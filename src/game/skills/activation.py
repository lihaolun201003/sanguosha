"""Active-skill activation: validate → pay → settle.

A skill activation is one transactional step.  The caller (UI for the human,
AIController for AI) collects every required input first and then submits a
single ``ActivateSkillAction``; only after validation succeeds are the cost
cards discarded and the ``can_activate`` gate re-checked.

Because nothing is written before the final commit, cancelling a half-finished
activation leaves no ``used`` mark and no discarded cards behind.
"""

from src.game.atoms_v2 import MoveCardAtom


def activation_inputs(definition, game, player):
    """该技能当前需要的输入：目标候选与费用牌数量。"""

    spec = definition.active_spec
    if spec is None:
        return {"needs_target": False, "targets": [], "cost_cards": 0}
    targets = []
    if spec.needs_target:
        candidates = spec.target_candidates
        if candidates is not None:
            targets = list(candidates(game, player))
        else:
            targets = [
                other for other in game.get_alive_players()
                if other is not player
            ]
    return {
        "needs_target": bool(spec.needs_target),
        "targets": targets,
        "cost_cards": int(spec.cost_cards or 0),
        "variable_cost": bool(spec.variable_cost),
        "transfer_cards": bool(spec.transfer_cards),
    }


def resolve_activation(engine, action):
    """执行一次技能发动；返回 (ok, message)。"""

    game = engine.game
    player = action.actor
    definition = game.skill_registry.get(action.skill_id)
    if definition is None or definition.activate is None:
        raise ValueError("unknown active skill: " + str(action.skill_id))

    # 真正执行前再次校验：状态可能已经变化。
    allowed, reason = game.skills.can_activate(player, action.skill_id)
    if not allowed:
        game.message = reason
        game.add_log(player.name + " 未能发动【" + definition.name + "】：" + reason)
        return False, reason

    inputs = activation_inputs(definition, game, player)
    if inputs["needs_target"] and action.target is None:
        return False, definition.active_spec.target_prompt
    if inputs["needs_target"] and not any(
        action.target is candidate for candidate in inputs["targets"]
    ):
        return False, "目标不合法"

    cards = list(action.cards or ())
    spec = definition.active_spec
    variable = bool(getattr(spec, "variable_cost", False)) if spec else False
    transfer = bool(getattr(spec, "transfer_cards", False)) if spec else False

    if variable:
        if not cards:
            return False, "至少选择一张手牌"
    elif inputs["cost_cards"] and len(cards) < inputs["cost_cards"]:
        return False, (
            definition.active_spec.cost_prompt
            if definition.active_spec is not None else "需要弃置更多手牌"
        )

    payable = list(cards) if variable else list(cards[: inputs["cost_cards"]])
    for card in payable:
        if not any(item is card for item in player.hand):
            return False, "选择的牌已经不在手牌中"

    # 校验全部通过：先支付费用，再交给技能自己结算。
    # 费用牌默认进弃牌堆；【仁德】一类技能改成把牌交给目标角色。
    if transfer and action.target is not None:
        destination = action.target.hand
    else:
        destination = game.deck.discard_pile
    for card in payable:
        engine.context.apply(MoveCardAtom(
            card,
            source=player.hand,
            destination=destination,
        ))

    from src.game.engine.events import Event, EventType

    # targets 只为表现层（指向箭头）提供"谁对谁发动了技能"，不参与任何规则判定。
    skill_targets = [action.target] if action.target is not None else []
    game.context.emit(Event(
        EventType.SKILL_TRIGGERED,
        source=player,
        payload={
            "skill_id": definition.id,
            "skill_name": definition.name,
            "targets": skill_targets,
        },
    ))
    result = definition.activate(
        game,
        player,
        target=action.target,
        cards=cards,
    )
    if result is False:
        return False, "技能未能发动"
    return True, ""
