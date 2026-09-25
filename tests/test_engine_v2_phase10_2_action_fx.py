"""Phase 10.2：指向箭头与动画节奏。

分组：

    A  箭头生成      单目标 / 多目标 / 无目标卡
    B  技能箭头      ACTIVE 技能的 targets
    C  生命周期      delay 错开、结束后回收、reset 清理
    D  几何与 resize 端点裁剪到座位边缘、分辨率变化后重新计算
    E  节奏配置      FX_TIMING 集中且可切换档位

只验证"产生了几条箭头、指向谁、什么时候消失"，不测试像素颜色。
多目标用例统一用【桃园结义】（一次性结算）；南蛮 / 万箭是逐目标结算，
箭头一次只画当前目标，由 Phase 10.4 的测试覆盖。
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
from tests.legacy_helpers import canonical_card, set_draw_order, tao

RESOLUTIONS = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440))


class ActionFxBase(unittest.TestCase):

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1920, 1080))
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count, hand=()):
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
        pygame.event.clear()
        self.renderer.draw(game)
        return game

    def play_card(self, game, index, targets=()):
        """让真人真的打出一张牌并指定目标。"""

        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(index, tuple(rects[index]))
        selection = game.pending_target_selection
        if selection is not None:
            for target in targets:
                game.toggle_target_selection(target)
            if targets:
                game.confirm_target_selection()
        return selection

    def ordered(self, game):
        return sorted(game.players, key=lambda player: player.seat)


class ArrowGenerationTests(ActionFxBase):
    """组 A：箭头生成本身。"""

    def test_single_target_card_makes_one_arrow(self):
        game = self.make_game(7, hand=[canonical_card("SHA")])
        target = self.ordered(game)[1]
        self.play_card(game, 0, [target])
        self.assertEqual(len(self.renderer.effects.arrows), 1)
        arrow = self.renderer.effects.arrows[0]
        self.assertIs(arrow.source, game.player)
        self.assertIs(arrow.target, target)

    def test_multi_target_card_makes_one_arrow_per_target(self):
        # 桃园结义是一次性结算的群体锦囊：每个目标一条箭头。
        # （南蛮 / 万箭改成逐目标结算后，箭头一次只画当前目标，见 Phase 10.4。）
        game = self.make_game(4, hand=[canonical_card("TAOYUAN")])
        self.play_card(game, 0)
        arrows = self.renderer.effects.arrows
        others = [p for p in game.players if p is not game.player]
        self.assertEqual(len(arrows), len(others))
        self.assertEqual({id(a.target) for a in arrows}, {id(p) for p in others})
        self.assertTrue(all(a.source is game.player for a in arrows))

    def test_multi_target_arrows_are_staggered(self):
        game = self.make_game(4, hand=[canonical_card("TAOYUAN")])
        self.play_card(game, 0)
        delays = [a.delay for a in self.renderer.effects.arrows]
        # 第一条箭头也要等"先看清牌"的停顿；之后逐个错开。
        self.assertAlmostEqual(delays[0], fx_module.timing().card_reveal_hold, places=3)
        self.assertEqual(len(set(delays)), len(delays), "多目标必须逐个出现")
        self.assertEqual(delays, sorted(delays))

    def test_targetless_card_makes_no_arrow(self):
        game = self.make_game(3, hand=[tao()])
        self.play_card(game, 0)
        self.assertEqual(self.renderer.effects.arrows, [])

    def test_equipment_card_makes_no_arrow(self):
        game = self.make_game(3, hand=[canonical_card("ZHUGE")])
        self.play_card(game, 0)
        self.assertEqual(self.renderer.effects.arrows, [])

    def test_arrow_ignores_self_target(self):
        effects = self.renderer.effects
        effects.reset()
        game = self.make_game(3)
        self.assertEqual(effects.add_arrow(game.player, [game.player]), 0)
        self.assertEqual(effects.arrows, [])

    def test_dead_target_still_records_but_never_duplicates(self):
        """阵亡角色不会再成为新目标；已记录的箭头仍然按目标去重。"""

        game = self.make_game(4, hand=[canonical_card("NANMAN")])
        ordered = self.ordered(game)
        ordered[1].alive = False
        ordered[1].hp = 0
        self.play_card(game, 0)
        targets = [a.target for a in self.renderer.effects.arrows]
        self.assertNotIn(ordered[1], targets)
        self.assertEqual(len(targets), len(set(id(t) for t in targets)))

    def test_arrow_color_depends_on_card_attribute_not_name(self):
        from src.card import Card

        red = Card(name="SHA", category="basic", color=(0, 0, 0),
                   suit="heart", rank="5")
        black = Card(name="SHA", category="basic", color=(0, 0, 0),
                     suit="spade", rank="7")
        equip = Card(name="ZHUGE", category="equipment", color=(0, 0, 0),
                     subtype="weapon")
        self.assertNotEqual(fx_module.arrow_color_for_card(red),
                            fx_module.arrow_color_for_card(black))
        self.assertNotEqual(fx_module.arrow_color_for_card(black),
                            fx_module.arrow_color_for_card(equip))


class SkillArrowTests(ActionFxBase):
    """组 B：技能指向。"""

    def test_active_skill_emits_target_arrow(self):
        game = self.make_game(3)
        game.set_general(game.player, "zhouyu")     # 反间：弃一张牌令目标受伤或弃牌
        game.current_turn_player = game.player
        game.phase = "play"
        game.player.hand = [tao(), canonical_card("SHA"), canonical_card("SHAN")]
        self.renderer.draw(game)

        target = self.ordered(game)[1]
        started = game.start_skill_activation("fanjian")
        if not started:
            self.skipTest("反间当前不可发动")
        game.toggle_skill_target(target)
        rects = self.renderer.get_card_rects(game.player.hand)
        game.select_skill_cost_card(game.player.hand[0])
        game.confirm_skill_input()

        arrows = [a for a in self.renderer.effects.arrows if a.target is target]
        self.assertTrue(arrows, "技能必须产生指向目标的箭头")
        self.assertIs(arrows[0].source, game.player)

    def test_skill_without_target_makes_no_arrow(self):
        game = self.make_game(3)
        effects = self.renderer.effects
        effects.reset()
        effects.add_arrow(game.player, ())
        self.assertEqual(effects.arrows, [])


class ArrowLifecycleTests(ActionFxBase):
    """组 C：箭头生命周期。"""

    def make_arrow(self, game, delay=0.0):
        effects = self.renderer.effects
        effects.reset()
        target = [p for p in game.players if p is not game.player][0]
        arrow = fx_module.TargetArrow(
            game.player, target, (255, 255, 255), fx_module.timing().target_arrow,
            delay=delay)
        effects.arrows.append(arrow)
        return arrow

    def test_arrow_alpha_ramps_up_and_out(self):
        game = self.make_game(3)
        arrow = self.make_arrow(game)
        # 刚创建：还没进入（alpha 0），推进一小步后开始淡入。
        self.assertEqual(arrow.alpha, 0)
        arrow.update(0.05)
        early = arrow.alpha
        # 入场时长由 timing().arrow_enter 决定（Phase 11.5 把它放慢了），
        # 所以这里按它推进，而不是写死一个秒数。
        arrow.update(fx_module.timing().arrow_enter + 0.05)
        middle = arrow.alpha
        self.assertLess(early, middle)
        self.assertEqual(middle, 255)
        # 释放后按剩余生命淡出。
        arrow.release()
        arrow.life = arrow.max_life * 0.05
        self.assertLess(arrow.alpha, middle)

    def test_delayed_arrow_waits_then_appears(self):
        game = self.make_game(3)
        arrow = self.make_arrow(game, delay=0.5)
        self.assertTrue(arrow.waiting)
        self.assertEqual(arrow.alpha, 0)
        arrow.update(0.6)
        self.assertFalse(arrow.waiting)
        self.assertGreater(arrow.alpha, 0)

    def test_arrow_is_removed_after_lifetime(self):
        game = self.make_game(3)
        effects = self.renderer.effects
        self.make_arrow(game)
        self.assertTrue(effects.arrows)
        for _ in range(200):
            effects.update(0.05)
        self.assertEqual(effects.arrows, [])

    def test_reset_clears_arrows(self):
        game = self.make_game(3)
        effects = self.renderer.effects
        self.make_arrow(game)
        self.assertTrue(effects.arrows)
        effects.reset()
        self.assertEqual(effects.arrows, [])

    def test_arrows_do_not_accumulate_across_frames(self):
        game = self.make_game(3)
        effects = self.renderer.effects
        self.make_arrow(game)
        initial = len(effects.arrows)
        for _ in range(30):
            self.renderer.draw(game)
            self.renderer.update(0.016)
        self.assertEqual(len(effects.arrows), initial)

    def test_arrow_draw_is_pure_presentation(self):
        """画箭头不能改动任何规则数据。"""

        game = self.make_game(4, hand=[canonical_card("TAOYUAN")])
        self.play_card(game, 0)
        before = (game.player.hp, len(game.player.hand),
                  tuple(p.hp for p in game.players))
        layout = layout_module.TableLayout(game, self.renderer.metrics)
        # 箭头要先等"亮牌停顿"再淡入（首帧 alpha 为 0 是正确的表现）。
        for _ in range(40):
            self.renderer.effects.update(0.05)
        drawn = table_module.draw_action_arrows(
            self.screen, self.renderer.effects.arrows, layout, self.renderer.metrics)
        after = (game.player.hp, len(game.player.hand),
                 tuple(p.hp for p in game.players))
        self.assertGreaterEqual(drawn, 1)
        self.assertEqual(before, after)


class ArrowGeometryTests(ActionFxBase):
    """组 D：几何与分辨率。"""

    def test_endpoints_stay_outside_panel_contents(self):
        game = self.make_game(7)
        layout = layout_module.TableLayout(game, self.renderer.metrics)
        effects = self.renderer.effects
        effects.reset()
        source = game.player
        target = self.ordered(game)[1]
        effects.add_arrow(source, [target])
        arrow = effects.arrows[0]
        source_rect = layout.any_seat_rect(source)
        target_rect = layout.any_seat_rect(target)
        start, end = arrow.endpoints(source_rect, target_rect)
        # 端点落在面板边缘上：既在面板外一点之外，也不会深入面板内部。
        self.assertFalse(source_rect.inflate(-4, -4).collidepoint(start))
        self.assertFalse(target_rect.inflate(-4, -4).collidepoint(end))

    def test_endpoints_track_layout_after_resize(self):
        game = self.make_game(7)
        target = self.ordered(game)[1]
        points = []
        for size in RESOLUTIONS:
            screen = pygame.display.set_mode(size)
            renderer = Renderer(screen)
            renderer.draw(game)
            layout = layout_module.TableLayout(game, renderer.metrics)
            effects = renderer.effects
            effects.reset()
            effects.add_arrow(game.player, [target])
            arrow = effects.arrows[0]
            start, end = arrow.endpoints(
                layout.any_seat_rect(game.player), layout.any_seat_rect(target))
            bounds = pygame.Rect(0, 0, *size)
            self.assertTrue(bounds.collidepoint(start))
            self.assertTrue(bounds.collidepoint(end))
            points.append(end)
        self.assertGreater(len(set(points)), 1, "分辨率变化后端点必须重算")

    def test_rect_edge_point_handles_horizontal_and_vertical(self):
        rect = pygame.Rect(0, 0, 100, 50)
        right = fx_module._rect_edge_point(rect, 1, 0)
        bottom = fx_module._rect_edge_point(rect, 0, 1)
        self.assertAlmostEqual(right[0], 100)
        self.assertAlmostEqual(right[1], 25)
        self.assertAlmostEqual(bottom[0], 50)
        self.assertAlmostEqual(bottom[1], 50)

    def test_arrow_skipped_when_participant_has_no_rect(self):
        game = self.make_game(3)
        layout = layout_module.TableLayout(game, self.renderer.metrics)
        effects = self.renderer.effects
        effects.reset()

        class Ghost:
            name = "ghost"

        effects.add_arrow(game.player, [Ghost()])
        drawn = table_module.draw_action_arrows(
            self.screen, effects.arrows, layout, self.renderer.metrics)
        self.assertEqual(drawn, 0)


class TimingTests(unittest.TestCase):
    """组 E：动画节奏配置。"""

    def tearDown(self):
        fx_module.set_speed_preset(fx_module.ACTIVE_SPEED_PRESET)

    def test_timing_is_centralized(self):
        self.assertTrue(hasattr(fx_module, "FXTiming"))
        t = fx_module.timing()
        for name in ("card_play", "target_arrow", "target_step", "judge_display",
                     "damage_float", "flash", "shake", "float"):
            self.assertGreater(getattr(t, name), 0.0, name)

    def test_key_actions_are_slower_than_before(self):
        t = fx_module.timing()
        # Phase 10.2 的目标：关键动作明显放慢。
        self.assertGreaterEqual(t.target_arrow, 0.80)
        self.assertGreaterEqual(t.judge_display, 0.90)
        self.assertGreaterEqual(t.target_step, 0.25)

    def test_speed_presets_scale_all_durations(self):
        normal = fx_module.timing().target_arrow
        fx_module.set_speed_preset("very_slow")
        very_slow = fx_module.timing().target_arrow
        fx_module.set_speed_preset("fast")
        fast = fx_module.timing().target_arrow
        self.assertGreater(very_slow, normal)
        self.assertLess(fast, normal)

    def test_unknown_preset_keeps_current(self):
        before = fx_module.ANIMATION_SPEED
        self.assertEqual(fx_module.set_speed_preset("nonsense"), before)

    def test_default_engine_speed_is_slower_than_before(self):
        self.assertLess(Game.DEFAULT_SPEED, 1.0)
        self.assertIn(Game.DEFAULT_SPEED, Game.SPEED_STEPS)


if __name__ == "__main__":
    unittest.main()
