import unittest

from src.card_catalog import TRICK_DEFINITIONS, create_development_deck, create_trick_cards
from src.game.engine import (
    ConfirmPendingAction,
    FlowStatus,
    PassPendingAction,
    SelectCardsAction,
    UseCardAction,
)
from src.game.flows import JudgeFlow
from src.game.rules import DistanceRule, TargetRule
from tests.legacy_helpers import equipment, make_test_game, normal_sha, shan, tao


def trick(name):
    return next(card for card in create_trick_cards() if card.name == name)


class EngineV2Phase3Tests(unittest.TestCase):
    def test_registry_target_rules_and_catalog(self):
        game = make_test_game()
        self.assertEqual(len(game.engine.card_effects), 16)
        self.assertIs(game.engine.card_effects.require("NANMAN").target_rule, TargetRule.ALL_OTHERS)
        self.assertEqual(len(create_development_deck()), 128)
        self.assertEqual(sum(map(len, TRICK_DEFINITIONS.values())), 49)
        self.assertEqual(TRICK_DEFINITIONS["WUZHONG"], (("heart", "7"), ("heart", "8"), ("heart", "9"), ("heart", "J")))
        cards = create_development_deck()
        self.assertEqual(len({card.id for card in cards}), len(cards))

    def test_wuzhong_draws_two_through_atom(self):
        card = trick("WUZHONG")
        draw_one, draw_two = tao(), shan()
        game = make_test_game(player_hand=[card], draw_order=[draw_one, draw_two])
        game.submit_action(UseCardAction(game.player, card, [game.player]))
        self.assertEqual(game.player.hand, [draw_one, draw_two])

    def test_guohe_discards_selected_target_card(self):
        card, victim = trick("GUOHE"), tao()
        game = make_test_game(player_hand=[card], enemy_hand=[victim])
        waiting = game.submit_action(UseCardAction(game.player, card, [game.enemy]))
        request = game.pending_request
        result = game.submit_action(SelectCardsAction(game.player, request.request_id, [victim]))
        self.assertEqual(waiting.status, FlowStatus.WAITING)
        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertIn(victim, game.deck.discard_pile)

    def test_shunshou_gains_selected_equipment_at_distance_one(self):
        card, armor = trick("SHUNSHOU"), equipment("RENWANG")
        game = make_test_game(player_hand=[card], enemy_equipment=[armor])
        game.submit_action(UseCardAction(game.player, card, [game.enemy]))
        request = game.pending_request
        game.submit_action(SelectCardsAction(game.player, request.request_id, [armor]))
        self.assertIn(armor, game.player.hand)
        self.assertIsNone(game.enemy.get_equipment("armor"))

    def test_duel_alternates_sha_until_pass(self):
        card, answer = trick("JUEDOU"), normal_sha()
        game = make_test_game(player_hand=[card], enemy_hand=[answer])
        waiting = game.submit_action(UseCardAction(game.player, card, [game.enemy]))
        self.assertEqual(waiting.status, FlowStatus.WAITING)
        self.assertIs(game.pending_request.target, game.player)
        game.submit_action(PassPendingAction(game.player, game.pending_request.request_id))
        self.assertEqual(game.player.hp, 3)
        self.assertFalse(game.sha_used)

    def test_nanman_uses_targets_and_sha_response(self):
        card, answer = trick("NANMAN"), normal_sha()
        game = make_test_game(player_hand=[card], enemy_hand=[answer])
        result = game.submit_action(UseCardAction(game.player, card, [game.enemy]))
        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertEqual(game.enemy.hp, 4)
        self.assertIn(answer, game.deck.discard_pile)

    def test_wanjian_pass_deals_damage(self):
        card = trick("WANJIAN")
        game = make_test_game(player_hand=[card], enemy_hand=[])
        result = game.submit_action(UseCardAction(game.player, card, [game.enemy]))
        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertEqual(game.enemy.hp, 3)

    def test_taoyuan_recovers_all_without_exceeding_max(self):
        card = trick("TAOYUAN")
        game = make_test_game(player_hp=3, enemy_hp=4, player_hand=[card])
        game.submit_action(UseCardAction(game.player, card, [game.player, game.enemy]))
        self.assertEqual((game.player.hp, game.enemy.hp), (4, 4))

    def test_judge_flow_exposes_result_and_discards_card(self):
        judged = tao()
        game = make_test_game(draw_order=[judged])
        result = JudgeFlow(game.engine, game.player, "test").start().value
        self.assertIs(result.card, judged)
        self.assertEqual(result.color, "red")
        self.assertIn(judged, game.deck.discard_pile)

    def test_distance_rule_applies_both_horses(self):
        defensive = equipment("JUEYING")
        offensive = equipment("CHITU")
        game = make_test_game(player_equipment=[offensive], enemy_equipment=[defensive])
        self.assertEqual(DistanceRule.distance(game, game.player, game.enemy), 1)

    def test_qinglong_confirm_select_reenters_use_card_flow(self):
        first, second, dodge = normal_sha(), normal_sha(card_color="red"), shan()
        game = make_test_game(player_hand=[first, second], enemy_hand=[dodge], player_equipment=[equipment("QINGLONG")])
        game.submit_action(UseCardAction(game.player, first, [game.enemy]))
        request = game.pending_request
        game.submit_action(ConfirmPendingAction(game.player, request.request_id, True))
        request = game.pending_request
        game.submit_action(SelectCardsAction(game.player, request.request_id, [second]))
        self.assertEqual(game.enemy.hp, 3)

    def test_guanshi_discards_two_and_forces_hit(self):
        attack, dodge, one, two = normal_sha(), shan(), tao(), trick("WUZHONG")
        game = make_test_game(player_hand=[attack, one, two], enemy_hand=[dodge], player_equipment=[equipment("GUANSHI")])
        game.submit_action(UseCardAction(game.player, attack, [game.enemy]))
        game.submit_action(ConfirmPendingAction(game.player, game.pending_request.request_id, True))
        game.submit_action(SelectCardsAction(game.player, game.pending_request.request_id, [one, two]))
        self.assertEqual(game.enemy.hp, 3)
        self.assertIn(one, game.deck.discard_pile)

    def test_hanbing_prevents_damage_and_discards_target_hand(self):
        attack, one, two = normal_sha(), tao(), trick("WUZHONG")
        game = make_test_game(player_hand=[attack], enemy_hand=[one, two], player_equipment=[equipment("HANBING")])
        game.submit_action(UseCardAction(game.player, attack, [game.enemy]))
        game.submit_action(ConfirmPendingAction(game.player, game.pending_request.request_id, True))
        game.submit_action(SelectCardsAction(game.player, game.pending_request.request_id, [one, two]))
        self.assertEqual(game.enemy.hp, 4)
        self.assertEqual(game.enemy.hand, [])


if __name__ == "__main__":
    unittest.main()
