import unittest

from src.game.atoms_v2 import LoseHpAtom
from src.game.engine import (
    EventType,
    FlowStatus,
    GameOutcome,
    PassPendingAction,
    PendingRequestType,
    RespondCardAction,
    UseCardAction,
)
from src.game.flows import DamageContext, DamageFlow

from tests.legacy_helpers import (
    assert_card_is_discarded,
    assert_game_settled,
    drain_actions,
    equipment,
    jiu,
    make_test_game,
    normal_sha,
    shan,
    tao,
)


class EngineV2CoreCombatTests(unittest.TestCase):
    def test_use_card_action_sha_hits_through_v2(self):
        attack = normal_sha()
        game = make_test_game(player_hand=[attack])

        result = game.submit_action(
            UseCardAction(game.player, attack, [game.enemy])
        )

        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertEqual(game.enemy.hp, 3)
        self.assertTrue(game.sha_used)
        assert_card_is_discarded(game, attack)
        drain_actions(game)
        assert_game_settled(game)

    def test_ai_shan_uses_same_respond_card_action_path(self):
        attack = normal_sha()
        dodge = shan()
        game = make_test_game(
            player_hand=[attack],
            enemy_hand=[dodge],
        )
        action_types = []
        original_submit = game.engine.submit

        def recording_submit(action):
            action_types.append(type(action))
            return original_submit(action)

        game.engine.submit = recording_submit
        result = game.submit_action(
            UseCardAction(game.player, attack, [game.enemy])
        )

        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertIn(RespondCardAction, action_types)
        self.assertEqual(game.enemy.hp, 4)
        assert_card_is_discarded(game, dodge)
        drain_actions(game)
        assert_game_settled(game)

    def test_player_shan_resumes_pending_use_card_flow(self):
        attack = normal_sha()
        dodge = shan()
        invalid_response = tao()
        game = make_test_game(
            player_hand=[dodge, invalid_response],
            enemy_hand=[attack],
        )
        game.phase = "enemy"

        result = game.submit_action(
            UseCardAction(game.enemy, attack, [game.player])
        )

        self.assertEqual(result.status, FlowStatus.WAITING)
        request = game.pending_request
        self.assertIsNotNone(request)
        self.assertEqual(request.request_type, PendingRequestType.RESPOND_CARD)
        self.assertTrue(game.response.active)

        with self.assertRaises(ValueError):
            game.submit_action(
                RespondCardAction(
                    game.player,
                    request.request_id,
                    invalid_response,
                )
            )
        self.assertIs(game.pending_request, request)

        drain_actions(game)
        game.respond_with_card(0, (0, 0, 80, 120))

        self.assertEqual(game.player.hp, 4)
        self.assertIsNone(game.pending_request)
        self.assertFalse(game.response.active)
        drain_actions(game)
        assert_game_settled(game)

    def test_passing_shan_request_resumes_damage(self):
        attack = normal_sha()
        game = make_test_game(enemy_hand=[attack])
        game.phase = "enemy"

        waiting = game.submit_action(
            UseCardAction(game.enemy, attack, [game.player])
        )
        request = game.pending_request
        drain_actions(game)
        game.pass_response()

        self.assertEqual(waiting.status, FlowStatus.WAITING)
        self.assertEqual(game.player.hp, 3)
        self.assertIsNone(game.pending_request)

    def test_damage_flow_changes_hp_through_lose_hp_atom(self):
        attack = normal_sha()
        game = make_test_game()
        observed_atoms = []
        game.context.events.subscribe(
            EventType.ATOM_AFTER,
            lambda _, event: observed_atoms.append(event.source),
        )
        damage = DamageContext(
            source=game.player,
            target=game.enemy,
            amount=1,
            nature="normal",
            card=attack,
        )

        result = DamageFlow(game.engine, damage).start()

        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertEqual(game.enemy.hp, 3)
        self.assertTrue(any(isinstance(atom, LoseHpAtom) for atom in observed_atoms))

    def test_dying_flow_recovers_player_with_tao(self):
        attack = normal_sha()
        rescue = tao()
        game = make_test_game(
            player_hp=1,
            player_hand=[rescue],
            enemy_hand=[attack],
        )
        game.phase = "enemy"

        waiting_for_shan = game.submit_action(
            UseCardAction(game.enemy, attack, [game.player])
        )
        shan_request = game.pending_request
        waiting_for_rescue = game.submit_action(
            PassPendingAction(game.player, shan_request.request_id)
        )
        request = game.pending_request
        completed = game.submit_action(
            RespondCardAction(game.player, request.request_id, rescue)
        )

        self.assertEqual(waiting_for_shan.status, FlowStatus.WAITING)
        self.assertEqual(waiting_for_rescue.status, FlowStatus.WAITING)
        self.assertEqual(completed.status, FlowStatus.COMPLETED)
        self.assertEqual(game.player.hp, 1)
        self.assertFalse(game.game_over)
        assert_card_is_discarded(game, rescue)

    def test_dying_flow_recovers_player_with_jiu(self):
        attack = normal_sha()
        rescue = jiu()
        game = make_test_game(
            player_hp=1,
            player_hand=[rescue],
            enemy_hand=[attack],
        )
        game.phase = "enemy"

        game.submit_action(UseCardAction(game.enemy, attack, [game.player]))
        shan_request = game.pending_request
        game.submit_action(
            PassPendingAction(game.player, shan_request.request_id)
        )
        request = game.pending_request
        game.submit_action(
            RespondCardAction(game.player, request.request_id, rescue)
        )

        self.assertEqual(game.player.hp, 1)
        self.assertFalse(game.game_over)
        assert_card_is_discarded(game, rescue)

    def test_no_rescue_runs_death_flow_and_sets_ai_win(self):
        attack = normal_sha()
        game = make_test_game(
            player_hp=1,
            player_hand=[],
            enemy_hand=[attack],
        )
        game.phase = "enemy"

        waiting = game.submit_action(
            UseCardAction(game.enemy, attack, [game.player])
        )
        shan_request = game.pending_request
        result = game.submit_action(
            PassPendingAction(game.player, shan_request.request_id)
        )

        self.assertEqual(waiting.status, FlowStatus.WAITING)
        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertTrue(game.game_over)
        self.assertEqual(game.phase, "over")
        self.assertEqual(game.result.outcome, GameOutcome.AI_WIN)
        self.assertIs(game.winner, game.enemy)

    def test_enemy_death_sets_structured_player_win(self):
        attack = normal_sha()
        game = make_test_game(
            enemy_hp=1,
            player_hand=[attack],
            enemy_hand=[],
        )

        game.submit_action(
            UseCardAction(game.player, attack, [game.enemy])
        )

        self.assertTrue(game.game_over)
        self.assertEqual(game.result.outcome, GameOutcome.PLAYER_WIN)
        self.assertIs(game.winner, game.player)

    def test_vine_block_is_supplied_by_compatibility_hook(self):
        attack = normal_sha()
        vine = equipment("TENGJIA")
        game = make_test_game(
            player_hand=[attack],
            enemy_equipment=[vine],
        )

        self.assertTrue(
            game.engine.compatibility.supports_v2_sha(
                game.player,
                game.enemy,
                attack,
            )
        )
        result = game.submit_action(
            UseCardAction(game.player, attack, [game.enemy])
        )

        self.assertEqual(result.status, FlowStatus.COMPLETED)
        self.assertEqual(game.enemy.hp, 4)
        self.assertTrue(result.value["cancelled"])


if __name__ == "__main__":
    unittest.main()
