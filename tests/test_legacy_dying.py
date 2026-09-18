import unittest

from tests.legacy_helpers import (
    SettlementStatus,
    assert_card_is_discarded,
    assert_game_settled,
    drain_actions,
    jiu,
    make_test_game,
    normal_sha,
    shan,
    tao,
)


class LegacyDyingBaselineTests(unittest.TestCase):
    def _deal_incoming_sha_damage(self, game, attack):
        game.phase = "enemy"
        game.add_table_card(attack, (0, 0, 80, 120))
        game.resolve_enemy_sha_damage(attack, 1)
        return drain_actions(game)

    def test_dying_player_uses_tao_and_returns_to_play(self):
        rescue = tao()
        incoming_sha = normal_sha()
        game = make_test_game(
            player_hp=1,
            player_hand=[rescue],
            draw_order=[shan(), normal_sha(card_color="red")],
        )

        status = self._deal_incoming_sha_damage(game, incoming_sha)

        self.assertEqual(status, SettlementStatus.WAITING_FOR_PLAYER)
        self.assertEqual(game.player.hp, 0)
        self.assertEqual(game.phase, "dying")
        self.assertTrue(game.response.active)

        game.respond_with_card(0, (0, 0, 80, 120))
        drain_actions(game)

        self.assertEqual(game.player.hp, 1)
        self.assertFalse(game.game_over)
        self.assertEqual(game.phase, "play")
        assert_card_is_discarded(game, rescue)
        assert_card_is_discarded(game, incoming_sha)
        assert_game_settled(game)

    def test_dying_player_uses_jiu_and_returns_to_play(self):
        rescue = jiu()
        incoming_sha = normal_sha()
        game = make_test_game(
            player_hp=1,
            player_hand=[rescue],
            draw_order=[shan(), normal_sha(card_color="red")],
        )

        status = self._deal_incoming_sha_damage(game, incoming_sha)

        self.assertEqual(status, SettlementStatus.WAITING_FOR_PLAYER)
        self.assertTrue(game.response.active)
        game.respond_with_card(0, (0, 0, 80, 120))
        drain_actions(game)

        self.assertEqual(game.player.hp, 1)
        self.assertFalse(game.game_over)
        self.assertEqual(game.phase, "play")
        assert_card_is_discarded(game, rescue)
        assert_game_settled(game)

    def test_dying_without_rescue_ends_game(self):
        incoming_sha = normal_sha()
        game = make_test_game(player_hp=1, player_hand=[])

        status = self._deal_incoming_sha_damage(game, incoming_sha)

        self.assertEqual(status, SettlementStatus.IDLE)
        self.assertEqual(game.player.hp, 0)
        self.assertTrue(game.game_over)
        self.assertEqual(game.phase, "over")
        self.assertEqual(game.message, "你阵亡了！")
        self.assertFalse(game.response.active)
        assert_card_is_discarded(game, incoming_sha)
        assert_game_settled(game)


if __name__ == "__main__":
    unittest.main()
