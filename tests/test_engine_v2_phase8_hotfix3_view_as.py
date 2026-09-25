"""Phase 8 Hotfix 3 tests：View-As 必须先点技能，再选牌。

覆盖用户要求的 10 项确定性场景，全部走真实鼠标点击
（``pygame.event.post`` + ``src.ui.interaction.handle_game_click``）。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import ActivateSkillAction
from src.game.skills.conversion_probes import PAIR_PROBE_ID, bind_conversion_probes
from src.renderer import Renderer
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import canonical_card, normal_sha, shan, tao


class ViewAsTestCase(unittest.TestCase):

    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, general=None, ai_count=3):
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

    # ---- 真实点击 ----

    def frame(self, game, mouse=None, count=1):
        for _ in range(count):
            game.update(1 / 60)
            self.renderer.update(1 / 60)
            self.renderer.draw(game, mouse)

    def click(self, position, game):
        self.frame(game, position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}))
        for event in pygame.event.get():
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                handle_game_click(event.pos, game, self.renderer)
        self.frame(game, position)

    def hand_center(self, game, index):
        self.frame(game)
        return self.renderer.get_card_rects(game.player.hand)[index].center

    def seat_center(self, game, player):
        self.frame(game)
        return self.renderer.table_layout.seat_rects[player].center

    def skill_button_center(self, game):
        self.frame(game)
        return self.renderer.skill_bar.button.rect.center

    def settle(self, game, frames=400):
        for _ in range(frames):
            if not game.busy:
                return True
            self.frame(game)
        return False

    def activate_skill(self, game, skill_id=None):
        """点技能区里的技能名（Phase 10.3 起直接发动，无中转面板）。"""

        self.frame(game)
        bar = self.renderer.skill_bar
        rect = bar.rect_for(skill_id) if skill_id else None
        if rect is None:
            rect = bar.button.rect
        self.click(rect.center, game)


# ==================================================
# 1. 未点技能时点击【闪】不得转换
# ==================================================


class NoImplicitViewAsTests(ViewAsTestCase):

    def test_zhaoyun_shan_without_skill_does_not_convert(self):
        game = self.make_game(general="zhaoyun")
        flash = shan()
        game.player.hand = [flash]

        self.click(self.hand_center(game, 0), game)

        self.assertIsNone(game.pending_target_selection)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_card_action)
        self.assertIs(flash, game.player.hand[0], "牌没有被消耗")
        self.assertIn("发动技能", game.message)

    def test_guanyu_red_card_without_skill_does_not_convert(self):
        game = self.make_game(general="guanyu")
        peach = tao()
        game.player.hand = [peach]
        game.player.hp = game.player.max_hp - 1

        # 正常使用【桃】：回血，而不是被【武圣】当成【杀】
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(game.player.hp, game.player.max_hp)
        self.assertIsNone(game.pending_target_selection)

        # 满血时【桃】既不能正常用、也不自动转成【杀】
        game2 = self.make_game(general="guanyu")
        peach2 = tao()
        game2.player.hand = [peach2]
        self.click(self.hand_center(game2, 0), game2)
        self.assertIsNone(game2.pending_target_selection)
        self.assertIsNone(game2.pending_view_as)
        self.assertIn(peach2, game2.player.hand)


# ==================================================
# 2. 点技能后：source 合法 + 完整结算
# ==================================================


class LongdanActivationTests(ViewAsTestCase):

    def test_longdan_makes_shan_a_valid_source(self):
        game = self.make_game(general="zhaoyun")
        flash, peach = shan(), tao()
        game.player.hand = [flash, peach]

        self.activate_skill(game)
        self.assertIsNotNone(game.pending_view_as, "单技能应直接进入")
        self.assertEqual(game.pending_view_as.skill_id, "longdan")
        self.assertIn("请选择", game.message)

        self.frame(game)
        self.assertEqual(self.renderer.playable, {0},
                         "只有【闪】可以作为来源")
        self.assertEqual(game.view_as_candidate_ids(), {id(flash)})

    def test_longdan_shan_to_sha_full_flow(self):
        game = self.make_game(general="zhaoyun")
        flash = shan()
        game.player.hand = [flash]
        target = game.players[1]
        target.hand = []

        self.activate_skill(game)
        self.click(self.hand_center(game, 0), game)
        self.assertIsNone(game.pending_view_as, "选完 source 进入目标选择")
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertTrue(selection["card"].is_virtual)
        self.assertEqual(selection["card"].name, "SHA")
        self.assertIs(selection["card"].source_cards[0], flash)

        self.click(self.seat_center(game, target), game)
        self.settle(game)
        self.assertIn(flash, game.deck.discard_pile)
        self.assertTrue(game.player.sha_used)
        self.assertTrue(any("龙胆" in line for line in game.game_log))


# ==================================================
# 3. 响应阶段同样要先点技能
# ==================================================


class ResponseViewAsTests(ViewAsTestCase):

    def make_response_game(self, card):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [card]
        return game

    def test_sha_without_skill_cannot_respond(self):
        game = self.make_response_game(normal_sha())
        submitted = []
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda index, card, rect: submitted.append(index),
            on_pass=lambda: submitted.append(None),
        )
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(submitted, [], "未点【龙胆】不得直接响应")
        self.assertIn("发动技能", game.message)

    def test_sha_to_shan_after_longdan(self):
        game = self.make_response_game(normal_sha())
        submitted = []
        game.response.request(
            prompt="【杀】：请打出一张【闪】",
            allowed_cards={"SHAN"},
            on_card=lambda index, card, rect: submitted.append(index),
            on_pass=lambda: submitted.append(None),
        )
        self.activate_skill(game)
        self.assertIsNotNone(game.pending_view_as)
        self.assertIn("打出", game.message)
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(submitted, [0], "点技能后【杀】当【闪】打出")


# ==================================================
# 4. Cancel：无副作用
# ==================================================


class ViewAsCancelTests(ViewAsTestCase):

    def test_cancel_has_no_side_effects(self):
        game = self.make_game(general="zhaoyun")
        flash = shan()
        game.player.hand = [flash]

        self.activate_skill(game)
        self.click(self.hand_center(game, 0), game)      # 收齐 → 目标选择
        self.assertIsNotNone(game.pending_target_selection)

        game.cancel_target_selection()
        self.frame(game)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)
        self.assertIs(flash, game.player.hand[0], "取消不弃牌")
        self.assertFalse(game.player.sha_used, "取消不消耗杀次数")
        self.assertFalse(any("龙胆" in line for line in game.game_log),
                         "取消不写战报")

    def test_cancel_while_selecting_source(self):
        game = self.make_game(general="guanyu")
        peach = tao()
        game.player.hand = [peach]
        game.player.hp = game.player.max_hp

        self.activate_skill(game)
        self.assertIsNotNone(game.pending_view_as)
        self.click(self.renderer.secondary_button.rect.center, game)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)
        self.assertIn(peach, game.player.hand)
        self.assertFalse(any("武圣" in line for line in game.game_log))

    def test_can_reenter_after_cancel(self):
        game = self.make_game(general="zhaoyun")
        flash = shan()
        game.player.hand = [flash]
        self.activate_skill(game)
        self.click(self.renderer.secondary_button.rect.center, game)
        self.activate_skill(game)
        self.assertIsNotNone(game.pending_view_as, "取消之后可以重新进入")


# ==================================================
# 5. 多 source：先点技能，再选两张
# ==================================================


class MultiSourceViewAsTests(ViewAsTestCase):

    def test_pair_probe_requires_skill_then_two_cards(self):
        game = self.make_game()
        first, second = shan(), normal_sha()
        game.player.hand = [first, second, tao()]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)

        # 未点技能：点击不会组成转化
        self.click(self.hand_center(game, 0), game)
        self.assertIsNone(game.pending_view_as)
        self.assertIsNone(game.pending_target_selection)

        self.activate_skill(game, PAIR_PROBE_ID)
        session = game.pending_view_as
        self.assertIsNotNone(session)
        self.assertEqual(session.required_source_count, 2)

        self.click(self.hand_center(game, 0), game)
        self.assertEqual(len(game.pending_view_as.selected_source_cards), 1)
        self.assertIsNone(game.pending_target_selection, "还差一张")

        self.click(self.hand_center(game, 1), game)
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        self.assertEqual(len(selection["card"].source_cards), 2)
        self.assertEqual(selection["card"].name, "SHA")


# ==================================================
# 6. ACTIVE 技能不回归
# ==================================================


class ActiveSkillRegressionTests(ViewAsTestCase):

    def test_fanjian_still_uses_active_pipeline(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.activate_skill(game)
        self.assertIsNotNone(game.pending_skill_input, "反间仍走主动技能输入")
        self.assertEqual(game.pending_skill_input["skill_id"], "fanjian")
        self.assertIsNone(game.pending_view_as)

    def test_jieyin_still_uses_active_pipeline(self):
        game = self.make_game(general="sunshangxiang")
        game.player.hp = 1
        game.player.hand = [tao(), tao()]
        partner = game.players[1]
        partner.gender = "male"
        partner.hp = 2

        # 孙尚香只有一个主动技（枭姬是触发技）：入口直达技能输入流程
        self.activate_skill(game)
        self.assertIsNotNone(game.pending_skill_input)
        self.assertEqual(game.pending_skill_input["skill_id"], "jieyin")
        self.assertEqual(game.pending_skill_input["cost_cards"], 2)
        self.assertIsNone(game.pending_view_as)

    def test_active_and_view_as_share_one_entry(self):
        """同一个「发动技能」入口同时列出两种技能，语义由引擎分流。"""

        game = self.make_game(general="zhouyu")
        game.player.hand = [tao(), shan()]
        bind_conversion_probes(game, game.player, PAIR_PROBE_ID)
        self.frame(game)
        rows = dict((item[0], item) for item in game.skill_picker_rows())
        self.assertIn("fanjian", rows)
        self.assertIn(PAIR_PROBE_ID, rows)

        self.activate_skill(game, PAIR_PROBE_ID)
        self.assertIsNotNone(game.pending_view_as,
                             "视为技进入选牌模式")
        self.assertIsNone(game.pending_skill_input)

        game.cancel_view_as()
        self.activate_skill(game, "fanjian")
        self.assertIsNotNone(game.pending_skill_input,
                             "主动技进入技能输入流程")
        self.assertIsNone(game.pending_view_as)


# ==================================================
# 7. 只有真正提交才算发动
# ==================================================


class CommitTimingTests(ViewAsTestCase):

    def test_no_log_or_state_before_commit(self):
        from src.game.engine import EventType

        game = self.make_game(general="zhaoyun")
        flash = shan()
        game.player.hand = [flash]
        fired = []
        game.context.events.subscribe(
            EventType.SKILL_TRIGGERED,
            lambda context, event: fired.append(event.payload.get("skill_id")))

        self.activate_skill(game)
        self.assertEqual(fired, [])
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(fired, [], "还没确认目标")
        self.assertFalse(any("龙胆" in line for line in game.game_log))

        self.click(self.seat_center(game, game.players[1]), game)
        self.assertIn("longdan", fired)
        self.assertTrue(any("龙胆" in line for line in game.game_log))

    def test_activate_action_not_used_for_view_as(self):
        game = self.make_game(general="zhaoyun")
        game.player.hand = [shan()]
        submitted = []
        original = game.submit_action

        def spy(action):
            submitted.append(action)
            return original(action)

        game.submit_action = spy
        self.activate_skill(game)
        self.click(self.hand_center(game, 0), game)
        self.click(self.seat_center(game, game.players[1]), game)
        self.assertFalse(
            any(isinstance(action, ActivateSkillAction) for action in submitted),
            "视为技不通过 ActivateSkillAction")


if __name__ == "__main__":
    unittest.main()
