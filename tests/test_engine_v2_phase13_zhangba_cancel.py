"""丈八蛇矛选牌：三个状态都能点、取消能退出、AI 不趁玩家挑牌时行动。

两个已实测的失效模式：

* 上一名 AI 的动作队列／回调余波让 ``game.busy`` 一直是 True，``handle_game_click``
  在"玩家自己的交互槽位"之前就 return False —— 点来源牌没反应，界面停在
  "已选择 0/2"／"1/2"；
* 取消按钮必须始终可点（它走 ``Renderer.hit_action``），取消后实体牌留在手里、
  不计入出杀次数。

本文件全部走**真实点击链路**：``Renderer`` 的命中矩形 → ``handle_game_click``
→ ``LocalHumanController`` → ``Game`` 的公开入口。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.atoms_v2 import EquipCardAtom
from src.renderer import Renderer
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import equipment, normal_sha, shan, tao

ZHANGBA = "equipment.zhangba"
#: 验收窗口尺寸：桌面实际分辨率 + 用户报的那一个窄窗口。
WINDOW_SIZES = ((1206, 676), (1920, 1080))


class ZhangbaClickTests(unittest.TestCase):

    def setUp(self):
        pygame.init()

    def tearDown(self):
        pygame.display.quit()

    # ---- 装置 ----

    def make_game(self, screen, hands=("SHA", "TAO", "SHAN")):
        game = Game(ai_count=2)
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
        game.player.hand = [self.card(name) for name in hands]
        game.context.apply(EquipCardAtom(game.player, equipment("ZHANGBA")))
        pygame.event.clear()
        return game

    @staticmethod
    def card(name):
        return {"SHA": normal_sha, "TAO": tao, "SHAN": shan}[name]()

    def frame(self, game, renderer, mouse=None, count=1):
        for _ in range(count):
            game.update(1 / 60)
            renderer.update(1 / 60)
            renderer.draw(game, mouse)

    def click(self, position, game, renderer):
        """真实点击：先画一帧（表位与命中矩形同源），再分发事件。"""

        self.frame(game, renderer, position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}))
        consumed = False
        for event in pygame.event.get():
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                consumed = handle_game_click(event.pos, game, renderer)
        self.frame(game, renderer, position)
        return consumed

    def hand_center(self, game, renderer, index):
        self.frame(game, renderer)
        return renderer.get_card_rects(game.player.hand)[index].center

    def weapon_center(self, game, renderer):
        self.frame(game, renderer)
        return renderer.player_equipment_slot_rects(game)["weapon"].center

    def secondary_button_center(self, game, renderer):
        """底部第二个固定按钮（宽屏在右下，窄屏同样由布局给出）。"""

        self.frame(game, renderer)
        return renderer.actions_for(game)["secondary"].rect.center

    # ==================================================
    # 三个状态：未选牌 / 已选一张 / 已选两张进入目标选择
    # ==================================================

    def test_three_selection_states_with_real_clicks(self):
        for size in WINDOW_SIZES:
            with self.subTest(window=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                game = self.make_game(screen)

                # ---- 未选牌：点武器进入选牌 ----
                self.click(self.weapon_center(game, renderer), game, renderer)
                session = game.pending_view_as
                self.assertIsNotNone(session, "%s：点武器应当进入视为技选牌" % (size,))
                self.assertEqual(session.required_source_count, 2)
                self.assertEqual(session.selected_source_cards, [])
                self.assertIn("请选择 2 张牌", game.message, "应该提示还差几张")

                # ---- 已选一张 ----
                self.assertTrue(self.click(self.hand_center(game, renderer, 0), game, renderer),
                                "%s：选第一张来源牌应当被响应" % (size,))
                self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)
                self.assertIn("丈八蛇矛", game.message)

                # ---- 已选两张 → 进入目标选择 ----
                self.assertTrue(self.click(self.hand_center(game, renderer, 1), game, renderer))
                self.assertIsNone(game.pending_view_as, "%s：选满就该离开选牌态" % (size,))
                self.assertIsNotNone(game.pending_target_selection,
                                     "%s：选满两张应当进入目标选择" % (size,))
                self.assertFalse(game.player.sha_used, "还没确认使用，不该记为已出【杀】")

                # ---- 取消目标 → 回到正常出牌状态，牌还在手里 ----
                self.click(self.secondary_button_center(game, renderer), game, renderer)
                self.assertIsNone(game.pending_target_selection)
                self.assertIsNone(game.pending_view_as)
                self.assertEqual(len(game.player.hand), 3, "取消不该消耗实体牌")
                self.assertFalse(game.player.sha_used, "取消不该计入出杀次数")

                pygame.display.quit()
                pygame.init()

    # ==================================================
    # 取消技能
    # ==================================================

    def test_cancel_button_always_works_while_busy(self):
        """动画/结算余波让 busy 为 True 时，取消按钮照样能退出选牌。"""

        for size in WINDOW_SIZES:
            with self.subTest(window=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                game = self.make_game(screen)

                self.click(self.weapon_center(game, renderer), game, renderer)
                self.assertIsNotNone(game.pending_view_as)
                self.click(self.hand_center(game, renderer, 0), game, renderer)
                self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)

                # 人为制造"动作队列还在跑"的余波：这正是 AI 回合拖尾的样子。
                game.actions.add(_NeverEndingAction())
                self.assertTrue(game.busy)

                self.click(self.secondary_button_center(game, renderer), game, renderer)
                self.assertIsNone(game.pending_view_as, "%s：取消应当退出选牌" % (size,))
                self.assertEqual(len(game.player.hand), 3, "取消不该消耗实体牌")
                self.assertFalse(game.player.sha_used)
                self.assertIn("取消", game.message)

                pygame.display.quit()
                pygame.init()

    def test_hand_click_is_not_swallowed_while_busy(self):
        """AI 的动作余波不该吞掉丈八的来源牌点击（界面会卡在"已选择 0/2"）。"""

        screen = pygame.display.set_mode((1206, 676))
        renderer = Renderer(screen)
        game = self.make_game(screen)

        self.click(self.weapon_center(game, renderer), game, renderer)
        self.assertIsNotNone(game.pending_view_as)

        game.actions.add(_NeverEndingAction())
        self.assertTrue(game.busy)

        consumed = self.click(self.hand_center(game, renderer, 0), game, renderer)
        self.assertTrue(consumed, "选来源牌的点击被 busy 吞掉了")
        self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)

    # ==================================================
    # AI 不趁玩家挑牌时行动
    # ==================================================

    def test_ai_does_not_act_while_the_human_picks_sources(self):
        """玩家在丈八选牌态里停留多帧，AI 的手牌与战报都不许动。"""

        screen = pygame.display.set_mode((1206, 676))
        renderer = Renderer(screen)
        game = self.make_game(screen)
        for player in game.players[1:]:
            player.hand = [normal_sha(), tao()]

        self.click(self.weapon_center(game, renderer), game, renderer)
        self.click(self.hand_center(game, renderer, 0), game, renderer)

        hands_before = [len(player.hand) for player in game.players]
        log_before = len(game.game_log)
        turn_before = game.current_turn_player

        self.frame(game, renderer, count=180)

        self.assertEqual([len(player.hand) for player in game.players], hands_before,
                         "玩家挑来源牌时 AI 的手牌变了")
        self.assertEqual(len(game.game_log), log_before, "玩家挑来源牌时 AI 行动了")
        self.assertIs(game.current_turn_player, turn_before, "回合不该换人")
        self.assertIsNotNone(game.pending_view_as, "选牌态不该自己消失")


class _NeverEndingAction:
    """永远播不完的动作：模拟"AI 那边还有动画／等待在跑"。"""

    def update(self, dt):
        return False


if __name__ == "__main__":
    unittest.main()
