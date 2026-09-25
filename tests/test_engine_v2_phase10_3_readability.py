"""Phase 10.3：战场可读性（节奏 / 箭头生命周期 / 发牌 / 技能 UI / Tooltip）。

分组：

    A  语义节奏      集中配置、档位、最慢档确实更慢
    B  出牌顺序      先亮牌 → 再出现箭头
    C  箭头生命周期  响应结束才释放、用牌结束必释放、兜底上限
    D  开局发牌      每张牌恰好一次表现、不改实体牌
    E  技能 UI       真实技能名 / 说明 / 类型区分（数据驱动）
    F  Tooltip 选位  不越界、尽量避开 anchor 与已占用区域
    G  牌堆弱化      中央只留小图标 + 数字，悬停才预览
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.renderer import Renderer
from src.ui import fx as fx_module
from src.ui import layout as layout_module
from src.ui import table as table_module
from src.ui import theme
from src.ui.widgets import place_tooltip
from tests.legacy_helpers import canonical_card, set_draw_order, shan, tao

RESOLUTIONS = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440))


class HotfixBase(unittest.TestCase):

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1920, 1080))
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()
        fx_module.set_speed_preset(fx_module.ACTIVE_SPEED_PRESET)

    def make_game(self, ai_count=3, hand=(), general=None):
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
        set_draw_order(game, [canonical_card("SHA") for _ in range(30)])
        game.player.hand = list(hand)
        if general:
            game.set_general(game.player, general)
        pygame.event.clear()
        self.renderer.draw(game)
        return game

    def ordered(self, game):
        return sorted(game.players, key=lambda player: player.seat)


class TimingTests(HotfixBase):
    """组 A：语义化节奏配置。"""

    def test_timing_exposes_semantic_phases(self):
        timing = fx_module.timing()
        for name in ("card_reveal", "card_reveal_hold", "arrow_enter", "arrow_hold",
                     "response_prepare", "response_card_show", "damage_show",
                     "heal_show", "judge_reveal", "judge_hold", "between_actions",
                     "initial_deal_card", "banner_hold"):
            with self.subTest(phase=name):
                self.assertGreater(getattr(timing, name), 0.0)

    def test_very_slow_preset_exists_and_is_slowest(self):
        self.assertIn("very_slow", fx_module.ANIMATION_SPEED_PRESETS)
        presets = fx_module.ANIMATION_SPEED_PRESETS
        self.assertEqual(max(presets.values()), presets["very_slow"])

    def test_card_reveal_precedes_arrow(self):
        """先亮牌，再出现箭头——两段时间都真实存在。"""

        timing = fx_module.timing()
        self.assertGreater(timing.card_reveal, 0.3)
        self.assertGreater(timing.card_reveal_hold, 0.2)

    def test_slowest_preset_really_slow(self):
        fx_module.set_speed_preset("very_slow")
        timing = fx_module.timing()
        # 用户验收：最慢档下"不用看日志也跟得上"。
        self.assertGreaterEqual(timing.card_reveal, 0.8)
        self.assertGreaterEqual(timing.response_card_show, 0.8)
        self.assertGreaterEqual(timing.damage_show, 0.7)

    def test_presets_scale_monotonically(self):
        values = []
        for name in ("fast", "normal", "slow", "very_slow"):
            fx_module.set_speed_preset(name)
            values.append(fx_module.timing().arrow_hold)
        self.assertEqual(values, sorted(values))

    def test_fps_is_not_reduced_for_slowmo(self):
        """慢速是"事件之间留时间"，不是降低帧率。"""

        self.assertGreaterEqual(Game.SPEED_STEPS[0], 0.3)
        self.assertGreater(fx_module.timing().scale, 0)


class PresentationOrderTests(HotfixBase):
    """组 B：出牌 → 箭头 的先后顺序。"""

    def play_sha(self, game):
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        selection = game.pending_target_selection
        if selection is not None and selection["candidates"]:
            game.toggle_target_selection(selection["candidates"][0])
            game.confirm_target_selection()
        return selection

    def test_arrow_waits_for_card_reveal(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        self.play_sha(game)
        arrows = self.renderer.effects.arrows
        self.assertTrue(arrows)
        # 箭头存在，但在"亮牌停顿"结束前不可见。
        self.assertGreater(arrows[0].delay, 0)
        self.assertEqual(arrows[0].alpha, 0)

    def test_arrow_becomes_visible_after_reveal(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        self.play_sha(game)
        arrow = self.renderer.effects.arrows[0]
        for _ in range(40):
            self.renderer.effects.update(0.05)
        self.assertEqual(arrow.alpha, 255)

    def test_action_banner_uses_real_names(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        target = self.ordered(game)[1]
        self.play_sha(game)
        info = self.renderer.effects.action_display()
        self.assertIsNotNone(info)
        self.assertIn("杀", info["text"])
        self.assertIn(game.player.name, info["text"])

    def test_targetless_card_makes_no_arrow_and_no_banner(self):
        game = self.make_game(hand=[tao()])
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        self.assertEqual(self.renderer.effects.arrows, [])
        self.assertIsNone(self.renderer.effects.action_display())


class ArrowLifecycleTests(HotfixBase):
    """组 C：箭头在响应阶段仍然存在，响应结束才释放。"""

    def start_sha(self, game):
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        selection = game.pending_target_selection
        target = selection["candidates"][0]
        game.toggle_target_selection(target)
        game.confirm_target_selection()
        return target

    def test_arrow_held_during_response(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        target = self.start_sha(game)
        arrows = self.renderer.effects.arrows
        self.assertTrue(arrows)
        self.assertTrue(arrows[0].hold, "需要响应的牌，箭头必须等语义释放")
        # 推进很久（响应还没结束）：箭头仍在，且不淡出。
        for _ in range(40):
            self.renderer.effects.update(0.05)
        self.assertIn(arrows[0], self.renderer.effects.arrows)
        self.assertEqual(arrows[0].alpha, 255)

    def test_pending_resolved_releases_arrow(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        target = self.start_sha(game)
        arrow = self.renderer.effects.arrows[0]
        released = self.renderer.effects.release_arrows(release_all=True)
        self.assertGreaterEqual(released, 1)
        self.assertTrue(arrow.released)

    def test_released_arrow_eventually_disappears(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        self.start_sha(game)
        self.renderer.effects.release_arrows(release_all=True)
        for _ in range(120):
            self.renderer.effects.update(0.05)
        self.assertEqual(self.renderer.effects.arrows, [])

    def test_arrow_has_hard_timeout(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        self.start_sha(game)
        arrow = self.renderer.effects.arrows[0]
        # 兜底：即使没有任何释放事件，也不会永远挂着
        # （hold 上限走完之后还要走完淡出）。
        frames = int(fx_module.timing().arrow_max_hold / 0.05) * 2 + 80
        for _ in range(frames):
            self.renderer.effects.update(0.05)
        self.assertEqual(self.renderer.effects.arrows, [])

    def test_release_matches_only_the_same_card(self):
        game = self.make_game(hand=[canonical_card("SHA"), canonical_card("SHA")])
        self.start_sha(game)
        arrows = self.renderer.effects.arrows
        self.renderer.effects.release_arrows(key=canonical_card("WUZHONG"))
        self.assertTrue(all(not arrow.released for arrow in arrows))


class InitialDealTests(HotfixBase):
    """组 D：开局逐张发牌（只做表现）。"""

    def test_engine_marks_deal_presentation(self):
        game = Game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = game.generals.ids()[0]
        game.start_local_battle(3)
        self.assertIsNotNone(game.deal_presentation)
        owner, cards = game.deal_presentation
        self.assertIs(owner, game.player)
        self.assertEqual(len(cards), 4)

    def test_deal_does_not_change_card_count(self):
        game = Game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = game.generals.ids()[0]
        game.start_local_battle(3)
        hand_size = len(game.player.hand)
        self.renderer.draw(game)
        self.assertEqual(len(game.player.hand), hand_size, "动画不得改变实体牌")

    def test_one_flight_per_card(self):
        game = Game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = game.generals.ids()[0]
        game.start_local_battle(3)
        # 以引擎登记的表现清单为准：draw 之后手牌可能已经因为回合开始而变化。
        owner, cards = game.deal_presentation
        self.renderer.draw(game)
        flights = self.renderer.effects.deal_flights
        self.assertEqual(len(flights), len(cards), "每张牌恰好一次表现")
        self.assertEqual({id(f.card) for f in flights}, {id(c) for c in cards})

    def test_deal_is_staggered(self):
        game = Game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = game.generals.ids()[0]
        game.start_local_battle(3)
        self.renderer.draw(game)
        delays = [f.delay for f in self.renderer.effects.deal_flights]
        self.assertEqual(delays, sorted(delays))
        self.assertEqual(len(set(delays)), len(delays), "必须逐张发出")

    def test_dealt_cards_hidden_until_arrival(self):
        game = Game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = game.generals.ids()[0]
        game.start_local_battle(3)
        owner, cards = game.deal_presentation
        self.renderer.draw(game)
        hidden = self.renderer.effects.dealing_card_ids(game.player)
        self.assertEqual(len(hidden), len(cards))
        # 全部落位后不再隐藏。
        for _ in range(80):
            self.renderer.effects.update(0.05)
        self.assertEqual(self.renderer.effects.dealing_card_ids(game.player), set())

    def test_presentation_consumed_once(self):
        game = Game(ai_count=3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = game.generals.ids()[0]
        game.start_local_battle(3)
        self.renderer.draw(game)
        first = len(self.renderer.effects.deal_flights)
        self.renderer.draw(game)
        self.assertEqual(len(self.renderer.effects.deal_flights), first,
                         "第二次绘制不得重复播放")


class SkillUiTests(HotfixBase):
    """组 E：技能区完全数据驱动。"""

    def test_buttons_show_real_skill_names(self):
        game = self.make_game(general="zhouyu", hand=[tao()])
        self.renderer.draw(game)
        names = [definition.name for definition in self.renderer.skill_bar.skills]
        self.assertIn("英姿", names)
        self.assertIn("反间", names)

    def test_locked_skill_is_not_an_action_button(self):
        game = self.make_game(general="zhouyu", hand=[tao()])
        self.renderer.draw(game)
        bar = self.renderer.skill_bar
        # 英姿是锁定技：不会出现在"可发动"集合里。
        self.assertNotIn("yingzi", bar.enabled_ids)
        self.assertIn("fanjian", bar.enabled_ids)

    def test_triggered_skill_is_not_an_action_button(self):
        game = self.make_game(general="huangyueying", hand=[tao()])
        self.renderer.draw(game)
        bar = self.renderer.skill_bar
        # 集智是触发技：只显示、可查看说明，但不是"可发动按钮"。
        self.assertNotIn("jizhi", bar.enabled_ids)
        self.assertIsNotNone(bar.rect_for("jizhi"))

    def test_view_as_skill_is_actionable_when_material_allows(self):
        game = self.make_game(general="guanyu", hand=[tao()])
        self.renderer.draw(game)
        bar = self.renderer.skill_bar
        # 武圣是视为技：手里有红牌时应当可以点（进入先点技能再选牌的模式）。
        self.assertIn("wusheng", bar.enabled_ids)

    def test_skill_info_comes_from_skilldef(self):
        game = self.make_game(general="zhouyu", hand=[tao()])
        self.renderer.draw(game)
        bar = self.renderer.skill_bar
        bar.info_skill_id = "yingzi"
        text = bar.info_text(game)
        definition = game.skill_registry.require("yingzi")
        self.assertIn(definition.name, text)
        self.assertIn(definition.description, text)
        self.assertIn("锁定技", text)

    def test_clicking_non_actionable_skill_opens_info_only(self):
        game = self.make_game(general="zhouyu", hand=[tao()])
        self.renderer.draw(game)
        bar = self.renderer.skill_bar
        rect = bar.rect_for("yingzi")
        self.assertIsNotNone(rect)
        self.assertEqual(bar.hit(rect.center, game), "skill_info")
        self.assertEqual(bar.info_skill_id, "yingzi")
        self.assertIsNone(game.pending_skill_input)

    def test_clicking_actionable_skill_returns_skill_action(self):
        game = self.make_game(general="zhouyu", hand=[tao()])
        self.renderer.draw(game)
        bar = self.renderer.skill_bar
        rect = bar.rect_for("fanjian")
        self.assertEqual(bar.hit(rect.center, game), ("skill", "fanjian"))

    def test_every_roster_general_gets_buttons(self):
        """25 名武将都能在技能区列出自己的技能（主公技除外：非主公不绑定）。"""

        game = Game(ai_count=3)
        for general_id in game.generals.ids():
            general = game.generals.get(general_id)
            if general is None or not general.implemented:
                # 图鉴条目（卡面正文无法核实的武将）明确禁止开局，
                # 它们不需要技能按钮；能开局的武将一个都不能少。
                continue
            with self.subTest(general=general_id):
                game.set_general(game.player, general_id)
                self.renderer.draw(game)
                got = [item.id for item in self.renderer.skill_bar.skills]
                self.assertTrue(got, "技能区不能为空")
                for skill_id in game.skills.skill_ids_of(game.player):
                    definition = game.skill_registry.get(skill_id)
                    if definition is None or definition.is_lord_skill:
                        continue
                    self.assertIn(skill_id, got)

    def test_buttons_stay_inside_screen(self):
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                game = self.make_game(general="zhouyu", hand=[tao()])
                renderer.draw(game)
                bounds = pygame.Rect(0, 0, *size)
                for rect in renderer.skill_bar.rects:
                    self.assertTrue(bounds.contains(rect))


class TooltipPlacementTests(unittest.TestCase):
    """组 F：提示框选位。"""

    VIEWPORT = pygame.Rect(0, 0, 1920, 1080)

    def test_prefers_right_when_space_allows(self):
        anchor = pygame.Rect(100, 100, 40, 40)
        rect = place_tooltip(anchor, (300, 200), self.VIEWPORT)
        self.assertGreaterEqual(rect.left, anchor.right)

    def test_flips_left_when_no_room_on_the_right(self):
        anchor = pygame.Rect(self.VIEWPORT.right - 60, 200, 40, 40)
        rect = place_tooltip(anchor, (400, 200), self.VIEWPORT)
        self.assertLessEqual(rect.right, anchor.left)

    def test_never_leaves_viewport(self):
        for position in ((0, 0), (1900, 0), (0, 1070), (1900, 1070), (960, 540)):
            with self.subTest(position=position):
                anchor = pygame.Rect(position[0], position[1], 20, 20)
                rect = place_tooltip(anchor, (520, 380), self.VIEWPORT)
                self.assertTrue(self.VIEWPORT.contains(rect))

    def test_avoids_regions_when_possible(self):
        anchor = pygame.Rect(900, 500, 20, 20)
        blocker = pygame.Rect(940, 460, 700, 400)
        rect = place_tooltip(anchor, (300, 200), self.VIEWPORT, avoid=[blocker])
        self.assertFalse(rect.colliderect(blocker))

    def test_falls_back_to_least_overlap(self):
        anchor = pygame.Rect(960, 540, 20, 20)
        # 整个视口都被占满时，仍然要返回一个合法矩形而不是崩溃。
        blocker = pygame.Rect(0, 0, 1920, 1080)
        rect = place_tooltip(anchor, (300, 200), self.VIEWPORT, avoid=[blocker])
        self.assertTrue(self.VIEWPORT.contains(rect))

    def test_renderer_card_tooltip_avoids_seats(self):
        pygame.init()
        screen = pygame.display.set_mode((1920, 1080))
        renderer = Renderer(screen)
        game = Game(ai_count=5)
        game.scene = "game"
        game.general_pool = tuple(game.generals.ids())
        game.player.hand = []
        game.phase = "play"
        game.current_turn_player = game.player
        renderer.draw(game)
        seats = list(renderer.table_layout.seat_rects.values())
        self.assertTrue(seats)
        target = seats[0]
        rect = place_tooltip(
            target, (520, 380), screen.get_rect(), avoid=seats)
        self.assertTrue(screen.get_rect().contains(rect))
        # 核心诉求：查看某个 AI 的装备时，Tooltip 不得压住**被查看的那个面板**。
        overlap = rect.clip(target)
        self.assertEqual(overlap.width * overlap.height, 0,
                         "Tooltip 压住了被查看的座位")
        pygame.display.quit()


class PileReadabilityTests(HotfixBase):
    """组 G：牌堆 / 弃牌堆弱化。"""

    def test_pile_icons_are_small(self):
        metrics = layout_module.LayoutMetrics(1920, 1080)
        rect = metrics.to_screen(layout_module.DRAW_PILE_RECT)
        self.assertLessEqual(rect.width, metrics.px(70))
        self.assertLessEqual(rect.height, metrics.px(96))

    def test_piles_stay_inside_central_area(self):
        metrics = layout_module.LayoutMetrics(1920, 1080)
        central = metrics.central
        draw_rect = metrics.to_screen(layout_module.DRAW_PILE_RECT)
        discard_rect = metrics.to_screen(layout_module.DISCARD_PILE_RECT)
        self.assertLess(draw_rect.right, central.centerx)
        self.assertGreater(discard_rect.left, central.centerx)
        self.assertTrue(central.contains(draw_rect))
        self.assertTrue(central.contains(discard_rect))

    def test_hover_detection(self):
        metrics = layout_module.LayoutMetrics(1920, 1080)
        draw_rect = metrics.to_screen(layout_module.DRAW_PILE_RECT)
        self.assertEqual(
            table_module.pile_at_position(draw_rect.center, metrics), "draw")
        discard_rect = metrics.to_screen(layout_module.DISCARD_PILE_RECT)
        self.assertEqual(
            table_module.pile_at_position(discard_rect.center, metrics), "discard")
        self.assertIsNone(table_module.pile_at_position((5, 5), metrics))

    def test_piles_render_with_and_without_hover(self):
        game = self.make_game(hand=[tao()])
        for hover in (None, "draw", "discard"):
            with self.subTest(hover=hover):
                self.renderer.draw(game)
                table_module.draw_piles(
                    self.screen, game, self.renderer.metrics, hover=hover)

    def test_banner_and_arrows_draw_without_error(self):
        game = self.make_game(hand=[canonical_card("SHA")])
        self.renderer.draw(game)
        table_module.draw_action_banner(
            self.screen, self.renderer.metrics, {"text": "测试横幅", "alpha": 200})
        table_module.draw_action_banner(self.screen, self.renderer.metrics, None)


if __name__ == "__main__":
    unittest.main()
