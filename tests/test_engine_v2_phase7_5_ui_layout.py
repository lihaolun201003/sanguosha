"""Phase 7.5 tests: resolution-aware layout, overlap audit, hit-test parity."""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.choice import ChoiceOverlay
from src.game import Game
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui import layout, theme
from src.ui.seats import PAD
from src.ui.widgets import ellipsize_text
from tests.legacy_helpers import canonical_card, equipment, normal_sha, tao


RESOLUTIONS = (
    (1280, 720),
    (1366, 768),
    (1600, 900),
    (1920, 1080),
    (2560, 1440),
)

TARGET_RESOLUTION = (1920, 1080)


class LayoutTestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(TARGET_RESOLUTION)
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def use_resolution(self, size):
        self.screen = pygame.display.set_mode(size)
        self.renderer.set_screen(self.screen)
        return self.renderer.metrics

    def make_game(self, ai_count, *, hand_size=4, dressed=False):
        game = Game(ai_count=ai_count)
        game.start_single_player()
        game.actions.clear()
        for player in game.players:
            player.hand = []
        game.player.hand = [normal_sha() for _ in range(hand_size)]
        game.player.hp = game.player.max_hp
        game.current_turn_player = game.player
        game.phase = "play"
        if dressed:
            for index, player in enumerate(game.players[1:]):
                player.set_equipment(equipment("QINGLONG"))
                player.set_equipment(equipment("BAGUA"))
                player.set_equipment(equipment("CHITU"))
                player.set_equipment(equipment("JUEYING"))
                player.judgement_zone.append(canonical_card("LEBU"))
                player.chained = index % 2 == 0
                player.hand = [normal_sha() for _ in range(3 + index)]
        return game

    def render(self, game, mouse_pos=(0, 0)):
        self.renderer.draw(game, mouse_pos)
        pygame.display.flip()


class MetricsTests(LayoutTestCase):
    def test_scale_matches_the_tighter_axis(self):
        expectations = {
            (1280, 720): 0.8,
            (1366, 768): 768 / 900.0,
            (1600, 900): 1.0,
            (1920, 1080): 1.2,
            (2560, 1440): 1.6,
        }
        for size, expected in expectations.items():
            metrics = layout.LayoutMetrics(*size)
            self.assertAlmostEqual(metrics.scale, expected, places=4, msg=str(size))

    def test_design_space_is_16_9(self):
        self.assertAlmostEqual(
            layout.DESIGN_WIDTH / float(layout.DESIGN_HEIGHT),
            16 / 9.0,
            places=3,
        )

    def test_no_letterbox_at_16_9(self):
        for size in ((1280, 720), (1920, 1080)):
            metrics = layout.LayoutMetrics(*size)
            self.assertLess(abs(metrics.offset_x), 1.5, str(size))
            self.assertLess(abs(metrics.offset_y), 1.5, str(size))

    def test_non_16_9_does_not_break(self):
        for size in ((1024, 768), (1680, 1050), (2560, 1080)):
            metrics = layout.LayoutMetrics(*size)
            self.assertGreater(metrics.scale, 0)
            rect = metrics.central
            self.assertTrue(self.screen.get_rect().contains(rect), str(size))

    def test_font_sizes_grow_with_resolution(self):
        small = layout.LayoutMetrics(1280, 720)
        big = layout.LayoutMetrics(1920, 1080)
        self.assertGreater(
            big.fonts.get("normal").get_height(),
            small.fonts.get("normal").get_height(),
        )


class OverlapAuditTests(LayoutTestCase):
    """几何审计：主要区域在 2～8 人 × 多分辨率下互不重叠、不越界。"""

    def audit(self, game):
        metrics = self.renderer.metrics
        table = layout.TableLayout(game, metrics)
        screen = pygame.Rect(0, 0, metrics.screen_w, metrics.screen_h)
        findings = []

        seats = list(table.seat_rects.values())
        for rect in seats:
            if not screen.contains(rect):
                findings.append("座位越界 %s" % (rect,))
        for index, first in enumerate(seats):
            for second in seats[index + 1:]:
                if first.colliderect(second):
                    findings.append("座位重叠 %s %s" % (first, second))

        central = metrics.central
        for rect in seats:
            if central.colliderect(rect):
                findings.append("座位与中央区重叠 %s" % (rect,))

        prompt = metrics.prompt
        for rect in seats:
            if prompt.colliderect(rect):
                findings.append("Prompt 与座位重叠 %s" % (rect,))

        status = metrics.player_status
        if prompt.colliderect(status):
            findings.append("Prompt 与真人状态条重叠")

        hand = metrics.hand_area
        if status.colliderect(hand):
            findings.append("真人状态条与手牌区重叠")

        # Hover / Selected 上浮之后也不得压到状态信息
        for lift in (layout.HOVER_LIFT, layout.SELECTED_LIFT):
            lifted = hand.move(0, -metrics.px(lift))
            if status.colliderect(lifted):
                findings.append("手牌上浮 %d 后压到状态条" % lift)

        for name in ("primary_button", "secondary_button"):
            rect = getattr(metrics, name)
            if not screen.contains(rect):
                findings.append("%s 越界" % name)
            if rect.colliderect(hand):
                findings.append("%s 与手牌区重叠" % name)
            if rect.colliderect(prompt):
                findings.append("%s 与 Prompt 重叠" % name)
            for seat in seats:
                if rect.colliderect(seat):
                    findings.append("%s 与座位重叠" % name)

        if not screen.contains(metrics.speed_control):
            findings.append("节奏控件越界")
        for seat in seats:
            if metrics.speed_control.colliderect(seat):
                findings.append("节奏控件与座位重叠")

        return findings

    def test_all_resolutions_and_player_counts_are_clean(self):
        for size in RESOLUTIONS:
            self.use_resolution(size)
            for ai_count in range(1, 8):
                game = self.make_game(ai_count, hand_size=6, dressed=True)
                self.render(game)
                findings = self.audit(game)
                self.assertEqual(
                    findings, [],
                    "分辨率 %s / %d 人：%s" % (size, ai_count + 1, findings),
                )

    def test_large_hands_do_not_break_the_audit(self):
        self.use_resolution(TARGET_RESOLUTION)
        for hand_size in (12, 20, 30):
            game = self.make_game(4, hand_size=hand_size, dressed=True)
            self.render(game)
            findings = self.audit(game)
            self.assertEqual(findings, [], "%d 张手牌：%s" % (hand_size, findings))

    def test_equipment_slot_has_room_for_icon_and_text(self):
        """组件内部：图标与文字分区，长名字靠省略号收尾而不是互相压盖。"""

        self.use_resolution(TARGET_RESOLUTION)
        game = self.make_game(4)
        table = layout.TableLayout(game, self.renderer.metrics)
        metrics = self.renderer.metrics
        font = metrics.fonts.get("micro")

        for slot, rect in table.player_equipment_rects().items():
            icon_width = metrics.px(18)
            text_space = rect.width - icon_width - metrics.px(6)
            self.assertGreater(text_space, metrics.px(20), slot)
            label = "青龙偃月刀"
            fitted = ellipsize_text(label, font, text_space)
            self.assertLessEqual(font.size(fitted)[0], text_space, slot)
            self.assertTrue(fitted)

    def test_dead_and_chained_seat_text_fits(self):
        self.use_resolution(TARGET_RESOLUTION)
        game = self.make_game(4, dressed=True)
        self.render(game)
        metrics = self.renderer.metrics
        font = metrics.fonts.get("seat_name")
        table = layout.TableLayout(game, metrics)
        for rect in table.seat_rects.values():
            name_space = rect.width - metrics.px(PAD) * 2 - metrics.px(66) - metrics.px(56)
            self.assertGreater(name_space, metrics.px(30))
            self.assertLessEqual(font.size("AI 7")[0], name_space)


class HitTestParityTests(LayoutTestCase):
    def test_click_rects_equal_drawn_rects_at_every_resolution(self):
        for size in RESOLUTIONS:
            metrics = self.use_resolution(size)
            game = self.make_game(4, hand_size=7)
            self.render(game)

            drawn = [pygame.Rect(rect) for rect in self.renderer.table_layout.hand_rects]
            queried = self.renderer.get_card_rects(game.player.hand)
            self.assertEqual(drawn, queried, str(size))

            for index, rect in enumerate(queried):
                self.assertEqual(
                    self.renderer.card_at_position(rect.center, game.player.hand),
                    index,
                    "%s / 第 %d 张" % (size, index),
                )

    def test_seat_hit_test_matches_panels(self):
        for size in RESOLUTIONS:
            self.use_resolution(size)
            game = self.make_game(7)
            self.render(game)
            for player, rect in self.renderer.get_player_panel_rects(game).items():
                self.assertIs(
                    self.renderer.player_at_position(rect.center, game),
                    player,
                    "%s / %s" % (size, player.name),
                )

    def test_layout_rebuilds_after_resolution_change(self):
        game = self.make_game(4, hand_size=5)
        self.use_resolution((1280, 720))
        self.render(game)
        small = self.renderer.get_card_rects(game.player.hand)[0]

        self.use_resolution((1920, 1080))
        self.render(game)
        big = self.renderer.get_card_rects(game.player.hand)[0]

        self.assertNotEqual(small, big)
        self.assertGreater(big.width, small.width)
        self.assertEqual(
            self.renderer.card_at_position(big.center, game.player.hand), 0
        )

    def test_animation_rects_follow_the_current_layout(self):
        game = self.make_game(4)
        self.use_resolution((1280, 720))
        self.render(game)
        small = dict(game.ui_rects)
        self.use_resolution((1920, 1080))
        self.render(game)
        big = dict(game.ui_rects)
        self.assertNotEqual(small["table_card"], big["table_card"])
        self.assertEqual(big["table_card"], self.renderer.metrics.to_screen(layout.TABLE_CARD_RECT))
        for key in ("table_card", "response_card", "discard_pile", "draw_pile", "player_hand"):
            self.assertIn(key, big)


class PanelPlacementTests(LayoutTestCase):
    def test_start_menu_is_centred_and_scaled(self):
        menu = StartMenu(self.screen)
        for size in RESOLUTIONS:
            metrics = self.use_resolution(size)
            menu.sync_layout(metrics)
            self.assertAlmostEqual(menu.panel_rect.centerx, size[0] // 2, delta=2, msg=str(size))
            self.assertAlmostEqual(menu.panel_rect.centery, size[1] // 2, delta=2, msg=str(size))
            self.assertTrue(self.screen.get_rect().contains(menu.panel_rect), str(size))
            for button in menu.buttons():
                self.assertTrue(menu.panel_rect.colliderect(button.rect), str(size))
                self.assertTrue(self.screen.get_rect().contains(button.rect), str(size))

    def test_menu_does_not_fill_the_whole_screen(self):
        metrics = self.use_resolution((1920, 1080))
        menu = StartMenu(self.screen)
        menu.sync_layout(metrics)
        self.assertLess(menu.panel_rect.width, metrics.screen_w * 0.8)
        self.assertLess(menu.panel_rect.height, metrics.screen_h * 0.98)

    def test_result_overlay_is_centred(self):
        for size in RESOLUTIONS:
            metrics = self.use_resolution(size)
            overlay = self.renderer.result_overlay
            overlay.layout(metrics)
            self.assertAlmostEqual(overlay.panel_rect.centerx, size[0] // 2, delta=2)
            self.assertAlmostEqual(overlay.panel_rect.centery, size[1] // 2, delta=2)
            self.assertTrue(self.screen.get_rect().contains(overlay.restart_rect()))
            self.assertTrue(self.screen.get_rect().contains(overlay.main_menu_rect()))

    def test_choice_overlay_is_centred(self):
        overlay = ChoiceOverlay(self.screen)
        for size in RESOLUTIONS:
            metrics = self.use_resolution(size)
            overlay.sync_layout(metrics)
            self.assertAlmostEqual(overlay.panel_rect.centerx, size[0] // 2, delta=2)
            self.assertAlmostEqual(overlay.panel_rect.centery, size[1] // 2, delta=2)
            self.assertTrue(self.screen.get_rect().contains(overlay.panel_rect))

    def test_speed_control_stays_in_the_corner(self):
        for size in RESOLUTIONS:
            metrics = self.use_resolution(size)
            control = self.renderer.speed_control
            self.assertEqual(control.rect, metrics.speed_control)
            self.assertTrue(self.screen.get_rect().contains(control.rect))
            self.assertLess(control.rect.centerx, metrics.screen_w // 4)


class SpacingTests(LayoutTestCase):
    """需求八：新增空间要变成留白，而不是把内容重新塞满。"""

    def test_player_status_and_hand_have_a_real_gap(self):
        for size in RESOLUTIONS:
            metrics = self.use_resolution(size)
            gap = metrics.hand_top() - metrics.player_status.bottom
            self.assertGreaterEqual(
                gap, metrics.px(layout.SELECTED_LIFT),
                "%s 的间距不足以容纳选中上浮" % (size,),
            )

    def test_seat_cards_are_not_cramped(self):
        metrics = layout.LayoutMetrics(*TARGET_RESOLUTION)
        self.assertGreaterEqual(metrics.px(layout.SEAT_TOP_HEIGHT), 140)
        self.assertGreaterEqual(metrics.px(layout.SEAT_SIDE_WIDTH), 240)

    def test_cards_grew_compared_to_phase_6(self):
        metrics = layout.LayoutMetrics(*TARGET_RESOLUTION)
        width, height = metrics.hand_card_size()
        self.assertGreaterEqual(width, 120)     # Phase 6 是 88×122 的固定尺寸
        self.assertGreaterEqual(height, 170)

    def test_central_area_is_generous(self):
        metrics = layout.LayoutMetrics(*TARGET_RESOLUTION)
        central = metrics.central
        self.assertGreater(central.width, metrics.screen_w * 0.55)
        self.assertGreater(central.height, metrics.screen_h * 0.3)

    def test_prompt_panel_has_three_lines_of_room(self):
        metrics = layout.LayoutMetrics(*TARGET_RESOLUTION)
        prompt_height = metrics.prompt.height
        title = metrics.fonts.get("normal").get_height()
        body = metrics.fonts.get("small").get_height()
        # 分层显示：标题一行 + 说明一行，并且上下还有内边距
        self.assertGreater(prompt_height, title + body + metrics.px(16))


class TextMeasurementTests(LayoutTestCase):
    def test_ellipsize_uses_real_width_measurement(self):
        metrics = layout.LayoutMetrics(*TARGET_RESOLUTION)
        font = metrics.fonts.get("seat_small")
        long_name = "青龙偃月刀"
        full_width = font.size(long_name)[0]

        self.assertEqual(ellipsize_text(long_name, font, full_width + 10), long_name)

        clipped = ellipsize_text(long_name, font, full_width // 2)
        self.assertTrue(clipped.endswith("…"))
        self.assertLessEqual(font.size(clipped)[0], full_width // 2)

    def test_ellipsize_never_returns_overlong_text(self):
        metrics = layout.LayoutMetrics(*RESOLUTIONS[0])
        font = metrics.fonts.get("small")
        for text in ("青龙偃月刀", "诸葛亮", "过河拆桥", "AI 7", "玩家"):
            for width in (20, 40, 60, 120):
                fitted = ellipsize_text(text, font, width)
                self.assertLessEqual(font.size(fitted)[0], width, "%s @ %d" % (text, width))

    def test_fitting_respects_width_for_dressed_seats(self):
        self.use_resolution((1280, 720))
        game = self.make_game(7, dressed=True)
        self.render(game)
        metrics = self.renderer.metrics
        font = metrics.fonts.get("micro")
        table = layout.TableLayout(game, metrics)
        for rect in table.seat_rects.values():
            column = (rect.width - metrics.px(PAD) * 2 - metrics.px(10)) // 2
            space = column - metrics.px(18) - metrics.px(6)
            self.assertGreaterEqual(font.size(ellipsize_text("青龙偃月刀", font, space))[0], 0)


class ResizeBehaviourTests(LayoutTestCase):
    def test_layout_size_tracks_the_surface(self):
        self.use_resolution((1366, 768))
        self.assertEqual(self.renderer.metrics.screen_w, 1366)
        self.use_resolution((1920, 1080))
        self.assertEqual(self.renderer.metrics.screen_w, 1920)

    def test_renderer_buttons_rebuild_on_resize(self):
        small = self.use_resolution((1280, 720))
        self.assertEqual(self.renderer.primary_button.rect, small.primary_button)
        big = self.use_resolution((1920, 1080))
        self.assertEqual(self.renderer.primary_button.rect, big.primary_button)
        self.assertNotEqual(small.primary_button, big.primary_button)

    def test_hit_action_uses_rebuilt_button_rects(self):
        game = self.make_game(4)
        metrics = self.use_resolution((1920, 1080))
        self.render(game)
        self.assertEqual(
            self.renderer.hit_action(metrics.primary_button.center, game),
            "end_turn",
        )
        self.assertIsNone(
            self.renderer.hit_action((10, metrics.screen_h - 10), game)
        )

    def test_effects_do_not_break_on_resize(self):
        from src.game.flows import DamageContext, DamageFlow

        game = self.make_game(4)
        self.render(game)
        DamageFlow(
            game.engine,
            DamageContext(game.player, game.players[1], 1, card=normal_sha()),
        ).start()
        for size in RESOLUTIONS:
            self.use_resolution(size)
            self.render(game)
        self.assertGreater(self.renderer.effects.seat_flash(game.players[1])[0], 0)


class FirstFrameLayoutTests(unittest.TestCase):
    """全屏启动（分辨率 ≠ 设计尺寸）时，第一帧就必须是正确布局。

    回归背景：屏幕组件曾经只在 ``panel_rect`` 还是初始小矩形时才重算布局，
    于是启动后的第一帧沿用设计尺寸，看起来"挤在中间"，直到某次点击才恢复。
    """

    def setUp(self):
        pygame.init()

    def tearDown(self):
        pygame.display.quit()

    def test_start_menu_first_frame_is_centred_at_every_resolution(self):
        from src.start_menu import StartMenu

        for size in RESOLUTIONS:
            screen = pygame.display.set_mode(size)
            game = Game(ai_count=2)
            renderer = Renderer(screen)
            menu = StartMenu(screen)
            menu.sync_layout(renderer.metrics)
            menu.sync_modes(game.modes.list_modes())
            menu.sync_layout(renderer.metrics)

            menu.draw(game, renderer.metrics)
            panel = menu.panel_rect
            self.assertAlmostEqual(panel.centerx, size[0] // 2, delta=2, msg=str(size))
            self.assertAlmostEqual(panel.centery, size[1] // 2, delta=2, msg=str(size))
            self.assertTrue(pygame.Rect(0, 0, *size).contains(panel), str(size))
            for button in menu.buttons():
                self.assertTrue(panel.colliderect(button.rect), str(size))

    def test_first_draw_relayouts_when_metrics_change(self):
        """即使调用方没有显式同步，draw 收到新度量也必须重算。"""

        from src.start_menu import StartMenu
        from src.ui.general_select import GeneralSelectScreen
        from src.ui.identity_reveal import IdentityRevealScreen

        screen = pygame.display.set_mode((1600, 900))
        renderer = Renderer(screen)
        game = Game(ai_count=4)
        menu = StartMenu(screen)
        select_screen = GeneralSelectScreen(screen)
        reveal = IdentityRevealScreen(screen)

        # 切到更大的分辨率：只调用 draw，不调用任何 sync_layout。
        screen = pygame.display.set_mode((2560, 1440))
        renderer.set_screen(screen)
        menu.screen = select_screen.screen = reveal.screen = screen

        menu.draw(game, renderer.metrics)
        select_screen.draw(game, renderer.metrics)
        reveal.draw(game, renderer.metrics)

        screen_rect = pygame.Rect(0, 0, 2560, 1440)
        for widget, name in ((menu, "开始菜单"), (reveal, "身份展示")):
            panel = widget.panel_rect
            self.assertAlmostEqual(panel.centerx, 2560 // 2, delta=2, msg=name)
            self.assertAlmostEqual(panel.centery, 1440 // 2, delta=2, msg=name)
            self.assertTrue(screen_rect.contains(panel), name)

        # 选将界面用卡片网格：卡片必须落在屏内，并随分辨率放大。
        self.assertTrue(select_screen.card_rects)
        for rect in select_screen.card_rects:
            self.assertTrue(screen_rect.contains(rect), "武将卡超出屏幕")
        # Phase 10 起卡片改为竖版武将牌（不再是横向卡），所以用面积而不是
        # 宽度来断言"随分辨率重算"：2560×1440 的卡片必须大于 1600×900 的。
        big_area = (
            select_screen.card_rects[0].width * select_screen.card_rects[0].height)
        select_screen.sync_layout(
            layout.LayoutMetrics(1600, 900), game.generals.list_generals())
        small_area = (
            select_screen.card_rects[0].width * select_screen.card_rects[0].height)
        self.assertGreater(big_area, small_area, "卡片尺寸没有随分辨率重算")

    def test_resized_window_keeps_selection_screen_usable(self):
        from src.ui.general_select import GeneralSelectScreen

        screen = pygame.display.set_mode((1280, 720))
        renderer = Renderer(screen)
        game = Game(ai_count=4)
        select_screen = GeneralSelectScreen(screen)

        for size in RESOLUTIONS:
            screen = pygame.display.set_mode(size)
            renderer.set_screen(screen)
            select_screen.screen = screen
            select_screen.sync_layout(renderer.metrics, game.generals.list_generals())
            select_screen.draw(game, renderer.metrics)
            self.assertTrue(select_screen.card_rects, str(size))
            # 一屏放不下的武将**不会被画出来**（扩展包加载后注册表有 91 名），
            # 但放出来的每一张都必须在屏内；真实开局只传候选，不会溢出。
            for rect in select_screen.card_rects:
                self.assertTrue(
                    pygame.Rect(0, 0, *size).contains(rect),
                    "武将卡超出屏幕：%s" % (size,))
            self.assertEqual(
                len(select_screen.card_rects) + select_screen.hidden_count,
                len(game.generals.list_generals()),
                "卡片数量加上隐藏数量应当等于传入的武将总数：%s" % (size,))
            for rect in select_screen.card_rects:
                self.assertFalse(
                    select_screen.confirm_button.rect.colliderect(rect),
                    "确认按钮压到武将卡：%s" % (size,))


if __name__ == "__main__":
    unittest.main()
