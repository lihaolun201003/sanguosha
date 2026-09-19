"""Equipment CardEffect: playing a card into its own equipment slot."""

from src.game.atoms_v2 import EquipCardAtom, MoveCardAtom, RecoverHpAtom
from src.game.rules import TargetRule

from .base import CardEffect


class EquipEffect(CardEffect):
    """Any equipment card is used on its owner and occupies a fixed slot."""

    card_category = "equipment"
    category = "equipment"
    target_rule = TargetRule.SELF
    min_targets = 0
    max_targets = 0

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        if action.card.subtype not in action.actor.equipment:
            return False, "这张牌没有可装备的位置。"
        return True, ""

    def begin(self, flow):
        card = flow.card
        actor = flow.actor
        old = actor.get_equipment(card.subtype)

        # 装备牌从处理区进入装备槽，不进入弃牌堆。
        flow.context.apply(
            MoveCardAtom(card, source=flow.game.processing_zone, destination=None)
        )
        flow.keep_processing_card = True
        flow.context.apply(EquipCardAtom(actor, card))

        if old is not None:
            flow.context.apply(
                MoveCardAtom(old, destination=flow.game.deck.discard_pile)
            )
            if old.name == "BAIYIN" and actor.hp < actor.max_hp:
                flow.context.apply(RecoverHpAtom(actor, 1))

        flow.game.message = actor.name + "装备了【" + card.display_name + "】。"
        flow.game.add_log(actor.name + " 装备【" + card.display_name + "】")
        return flow.finish(cancelled=False)
