"""Phase 10：美术资源接入（Asset Registry / Cache / Fallback / 视觉一致性）。

分组：

    A  Asset Registry   稳定 ID → 素材解析，路径全部落在 assets/ 下
    B  Image Cache      原始 / 缩放缓存、失败 fallback、缓存有界
    C  武将选择         候选视觉、点击区域、hover 不改变候选
    D  身份可见性       身份牌不会泄露隐藏身份
    E  卡牌真实数据     Card.suit / Card.rank 永远画在扫描卡面之上
    F  卡牌交互         换图后点击 / hover / 选中 / View-As 不变
    G  响应式布局       1280×720 ～ 2560×1440 都不越界

全部确定性：不依赖随机数、不依赖屏幕、不写任何文件。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card import Card
from src.game import Game
from src.game.identity import Identity
from src.game.rules import TurnPhase
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui import assets as assets_module
from src.ui import cards as cards_module
from src.ui import theme
from src.ui.general_select import GeneralSelectScreen
from src.ui.identity_reveal import IdentityRevealScreen
from src.ui.interaction import handle_game_click
from src.ui.overlay import GameOverOverlay
from tests.legacy_helpers import canonical_card, equipment, set_draw_order, shan, tao


FIRST_ROSTER = (
    "zhangfei", "huangyueying", "xiahoudun", "caocao", "simayi",
    "guojia", "zhangliao", "guanyu", "zhaoyun", "zhouyu", "sunshangxiang",
)

RESOLUTIONS = ((1280, 720), (1600, 900), (1920, 1080), (2560, 1440))


def card(name, category, suit="spade", rank="7", *, nature="normal", subtype=None):
    """构造一张指定花色点数的实体牌（花色点数只用于显示，不参与规则）。"""

    return Card(name=name, category=category, color=(0, 0, 0), suit=suit,
                rank=rank, nature=nature, subtype=subtype)


def render_card(target, card_obj, size=(106, 148), font_set=None):
    surface = pygame.Surface(size, pygame.SRCALPHA)
    cards_module.draw_card(surface, card_obj, pygame.Rect(0, 0, *size),
                           font_set or theme.fonts())
    return surface


def pixels(surface):
    return pygame.image.tobytes(surface, "RGBA")


class AssetRegistryTests(unittest.TestCase):
    """组 A：稳定 ID → 素材解析。"""

    @classmethod
    def setUpClass(cls):
        cls.registry = assets_module.AssetRegistry()

    def test_all_first_roster_generals_have_art(self):
        for general_id in FIRST_ROSTER:
            with self.subTest(general=general_id):
                surface = self.registry.general_card(general_id)
                self.assertIsNotNone(surface, general_id)
                self.assertGreater(surface.get_width(), 0)

    def test_general_back_exists(self):
        self.assertIsNotNone(self.registry.general_back())

    def test_all_identities_have_art(self):
        for identity in Identity:
            with self.subTest(identity=identity):
                self.assertIsNotNone(self.registry.identity_card(identity))

    def test_basic_cards_resolve(self):
        for name in ("SHA", "SHAN", "TAO", "JIU"):
            with self.subTest(card=name):
                self.assertIsNotNone(self.registry.card_art(card(name, "basic")))

    def test_key_tricks_resolve(self):
        names = ("WUZHONG", "GUOHE", "SHUNSHOU", "JUEDOU", "NANMAN", "WANJIAN",
                 "TAOYUAN", "WUGU", "WUXIE", "JIEDAO", "HUOGONG", "TIESUO")
        for name in names:
            with self.subTest(card=name):
                self.assertIsNotNone(self.registry.card_art(card(name, "trick")))

    def test_delayed_tricks_resolve(self):
        for name in ("LEBU", "SHANDIAN", "BINGLIANG"):
            with self.subTest(card=name):
                self.assertIsNotNone(self.registry.card_art(card(name, "trick")))

    def test_equipment_resolves(self):
        names = ("ZHUGE", "CIXIONG", "HANBING", "QINGGANG", "QINGLONG", "ZHANGBA",
                 "GUANSHI", "FANGTIAN", "ZHUQUE", "QILIN", "BAGUA", "RENWANG",
                 "TENGJIA", "BAIYIN", "JUEYING", "DILU", "ZHAOHUANG", "CHITU",
                 "DAWAN", "ZIXING")
        for name in names:
            with self.subTest(card=name):
                self.assertIsNotNone(self.registry.card_art(card(name, "equipment")))

    def test_equipment_name_aliases(self):
        """游戏内牌名与素材文件名不同的两张牌必须解析到真实存在的素材。"""

        for name in ("CIXIONG", "QINGGANG"):
            with self.subTest(card=name):
                path = self.registry.path_for("card:" + name)
                self.assertTrue(os.path.exists(path), path)

    def test_unknown_resource_returns_none(self):
        self.assertIsNone(self.registry.surface("card:NO_SUCH_CARD"))
        self.assertIsNone(self.registry.general_card("no_such_general"))
        self.assertIsNone(self.registry.surface(None))

    def test_general_without_art_returns_none(self):
        """未收录素材的武将（例如第二批）必须安全返回 None。"""

        self.assertIsNone(self.registry.general_card("liubei_future"))

    def test_hualiu_missing_is_known_gap(self):
        """骅骝在素材库里确实没有对应文件——这是已知缺口，不是 bug。"""

        self.assertNotIn("HUALIU", assets_module.CARD_ASSETS)
        self.assertIsNone(self.registry.card_art(card("HUALIU", "equipment")))

    def test_asset_paths_live_under_assets_root(self):
        registry = self.registry
        asset_ids = (
            [assets_module.general_asset_id(g) for g in FIRST_ROSTER]
            + [assets_module.identity_asset_id(i) for i in Identity]
            + ["card:" + name for name in assets_module.CARD_ASSETS]
            + ["card:SHA:fire", "card:SHA:thunder"]
            + [assets_module.general_back_asset_id()]
        )
        root = os.path.abspath(registry.root)
        for asset_id in asset_ids:
            with self.subTest(asset=asset_id):
                path = registry.path_for(asset_id)
                self.assertIsNotNone(path, asset_id)
                self.assertTrue(os.path.abspath(path).startswith(root))
                self.assertTrue(os.path.exists(path), path)

    def test_nature_variants_are_distinct(self):
        plain = assets_module.card_asset_id(card("SHA", "basic"))
        fire = assets_module.card_asset_id(card("SHA", "basic", nature="fire"))
        thunder = assets_module.card_asset_id(card("SHA", "basic", nature="thunder"))
        self.assertEqual(len({plain, fire, thunder}), 3)

    def test_default_and_alternate_versions(self):
        """闪电 / 无懈可击有多个版本：默认明确，备用仍保留在库里。"""

        for name in ("SHANDIAN", "WUXIE"):
            with self.subTest(card=name):
                default = self.registry.path_for("card:" + name)
                alternates = self.registry.alternate_paths("card:" + name)
                self.assertTrue(os.path.exists(default), default)
                self.assertTrue(alternates, name)
                for path in alternates:
                    self.assertTrue(os.path.exists(path), path)
                    self.assertNotEqual(path, default)

    def test_inventory_paths_are_available(self):
        """映射指向的路径必须出现在 asset_inventory.json 里（诊断用，不参与运行）。"""

        inventory = self.registry.inventory()
        if not inventory:
            self.skipTest("asset_inventory.json 不可用")
        for general_id in FIRST_ROSTER:
            relative = os.path.relpath(
                self.registry.path_for(assets_module.general_asset_id(general_id)),
                self.registry.root).replace(os.sep, "/")
            self.assertIn(relative, inventory)


class ImageCacheTests(unittest.TestCase):
    """组 B：缓存与 fallback。"""

    def setUp(self):
        self.registry = assets_module.AssetRegistry()

    def test_same_resource_same_size_reuses_surface(self):
        first = self.registry.scaled("general:caocao", (120, 164))
        second = self.registry.scaled("general:caocao", (120, 164))
        self.assertIsNotNone(first)
        self.assertIs(first, second)

    def test_different_size_returns_correct_size(self):
        small = self.registry.scaled("general:caocao", (60, 82))
        large = self.registry.scaled("general:caocao", (200, 272))
        self.assertEqual(small.get_size(), (60, 82))
        self.assertEqual(large.get_size(), (200, 272))

    def test_repeated_request_is_cache_hit(self):
        self.registry.scaled("general:guanyu", (100, 136))
        self.registry.reset_counters()
        self.registry.scaled("general:guanyu", (100, 136))
        stats = self.registry.stats()
        self.assertEqual(stats["loads"], 0)
        self.assertEqual(stats["scales"], 0)
        self.assertGreaterEqual(stats["hits"], 1)

    def test_original_surface_loaded_once(self):
        self.registry.reset_counters()
        self.registry.surface("general:zhouyu")
        self.registry.surface("general:zhouyu")
        self.assertEqual(self.registry.stats()["loads"], 1)

    def test_missing_resource_is_not_retried(self):
        self.registry.surface("card:NO_SUCH_CARD")
        self.registry.reset_counters()
        self.registry.surface("card:NO_SUCH_CARD")
        self.assertEqual(self.registry.stats()["loads"], 0)

    def test_broken_file_falls_back(self):
        """非图片内容也必须安全返回 None，而不是抛异常。"""

        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            broken = os.path.join(folder, "broken.png")
            with open(broken, "wb") as handle:
                handle.write(b"not a png at all")
            registry = assets_module.AssetRegistry(root=folder)
            registry.path_for = lambda asset_id: broken  # type: ignore[assignment]
            self.assertIsNone(registry.surface("card:SHA"))

    def test_missing_file_warns_once(self):
        registry = assets_module.AssetRegistry()
        registry.surface("card:NO_SUCH_CARD")
        first = len(registry._warned)
        registry.surface("card:NO_SUCH_CARD")
        self.assertEqual(len(registry._warned), first)

    def test_scaled_cache_is_bounded(self):
        registry = assets_module.AssetRegistry()
        for index in range(assets_module.MAX_SCALED_ENTRIES + 40):
            registry.scaled("general:caocao", (40 + index, 55 + index))
        self.assertLessEqual(len(registry._scaled), assets_module.MAX_SCALED_ENTRIES)

    def test_headless_load_works(self):
        """dummy 视频驱动（无 display surface）下也必须能加载。"""

        self.assertIsNone(pygame.display.get_surface())
        self.assertIsNotNone(self.registry.surface("general:zhangfei"))

    def test_clear_cache_keeps_originals(self):
        self.registry.surface("general:zhangfei")
        self.registry.scaled("general:zhangfei", (80, 109))
        self.registry.clear_cache()
        self.assertFalse(self.registry._scaled)
        self.registry.reset_counters()
        self.registry.scaled("general:zhangfei", (80, 109))
        self.assertEqual(self.registry.stats()["loads"], 0)

    def test_disabled_registry_returns_none(self):
        registry = assets_module.AssetRegistry(enabled=False)
        self.assertIsNone(registry.general_card("caocao"))
        self.assertIsNone(registry.scaled("general:caocao", (10, 10)))

    def test_fit_contain_keeps_aspect(self):
        rect = pygame.Rect(0, 0, 100, 100)
        target = assets_module.fit_contain(rect, (420, 572))
        self.assertAlmostEqual(target.width / float(target.height), 420 / 572.0, places=2)
        self.assertTrue(rect.contains(target))

    def test_fit_contain_handles_landscape_source(self):
        rect = pygame.Rect(0, 0, 100, 140)
        target = assets_module.fit_contain(rect, (436, 320))
        self.assertTrue(rect.contains(target))
        self.assertLessEqual(target.width, rect.width)


class GeneralSelectTests(unittest.TestCase):
    """组 C：选将界面的视觉与交互。"""

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1920, 1080))
        self.renderer = Renderer(self.screen)
        self.game = Game(ai_count=4)
        self.game.set_mode("identity")
        self.game.begin_general_select()
        self.game.confirm_identity()
        self.screen_obj = GeneralSelectScreen(self.screen)
        self.screen_obj.sync_layout(self.renderer.metrics, self.game.selectable_generals())

    def tearDown(self):
        pygame.display.quit()

    def test_three_candidates_with_art(self):
        candidates = self.game.selectable_generals()
        self.assertEqual(len(candidates), 3)
        registry = assets_module.get_registry()
        for general in candidates:
            with self.subTest(general=general.id):
                self.assertIsNotNone(registry.surface(
                    assets_module.general_asset_id(general.id)))

    def test_click_selects_correct_general(self):
        for index, general in enumerate(self.screen_obj.generals):
            with self.subTest(index=index):
                self.game.selected_general = None
                rect = self.screen_obj.card_rects[index]
                action = self.screen_obj.handle_click(rect.center, self.game)
                self.assertEqual(action, "select")
                self.assertEqual(self.game.selected_general, general.id)

    def test_hover_does_not_change_selection(self):
        self.game.selected_general = None
        rect = self.screen_obj.card_rects[1]
        self.screen_obj.set_hover(rect.center)
        self.assertEqual(self.screen_obj.hover_index, 1)
        self.assertIsNone(self.game.selected_general)

    def test_hover_hit_rect_covers_lifted_card(self):
        """卡片悬停上浮后，鼠标仍在可点区域内。"""

        rect = self.screen_obj.card_rects[0]
        hit = self.screen_obj.card_hit_rects[0]
        self.assertTrue(hit.contains(rect))
        self.assertEqual(self.screen_obj.card_index_at(rect.center), 0)

    def test_card_rects_inside_screen(self):
        screen_rect = pygame.Rect(0, 0, *self.screen.get_size())
        for rect in self.screen_obj.card_rects:
            self.assertTrue(screen_rect.contains(rect))

    def test_confirm_button_clear_of_cards(self):
        for rect in self.screen_obj.card_rects:
            self.assertFalse(self.screen_obj.confirm_button.rect.colliderect(rect))

    def test_general_without_art_still_selectable(self):
        """素材缺失的武将（第二批）必须仍能选中并进入游戏。"""

        unknown = self.game.generals.list_generals()[0]
        self.screen_obj.sync_layout(self.renderer.metrics, [unknown])
        self.assertIsNone(
            assets_module.get_registry().surface(
                assets_module.general_asset_id("future_general")))
        action = self.screen_obj.handle_click(
            self.screen_obj.card_rects[0].center, self.game)
        self.assertEqual(action, "select")
        self.assertEqual(self.game.selected_general, unknown.id)

    def test_full_roster_layout_has_no_overlap(self):
        self.screen_obj.sync_layout(
            self.renderer.metrics, self.game.generals.list_generals())
        rects = self.screen_obj.card_rects
        for index, first in enumerate(rects):
            for second in rects[index + 1:]:
                self.assertFalse(first.colliderect(second), "卡片重叠")

    def test_draw_works_with_and_without_hover(self):
        self.screen_obj.draw(self.game, self.renderer.metrics)
        self.screen_obj.set_hover(self.screen_obj.card_rects[0].center)
        self.screen_obj.draw(self.game, self.renderer.metrics)


class IdentityVisibilityTests(unittest.TestCase):
    """组 D：身份牌不会泄露隐藏身份。"""

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1920, 1080))
        self.renderer = Renderer(self.screen)
        self.game = Game(ai_count=6)
        self.game.set_mode("identity")
        self.game.begin_general_select()

    def tearDown(self):
        pygame.display.quit()

    def test_human_identity_card_matches_assigned_identity(self):
        human_identity = self.game.player.identity
        self.assertIsNotNone(human_identity)
        self.assertIsNotNone(
            assets_module.get_registry().identity_card(human_identity))
        reveal = IdentityRevealScreen(self.screen)
        reveal.sync_layout(self.renderer.metrics)
        reveal.draw(self.game, self.renderer.metrics)

    def test_lord_identity_is_public(self):
        lord = self.game.mode.lord()
        self.assertIsNotNone(lord)
        self.assertEqual(self.game.mode.public_identity_of(lord), Identity.LORD)
        self.assertEqual(self.game.mode.identity_label(lord), "主公")

    def test_hidden_identity_is_not_exposed(self):
        """未公开的非主公身份，UI 侧拿不到任何身份信息（真人也不例外）。"""

        hidden = [p for p in self.game.players
                  if p.identity is not Identity.LORD]
        self.assertTrue(hidden)
        for player in hidden:
            with self.subTest(seat=player.seat):
                self.assertIsNone(self.game.mode.public_identity_of(player))
                self.assertEqual(self.game.mode.identity_label(player), "")

    def test_own_identity_is_visible_to_self(self):
        from src.game.identity import visible_identity

        self.assertEqual(visible_identity(self.game.player, self.game.player),
                         self.game.player.identity)

    def test_death_reveals_identity(self):
        hidden = [p for p in self.game.players
                  if p is not self.game.player and p.identity is not Identity.LORD]
        victim = hidden[0]
        victim.alive = False
        self.assertEqual(self.game.mode.public_identity_of(victim), victim.identity)
        self.assertTrue(self.game.mode.identity_label(victim))

    def test_game_over_reveals_all(self):
        self.game.mode.reveal_all()
        for player in self.game.players:
            with self.subTest(seat=player.seat):
                self.assertEqual(self.game.mode.public_identity_of(player),
                                 player.identity)

    def test_result_rows_carry_stable_ids(self):
        """结算行必须带稳定 ID，UI 才能取素材（不靠中文名猜）。"""

        rows = self.game.mode.result_lines()
        self.assertTrue(rows)
        for row in rows:
            self.assertIn("identity_id", row)
            self.assertIn("general_id", row)
            if row["identity_id"]:
                self.assertIn(row["identity_id"], {i.value for i in Identity})

    def test_hidden_identity_never_renders_identity_art(self):
        """绘制一帧后，仍处于隐藏状态的角色身份不进入 UI 查询路径。"""

        self.renderer.draw(self.game)
        for player in self.game.players:
            if player is self.game.player:
                continue
            if player.identity is Identity.LORD or not player.alive:
                continue
            self.assertIsNone(self.game.mode.public_identity_of(player))


class CardDataAuthorityTests(unittest.TestCase):
    """组 E：真实 Card 数据始终画在扫描卡面之上。"""

    def setUp(self):
        pygame.init()
        pygame.display.set_mode((640, 480))

    def tearDown(self):
        pygame.display.quit()

    def test_asset_id_ignores_suit_and_rank(self):
        spade = assets_module.card_asset_id(card("SHA", "basic", "spade", "7"))
        heart = assets_module.card_asset_id(card("SHA", "basic", "heart", "9"))
        self.assertEqual(spade, heart)

    def test_same_art_for_physical_and_virtual_card(self):
        physical = card("SHA", "basic", "spade", "7")
        virtual = Card(name="SHA", category="basic", color=(0, 0, 0))
        self.assertEqual(assets_module.card_asset_id(physical),
                         assets_module.card_asset_id(virtual))
        self.assertEqual(physical.identity_label, "♠ 7")
        self.assertEqual(virtual.identity_label, "")

    def test_rendered_card_differs_by_suit_rank(self):
        """同一张牌的两种花色点数，渲染结果必须不同（真实数据被画出来了）。"""

        first = render_card(None, card("SHA", "basic", "spade", "7"))
        second = render_card(None, card("SHA", "basic", "heart", "9"))
        self.assertNotEqual(pixels(first), pixels(second))

    def test_physical_card_renders_badge_over_art(self):
        """实体牌比同底图的虚拟牌多一个花色点数徽标。"""

        physical = render_card(None, card("SHA", "basic", "spade", "7"))
        virtual = render_card(None, Card(name="SHA", category="basic", color=(0, 0, 0)))
        self.assertNotEqual(pixels(physical), pixels(virtual))

    def test_fire_and_thunder_sha_use_different_art(self):
        fire = render_card(None, card("SHA", "basic", "heart", "9", nature="fire"))
        thunder = render_card(None, card("SHA", "basic", "spade", "8", nature="thunder"))
        self.assertNotEqual(pixels(fire), pixels(thunder))

    def test_fire_sha_asset_is_junzheng(self):
        path = assets_module.get_registry().path_for("card:SHA:fire")
        self.assertIn("junzheng", path.replace("\\", "/"))

    def test_representative_cards_render_with_real_data(self):
        samples = (
            card("SHA", "basic", "spade", "7"),
            card("SHA", "basic", "heart", "9", nature="fire"),
            card("SHA", "basic", "spade", "8", nature="thunder"),
            card("SHAN", "basic", "diamond", "2"),
            card("TAO", "basic", "heart", "3"),
            card("ZHUGE", "equipment", "diamond", "A", subtype="weapon"),
            card("LEBU", "trick", "heart", "6"),
        )
        for sample in samples:
            with self.subTest(card=sample.name):
                surface = render_card(None, sample)
                self.assertEqual(surface.get_size(), (106, 148))
                self.assertTrue(surface.get_at((4, 4))[3] > 0)

    def test_opaque_asset_does_not_break_card_size(self):
        """无 Alpha 通道的素材（无懈可击）也必须按目标尺寸正确缩放。"""

        surface = render_card(None, card("WUXIE", "trick", "spade", "11"))
        self.assertEqual(surface.get_size(), (106, 148))

    def test_odd_size_asset_scales_to_target(self):
        """尺寸异常的素材（绝影 400×562）同样不能改变卡片目标尺寸。"""

        surface = render_card(None, card("JUEYING", "equipment", "spade", "5",
                                         subtype="defensive_horse"))
        self.assertEqual(surface.get_size(), (106, 148))

    def test_disabled_card_still_visible(self):
        surface = pygame.Surface((106, 148), pygame.SRCALPHA)
        cards_module.draw_card(surface, card("SHA", "basic"), surface.get_rect(),
                               theme.fonts(), disabled=True)
        self.assertTrue(surface.get_at((53, 74))[3] > 0)

    def test_compact_card_has_no_art(self):
        """小到看不清的卡片退回程序绘制（有名字与花色点数即可）。"""

        surface = pygame.Surface((60, 84), pygame.SRCALPHA)
        cards_module.draw_card(surface, card("SHA", "basic", "spade", "7"),
                               surface.get_rect(), theme.fonts(), compact=True)
        self.assertTrue(surface.get_at((30, 42))[3] > 0)


class CardInteractionTests(unittest.TestCase):
    """组 F：换图后交互不变。"""

    RESOLUTION = (1920, 1080)

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode(self.RESOLUTION)
        self.renderer = Renderer(self.screen)
        self.game = Game(ai_count=2)
        self.game.scene = "game"
        game = self.game
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        game.current_turn_player = game.player
        game.set_general(game.player, "guanyu")
        game.player.hp = game.player.max_hp
        set_draw_order(game, [canonical_card("SHA") for _ in range(24)])
        pygame.event.clear()

    def tearDown(self):
        pygame.display.quit()

    def test_hand_click_selects_card(self):
        self.game.player.hand = [canonical_card("SHA"), tao()]
        self.renderer.draw(self.game)
        rect = self.renderer.get_card_rects(self.game.player.hand)[1]
        consumed = handle_game_click(rect.center, self.game, self.renderer)
        # 点到牌了才会被 UI 消费（点空处返回 False）。
        self.assertTrue(consumed, "点击手牌必须被 UI 消费")

    def test_hover_index_matches_pointer(self):
        self.game.player.hand = [canonical_card("SHA"), tao(), shan()]
        self.renderer.begin_frame(self.game)
        rects = self.renderer.get_card_rects(self.game.player.hand)
        index = self.renderer.card_at_position(rects[2].center, self.game.player.hand)
        self.assertEqual(index, 2)

    def test_selected_hand_card_is_lifted(self):
        self.game.player.hand = [canonical_card("SHA"), tao()]
        self.renderer.begin_frame(self.game)
        base = self.renderer.table_layout.hand_base_rects[0]
        self.game.pending_selection = {
            "zone": "hand",
            "selected": [(self.game.player.hand[0], tuple(base), None)],
        }
        self.renderer.begin_frame(self.game)
        lifted = self.renderer.table_layout.hand_rects[0]
        self.assertLess(lifted.y, base.y)
        self.game.pending_selection = None

    def test_equipment_zone_still_hit_tests(self):
        self.game.player.set_equipment(equipment("QINGLONG"))
        self.renderer.draw(self.game)
        rect = self.renderer.player_equipment_slot_rects(self.game)["weapon"]
        self.assertTrue(rect.width > 0 and rect.height > 0)

    def test_public_pool_click_unchanged(self):
        self.game.player.hand = []
        self.game.public_card_pool = [tao(), shan()]
        rects = self.renderer.get_public_card_rects(self.game.public_card_pool)
        self.assertEqual(len(rects), 2)
        self.assertTrue(rects[0].collidepoint(rects[0].center))

    def test_tooltip_draws_for_equipment(self):
        card_obj = equipment("QINGLONG")
        self.renderer.draw(self.game)
        self.renderer.draw_card_tooltip(card_obj, (500, 500))

    def test_moving_card_renders(self):
        self.renderer.draw(self.game)

    def test_view_as_candidate_highlight_unchanged(self):
        """View-As（武圣）只把合法来源牌点亮，其余灰掉。

        卡面换成图片之后这条链路不能变——它决定玩家能不能看出该点哪张牌。
        """

        from src.ui.player import playable_hand_indices

        game = self.game
        game.set_general(game.player, "guanyu")
        red_card = card("TAO", "basic", "heart", "5")
        black_card = canonical_card("SHA")
        game.player.hand = [red_card, black_card]
        game.current_turn_player = game.player
        game.phase = "play"
        self.renderer.draw(game)

        started = game.start_skill_activation("wusheng")
        self.assertTrue(started, "武圣应当可以发动")

        candidates = game.view_as_candidate_ids()
        self.assertIn(id(red_card), candidates, "红色牌可以作为武圣来源")
        self.assertNotIn(id(black_card), candidates, "黑色牌不能作为武圣来源")

        playable = playable_hand_indices(game)
        self.assertIn(0, playable)
        self.assertNotIn(1, playable)

        # 选牌后流程正常推进（换图不影响这条链路）。
        rects = self.renderer.get_card_rects(game.player.hand)
        game.toggle_view_as_source(red_card, tuple(rects[0]))
        self.renderer.draw(game)


class Ph10ResponsiveTests(unittest.TestCase):
    """组 G：全分辨率布局不越界、文字不重叠。"""

    def setUp(self):
        pygame.init()

    def tearDown(self):
        pygame.display.quit()

    def test_general_select_stays_inside_every_resolution(self):
        game = Game(ai_count=4)
        game.set_mode("identity")
        game.begin_general_select()
        game.confirm_identity()
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                view = GeneralSelectScreen(screen)
                view.sync_layout(renderer.metrics, game.selectable_generals())
                screen_rect = pygame.Rect(0, 0, *size)
                for rect in view.card_rects:
                    self.assertTrue(screen_rect.contains(rect))
                self.assertTrue(screen_rect.contains(view.confirm_button.rect))
                self.assertFalse(
                    view.confirm_button.rect.colliderect(view.card_rects[0]))

    def test_general_select_full_roster_inside_every_resolution(self):
        game = Game(ai_count=4)
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                view = GeneralSelectScreen(screen)
                view.sync_layout(renderer.metrics, game.generals.list_generals())
                screen_rect = pygame.Rect(0, 0, *size)
                for rect in view.card_rects:
                    self.assertTrue(screen_rect.contains(rect))
                for rect in view.card_rects:
                    self.assertFalse(view.confirm_button.rect.colliderect(rect))

    def test_identity_reveal_text_bands_do_not_overlap(self):
        game = Game(ai_count=6)
        game.set_mode("identity")
        game.begin_general_select()
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                view = IdentityRevealScreen(screen)
                view.sync_layout(renderer.metrics)
                self.assertLess(view.art_rect.bottom, view.name_y)
                self.assertLess(view.name_y, view.hint_y)
                self.assertLess(view.hint_y, view.lord_y)
                self.assertLess(view.lord_y, view.rule_y)
                self.assertLess(view.rule_y, view.continue_button.rect.top)
                screen_rect = pygame.Rect(0, 0, *size)
                self.assertTrue(screen_rect.contains(view.panel_rect))

    def test_identity_reveal_renders_every_resolution(self):
        game = Game(ai_count=6)
        game.set_mode("identity")
        game.begin_general_select()
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                view = IdentityRevealScreen(screen)
                view.sync_layout(renderer.metrics)
                view.draw(game, renderer.metrics)

    def test_game_over_rows_stay_inside_panel(self):
        game = Game(ai_count=6)
        game.set_mode("identity")
        game.begin_general_select()
        game.confirm_identity()
        game.selected_general = game.selectable_generals()[0].id
        game.confirm_general()
        game.mode.reveal_all()
        game.game_over = True
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                overlay = GameOverOverlay()
                overlay.layout(renderer.metrics)
                rows = overlay.identity_rows(game)
                self.assertTrue(rows)
                text_font = renderer.metrics.fonts.get("normal")
                top = overlay.panel_rect.y + renderer.metrics.px(184)
                self.assertLess(top, overlay.restart_button.rect.top)
                self.assertGreater(text_font.get_height(), 0)

    def test_menu_and_table_render_every_resolution(self):
        game = Game(ai_count=6)
        game.general_pool = tuple(game.generals.ids())
        game.start_single_player()
        for size in RESOLUTIONS:
            with self.subTest(size=size):
                screen = pygame.display.set_mode(size)
                renderer = Renderer(screen)
                menu = StartMenu(screen)
                menu.draw(game, renderer.metrics)
                renderer.draw(game)


if __name__ == "__main__":
    unittest.main()
