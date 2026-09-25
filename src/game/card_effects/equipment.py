"""Equipment CardEffect: playing a card into its own equipment slot."""

from src.actions import CallbackAction

from src.game.atoms_v2 import EquipCardAtom, MoveCardAtom, UnequipAtom
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
        if old is not None:
            # 旧装备先按「失去装备」离场：回血 / 枭姬一类由事件订阅者处理。
            flow.context.apply(UnequipAtom(actor, card.subtype))
            flow.context.apply(
                MoveCardAtom(old, destination=flow.game.deck.discard_pile)
            )
        flow.context.apply(EquipCardAtom(actor, card))

        # 装备牌进了装备槽：出牌动画留在桌面上的展示副本必须收掉，否则它会
        # 一直停在桌子中央。出牌动画是异步的（播完才把牌放上桌面），所以
        # 这里也排一个回调，等那份副本出现之后再移除。
        flow.game.actions.add(
            CallbackAction(lambda equipped=card: flow.game.remove_table_card(equipped))
        )

        flow.game.message = actor.name + "装备了【" + card.display_name + "】。"
        flow.game.add_log(actor.name + " 装备【" + card.display_name + "】")
        return flow.finish(cancelled=False)
