"""Phase 8：真人改判（鬼才）的 UI 链路测试。

走的是与 main.py 同一份点击路由（``src.ui.interaction``），因此"测试里
点得到"就等价于"真人点得到"：

* 判定窗口打开时固定按钮提供「跳过」（= 放弃本次改判）
* 点击手牌 = 用这张实体牌替换判定牌
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.flows.judge import JudgeFlow
from src.renderer import Renderer
from src.ui.interaction import handle_game_click, run_action
from tests.legacy_helpers import set_draw_order
from tests.test_engine_v2_phase8_judge_replacement import heart, spade


class JudgeReplacementUiTests(unittest.TestCase):
    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count=2):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.ai_pacing = True
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        game.current_turn_player = game.player
        game.set_general(game.player, "simayi")
        game.player.hp = game.player.max_hp
        pygame.event.clear()
        return game

    def frame(self, game, mouse=None, count=1):
        for _ in range(count):
            game.update(1 / 60)
            self.renderer.update(1 / 60)
            self.renderer.draw(game, mouse)

    def click(self, position, game):
        self.frame(game, position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}
        ))
        for event in pygame.event.get():
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                handle_game_click(event.pos, game, self.renderer)
        self.frame(game, position)

    def settle(self, game, frames=400):
        for _ in range(frames):
            if not game.busy:
                return True
            self.frame(game)
        return not game.busy

    def open_window(self, game, judged_card):
        set_draw_order(game, [judged_card])
        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        judge.start()
        self.assertIsNotNone(game.pending_request)
        self.assertIs(game.pending_request.target, game.player)
        return judge

    # ==================================================
    # Smoke 1：真人 Pass
    # ==================================================

    def test_the_fixed_skip_button_passes_the_replacement(self):
        game = self.make_game()
        held = heart("K")
        game.player.hand = [held]
        original = spade("7")
        judge = self.open_window(game, original)

        self.frame(game)
        actions = self.renderer.actions_for(game)
        self.assertEqual(actions["secondary"].label, "跳过")
        self.assertTrue(actions["secondary"].enabled)
        self.assertEqual(
            self.renderer.hit_action(actions["secondary"].rect.center, game),
            "pass_selection",
        )

        self.click(actions["secondary"].rect.center, game)
        self.settle(game)

        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.card, original)
        self.assertIn(held, game.player.hand)
        self.assertIsNone(game.pending_request)

    # ==================================================
    # Smoke 2：真人 Replace
    # ==================================================

    def test_clicking_a_hand_card_replaces_the_judgement(self):
        game = self.make_game()
        replacement = heart("K")
        game.player.hand = [replacement]
        original = spade("7")
        judge = self.open_window(game, original)

        self.frame(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        self.click(rects[0].center, game)
        self.settle(game)

        self.assertTrue(judge.result.replaced)
        self.assertIs(judge.result.card, replacement)
        self.assertNotIn(replacement, game.player.hand)
        self.assertIn(replacement, game.deck.discard_pile)
        self.assertIn(original, game.deck.discard_pile)
        self.assertIsNone(game.pending_request)

    # ==================================================
    # 取消语义：窗口未打开时按钮不可用
    # ==================================================

    def test_the_skip_button_is_only_offered_when_zero_cards_are_allowed(self):
        game = self.make_game()
        self.frame(game)
        actions = self.renderer.actions_for(game)
        self.assertNotEqual(actions["secondary_action"], "pass_selection")

        game.player.hand = [heart("K"), spade("3")]
        game.start_card_selection(
            zone="hand",
            owner=game.player,
            candidates=[(game.player.hand[0], None)],
            number=1,
            prompt="请选择一张手牌",
            on_complete=lambda selected: None,
        )
        self.assertFalse(game.can_cancel_pending_selection())
        self.frame(game)
        actions = self.renderer.actions_for(game)
        self.assertNotEqual(actions["secondary_action"], "pass_selection")
        self.assertFalse(actions["secondary"].enabled)

    def test_cancel_pending_selection_is_a_noop_for_mandatory_requests(self):
        game = self.make_game()
        game.player.hand = [heart("K")]
        game.start_card_selection(
            zone="hand",
            owner=game.player,
            candidates=[(game.player.hand[0], None)],
            number=1,
            prompt="请选择一张手牌",
            on_complete=lambda selected: None,
        )
        self.assertFalse(game.cancel_pending_selection())
        self.assertIsNotNone(game.pending_selection)


if __name__ == "__main__":
    unittest.main()
