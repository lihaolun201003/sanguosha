"""Phase 8 hotfix tests: 主动技能的真人链路（ActionBar → Picker → 输入 → 引擎）。

覆盖用户报告的问题：主动技能无法由真人正常发动。

关键点：这里的每一步都用 ``pygame.event.post`` 投递真实的 MOUSEBUTTONDOWN
事件，再交给 ``src.ui.interaction.handle_game_click``（与 main.py 同一份路由），
所以"测试里点得到"等价于"真人点得到"。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card_catalog import create_development_deck
from src.game import Game
from src.game.engine import (
    ConfirmPendingAction,
    PassPendingAction,
    SelectCardsAction,
)
from src.game.engine.pending import PendingRequestType
from src.game.generals import GeneralDef
from src.renderer import Renderer
from src.ui import layout
from src.ui.interaction import handle_game_click
from src.ui.skill_bar import SkillPicker
from tests.legacy_helpers import canonical_card, normal_sha, tao

TWIN_ID = "hotfix_twin"


def shan_card():
    return canonical_card("SHAN")


def spade(rank="7"):
    return next(
        card for card in create_development_deck()
        if card.suit == "spade" and card.rank == rank and card.name != "SHANDIAN"
    )


def heart(rank=None):
    for card in create_development_deck():
        if card.suit != "heart":
            continue
        if rank is not None and card.rank != rank:
            continue
        return card
    raise AssertionError("no heart card")


class HotfixUiTestCase(unittest.TestCase):
    """真实鼠标事件驱动的一局游戏。"""

    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    # ---------- 建局辅助 ----------

    def make_game(self, ai_count=3, general="zhouyu"):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        # 真实入口开启节奏模式：AI 的响应排队出现。
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

    def twin_general(self, game, *, hp=2):
        """注册一个同时拥有反间 + 结姻的测试武将（两个主动技 → 触发选择面板）。"""

        if game.generals.get(TWIN_ID) is None:
            game.generals.register(GeneralDef(
                id=TWIN_ID,
                name="双子",
                kingdom="wu",
                gender="female",
                max_hp=4,
                skill_ids=("fanjian", "jieyin"),
                title="测试用武将",
                description="同时拥有【反间】与【结姻】。",
            ))
        game.set_general(game.player, TWIN_ID)
        game.player.max_hp = 4
        game.player.hp = hp
        partner = game.players[1]
        partner.gender = "male"
        partner.hp = max(1, partner.max_hp - 1)
        return partner

    def frame(self, game, mouse=None, count=1):
        for _ in range(count):
            game.update(1 / 60)
            self.renderer.update(1 / 60)
            self.renderer.draw(game, mouse)

    def click(self, position, game, mouse=None):
        """投递真实鼠标左键事件，并用正式路由处理它。"""

        self.frame(game, mouse if mouse is not None else position)
        pygame.event.post(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": position, "button": 1}
        ))
        handled = 0
        for event in pygame.event.get():
            if event.type != pygame.MOUSEBUTTONDOWN or event.button != 1:
                continue
            handled += 1
            handle_game_click(event.pos, game, self.renderer)
        self.frame(game, position)
        return handled

    def drain(self, game, human_select=None, max_steps=400):
        """把引擎推到没有 Pending（真人请求按给定策略回答）。"""

        for _ in range(max_steps):
            if game.busy:
                game.update(1 / 60)
                continue
            request = game.pending_request
            if request is None:
                return True
            if getattr(request.target, "is_human", False):
                if request.request_type is PendingRequestType.SELECT_CARDS:
                    if human_select is not None:
                        chosen = human_select(request)
                        if chosen:
                            game.submit_action(SelectCardsAction(
                                request.target, request.request_id, chosen))
                            continue
                    if request.min_cards == 0:
                        game.submit_action(PassPendingAction(
                            request.target, request.request_id))
                        continue
                    game.submit_action(SelectCardsAction(
                        request.target, request.request_id,
                        list(request.context.get("candidates", ()))[:request.min_cards],
                    ))
                    continue
                if request.request_type is PendingRequestType.RESPOND_CARD:
                    game.submit_action(PassPendingAction(request.target, request.request_id))
                    continue
                game.submit_action(ConfirmPendingAction(
                    request.target, request.request_id, False))
                continue
            game.engine.present_or_auto_resolve(request)
        return False

    # ---------- 位置辅助 ----------

    def skill_button_center(self, game):
        self.frame(game)
        return self.renderer.skill_bar.button.rect.center

    def seat_center(self, game, player):
        self.frame(game)
        return self.renderer.table_layout.seat_rects[player].center

    def hand_center(self, game, index):
        self.frame(game)
        return self.renderer.get_card_rects(game.player.hand)[index].center

    def primary_center(self, game):
        self.frame(game)
        return self.renderer.primary_button.rect.center

    def secondary_center(self, game):
        self.frame(game)
        return self.renderer.secondary_button.rect.center

    def picker_row_center(self, game, skill_id):
        """技能按钮中心。

        Phase 10.3 起技能区直接显示真实技能名，点技能名即可发动，
        不再经过"发动技能 → 选择技能"的中转面板，所以这里返回技能按钮。
        """

        self.frame(game)
        rect = self.renderer.skill_bar.rect_for(skill_id)
        if rect is not None:
            return rect.center
        picker = game.pending_skill_picker
        ids = [item[0] for item in picker]
        return self.renderer.skill_picker.rects[ids.index(skill_id)].center


# ==================================================
# 1. ActionBar：按钮可见性与 Disabled 区分
# ==================================================


class ActionBarAvailabilityTests(HotfixUiTestCase):

    def test_no_active_skill_leaves_button_disabled(self):
        # 郭嘉只有触发 / 锁定技：既没有主动技也没有视为技。
        game = self.make_game(general="guojia")
        game.player.hand = [normal_sha(), shan_card()]
        self.frame(game)
        self.assertFalse(self.renderer.skill_bar.button.enabled,
                         "没有可发动的主动技能时按钮必须不可点")
        self.assertEqual(self.click(self.skill_button_center(game), game), 1)
        self.assertIsNone(game.pending_skill_picker)
        self.assertIsNone(game.pending_skill_input)

    def test_active_skill_enables_button(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.frame(game)
        self.assertTrue(self.renderer.skill_bar.button.enabled)
        # Phase 10.3：技能区直接显示技能名，点击即发动该技能（不再有中转面板）。
        self.assertEqual(
            self.renderer.hit_action(self.skill_button_center(game), game),
            ("skill", "fanjian"),
        )

    def test_button_rect_equals_hit_rect(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.frame(game)
        rect = self.renderer.skill_bar.button.rect
        self.assertTrue(rect.collidepoint(rect.center))
        self.assertEqual(
            self.renderer.hit_action(rect.center, game), ("skill", "fanjian"))
        self.assertNotEqual(
            self.renderer.hit_action((rect.right + 2, rect.y), game),
            ("skill", "fanjian"))

    def test_button_stays_clickable_after_resolution_change(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        for size in ((2560, 1440), (1280, 720), (1920, 1080), (1366, 768)):
            self.screen = pygame.display.set_mode(size)
            self.renderer.set_screen(self.screen)
            self.frame(game)
            center = self.renderer.skill_bar.button.rect.center
            self.assertEqual(
                self.renderer.hit_action(center, game), ("skill", "fanjian"),
                "F11/Resize 后技能按钮必须仍然可点 @%s" % (size,))

    def test_skill_button_disabled_on_other_players_turn(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        game.current_turn_player = game.players[1]
        self.frame(game)
        self.assertFalse(self.renderer.skill_bar.button.enabled)


# ==================================================
# 2. Skill Picker：数据驱动、单选直达、取消
# ==================================================


class SkillPickerTests(HotfixUiTestCase):

    def test_single_skill_enters_input_directly(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.click(self.skill_button_center(game), game)
        self.assertIsNone(game.pending_skill_picker, "只有一个主动技时不应弹出面板")
        self.assertIsNotNone(game.pending_skill_input)
        self.assertEqual(game.pending_skill_input["skill_id"], "fanjian")

    def test_two_skills_are_listed_and_clickable(self):
        """两个技能都要在技能区显示，且都能直接点开。

        Phase 10.3 起不再有"发动技能 → 选择技能"的中转面板：技能区按
        SkillDef 逐个列出真实技能名，点哪个就发动哪个。
        """

        game = self.make_game()
        self.twin_general(game)
        game.player.hand = [tao(), tao()]
        self.frame(game)

        bar = self.renderer.skill_bar
        ids = sorted(item.id for item in bar.skills)
        self.assertEqual(ids, ["fanjian", "jieyin"])
        self.assertEqual(sorted(bar.enabled_ids), ["fanjian", "jieyin"])
        for skill_id in ("fanjian", "jieyin"):
            definition = game.skill_registry.require(skill_id)
            self.assertTrue(definition.name, "按钮文案必须来自 SkillDef.name")
            self.assertTrue(definition.description,
                            "说明必须来自 SkillDef.description")
            rect = bar.rect_for(skill_id)
            self.assertIsNotNone(rect)
            self.assertEqual(self.renderer.hit_action(rect.center, game),
                             ("skill", skill_id))

        self.click(self.picker_row_center(game, "jieyin"), game)
        self.assertIsNone(game.pending_skill_picker)
        self.assertEqual(game.pending_skill_input["skill_id"], "jieyin")

    def test_skill_input_swallows_other_clicks(self):
        """技能输入流程中，点手牌不能出牌、也不能意外取消技能。"""

        game = self.make_game()
        self.twin_general(game)
        game.player.hand = [tao(), tao()]
        self.click(self.picker_row_center(game, "jieyin"), game)
        self.assertIsNotNone(game.pending_skill_input)

        # 点手牌既不能出牌，也不能关掉技能输入
        self.click(self.hand_center(game, 0), game)
        self.assertIsNotNone(game.pending_skill_input)
        self.assertIsNone(game.pending_target_selection)

    def test_skill_cancel_writes_nothing(self):
        game = self.make_game()
        partner = self.twin_general(game)
        game.player.hand = [tao(), tao()]
        hp_before = game.player.hp

        self.click(self.picker_row_center(game, "jieyin"), game)
        self.assertIsNotNone(game.pending_skill_input)
        self.frame(game)
        self.click(self.renderer.secondary_button.rect.center, game)

        self.assertIsNone(game.pending_skill_input)
        self.assertEqual(game.player.skill_state.get("jieyin", "used", 0), 0,
                         "取消不得写入 used 标记")
        self.assertEqual(game.player.skill_state.get("fanjian", "used", 0), 0)
        self.assertEqual(len(game.player.hand), 2, "取消不得弃牌")
        self.assertEqual(game.player.hp, hp_before)
        self.assertEqual(partner.hp, partner.max_hp - 1, "取消不得治疗")

    def test_disabled_skills_keep_button_off_and_explain(self):
        """技能存在但当回合不可发动：按钮不可点，并给出原因（Disabled ≠ 不存在）。"""

        game = self.make_game(general="zhouyu")
        game.player.hand = []
        self.frame(game)
        self.assertFalse(self.renderer.skill_bar.button.enabled)
        # 提示只在悬停时出现（常驻提示条会挡住手牌区）。
        self.assertIsNone(
            self.renderer.skill_bar.tooltip(game),
            "鼠标不在技能上时不该有常驻提示")
        fanjian_rect = self.renderer.skill_bar.rect_for("fanjian")
        self.assertIsNotNone(fanjian_rect)
        tooltip = self.renderer.skill_bar.tooltip(game, fanjian_rect.center)
        self.assertIsNotNone(tooltip, "悬停禁用技能时必须解释原因")
        self.assertIn("无法发动", tooltip)
        self.assertEqual(self.click(self.skill_button_center(game), game), 1)
        self.assertIsNone(game.pending_skill_input)
        self.assertIsNone(game.pending_skill_picker)

    def test_skill_buttons_disabled_while_input_open(self):
        """技能输入进行中：技能区不再显示为可发动，避免叠加第二次发动。"""

        game = self.make_game()
        self.twin_general(game)
        game.player.hand = [tao(), tao()]
        self.click(self.picker_row_center(game, "jieyin"), game)
        self.assertIsNotNone(game.pending_skill_input)
        self.frame(game)
        self.assertFalse(self.renderer.skill_bar.button.enabled,
                         "技能输入已打开时不应再次发动")

    def test_picker_layout_uses_metrics(self):
        """SkillPicker 组件本身仍然可用（面板几何按 LayoutMetrics 计算）。"""

        picker = SkillPicker()
        metrics = layout.LayoutMetrics(2560, 1440)
        picker.sync_layout(metrics, 3)
        self.assertEqual(len(picker.rects), 3)
        for rect in picker.rects:
            self.assertTrue(picker.panel_rect.contains(rect))
        self.assertTrue(picker.panel_rect.contains(picker.cancel_button.rect))


# ==================================================
# 3. 目标 / 费用输入：真人点击 → 引擎
# ==================================================


class SkillInputTests(HotfixUiTestCase):

    def test_fanjian_full_click_chain(self):
        """最低 smoke：真人周瑜发动反间的完整链路（点击 → 目标 → 确认）。"""

        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        target = game.players[1]
        target.hand = [spade("3")]
        hp_before = target.hp

        self.click(self.skill_button_center(game), game)
        self.assertIsNotNone(game.pending_skill_input)

        self.click(self.seat_center(game, target), game)
        self.assertIs(game.pending_skill_input["target"], target)
        self.assertTrue(game.skill_input_ready())

        self.frame(game)
        self.assertEqual(self.renderer.primary_button.label, "确认发动")
        self.assertEqual(
            self.renderer.hit_action(self.primary_center(game), game), "confirm_skill")

        self.click(self.primary_center(game), game)
        self.assertIsNone(game.pending_skill_input)
        self.assertEqual(game.player.skill_state.get("fanjian", "used", 0), 1,
                         "确认后必须写入 used 标记")

        # 反间流程要求周瑜先交出一张手牌
        self.drain(game)
        self.assertEqual(game.player.skill_state.get("fanjian", "used", 0), 1)
        self.assertLessEqual(target.hp, hp_before)

        # 再次点击不可发动
        self.frame(game)
        self.assertFalse(self.renderer.skill_bar.button.enabled,
                         "本阶段发动过后不能再发动")

    def test_jieyin_cost_cards_clicked_from_hand(self):
        game = self.make_game()
        partner = self.twin_general(game, hp=1)
        game.player.hand = [tao(), tao(), heart("2")]

        # 技能区直接显示真实技能名：点【结姻】即进入输入流程。
        self.click(self.picker_row_center(game, "jieyin"), game)

        state = game.pending_skill_input
        self.assertIsNotNone(state)
        self.assertEqual(state["cost_cards"], 2)
        self.assertFalse(game.skill_input_ready(), "还没选目标与费用牌")

        self.click(self.seat_center(game, partner), game)
        self.assertFalse(game.skill_input_ready(), "还差两张费用牌")

        self.click(self.hand_center(game, 0), game)
        self.assertEqual(len(game.pending_skill_input["cards"]), 1)
        self.assertFalse(game.skill_input_ready())

        self.click(self.hand_center(game, 1), game)
        self.assertEqual(len(game.pending_skill_input["cards"]), 2)
        self.assertTrue(game.skill_input_ready())

        hand_before = len(game.player.hand)
        self.click(self.primary_center(game), game)
        self.drain(game)
        self.assertEqual(len(game.player.hand), hand_before - 2, "费用牌已弃置")
        self.assertEqual(game.player.hp, 2)
        self.assertEqual(partner.hp, partner.max_hp)
        self.assertEqual(game.player.skill_state.get("jieyin", "used", 0), 1)

    def test_clicking_same_card_twice_deselects_cost(self):
        game = self.make_game()
        self.twin_general(game, hp=1)
        game.player.hand = [tao(), tao()]
        self.click(self.picker_row_center(game, "jieyin"), game)

        self.click(self.hand_center(game, 0), game)
        self.assertEqual(len(game.pending_skill_input["cards"]), 1)
        self.click(self.hand_center(game, 0), game)
        self.assertEqual(len(game.pending_skill_input["cards"]), 0, "再点一次取消选择")

    def test_self_is_not_a_valid_target(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.click(self.skill_button_center(game), game)
        state = game.pending_skill_input
        self.assertFalse(game.toggle_skill_target(game.player),
                         "自己不在反间的候选里")
        self.assertIsNone(state["target"])
        self.assertNotIn(game.player, self.renderer.table_layout.seat_rects,
                         "真人自己的面板不是座位，不会被误当成技能目标")

    def test_cancel_input_is_clean(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        target = game.players[1]
        self.click(self.skill_button_center(game), game)
        self.click(self.seat_center(game, target), game)
        self.assertIsNotNone(game.pending_skill_input["target"])

        self.frame(game)
        self.assertEqual(
            self.renderer.hit_action(self.secondary_center(game), game), "cancel_skill")
        self.click(self.secondary_center(game), game)

        self.assertIsNone(game.pending_skill_input)
        self.assertEqual(game.player.skill_state.get("fanjian", "used", 0), 0)
        self.assertEqual(len(game.player.hand), 1, "取消后手牌不变")
        self.assertEqual(game.phase, "play", "取消后仍是普通出牌状态")
        self.assertTrue(self.renderer.skill_bar.button.enabled, "取消后可以重新发动")

    def test_prompt_reflects_skill_input(self):
        from src.ui import prompt as prompt_module

        game = self.make_game()
        partner = self.twin_general(game, hp=1)
        game.player.hand = [tao(), tao()]
        # 直接点【结姻】技能名：Phase 10.3 起技能区不再有中转面板。
        self.click(self.picker_row_center(game, "jieyin"), game)

        info = prompt_module.describe(game)
        self.assertIn("结姻", info.title)
        self.assertIn("0/2", info.progress, "Prompt 必须给出选牌进度")

        self.click(self.seat_center(game, partner), game)
        info = prompt_module.describe(game)
        self.assertIn(partner.name, info.progress, "Prompt 必须显示已选目标")

        self.click(self.hand_center(game, 0), game)
        self.click(self.hand_center(game, 1), game)
        info = prompt_module.describe(game)
        self.assertIn("确认发动", info.body)

    def test_confirm_without_selection_is_rejected(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.click(self.skill_button_center(game), game)
        self.frame(game)
        self.assertFalse(self.renderer.primary_button.enabled, "未选目标前不能确认")
        self.assertFalse(game.confirm_skill_input())
        self.assertIsNotNone(game.pending_skill_input, "缺目标时确认必须被拒")
        self.assertEqual(game.player.skill_state.get("fanjian", "used", 0), 0)

    def test_skill_input_blocks_playing_cards(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [normal_sha(), tao()]
        self.click(self.skill_button_center(game), game)
        self.click(self.hand_center(game, 0), game)
        self.assertIsNone(game.pending_target_selection,
                          "技能输入期间点手牌不能当作出牌")

    def test_second_activation_same_phase_is_refused(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao(), tao()]
        target = game.players[1]
        target.hand = [spade("3")]

        self.click(self.skill_button_center(game), game)
        self.click(self.seat_center(game, target), game)
        self.click(self.primary_center(game), game)
        self.drain(game)

        allowed, reason = game.skills.can_activate(game.player, "fanjian")
        self.assertFalse(allowed, "出牌阶段限一次")
        self.assertTrue(reason)
        self.assertFalse(game.begin_skill_activation())

    def test_can_activate_rechecked_on_confirm(self):
        """收集参数期间状态变化 → 提交时必须被二次校验拦下。"""

        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        target = game.players[1]
        self.click(self.skill_button_center(game), game)
        self.click(self.seat_center(game, target), game)

        # 收集参数期间状态变化：手牌被其它效果拿走 → 反间不再可发动
        game.player.hand = []
        self.assertFalse(game.confirm_skill_input(), "状态已变化，确认必须失败")
        self.assertIsNone(game.pending_skill_input, "失败后清空输入状态")


# ==================================================
# 4. Pending 接回技能流程
# ==================================================


class SkillPendingTests(HotfixUiTestCase):

    def test_fanjian_pending_resumes_skill_flow(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        target = game.players[1]
        target.hand = []

        self.click(self.skill_button_center(game), game)
        self.click(self.seat_center(game, target), game)
        self.click(self.primary_center(game), game)

        game.update(1 / 60)
        request = game.pending_request
        self.assertIsNotNone(request, "技能必须建立 Pending 等待真人选牌")
        self.assertIs(request.request_type, PendingRequestType.SELECT_CARDS)
        card = list(request.context["candidates"])[0]
        game.submit_action(SelectCardsAction(
            request.target, request.request_id, [card]))
        self.drain(game)

        self.assertEqual(len(target.hand), 1, "手牌已交给目标")
        self.assertLess(target.hp, target.max_hp, "目标没有同花色手牌，受到伤害")
        self.assertIsNone(game.pending_request)


# ==================================================
# 5. 重置 / 返回菜单不留下残留状态
# ==================================================


class SkillResetTests(HotfixUiTestCase):

    def test_restart_clears_skill_input(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.click(self.skill_button_center(game), game)
        self.assertIsNotNone(game.pending_skill_input)
        game.reset()
        self.assertIsNone(game.pending_skill_input)
        self.assertIsNone(game.pending_skill_picker)

    def test_return_to_menu_clears_skill_state(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.click(self.skill_button_center(game), game)
        game.return_to_menu()
        self.assertIsNone(game.pending_skill_input)
        self.assertIsNone(game.pending_skill_picker)

    def test_restart_clears_pending_choice_state(self):
        """重开后不得残留技能输入 / 选择面板状态。"""

        game = self.make_game()
        self.twin_general(game)
        game.player.hand = [tao(), tao()]
        self.click(self.picker_row_center(game, "jieyin"), game)
        self.assertIsNotNone(game.pending_skill_input)
        game.reset()
        self.assertIsNone(game.pending_skill_input)
        self.assertIsNone(game.pending_skill_picker)

    def test_no_stale_rects_after_restart(self):
        game = self.make_game(general="zhouyu")
        game.player.hand = [tao()]
        self.click(self.skill_button_center(game), game)
        game.reset()
        game.start_single_player()
        game.actions.clear()
        self.frame(game)
        self.assertFalse(self.renderer.skill_bar.button.enabled,
                         "重开后真人没有可发动的技能，按钮必须回到不可点")


# ==================================================
# 6. AI 走同一条 Action 路径
# ==================================================


class SkillAiParityTests(HotfixUiTestCase):

    def drive_ai_until(self, game, predicate, max_steps=4000):
        """驱动 AI 回合直到条件成立（真人 Pending 自动 Pass）。"""

        for _ in range(max_steps):
            if predicate():
                return True
            game.update(1 / 60)
            request = game.pending_request
            if request is not None:
                if getattr(request.target, "is_human", False):
                    if request.request_type is PendingRequestType.SELECT_CARDS:
                        game.submit_action(SelectCardsAction(
                            request.target, request.request_id,
                            list(request.context.get("candidates", ()))[:max(1, request.min_cards)]
                            if request.min_cards else [],
                        ))
                    else:
                        game.submit_action(PassPendingAction(
                            request.target, request.request_id))
                else:
                    game.engine.present_or_auto_resolve(request)
            if game.game_over:
                return False
        return False

    def test_ai_activates_skill_through_action(self):
        """AI 与真人共用 ActivateSkillAction，不能因为 UI 改动而回退。"""

        from src.game.engine import ActivateSkillAction

        game = self.make_game(general="zhaoyun")
        ai = game.players[1]
        game.set_general(ai, "zhouyu")
        ai.hp = ai.max_hp = 3
        ai.hand = [tao()]
        game.player.hand = []
        game.current_turn_player = ai
        game.phase = "play"
        for player in game.players[2:]:
            player.hand = []
            player.hp = player.max_hp

        submitted = []
        original = game.submit_action

        def spy(action):
            submitted.append(action)
            return original(action)

        game.submit_action = spy
        finished = []
        game.get_controller(ai).take_turn(lambda: finished.append(True))

        for _ in range(2000):
            game.update(1 / 60)
            request = game.pending_request
            if request is not None:
                if getattr(request.target, "is_human", False):
                    game.submit_action(PassPendingAction(
                        request.target, request.request_id))
                else:
                    game.engine.present_or_auto_resolve(request)
            if ai.skill_state.get("fanjian", "used", 0) or finished:
                break

        self.assertTrue(
            any(isinstance(action, ActivateSkillAction) for action in submitted),
            "AI 必须提交 ActivateSkillAction")
        self.assertEqual(ai.skill_state.get("fanjian", "used", 0), 1)

    def test_ai_never_touches_skill_ui_state(self):
        game = self.make_game(general="zhaoyun")
        ai = game.players[1]
        game.set_general(ai, "zhouyu")
        ai.hp = ai.max_hp = 3
        ai.hand = [tao(), tao()]
        game.current_turn_player = ai
        game.phase = "play"

        for _ in range(600):
            game.update(1 / 60)
            request = game.pending_request
            if request is not None and not getattr(request.target, "is_human", False):
                game.engine.present_or_auto_resolve(request)
            if game.current_turn_player is not ai or game.game_over:
                break
            self.assertIsNone(game.pending_skill_picker)
            self.assertIsNone(game.pending_skill_input)


if __name__ == "__main__":
    unittest.main()
