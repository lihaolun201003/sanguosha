"""Information-safe free-for-all AI controller.

The AI submits GameActions exactly like the human seat does, so every rule
stays in the engine.  It may read other characters' HP, hand *count*,
equipment and judgement zone, but never the contents of another player's hand.
"""

import random

from src.actions import CallbackAction

from src.game.atoms_v2 import MoveCardAtom
from src.game.engine import (
    ChooseOptionAction,
    ConfirmPendingAction,
    PassPendingAction,
    RespondCardAction,
    SelectCardsAction,
    UseCardAction,
)
from src.game.engine.pending import PendingRequestType
from src.game.rules import TargetRule, target_candidates

from .base import PlayerController


# 自由混战没有队友：AI 只救自己。
SELF_RESCUE_NAMES = ("TAO", "JIU")
# 对全场（含自己）有利的牌，AI 不会用无懈可击去抵消。
BENEFICIAL_TRICKS = {"WUZHONG", "TAOYUAN", "WUGU"}


class AIController(PlayerController):

    MAX_CARDS_PER_TURN = 10

    CARD_VALUES = {
        "TAO": 10,
        "JIU": 6,
        "SHA": 6,
        "SHAN": 5,
        "WUXIE": 8,
        "WUZHONG": 7,
        "GUOHE": 6,
        "SHUNSHOU": 8,
        "JUEDOU": 5,
        "NANMAN": 6,
        "WANJIAN": 6,
        "TAOYUAN": 7,
        "WUGU": 7,
        "JIEDAO": 4,
        "LEBU": 7,
        "SHANDIAN": 5,
        "HUOGONG": 6,
        "TIESUO": 4,
        "BINGLIANG": 7,
    }

    def __init__(self, game, player, rng=None):
        super().__init__(game, player)
        self.rng = rng if rng is not None else random.Random()
        self._cards_played = 0
        self._failed_cards = set()

    # ==================================================
    # 估值与信息边界
    # ==================================================

    def card_value(self, card):
        """估值只用于自己的手牌与全场公开牌。"""

        if card.name in self.CARD_VALUES:
            return self.CARD_VALUES[card.name]
        if card.category == "equipment":
            if card.subtype == "weapon":
                return 4 + card.attack_range
            return 5
        return 4

    def score_target(self, target, card):
        score = (target.max_hp - target.hp) * 20 - target.hp * 3
        if card.name in {"GUOHE", "SHUNSHOU"}:
            score += len(target.hand) * 4
            score += sum(value is not None for value in target.equipment.values()) * 3
        if card.name == "HUOGONG" and target.hand:
            score += 10
        if card.name == "SHA":
            score += len(target.hand) * 1.5 - target.hp * 0.5
        # 打破平局：否则所有 AI 永远围殴座次最前的那一个角色。
        score += self.rng.random() * 6
        return score

    # ==================================================
    # 合法目标
    # ==================================================

    def legal_targets(self, card):
        game = self.game
        actor = self.player
        effect = game.engine.card_effects.get(card)
        if effect is None:
            return []
        if effect.target_rule is TargetRule.SELF:
            return [actor]
        if effect.target_rule is TargetRule.ALL_PLAYERS:
            return game.seats.alive_players_in_order(start_after=actor, include_start=True)
        if effect.target_rule is TargetRule.ALL_OTHERS:
            return [p for p in game.seats.alive_players_in_order(start_after=actor) if p is not actor]
        candidates = target_candidates(game, actor, effect.target_rule)
        legal = []
        for candidate in candidates:
            probe = UseCardAction(actor, card, [candidate], ignore_usage_limit=False)
            if effect.can_use(game, probe)[0]:
                legal.append(candidate)
        return sorted(legal, key=lambda p: self.score_target(p, card), reverse=True)

    def _can_use(self, card, action):
        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return False
        return effect.can_use(self.game, action)[0]

    # ==================================================
    # 出牌决策
    # ==================================================

    def _wants_equipment(self, card):
        current = self.player.get_equipment(card.subtype)
        if current is None:
            return True
        if card.subtype == "weapon":
            return card.attack_range > current.attack_range
        return self.card_value(card) > self.card_value(current)

    def _build_action(self, card):
        effect = self.game.engine.card_effects.get(card)
        if effect is None:
            return None
        if card.name == "TIESUO":
            fresh = [target for target in self.legal_targets(card) if not target.chained][:2]
            if fresh:
                action = UseCardAction(self.player, card, fresh)
                return action if self._can_use(card, action) else None
            recast = UseCardAction(
                self.player, card, [],
                metadata={"recast": True, "skip_wuxie": True},
            )
            return recast if self._can_use(card, recast) else None

        candidates = self.legal_targets(card)
        rule = effect.target_rule
        if rule is TargetRule.MULTIPLE:
            targets = [player for player in candidates if not player.chained][:2]
        elif rule in (TargetRule.ALL_PLAYERS, TargetRule.ALL_OTHERS):
            targets = candidates
        elif rule is TargetRule.NO_TARGET:
            targets = []
        else:
            targets = candidates[:1]

        action = UseCardAction(self.player, card, targets)
        return action if self._can_use(card, action) else None

    def choose_action(self):
        hand = [card for card in self.player.hand if card.id not in self._failed_cards]
        if not hand:
            return None

        # 1) 体力明显偏低时补充
        if self.player.hp <= self.player.max_hp - 2:
            tao = next((card for card in hand if card.name == "TAO"), None)
            if tao is not None:
                action = UseCardAction(self.player, tao, [self.player])
                if self._can_use(tao, action):
                    return action

        # 2) 装备：空槽直接装备，武器只换更长的
        for card in hand:
            if card.category != "equipment" or not self._wants_equipment(card):
                continue
            action = UseCardAction(self.player, card, [])
            if self._can_use(card, action):
                return action

        # 3) 酒 + 杀
        sha = next((card for card in hand if card.name == "SHA"), None)
        if sha is not None:
            sha_targets = self.legal_targets(sha)
            if sha_targets:
                jiu = next((card for card in hand if card.name == "JIU"), None)
                if (
                    jiu is not None
                    and not self.player.jiu_used
                    and not self.player.sha_used
                    and self.player.hp > 1
                ):
                    action = UseCardAction(self.player, jiu, [self.player])
                    if self._can_use(jiu, action):
                        return action
                action = UseCardAction(self.player, sha, sha_targets[:1])
                if self._can_use(sha, action):
                    return action

        # 4) 其余锦囊，按价值从高到低尝试
        ranked = sorted(hand, key=self.card_value, reverse=True)
        for card in ranked:
            if card.name in ("WUXIE", "TAO", "JIU", "SHA", "SHAN"):
                continue
            action = self._build_action(card)
            if action is not None:
                return action
        return None

    # ==================================================
    # 回合循环
    # ==================================================

    def take_turn(self, on_complete):
        self._cards_played = 0
        self._failed_cards = set()
        self._continue_turn(on_complete)

    def _continue_turn(self, on_complete):
        game = self.game
        if (
            self._cards_played >= self.MAX_CARDS_PER_TURN
            or game.game_over
            or not self.player.is_alive
        ):
            game.actions.add(CallbackAction(on_complete))
            return

        action = self.choose_action()
        if action is None:
            game.actions.add(CallbackAction(on_complete))
            return

        self._cards_played += 1
        self._failed_cards.add(action.card.id)
        action.on_complete = lambda _result: game.actions.add(
            CallbackAction(lambda: self._continue_turn(on_complete))
        )
        result = self.submit(action)
        if getattr(result.status, "value", None) == "cancelled":
            game.actions.add(CallbackAction(lambda: self._continue_turn(on_complete)))

    def discard_to_hand_limit(self):
        limit = max(0, self.player.hp)
        while len(self.player.hand) > limit:
            card = min(self.player.hand, key=self.card_value)
            self.game.engine.context.apply(
                MoveCardAtom(
                    card,
                    source=self.player.hand,
                    destination=self.game.deck.discard_pile,
                )
            )

    # ==================================================
    # Pending 响应
    # ==================================================

    def respond(self, request):
        request_type = request.request_type
        if request_type is PendingRequestType.RESPOND_CARD:
            return self._respond_with_card(request)
        if request_type is PendingRequestType.CONFIRM:
            return self._respond_confirm(request)
        if request_type is PendingRequestType.CHOOSE_OPTION:
            return self._respond_option(request)
        if request_type is PendingRequestType.SELECT_CARDS:
            return self._respond_select(request)
        raise ValueError("unsupported AI pending request: " + str(request_type))

    def _pass(self, request):
        self.submit(PassPendingAction(request.target, request.request_id))

    def _play_or_pass(self, request, card):
        if card is None:
            self._pass(request)
            return
        self.submit(
            RespondCardAction(request.target, request.request_id, card, None)
        )

    def _pick_card(self, player, names):
        for name in names:
            card = next((item for item in player.hand if item.name == name), None)
            if card is not None:
                return card
        return None

    def _respond_with_card(self, request):
        reason = request.context.get("reason")
        responder = request.target

        if reason == "dying_rescue":
            if responder is not request.context.get("dying_player"):
                # 自由混战：不使用【桃】救其他角色，但仍走规则层流程。
                self._pass(request)
                return
            self._play_or_pass(request, self._pick_card(responder, SELF_RESCUE_NAMES))
            return

        if reason == "wuxie_chain":
            self._play_or_pass(request, self._wuxie_choice(request))
            return

        self._play_or_pass(request, self._pick_card(responder, tuple(request.allowed_cards)))

    def _wuxie_choice(self, request):
        card = self._pick_card(request.target, ("WUXIE",))
        if card is None:
            return None
        trick = request.context.get("card")
        if trick is not None and trick.name in BENEFICIAL_TRICKS:
            return None
        affected = request.context.get("targets", ())
        if not any(target is self.player for target in affected):
            # 不替别人消耗无懈可击。
            return None
        if request.context.get("nullified"):
            # 该效果已经被抵消，对当前的自己有利，不再反无懈。
            return None
        return card

    def _respond_confirm(self, request):
        reason = request.context.get("reason", "")
        actor = request.target
        confirmed = True
        if reason == "guanshi_confirm":
            confirmed = len(actor.hand) >= 4
        elif reason == "qinglong_confirm":
            confirmed = (
                not actor.sha_used
                and any(card.name == "SHA" for card in actor.hand)
            )
        elif reason == "zhangba_confirm":
            confirmed = False
        self.submit(
            ConfirmPendingAction(request.target, request.request_id, confirmed)
        )

    def _respond_option(self, request):
        options = list(request.options)
        pick = "draw" if "draw" in options else (options[0] if options else None)
        self.submit(ChooseOptionAction(request.target, request.request_id, pick))

    def _respond_select(self, request):
        reason = request.context.get("reason")
        candidates = list(request.context.get("candidates", ()))
        if not candidates:
            self._pass(request)
            return
        count = max(1, request.min_cards)

        if reason == "wugu":
            picked = sorted(candidates, key=self.card_value, reverse=True)[:count]
        elif reason in ("guohe", "shunshou"):
            picked = self._pick_from_opponent(request, candidates)[:count]
        else:
            picked = sorted(candidates, key=self.card_value)[:count]

        self.submit(
            SelectCardsAction(request.target, request.request_id, picked)
        )

    def _pick_from_opponent(self, request, candidates):
        owner = request.context.get("zone_owner")
        if owner is not None and owner is not self.player:
            equipped = {id(card) for card in owner.equipment.values() if card is not None}
            exposed = [card for card in candidates if id(card) in equipped]
            if exposed:
                # 装备是公开信息；对方手牌内容不可读，只能随机选择。
                return [max(exposed, key=self.card_value)]
            return [self.rng.choice(candidates)]
        return sorted(candidates, key=self.card_value, reverse=True)
