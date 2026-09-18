"""First playable standard trick-card effects."""

from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom, TransferEquipmentAtom, SetChainedAtom
from src.game.engine import FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.rules import DistanceRule, TargetRule

from .base import CardEffect


class WuzhongEffect(CardEffect):
    card_name = "WUZHONG"
    target_rule = TargetRule.SELF
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def begin(self, flow):
        flow.context.apply(DrawCardsAtom(flow.actor, 2))
        flow.game.message = flow.actor.name + "使用【无中生有】摸两张牌。"
        return flow.finish(cancelled=False)


class _ChooseTargetCardEffect(CardEffect):
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    destination_is_actor = False

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        target = list(action.targets)[0]
        if not target.hand and not any(target.equipment.values()):
            return False, "目标没有可选择的牌。"
        if self.distance_limit is not None and not DistanceRule.in_range(
            game, action.actor, target, self.distance_limit
        ):
            return False, "目标距离过远。"
        return True, ""

    def begin(self, flow):
        target = flow.targets[0]
        candidates = list(target.hand) + [card for card in target.equipment.values() if card]
        request = flow.engine.pending.create(
            PendingRequestType.SELECT_CARDS,
            source=flow.actor, target=flow.actor,
            prompt="请选择" + target.name + "区域内的一张牌",
            owner_flow=flow, min_cards=1, max_cards=1,
            request_context={"reason": self.card_name.lower(), "candidates": candidates, "zone_owner": target},
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        card = resolution.cards[0]
        target = flow.targets[0]
        source = target.hand
        if not any(item is card for item in source):
            for slot, equipped in target.equipment.items():
                if equipped is card:
                    target.remove_equipment(slot)
                    source = None
                    break
        destination = flow.actor.hand if self.destination_is_actor else flow.game.deck.discard_pile
        flow.context.apply(MoveCardAtom(card, source=source, destination=destination))
        flow.game.message = flow.actor.name + "使用【" + flow.card.display_name + "】获得一张牌。" if self.destination_is_actor else flow.actor.name + "使用【过河拆桥】弃置一张牌。"
        return flow.finish(cancelled=False)


class GuoheEffect(_ChooseTargetCardEffect):
    card_name = "GUOHE"
    cancellable_by_wuxie = True


class ShunshouEffect(_ChooseTargetCardEffect):
    card_name = "SHUNSHOU"
    destination_is_actor = True
    distance_limit = 1
    cancellable_by_wuxie = True


class DuelEffect(CardEffect):
    card_name = "JUEDOU"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    can_respond = True
    cancellable_by_wuxie = True

    def begin(self, flow):
        flow.effect_state = {"responder": flow.targets[0], "other": flow.actor}
        return self._request(flow)

    def _request(self, flow):
        responder = flow.effect_state["responder"]
        request = flow.engine.pending.create(
            PendingRequestType.RESPOND_CARD, source=flow.actor, target=responder,
            prompt="【决斗】：请打出【杀】，或选择不响应", owner_flow=flow,
            allowed_cards={"SHA"}, min_cards=0, max_cards=1,
            request_context={"reason": "duel", "card": flow.card},
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        if resolution.card is not None:
            state = flow.effect_state
            state["responder"], state["other"] = state["other"], state["responder"]
            return self._request(flow)
        loser = resolution.actor
        winner = flow.effect_state["other"]
        damage = DamageFlow(flow.engine, DamageContext(winner, loser, 1, card=flow.card), on_complete=lambda _: flow.finish(cancelled=False))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            flow.stage = "effect_child"
            return flow.wait(damage)
        return flow.finish(cancelled=False)


class _MassResponseEffect(CardEffect):
    target_rule = TargetRule.ALL_OTHERS
    response_name = None
    cancellable_by_wuxie = True
    per_target_wuxie = True

    def begin(self, flow):
        flow.effect_state = {"index": 0}
        return self._next(flow)

    def _next(self, flow):
        index = flow.effect_state["index"]
        if index >= len(flow.targets):
            return flow.finish(cancelled=False)
        target = flow.targets[index]
        if not target.alive or target.hp <= 0:
            flow.effect_state["index"] += 1
            return self._next(flow)
        from src.game.flows.wuxie import WuxieResponseChain
        flow.stage = "effect_wuxie"
        chain = WuxieResponseChain(
            flow.engine, flow.actor, flow.card, [target],
            lambda nullified: self._after_target_wuxie(flow, nullified),
        )
        flow.wait(chain)
        chain.start()
        return flow.current_result()

    def _after_target_wuxie(self, flow, nullified):
        if flow.status is FlowStatus.WAITING:
            flow.status = FlowStatus.RUNNING
            flow.pending_request = None
        if nullified:
            flow.effect_state["index"] += 1
            return self._next(flow)
        return self._request_response(flow)

    def _request_response(self, flow):
        target = flow.targets[flow.effect_state["index"]]
        request = flow.engine.pending.create(
            PendingRequestType.RESPOND_CARD, source=flow.actor, target=target,
            prompt="【" + flow.card.display_name + "】：请打出响应牌，或选择不响应",
            owner_flow=flow, allowed_cards={self.response_name}, min_cards=0, max_cards=1,
            request_context={"reason": self.card_name.lower(), "card": flow.card},
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        if resolution.card is not None:
            flow.effect_state["index"] += 1
            return self._next(flow)
        target = resolution.actor
        damage = DamageFlow(flow.engine, DamageContext(flow.actor, target, 1, card=flow.card), on_complete=lambda _: self._after_damage(flow))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            flow.stage = "effect_child"
            return flow.wait(damage)
        return flow.current_result()

    def _after_damage(self, flow):
        if flow.status is FlowStatus.COMPLETED:
            return flow.current_result()
        flow.effect_state["index"] += 1
        return self._next(flow)


class NanmanEffect(_MassResponseEffect):
    card_name = "NANMAN"
    response_name = "SHA"
    cancellable_by_wuxie = True


class WanjianEffect(_MassResponseEffect):
    card_name = "WANJIAN"
    response_name = "SHAN"
    cancellable_by_wuxie = True


class TaoyuanEffect(CardEffect):
    card_name = "TAOYUAN"
    target_rule = TargetRule.ALL_PLAYERS
    cancellable_by_wuxie = True

    def begin(self, flow):
        for target in flow.targets:
            if target.alive and target.hp > 0:
                flow.context.apply(RecoverHpAtom(target, 1))
        flow.game.message = "【桃园结义】令所有存活角色回复体力。"
        return flow.finish(cancelled=False)


class WuguEffect(CardEffect):
    card_name = "WUGU"
    target_rule = TargetRule.ALL_PLAYERS
    cancellable_by_wuxie = True

    def begin(self, flow):
        pool = flow.game.public_card_pool
        pool.clear()
        for _ in flow.targets:
            card = flow.game.deck.draw()
            if card is not None:
                pool.append(card)
        flow.effect_state = {"order": list(flow.targets), "index": 0}
        return self._choose(flow)

    def _choose(self, flow):
        pool = flow.game.public_card_pool
        index = flow.effect_state["index"]
        order = flow.effect_state["order"]
        while index < len(order) and order[index].hp <= 0:
            index += 1
        flow.effect_state["index"] = index
        if index >= len(order) or not pool:
            for card in list(pool):
                flow.context.apply(MoveCardAtom(card, source=pool, destination=flow.game.deck.discard_pile))
            return flow.finish(cancelled=False)
        selector = order[index]
        request = flow.engine.pending.create(
            PendingRequestType.SELECT_CARDS, source=flow.actor, target=selector,
            prompt="【五谷丰登】：请选择一张公共牌", owner_flow=flow,
            min_cards=1, max_cards=1,
            request_context={"reason": "wugu", "candidates": list(pool), "zone": "public_pool", "zone_owner": selector},
        )
        flow.stage = "effect_waiting"; flow.wait(request); flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        card = resolution.cards[0]
        flow.context.apply(MoveCardAtom(card, source=flow.game.public_card_pool, destination=resolution.actor.hand))
        flow.effect_state["index"] += 1
        return self._choose(flow)


class WuxieEffect(CardEffect):
    card_name = "WUXIE"
    target_rule = TargetRule.NO_TARGET

    def can_use(self, game, action):
        return False, "【无懈可击】只能在锦囊响应链中使用。"


class JiedaoEffect(CardEffect):
    card_name = "JIEDAO"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        if list(action.targets)[0].get_equipment("weapon") is None:
            return False, "目标没有武器。"
        return True, ""

    def begin(self, flow):
        wielder = flow.targets[0]
        candidates = [p for p in flow.game.get_alive_players() if p is not wielder and p is not flow.actor]
        victim = flow.action.metadata.get("jiedao_victim")
        if victim is None and candidates:
            victim = candidates[0]
        sha = next((card for card in wielder.hand if card.name == "SHA"), None)
        if victim is not None and sha is not None and DistanceRule.in_attack_range(flow.game, wielder, victim):
            flow.wait({"reason": "jiedao_sha"})
            flow.engine.submit(__import__("src.game.engine", fromlist=["UseCardAction"]).UseCardAction(wielder, sha, [victim], ignore_usage_limit=True, on_complete=lambda _: flow.finish(cancelled=False)))
            return flow.current_result()
        flow.context.apply(TransferEquipmentAtom(wielder, "weapon", flow.actor.hand))
        return flow.finish(cancelled=False)


class _DelayedTrickEffect(CardEffect):
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if not valid:
            return valid, message
        target = list(action.targets)[0]
        if any(card.name == action.card.name for card in target.judgement_zone):
            return False, "目标判定区已有同名延时锦囊。"
        return True, ""

    def begin(self, flow):
        flow.context.apply(MoveCardAtom(flow.card, source=flow.game.processing_zone, destination=flow.targets[0].judgement_zone))
        flow.keep_processing_card = True
        return flow.finish(cancelled=False)


class LebuEffect(_DelayedTrickEffect):
    card_name = "LEBU"


class BingliangEffect(_DelayedTrickEffect):
    card_name = "BINGLIANG"
    distance_limit = 1

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if valid and not DistanceRule.in_range(game, action.actor, list(action.targets)[0], 1):
            return False, "目标距离超过 1。"
        return valid, message


class ShandianEffect(_DelayedTrickEffect):
    card_name = "SHANDIAN"
    target_rule = TargetRule.SELF


class HuogongEffect(CardEffect):
    card_name = "HUOGONG"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        return (False, "目标没有手牌。") if valid and not list(action.targets)[0].hand else (valid, message)

    def begin(self, flow):
        target = flow.targets[0]
        request = flow.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=target, prompt="【火攻】：展示一张手牌", owner_flow=flow, min_cards=1, max_cards=1, request_context={"reason": "huogong_reveal", "candidates": list(target.hand), "zone_owner": target})
        flow.effect_state = {}; flow.stage = "effect_waiting"; flow.wait(request); flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        if "suit" not in flow.effect_state:
            revealed = resolution.cards[0]
            flow.effect_state["suit"] = revealed.suit
            flow.game.revealed_card = revealed
            candidates = [card for card in flow.actor.hand if card.suit == revealed.suit]
            if not candidates:
                flow.game.revealed_card = None
                return flow.finish(cancelled=False)
            request = flow.engine.pending.create(PendingRequestType.SELECT_CARDS, source=flow.actor, target=flow.actor, prompt="弃置一张与展示牌同花色的手牌", owner_flow=flow, min_cards=1, max_cards=1, request_context={"reason": "huogong_discard", "candidates": candidates, "zone_owner": flow.actor})
            flow.stage = "effect_waiting"; flow.wait(request); flow.engine.present_or_auto_resolve(request)
            return flow.current_result()
        flow.context.apply(MoveCardAtom(resolution.cards[0], source=flow.actor.hand, destination=flow.game.deck.discard_pile))
        flow.game.revealed_card = None
        damage = DamageFlow(flow.engine, DamageContext(flow.actor, flow.targets[0], 1, nature="fire", card=flow.card), on_complete=lambda _: flow.finish(cancelled=False))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            return flow.wait(damage)
        return flow.current_result()


class TiesuoEffect(CardEffect):
    card_name = "TIESUO"
    target_rule = TargetRule.MULTIPLE
    min_targets = 1
    max_targets = 2
    cancellable_by_wuxie = True

    def can_use(self, game, action):
        if action.metadata.get("recast"):
            if not any(card is action.card for card in action.actor.hand):
                return False, "牌不在手牌中。"
            return True, ""
        return super().can_use(game, action)

    def begin(self, flow):
        if flow.action.metadata.get("recast"):
            flow.context.apply(DrawCardsAtom(flow.actor, 1))
            return flow.finish(cancelled=False)
        for target in flow.targets:
            flow.context.apply(SetChainedAtom(target, not target.chained))
            flow.context.emit(__import__("src.game.engine", fromlist=["Event"]).Event(__import__("src.game.engine", fromlist=["EventType"]).EventType.CHAIN_STATE_CHANGED, source=flow.actor, target=target, payload={"chained": target.chained}))
        return flow.finish(cancelled=False)


TRICK_EFFECTS = (
    WuzhongEffect, GuoheEffect, ShunshouEffect, DuelEffect, NanmanEffect,
    WanjianEffect, TaoyuanEffect, WuguEffect, WuxieEffect, JiedaoEffect,
    LebuEffect, ShandianEffect, HuogongEffect, TiesuoEffect, BingliangEffect,
)
