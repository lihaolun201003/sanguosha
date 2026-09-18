"""Small information-safe free-for-all AI controller."""

from src.actions import CallbackAction
from src.game.engine import UseCardAction
from src.game.rules import DistanceRule, TargetRule, target_candidates
from .base import PlayerController


class AIController(PlayerController):

    def score_target(self, target, card):
        score = (target.max_hp - target.hp) * 20 - target.hp * 3
        if card.name in {"GUOHE", "SHUNSHOU"}:
            score += len(target.hand) * 4 + sum(value is not None for value in target.equipment.values()) * 3
        if card.name == "HUOGONG" and target.hand:
            score += 10
        return score

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

    def choose_action(self):
        tricks = [card for card in self.player.hand if self.game.engine.card_effects.get(card) is not None]
        for card in tricks:
            effect = self.game.engine.card_effects.get(card)
            if card.name == "WUXIE":
                continue
            candidates = self.legal_targets(card)
            if effect.target_rule is TargetRule.MULTIPLE:
                targets = [p for p in candidates if not p.chained][:2]
            elif effect.target_rule in (TargetRule.ALL_PLAYERS, TargetRule.ALL_OTHERS):
                targets = candidates
            elif effect.target_rule is TargetRule.NO_TARGET:
                targets = []
            else:
                targets = candidates[:1]
            action = UseCardAction(self.player, card, targets)
            if effect.can_use(self.game, action)[0]:
                return action
        return None

    def take_turn(self, on_complete):
        action = self.choose_action()
        if action is None:
            self.game.actions.add(CallbackAction(on_complete))
            return
        action.on_complete = lambda _result: self.game.actions.add(CallbackAction(on_complete))
        result = self.submit(action)
        if result.status.value == "cancelled":
            self.game.actions.add(CallbackAction(on_complete))
