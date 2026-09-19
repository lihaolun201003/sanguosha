"""Basic-card effects shared by the human UI and every AI seat."""

from src.game.atoms_v2 import RecoverHpAtom
from src.game.rules import DistanceRule, TargetRule

from .base import CardEffect


class TaoEffect(CardEffect):
    card_name = "TAO"
    category = "basic"
    target_rule = TargetRule.SELF
    min_targets = 1
    max_targets = 1

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        if action.actor.hp >= action.actor.max_hp:
            return False, "你的体力已经是满的。"
        return True, ""

    def begin(self, flow):
        flow.context.apply(RecoverHpAtom(flow.actor, 1))
        flow.game.message = flow.actor.name + "使用了【桃】。"
        flow.game.add_log(flow.actor.name + " 使用【桃】回复 1 点体力")
        return flow.finish(cancelled=False)


class JiuEffect(CardEffect):
    card_name = "JIU"
    category = "basic"
    target_rule = TargetRule.SELF
    min_targets = 1
    max_targets = 1

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        actor = action.actor
        if actor.jiu_used:
            return False, "本回合已经使用过【酒】。"
        if actor.sha_used and not game.can_use_unlimited_sha(actor):
            return False, "本回合已经使用过【杀】，不能再使用【酒】强化杀。"
        if not any(card.name == "SHA" for card in actor.hand):
            return False, "你没有【杀】，现在不能使用【酒】。"
        if not any(
            target is not actor
            and target.alive
            and target.hp > 0
            and DistanceRule.in_attack_range(game, actor, target)
            for target in game.get_alive_players()
        ):
            return False, "当前没有能够使用【杀】攻击到的目标，不能使用【酒】。"
        return True, ""

    def begin(self, flow):
        actor = flow.actor
        actor.jiu_used = True
        actor.wine_buff = True
        actor.wine_sha_required = True
        flow.game.message = actor.name + "使用了【酒】，下一张【杀】伤害 +1。"
        return flow.finish(cancelled=False)


BASIC_EFFECTS = (TaoEffect, JiuEffect)
