"""Phase 10.4：五谷真实卡面 + 南蛮/万箭无懈窗口 + 主菜单人数控件。

分组：

    A  五谷公共牌池   真实卡面 / 竖版比例 / 牌多自动缩小 / hover 放大 / 缺素材 fallback
    B  南蛮入侵       整张牌只开一次【无懈可击】窗口，之后逐目标只要【杀】
    C  万箭齐发       同上，逐目标只要【闪】
    D  无懈回归       无懈套无懈仍可反转，其它锦囊的无懈窗口未受影响
    E  主菜单         人数数字的字号层级、居中、模式人数限制

全部确定性：不依赖随机数、不依赖屏幕、不写任何文件。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import EventType, RespondCardAction, UseCardAction
from src.game.flows.wuxie import WuxieResponseChain
from src.renderer import Renderer
from src.start_menu import StartMenu, centered_text_origin
from src.ui import assets as assets_module
from src.ui import cards as cards_module
from src.ui import layout, table, theme
from src.ui.assets import AssetRegistry
from tests.legacy_helpers import (
    canonical_card,
    normal_sha,
    set_draw_order,
    shan,
    tao,
)

# 五谷公共牌用到的素材（全部存在于 assets/，用于断言真实卡面真的接上了）。
WUGU_POOL_CARDS = ("TAO", "SHAN", "NANMAN", "BAGUA", "ZIXING", "WUZHONG")

RESOLUTIONS = ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440))

WUXIE_REASON = "wuxie_chain"


def trick(name):
    return canonical_card(name)


class Phase104TestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1600, 900))
        self.metrics = layout.LayoutMetrics(1600, 900)

    def tearDown(self):
        pygame.display.quit()

    # ---- 引擎脚手架 ----

    def make_game(self, ai_count):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        return game

    def watch_requests(self, game):
        """订阅 PENDING_CREATED，按发生顺序记录所有请求。"""

        seen = []
        game.context.events.subscribe(
            EventType.PENDING_CREATED, lambda _ctx, event: seen.append(event.payload["request"]))
        return seen

    def use_mass_trick(self, game, name, actor=None):
        """让某个角色使用南蛮 / 万箭，目标为除他之外的所有存活角色。"""

        actor = actor or game.player
        card = trick(name)
        actor.hand.append(card)
        targets = game.seats.alive_players_in_order(start_after=actor)
        game.submit_action(UseCardAction(actor, card, targets))
        return card

    def use_by_ai(self, game, name, targets=None, actor_index=1):
        """让 AI 使用一张锦囊；目标是真人时选择全部自动完成。"""

        actor = game.players[actor_index]
        card = trick(name)
        actor.hand.append(card)
        if targets is None:
            targets = game.seats.alive_players_in_order(start_after=actor)
        game.submit_action(UseCardAction(actor, card, list(targets)))
        return card

    def settle(self, game, *, pass_human=True, limit=4000):
        """推进到空闲；真人被问到就按 ``pass_human`` 回答，并记录真人可见的响应。"""

        offered = []
        for _ in range(limit):
            if game.response.active:
                current = game.response.current
                # 同一请求会跨帧存在，按 prompt 去重，只保留第一次。
                if not offered or offered[-1][1] != current.prompt:
                    offered.append((frozenset(current.allowed_cards), current.prompt))
                if pass_human:
                    game.pass_response()
                else:
                    break
            elif game.pending_request is None and not game.busy:
                break
            game.update(1 / 60)
        return offered

    @staticmethod
    def reasons(requests):
        return [(getattr(item, "context", {}) or {}).get("reason") for item in requests]

    @staticmethod
    def allowed_names(requests, reason):
        return [
            sorted(item.allowed_cards or [])
            for item in requests
            if (getattr(item, "context", {}) or {}).get("reason") == reason
        ]


# ==================================================
# A 五谷公共牌池
# ==================================================

class WuguPoolVisualTests(Phase104TestCase):

    def test_pool_cards_resolve_to_real_card_art(self):
        """公共牌池里的每一张牌都必须能取到真实卡面素材。"""

        registry = assets_module.get_registry()
        body = pygame.Rect(0, 0, 96, 134)
        for name in WUGU_POOL_CARDS:
            card = trick(name)
            with self.subTest(card=name):
                self.assertIsNotNone(
                    cards_module.card_art(card, body, registry),
                    name + " 没有解析到真实卡面",
                )

    def test_pool_rects_keep_portrait_ratio_and_never_shrink_below_art_minimum(self):
        """任何牌数下都是竖版比例，且不会小到让卡面退回纯文字排版。"""

        for count in range(2, 9):
            cards = [trick("TAO") for _ in range(count)]
            rects = layout.public_rect_list(cards)
            with self.subTest(count=count):
                self.assertEqual(len(rects), count)
                for rect in rects:
                    self.assertGreaterEqual(rect.width, cards_module.ART_MIN_WIDTH)
                    self.assertGreaterEqual(rect.height, cards_module.ART_MIN_HEIGHT)
                    ratio = rect.width / float(rect.height)
                    self.assertAlmostEqual(ratio, layout.PUBLIC_POOL_ASPECT, places=1)

    def test_pool_shrinks_when_many_cards_share_one_row(self):
        """牌少用完整尺寸，牌多自动缩小，且整行不超出允许宽度。"""

        few = layout.public_rect_list([trick("TAO") for _ in range(3)])
        many = layout.public_rect_list([trick("TAO") for _ in range(8)])
        self.assertLessEqual(many[0].width, few[0].width)
        self.assertLessEqual(many[0].height, few[0].height)

        span = many[-1].right - many[0].left
        self.assertLessEqual(span, layout.PUBLIC_POOL_MAX_SPAN)
        # 仍然居中
        center = (many[0].left + many[-1].right) / 2.0
        self.assertAlmostEqual(center, layout.DESIGN_WIDTH / 2.0, delta=1)

    def test_pool_layout_stays_inside_the_central_area_at_every_resolution(self):
        """1280×720 ～ 2560×1440：公共牌不越出屏幕、不压到提示条。"""

        entries = [trick("TAO") for _ in range(8)]
        for width, height in RESOLUTIONS:
            metrics = layout.LayoutMetrics(width, height)
            rects = layout.public_rect_list(entries, metrics)
            screen = pygame.Rect(0, 0, width, height)
            with self.subTest(resolution=(width, height)):
                for rect in rects:
                    self.assertTrue(screen.contains(rect), "公共牌越出屏幕")
                    self.assertLessEqual(rect.bottom, metrics.prompt.top,
                                         "公共牌压到了提示条")

    def test_pool_hover_rect_grows_upward_and_keeps_its_bottom_anchor(self):
        """悬停放大锚在底边中心：只向上展开，不改变水平位置。"""

        base = pygame.Rect(400, 200, 96, 128)
        lifted = table.pool_hover_rect(base, self.metrics)
        self.assertGreater(lifted.width, base.width)
        self.assertGreater(lifted.height, base.height)
        self.assertEqual(lifted.centerx, base.centerx)
        self.assertLess(lifted.bottom, base.bottom)

    def test_pool_hover_index_picks_the_card_under_the_cursor(self):
        rects = layout.public_rect_list([trick("TAO") for _ in range(4)])
        for index, rect in enumerate(rects):
            with self.subTest(index=index):
                self.assertEqual(table.pool_hover_index(rects, rect.center), index)
        self.assertIsNone(table.pool_hover_index(rects, (5, 5)))

    def test_draw_pool_keeps_the_art_path_for_small_slots(self):
        """格子低于自动判定线时，公共牌池仍然画真实卡面而不是纯文字卡。"""

        game = self.make_game(1)
        card = trick("NANMAN")
        rect = pygame.Rect(0, 0, 60, 84)
        self.assertLess(rect.width, cards_module.ART_MIN_WIDTH)

        pool_surface = pygame.Surface(rect.size)
        table.draw_pool(
            pool_surface, game, [(card, None)], [rect],
            is_candidate=lambda _c, _k: False,
            is_selected=lambda _c, _k: False,
            metrics=self.metrics,
        )
        art_surface = pygame.Surface(rect.size)
        cards_module.draw_card(art_surface, card, rect, theme.fonts(), compact=False)

        self.assertEqual(
            pygame.image.tobytes(pool_surface, "RGB"),
            pygame.image.tobytes(art_surface, "RGB"),
            "公共牌池没有走真实卡面路径",
        )

    def test_draw_pool_falls_back_without_crashing_when_asset_is_missing(self):
        """整组素材都取不到时，公共牌池退回程序绘制，不会崩掉。"""

        registry = AssetRegistry(enabled=False)
        previous = assets_module.get_registry()
        assets_module.set_registry(registry)
        try:
            game = self.make_game(1)
            cards_in_pool = [trick("HUALIU"), trick("TAO")]
            rects = layout.public_rect_list(cards_in_pool, self.metrics)
            surface = pygame.Surface(self.screen.get_size(), pygame.SRCALPHA)
            table.draw_pool(
                surface, game,
                [(card, None) for card in cards_in_pool], rects,
                is_candidate=lambda _c, _k: True,
                is_selected=lambda _c, _k: False,
                metrics=self.metrics,
            )
            for rect in rects:
                patch = surface.subsurface(rect)
                self.assertTrue(patch.get_bounding_rect().width > 0, "公共牌位置是空的")
        finally:
            assets_module.set_registry(previous)


# ==================================================
# B 南蛮入侵：无懈窗口只属于锦囊本身
# ==================================================

class NanmanWuxieWindowTests(Phase104TestCase):

    def test_nanman_nullified_never_asks_for_sha(self):
        """第一次无懈成功 → 整张南蛮结束，没有任何【杀】响应请求。"""

        game = self.make_game(3)
        game.players[1].hand = [trick("WUXIE")]
        set_draw_order(game, [shan() for _ in range(20)])
        requests = self.watch_requests(game)
        self.use_mass_trick(game, "NANMAN")
        self.settle(game, pass_human=False)

        self.assertNotIn("nanman", self.reasons(requests))
        self.assertEqual([player.hp for player in game.players[1:]], [4, 4, 4])
        self.assertIn("抵消", game.message)

    def test_nanman_opens_exactly_one_wuxie_window_before_any_sha_request(self):
        """无懈请求全部出现在第一个【杀】请求之前，之后不再出现。"""

        game = self.make_game(3)
        set_draw_order(game, [shan() for _ in range(20)])
        # 只有真人（锦囊使用者）手里有【无懈可击】，而且他会放弃：共享无懈
        # 阶段只开一次，之后逐目标只要【杀】。使用者本人也要被检查——这是
        # Phase 11.6 的语义（旧的"按座次逐人问、跳过使用者"已废弃）。
        game.player.hand = [trick("WUXIE")]
        requests = self.watch_requests(game)
        self.use_mass_trick(game, "NANMAN")
        self.settle(game)

        reasons = self.reasons(requests)
        self.assertIn(WUXIE_REASON, reasons)
        self.assertIn("nanman", reasons)
        first_sha = reasons.index("nanman")
        self.assertTrue(all(reason != WUXIE_REASON for reason in reasons[first_sha:]),
                        "进入【杀】响应后又打开了无懈窗口")
        self.assertTrue(all(reason == WUXIE_REASON for reason in reasons[:first_sha]))

    def test_nanman_response_request_allows_only_sha(self):
        """逐目标响应阶段，Pending 只接受【杀】。"""

        game = self.make_game(3)
        set_draw_order(game, [shan() for _ in range(20)])
        requests = self.watch_requests(game)
        self.use_mass_trick(game, "NANMAN")
        self.settle(game, pass_human=False)

        allowed = self.allowed_names(requests, "nanman")
        self.assertEqual(len(allowed), 3)
        for names in allowed:
            self.assertEqual(names, ["SHA"])

    def test_nanman_multi_target_uses_one_window_and_n_responses(self):
        """多人南蛮 = 1 次无懈窗口 + N 个【杀】响应，不是 N 次窗口。"""

        for ai_count in (2, 3, 7):
            game = self.make_game(ai_count)
            set_draw_order(game, [shan() for _ in range(30)])
            # 使用者（真人）手里有无懈、并且放弃：窗口由"有资格的人"决定，
            # 而不再是"按座次挨个问一遍"。
            game.player.hand = [trick("WUXIE")]
            requests = self.watch_requests(game)
            self.use_mass_trick(game, "NANMAN")
            self.settle(game)

            reasons = self.reasons(requests)
            window = reasons.count(WUXIE_REASON)
            with self.subTest(ai_count=ai_count):
                self.assertEqual(reasons.count("nanman"), ai_count)
                # 一次无懈阶段里，每个有资格的人最多收到一条请求；若按目标
                # 重开窗口，请求数会成倍增长。
                self.assertEqual(window, 1)
                self.assertEqual(reasons[:window], [WUXIE_REASON] * window)
                self.assertTrue(all(reason != WUXIE_REASON for reason in reasons[window:]))

    def test_nanman_human_response_phase_never_offers_wuxie(self):
        """真人进入【杀】响应后，允许牌只有【杀】——不再出现无懈可击。"""

        game = self.make_game(2)
        game.player.hand = [normal_sha(), trick("WUXIE")]
        set_draw_order(game, [shan() for _ in range(20)])
        self.use_by_ai(game, "NANMAN")
        offered = self.settle(game)

        self.assertTrue(offered)
        self.assertEqual(set(offered[0][0]), {"WUXIE"})
        # 第一个【杀】响应之后，真人再没有被提供过【无懈可击】。
        response_index = next(
            index for index, (allowed, _prompt) in enumerate(offered)
            if set(allowed) == {"SHA"}
        )
        for allowed, _prompt in offered[response_index:]:
            self.assertEqual(set(allowed), {"SHA"})


class MassTrickArrowTests(Phase104TestCase):
    """南蛮 / 万箭的箭头一次只指向当前结算的目标。"""

    def watch_arrows(self, game, renderer):
        """每个逐目标响应请求出现的瞬间，记录"当前生效"的箭头。"""

        observed = []
        live = lambda: [arrow for arrow in renderer.effects.arrows if not arrow.released]

        def handler(_ctx, event):
            request = event.payload["request"]
            context_data = getattr(request, "context", None) or {}
            if context_data.get("sequential_targets"):
                observed.append((request.target, list(live())))
        game.context.events.subscribe(EventType.PENDING_CREATED, handler)
        return observed

    def test_nanman_does_not_paint_every_arrow_up_front(self):
        game = self.make_game(4)
        set_draw_order(game, [shan() for _ in range(30)])
        renderer = Renderer(self.screen)
        renderer.draw(game)
        self.use_mass_trick(game, "NANMAN")
        # 刚打出时只展示卡面，还没有任何指向箭头。
        self.assertEqual(
            [arrow for arrow in renderer.effects.arrows if not arrow.released], [])

    def test_nanman_arrow_points_at_the_target_being_resolved(self):
        game = self.make_game(4)
        set_draw_order(game, [shan() for _ in range(30)])
        renderer = Renderer(self.screen)
        renderer.draw(game)
        observed = self.watch_arrows(game, renderer)
        self.use_mass_trick(game, "NANMAN")
        self.settle(game, pass_human=False)

        targets = [item[0] for item in observed]
        self.assertEqual(targets, list(game.players[1:]))
        for target, arrows in observed:
            with self.subTest(target=target.name):
                self.assertEqual(len(arrows), 1, "一帧里只能有当前目标一条箭头")
                self.assertIs(arrows[0].target, target)
                self.assertIs(arrows[0].source, game.player)

    def test_wanjian_arrow_points_at_the_target_being_resolved(self):
        game = self.make_game(4)
        set_draw_order(game, [normal_sha() for _ in range(30)])
        renderer = Renderer(self.screen)
        renderer.draw(game)
        observed = self.watch_arrows(game, renderer)
        self.use_mass_trick(game, "WANJIAN")
        self.settle(game, pass_human=False)

        targets = [item[0] for item in observed]
        self.assertEqual(targets, list(game.players[1:]))
        for target, arrows in observed:
            with self.subTest(target=target.name):
                self.assertEqual(len(arrows), 1)
                self.assertIs(arrows[0].target, target)


# ==================================================
# C 万箭齐发：同一条生命周期
# ==================================================

class WanjianWuxieWindowTests(Phase104TestCase):

    def test_wanjian_opens_exactly_one_wuxie_window_before_any_shan_request(self):
        game = self.make_game(3)
        set_draw_order(game, [normal_sha() for _ in range(20)])
        # 同南蛮：使用者本人也在共享无懈阶段的检查范围内。
        game.player.hand = [trick("WUXIE")]
        requests = self.watch_requests(game)
        self.use_mass_trick(game, "WANJIAN")
        self.settle(game)

        reasons = self.reasons(requests)
        self.assertIn(WUXIE_REASON, reasons)
        self.assertIn("wanjian", reasons)
        first_shan = reasons.index("wanjian")
        self.assertTrue(all(reason != WUXIE_REASON for reason in reasons[first_shan:]),
                        "进入【闪】响应后又打开了无懈窗口")
        self.assertTrue(all(reason == WUXIE_REASON for reason in reasons[:first_shan]))

    def test_wanjian_response_request_allows_only_shan(self):
        game = self.make_game(3)
        set_draw_order(game, [normal_sha() for _ in range(20)])
        requests = self.watch_requests(game)
        self.use_mass_trick(game, "WANJIAN")
        self.settle(game, pass_human=False)

        allowed = self.allowed_names(requests, "wanjian")
        self.assertEqual(len(allowed), 3)
        for names in allowed:
            self.assertEqual(names, ["SHAN"])

    def test_wanjian_multi_target_uses_one_window_and_n_responses(self):
        for ai_count in (2, 5, 7):
            game = self.make_game(ai_count)
            set_draw_order(game, [normal_sha() for _ in range(30)])
            game.player.hand = [trick("WUXIE")]
            requests = self.watch_requests(game)
            self.use_mass_trick(game, "WANJIAN")
            self.settle(game)

            reasons = self.reasons(requests)
            with self.subTest(ai_count=ai_count):
                window = reasons.count(WUXIE_REASON)
                self.assertEqual(reasons.count("wanjian"), ai_count)
                self.assertEqual(window, 1)
                self.assertEqual(reasons[:window], [WUXIE_REASON] * window)

    def test_wanjian_human_response_phase_never_offers_wuxie(self):
        game = self.make_game(2)
        game.player.hand = [shan(), trick("WUXIE")]
        set_draw_order(game, [normal_sha() for _ in range(20)])
        self.use_by_ai(game, "WANJIAN")
        offered = self.settle(game)

        self.assertTrue(offered)
        self.assertEqual(set(offered[0][0]), {"WUXIE"})
        response_index = next(
            index for index, (allowed, _prompt) in enumerate(offered)
            if set(allowed) == {"SHAN"}
        )
        for allowed, _prompt in offered[response_index:]:
            self.assertEqual(set(allowed), {"SHAN"})


# ==================================================
# D 无懈回归：链本身没被砍掉
# ==================================================

class WuxieRegressionTests(Phase104TestCase):

    def test_wuxie_chain_still_reverses_on_a_counter(self):
        """无懈套无懈：第二张无懈把结果反转回来，能力没有被热修砍掉。"""

        game = self.make_game(2)
        # 只留真人存活：无懈链的响应者就只剩他一个，回答完全可控。
        for player in game.players[1:]:
            player.alive = False
            player.hp = 0
        game.player.hand = [trick("WUXIE"), trick("WUXIE")]

        outcomes = []
        chain = WuxieResponseChain(
            game.engine, game.players[1], trick("NANMAN"), [game.player],
            lambda nullified: outcomes.append(nullified),
        )
        chain.start()
        self.assertFalse(chain.nullified)

        first = game.pending_request
        self.assertIs(first.target, game.player)
        game.engine.submit(
            RespondCardAction(first.target, first.request_id, game.player.hand[0], None))
        self.assertTrue(chain.nullified, "第一张无懈应当抵消")

        second = game.pending_request
        game.engine.submit(
            RespondCardAction(second.target, second.request_id, game.player.hand[0], None))
        self.assertFalse(chain.nullified, "第二张无懈应当把结果反转回来")
        self.assertEqual(chain.wuxie_count, 2)

    def test_single_target_tricks_keep_their_wuxie_window(self):
        """其它单体锦囊的无懈窗口仍在（没有被 AOE 热修误删）。"""

        for name in ("GUOHE", "SHUNSHOU", "JUEDOU", "HUOGONG"):
            game = self.make_game(2)
            set_draw_order(game, [normal_sha() for _ in range(20)])
            victim = game.player
            # 被无懈的这名角色手里有无懈：共享阶段会问他（这里只记录、不回答）。
            victim.hand = [normal_sha(), trick("WUXIE")]
            victim.hp = victim.max_hp
            requests = self.watch_requests(game)
            self.use_by_ai(game, name, [victim])
            self.settle(game, pass_human=False)

            with self.subTest(card=name):
                self.assertIn(WUXIE_REASON, self.reasons(requests))

    def test_self_target_trick_keeps_its_wuxie_window(self):
        game = self.make_game(2)
        set_draw_order(game, [normal_sha() for _ in range(20)])
        requests = self.watch_requests(game)
        card = trick("WUZHONG")
        game.player.hand.append(card)
        game.player.hand.append(trick("WUXIE"))
        game.submit_action(UseCardAction(game.player, card, [game.player]))
        self.settle(game, pass_human=False)
        self.assertIn(WUXIE_REASON, self.reasons(requests))

    def test_delayed_tricks_keep_their_wuxie_window(self):
        for name in ("LEBU", "BINGLIANG"):
            game = self.make_game(2)
            set_draw_order(game, [normal_sha() for _ in range(20)])
            game.player.hand.append(trick("WUXIE"))
            requests = self.watch_requests(game)
            self.use_by_ai(game, name, [game.player])
            self.settle(game, pass_human=False)

            with self.subTest(card=name):
                self.assertIn(WUXIE_REASON, self.reasons(requests))

    def test_wugu_and_taoyuan_keep_their_wuxie_window(self):
        for name in ("TAOYUAN", "WUGU"):
            game = self.make_game(2)
            set_draw_order(game, [tao() for _ in range(20)])
            requests = self.watch_requests(game)
            card = trick(name)
            game.player.hand.append(card)
            game.player.hand.append(trick("WUXIE"))
            targets = game.seats.alive_players_in_order(start_after=game.player, include_start=True)
            game.submit_action(UseCardAction(game.player, card, targets))
            self.settle(game, pass_human=False)

            with self.subTest(card=name):
                self.assertIn(WUXIE_REASON, self.reasons(requests))


# ==================================================
# E 主菜单人数控件
# ==================================================

class StartMenuCountTests(Phase104TestCase):

    def make_menu(self, resolution=(1600, 900)):
        metrics = layout.LayoutMetrics(*resolution)
        menu = StartMenu(self.screen)
        menu.sync_layout(metrics)
        game = Game(ai_count=4)
        game.ai_count = 4
        return menu, game, metrics

    def test_count_font_is_smaller_than_the_button_text(self):
        """人数数字不能比模式按钮 / 开始游戏更抢眼。"""

        self.assertLess(theme.FONT_SIZES["menu_count"], theme.FONT_SIZES["large"])
        self.assertLessEqual(theme.FONT_SIZES["menu_count"], theme.FONT_SIZES["normal"])
        self.assertLessEqual(theme.FONT_SIZES["menu_count"], theme.FONT_SIZES["hero"] * 0.5)

    def test_count_value_is_centered_on_its_actual_ink(self):
        """数字按墨迹居中：5 ～ 8 的视觉位置完全一致。"""

        menu, game, metrics = self.make_menu()
        font = metrics.fonts.get("menu_count")
        centers = set()
        for count in range(2, 9):
            game.ai_count = count - 1
            rendered = font.render(str(game.total_players()), True, theme.GOLD_BRIGHT)
            origin = centered_text_origin(rendered, menu.value_rect)
            ink = rendered.get_bounding_rect().move(origin)
            with self.subTest(count=count):
                self.assertLessEqual(abs(ink.centerx - menu.value_rect.centerx), 1)
                self.assertLessEqual(abs(ink.centery - menu.value_rect.centery), 1)
                self.assertTrue(menu.value_rect.contains(ink))
            centers.add((ink.centerx, ink.centery))
        self.assertEqual(len(centers), 1, "不同数字的视觉位置不一致")

    def test_count_value_stays_inside_the_box_at_every_resolution(self):
        """响应式：任何分辨率下数字都不越框、不挤到 +/- 按钮。"""

        for resolution in RESOLUTIONS:
            menu, game, metrics = self.make_menu(resolution)
            font = metrics.fonts.get("menu_count")
            with self.subTest(resolution=resolution):
                self.assertGreater(menu.value_rect.width, 0)
                self.assertGreater(menu.value_rect.height, 0)
                for count in range(2, 9):
                    game.ai_count = count - 1
                    rendered = font.render(str(game.total_players()), True, theme.GOLD_BRIGHT)
                    origin = centered_text_origin(rendered, menu.value_rect)
                    ink = rendered.get_bounding_rect().move(origin)
                    self.assertTrue(menu.value_rect.contains(ink),
                                    "人数数字越出数值框")
                    self.assertFalse(ink.colliderect(menu.minus_button.rect))
                    self.assertFalse(ink.colliderect(menu.plus_button.rect))

    def test_player_count_limits_per_mode_are_unchanged(self):
        """FFA 2～8 / Identity 5～8，切换模式后非法人数自动 clamp。"""

        game = Game(ai_count=1)
        game.set_mode("ffa")
        self.assertEqual((game.allowed_player_counts()[0],
                          game.allowed_player_counts()[-1]), (2, 8))
        for _ in range(20):
            game.adjust_player_count(1)
        self.assertEqual(game.total_players(), 8)

        game.set_mode("identity")
        self.assertEqual((game.allowed_player_counts()[0],
                          game.allowed_player_counts()[-1]), (5, 8))
        while game.total_players() > 5:
            game.adjust_player_count(-1)
        self.assertEqual(game.total_players(), 5)
        self.assertFalse(game.can_adjust_player_count(-1))

        game.set_mode("ffa")
        self.assertEqual(game.total_players(), 5)


if __name__ == "__main__":
    unittest.main()
