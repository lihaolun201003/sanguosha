import unittest

from src.actions import CallbackAction, WaitAction

from tests.legacy_helpers import (
    SettlementStatus,
    assert_game_settled,
    drain_actions,
    equipment,
    make_test_game,
    normal_sha,
    set_draw_order,
    shan,
    tao,
)


class LegacyTestToolsTests(unittest.TestCase):
    def test_factory_sets_fixed_state_without_pending_work(self):
        attack = normal_sha()
        dodge = shan()
        armor = equipment("TENGJIA")
        draw_card = tao()

        game = make_test_game(
            player_hp=3,
            enemy_hp=2,
            player_hand=[attack],
            enemy_hand=[dodge],
            enemy_equipment=[armor],
            draw_order=[draw_card],
        )

        self.assertEqual(game.player.hp, 3)
        self.assertEqual(game.enemy.hp, 2)
        self.assertEqual(game.player.hand, [attack])
        self.assertEqual(game.enemy.hand, [dodge])
        self.assertIs(game.enemy.get_equipment("armor"), armor)
        self.assertIs(game.deck.draw(), draw_card)
        assert_game_settled(game)

    def test_set_draw_order_matches_deck_draw_order(self):
        first = normal_sha()
        second = shan()
        game = make_test_game()

        set_draw_order(game, [first, second])

        self.assertIs(game.deck.draw(), first)
        self.assertIs(game.deck.draw(), second)

    def test_drain_skips_wait_time_and_runs_callbacks(self):
        game = make_test_game()
        called = []
        game.actions.add(WaitAction(999.0))
        game.actions.add(CallbackAction(lambda: called.append("done")))

        status = drain_actions(game)

        self.assertEqual(status, SettlementStatus.IDLE)
        self.assertEqual(called, ["done"])
        assert_game_settled(game)

    def test_drain_does_not_answer_player_request(self):
        game = make_test_game()
        game.response.request(
            prompt="test request",
            allowed_cards={"SHAN"},
            on_card=lambda *_: None,
            on_pass=lambda: None,
        )

        status = drain_actions(game)

        self.assertEqual(status, SettlementStatus.WAITING_FOR_PLAYER)
        self.assertTrue(game.response.active)
        with self.assertRaises(AssertionError):
            assert_game_settled(game)


if __name__ == "__main__":
    unittest.main()
