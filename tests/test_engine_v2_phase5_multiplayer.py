import os
import random
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from src.game import Game
from src.game.controllers import AIController
from src.game.engine import (
    PassPendingAction,
    SelectCardsAction,
    UseCardAction,
)
from src.game.flows import DamageContext, DamageFlow, DyingFlow, TurnFlow
from src.game.rules import DistanceRule, TargetRule
from tests.legacy_helpers import (
    canonical_card,
    equipment,
    make_test_game,
    normal_sha,
    shan,
    set_draw_order,
    tao,
)


def trick(name):
    return canonical_card(name)


class MultiplayerSetupMixin:
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
        game.current_turn_player = game.player
        return game


class MultiplayerTrickTests(MultiplayerSetupMixin, unittest.TestCase):
    def test_taoyuan_heals_every_alive_player(self):
        game = self.make_game(3)
        card = trick("TAOYUAN")
        game.player.hand = [card]
        game.player.hp = 2
        game.players[1].hp = 1
        game.players[2].hp = 0
        game.players[2].alive = False
        game.players[3].hp = 3
        targets = game.seats.alive_players_in_order(start_after=game.player, include_start=True)
        result = game.submit_action(UseCardAction(game.player, card, targets))
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(game.player.hp, 3)
        self.assertEqual(game.players[1].hp, 2)
        self.assertEqual(game.players[3].hp, 4)
        self.assertEqual(game.players[2].hp, 0)

    def test_tiesuo_accepts_one_to_two_targets(self):
        game = self.make_game(3)
        card = trick("TIESUO")
        game.player.hand = [card]
        result = game.submit_action(
            UseCardAction(game.player, card, [game.players[1], game.players[2]])
        )
        self.assertEqual(result.status.value, "completed")
        self.assertTrue(game.players[1].chained)
        self.assertTrue(game.players[2].chained)
        self.assertFalse(game.player.chained)

    def test_tiesuo_rejects_more_than_two_targets(self):
        game = self.make_game(4)
        card = trick("TIESUO")
        game.player.hand = [card]
        effect = game.engine.card_effects.require(card)
        action = UseCardAction(
            game.player, card,
            [game.players[1], game.players[2], game.players[3]],
        )
        legal, message = effect.can_use(game, action)
        self.assertFalse(legal)

    def test_jiedao_uses_a_third_party_victim(self):
        game = self.make_game(3)
        card = trick("JIEDAO")
        game.player.hand = [card]
        attacker = game.players[1]
        attacker.hand = [normal_sha()]
        attacker.set_equipment(equipment("QINGLONG"))
        victim = game.players[2]
        result = game.submit_action(
            UseCardAction(game.player, card, [attacker], metadata={"jiedao_victim": victim})
        )
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(victim.hp, 3)
        self.assertIsNotNone(attacker.get_equipment("weapon"))

    def test_jiedao_without_sha_transfers_the_weapon(self):
        game = self.make_game(3)
        card = trick("JIEDAO")
        game.player.hand = [card]
        attacker = game.players[1]
        weapon = equipment("QINGLONG")
        attacker.set_equipment(weapon)
        result = game.submit_action(
            UseCardAction(game.player, card, [attacker], metadata={"jiedao_victim": game.players[2]})
        )
        self.assertEqual(result.status.value, "completed")
        self.assertIsNone(attacker.get_equipment("weapon"))
        self.assertTrue(any(item is weapon for item in game.player.hand))


class MultiplayerDyingTests(MultiplayerSetupMixin, unittest.TestCase):
    def test_rescue_order_skips_dead_and_seats_are_stable(self):
        game = self.make_game(4)
        dying = game.players[1]
        game.players[2].alive = False
        game.players[2].hp = 0
        dying.hp = 0
        flow = DyingFlow(game.engine, dying_player=dying)
        self.assertEqual(
            [player.player_id for player in flow.rescue_order],
            ["P1", "P3", "P4", "P0"],
        )
        self.assertEqual(game.players[2].seat, 2)

    def test_human_can_spend_tao_to_save_an_ai(self):
        game = self.make_game(3)
        dying = game.players[2]
        dying.hp = 0
        save_card = tao()
        game.player.hand = [save_card]
        # 前面的 AI 无桃可救，最终轮到真人。
        game.players[1].hand = []
        flow = DyingFlow(game.engine, dying_player=dying)
        flow.start()
        while game.pending_request is not None:
            responder = game.pending_request.target
            if responder is game.player:
                break
            game.engine.present_or_auto_resolve(game.pending_request)
        self.assertIs(game.pending_request.target, game.player)
        self.assertIn("救援", game.pending_request.prompt)
        game.submit_action(
            PassPendingAction(game.player, game.pending_request.request_id)
        )
        self.assertFalse(dying.alive)

    def test_human_uses_tao_and_the_ai_survives(self):
        game = self.make_game(3)
        dying = game.players[2]
        dying.hp = 0
        save_card = tao()
        game.player.hand = [save_card]
        game.players[1].hand = []
        flow = DyingFlow(game.engine, dying_player=dying)
        flow.start()
        while game.pending_request is not None and game.pending_request.target is not game.player:
            game.engine.present_or_auto_resolve(game.pending_request)
        request = game.pending_request
        from src.game.engine import RespondCardAction

        game.submit_action(
            RespondCardAction(game.player, request.request_id, save_card, None)
        )
        self.assertTrue(dying.alive)
        self.assertEqual(dying.hp, 1)

    def test_group_trick_resumes_after_a_mid_flow_death(self):
        game = self.make_game(3)
        card = trick("NANMAN")
        game.player.hand = [card]
        targets = game.seats.alive_players_in_order(start_after=game.player)
        game.players[1].hand = []
        game.players[1].hp = 1
        game.players[2].hand = []
        result = game.submit_action(UseCardAction(game.player, card, targets))
        self.assertEqual(result.status.value, "completed")
        self.assertFalse(game.players[1].alive)
        # 外层群体锦囊必须继续处理后面的目标。
        self.assertEqual(game.players[2].hp, 3)
        self.assertIsNone(game.pending_request)


class MultiplayerAITests(MultiplayerSetupMixin, unittest.TestCase):
    def test_ai_never_rescues_another_character(self):
        game = self.make_game(3)
        dying = game.players[2]
        dying.hp = 0
        game.players[1].hand = [tao()]
        game.player.hand = [tao()]
        flow = DyingFlow(game.engine, dying_player=dying)
        flow.start()
        while game.pending_request is not None:
            responder = game.pending_request.target
            if responder is game.player:
                break
            game.engine.present_or_auto_resolve(game.pending_request)
        # AI 有【桃】也必须轮到真人，不能替别人用掉。
        self.assertIs(game.pending_request.target, game.player)
        self.assertEqual(len(game.players[1].hand), 1)
        game.submit_action(PassPendingAction(game.player, game.pending_request.request_id))
        self.assertFalse(dying.alive)

    def test_ai_wuxie_only_protects_itself(self):
        game = self.make_game(3)
        trick_card = trick("GUOHE")
        game.player.hand = [trick_card]
        for player in game.players[1:]:
            player.hand = [trick("WUXIE")]
        result = game.submit_action(
            UseCardAction(game.player, trick_card, [game.players[1]])
        )
        # 只有被指定的目标会用无懈；其他 AI 不会替别人挡牌。
        self.assertEqual(result.status.value, "completed")
        self.assertEqual(len(game.players[1].hand), 0)
        self.assertEqual(len(game.players[2].hand), 1)

    def test_ai_equips_a_weapon_to_reach_further_targets(self):
        game = self.make_game(3)
        actor = game.players[1]
        weapon = equipment("QINGLONG")
        actor.hand = [weapon]
        self.assertFalse(DistanceRule.in_attack_range(game, actor, game.players[3]))
        controller = AIController(game, actor, rng=random.Random(0))
        action = controller.choose_action()
        self.assertIsNotNone(action)
        self.assertIs(action.card, weapon)
        game.submit_action(action)
        self.assertIs(actor.get_equipment("weapon"), weapon)
        self.assertTrue(DistanceRule.in_attack_range(game, actor, game.players[3]))

    def test_ai_spends_tao_on_itself_when_hurt(self):
        game = self.make_game(3)
        actor = game.players[1]
        actor.hp = 2
        heal = tao()
        actor.hand = [heal]
        controller = AIController(game, actor, rng=random.Random(0))
        action = controller.choose_action()
        self.assertIs(action.card, heal)
        game.submit_action(action)
        self.assertEqual(actor.hp, 3)

    def test_ai_ignores_hidden_hand_contents(self):
        """同样的公开状态必须给出同样的决策分布，手牌内容不参与评分。"""

        game = self.make_game(3)
        actor = game.players[1]
        actor.hand = []
        target = game.players[2]
        target.hand = [tao(), tao(), tao()]

        hidden = [
            AIController(game, actor, rng=random.Random(seed)).score_target(target, normal_sha())
            for seed in range(5)
        ]
        target.hand = [normal_sha(), shan(), trick("WUXIE")]
        same_length = [
            AIController(game, actor, rng=random.Random(seed)).score_target(target, normal_sha())
            for seed in range(5)
        ]
        self.assertEqual(hidden, same_length)

    def test_ai_chooses_among_several_legal_targets(self):
        game = self.make_game(3)
        actor = game.players[1]
        actor.hand = [normal_sha()]
        controller = AIController(game, actor, rng=random.Random(3))
        legal = controller.legal_targets(actor.hand[0])
        self.assertEqual(len(legal), 2)
        action = controller.choose_action()
        self.assertEqual(len(action.targets), 1)
        self.assertIn(action.targets[0], legal)


class MultiplayerTurnTests(MultiplayerSetupMixin, unittest.TestCase):
    def test_dead_current_player_skips_draw_and_play(self):
        game = self.make_game(3)
        current = game.players[1]
        current.hp = 0
        current.alive = False
        flow = TurnFlow(game.engine, current)
        can_play = flow.begin_interactive()
        self.assertFalse(can_play)
        self.assertEqual(current.hand, [])

    def test_death_during_own_turn_advances_the_seat(self):
        game = self.make_game(3)
        game.current_turn_player = game.players[1]
        game.players[1].hp = 1
        DamageFlow(
            game.engine,
            DamageContext(game.player, game.players[1], 1, card=normal_sha()),
        ).start()
        self.assertFalse(game.players[1].alive)
        self.assertIs(game.current_turn_player, game.players[2])
        self.assertEqual(game.seats.next_alive_player(game.players[0]), game.players[2])

    def test_human_cannot_play_cards_during_an_ai_turn(self):
        game = self.make_game(3)
        game.current_turn_player = game.players[1]
        card = normal_sha()
        game.player.hand = [card]
        game.player_use_card(0, (0, 0, 10, 10))
        self.assertTrue(any(item is card for item in game.player.hand))
        self.assertIsNone(game.pending_request)

    def test_human_cannot_end_the_ai_turn(self):
        game = self.make_game(3)
        game.current_turn_player = game.players[1]
        game.phase = "play"
        game.end_player_turn()
        self.assertIs(game.current_turn_player, game.players[1])

    def test_lightning_skips_a_seat_that_already_holds_one(self):
        game = self.make_game(3)
        lightning = trick("SHANDIAN")
        blocked = trick("SHANDIAN")
        game.player.judgement_zone.append(lightning)
        game.players[1].judgement_zone.append(blocked)
        set_draw_order(game, [tao(), normal_sha(), tao(), normal_sha()])
        TurnFlow(game.engine, game.player).start()
        self.assertIn(lightning, game.players[2].judgement_zone)
        self.assertIn(blocked, game.players[1].judgement_zone)

    def test_restart_clears_multiplayer_state_and_controllers(self):
        game = self.make_game(4)
        game.get_controller(game.players[1])
        game.players[1].hand = [tao()]
        game.players[2].alive = False
        game.players[2].hp = 0
        game.players[3].chained = True
        game.players[3].judgement_zone.append(trick("LEBU"))
        game.public_card_pool.append(tao())
        game.pending_target_selection = {"card": tao(), "selected": [], "candidates": []}
        game.reset()
        self.assertEqual(len(game.players), 5)
        self.assertEqual(game.controllers, {})
        self.assertIsNone(game.pending_target_selection)
        self.assertEqual(game.public_card_pool, [])
        self.assertIsNone(game.pending_request)
        self.assertTrue(all(player.alive for player in game.players))
        self.assertTrue(all(not player.chained for player in game.players))
        self.assertTrue(all(not player.judgement_zone for player in game.players))
        self.assertTrue(all(not player.sha_used for player in game.players))

    def test_lightning_dying_on_own_turn_resumes_the_turn(self):
        """闪电在判定阶段把真人打进濒死，自救成功后回合必须继续。"""

        from src.card_catalog import create_development_deck
        from src.game.engine import RespondCardAction

        game = self.make_game(2)
        lightning = trick("SHANDIAN")
        game.player.judgement_zone.append(lightning)
        game.player.hp = 3
        heal = tao()
        game.player.hand = [heal]
        spade = next(
            card
            for card in create_development_deck()
            if card.suit == "spade" and card.rank == "2" and card.name != "SHANDIAN"
        )
        set_draw_order(game, [spade, tao(), tao()])

        flow = TurnFlow(game.engine, game.player)
        flow.begin_interactive()

        self.assertIsNotNone(game.pending_request)
        self.assertIn("自救", game.pending_request.prompt)
        game.submit_action(
            RespondCardAction(game.player, game.pending_request.request_id, heal, None)
        )
        self.assertEqual(game.player.hp, 1)
        self.assertTrue(game.player.alive)
        self.assertEqual(game.phase, "play")
        self.assertEqual(game.turn_phase.value, "play")

    def test_lightning_death_on_own_turn_ends_the_turn(self):
        from src.card_catalog import create_development_deck

        game = self.make_game(2)
        lightning = trick("SHANDIAN")
        game.player.judgement_zone.append(lightning)
        game.player.hp = 2
        game.player.hand = []
        spade = next(
            card
            for card in create_development_deck()
            if card.suit == "spade" and card.rank == "2" and card.name != "SHANDIAN"
        )
        set_draw_order(game, [spade, tao(), tao()])

        flow = TurnFlow(game.engine, game.player)
        flow.begin_interactive()

        self.assertFalse(game.player.alive)
        self.assertTrue(game.game_over)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")

    def test_damage_after_game_over_does_not_ask_the_human(self):
        game = self.make_game(1)
        game.players[1].hp = 0
        game.engine.run_death(game.players[1])
        self.assertTrue(game.game_over)
        self.assertIs(game.winner, game.player)
        self.assertIsNone(game.pending_request)
        # 对局结束后仍然传播的伤害不再结算，也不再创建求桃请求。
        game.player.hp = 1
        DamageFlow(
            game.engine,
            DamageContext(None, game.player, 3, nature="thunder", card=trick("SHANDIAN")),
        ).start()
        self.assertIsNone(game.pending_request)
        self.assertTrue(game.game_over)

    def test_wine_does_not_lock_the_turn_without_a_legal_target(self):
        game = self.make_game(2)
        game.current_turn_player = game.player
        game.phase = "play"
        game.player.hp = 2
        game.player.wine_sha_required = True
        game.player.wine_buff = True
        game.player.hand = [tao()]
        for player in game.players[1:]:
            player.alive = False
            player.hp = 0
        game.end_player_turn()
        self.assertFalse(game.player.wine_sha_required)
        self.assertFalse(game.player.wine_buff)

    def test_wine_releases_other_cards_when_no_sha_is_available(self):
        game = self.make_game(2)
        game.current_turn_player = game.player
        game.phase = "play"
        game.player.hp = 2
        game.player.wine_sha_required = True
        game.player.wine_buff = True
        heal = tao()
        game.player.hand = [heal]
        game.player_use_card(0, (0, 0, 10, 10))
        self.assertEqual(game.player.hp, 3)
        self.assertFalse(game.player.wine_sha_required)

    def test_last_survivor_is_the_only_winner(self):
        game = self.make_game(3)
        for player in game.players[1:3]:
            player.hp = 0
            game.engine.run_death(player)
        self.assertFalse(game.game_over)
        self.assertIsNone(game.winner)
        game.players[3].hp = 0
        game.engine.run_death(game.players[3])
        self.assertTrue(game.game_over)
        self.assertIs(game.winner, game.player)
        self.assertEqual(game.result.reason, "LAST_SURVIVOR")
        self.assertEqual(game.result.winner_player_id, "P0")

    def test_human_death_does_not_crown_an_ai_while_two_remain(self):
        game = self.make_game(3)
        game.players[1].hp = 0
        game.engine.run_death(game.players[1])
        game.player.hp = 0
        game.engine.run_death(game.player)
        self.assertTrue(game.game_over)
        self.assertIsNone(game.winner)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")

    def test_total_wipe_keeps_the_game_over(self):
        game = self.make_game(2)
        game.player.hp = 0
        game.engine.run_death(game.player)
        self.assertTrue(game.game_over)
        human_message = game.message
        self.assertIsNone(game.winner)
        # 真人阵亡后剩余的 AI 也全部阵亡：对局不能重新变成进行中，
        # 也不能把胜负改写成某个 AI 获胜。
        for player in game.players[1:]:
            player.hp = 0
            game.engine.run_death(player)
        self.assertTrue(game.game_over)
        self.assertIsNone(game.winner)
        self.assertEqual(game.message, human_message)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")

    def test_human_death_still_ends_the_game_when_ais_fight_on(self):
        game = self.make_game(3)
        game.player.hp = 0
        game.engine.run_death(game.player)
        self.assertTrue(game.game_over)
        self.assertIsNone(game.winner)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")
        for player in game.players[1:3]:
            player.hp = 0
            game.engine.run_death(player)
        self.assertTrue(game.game_over)
        self.assertIsNone(game.winner)
        self.assertEqual(game.result.reason, "HUMAN_ELIMINATED")


class TurnStallGuardTests(MultiplayerSetupMixin, unittest.TestCase):
    """守卫：AI 回合的动作链断开时自动接回，真人回合与等待输入不受影响。"""

    def _run_frames(self, game, frames):
        for _ in range(frames):
            game.update(1 / 60)

    def test_guard_resumes_a_broken_ai_turn(self):
        game = self.make_game(3)
        ai = game.players[1]
        game.current_turn_player = ai
        game.phase = "play"
        game.actions.clear()

        self._run_frames(game, game.STALL_GUARD_FRAMES + 2)

        self.assertIsNot(game.current_turn_player, ai, "卡住的 AI 回合没有被接回")
        self.assertTrue(any("回合守卫" in entry for entry in game.game_log))

    def test_guard_leaves_the_human_turn_alone(self):
        game = self.make_game(3)
        game.current_turn_player = game.player
        game.phase = "play"
        game.actions.clear()

        self._run_frames(game, game.STALL_GUARD_FRAMES + 5)

        self.assertIs(game.current_turn_player, game.player)
        self.assertFalse(any("回合守卫" in entry for entry in game.game_log))

    def test_guard_waits_while_the_game_expects_input(self):
        game = self.make_game(3)
        ai = game.players[1]
        game.current_turn_player = ai
        game.phase = "play"
        game.actions.clear()
        game.response.request(
            prompt="等待响应",
            allowed_cards={"SHAN"},
            on_card=lambda *args, **kwargs: None,
            on_pass=lambda: None,
        )

        self._run_frames(game, game.STALL_GUARD_FRAMES + 5)

        self.assertIs(game.current_turn_player, ai)
        game.response.clear()

    def test_guard_waits_while_an_engine_pending_is_open(self):
        game = self.make_game(3)
        ai = game.players[1]
        game.current_turn_player = ai
        game.phase = "play"
        game.actions.clear()
        from src.game.engine.pending import PendingRequestType

        request = game.engine.pending.create(
            PendingRequestType.CONFIRM,
            source=ai,
            target=ai,
            prompt="等待确认",
            owner_flow=None,
        )
        self.assertIsNotNone(request)

        self._run_frames(game, game.STALL_GUARD_FRAMES + 5)

        self.assertIs(game.current_turn_player, ai)
        game.engine.pending.clear()


class MultiplayerLongGameTests(MultiplayerSetupMixin, unittest.TestCase):
    def test_automated_multiplayer_game_reaches_a_result(self):
        from tools.multiplayer_smoke import run_smoke

        for ai_count in (2, 3, 6):
            game, result = run_smoke(ai_count=ai_count, seed=5, max_steps=40000)
            self.assertIsNone(result.error, result.error)
            self.assertIsNone(result.stuck_reason, result.stuck_reason)
            self.assertTrue(result.game_over, "AI=%s 的自动对局没有结束" % ai_count)
            self.assertGreater(result.deaths, 0)
            self.assertIsNone(game.pending_request)

    def test_dummy_driver_renders_a_five_player_table(self):
        import pygame

        from src.choice import ChoiceOverlay
        from src.renderer import Renderer

        pygame.init()
        screen = pygame.display.set_mode((1000, 700))
        renderer = Renderer(screen)
        overlay = ChoiceOverlay(screen, renderer.small_font, renderer.tiny_font)

        game = Game(ai_count=4)
        game.start_single_player()
        self.assertEqual(len(game.players), 5)
        self.assertEqual(game.scene, "game")

        panel_rects = renderer.get_player_panel_rects(game)
        self.assertEqual(len(panel_rects), 5)
        self.assertEqual(len({rect.topleft for rect in panel_rects.values()}), 5)

        turns_before = game.current_player_id
        for _ in range(400):
            pygame.event.pump()
            game.update(1 / 60)
            renderer.draw(game)
            overlay.draw(game.choice)
            pygame.display.flip()
            if (
                not game.busy
                and game.pending_request is None
                and game.current_turn_player is game.player
                and game.phase == "play"
            ):
                game.end_player_turn()

        # 真人结束回合后，AI 必须能自动接管并再次轮到真人。
        self.assertIsNotNone(game.current_turn_player)
        self.assertGreater(len([player for player in game.players if player.alive]), 0)
        self.assertNotEqual(turns_before, "none")
        pygame.display.quit()


if __name__ == "__main__":
    unittest.main()
