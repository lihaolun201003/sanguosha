"""Phase 8 第一批武将：真人 UI 链路测试。

统一走 ``src.ui.interaction.handle_game_click``（与 main.py 同一份路由），
因此"测试里点得到"就等价于"真人点得到"。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.rules import TurnPhase
from src.renderer import Renderer
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import set_draw_order, shan, tao


class FirstRosterUiTests(unittest.TestCase):
    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    # ---- 基础设施 ----

    def make_game(self, general=None, ai_count=2):
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
        if general:
            game.set_general(game.player, general)
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

    def click_button(self, game, name):
        self.frame(game)
        actions = self.renderer.actions_for(game)
        button = actions[name]
        self.assertTrue(button.enabled, name + " 按钮应当可用")
        self.click(button.rect.center, game)
        return actions

    def settle(self, game, frames=400):
        for _ in range(frames):
            if not game.busy:
                return True
            self.frame(game)
        return not game.busy

    def start_turn(self, game):
        from src.game.flows import TurnFlow

        flow = TurnFlow(game.engine, game.player)
        flow.begin_interactive()
        return flow

    # ==================================================
    # 突袭
    # ==================================================

    def test_human_tuxi_through_the_real_ui(self):
        game = self.make_game("zhangliao", ai_count=3)
        zhangliao = game.player
        first, second = game.players[1], game.players[2]
        first.hand = [tao()]
        second.hand = [shan()]
        set_draw_order(game, [tao() for _ in range(4)])

        flow = self.start_turn(game)
        # 摸牌阶段的询问弹窗：发动
        self.assertTrue(game.choice.active)
        self.assertIn("突袭", game.choice.current.prompt)
        game.choice.choose_yes()
        self.frame(game)

        selection = game.pending_target_selection
        self.assertIsNotNone(selection, "发动后进入选目标")
        self.assertEqual(selection["maximum"], 2)
        self.assertEqual(sorted(target.name for target in selection["candidates"]),
                         sorted([first.name, second.name]))

        # 点击两个角色面板（真实点击路由）
        panels = self.renderer.get_player_panel_rects(game)
        for target in (first, second):
            self.assertIs(self.renderer.player_at_position(panels[target].center, game), target)
            self.click(panels[target].center, game)
        self.assertEqual(len(game.pending_target_selection["selected"]), 2)

        # 固定按钮「确认目标」
        self.click_button(game, "primary")
        self.settle(game)

        self.assertEqual(zhangliao.hp * 0 + len(zhangliao.hand), 2, "突袭拿到两张手牌")
        self.assertTrue(flow.phase_control.is_skipped(TurnPhase.DRAW), "放弃摸牌")
        self.assertEqual(len(first.hand), 0)
        self.assertEqual(len(second.hand), 0)

    def test_human_can_cancel_tuxi_through_the_ui(self):
        game = self.make_game("zhangliao", ai_count=3)
        target = game.players[1]
        target.hand = [tao(), tao()]
        set_draw_order(game, [shan(), shan(), shan()])

        flow = self.start_turn(game)
        game.choice.choose_yes()
        self.frame(game)
        self.assertIsNotNone(game.pending_target_selection)
        self.assertEqual(game.message.find("突袭") >= 0, True)

        # 固定按钮「取消目标」
        self.click_button(game, "secondary")
        self.settle(game)

        self.assertFalse(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(target.hand), 2, "取消后不拿别人的牌")
        self.assertEqual(len(game.player.hand), 2, "取消后正常摸两张")

    def test_human_can_decline_tuxi_in_the_choice_modal(self):
        game = self.make_game("zhangliao", ai_count=2)
        game.players[1].hand = [tao()]
        set_draw_order(game, [shan(), shan()])

        flow = self.start_turn(game)
        self.assertTrue(game.choice.active)
        game.choice.choose_no()
        self.settle(game)

        self.assertFalse(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(game.player.hand), 2)

    # ==================================================
    # 反间 / 结姻：现有主动技入口
    # ==================================================

    def test_fanjian_activation_through_the_skill_entry(self):
        game = self.make_game("zhouyu", ai_count=2)
        zhouyu = game.player
        zhouyu.hand = [tao()]
        target = game.players[1]
        target.hp = target.max_hp
        target.hand = []

        self.frame(game)
        self.assertTrue(game.start_skill_activation("fanjian"), "反间入口仍可进入")
        self.assertIsNotNone(game.pending_skill_input)
        self.assertIn("反间", game.message)

        game.toggle_skill_target(target)
        self.assertTrue(game.skill_input_ready())
        self.assertTrue(game.confirm_skill_input())
        self.settle(game)

        # 反间需要周瑜选一张手牌交给目标（真人 Pending）
        self.assertIsNotNone(game.pending_selection, "进入交牌选择")
        game.select_pending_card(zhouyu.hand[0], (0, 0, 80, 120))
        self.settle(game)

        self.assertEqual(target.hp, target.max_hp - 1, "没有同花色手牌 → 受伤")

    def test_jieyin_activation_through_the_skill_entry(self):
        game = self.make_game("sunshangxiang", ai_count=2)
        sun = game.player
        sun.max_hp = 3
        sun.hp = 1
        sun.hand = [tao(), tao()]
        partner = game.players[1]
        partner.gender = "male"
        partner.hp = partner.max_hp - 1

        self.frame(game)
        self.assertTrue(game.start_skill_activation("jieyin"))
        game.toggle_skill_target(partner)
        for card in list(sun.hand):
            game.select_skill_cost_card(card)
        self.assertTrue(game.skill_input_ready())
        self.assertTrue(game.confirm_skill_input())
        self.settle(game)

        self.assertEqual(sun.hp, 2)
        self.assertEqual(partner.hp, partner.max_hp)

    # ==================================================
    # 武圣：必须先点技能
    # ==================================================

    def test_wusheng_needs_the_skill_entry_before_any_conversion(self):
        game = self.make_game("guanyu", ai_count=2)
        guanyu = game.player
        red = next(
            card for card in __import__("src.card_catalog", fromlist=["create_development_deck"])
            .create_development_deck()
            if card.card_color == "red" and card.name != "SHA"
        )
        guanyu.hand = [red]
        game.current_turn_player = guanyu
        game.phase = "play"

        self.frame(game)
        self.assertIsNone(game.pending_view_as)
        # 直接点实体红牌：进入普通出牌流程，而不是自动转换。
        self.click_hand_card(game, 0)
        self.assertIsNone(game.pending_view_as, "未点技能不会进入视为模式")
        picker = game.card_action_picker()
        labels = [option.label for option in picker]
        self.assertFalse(
            any("武圣" in str(label) for label in labels),
            "未点武圣时操作列表里不应出现转换选项：" + str(labels),
        )

    def click_hand_card(self, game, index):
        self.frame(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        self.click(rects[index].center, game)


if __name__ == "__main__":
    unittest.main()
