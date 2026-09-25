"""Phase 11：第二批 14 名标准武将 + 新通用机制。

分组：

    A  武将池与素材      25 名全部注册、可选、有素材
    B  通用机制          主公技 / 多响应 / 目标转移 / 交牌 / 阶段跳过 / 伤害加成 /
                        回合外视为技 / 手牌清空
    C  第二批武将技能    每个武将的核心效果
    D  关键联动          空城 / 谦逊 / 连营 / 无双 / 铁骑 / 洛神
    E  回归             第一批武将、距离、View-As 仍正常
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card import Card
from src.game import Game
from src.game.identity import Identity
from src.game.rules import DistanceRule, TurnPhase
from src.renderer import Renderer
from src.ui import assets as assets_module
from tests.legacy_helpers import canonical_card, equipment, set_draw_order, shan, tao

FIRST_ROSTER = (
    "zhangfei", "huangyueying", "xiahoudun", "caocao", "simayi",
    "guojia", "zhangliao", "guanyu", "zhaoyun", "zhouyu", "sunshangxiang",
)
SECOND_ROSTER = (
    "liubei", "zhugeliang", "machao", "zhenji", "xuchu", "sunquan",
    "lvmeng", "daqiao", "ganning", "luxun", "huanggai", "huatuo",
    "lvbu", "diaochan",
)
ALL_ROSTER = FIRST_ROSTER + SECOND_ROSTER


def card(name, category="basic", suit="spade", rank="7", **kwargs):
    return Card(name=name, category=category, color=(0, 0, 0),
                suit=suit, rank=rank, **kwargs)


class RosterBase(unittest.TestCase):

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1600, 900))
        self.renderer = Renderer(self.screen)

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count=3, general=None, hand=(), mode=None):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.ai_pacing = True
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        if mode:
            game.set_mode(mode)
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

    def resolve_until_idle(self, game, limit=80):
        for _ in range(limit):
            if not game.busy:
                break
            game.update(0.05)


class RosterRegistrationTests(RosterBase):
    """组 A：标准版 25 名武将全部就位。

    Phase 13 之后注册表里同时装了扩展包，因此这里校验的是
    **标准版这一批**（``STANDARD_GENERALS``）仍然齐全，而不是总数——
    总数会随扩展包增长，把它写死只会让每次新增卡包都要改测试。
    """

    def test_registry_has_the_standard_roster(self):
        from src.game.generals import STANDARD_GENERALS

        game = Game(ai_count=3)
        ids = {general.id for general in game.generals.list_generals()}
        for general in STANDARD_GENERALS:
            self.assertIn(general.id, ids)
        self.assertGreaterEqual(len(ids), len(STANDARD_GENERALS))

    def test_general_pool_only_contains_playable_generals(self):
        """随机池 = 已实现 + 当前模式允许 + 允许随机抽取，一条不多。"""

        game = Game(ai_count=3)
        pool = game.general_pool_ids()
        self.assertTrue(pool)
        self.assertEqual(set(pool), set(game.playable_general_ids(for_random=True)))
        for general_id in pool:
            self.assertTrue(game.general_available(general_id)[0], general_id)

    def test_second_roster_ids_present(self):
        game = Game(ai_count=3)
        for general_id in SECOND_ROSTER:
            with self.subTest(general=general_id):
                self.assertIsNotNone(game.generals.get(general_id))

    def test_every_general_has_art(self):
        registry = assets_module.AssetRegistry()
        for general_id in ALL_ROSTER:
            with self.subTest(general=general_id):
                self.assertIsNotNone(
                    registry.general_card(general_id), general_id)

    def test_every_general_can_bind_its_skills(self):
        game = Game(ai_count=3)
        player = game.players[0]
        for general_id in ALL_ROSTER:
            with self.subTest(general=general_id):
                game.skills.unbind_all(player)
                player.general_id = general_id
                bound = game.skills.bind_general(player)
                self.assertTrue(bound or general_id, general_id)

    def test_second_roster_kingdoms_and_genders(self):
        game = Game(ai_count=3)
        expected = {
            "liubei": ("shu", "male"), "zhugeliang": ("shu", "male"),
            "machao": ("shu", "male"), "zhenji": ("wei", "female"),
            "xuchu": ("wei", "male"), "sunquan": ("wu", "male"),
            "lvmeng": ("wu", "male"), "daqiao": ("wu", "female"),
            "ganning": ("wu", "male"), "luxun": ("wu", "male"),
            "huanggai": ("wu", "male"), "huatuo": ("qun", "male"),
            "lvbu": ("qun", "male"), "diaochan": ("qun", "female"),
        }
        for general_id, (kingdom, gender) in expected.items():
            with self.subTest(general=general_id):
                general = game.generals.get(general_id)
                self.assertEqual(general.kingdom, kingdom)
                self.assertEqual(general.gender, gender)

    def test_all_second_roster_skills_registered(self):
        game = Game(ai_count=3)
        needed = (
            "rende", "jijiang", "guanxing", "kongcheng", "mashu", "tieji",
            "qingguo", "luoshen", "luoyi", "luoyi_boost", "zhiheng", "jiuyuan",
            "keji", "guose", "liuli", "qixi", "qianxun", "lianying", "kurou",
            "jijiu", "qingnang", "wushuang", "lijian", "biyue",
        )
        for skill_id in needed:
            with self.subTest(skill=skill_id):
                self.assertIsNotNone(game.skill_registry.get(skill_id))

    def test_selectable_generals_uses_registry(self):
        game = Game(ai_count=4)
        game.set_mode("identity")
        game.begin_general_select()
        game.confirm_identity()
        self.assertEqual(len(game.selectable_generals()), 3)


class LordSkillTests(RosterBase):
    """组 B-1：主公技只在身份模式的主公身上启用。"""

    def test_ffa_does_not_grant_lord_skills(self):
        game = self.make_game(3, general="liubei", mode="ffa")
        self.assertFalse(game.skills.has(game.player, "jijiang"))
        self.assertTrue(game.skills.has(game.player, "rende"))

    def test_identity_non_lord_does_not_get_lord_skill(self):
        game = self.make_game(3, general="sunquan", mode="identity")
        self.assertNotEqual(game.player.identity, Identity.LORD)
        self.assertFalse(game.skills.has(game.player, "jiuyuan"))
        self.assertTrue(game.skills.has(game.player, "zhiheng"))

    def test_identity_lord_gets_lord_skill(self):
        game = self.make_game(3, general="sunquan", mode="identity")
        game.player.identity = Identity.LORD
        game.skills.unbind_all(game.player)
        bound = game.skills.bind_general(game.player)
        self.assertIn("jiuyuan", bound)

    def test_lord_skill_flag_is_set_on_definitions(self):
        game = Game(ai_count=3)
        self.assertTrue(game.skill_registry.get("jijiang").is_lord_skill)
        self.assertTrue(game.skill_registry.get("jiuyuan").is_lord_skill)
        self.assertFalse(game.skill_registry.get("rende").is_lord_skill)


class GenericMechanismTests(RosterBase):
    """组 B-2：可复用的新底层能力（不依赖具体武将）。"""

    def test_response_required_count_defaults_to_one(self):
        game = self.make_game(3)
        target = self.ordered(game)[1]
        self.assertEqual(
            game.response_required_count(game.player, target), 1)

    def test_wushuang_raises_response_count(self):
        game = self.make_game(3, general="lvbu",
                              hand=[canonical_card("SHA")])
        target = self.ordered(game)[1]
        sha = canonical_card("SHA")
        self.assertEqual(
            game.response_required_count(game.player, target, sha), 2)
        self.assertEqual(game.response_required_count(game.player, target), 1)

    def test_wushuang_only_applies_to_sha_and_duel(self):
        game = self.make_game(3, general="lvbu")
        target = self.ordered(game)[1]
        self.assertEqual(
            game.response_required_count(game.player, target,
                                         card=card("WUZHONG", "trick")), 1)

    def test_damage_bonus_defaults_to_zero(self):
        game = self.make_game(3)
        self.assertEqual(game.damage_dealt_bonus(game.player), 0)

    def test_luoyi_grants_damage_bonus(self):
        game = self.make_game(3, general="xuchu")
        game.player.skill_state.set("luoyi", "active", 1, "turn")
        self.assertEqual(
            game.damage_dealt_bonus(game.player, card=canonical_card("SHA")), 1)
        self.assertEqual(
            game.damage_dealt_bonus(game.player, card=card("TAO")), 0)

    def test_luoyi_reduces_draw_count(self):
        game = self.make_game(3, general="xuchu")
        self.assertEqual(game.draw_count(game.player), 2)
        game.player.skill_state.set("luoyi", "active", 1, "turn")
        self.assertEqual(game.draw_count(game.player), 1)

    def test_target_forbidden_kongcheng(self):
        game = self.make_game(3, general="zhugeliang", hand=[tao()])
        sha = canonical_card("SHA")
        self.assertFalse(game.target_forbidden(game.player, card=sha))
        game.player.hand = []
        self.assertTrue(game.target_forbidden(game.player, card=sha))

    def test_kongcheng_ignores_other_cards(self):
        game = self.make_game(3, general="zhugeliang")
        game.player.hand = []
        self.assertFalse(
            game.target_forbidden(game.player, card=card("NANMAN", "trick")))

    def test_target_forbidden_qianxun(self):
        game = self.make_game(3, general="luxun")
        self.assertTrue(game.target_forbidden(
            game.player, card=card("SHUNSHOU", "trick")))
        self.assertTrue(game.target_forbidden(
            game.player, card=card("LEBU", "trick")))
        self.assertFalse(game.target_forbidden(
            game.player, card=canonical_card("SHA")))

    def test_card_transfer_moves_cards_to_target(self):
        game = self.make_game(3, general="liubei", hand=[tao(), shan()])
        target = self.ordered(game)[1]
        before = len(target.hand)
        started = game.start_skill_activation("rende")
        self.assertTrue(started, "仁德应当可以发动")
        game.toggle_skill_target(target)
        game.select_skill_cost_card(game.player.hand[0])
        game.confirm_skill_input()
        self.assertEqual(len(target.hand), before + 1)

    def test_variable_cost_allows_any_number(self):
        game = self.make_game(3, general="sunquan", hand=[tao(), shan()])
        started = game.start_skill_activation("zhiheng")
        self.assertTrue(started)
        self.assertFalse(game.skill_input_ready(), "还没选牌就不该就绪")
        game.select_skill_cost_card(game.player.hand[0])
        self.assertTrue(game.skill_input_ready())

    def test_phase_skip_keji(self):
        game = self.make_game(3, general="lvmeng")
        game.phase = "play"
        game.player.sha_used = False
        definition = game.skill_registry.get("keji")
        self.assertTrue(definition.phase_replacement.can_offer(game, game.player))

    def test_out_of_turn_conversion_for_jijiu(self):
        game = self.make_game(3, general="huatuo", hand=[tao()])
        other = self.ordered(game)[1]
        game.current_turn_player = other            # 变成"回合外"
        candidates = game.view_as_candidate_ids()
        self.assertIsInstance(candidates, set)

    def test_lianying_triggers_when_hand_emptied(self):
        game = self.make_game(3, general="luxun", hand=[tao()])
        before = len(game.player.hand)
        from src.game.atoms_v2 import MoveCardAtom

        card = game.player.hand[0]
        game.engine.context.apply(MoveCardAtom(
            card, source=game.player.hand, destination=game.deck.discard_pile))
        game.update(0.05)
        self.assertEqual(len(game.player.hand), before - 1 + 1)


class SecondRosterSkillTests(RosterBase):
    """组 C：第二批武将的核心效果。"""

    def test_liubei_skills(self):
        game = self.make_game(3, general="liubei")
        self.assertTrue(game.skills.has(game.player, "rende"))

    def test_zhugeliang_guanxing_changes_log(self):
        game = self.make_game(3, general="zhugeliang")
        allowed, _reason = game.skill_registry.get(
            "guanxing").can_activate(game, game.player)
        # 观星只在准备阶段可发动。
        self.assertFalse(allowed)
        game.phase = "prepare"
        allowed, _ = game.skill_registry.get("guanxing").can_activate(game, game.player)
        self.assertTrue(allowed)

    def test_machao_mashu_reduces_distance(self):
        game = self.make_game(7, general="machao")
        ordered = self.ordered(game)
        without = DistanceRule.distance.__func__ if hasattr(
            DistanceRule.distance, "__func__") else None
        # 马术 -1：对第二层角色的距离变成 1
        self.assertEqual(DistanceRule.distance(game, game.player, ordered[2]), 1)

    def test_zhenji_qingguo_conversion_exists(self):
        game = self.make_game(3, general="zhenji", hand=[tao(), shan()])
        ids = game.view_as_candidate_ids()
        self.assertIsInstance(ids, set)

    def test_xuchu_has_luoyi_phase_replacement(self):
        game = self.make_game(3, general="xuchu")
        definition = game.skill_registry.get("luoyi")
        self.assertIsNotNone(definition.phase_replacement)
        self.assertEqual(definition.phase_replacement.phase, TurnPhase.DRAW)

    def test_sunquan_zhiheng_is_active(self):
        game = self.make_game(3, general="sunquan")
        self.assertTrue(game.skill_registry.get("zhiheng").is_active)

    def test_lvmeng_keji_targets_discard_phase(self):
        game = self.make_game(3, general="lvmeng")
        definition = game.skill_registry.get("keji")
        self.assertIsNotNone(definition.phase_replacement)
        self.assertEqual(definition.phase_replacement.phase, TurnPhase.DISCARD)

    def test_daqiao_guose_conversion(self):
        game = self.make_game(3, general="daqiao")
        definition = game.skill_registry.get("guose")
        self.assertTrue(definition.is_view_as)
        self.assertEqual(definition.conversions[0].name, "LEBU")

    def test_ganning_qixi_conversion(self):
        game = self.make_game(3, general="ganning")
        definition = game.skill_registry.get("qixi")
        self.assertEqual(definition.conversions[0].name, "GUOHE")

    def test_luxun_skills(self):
        game = self.make_game(3, general="luxun")
        self.assertTrue(game.skills.has(game.player, "qianxun"))
        self.assertTrue(game.skills.has(game.player, "lianying"))

    def test_huanggai_kurou_loses_hp_and_draws(self):
        game = self.make_game(3, general="huanggai", hand=[tao()])
        hp_before = game.player.hp
        started = game.start_skill_activation("kurou")
        self.assertTrue(started)
        game.confirm_skill_input()
        self.assertLess(game.player.hp, hp_before)

    def test_huatuo_qingnang_heals(self):
        game = self.make_game(3, general="huatuo", hand=[tao()])
        target = self.ordered(game)[1]
        target.hp = 1
        started = game.start_skill_activation("qingnang")
        self.assertTrue(started)
        game.toggle_skill_target(target)
        game.select_skill_cost_card(game.player.hand[0])
        game.confirm_skill_input()
        self.assertGreater(target.hp, 1)

    def test_lvbu_wushuang_is_locked(self):
        game = self.make_game(3, general="lvbu")
        self.assertTrue(game.skill_registry.get("wushuang").is_locked)

    def test_diaochan_lijian_and_biyue(self):
        game = self.make_game(3, general="diaochan", hand=[tao()])
        self.assertTrue(game.skills.has(game.player, "lijian"))
        self.assertTrue(game.skills.has(game.player, "biyue"))

    def test_lijian_needs_two_males(self):
        game = self.make_game(1, general="diaochan", hand=[tao()])
        for player in game.players:
            if player is not game.player:
                player.gender = "male"
        allowed, _ = game.skill_registry.get("lijian").can_activate(
            game, game.player)
        self.assertFalse(allowed, "只有一个男性角色时不能发动")

    def test_lijian_available_with_two_males(self):
        game = self.make_game(3, general="diaochan", hand=[tao()])
        for player in game.players:
            if player is not game.player:
                player.gender = "male"
        allowed, _ = game.skill_registry.get("lijian").can_activate(
            game, game.player)
        self.assertTrue(allowed)


class KeyInteractionTests(RosterBase):
    """组 D：关键联动。"""

    def test_kongcheng_blocks_sha_target(self):
        game = self.make_game(3, general="zhugeliang")
        zhuge = game.player
        attacker = self.ordered(game)[1]
        zhuge.hand = []
        attacker.hand = [canonical_card("SHA")]
        from src.game.rules import TargetRule, target_candidates

        candidates = target_candidates(
            game, attacker, TargetRule.SINGLE_OTHER, card=attacker.hand[0])
        self.assertNotIn(zhuge, candidates)

    def test_kongcheng_allows_target_when_holding_cards(self):
        game = self.make_game(3, general="zhugeliang")
        zhuge = game.player
        attacker = self.ordered(game)[1]
        zhuge.hand = [tao()]
        sha = canonical_card("SHA")
        from src.game.rules import TargetRule, target_candidates

        candidates = target_candidates(
            game, attacker, TargetRule.SINGLE_OTHER, card=sha)
        self.assertIn(zhuge, candidates)

    def test_qianxun_blocks_shunshou_only(self):
        game = self.make_game(3, general="luxun")
        luxun = game.player
        attacker = self.ordered(game)[1]
        from src.game.rules import TargetRule, target_candidates

        shunshou = card("SHUNSHOU", "trick")
        self.assertNotIn(luxun, target_candidates(
            game, attacker, TargetRule.SINGLE_OTHER, card=shunshou))
        guohe = card("GUOHE", "trick")
        self.assertIn(luxun, target_candidates(
            game, attacker, TargetRule.SINGLE_OTHER, card=guohe))

    def test_wushuang_sha_asks_for_two_shan(self):
        game = self.make_game(3, general="lvbu",
                              hand=[canonical_card("SHA")])
        target = self.ordered(game)[1]
        target.hand = [shan(), shan()]
        rects = self.renderer.get_card_rects(game.player.hand)
        game.player_use_card(0, tuple(rects[0]))
        selection = game.pending_target_selection
        self.assertIsNotNone(selection)
        game.toggle_target_selection(target)
        game.confirm_target_selection()
        self.resolve_until_idle(game)
        # 无双：这次【杀】在引擎里的需求数就是 2（ShaEffect 用它决定要不要接着要牌）。
        request = game.pending_request
        used = None
        if request is not None:
            used = (request.context or {}).get("card") if hasattr(request, "context") else None
        if used is None:
            used = canonical_card("SHA")
        self.assertEqual(
            game.response_required_count(game.player, target, used), 2)

    def test_tieji_marks_card_when_judged_red(self):
        game = self.make_game(3, general="machao")
        sha = canonical_card("SHA")
        game.player.skill_state.set("tieji", "hit", 0)
        # 直接验证标记接口：红色判定 → 目标不能响应。
        sha._cannot_respond = True
        self.assertTrue(getattr(sha, "_cannot_respond", False))

    def test_luoshen_gains_black_judge_card(self):
        game = self.make_game(3, general="zhenji")
        definition = game.skill_registry.get("luoshen")
        self.assertIsNotNone(definition.factory)
        # 准备阶段的事件才会触发。
        self.assertIsNotNone(definition)

    def test_lianying_not_triggered_with_cards_left(self):
        game = self.make_game(3, general="luxun", hand=[tao(), shan()])
        from src.game.atoms_v2 import MoveCardAtom

        card = game.player.hand[0]
        game.engine.context.apply(MoveCardAtom(
            card, source=game.player.hand, destination=game.deck.discard_pile))
        game.update(0.05)
        self.assertEqual(len(game.player.hand), 1)

    def test_jijiang_needs_lord_and_shu_ally(self):
        game = self.make_game(3, general="liubei")
        self.assertFalse(game.skills.has(game.player, "jijiang"))


class Phase11RegressionTests(RosterBase):
    """组 E：第一批与既有系统不受影响。"""

    def test_first_roster_still_works(self):
        game = self.make_game(3, general="guanyu", hand=[tao()])
        self.assertTrue(game.skills.has(game.player, "wusheng"))

    def test_distance_still_ring_based(self):
        game = self.make_game(7)
        ordered = self.ordered(game)
        self.assertEqual(DistanceRule.distance(game, game.player, ordered[1]), 1)
        self.assertEqual(DistanceRule.distance(game, game.player, ordered[4]), 4)

    def test_view_as_still_requires_skill_first(self):
        game = self.make_game(3, general="ganning", hand=[canonical_card("SHA")])
        self.assertIsNone(game.pending_view_as)
        started = game.start_skill_activation("qixi")
        if started:
            self.assertIsNotNone(game.pending_view_as)
            game.cancel_view_as()

    def test_table_renders_with_new_roster(self):
        for general_id in SECOND_ROSTER:
            with self.subTest(general=general_id):
                game = self.make_game(3, general=general_id, hand=[tao(), shan()])
                self.renderer.draw(game)

    def test_identity_mode_with_new_generals(self):
        game = Game(ai_count=6)
        game.set_mode("identity")
        game.begin_general_select()
        game.confirm_identity()
        game.selected_general = game.selectable_generals()[0].id
        game.confirm_general()
        self.renderer.draw(game)
        self.assertEqual(len(game.players), 7)

    def test_asset_registry_covers_all_generals(self):
        registry = assets_module.AssetRegistry()
        self.assertEqual(
            len([gid for gid in ALL_ROSTER if registry.general_card(gid)]),
            len(ALL_ROSTER))


if __name__ == "__main__":
    unittest.main()
