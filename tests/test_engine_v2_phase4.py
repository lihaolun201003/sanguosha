import unittest

from src.card_catalog import create_trick_cards
from src.game.engine import (
    EventType, PassPendingAction, PendingRequestType, RespondCardAction,
    SelectCardsAction, UseCardAction,
)
from src.game.flows import DamageContext, DamageFlow, JudgeFlow, TurnFlow
from src.game.rules import TURN_PHASE_ORDER, TurnPhase
from tests.legacy_helpers import drain_actions, equipment, make_test_game, normal_sha, tao


def trick(name):
    return next(card for card in create_trick_cards() if card.name == name)


class EngineV2Phase4Tests(unittest.TestCase):
    def test_one_wuxie_nullifies_trick(self):
        guohe, wuxie, victim = trick("GUOHE"), trick("WUXIE"), tao()
        game = make_test_game(player_hand=[guohe], enemy_hand=[wuxie, victim])
        result = game.submit_action(UseCardAction(game.player, guohe, [game.enemy]))
        self.assertTrue(result.value["cancelled"])
        self.assertIn(victim, game.enemy.hand)
        self.assertIsNone(game.pending_request)

    def test_wuxie_can_be_countered_by_wuxie(self):
        guohe, first, counter, victim = trick("GUOHE"), trick("WUXIE"), trick("WUXIE"), tao()
        game = make_test_game(player_hand=[guohe, counter], enemy_hand=[first, victim])
        game.submit_action(UseCardAction(game.player, guohe, [game.enemy]))
        game.submit_action(RespondCardAction(game.player, game.pending_request.request_id, counter))
        request = game.pending_request
        game.submit_action(SelectCardsAction(game.player, request.request_id, [victim]))
        self.assertIn(victim, game.deck.discard_pile)
        self.assertIsNone(game.pending_request)

    def test_all_wuxie_passes_allow_effect(self):
        card, first, second = trick("WUZHONG"), tao(), normal_sha()
        game = make_test_game(player_hand=[card], draw_order=[first, second])
        game.submit_action(UseCardAction(game.player, card, [game.player]))
        self.assertEqual(game.player.hand, [first, second])

    def test_trick_source_is_also_checked_in_the_wuxie_window(self):
        """共享无懈阶段包含锦囊使用者：他可以放弃，放弃之后锦囊照常结算。"""

        card, own_wuxie, first, second = trick("WUZHONG"), trick("WUXIE"), tao(), normal_sha()
        game = make_test_game(
            player_hand=[card, own_wuxie],
            enemy_hand=[],
            draw_order=[first, second],
        )
        result = game.submit_action(UseCardAction(game.player, card, [game.player]))
        self.assertEqual(result.status.value, "waiting")
        request = game.pending_request
        self.assertTrue(request.is_group, "使用者本人也要在无懈阶段的检查范围内")
        self.assertTrue(request.is_member(game.player))

        # 放弃：锦囊照常结算（摸两张），手里的无懈一张不动。
        game.submit_action(PassPendingAction(game.player, request.request_id))
        self.assertIn(own_wuxie, game.player.hand)
        self.assertEqual(game.player.hand, [own_wuxie, first, second])
        self.assertIsNone(game.pending_request)

    def test_lebu_failure_skips_play_and_success_does_not(self):
        for judged, skipped in ((normal_sha(), True), (tao(), False)):
            # Keep two ordinary draw cards behind the judgment card so the
            # tiny deterministic test deck cannot immediately reshuffle the
            # discard pile during the following draw phase.
            game = make_test_game(draw_order=[judged, normal_sha(), tao()])
            lebu = trick("LEBU")
            game.enemy.judgement_zone.append(lebu)
            result = TurnFlow(game.engine, game.enemy).start().value
            self.assertEqual(TurnPhase.PLAY in result["skipped"], skipped)
            self.assertNotIn(lebu, game.enemy.judgement_zone)
            self.assertIn(lebu, game.deck.discard_pile)

    def test_bingliang_failure_skips_draw(self):
        game = make_test_game(draw_order=[tao(), normal_sha(), normal_sha()])
        game.player.judgement_zone.append(trick("BINGLIANG"))
        result = TurnFlow(game.engine, game.player).start().value
        self.assertIn(TurnPhase.DRAW, result["skipped"])
        self.assertEqual(game.player.hand, [])

    def test_resolved_lebu_and_bingliang_leave_the_visible_table(self):
        for name in ("LEBU", "BINGLIANG"):
            delayed = trick(name)
            game = make_test_game(draw_order=[normal_sha(), tao(), normal_sha()])
            game.player.judgement_zone.append(delayed)
            game.add_table_card(delayed, (0, 0, 80, 120))

            TurnFlow(game.engine, game.player).start()

            self.assertIn(delayed, game.deck.discard_pile)
            drain_actions(game)
            self.assertFalse(any(card is delayed for card, _rect in game.table_cards))

    def test_lightning_hit_deals_thunder_and_chains(self):
        game = make_test_game(draw_order=[normal_sha()])
        game.player.judgement_zone.append(trick("SHANDIAN"))
        game.player.chained = game.enemy.chained = True
        TurnFlow(game.engine, game.player).start()
        self.assertEqual((game.player.hp, game.enemy.hp), (1, 1))
        self.assertFalse(game.player.chained)
        self.assertFalse(game.enemy.chained)

    def test_lightning_miss_moves_to_next_alive_player(self):
        game = make_test_game(draw_order=[tao()])
        lightning = trick("SHANDIAN")
        game.player.judgement_zone.append(lightning)
        TurnFlow(game.engine, game.player).start()
        self.assertIn(lightning, game.enemy.judgement_zone)

    def test_bagua_and_delayed_tricks_share_judge_events(self):
        game = make_test_game(draw_order=[tao()])
        observed = []
        game.context.events.subscribe(EventType.JUDGE_RESULT, lambda _ctx, event: observed.append(event.payload["result"]))
        JudgeFlow(game.engine, game.player, "bagua").start()
        self.assertEqual(observed[0].reason, "bagua")
        self.assertEqual(observed[0].color, "red")

    def test_tiesuo_toggles_one_or_two_targets_and_recasts(self):
        chain = trick("TIESUO")
        game = make_test_game(player_hand=[chain])
        game.submit_action(UseCardAction(game.player, chain, [game.player, game.enemy]))
        self.assertTrue(game.player.chained and game.enemy.chained)
        recast, drawn = trick("TIESUO"), tao()
        game = make_test_game(player_hand=[recast], draw_order=[drawn])
        game.submit_action(UseCardAction(game.player, recast, [], metadata={"recast": True, "skip_wuxie": True}))
        self.assertEqual(game.player.hand, [drawn])

    def test_fire_and_thunder_chain_do_not_recurse(self):
        for nature in ("fire", "thunder"):
            game = make_test_game()
            game.player.chained = game.enemy.chained = True
            result = DamageFlow(game.engine, DamageContext(game.player, game.enemy, 1, nature=nature, card=normal_sha())).start()
            self.assertEqual(result.status.value, "completed")
            self.assertEqual((game.player.hp, game.enemy.hp), (3, 3))

    def test_huogong_discards_matching_suit_and_deals_fire_damage(self):
        attack, cost, reveal = trick("HUOGONG"), tao(), tao()
        game = make_test_game(player_hand=[attack, cost], enemy_hand=[reveal])
        game.submit_action(UseCardAction(game.player, attack, [game.enemy]))
        game.submit_action(SelectCardsAction(game.player, game.pending_request.request_id, [cost]))
        self.assertEqual(game.enemy.hp, 3)
        self.assertIn(cost, game.deck.discard_pile)

    def test_wugu_public_pool_selection_has_unique_owners(self):
        wugu, one, two = trick("WUGU"), tao(), normal_sha()
        game = make_test_game(player_hand=[wugu], draw_order=[one, two])
        game.submit_action(UseCardAction(game.player, wugu, [game.player, game.enemy]))
        self.assertEqual(len(game.public_card_pool), 2)
        game.submit_action(SelectCardsAction(game.player, game.pending_request.request_id, [one]))
        self.assertEqual(game.public_card_pool, [])
        self.assertIn(one, game.player.hand)
        self.assertIn(two, game.enemy.hand)

    def test_turn_flow_emits_complete_phase_order(self):
        game = make_test_game()
        starts = []
        game.context.events.subscribe(EventType.PHASE_START, lambda _ctx, event: starts.append(event.payload["phase"]))
        TurnFlow(game.engine, game.player).start()
        self.assertEqual(tuple(starts), TURN_PHASE_ORDER)

    def test_jiedao_transfers_weapon_when_no_third_target(self):
        card, weapon = trick("JIEDAO"), equipment("QINGGANG")
        game = make_test_game(player_hand=[card], enemy_equipment=[weapon])
        game.submit_action(UseCardAction(game.player, card, [game.enemy]))
        self.assertIn(weapon, game.player.hand)
        self.assertIsNone(game.enemy.get_equipment("weapon"))

    def test_pending_manager_preserves_outer_request(self):
        game = make_test_game()
        outer = game.engine.pending.create(PendingRequestType.CONFIRM, source=game.player, target=game.player, prompt="outer", owner_flow=object())
        inner = game.engine.pending.create(PendingRequestType.CONFIRM, source=game.player, target=game.player, prompt="inner", owner_flow=object())
        self.assertIs(game.pending_request, inner)
        game.engine.pending.take(inner.request_id)
        self.assertIs(game.pending_request, outer)

    def test_death_cleanup_discards_judgement_zone(self):
        game = make_test_game(enemy_hp=0)
        delayed = trick("LEBU")
        game.enemy.judgement_zone.append(delayed)
        game.engine.run_death(game.enemy)
        self.assertEqual(game.enemy.judgement_zone, [])
        self.assertIn(delayed, game.deck.discard_pile)

    def test_cixiong_only_triggers_when_owner_uses_sha(self):
        # Active Sha against an opposite-gender target opens Cixiong choice.
        attack = normal_sha()
        game = make_test_game(
            player_hand=[tao()],
            enemy_hand=[attack],
            enemy_equipment=[equipment("CIXIONG")],
        )
        game.phase = "enemy"
        game.submit_action(UseCardAction(game.enemy, attack, [game.player]))
        self.assertEqual(game.pending_request.context["reason"], "cixiong")

        # A Sha merely played as Duel response is consumed by RespondCardAction
        # and must not open a Cixiong request.
        duel, response_sha = trick("JUEDOU"), normal_sha()
        game = make_test_game(
            player_hand=[duel],
            enemy_hand=[response_sha],
            enemy_equipment=[equipment("CIXIONG")],
        )
        game.submit_action(UseCardAction(game.player, duel, [game.enemy]))
        self.assertEqual(game.pending_request.context["reason"], "duel")
        self.assertIs(game.pending_request.target, game.player)


if __name__ == "__main__":
    unittest.main()
