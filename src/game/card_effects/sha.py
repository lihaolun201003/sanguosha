"""Sha CardEffect."""

from src.game.engine import Event, EventType, FlowResult
from src.game.engine.pending import PendingRequestType
from src.game.rules import DistanceRule, TargetRule
from .base import CardEffect


class ShaEffect(CardEffect):
    card_name = "SHA"
    category = "basic"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    can_respond = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        if game.phase not in ("play", "enemy") and not action.ignore_usage_limit:
            return False, "当前不是出牌阶段。"
        if action.actor is game.player and game.sha_used and not action.ignore_usage_limit and not game.can_use_unlimited_sha(action.actor):
            return False, "本回合已经使用过【杀】。"
        if not DistanceRule.in_attack_range(game, action.actor, list(action.targets)[0]):
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

        if event.cancelled:
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

        request = use_flow.engine.pending.create(
            PendingRequestType.RESPOND_CARD,
            source=use_flow.actor,
            target=use_flow.target,
            prompt="请使用【闪】，或选择不响应",
            owner_flow=use_flow,
            allowed_cards={"SHAN"},
            min_cards=0,
            max_cards=1,
            request_context={
                "reason": "sha",
                "card": use_flow.card,
            },
        )
        use_flow.stage = "waiting_for_shan"
        use_flow.wait(request)
        use_flow.engine.present_or_auto_resolve(request)
        return FlowResult(use_flow.status, use_flow.result)

    def resume(self, use_flow, resolution):
        special = use_flow.engine.equipment.resume_sha(use_flow, resolution)
        if special is not None:
            return special
        if resolution.card is not None:
            use_flow.game.message = use_flow.target.name + "使用了【闪】。"
            handled = use_flow.engine.equipment.on_sha_dodged(use_flow)
            if handled is not None:
                return handled
            return use_flow.finish(cancelled=True)
        return use_flow.start_damage()
