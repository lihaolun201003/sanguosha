"""Phase 9：开局流程的真人 UI 链路测试。

覆盖三条链路：

    UI 1  菜单 -> 标准身份 -> 人数 -> 身份展示 -> 继续 -> 选将 -> 对局
    UI 2  FFA -> 人数 -> 选将 -> 对局
    UI 3  身份模式结算面板 -> 全员身份展示 -> 重新开始
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.identity import Identity, identity_name
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui.general_select import GeneralSelectScreen
from src.ui.identity_reveal import IdentityRevealScreen


class SetupFlowUiTests(unittest.TestCase):
    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)
        self.menu = StartMenu(self.screen)
        self.general_select = GeneralSelectScreen(self.screen)
        self.identity_reveal = IdentityRevealScreen(self.screen)
        # 用真实分辨率布局，之后取到的按钮 rect 才是稳定的。
        self.menu.sync_layout(self.renderer.metrics)
        self.menu.sync_modes(Game().modes.list_modes())
        self.menu.sync_layout(self.renderer.metrics)

    def tearDown(self):
        pygame.display.quit()

    def new_game(self, ai_count=4, seed=5):
        game = Game(ai_count=ai_count)
        game.rng.seed(seed)
        game.actions.clear()
        return game

    def click_menu(self, game, position):
        self.menu.sync_layout(self.renderer.metrics)
        self.menu.sync_modes(game.modes.list_modes())
        self.menu.sync_layout(self.renderer.metrics)
        game.actions.clear()
        return self.menu.handle_click(position, game)

    # ==================================================
    # UI 1：身份模式完整开局
    # ==================================================

    def test_identity_setup_through_the_real_menu(self):
        game = self.new_game(2)
        self.assertEqual(game.scene, "menu")

        # 切到标准身份
        modes = {cls.id: cls for cls in game.modes.list_modes()}
        self.assertTrue(
            self.menu.mode_button("identity").rect.width > 10,
            "模式按钮必须已经布局")
        action = self.click_menu(game, self.menu.mode_button("identity").rect.center)
        self.assertEqual(action, "mode")
        self.assertEqual(game.mode_id, "identity")
        self.assertIn(game.total_players(), game.allowed_player_counts())

        # 人数：± 只在身份模式允许的人数里移动
        self.click_menu(game, self.menu.plus_button.rect.center)
        self.assertIn(game.total_players(), (5, 6, 7, 8))

        # 开始游戏 -> 身份展示
        action = self.click_menu(game, self.menu.start_button.rect.center)
        self.assertEqual(action, "select_general")
        self.assertEqual(game.scene, "identity_reveal")
        self.assertIsNotNone(game.player.identity)

        # 身份展示界面
        self.identity_reveal.sync_layout(self.renderer.metrics)
        self.assertTrue(self.identity_reveal.panel_rect.width > 10)
        self.assertIsNone(self.identity_reveal.handle_click((0, 0), game))
        self.assertEqual(
            self.identity_reveal.handle_click(
                self.identity_reveal.continue_button.rect.center, game),
            "continue")
        self.assertTrue(game.confirm_identity())
        self.assertEqual(game.scene, "general_select")

        # 选将：只显示候选
        shown = game.selectable_generals()
        self.general_select.sync_layout(self.renderer.metrics, shown)
        self.assertTrue(shown)
        self.assertEqual(len(self.general_select.generals), len(shown))
        chosen = shown[1]
        self.assertEqual(
            self.general_select.handle_click(
                self.general_select.card_rects[1].center, game),
            "select")
        self.assertEqual(game.selected_general, chosen.id)
        self.assertEqual(
            self.general_select.handle_click(
                self.general_select.confirm_button.rect.center, game),
            "confirm")
        game.confirm_general()

        # 正式对局
        self.assertEqual(game.scene, "game")
        lord = game.mode.lord()
        self.assertIsNotNone(lord)
        self.assertTrue(lord.identity_revealed)
        self.assertIs(game.current_turn_player, lord)

    def test_mode_switch_changes_the_player_count_range_in_the_menu(self):
        game = self.new_game(1)
        self.click_menu(game, self.menu.mode_button("ffa").rect.center)
        self.assertEqual(game.allowed_player_counts(), tuple(range(2, 9)))
        self.click_menu(game, self.menu.mode_button("identity").rect.center)
        self.assertEqual(game.allowed_player_counts(), (5, 6, 7, 8))
        self.assertGreaterEqual(game.total_players(), 5)

    # ==================================================
    # UI 2：FFA 开局
    # ==================================================

    def test_ffa_setup_still_works_through_the_menu(self):
        game = self.new_game(3)
        self.assertEqual(game.mode_id, "ffa")

        self.assertEqual(
            self.click_menu(game, self.menu.minus_button.rect.center), "count")
        self.assertEqual(game.total_players(), 3)

        self.assertEqual(
            self.click_menu(game, self.menu.start_button.rect.center),
            "select_general")
        self.assertEqual(game.scene, "general_select", "FFA 不需要身份展示")

        self.general_select.sync_layout(self.renderer.metrics, game.selectable_generals())
        self.general_select.handle_click(self.general_select.card_rects[0].center, game)
        game.confirm_general()

        self.assertEqual(game.scene, "game")
        self.assertTrue(all(player.identity is None for player in game.players))
        self.assertIs(game.current_turn_player, game.player)

    # ==================================================
    # UI 3：结算面板
    # ==================================================

    def test_identity_result_overlay_lists_every_identity(self):
        game = self.new_game(4)
        game.set_mode("identity")
        game.begin_general_select()
        game.confirm_identity()
        game.selected_general = game.general_candidates[0]
        game.confirm_general()

        # 直接结束对局，检查结算面板的内容
        game.mode.reveal_all()
        game.game_over = True
        game.phase = "over"
        from src.game.engine.state import GameOutcome, GameResult

        game.result = GameResult(
            outcome=GameOutcome.AI_WIN, winner=None, loser=None, reason="REBEL_WIN")

        self.renderer.result_overlay.layout(self.renderer.metrics)
        rows = self.renderer.result_overlay.identity_rows(game)
        self.assertEqual(len(rows), len(game.players))
        for row in rows:
            self.assertTrue(row["identity"], row["name"] + " 的身份必须公开")
            self.assertIn(
                row["identity"],
                [identity_name(identity) for identity in Identity],
            )

        title, subtitle, _color = self.renderer.result_overlay.result_texts(game)
        self.assertIn("反贼获胜", subtitle)
        self.assertIn("你是", subtitle)

    def test_result_overlay_restart_returns_to_the_menu(self):
        from src.ui.interaction import run_action

        game = self.new_game(4)
        game.set_mode("identity")
        game.begin_general_select()
        game.confirm_identity()
        game.selected_general = game.general_candidates[0]
        game.confirm_general()
        self.assertEqual(game.scene, "game")

        run_action("restart", game, self.renderer)

        self.assertEqual(game.scene, "menu")
        self.assertTrue(all(player.identity is None for player in game.players))
        self.assertTrue(all(player.general_id is None for player in game.players))
        self.assertFalse(game.game_over)

    def test_ffa_result_overlay_keeps_the_old_layout(self):
        game = self.new_game(2)
        game.start_local_battle(2)
        game.game_over = True
        game.winner = game.player
        from src.game.engine.state import GameOutcome, GameResult

        game.result = GameResult(
            outcome=GameOutcome.PLAYER_WIN, winner=game.player, loser=None,
            reason="LAST_SURVIVOR")

        self.renderer.result_overlay.layout(self.renderer.metrics)
        self.assertEqual(self.renderer.result_overlay.identity_rows(game), ())
        title, subtitle, _color = self.renderer.result_overlay.result_texts(game)
        self.assertEqual(title, "胜 利")


if __name__ == "__main__":
    unittest.main()
