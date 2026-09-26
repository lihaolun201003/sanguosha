"""First playable standard trick-card effects."""

from src.card import DISPLAY_NAMES
from src.game.atoms_v2 import DrawCardsAtom, MoveCardAtom, RecoverHpAtom, TransferEquipmentAtom, SetChainedAtom, UnequipAtom
from src.game.engine import FlowStatus
from src.game.engine.pending import PendingRequestType
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.flows.response_requirement import ResponseRequirement
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
        limit = self.distance_limit_for(game, action.actor, action.card)
        if (
            limit is not None
            and not game.ignores_trick_range(action.actor)
            and not DistanceRule.in_range(game, action.actor, target, limit)
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
            # 装备区的牌统一走 UnequipAtom 离场：失去装备事件由它发出。
            for slot, equipped in target.equipment.items():
                if equipped is card:
                    flow.context.apply(UnequipAtom(target, slot))
                    source = None
                    break
        destination = flow.actor.hand if self.destination_is_actor else flow.game.deck.discard_pile
        flow.context.apply(MoveCardAtom(card, source=source, destination=destination))
        # 展示被拿走 / 被弃置的那张牌并停留片刻，让真人看清发生了什么。
        flow.engine.show_taken_card(
            card, target, flow.actor, to_hand=self.destination_is_actor)
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
    """决斗：双方轮流打出【杀】，先交不出来的一方受到 1 点伤害。

    每一轮"需要交出几张【杀】"由**响应者的对手**决定（RESPONSE_COUNT
    modifier，例如无双 = 2）。所以吕布无论主动使用决斗还是被决斗，他的
    对手每轮都要连续打出两张【杀】，而吕布自己每轮仍然只要一张。
    """

    card_name = "JUEDOU"
    target_rule = TargetRule.SINGLE_OTHER
    min_targets = max_targets = 1
    can_respond = True
    cancellable_by_wuxie = True

    def begin(self, flow):
        flow.effect_state = {"responder": flow.targets[0], "other": flow.actor}
        return self._open_round(flow)

    def _open_round(self, flow):
        """开始一轮：响应者换人，需求按对手的 modifier 重新计算。"""

        state = flow.effect_state
        responder = state["responder"]
        opponent = state["other"]
        state["requirement"] = ResponseRequirement.for_source(
            flow.game, opponent, responder, flow.card, {"SHA"}, "duel")
        return self._ask(flow)

    def _ask(self, flow):
        requirement = flow.effect_state["requirement"]
        responder = flow.effect_state["responder"]
        request = requirement.create_request(
            flow.engine,
            flow=flow,
            source=flow.actor,
            responder=responder,
            prompt="【决斗】：请打出【杀】，或选择不响应",
        )
        flow.stage = "effect_waiting"
        flow.wait(request)
        flow.engine.present_or_auto_resolve(request)
        return flow.current_result()

    def resume(self, flow, resolution):
        state = flow.effect_state
        requirement = state["requirement"]
        if resolution.card is not None:
            # 这一张【杀】已经由引擎真实移出手牌：即使下一张交不出来，
            # 也不会退回。
            if not requirement.accept(resolution.card):
                return self._ask(flow)
            state["responder"], state["other"] = state["other"], state["responder"]
            return self._open_round(flow)
        loser = resolution.actor
        winner = state["other"]
        damage = DamageFlow(flow.engine, DamageContext(winner, loser, 1, card=flow.card), on_complete=lambda _: flow.finish(cancelled=False))
        result = damage.start()
        if result.status is FlowStatus.WAITING:
            flow.stage = "effect_child"
            return flow.wait(damage)
        return flow.finish(cancelled=False)


class _MassResponseEffect(CardEffect):
    """群体锦囊（南蛮入侵 / 万箭齐发）：逐目标要求响应牌。

    生命周期刻意分成两段，二者不可混用：

    * ``TRICK_NEGATION_WINDOW``——【无懈可击】窗口。它属于**锦囊本身**，
      由 ``UseCardFlow`` 在效果开始之前统一开启一次，所有目标共用同一条
      无懈链（无懈套无懈照常反转）。被抵消则整张牌结束。
    * ``CARD_RESPONSE_REQUIREMENT``——效果开始之后逐个目标要求
      【杀】/【闪】。这一段只推进目标下标，**绝不再打开无懈链**。

    因此这里的 ``cancellable_by_wuxie`` 仍然为真（锦囊本身可被无懈），
    但效果阶段自身不产生任何无懈请求。
    """

    target_rule = TargetRule.ALL_OTHERS
    response_name = None
    cancellable_by_wuxie = True
    sequential_targets = True

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
        return self._request_response(flow)

    def _request_response(self, flow):
        target = flow.targets[flow.effect_state["index"]]
        display = DISPLAY_NAMES.get(self.response_name, self.response_name)
        request = flow.engine.pending.create(
            PendingRequestType.RESPOND_CARD, source=flow.actor, target=target,
            prompt="【" + flow.card.display_name + "】：请打出【" + display + "】，或选择不响应",
            owner_flow=flow, allowed_cards={self.response_name}, min_cards=0, max_cards=1,
            request_context={
                "reason": self.card_name.lower(),
                "card": flow.card,
                # 逐目标响应请求：UI 据此一次只展示当前目标的箭头与提示。
                "sequential_targets": True,
            },
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
        # 伤害来源走规则层查询：默认是使用这张锦囊的角色，【祸首】一类能力
        # 可以把它改写成别人（"你是任何【南蛮入侵】造成伤害的来源"）。
        damage_source = flow.game.trick_source(flow.actor, flow.card)
        damage = DamageFlow(flow.engine, DamageContext(damage_source, target, 1, card=flow.card), on_complete=lambda _: self._after_damage(flow))
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
        # 进判定区的是**实体牌**：View-As 用出来的延时锦囊（徐晃【断粮】
        # 把黑色装备牌当【兵粮寸断】）本身是虚拟牌，它在任何区域里都不存在，
        # 直接移它会在原子层抛 "card is no longer in the expected source zone"。
        moved = False
        for card in flow.material_cards:
            if not any(item is card for item in flow.game.processing_zone):
                continue
            flow.context.apply(MoveCardAtom(
                card, source=flow.game.processing_zone,
                destination=flow.targets[0].judgement_zone))
            moved = True
        if not moved:
            # 素材已经不在处理区（被技能取走一类）：这张延时锦囊不落区，
            # 按普通锦囊收尾，不制造半截状态。
            flow.game.add_log("【%s】没有可用的实体牌，未置入判定区。" % flow.card.display_name)
            return flow.finish(cancelled=False)
        flow.keep_processing_card = True
        # 牌已经真实进入判定区：出牌动画留在桌面上的展示副本必须收掉，
        # 否则它会一直停在中央（同一张牌不允许有两个视觉位置）。出牌动画
        # 是异步的（播完才把牌放上桌面），所以等那份副本出现之后再移除。
        from src.actions import CallbackAction

        flow.game.actions.add(
            CallbackAction(lambda placed=flow.card: flow.game.remove_table_card(placed))
        )
        return flow.finish(cancelled=False)


class LebuEffect(_DelayedTrickEffect):
    card_name = "LEBU"


class BingliangEffect(_DelayedTrickEffect):
    card_name = "BINGLIANG"
    distance_limit = 1

    def can_use(self, game, action):
        valid, message = super().can_use(game, action)
        if (
            valid
            and not game.ignores_trick_range(action.actor)
            and not DistanceRule.in_range(game, action.actor, list(action.targets)[0], 1)
        ):
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
    # 铁索连环可以重铸（置入弃牌堆并摸一张牌）：本地 UI 的"连环／重铸"二选一、
    # AI 的兜底分支、远程控制器下发的重铸选项都读这个声明。
    can_recast = True

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
