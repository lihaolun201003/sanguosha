import unittest

from src.game import Game
from src.game.controllers import AIController
from src.game.engine import PassPendingAction, SelectCardsAction, UseCardAction
from src.game.flows import DamageContext, DamageFlow, DyingFlow, TurnFlow
from src.game.flows.wuxie import WuxieResponseChain
from src.game.rules import DistanceRule
from tests.legacy_helpers import canonical_card, normal_sha, set_draw_order, tao


def trick(name):
    return canonical_card(name)


class EngineV2Phase5Tests(unittest.TestCase):
    def make_game(self, ai_count):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        return game

    def test_creates_two_four_and_eight_player_games(self):
        for ai_count in (1, 3, 7):
            game = self.make_game(ai_count)
            self.assertEqual(len(game.players), ai_count + 1)
            self.assertEqual([p.player_id for p in game.players], ["P" + str(i) for i in range(ai_count + 1)])
            self.assertEqual([p.seat for p in game.players], list(range(ai_count + 1)))

    def test_next_alive_skips_dead_without_reseating(self):
        game = self.make_game(3)
        game.players[1].alive = False
        game.players[1].hp = 0
        self.assertIs(game.seats.next_alive_player(game.player), game.players[2])
        self.assertEqual(game.players[2].seat, 2)

    def test_circular_distance_and_dead_player_compression(self):
        game = self.make_game(7)
        self.assertEqual(DistanceRule.distance(game, game.players[0], game.players[7]), 1)
        self.assertEqual(DistanceRule.distance(game, game.players[0], game.players[3]), 3)
        game.players[1].alive = False
        game.players[1].hp = 0
        self.assertEqual(DistanceRule.distance(game, game.players[0], game.players[2]), 1)

    def test_sha_uses_multiplayer_attack_range(self):
        game = self.make_game(3)
        attack = normal_sha()
        game.player.hand = [attack]
        legal, _ = game.engine.card_effects.require(attack).can_use(
            game, UseCardAction(game.player, attack, [game.players[2]])
        )
        self.assertFalse(legal)
        game.players[1].alive = False
        game.players[1].hp = 0
        legal, _ = game.engine.card_effects.require(attack).can_use(
            game, UseCardAction(game.player, attack, [game.players[2]])
        )
        self.assertTrue(legal)

    def test_nanman_and_wanjian_resolve_three_targets_in_order(self):
        for name in ("NANMAN", "WANJIAN"):
            game = self.make_game(3)
            card = trick(name)
            game.player.hand = [card]
            targets = game.seats.alive_players_in_order(start_after=game.player)
            result = game.submit_action(UseCardAction(game.player, card, targets))
            self.assertEqual(result.status.value, "completed")
            self.assertEqual([p.hp for p in targets], [3, 3, 3])

    def test_wugu_reveals_alive_count_and_all_ai_choose(self):
        game = self.make_game(4)
        card = trick("WUGU")
        draws = [tao(), normal_sha(), tao(), normal_sha(), tao()]
        game.player.hand = [card]
        set_draw_order(game, draws)
        targets = game.seats.alive_players_in_order(start_after=game.player, include_start=True)
        game.submit_action(UseCardAction(game.player, card, targets))
        self.assertEqual(len(game.public_card_pool), 5)
        game.submit_action(SelectCardsAction(game.player, game.pending_request.request_id, [draws[0]]))
        self.assertEqual(game.public_card_pool, [])
        self.assertTrue(all(len(player.hand) == 1 for player in game.players))

    def test_wuxie_window_covers_every_eligible_player_in_seat_order(self):
        """共享无懈阶段：**所有打得出无懈的人**按座次同轮获得机会。

        （旧语义是"按座次逐人问一遍"，Phase 11.6 改成"一轮同时问所有有资格的
        人、谁先打出谁锁定本轮"。）
        """

        game = self.make_game(3)
        for player in game.players:
            player.hand = [trick("WUXIE")]
        chain = WuxieResponseChain(
            game.engine, game.players[2], trick("GUOHE"), [game.player],
            lambda _x: None)
        chain.start()
        request = game.pending_request
        self.assertTrue(request.is_group)
        self.assertEqual([p.seat for p in request.responders], [2, 3, 0, 1])
        self.assertEqual(request.context.get("round_id"), 1)

    def test_dying_flow_asks_human_to_save_other_player(self):
        game = self.make_game(2)
        dying = game.players[1]
        dying.hp = 0
        game.player.hand = [tao()]
        result = DyingFlow(game.engine, dying_player=dying).start()
        self.assertEqual(result.status.value, "waiting")
        self.assertIs(game.pending_request.target, game.player)
        self.assertIn("救援", game.pending_request.prompt)
        game.submit_action(PassPendingAction(game.player, game.pending_request.request_id))
        self.assertFalse(dying.alive)

    def test_lightning_moves_to_next_legal_alive_seat(self):
        game = self.make_game(3)
        lightning = trick("SHANDIAN")
        game.player.judgement_zone.append(lightning)
        game.players[1].alive = False
        game.players[1].hp = 0
        set_draw_order(game, [tao(), normal_sha(), tao()])
        TurnFlow(game.engine, game.player).start()
        self.assertIn(lightning, game.players[2].judgement_zone)

    def test_chain_damage_propagates_in_seat_order_across_death(self):
        game = self.make_game(3)
        for player in game.players[1:]:
            player.chained = True
        game.players[2].hp = 1
        DamageFlow(game.engine, DamageContext(game.player, game.players[1], 1, nature="fire", card=normal_sha())).start()
        self.assertEqual(game.players[3].hp, 3)
        self.assertFalse(game.players[2].alive)

    def test_ai_selects_from_multiple_legal_targets_without_hidden_cards(self):
        game = self.make_game(3)
        attack = normal_sha()
        actor = game.players[1]
        actor.hand = [attack]
        game.players[2].hp = 1
        controller = AIController(game, actor)
        action = controller.choose_action()
        self.assertIsNotNone(action)
        self.assertIs(action.targets[0], game.players[2])

    def test_current_player_death_advances_current_actor(self):
        game = self.make_game(3)
        game.current_turn_player = game.players[1]
        game.players[1].hp = 0
        game.engine.run_death(game.players[1])
        self.assertIs(game.current_turn_player, game.players[2])

    def test_last_survivor_and_human_elimination_results(self):
        game = self.make_game(2)
        game.player.hp = 0
        game.engine.run_death(game.player)
        self.assertTrue(game.game_over)
        self.assertIsNone(game.winner)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")

        game = self.make_game(2)
        game.players[2].alive = False
        game.players[2].hp = 0
        game.players[1].hp = 0
        game.engine.run_death(game.players[1])
        self.assertIs(game.winner, game.player)
        self.assertEqual(game.result.reason, "LAST_SURVIVOR")

    def test_restart_rebuilds_clean_multiplayer_state(self):
        game = self.make_game(4)
        old_ids = [id(player) for player in game.players]
        game.players[2].alive = False
        game.players[3].chained = True
        game.public_card_pool.append(tao())
        game.reset()
        self.assertEqual(len(game.players), 5)
        self.assertNotEqual(old_ids, [id(player) for player in game.players])
        self.assertTrue(all(player.alive and not player.chained for player in game.players))
        self.assertEqual(game.public_card_pool, [])
        self.assertIsNone(game.pending_request)


if __name__ == "__main__":
    unittest.main()
