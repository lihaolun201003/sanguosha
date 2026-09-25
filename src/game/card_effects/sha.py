"""Sha CardEffect."""

from src.game.engine import Event, EventType, FlowResult
from src.game.flows.response_requirement import ResponseRequirement
from src.game.rules import DistanceRule, TargetRule
from .base import CardEffect


class ShaEffect(CardEffect):
    card_name = "SHA"
    category = "basic"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    can_respond = True

    def target_rule_for(self, game, actor, card=None):
        """有"多指定目标"能力时（天义 / 神戟）改用多目标规则。"""

        if game.slash_target_bonus(actor, card) > 0:
            return TargetRule.MULTIPLE
        return TargetRule.SINGLE_OTHER

    def target_bounds_for(self, game, actor, card=None):
        bonus = game.slash_target_bonus(actor, card)
        if bonus > 0:
            return 1, 1 + bonus
        return 1, 1

    def can_use(self, game, action):
        # 技能可以禁止出【杀】（天义拼点没赢）：走统一的规则查询，
        # 不在这里认任何具体武将。
        if game.slash_forbidden(action.actor, action.card):
            return False, "本回合不能使用【杀】。"
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        actor = action.actor
        # Usage limits are per character: every AI tracks its own Sha count,
        # and the turn flow resets the flag when that character's turn starts.
        if actor.sha_used and not action.ignore_usage_limit and not game.can_use_unlimited_sha(actor):
            return False, "本回合已经使用过【杀】。"
        # 多目标时逐个校验距离：只有第一个目标在范围内是不够的。
        # 「无距离限制」走规则层查询（武神），不在这里认具体技能。
        if not game.slash_ignores_distance(actor, action.card):
            for target in action.targets:
                if not DistanceRule.in_attack_range(game, actor, target):
                    return False, "攻击距离不足。"
        return True, ""

    def begin(self, use_flow):
        if use_flow.engine.equipment.before_sha_response(use_flow):
            return FlowResult(use_flow.status, use_flow.result)
        return self.request_shan(use_flow)

    def request_shan(self, use_flow):
        event = use_flow.context.emit(
            Event(
                EventType.CARD_EFFECT_BEFORE,
                source=use_flow.actor,
                target=use_flow.target,
                payload={"card": use_flow.card, "use_flow": use_flow},
            )
        )
        # 啖酪一类技能在这里开窗口决定"这张牌还算不算数"：窗口没答完就继续
        # 要【闪】的话，玩家会发现牌已经结算了、自己才被问到要不要取消它。
        guard = use_flow.guard_child_flows()
        if guard is not None:
            use_flow.stage = "effect_prelude"
            use_flow._effect_prelude_event = event
            return guard
        return self._after_effect_before(use_flow, event)

    def resume_after_child(self, use_flow, result):
        return self._after_effect_before(
            use_flow, getattr(use_flow, "_effect_prelude_event", None))

    def _after_effect_before(self, use_flow, event):
        if event is not None and event.cancelled:
            blocked_by = event.payload.get("blocked_by", "防具")
            use_flow.game.message = (
                use_flow.target.name
                + "的【"
                + str(blocked_by)
                + "】令【"
                + use_flow.card.display_name
                + "】无效。"
            )
            return use_flow.finish(cancelled=True)

        # 这张【杀】是否已经被技能判定成"不可被响应"（铁骑一类）。
        # 只读实体牌上的标记，不认具体技能，也不改变普通杀的行为。
        if use_flow.game.cannot_respond_to(use_flow.card, use_flow.target):
            use_flow.game.message = (
                use_flow.target.name + "无法响应这张【" + use_flow.card.display_name + "】。")
            return use_flow.start_damage()

        # 目标需要连续交出几张【闪】由规则层的 modifier 决定（【无双】= 2）。
        # 这里只按数字循环要牌，不认具体技能。
        use_flow.requirement = ResponseRequirement.for_source(
            use_flow.game, use_flow.actor, use_flow.target, use_flow.card,
            {"SHAN"}, "sha")
        return self._ask_for_shan(use_flow)

    def _ask_for_shan(self, use_flow):
        requirement = use_flow.requirement
        request = requirement.create_request(
            use_flow.engine,
            flow=use_flow,
            source=use_flow.actor,
            responder=use_flow.target,
            prompt="请使用【闪】，或选择不响应",
        )
        use_flow.stage = "waiting_for_shan"
        use_flow.wait(request)
        use_flow.engine.present_or_auto_resolve(request)
        return FlowResult(use_flow.status, use_flow.result)

    def resume(self, use_flow, resolution):
        special = use_flow.engine.equipment.resume_sha(use_flow, resolution)
        if special is not None:
            return special
        requirement = getattr(use_flow, "requirement", None)
        if resolution.card is not None and requirement is not None:
            # 这张【闪】已经由引擎真实移出，第一张不会被退回。
            if not requirement.accept(resolution.card):
                use_flow.game.message = (
                    use_flow.target.name + "使用了【闪】，还需要 "
                    + str(requirement.remaining) + " 张。")
                return self._ask_for_shan(use_flow)
            use_flow.game.message = use_flow.target.name + "使用了【闪】。"
            # 「杀被闪抵消」是一条通用时机：装备（贯石斧 / 青龙偃月刀）与
            # 武将技能（猛进）都从这一个事件接进去，不在杀里认任何具体能力。
            use_flow.context.emit(Event(
                EventType.SHA_DODGED,
                source=use_flow.actor,
                target=use_flow.target,
                payload={"card": use_flow.card, "use_flow": use_flow},
            ))
            handled = use_flow.engine.equipment.on_sha_dodged(use_flow)
            if handled is not None:
                return handled
            return use_flow.finish(cancelled=True)
        return use_flow.start_damage()
