import unittest

from tests.legacy_helpers import (
    assert_card_is_discarded,
    assert_game_settled,
    drain_actions,
    jiu,
    make_test_game,
    normal_sha,
    play_player_card,
    shan,
)


class LegacyBasicCombatBaselineTests(unittest.TestCase):
    def test_sha_hits_unarmored_target(self):
        attack = normal_sha()
        game = make_test_game(player_hand=[attack])

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 3)
        self.assertNotIn(attack, game.player.hand)
        assert_card_is_discarded(game, attack)
        self.assertFalse(game.response.active)
        assert_game_settled(game)

    def test_sha_is_cancelled_by_enemy_shan(self):
        attack = normal_sha()
        dodge = shan()
        game = make_test_game(
            player_hand=[attack],
            enemy_hand=[dodge],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 4)
        assert_card_is_discarded(game, attack)
        assert_card_is_discarded(game, dodge)
        self.assertFalse(game.response.active)
        assert_game_settled(game)

    def test_jiu_then_sha_deals_two_and_clears_wine_buff(self):
        wine = jiu()
        attack = normal_sha()
        game = make_test_game(player_hand=[wine, attack])

        play_player_card(game, wine)
        drain_actions(game)

        self.assertTrue(game.jiu_used)
        self.assertTrue(game.player_wine_buff)
        self.assertTrue(game.wine_sha_required)
        assert_card_is_discarded(game, wine)

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 2)
        self.assertFalse(game.player_wine_buff)
        self.assertFalse(game.wine_sha_required)
        self.assertTrue(game.jiu_used)
        assert_card_is_discarded(game, attack)
        assert_game_settled(game)


if __name__ == "__main__":
    unittest.main()
