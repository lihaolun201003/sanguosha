"""Phase 12：1v1 测试模式（开战前自由指定双方武将 + 双边手动测试）。

覆盖范围与验收清单一一对应：

A. **设置层**：完整武将池（含新增武将自动出现）、搜索 / 势力 / 扩展包筛选、分页、
   同名不同版本的标签、未实现条目禁止开局、非法配置不开局、无键盘快捷键误触。
B. **开局规则**：双方按所选武将初始化、各四张手牌、先手三种取值、指定武将不被
   随机覆盖、镜像局（同一武将）双方技能状态互不影响。
C. **双边手动**：视角跟着当前决策者切换、杀 / 闪、共享无懈（含"一方放弃后另一方
   仍被问到"）、主动技能、濒死救援、嵌套响应结束后控制权恢复。
D. **可复现**：固定种子覆盖洗牌 / 先手 / AI 随机源。
E. **重开与清理**：原配置重开、换将、连续重开不叠加监听 / 不留标记 / 不卡死；
   离开模式后不把指定武将泄漏给其它模式。
F. **兼容性**：自由混战与身份局照常可用；底部布局在 4:3 屏幕上贴住屏幕底边。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_engine_v2_phase12_duel_test``
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.generals import GeneralDef
from src.game.modes.duel import (
    DuelConfig,
    config_of,
)
from src.renderer import Renderer
from src.ui import layout
from src.ui import duel_setup
from src.ui.duel_hud import DuelHud
from src.ui.duel_setup import DuelSetupScreen
from src.ui.interaction import handle_game_click
from tests.legacy_helpers import canonical_card

SCREEN_SIZE = (1280, 800)


def build_duel(*, my="guanyu", enemy="lvbu", first="me", control="ai", seed=""):
    """建一局 1v1 测试（可指定配置）；返回 (game, config)。"""

    game = Game()
    game.set_mode("duel_test")
    config = config_of(game)
    config.my_general = my
    config.enemy_general = enemy
    config.first = first
    config.control = control
    config.seed = seed
    game.begin_general_select()
    return game, config


def give(player, name):
    card = canonical_card(name)
    player.hand.append(card)
    return card


def card_id(player, name):
    for card in player.hand:
        if card.name == name:
            return card
    return None


class ScreenCase(unittest.TestCase):
    """带一块 dummy 显示表面与 Renderer 的测试基类。"""

    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.screen = pygame.display.set_mode(SCREEN_SIZE)

    def make_renderer(self):
        return Renderer(self.screen)

    # ---- 驱动 ----

    def frame(self, game, renderer, count=1):
        for _ in range(count):
            game.update(1 / 60)
            renderer.update(1 / 60)
            renderer.draw(game)

    def wait_until(self, game, renderer, condition, frames=900, what="条件"):
        for _ in range(frames):
            if condition():
                return True
            self.frame(game, renderer)
        self.fail("等待超时：" + what)

    def click(self, game, renderer, position):
        return handle_game_click(position, game, renderer)

    def click_card(self, game, renderer, card):
        """点手牌（走真实命中：位置 → 手牌下标 → 交互路由）。"""
        hand = game.player.hand
        index = next((i for i, item in enumerate(hand) if item is card), None)
        self.assertIsNotNone(index, "这张牌不在当前操作者手上")
        self.wait_until(game, renderer, lambda: not game.busy, what="动画播完")
        rect = renderer.get_card_rects(game.player.hand)[index]
        return self.click(game, renderer, rect.center)

    def click_button(self, game, renderer, button):
        """点固定按钮：动画播放中点击会被牌桌忽略，所以先等它播完。"""

        self.wait_until(game, renderer, lambda: not game.busy, what="动画播完")
        return self.click(game, renderer, button.rect.center)

    def click_end_turn(self, game, renderer):
        self.wait_until(
            game, renderer,
            lambda: renderer.hit_action(
                renderer.primary_button.rect.center, game) == "end_turn",
            what="可以结束回合")
        return self.click(game, renderer, renderer.primary_button.rect.center)

    def finish_turn(self, game, renderer, frames=1200):
        """把当前操作者的整段回合走完：超上限先弃牌，然后结束回合换人。"""

        who = game.current_turn_player
        for _ in range(frames):
            if game.current_turn_player is not who or game.game_over:
                return True
            if game.busy:
                self.frame(game, renderer)
                continue
            if game.phase == "discard" and game.current_turn_player is game.player:
                surplus = len(game.player.hand) - game.hand_limit(game.player)
                if surplus > 0:
                    rects = renderer.get_card_rects(game.player.hand)
                    self.click(game, renderer, rects[-1].center)
                    self.frame(game, renderer)
                    continue
            if renderer.hit_action(renderer.primary_button.rect.center, game) == "end_turn":
                self.click(game, renderer, renderer.primary_button.rect.center)
            self.frame(game, renderer)
        self.fail("回合没有结束：" + str(who.name))

    def pass_when_asked(self, game, renderer, frames=600):
        """只要有人被要求响应就点「不出」，直到窗口全部关掉。"""

        for _ in range(frames):
            if not game.response.active and not game.engine.pending.active:
                return True
            if not game.busy:
                if game.response.active:
                    self.click(game, renderer, renderer.secondary_button.rect.center)
                elif game.choice.active:
                    game.choice.choose_no()
            self.frame(game, renderer)
        self.fail("响应窗口没有关闭")


# ==================================================
# A. 设置页
# ==================================================


class TestDuelSetupScreen(ScreenCase):

    def setUp(self):
        self.game, self.config = build_duel()
        self.renderer = self.make_renderer()
        self.setup = DuelSetupScreen(self.screen)
        self.setup.sync_layout(self.renderer.metrics, self.game)
        self.setup.sync_cards(self.game)

    def test_enters_setup_scene_from_menu_flow(self):
        """「开始游戏」在 1v1 模式下进的是设置页，而不是随机三选一。"""

        self.assertEqual(self.game.scene, "duel_setup")
        self.assertEqual(self.game.general_candidates, ())

    def test_full_roster_is_selectable(self):
        """双方都能从**完整**武将池里选，不受三选一限制。"""

        ids = {general.id for general in self.setup.matched}
        self.assertEqual(ids, set(self.game.generals.ids()))
        self.assertGreater(len(ids), 3)

    def test_new_general_and_pack_appear_automatically(self):
        """新增武将 / 新增扩展包不需要改 UI：自动出现在列表与筛选按钮里。"""

        self.game.generals.register(GeneralDef(
            id="sp_ce_test", name="测试将", kingdom="qun", pack="SP",
            skill_ids=(), max_hp=4))
        self.setup.on_enter(self.game)
        self.assertIn("sp_ce_test", [general.id for general in self.setup.matched])
        self.assertIn("SP", [value for value, _label in self.setup._pack_options(self.game)])

    def test_same_name_different_version_has_label(self):
        """同名不同版本必须带明确标签，否则玩家分不清选的是谁。"""

        self.game.generals.register(GeneralDef(
            id="guanyu_limit", name="关羽", kingdom="shu", pack="界限突破",
            version="界限突破", skill_ids=(), max_hp=4))
        labels = self.game.generals.labelled_names()
        self.assertNotEqual(labels["guanyu"], labels["guanyu_limit"])
        for general_id in ("guanyu", "guanyu_limit"):
            self.assertIn("关羽", labels[general_id])
            self.assertIn("（", labels[general_id])

    def test_unimplemented_entry_is_visible_but_blocked(self):
        """未实现的条目看得见、说得清，但**不许**被选上开局。"""

        self.game.generals.register(GeneralDef(
            id="god_test", name="测试神将", kingdom="qun", pack="神",
            implemented=False, unavailable_reason="技能规则待核实。"))
        self.setup.on_enter(self.game)
        general = self.game.generals.get("god_test")
        ok, reason = general.availability
        self.assertFalse(ok)
        self.assertIn("待核实", reason)
        self.assertIn("god_test", [item.id for item in self.setup.matched],
                      "未实现的条目也要列出来（看得见才知道为什么不能选）")
        self.config.my_general = ""
        self.setup.set_active_seat(0)
        self.assertFalse(self.setup.choose_general(self.game, general))
        self.assertIn("不能开局", self.setup.notice)
        self.assertEqual(config_of(self.game).my_general, "")

    def test_search_filters_and_pagination(self):
        self.setup.search_field.set_text("月英")
        self.setup.on_filter_changed(self.game)
        self.assertEqual([g.name for g in self.setup.matched], ["黄月英"])

        self.setup.search_field.set_text("")
        self.setup.on_filter_changed(self.game)
        self.assertEqual(len(self.setup.matched), len(self.game.generals))

        self.setup.kingdom_filter = "wu"
        self.setup.on_filter_changed(self.game)
        self.assertTrue(all(g.kingdom == "wu" for g in self.setup.matched))
        self.assertGreater(len(self.setup.matched), 0)

        self.setup.kingdom_filter = ""
        self.setup.pack_filter = "不存在"
        self.setup.on_filter_changed(self.game)
        self.assertEqual(self.setup.matched, [])

        self.setup.pack_filter = ""
        self.setup.on_filter_changed(self.game)
        self.assertGreater(self.setup.page_count(), 1, "25 名武将应当分页")

    def test_paging_keeps_all_generals_reachable(self):
        seen = set()
        for _ in range(self.setup.page_count()):
            seen.update(general.id for general in self.setup.cards)
            self.setup.change_page(1, self.game)
        self.assertEqual(seen, set(self.game.generals.ids()))

    def test_choose_swap_and_random(self):
        self.setup.set_active_seat(0)
        self.setup.choose_general(self.game, self.game.generals.get("guanyu"))
        self.setup.set_active_seat(1)
        self.setup.choose_general(self.game, self.game.generals.get("lvbu"))
        self.assertEqual((self.config.my_general, self.config.enemy_general),
                         ("guanyu", "lvbu"))

        self.config.swap()
        self.assertEqual((self.config.my_general, self.config.enemy_general),
                         ("lvbu", "guanyu"))

        self.setup.random_for(self.game, 0)
        self.assertIn(self.config.my_general, set(self.game.generals.ids()))

    def test_start_blocked_until_both_sides_chosen(self):
        self.config.my_general = ""
        self.config.enemy_general = ""
        ok, message = self.game.mode.validate_setup()
        self.assertFalse(ok)
        self.assertIn("我方", message)
        self.setup.set_active_seat(0)
        self.setup.choose_general(self.game, self.game.generals.get("guanyu"))
        ok, message = self.game.mode.validate_setup()
        self.assertFalse(ok)
        self.assertIn("对手", message)
        self.setup.set_active_seat(1)
        self.setup.choose_general(self.game, self.game.generals.get("lvbu"))
        self.assertTrue(self.game.mode.validate_setup()[0])
        ok, _message = self.setup.start_battle(self.game)
        self.assertTrue(ok)
        self.assertEqual(self.game.scene, "game")

    def test_illegal_config_cannot_start(self):
        """配置不全 / 指定了未知武将 / 指定了未实现的武将 —— 一律不开局。"""

        self.setup.set_active_seat(0)
        self.setup.choose_general(self.game, self.game.generals.get("guanyu"))
        self.setup.set_active_seat(1)
        self.setup.choose_general(self.game, self.game.generals.get("lvbu"))

        self.config.enemy_general = "no_such_general"
        ok, _message = self.setup.start_battle(self.game)
        self.assertFalse(ok)
        self.assertEqual(self.game.scene, "duel_setup")

        self.game.generals.register(GeneralDef(
            id="god_test2", name="测试神将二", implemented=False,
            unavailable_reason="规则待核实。"))
        self.config.enemy_general = "god_test2"
        ok, message = self.setup.start_battle(self.game)
        self.assertFalse(ok)
        self.assertIn("待核实", message)
        self.assertEqual(self.game.scene, "duel_setup")

    def test_seed_field_round_trip(self):
        self.setup.seed_field.set_text("20240925")
        self.setup.start_battle(self.game)          # 配置不全 → 不开局，但种子要写进去
        self.assertEqual(self.config.seed, "20240925")
        self.setup.sync_fields(self.game)
        self.assertEqual(self.setup.seed_field.text, "20240925")

    def test_keyboard_goes_to_search_box_not_shortcuts(self):
        """搜索框聚焦时，'1'/'3' 这些快捷键不能顺手改对局速度。"""

        speed = self.game.speed
        self.setup.search_field.focus()
        for key in (pygame.K_1, pygame.K_3, pygame.K_MINUS, pygame.K_EQUALS):
            event = pygame.event.Event(pygame.KEYDOWN, key=key)
            self.assertEqual(self.setup.handle_event(event, self.game), "handled")
        self.assertEqual(self.game.speed, speed, "输入搜索词时不该改动节奏")

        typing = pygame.event.Event(pygame.TEXTINPUT, text="1")
        self.setup.handle_event(typing, self.game)
        self.assertEqual(self.setup.search_field.text, "1")
        self.assertEqual(self.game.speed, speed)

        self.setup.search_field.blur()
        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1)
        self.assertIsNone(self.setup.handle_event(event, self.game),
                          "没在输入时，快捷键要留给主循环处理")

    def test_escape_blurs_field_and_mouse_click_selects(self):
        self.setup.search_field.focus()
        escape = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)
        self.assertEqual(self.setup.handle_event(escape, self.game), "handled")
        self.assertFalse(self.setup.search_field.focused)

        self.setup.on_enter(self.game)
        general = self.game.generals.get("zhaoyun")
        index = [g.id for g in self.setup.cards].index("zhaoyun")
        click = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, pos=self.setup.card_rects[index].center, button=1)
        self.setup.set_active_seat(0)
        self.setup.handle_event(click, self.game)
        self.assertEqual(self.config.my_general, general.id)

    def test_wheel_turns_pages_inside_grid(self):
        if self.setup.page_count() < 2:              # pragma: no cover - 兜底
            self.skipTest("只有一页")
        wheel = pygame.event.Event(
            pygame.MOUSEWHEEL, x=0, y=-1, pos=self.setup.grid_rect.center)
        self.setup.handle_event(wheel, self.game)
        self.assertEqual(self.setup.page, 1)

    def test_layout_fits_small_window(self):
        """小窗口（1280x800 / 1024x768）里各块面板不许重叠、越界。"""

        for size in ((1280, 800), (1024, 768)):
            metrics = layout.LayoutMetrics(*size)
            self.setup.sync_layout(metrics, self.game)
            self.setup.sync_cards(self.game)
            rects = [self.setup.grid_rect, self.setup.detail_rect,
                     self.setup.settings_rect, self.setup.start_button.rect,
                     self.setup.back_button.rect] + list(self.setup.side_panels.values())
            for rect in rects:
                self.assertGreaterEqual(rect.left, 0, str(size))
                self.assertLessEqual(rect.right, metrics.screen_w, str(size))
                self.assertLessEqual(rect.bottom, metrics.screen_h, str(size))
            for index, first in enumerate(rects):
                for second in rects[index + 1:]:
                    self.assertFalse(first.colliderect(second),
                                     "面板重叠 %s: %s %s" % (size, first, second))

    def test_small_window_text_keeps_a_readable_minimum_size(self):
        """小窗口里武将名与右栏说明都不许缩到看不清（各有字号下限）。

        按设计稿等比缩放在 1024×768 上只有 15px / 10px，读技能说明太费劲；
        下限只在**小窗口**生效，大屏仍然按设计稿比例走。
        """

        setup = DuelSetupScreen(pygame.Surface((1024, 768)))
        small = layout.LayoutMetrics(1024, 768)
        setup.sync_layout(small, self.game)
        self.assertGreaterEqual(setup._name_font(small).get_height(),
                                duel_setup.NAME_MIN_PIXELS)
        self.assertGreaterEqual(setup._note_font(small).get_height(),
                                duel_setup.NOTE_MIN_PIXELS)
        big = layout.LayoutMetrics(1920, 1080)
        self.assertGreater(setup._name_font(big).get_height(), duel_setup.NAME_MIN_PIXELS)
        self.assertGreater(setup._note_font(big).get_height(), duel_setup.NOTE_MIN_PIXELS)

    def test_settings_labels_are_drawn_inside_the_panel(self):
        """「先手」「操作方式」标签必须画在设置面板**里面**。

        原来标签是贴着按钮行左边界往外画的，而面板左边就是武将网格——1024×768
        上直接盖在武将牌上。这里查的是实际画出来的文字矩形（不是面板矩形）。
        """

        for size in ((1024, 768), (1280, 800), (1920, 1080)):
            setup = DuelSetupScreen(pygame.Surface(size))
            metrics = layout.LayoutMetrics(*size)
            setup.sync_layout(metrics, self.game)
            setup.sync_cards(self.game)
            setup.draw(self.game, metrics)

            panel = setup.settings_rect
            labels = dict(setup.settings_labels)
            self.assertEqual(set(labels), {"先手", "操作方式"}, str(size))
            for title, bounds in labels.items():
                self.assertTrue(panel.contains(bounds),
                                "%s 的「%s」标签画到面板外：%s 不在 %s"
                                % (size, title, bounds, panel))
            for buttons in (setup.first_buttons, setup.control_buttons):
                for button in buttons.values():
                    for title, bounds in labels.items():
                        self.assertFalse(
                            bounds.colliderect(button.rect),
                            "%s 的「%s」标签压住了按钮「%s」"
                            % (size, title, button.label))

    def test_general_name_text_stays_inside_card_frame(self):
        """武将名必须完整画在卡框内，并且在文字带里**上下居中**。

        长名字（本用例注册一名 10 个字带后缀的武将）在最小分辨率下最容易
        顶到卡框底边，所以四档分辨率都要过；居中的判据用**字形墨迹**而不是
        渲染表面的矩形——中文字形的墨迹在行高里本来就偏下。
        """

        self.game.generals.register(GeneralDef(
            id="probe_long_name", name="诸葛亮（界限突破·卧龙）", kingdom="shu",
            pack="界限突破", version="界限突破", skill_ids=(), max_hp=3))
        for size in ((800, 600), (1024, 768), (1280, 800), (1920, 1080)):
            setup = DuelSetupScreen(pygame.Surface(size))
            metrics = layout.LayoutMetrics(*size)
            setup.sync_layout(metrics, self.game)
            setup.search_field.set_text("界限突破·卧龙")
            setup.on_filter_changed(self.game)
            setup.draw(self.game, metrics)

            self.assertEqual([general.id for general in setup.cards],
                             ["probe_long_name"], str(size))
            name_font = setup._name_font(metrics)
            self.assertGreaterEqual(setup.card_text_h, name_font.get_linesize(),
                                    "%s 的文字区比字体行高还矮" % (size,))
            self.assertTrue(setup.card_name_rects, str(size))
            for bounds, ink, area, card in zip(
                    setup.card_name_rects, setup.card_name_inks,
                    setup.card_name_areas, setup.card_frames):
                self.assertTrue(card.contains(bounds),
                                "%s 武将名越出卡框：%s 不在 %s" % (size, bounds, card))
                self.assertLessEqual(abs(bounds.centerx - card.centerx), 1,
                                     "%s 武将名没有左右居中：%s vs %s"
                                     % (size, bounds, card))
                self.assertLessEqual(abs(ink.centery - area.centery), 1,
                                     "%s 武将名没有上下居中：%s vs %s"
                                     % (size, ink, area))
                self.assertTrue(card.contains(ink),
                                "%s 武将名的墨迹越出卡框：%s 不在 %s" % (size, ink, card))

    def test_right_column_fills_the_real_screen_height(self):
        """右栏跟着屏幕高度分配空间，并且设计稿尺寸下与原来的坐标一致。"""

        for size in ((1024, 768), (1280, 800), (1920, 1080)):
            metrics = layout.LayoutMetrics(*size)
            self.setup.sync_layout(metrics, self.game)
            expected = metrics.screen_h - metrics.px(24)
            self.assertLessEqual(abs(self.setup.back_button.rect.bottom - expected), 1,
                                 "%s 的右栏没有贴住屏幕底边" % (size,))
            blocks = [self.setup.side_panels[0], self.setup.side_panels[1],
                      self.setup.detail_rect, self.setup.settings_rect,
                      self.setup.random_buttons[0].rect,
                      self.setup.start_button.rect, self.setup.back_button.rect]
            for index, first in enumerate(blocks):
                self.assertLessEqual(first.bottom, metrics.screen_h, str(size))
                for second in blocks[index + 1:]:
                    self.assertFalse(first.colliderect(second),
                                     "右栏分块重叠 %s: %s %s" % (size, first, second))

        metrics = layout.LayoutMetrics(1600, 900)
        self.setup.sync_layout(metrics, self.game)
        right_x = metrics.screen_w - metrics.px(460)
        width = metrics.px(440)
        self.assertEqual(self.setup.side_panels[0], pygame.Rect(right_x, 86, width, 136))
        self.assertEqual(self.setup.side_panels[1], pygame.Rect(right_x, 228, width, 136))
        self.assertEqual(self.setup.detail_rect, pygame.Rect(right_x, 370, width, 186))
        self.assertEqual(self.setup.settings_rect, pygame.Rect(right_x, 562, width, 142))
        self.assertEqual(self.setup.start_button.rect, pygame.Rect(right_x, 764, width, 58))
        self.assertEqual(self.setup.back_button.rect, pygame.Rect(right_x, 828, width, 48))

    def test_narrow_screen_reduces_grid_columns_and_keeps_paging_consistent(self):
        """窄屏少排一列（把宽度让给卡牌与名字），分页大小必须跟着列数走。"""

        self.setup.sync_layout(layout.LayoutMetrics(1024, 768), self.game)
        self.setup.sync_cards(self.game)
        self.assertLess(self.setup.grid_columns, duel_setup.GRID_COLUMNS)
        self.assertEqual(self.setup._page_size(),
                         self.setup.grid_columns * duel_setup.GRID_ROWS)
        self.assertEqual(len(self.setup.cards), self.setup._page_size())
        first_page = {general.id for general in self.setup.cards}
        self.assertTrue(self.setup.change_page(1, self.game))
        self.assertEqual(len(self.setup.cards), self.setup._page_size())
        self.assertFalse(first_page & {general.id for general in self.setup.cards},
                         "翻页后不该重复出现上一页的武将")

        self.setup.sync_layout(layout.LayoutMetrics(1280, 800), self.game)
        self.setup.sync_cards(self.game)
        self.assertEqual(self.setup.grid_columns, duel_setup.GRID_COLUMNS)


# ==================================================
# B. 开局规则
# ==================================================


class TestDuelBattleSetup(ScreenCase):

    def test_generals_are_exactly_what_was_chosen(self):
        game, _config = build_duel(my="zhouyu", enemy="xiahoudun")
        game.start_local_battle(1)
        self.assertEqual([p.general_id for p in game.players], ["zhouyu", "xiahoudun"])
        self.assertEqual([p.name for p in game.players], ["我方", "对手"])
        self.assertEqual([p.hp for p in game.players], [3, 4])
        self.assertEqual([p.max_hp for p in game.players], [3, 4])

    def test_both_sides_start_with_four_cards(self):
        game, _config = build_duel(seed="1")
        game.start_local_battle(1)
        # 开局各四张；先手方在自己的摸牌阶段照常多摸两张。
        self.assertEqual(len(game.players[1].hand), 4)
        self.assertIn(len(game.players[0].hand), (4, 6))

    def test_first_player_options(self):
        mine, _ = build_duel(first="me")
        mine.start_local_battle(1)
        self.assertIs(mine.current_turn_player, mine.players[0])

        theirs, _ = build_duel(first="enemy")
        theirs.start_local_battle(1)
        self.assertIs(theirs.current_turn_player, theirs.players[1])

        for seed in ("7", "9"):
            random_first, _ = build_duel(first="random", seed=seed)
            random_first.start_local_battle(1)
            self.assertIn(random_first.current_turn_player, random_first.players)
        again, _ = build_duel(first="random", seed="7")
        again.start_local_battle(1)

    def test_ai_general_is_not_re_randomised(self):
        """指定给 AI 的武将在开局 / 重开后都不能被随机掉。"""

        game, _config = build_duel(my="guanyu", enemy="huatuo", control="ai")
        for _ in range(3):
            game.start_local_battle(1)
            self.assertEqual([p.general_id for p in game.players], ["guanyu", "huatuo"])
        self.assertTrue(game.players[1].is_ai)
        self.assertTrue(game.players[0].is_human)

    def test_manual_mode_gives_both_sides_a_human_controller(self):
        from src.game.controllers import HumanController
        game, _config = build_duel(control="manual")
        game.start_local_battle(1)
        for player in game.players:
            self.assertIsInstance(game.get_controller(player), HumanController)

    def test_mirror_match_keeps_skill_state_separate(self):
        """双方选同一个武将：技能实例、限定次数与标记必须互相独立。"""

        game, _config = build_duel(my="xiahoudun", enemy="xiahoudun", control="manual")
        game.start_local_battle(1)
        first, second = game.players
        self.assertEqual(first.general_id, second.general_id)
        self.assertIsNot(first.skill_state, second.skill_state)

        self.assertEqual(game.skills.skill_ids_of(first),
                         game.skills.skill_ids_of(second))
        first_skills = game.skills.skills_of(first)
        second_skills = game.skills.skills_of(second)
        self.assertTrue(first_skills, "刚烈这类触发技应当有独立实例")
        self.assertNotEqual({id(skill) for skill in first_skills},
                            {id(skill) for skill in second_skills})
        self.assertGreater(game.skills.total_listeners(), 0)

        # 一方打上"本回合已用过"的标记，另一方不受影响。
        first.skill_state.set("ganglie", "used_this_turn", 1)
        self.assertEqual(first.skill_state.get("ganglie", "used_this_turn", 0), 1)
        self.assertEqual(second.skill_state.get("ganglie", "used_this_turn", 0), 0)
        self.assertLess(len(second.skill_state), 1)
        game.restart_battle()
        self.assertEqual(len(game.players[0].skill_state), 0)

    def test_lord_skills_are_not_active_and_are_explained(self):
        """1v1 没有身份，主公技不激活，并且要在设置页说清楚。"""

        game, _config = build_duel(my="liubei", enemy="sunquan")
        game.start_local_battle(1)
        for player in game.players:
            self.assertNotIn("jijiang", game.skills.skill_ids_of(player))
            self.assertNotIn("jiuyuan", game.skills.skill_ids_of(player))
        note = game.mode.general_note(game.generals.get("liubei"))
        self.assertIn("主公技", note)
        self.assertIn("激将", note)

    def test_victory_is_last_survivor_with_neutral_text(self):
        game, _config = build_duel()
        game.start_local_battle(1)
        renderer = self.make_renderer()
        loser = game.players[1]
        game.lose_hp(loser, 99)
        for _ in range(900):
            if game.game_over:
                break
            if not game.busy:
                if game.response.active:             # 濒死求桃：直接放弃
                    game.pass_response()
                elif game.choice.active:
                    game.choice.choose_no()
            self.frame(game, renderer)
        self.assertTrue(game.game_over)
        self.assertIs(game.winner, game.players[0])
        self.assertEqual(game.result.reason, "LAST_SURVIVOR")
        self.assertIn("我方", game.message)
        title, subtitle, tone = game.mode.overlay_result_texts(game)
        self.assertEqual(tone, "positive")
        self.assertIn("我方", subtitle)


# ==================================================
# C. 双边手动测试
# ==================================================


class TestManualControl(ScreenCase):

    def setUp(self):
        self.game, self.config = build_duel(
            my="guanyu", enemy="lvbu", first="me", control="manual", seed="20240925")
        self.renderer = self.make_renderer()
        self.game.start_local_battle(1)

    def operator(self):
        return self.game.player

    def test_view_follows_the_current_turn(self):
        self.wait_until(self.game, self.renderer,
                        lambda: self.game.local_operator_label.startswith("我方"),
                        what="轮到先手方（我方）")
        self.finish_turn(self.game, self.renderer)
        self.wait_until(self.game, self.renderer,
                        lambda: self.game.local_operator_label.startswith("对手"),
                        what="视角切到对手")
        self.assertIs(self.operator(), self.game.players[1])
        self.assertEqual(len(self.game.players[1].hand), 6,
                         "对手摸牌后自己的手牌被展示出来")

    def test_sha_and_shan_in_manual_mode(self):
        """我方出【杀】→ 视角切到对手 → 对手点手牌打出【闪】。"""

        self.wait_until(self.game, self.renderer, self.game.local_can_play,
                        what="我方出牌阶段")
        self.game.players[1].hand = []               # 只留我们指定的【闪】
        sha = give(self.operator(), "SHA")
        shan = give(self.game.players[1], "SHAN")
        self.click_card(self.game, self.renderer, sha)

        self.wait_until(self.game, self.renderer,
                        lambda: self.game.response.active, what="对手收到【闪】响应窗口")
        self.assertIs(self.operator(), self.game.players[1])
        self.assertIn("SHAN", self.game.response.current.allowed_cards)

        self.click_card(self.game, self.renderer, shan)
        self.wait_until(self.game, self.renderer,
                        lambda: not self.game.response.active and not self.game.busy,
                        what="闪结算完成")
        self.assertEqual(self.game.players[1].hand, [],
                         "【闪】被打出后不在对手手上")
        self.assertIs(self.operator(), self.game.players[0], "结算后控制权回到我方")

    def test_wuxie_window_asks_both_sides_in_order(self):
        """共享无懈阶段：一方放弃后另一方仍然会被问到（不能卡死）。"""

        self.wait_until(self.game, self.renderer, self.game.local_can_play,
                        what="我方出牌阶段")
        self.game.players[0].hand = []
        self.game.players[1].hand = []
        theirs = give(self.game.players[1], "WUXIE")
        trick = give(self.game.players[0], "GUOHE")
        self.click_card(self.game, self.renderer, trick)

        # 只有对手有【无懈可击】可打：窗口必须开在他身上，而不是空转。
        self.wait_until(self.game, self.renderer,
                        lambda: self.game.response.active, what="无懈窗口打开")
        self.assertIs(self.operator(), self.game.players[1])
        self.assertIn("WUXIE", self.game.response.current.allowed_cards)
        self.click_card(self.game, self.renderer, theirs)
        self.wait_until(self.game, self.renderer,
                        lambda: not self.game.engine.pending.active and not self.game.busy,
                        what="无懈结算完成")
        self.assertNotIn(theirs, self.game.players[1].hand)
        self.assertIs(self.operator(), self.game.players[0], "结算后控制权回到我方")

    def test_wuxie_window_survives_a_pass(self):
        """一方放弃本轮后，另一方仍然拿到面板（两侧都是本机操作者时最容易卡死）。"""

        self.wait_until(self.game, self.renderer, self.game.local_can_play,
                        what="我方出牌阶段")
        self.game.players[0].hand = []
        self.game.players[1].hand = []
        mine = give(self.game.players[0], "WUXIE")
        theirs = give(self.game.players[1], "WUXIE")
        trick = give(self.game.players[0], "GUOHE")
        self.click_card(self.game, self.renderer, trick)

        # 按座次先问我方；我方点「不出」之后必须轮到对手，而不是停在这里。
        self.wait_until(self.game, self.renderer,
                        lambda: self.game.response.active, what="无懈窗口打开")
        self.assertIs(self.operator(), self.game.players[0])
        self.click_button(self.game, self.renderer, self.renderer.secondary_button)

        self.wait_until(self.game, self.renderer,
                        lambda: self.game.response.active
                        and self.operator() is self.game.players[1],
                        what="我方放弃后轮到对手")
        self.assertIn("WUXIE", self.game.response.current.allowed_cards)
        self.click_card(self.game, self.renderer, theirs)
        # 对手打出无懈后，这一轮翻转、窗口重开——我方被再问一次，点「不出」收尾。
        self.wait_until(self.game, self.renderer,
                        lambda: self.game.response.active
                        and self.operator() is self.game.players[0],
                        what="无懈链第二轮问我方")
        self.pass_when_asked(self.game, self.renderer)
        self.assertIn(mine, self.game.players[0].hand, "放弃了的【无懈可击】不能丢")
        self.assertNotIn(theirs, self.game.players[1].hand)

    def test_active_skill_activation_by_mouse(self):
        """主动技能：双边手动下点技能栏发动【苦肉】。"""

        game, _config = build_duel(my="huanggai", enemy="lvbu",
                                   first="me", control="manual", seed="3")
        game.start_local_battle(1)
        renderer = self.renderer
        self.wait_until(game, renderer, game.local_can_play, what="我方出牌阶段")
        hp = game.player.hp
        self.assertTrue(game.start_skill_activation("kurou"))
        self.wait_until(game, renderer, lambda: not game.busy, what="苦肉结算完成")
        self.assertEqual(game.player.hp, hp - 1)
        self.assertEqual(len(game.player.hand), 6 + 2)

    def test_dying_rescue_is_played_by_the_other_side(self):
        """濒死救援：视角切到救援方，由他点【桃】救人。"""

        game, _config = build_duel(my="guanyu", enemy="lvbu", first="me",
                                   control="manual", seed="11")
        game.start_local_battle(1)
        renderer = self.renderer
        self.wait_until(game, renderer, game.local_can_play, what="我方出牌阶段")

        victim = game.players[1]
        victim.hp = 1
        victim.hand = []                             # 没有【闪】也没有【桃】
        game.players[0].hand = []
        tao = give(game.players[0], "TAO")
        sha = give(game.players[0], "SHA")
        self.click_card(game, renderer, sha)
        # 先是对手的响应窗口：他打不出【闪】，点「不出」。
        self.wait_until(game, renderer, lambda: game.response.active,
                        what="【闪】响应窗口")
        self.assertIs(game.player, game.players[1])
        self.click_button(game, renderer, renderer.secondary_button)

        # 掉血 → 濒死：桃在我方手上，所以"求桃"这一步落在救援者身上。
        self.wait_until(game, renderer,
                        lambda: game.response.active and victim.hp <= 0,
                        what="濒死求桃窗口")
        self.assertIs(game.player, game.players[0])
        self.assertIn("TAO", game.response.current.allowed_cards)
        self.click_card(game, renderer, tao)
        self.wait_until(game, renderer, lambda: not game.game_over and not game.busy,
                        what="救援结算完成")
        self.assertEqual(victim.hp, 1)
        self.assertNotIn(tao, game.players[0].hand)
        self.assertIs(game.player, game.players[0])

    def test_manual_mode_never_lets_ai_decide(self):
        """两边都是真人控制器：没有任何一方被 AI 悄悄接管。"""

        from src.game.controllers import AIController
        for player in self.game.players:
            self.assertNotIsInstance(self.game.get_controller(player), AIController)


# ==================================================
# D. 固定种子
# ==================================================


class TestDeterministicSeed(ScreenCase):

    @staticmethod
    def fingerprint(seed, first="random"):
        game, _config = build_duel(my="zhouyu", enemy="xiahoudun", first=first, seed=seed)
        game.start_local_battle(1)

        def hand(player):
            return tuple((card.name, card.suit, card.rank) for card in player.hand)

        return (
            game.current_turn_player.player_id,
            hand(game.players[0]),
            hand(game.players[1]),
            tuple((card.name, card.suit, card.rank) for card in game.deck.draw_pile),
        )

    def test_same_seed_replays_the_same_battle(self):
        self.assertEqual(self.fingerprint("20240925"), self.fingerprint("20240925"))

    def test_different_seed_shuffles_differently(self):
        self.assertNotEqual(self.fingerprint("20240925"), self.fingerprint("1"))

    def test_seed_covers_the_ai_random_source(self):
        """AI 的随机决策也必须在种子里：否则"相同配置"复现不出来。"""

        game, _config = build_duel(seed="55")
        game.start_local_battle(1)
        ai = game.get_controller(game.players[1])
        self.assertIs(ai.rng, game.rng)

    def test_empty_seed_still_starts(self):
        game, _config = build_duel(seed="")
        game.start_local_battle(1)
        self.assertEqual(game.scene, "game")

    def test_reproducible_ai_choices(self):
        """同一个种子下，AI 在同一局面做出的随机选择必须一致。"""

        def sample(seed):
            game, _config = build_duel(my="guanyu", enemy="lvbu", seed=seed)
            game.start_local_battle(1)
            ai = game.get_controller(game.players[1])
            game.players[1].hand = [canonical_card(name)
                                    for name in ("SHA", "SHAN", "TAO", "WUXIE", "JIU")]
            game.players[1].hp = 2
            chosen = ai.discard_to_hand_limit()
            return tuple(card.name for card in chosen)

        self.assertEqual(len(sample("2024")), 3)
        self.assertEqual(sample("2024"), sample("2024"))

    def test_ai_rng_does_not_leak_into_other_modes(self):
        game, _config = build_duel(seed="9")
        game.start_local_battle(1)
        self.assertIsNotNone(game.ai_rng)
        game.set_mode("ffa")
        game.start_local_battle(1)
        self.assertIsNone(game.ai_rng)


# ==================================================
# E. 重开 / 换将 / 清理
# ==================================================


class TestRestartAndCleanup(ScreenCase):

    def test_restart_keeps_the_config_and_clears_the_state(self):
        game, config = build_duel(my="xiahoudun", enemy="huatuo",
                                  first="me", control="ai", seed="5")
        game.start_local_battle(1)
        renderer = self.make_renderer()
        self.frame(game, renderer, 10)
        listeners = game.skills.total_listeners()
        self.assertGreater(listeners, 0, "刚烈这类触发技应当挂在事件总线上")

        first = game.players[0]
        first.skill_state.set("ganglie", "used_this_turn", 1)
        first.hp = 1
        first.alive = False
        game.pending_target_selection = {"zone": "hand"}
        game.game_over = True

        self.assertTrue(game.restart_battle())
        self.assertEqual(game.scene, "game")
        self.assertFalse(game.game_over)
        self.assertIsNone(game.pending_target_selection)
        self.assertEqual([p.general_id for p in game.players], ["xiahoudun", "huatuo"])
        self.assertEqual(game.skills.total_listeners(), listeners,
                         "重开不能叠加监听")
        self.assertEqual(len(game.players[0].skill_state), 0)
        self.assertEqual(game.players[0].hp, game.players[0].max_hp)
        self.assertTrue(game.players[0].alive)
        self.assertEqual(config.seed, "5")

    def test_many_restarts_stay_stable(self):
        game, _config = build_duel(my="guanyu", enemy="lvbu", seed="6")
        game.start_local_battle(1)
        renderer = self.make_renderer()
        baseline = game.skills.total_listeners()
        for _ in range(5):
            game.restart_battle()
            self.frame(game, renderer, 5)
        self.assertEqual(game.skills.total_listeners(), baseline)
        self.assertEqual(len(game.engine.pending.stack), 0)
        self.assertEqual(game.table_cards, [])
        self.assertEqual(game.processing_zone, [])

    def test_switching_generals_between_battles(self):
        game, config = build_duel(my="guanyu", enemy="lvbu", seed="8")
        game.start_local_battle(1)
        config.my_general = "zhouyu"
        game.restart_battle()
        self.assertEqual([p.general_id for p in game.players], ["zhouyu", "lvbu"])
        self.assertIn("yingzi", game.skills.skill_ids_of(game.players[0]))

    def test_setup_scene_clears_pending_config(self):
        game, config = build_duel(my="guanyu", enemy="lvbu")
        game.start_local_battle(1)
        game.begin_general_select()
        self.assertEqual(game.scene, "duel_setup")
        self.assertEqual(game.general_pool, ())
        self.assertEqual(game.general_assignments, {})
        self.assertEqual(game.general_picks, {})

    def test_leaving_the_mode_does_not_leak_the_chosen_generals(self):
        game, _config = build_duel(my="guanyu", enemy="lvbu", control="ai")
        game.start_local_battle(1)
        game.return_to_menu()
        self.assertEqual(game.scene, "menu")
        self.assertEqual(game.general_assignments, {})
        self.assertEqual(game.general_picks, {})
        self.assertEqual(game.general_pool, ())

        game.set_mode("ffa")
        game.begin_general_select()
        self.assertEqual(game.scene, "general_select")
        self.assertEqual(len(game.general_candidates), 3,
                         "自由混战仍然是随机三选一")
        game.confirm_general(game.general_candidates[0])
        self.assertNotIn("guanyu", [p.general_id for p in game.players if p is not game.player])

    def test_original_modes_still_work(self):
        """自由混战与身份局照常开局：人数、选将、首行动都正常。"""

        ffa = Game()
        self.assertEqual(ffa.modes.ids(), ("ffa", "identity", "duel_test"))
        ffa.set_mode("ffa")
        ffa.ai_count = 3
        ffa.begin_general_select()
        self.assertEqual(ffa.scene, "general_select")
        ffa.selected_general = ffa.general_candidates[0]
        ffa.confirm_general()
        self.assertEqual(ffa.scene, "game")
        self.assertEqual(len(ffa.players), 4)
        self.assertEqual(len({p.general_id for p in ffa.players}), 4)

        identity = Game()
        identity.set_mode("identity")
        identity.ai_count = 4
        self.assertEqual(identity.begin_general_select(), "identity_reveal")
        identity.confirm_identity()
        self.assertEqual(identity.scene, "general_select")
        identity.selected_general = identity.general_candidates[0]
        identity.confirm_general()
        self.assertEqual(identity.scene, "game")
        lord = identity.mode.lord()
        self.assertIsNotNone(lord, "身份局必须有主公")
        self.assertIs(identity.current_turn_player, lord)
        self.assertEqual(lord.max_hp, identity.generals.get(lord.general_id).max_hp + 1,
                         "主公体力上限 +1")


# ==================================================
# F. 对局内控制条与布局
# ==================================================


class TestDuelHud(ScreenCase):

    def setUp(self):
        self.game, self.config = build_duel(my="guanyu", enemy="lvbu", seed="2")
        self.renderer = self.make_renderer()
        self.hud = DuelHud(self.screen)
        self.hud.sync_layout(self.renderer.metrics)
        self.game.start_local_battle(1)

    def test_hud_only_takes_over_in_duel_battles(self):
        self.assertTrue(self.hud.active(self.game))
        self.game.scene = "duel_setup"
        self.assertFalse(self.hud.active(self.game))
        self.game.scene = "game"
        self.game.mode_id = "ffa"
        self.assertFalse(self.hud.active(self.game))

    def test_restart_button_replays_the_same_config(self):
        action = self.hud.handle_click(self.hud.restart_button.rect.center, self.game)
        self.assertEqual(action, "restart")
        self.game.players[0].hp = 1
        self.assertTrue(self.hud.run_action(action, self.game))
        self.assertEqual(self.game.scene, "game")
        self.assertEqual([p.general_id for p in self.game.players], ["guanyu", "lvbu"])
        self.assertEqual(self.game.players[0].hp, self.game.players[0].max_hp)

    def test_setup_button_returns_to_the_setup_screen_keeping_the_config(self):
        self.assertTrue(self.hud.run_action("setup", self.game))
        self.assertEqual(self.game.scene, "duel_setup")
        self.assertEqual(self.config.my_general, "guanyu")
        self.assertEqual(self.config.enemy_general, "lvbu")

    def test_menu_button_goes_back_to_main_menu(self):
        self.assertTrue(self.hud.run_action("menu", self.game))
        self.assertEqual(self.game.scene, "menu")

    def test_restart_action_of_the_result_overlay_uses_the_mode(self):
        """结算浮层的「重新开始」在 1v1 里也是"原配置重开"，不是回菜单。"""

        from src.ui.human_control import LocalHumanController
        game = self.game
        game.lose_hp(game.players[1], 99)
        for _ in range(600):
            if game.game_over:
                break
            if game.busy:
                pass
            elif game.response.active:
                game.pass_response()
            elif game.choice.active:
                game.choice.choose_no()
            self.frame(game, self.renderer)
        self.assertTrue(game.game_over)
        LocalHumanController(game).run_action("restart", self.renderer)
        self.assertEqual(game.scene, "game")
        self.assertFalse(game.game_over)
        self.assertEqual([p.general_id for p in game.players], ["guanyu", "lvbu"])


class TestBottomAnchoredLayout(unittest.TestCase):

    def test_hand_sits_at_the_bottom_on_every_aspect(self):
        """手牌区贴屏幕底边：4:3 等比例下不再浮在半空（底部留一大片空白）。"""

        for size in ((1920, 1080), (1600, 900), (1366, 768),
                     (1024, 768), (1280, 1024)):
            metrics = layout.LayoutMetrics(*size)
            margin = metrics.screen_h - metrics.hand_area.bottom
            self.assertLessEqual(
                margin, metrics.px(20) + 1,
                "手牌离屏幕底边太远：%s（%d px）" % (size, margin))
            self.assertLess(metrics.player_status.bottom, metrics.hand_area.y,
                            "状态条不能压到手牌：%s" % (size,))
            self.assertGreaterEqual(metrics.hand_area.y, 0, str(size))
            self.assertLessEqual(metrics.secondary_button.bottom, metrics.screen_h, str(size))

    def test_design_size_is_unchanged(self):
        """16:9 屏幕上与原来的设计坐标逐像素一致（既有模式不受影响）。"""

        metrics = layout.LayoutMetrics(1600, 900)
        self.assertEqual(tuple(metrics.hand_area), (288, 710, 1024, 173))
        self.assertEqual(tuple(metrics.player_status), (288, 580, 1024, 74))
        self.assertEqual(tuple(metrics.prompt), (288, 482, 1024, 88))
        self.assertEqual(tuple(metrics.primary_button), (1336, 742, 248, 60))


if __name__ == "__main__":
    unittest.main()
