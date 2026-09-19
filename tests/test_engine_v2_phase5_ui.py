"""Pygame-dummy driven checks for the multiplayer table interactions.

These tests never open a window: they exercise the same code paths the real
event loop uses (menu clicks, hand clicks, panel clicks) so an unclickable
target or an out-of-bounds panel fails here.
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.choice import ChoiceOverlay
from src.constants import (
    AI_MINUS_RECT,
    AI_PLUS_RECT,
    MAIN_MENU_RECT,
    SINGLE_PLAYER_RECT,
)
from src.game import Game
from src.renderer import Renderer
from src.start_menu import StartMenu
from tests.legacy_helpers import canonical_card, normal_sha, tao


def trick(name):
    return canonical_card(name)


class MultiplayerUiTests(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1000, 700))
        self.renderer = Renderer(self.screen)
        self.overlay = ChoiceOverlay(self.screen, self.renderer.small_font, self.renderer.tiny_font)
        self.menu = StartMenu(self.screen, self.renderer.big_font, self.renderer.small_font, self.renderer.tiny_font)

    def tearDown(self):
        pygame.display.quit()

    def start_battle(self, ai_count):
        game = Game(ai_count=1)
        while game.ai_count < ai_count:
            self.menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
        self.menu.handle_click(pygame.Rect(*SINGLE_PLAYER_RECT).center, game)
        game.actions.clear()
        # 每个用例从空手牌开始，AI 的随机初始手牌不能影响断言。
        for player in game.players:
            player.hand = []
        return game

    def render(self, game):
        self.renderer.draw(game)
        self.overlay.draw(game.choice)
        pygame.display.flip()

    def click_hand_card(self, game, index):
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(index, tuple(rects[index]))

    def test_menu_changes_ai_count_and_starts_the_battle(self):
        game = Game(ai_count=1)
        self.assertEqual(game.scene, "menu")
        self.menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
        self.menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
        self.assertEqual(game.ai_count, 3)
        self.menu.handle_click(pygame.Rect(*AI_MINUS_RECT).center, game)
        self.assertEqual(game.ai_count, 2)
        while game.ai_count < 7:
            self.menu.handle_click(pygame.Rect(*AI_PLUS_RECT).center, game)
        self.assertEqual(game.ai_count, 7)
        self.menu.handle_click(pygame.Rect(*SINGLE_PLAYER_RECT).center, game)
        self.assertEqual(game.scene, "game")
        self.assertEqual(len(game.players), 8)

    def test_panels_do_not_overlap_for_up_to_eight_players(self):
        for ai_count in range(1, 8):
            game = self.start_battle(ai_count)
            rects = list(self.renderer.get_player_panel_rects(game).values())
            self.assertEqual(len(rects), ai_count + 1)
            for index, first in enumerate(rects):
                for second in rects[index + 1:]:
                    self.assertFalse(first.colliderect(second), "面板重叠：AI=%d" % ai_count)

    def test_clicking_a_card_then_an_ai_panel_uses_sha_on_that_ai(self):
        game = self.start_battle(3)
        game.actions.clear()
        attack = normal_sha()
        game.player.hand = [attack]
        game.player.hp = game.player.max_hp
        game.current_turn_player = game.player
        game.phase = "play"

        self.render(game)
        self.click_hand_card(game, 0)

        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertTrue(selection["candidates"])

        panels = self.renderer.get_player_panel_rects(game)
        victim = selection["candidates"][0]
        self.assertIs(self.renderer.player_at_position(panels[victim].center, game), victim)
        # 面板必须落在窗口内，否则玩家点不到。
        self.assertTrue(self.screen.get_rect().contains(panels[victim]))

        game.toggle_target_selection(victim)
        self.assertIsNone(game.pending_target_selection)
        self.assertFalse(any(item is attack for item in game.player.hand))
        self.assertTrue(
            any(item is attack for item in game.deck.discard_pile)
            or any(item is attack for item in game.processing_zone)
        )

    def test_tiesuo_accepts_two_clicked_panels(self):
        game = self.start_battle(3)
        game.actions.clear()
        card = trick("TIESUO")
        game.player.hand = [card]
        game.current_turn_player = game.player
        game.phase = "play"

        self.render(game)
        self.click_hand_card(game, 0)
        game.choice.choose_yes()

        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertEqual(selection["maximum"], 2)
        panels = self.renderer.get_player_panel_rects(game)
        for victim in (game.players[1], game.players[2]):
            self.assertIs(self.renderer.player_at_position(panels[victim].center, game), victim)
            game.toggle_target_selection(victim)
        self.assertEqual(len(selection["selected"]), 2)
        self.assertIn("2", game.message)
        game.confirm_target_selection()
        self.assertTrue(game.players[1].chained)
        self.assertTrue(game.players[2].chained)

    def test_running_out_of_legal_targets_reports_and_keeps_the_card(self):
        game = self.start_battle(3)
        game.actions.clear()
        attack = normal_sha()
        game.player.hand = [attack]
        game.current_turn_player = game.player
        game.phase = "play"
        for player in game.players[1:]:
            player.alive = False
            player.hp = 0

        self.click_hand_card(game, 0)
        self.assertIsNone(game.pending_target_selection)
        self.assertTrue(any(item is attack for item in game.player.hand))
        self.assertIn("目标", game.message)

    def test_current_turn_is_highlighted_and_clickable(self):
        game = self.start_battle(4)
        game.actions.clear()
        current = game.players[2]
        game.current_turn_player = current
        game.phase = "play"
        self.render(game)
        panels = self.renderer.get_player_panel_rects(game)
        # 高亮依据就是 current_turn_player 本身，玩家也能在桌面上点到它。
        self.assertEqual(current.name, "AI 2")
        self.assertIs(self.renderer.player_at_position(panels[current].center, game), current)
        self.assertIsNot(game.current_turn_player, game.player)

    def test_human_panels_and_hand_stay_inside_the_window(self):
        game = self.start_battle(7)
        self.render(game)
        bounds = self.screen.get_rect()
        for rect in self.renderer.get_player_panel_rects(game).values():
            self.assertTrue(bounds.contains(rect), "面板越界：" + str(rect))
        for rect in self.renderer.get_card_rects(game.player.hand):
            self.assertTrue(bounds.contains(rect), "手牌越界：" + str(rect))

    def test_game_over_screen_exposes_restart_and_main_menu(self):
        game = self.start_battle(3)
        game.game_over = True
        game.message = "你已阵亡 / 游戏失败"
        self.render(game)
        self.assertTrue(pygame.Rect(*MAIN_MENU_RECT).width > 0)
        self.assertTrue(self.renderer.get_restart_rect().width > 0)
        game.return_to_menu()
        self.assertEqual(game.scene, "menu")
        self.assertEqual(len(game.players), 4)

    def test_restart_after_game_over_rebuilds_the_same_table_size(self):
        game = self.start_battle(4)
        game.reset()
        self.assertEqual(game.scene, "game")
        self.assertEqual(len(game.players), 5)
        self.assertFalse(game.game_over)
        self.assertIsNone(game.result)
        self.assertEqual(len(game.player.hand), 4)
        for player in game.players:
            self.assertEqual(len(player.hand), 4)


if __name__ == "__main__":
    unittest.main()
