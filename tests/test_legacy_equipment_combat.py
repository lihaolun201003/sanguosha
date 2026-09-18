import unittest

from tests.legacy_helpers import (
    SettlementStatus,
    assert_card_is_discarded,
    assert_game_settled,
    drain_actions,
    equipment,
    fire_sha,
    jiu,
    make_test_game,
    normal_sha,
    play_player_card,
    select_card,
    set_draw_order,
    shan,
    tao,
)


class LegacyEquipmentCombatBaselineTests(unittest.TestCase):
    def test_normal_sha_is_blocked_by_vine(self):
        attack = normal_sha()
        vine = equipment("TENGJIA")
        game = make_test_game(
            player_hand=[attack],
            enemy_equipment=[vine],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 4)
        assert_card_is_discarded(game, attack)
        assert_game_settled(game)

    def test_fire_sha_gets_vine_bonus(self):
        attack = fire_sha()
        vine = equipment("TENGJIA")
        game = make_test_game(
            player_hand=[attack],
            enemy_equipment=[vine],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 2)
        assert_card_is_discarded(game, attack)
        assert_game_settled(game)

    def test_qinggang_ignores_vine(self):
        attack = normal_sha()
        qinggang = equipment("QINGGANG")
        vine = equipment("TENGJIA")
        game = make_test_game(
            player_hand=[attack],
            player_equipment=[qinggang],
            enemy_equipment=[vine],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 3)
        assert_game_settled(game)

    def test_qinggang_ignores_renwang_shield(self):
        attack = normal_sha(card_color="black")
        qinggang = equipment("QINGGANG")
        renwang = equipment("RENWANG")
        game = make_test_game(
            player_hand=[attack],
            player_equipment=[qinggang],
            enemy_equipment=[renwang],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 3)
        assert_game_settled(game)

    def test_silver_lion_caps_wine_sha_damage_at_one(self):
        wine = jiu()
        attack = normal_sha()
        silver_lion = equipment("BAIYIN")
        game = make_test_game(
            player_hand=[wine, attack],
            enemy_equipment=[silver_lion],
        )

        play_player_card(game, wine)
        drain_actions(game)
        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 3)
        assert_card_is_discarded(game, wine)
        assert_card_is_discarded(game, attack)
        assert_game_settled(game)

    def test_qinglong_chases_after_shan_and_second_sha_hits(self):
        first_sha = normal_sha()
        second_sha = normal_sha(card_color="red")
        dodge = shan()
        qinglong = equipment("QINGLONG")
        game = make_test_game(
            player_hand=[first_sha, second_sha],
            enemy_hand=[dodge],
            player_equipment=[qinglong],
        )

        play_player_card(game, first_sha)
        status = drain_actions(game)

        self.assertEqual(status, SettlementStatus.WAITING_FOR_PLAYER)
        self.assertTrue(game.choice.active)
        game.choice.choose_yes()
        self.assertIsNotNone(game.pending_selection)
        select_card(game, second_sha)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 3)
        self.assertEqual(game.player.hand, [])
        assert_card_is_discarded(game, dodge)
        assert_card_is_discarded(game, first_sha)
        assert_card_is_discarded(game, second_sha)
        assert_game_settled(game)

    def test_qinglong_can_decline_chase_after_shan(self):
        first_sha = normal_sha()
        second_sha = normal_sha(card_color="red")
        dodge = shan()
        qinglong = equipment("QINGLONG")
        game = make_test_game(
            player_hand=[first_sha, second_sha],
            enemy_hand=[dodge],
            player_equipment=[qinglong],
        )

        play_player_card(game, first_sha)
        drain_actions(game)
        self.assertTrue(game.choice.active)

        game.choice.choose_no()
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 4)
        self.assertEqual(game.player.hand, [second_sha])
        assert_card_is_discarded(game, dodge)
        assert_card_is_discarded(game, first_sha)
        self.assertFalse(game.choice.active)
        self.assertIsNone(game.pending_selection)
        assert_game_settled(game)

    def test_guanshi_forces_hit_after_two_hand_cards_are_discarded(self):
        attack = normal_sha()
        material_one = tao()
        material_two = jiu()
        dodge = shan()
        guanshi = equipment("GUANSHI")
        game = make_test_game(
            player_hand=[attack, material_one, material_two],
            enemy_hand=[dodge],
            player_equipment=[guanshi],
        )

        play_player_card(game, attack)
        drain_actions(game)
        self.assertTrue(game.choice.active)

        game.choice.choose_yes()
        self.assertIsNotNone(game.pending_selection)
        select_card(game, material_one)
        select_card(game, material_two)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 3)
        self.assertEqual(game.player.hand, [])
        for card in (attack, dodge, material_one, material_two):
            assert_card_is_discarded(game, card)
        self.assertIsNone(game.pending_selection)
        assert_game_settled(game)

    def test_hanbing_replaces_damage_with_two_selected_discards(self):
        attack = normal_sha()
        target_card_one = tao()
        target_card_two = jiu()
        hanbing = equipment("HANBING")
        game = make_test_game(
            player_hand=[attack],
            enemy_hand=[target_card_one, target_card_two],
            player_equipment=[hanbing],
        )

        play_player_card(game, attack)
        drain_actions(game)
        self.assertTrue(game.choice.active)

        game.choice.choose_yes()
        self.assertIsNotNone(game.pending_selection)
        select_card(game, target_card_one)
        select_card(game, target_card_two)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 4)
        self.assertEqual(game.enemy.hand, [])
        for card in (attack, target_card_one, target_card_two):
            assert_card_is_discarded(game, card)
        self.assertIsNone(game.pending_selection)
        assert_game_settled(game)

    def test_guding_adds_damage_when_target_has_no_hand_cards(self):
        attack = normal_sha()
        guding = equipment("GUDING")
        game = make_test_game(
            player_hand=[attack],
            enemy_hand=[],
            player_equipment=[guding],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 2)
        assert_game_settled(game)

    def test_enemy_bagua_red_judgment_counts_as_shan(self):
        attack = normal_sha()
        bagua = equipment("BAGUA")
        red_judgment = tao()
        game = make_test_game(
            player_hand=[attack],
            enemy_equipment=[bagua],
            draw_order=[red_judgment],
        )

        play_player_card(game, attack)
        drain_actions(game)

        self.assertEqual(game.enemy.hp, 4)
        assert_card_is_discarded(game, red_judgment)
        assert_card_is_discarded(game, attack)
        assert_game_settled(game)

    def test_player_bagua_black_judgment_continues_to_shan_request(self):
        incoming_sha = normal_sha()
        black_judgment = normal_sha()
        bagua = equipment("BAGUA")
        game = make_test_game(
            player_equipment=[bagua],
            draw_order=[black_judgment, tao(), shan()],
        )
        game.phase = "enemy"
        game.add_table_card(incoming_sha, (0, 0, 80, 120))

        game.request_player_shan(incoming_sha, 1)
        self.assertTrue(game.choice.active)
        game.choice.choose_yes()
        status = drain_actions(game)

        self.assertEqual(status, SettlementStatus.WAITING_FOR_PLAYER)
        self.assertTrue(game.response.active)
        self.assertEqual(game.player.hp, 4)
        assert_card_is_discarded(game, black_judgment)

        game.pass_response()
        drain_actions(game)

        self.assertEqual(game.player.hp, 3)
        assert_card_is_discarded(game, incoming_sha)
        assert_game_settled(game)


if __name__ == "__main__":
    unittest.main()
