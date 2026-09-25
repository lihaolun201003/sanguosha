"""Phase 10.1：横版素材竖版化 / 座次环距离 / 高亮体系。

分组：

    A  座次环距离        3～8 人的左右邻居、正对面距离
    B  死亡角色          尸体退出有效距离环
    C  Modifier          +1/-1 马、攻击范围仍然生效
    D  目标合法性        真实【杀】只能打到座次环上的距离 1 邻居
    E  UI 座次映射       左右邻居在屏幕上的位置体现座次环（只看视觉，不看规则）
    F  横版素材竖版化    竖版容器合成、缓存、不改原图
    G  视觉状态          高亮层次与优先级

全部确定性：不依赖随机、不依赖显示器。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.rules import DistanceRule
from src.game.rules.seats import SeatManager
from src.renderer import Renderer
from src.ui import assets as assets_module
from src.ui import layout as layout_module
from src.ui import theme
from src.ui.cards import card_art
from src.ui.interaction import handle_game_click
from src.ui.seats import draw_seat
from tests.legacy_helpers import canonical_card, equipment, set_draw_order, shan, tao

RESOLUTIONS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440))


class SeatDistanceBase(unittest.TestCase):
    """共用的干净对局：真人先手、手牌自定、引擎无残留。"""

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
        set_draw_order(game, [canonical_card("SHA") for _ in range(24)])
        game.player.hand = list(hand)
        pygame.event.clear()
        return game

    @staticmethod
    def ordered(game):
        return sorted(game.players, key=lambda player: player.seat)

    def distance(self, game, source, target):
        return DistanceRule.distance(game, source, target)


class SeatRingDistanceTests(SeatDistanceBase):
    """组 A：距离完全由座次环决定。"""

    def test_three_players_both_others_are_distance_one(self):
        game = self.make_game(2)
        others = [p for p in game.players if p is not game.player]
        self.assertEqual(len(others), 2)
        for player in others:
            with self.subTest(seat=player.seat):
                self.assertEqual(self.distance(game, game.player, player), 1)

    def test_four_players_opposite_is_distance_two(self):
        game = self.make_game(3)
        ordered = self.ordered(game)
        self.assertEqual(self.distance(game, game.player, ordered[1]), 1)
        self.assertEqual(self.distance(game, game.player, ordered[3]), 1)
        self.assertEqual(self.distance(game, game.player, ordered[2]), 2)

    def test_five_players_layers(self):
        game = self.make_game(4)
        ordered = self.ordered(game)
        self.assertEqual(self.distance(game, game.player, ordered[1]), 1)
        self.assertEqual(self.distance(game, game.player, ordered[4]), 1)
        self.assertEqual(self.distance(game, game.player, ordered[2]), 2)
        self.assertEqual(self.distance(game, game.player, ordered[3]), 2)

    def test_eight_players_layers(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        expected = {1: 1, 7: 1, 2: 2, 6: 2, 3: 3, 5: 3, 4: 4}
        for offset, distance in expected.items():
            with self.subTest(offset=offset):
                self.assertEqual(
                    self.distance(game, game.player, ordered[offset]), distance)

    def test_distance_is_symmetric(self):
        game = self.make_game(7)
        for player in game.players:
            if player is game.player:
                continue
            with self.subTest(seat=player.seat):
                self.assertEqual(
                    self.distance(game, game.player, player),
                    self.distance(game, player, game.player),
                )

    def test_distance_never_depends_on_list_order(self):
        """把 players 列表打乱也不影响距离（规则只认 seat）。"""

        game = self.make_game(7)
        ordered = self.ordered(game)
        before = [self.distance(game, game.player, p) for p in ordered]
        game.players.reverse()
        after = [self.distance(game, game.player, p) for p in ordered]
        self.assertEqual(before, after)

    def test_distance_to_self_is_zero(self):
        game = self.make_game(3)
        self.assertEqual(game.seats.distance(game.player, game.player), 0)

    def test_seat_manager_is_the_single_source(self):
        """Game.seats 必须就是规则层的 SeatManager，没有第二套实现。"""

        game = self.make_game(3)
        self.assertIsInstance(game.seats, SeatManager)


class DeadPlayerRingTests(SeatDistanceBase):
    """组 B：死亡角色退出有效距离环。"""

    def test_dead_neighbor_collapses_the_ring(self):
        game = self.make_game(4)          # 5 人：自己 + AI1..AI4
        ordered = self.ordered(game)
        neighbor, far = ordered[1], ordered[2]
        self.assertEqual(self.distance(game, game.player, far), 2)
        neighbor.alive = False
        self.assertEqual(self.distance(game, game.player, far), 1)

    def test_dead_player_is_unreachable(self):
        game = self.make_game(4)
        ordered = self.ordered(game)
        victim = ordered[2]
        victim.alive = False
        self.assertGreater(self.distance(game, game.player, victim), 100)

    def test_two_deaths_shrink_ring_further(self):
        game = self.make_game(6)          # 7 人
        ordered = self.ordered(game)
        target = ordered[3]
        self.assertEqual(self.distance(game, game.player, target), 3)
        ordered[2].alive = False          # 顺序剔除，环逐步收缩
        self.assertEqual(self.distance(game, game.player, target), 2)
        ordered[1].alive = False
        self.assertEqual(self.distance(game, game.player, target), 1)

    def test_zero_hp_counts_as_out_of_ring(self):
        game = self.make_game(3)
        ordered = self.ordered(game)
        ordered[1].hp = 0
        self.assertEqual(self.distance(game, game.player, ordered[2]), 1)


class DistanceModifierTests(SeatDistanceBase):
    """组 C：马与攻击范围仍然叠加在正确的基础距离上。"""

    def test_defensive_horse_adds_one(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        neighbor = ordered[1]
        self.assertEqual(self.distance(game, game.player, neighbor), 1)
        neighbor.set_equipment(equipment("DILU"))       # +1 马（的卢）
        self.assertEqual(self.distance(game, game.player, neighbor), 2)

    def test_offensive_horse_subtracts_one(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        second = ordered[2]
        self.assertEqual(self.distance(game, game.player, second), 2)
        game.player.set_equipment(equipment("CHITU"))   # -1 马（赤兔）
        self.assertEqual(self.distance(game, game.player, second), 1)

    def test_distance_never_below_one(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        game.player.set_equipment(equipment("CHITU"))
        self.assertEqual(self.distance(game, game.player, ordered[1]), 1)

    def test_attack_range_defaults_to_weapon_less_one(self):
        game = self.make_game(7)
        self.assertEqual(DistanceRule.attack_range(game, game.player), 1)

    def test_weapon_extends_attack_range(self):
        game = self.make_game(7)
        game.player.set_equipment(equipment("QINGLONG"))  # 青龙偃月刀：范围 3
        self.assertEqual(DistanceRule.attack_range(game, game.player), 3)
        ordered = self.ordered(game)
        self.assertTrue(DistanceRule.in_attack_range(
            game, game.player, ordered[3]))
        self.assertFalse(DistanceRule.in_attack_range(
            game, game.player, ordered[4]))


class ShaTargetLegalityTests(SeatDistanceBase):
    """组 D：真实【杀】的目标合法性来自座次环，不是屏幕顺序。"""

    def begin_sha(self, game):
        game.player.hand = [canonical_card("SHA")]
        self.renderer.draw(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        return game.pending_target_selection

    def test_eight_players_sha_has_two_candidates(self):
        game = self.make_game(7)
        selection = self.begin_sha(game)
        self.assertIsNotNone(selection)
        candidates = list(selection["candidates"])
        self.assertEqual(len(candidates), 2)

    def test_candidates_are_the_ring_neighbors(self):
        game = self.make_game(7)
        selection = self.begin_sha(game)
        ordered = self.ordered(game)
        expected = {ordered[1].seat, ordered[7].seat}
        self.assertEqual({p.seat for p in selection["candidates"]}, expected)

    def test_first_two_screen_seats_are_not_the_rule(self):
        """回归点：不能"从左往右数两个"，那会选中屏幕左侧的两个人。"""

        game = self.make_game(7)
        selection = self.begin_sha(game)
        candidates = list(selection["candidates"])
        table = layout_module.TableLayout(game, self.renderer.metrics)
        by_x = sorted(
            (table.seat_rects[p] for p in game.players if p in table.seat_rects
             and p.alive),
            key=lambda rect: rect.centerx,
        )
        leftmost_two = {rect.centerx for rect in by_x[:2]}
        self.assertNotEqual({table.seat_rects[p].centerx for p in candidates},
                            leftmost_two)

    def test_dead_neighbor_opens_the_next_target(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        ordered[1].alive = False
        ordered[1].hp = 0
        selection = self.begin_sha(game)
        self.assertEqual(
            {p.seat for p in selection["candidates"]},
            {ordered[2].seat, ordered[7].seat},
        )

    def test_weapon_extends_sha_candidates(self):
        game = self.make_game(7)
        game.player.set_equipment(equipment("QINGLONG"))
        selection = self.begin_sha(game)
        self.assertEqual(len(selection["candidates"]), 6)


class SeatLayoutTests(SeatDistanceBase):
    """组 E：UI 座次映射（视觉体现座次环，但不参与规则）。"""

    def seat_rects(self, game):
        return layout_module.TableLayout(game, self.renderer.metrics).seat_rects

    def test_two_players_opponent_is_on_top(self):
        game = self.make_game(1)
        rects = self.seat_rects(game)
        opponent = [p for p in game.players if p is not game.player][0]
        rect = rects[opponent]
        self.assertLess(rect.bottom, self.renderer.metrics.screen_h // 2)
        self.assertAlmostEqual(rect.centerx,
                               self.renderer.metrics.screen_w // 2, delta=80)

    def test_three_players_are_left_and_right(self):
        game = self.make_game(2)
        rects = self.seat_rects(game)
        ordered = self.ordered(game)
        middle = self.renderer.metrics.screen_w // 2
        self.assertLess(rects[ordered[1]].centerx, middle)
        self.assertGreater(rects[ordered[2]].centerx, middle)

    def test_four_players_left_top_right(self):
        game = self.make_game(3)
        rects = self.seat_rects(game)
        ordered = self.ordered(game)
        middle = self.renderer.metrics.screen_w // 2
        self.assertLess(rects[ordered[1]].centerx, middle)
        self.assertAlmostEqual(rects[ordered[2]].centerx, middle, delta=80)
        self.assertGreater(rects[ordered[3]].centerx, middle)

    def test_eight_players_neighbors_are_on_the_two_sides(self):
        game = self.make_game(7)
        rects = self.seat_rects(game)
        ordered = self.ordered(game)
        middle = self.renderer.metrics.screen_w // 2
        left_neighbor = rects[ordered[1]]
        right_neighbor = rects[ordered[7]]
        self.assertLess(left_neighbor.centerx, middle)
        self.assertGreater(right_neighbor.centerx, middle)

    def test_distance_one_neighbors_are_lower_than_the_opposite(self):
        """越近的座位越靠下——视觉上能看出谁离自己近。"""

        game = self.make_game(7)
        rects = self.seat_rects(game)
        ordered = self.ordered(game)
        opposite = rects[ordered[4]]
        self.assertGreater(rects[ordered[1]].centery, opposite.centery)
        self.assertGreater(rects[ordered[7]].centery, opposite.centery)
        self.assertGreater(rects[ordered[2]].centery, opposite.centery)

    def test_seats_do_not_overlap_at_any_player_count(self):
        for ai_count in range(1, 8):
            with self.subTest(players=ai_count + 1):
                game = self.make_game(ai_count)
                rects = list(self.seat_rects(game).values())
                for index, first in enumerate(rects):
                    for second in rects[index + 1:]:
                        self.assertFalse(first.colliderect(second), "座位重叠")

    def test_layout_is_inside_screen_at_every_resolution(self):
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                game = self.make_game(7)
                rects = layout_module.TableLayout(game, renderer.metrics).seat_rects
                bounds = pygame.Rect(0, 0, *size)
                for rect in rects.values():
                    self.assertTrue(bounds.contains(rect))
                self.assertGreaterEqual(len(rects), 1)

    def test_layout_does_not_mutate_players_order(self):
        """为 UI 重排 players 是禁止的：座次环顺序必须保持。"""

        game = self.make_game(7)
        before = [p.seat for p in game.players]
        self.seat_rects(game)
        self.assertEqual([p.seat for p in game.players], before)

    def test_ring_neighbors_share_a_side(self):
        """左右邻居各自占据一个侧边（不会跑到对面去）。"""

        game = self.make_game(5)   # 6 人
        rects = self.seat_rects(game)
        ordered = self.ordered(game)
        top_rect = rects[ordered[3]]          # 正对面
        for offset in (1, 5):
            with self.subTest(offset=offset):
                self.assertGreater(rects[ordered[offset]].centery, top_rect.centery)


class LandscapePortraitTests(unittest.TestCase):
    """组 F：横版素材的竖版化。"""

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((1280, 720))
        self.registry = assets_module.AssetRegistry()

    def tearDown(self):
        pygame.display.quit()

    def test_landscape_sources_detected(self):
        self.assertTrue(self.registry.is_landscape("card:LEBU"))
        self.assertTrue(self.registry.is_landscape("card:BINGLIANG"))
        self.assertFalse(self.registry.is_landscape("card:SHA"))

    def test_original_file_is_untouched(self):
        """竖版化是运行时的：原图仍是横版，尺寸不变。"""

        source = self.registry.surface("card:LEBU")
        self.assertGreater(source.get_width(), source.get_height())
        self.assertEqual(source.get_size(), (436, 320))

    def test_composite_fills_the_portrait_slot(self):
        surface = self.registry.oriented(
            "card:LEBU", (106, 148), title="乐不思蜀", category="锦囊")
        self.assertIsNotNone(surface)
        self.assertEqual(surface.get_size(), (106, 148))

    def test_composite_keeps_source_aspect(self):
        """合成出来的画面必然比竖版卡位更"扁"——因为横版原图没有被拉伸。"""

        surface = self.registry.oriented(
            "card:LEBU", (106, 148), title="乐不思蜀", category="锦囊")
        # 顶部给徽标留白 + 底部牌名/类型，中部才是原图；只要画面不是纯色就算通过。
        colors = {surface.get_at((x, 74))[:3]
                  for x in range(2, 104, 4)}
        self.assertGreater(len(colors), 3)

    def test_composite_is_cached(self):
        key = ((160, 224))
        first = self.registry.oriented("card:BINGLIANG", key,
                                       title="兵粮寸断", category="锦囊")
        self.registry.reset_counters()
        second = self.registry.oriented("card:BINGLIANG", key,
                                        title="兵粮寸断", category="锦囊")
        self.assertIs(first, second)
        self.assertEqual(self.registry.stats()["portraits"], 0)

    def test_different_sizes_get_different_surfaces(self):
        small = self.registry.oriented("card:LEBU", (80, 112), title="乐不思蜀")
        large = self.registry.oriented("card:LEBU", (160, 224), title="乐不思蜀")
        self.assertNotEqual(small.get_size(), large.get_size())

    def test_portrait_cards_uniform_in_hand_rendering(self):
        """手牌尺寸下横版牌与竖版牌都是"竖卡"，不会一个竖一个横条。"""

        from src.card import Card

        landscape = Card(name="LEBU", category="trick", color=(0, 0, 0),
                         suit="spade", rank="6")
        portrait = Card(name="SHA", category="basic", color=(0, 0, 0),
                        suit="spade", rank="7")
        body = pygame.Rect(0, 0, 106, 148)
        landscape_art = card_art(landscape, body, self.registry)
        portrait_art = card_art(portrait, body, self.registry)
        self.assertIsNotNone(landscape_art)
        self.assertIsNotNone(portrait_art)
        # 横版素材铺满整个竖版容器；竖版素材按原比例内接（420:572 与卡位几乎同比）。
        self.assertEqual(landscape_art[1].size, body.size)
        self.assertAlmostEqual(
            portrait_art[1].height / float(body.height), 1.0, delta=0.05)
        self.assertLessEqual(portrait_art[1].width, body.width)

    def test_rotated_modes_keep_aspect(self):
        """rotate_cw / rotate_ccw 模式也不会把图片压扁。"""

        for mode in ("rotate_cw", "rotate_ccw"):
            with self.subTest(mode=mode):
                previous = assets_module.LANDSCAPE_MODES.get("card:LEBU")
                assets_module.LANDSCAPE_MODES["card:LEBU"] = mode
                try:
                    self.registry.clear_cache()
                    surface = self.registry.oriented("card:LEBU", (106, 148))
                    self.assertEqual(surface.get_size(), (106, 148))
                finally:
                    if previous is None:
                        assets_module.LANDSCAPE_MODES.pop("card:LEBU", None)
                    else:
                        assets_module.LANDSCAPE_MODES["card:LEBU"] = previous


class VisualStateTests(SeatDistanceBase):
    """组 G：高亮层次由 theme 统一给出。"""

    def test_every_state_has_the_same_schema(self):
        for name, state in theme.VISUAL_STATES.items():
            with self.subTest(state=name):
                for field in ("border", "width", "glow", "glow_width", "dim", "label"):
                    self.assertIn(field, state)

    def test_selected_beats_hover(self):
        self.assertEqual(theme.resolve_state("hover", "selected"), "selected")

    def test_selected_target_beats_valid_target(self):
        self.assertEqual(
            theme.resolve_state("valid_target", "selected_target"),
            "selected_target")

    def test_valid_target_hover_sits_between(self):
        valid = theme.visual_state("valid_target")
        hover = theme.visual_state("valid_target_hover")
        selected = theme.visual_state("selected_target")
        self.assertGreater(hover["glow_width"], valid["glow_width"])
        self.assertGreater(selected["glow_width"], hover["glow_width"])
        self.assertGreaterEqual(selected["width"], hover["width"])
        self.assertGreaterEqual(hover["width"], valid["width"])

    def test_dead_outranks_target_states(self):
        self.assertEqual(
            theme.resolve_state("valid_target", "dead"), "dead")

    def test_hover_beats_plain_state(self):
        self.assertEqual(theme.resolve_state("none", "hover"), "hover")

    def test_resolve_state_ignores_none(self):
        self.assertEqual(theme.resolve_state(None, None, "hover"), "hover")
        self.assertEqual(theme.resolve_state(None, None), "none")

    def test_glow_border_is_cached(self):
        theme.clear_caches()
        first = theme.glow_border((100, 50), theme.TARGET_BLUE, 4, 6, 8)
        second = theme.glow_border((100, 50), theme.TARGET_BLUE, 4, 6, 8)
        self.assertIs(first, second)

    def test_seat_highlight_covers_the_whole_panel(self):
        """合法目标的高亮覆盖整块 seat（不是只圈武将图），点击区域不因此改变。"""

        game = self.make_game(3)
        table = layout_module.TableLayout(game, self.renderer.metrics)
        opponent = [p for p in game.players if p is not game.player][0]
        rect = pygame.Rect(table.seat_rects[opponent])
        draw_seat(
            self.screen, opponent, rect, metrics=table.metrics,
            general=game.generals.get(opponent.general_id),
            candidate=True, hovered=True,
        )
        # 角标与描边都画在 seat 的真实 rect 上：命中区域仍是这块 rect。
        self.assertTrue(rect.collidepoint(rect.center))
        self.assertEqual(rect.size, table.seat_rects[opponent].size)

    def test_seat_draw_accepts_all_states(self):
        game = self.make_game(3)
        table = layout_module.TableLayout(game, self.renderer.metrics)
        opponent = [p for p in game.players if p is not game.player][0]
        rect = pygame.Rect(table.seat_rects[opponent])
        for kwargs in (
            {},
            {"candidate": True},
            {"candidate": True, "hovered": True},
            {"selected": True},
            {"is_current": True},
            {"is_responding": True},
            {"in_target_mode": True},
            {"in_target_mode": True, "candidate": True, "distance_hint": 2},
            {"hovered": True, "distance_hint": 1},
        ):
            with self.subTest(kwargs=kwargs):
                opponent.alive = True
                draw_seat(self.screen, opponent, rect, metrics=table.metrics,
                          general=game.generals.get(opponent.general_id), **kwargs)
        opponent.alive = False
        draw_seat(self.screen, opponent, rect, metrics=table.metrics,
                  general=game.generals.get(opponent.general_id))

    def test_distance_hint_comes_from_rule_layer(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        self.assertEqual(self.renderer.distance_to(game, ordered[1]), 1)
        self.assertEqual(self.renderer.distance_to(game, ordered[4]), 4)
        self.assertIsNone(self.renderer.distance_to(game, game.player))

    def test_equipment_and_judgement_hover_regions(self):
        """装备槽与判定区标签都有可用的悬停热区（不必命中 13px 缩略图）。"""

        from src.ui.player import draw_player_status

        game = self.make_game(3, hand=[tao()])
        game.player.set_equipment(equipment("QINGLONG"))
        game.player.judgement_zone.append(canonical_card("LEBU"))
        table = layout_module.TableLayout(game, self.renderer.metrics)
        _rect, judge_rects = draw_player_status(self.screen, game, table)
        self.assertTrue(judge_rects)
        card, tag_rect = judge_rects[0]
        self.assertEqual(card.name, "LEBU")
        pad_x = table.metrics.px(8)
        pad_y = table.metrics.px(10)
        hotspot = tag_rect.inflate(pad_x, pad_y)
        self.assertTrue(hotspot.contains(tag_rect))
        slots = table.player_equipment_rects()
        self.assertGreater(slots["weapon"].width, 0)

    def test_hand_hover_keeps_click_accuracy(self):
        """扩大 hover 区域不能改变"点哪张就是哪张"。"""

        game = self.make_game(3, hand=[tao(), canonical_card("SHA"), shan()])
        self.renderer.draw(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        for index in range(len(rects)):
            with self.subTest(index=index):
                hit = self.renderer.card_at_position(
                    rects[index].center, game.player.hand)
                self.assertEqual(hit, index)

    def test_overlapping_hand_prefers_topmost(self):
        game = self.make_game(2, hand=[canonical_card("SHA") for _ in range(12)])
        self.renderer.draw(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        base = self.renderer.table_layout.hand_base_rects
        self.assertGreater(len(rects), 1)
        if base[0].right > base[1].left:
            # 重叠区域：应命中视觉上更靠前的后一张。
            hit = self.renderer.card_at_position(
                (base[1].left + 2, base[1].centery), game.player.hand)
            self.assertEqual(hit, 1)

    def test_target_click_selects_highlighted_seat(self):
        """能点中的目标就是被高亮的那个：seat hitbox 与绘制 rect 一致。"""

        game = self.make_game(7)
        game.player.hand = [canonical_card("SHA")]
        self.renderer.draw(game)
        rects = self.renderer.get_card_rects(game.player.hand)
        handle_game_click(rects[0].center, game, self.renderer)
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        table = layout_module.TableLayout(game, self.renderer.metrics)
        for candidate in selection["candidates"]:
            rect = table.seat_rects[candidate]
            self.assertEqual(
                self.renderer.player_at_position(rect.center, game) is candidate,
                True)


if __name__ == "__main__":
    unittest.main()
