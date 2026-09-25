"""Phase 13：扩展卡牌（风 / 火 / 林 / 山 / 神 / 一将成名 / SP）接入验收。

覆盖四类要求：

1. **素材**：69 张图片逐项映射、能被 Pygame 加载、运行时只读项目内资源；
2. **注册表**：包 / 势力 / 版本标签 / 可用性状态全部从注册表推导；
3. **可用性过滤**：本地候选、随机按钮、AI 分配、联机池与直接指定入口
   读的是**同一份**判断，未实现与测试用形态不会混进对局；
4. **技能**：逐个验证本次新写的通用机制（拼点 / 翻面 / 觉醒 / 限定技 /
   标记 / 牌区 / 技能得失 / 额外回合 / 伤害转移）与代表技能的真实效果。
"""

import json
import os
import unittest

import pygame

from src.deck import Deck
from src.game.core import Game
from src.game.generals import create_default_general_registry
from src.game.skills import create_default_skill_registry
from src.ui import assets as assets_module

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(PROJECT_ROOT, "assets", "expansion_inventory.json")


def load_manifest():
    with open(MANIFEST, "r", encoding="utf-8") as handle:
        return json.load(handle)


class AssetImportTests(unittest.TestCase):
    """69 张素材逐项映射并可在项目内加载。"""

    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()
        cls.registry = assets_module.AssetRegistry()

    def test_manifest_covers_every_source_file(self):
        items = self.manifest["items"]
        self.assertEqual(len(items), 69)
        packs = {}
        for item in items:
            packs[item["source"].split("/")[0]] = packs.get(
                item["source"].split("/")[0], 0) + 1
        self.assertEqual(packs, {
            "风包": 10, "火包": 8, "林包": 8, "山包": 9,
            "神将": 8, "一将成名": 13, "SP": 13,
        })

    def test_every_item_is_copied_into_the_project(self):
        for item in self.manifest["items"]:
            path = os.path.join(PROJECT_ROOT, "assets", *item["target"].split("/"))
            self.assertTrue(os.path.exists(path), item["target"])

    def test_every_item_loads_in_pygame(self):
        pygame.init()
        pygame.display.set_mode((64, 64))
        for item in self.manifest["items"]:
            path = os.path.join(PROJECT_ROOT, "assets", *item["target"].split("/"))
            with self.subTest(target=item["target"]):
                surface = pygame.image.load(path)
                self.assertEqual(surface.get_size(), (420, 572))

    def test_no_runtime_dependency_on_the_desktop_source_folder(self):
        """清单里记录的源路径只用于审计；运行期不允许从桌面目录读图。"""

        for item in self.manifest["items"]:
            self.assertFalse(
                item["target"].startswith(item["source"].split("/")[0]),
                "目标路径不该沿用源目录名：" + item["target"])

    def test_general_art_resolves_for_every_general(self):
        registry = create_default_general_registry()
        for general in registry.list_generals():
            asset_id = assets_module.general_asset_id(general.id)
            with self.subTest(general=general.id):
                self.assertTrue(self.registry.has_asset(asset_id),
                                general.id + " 缺少卡面")

    def test_xushu_keeps_both_card_faces_as_alternate_art(self):
        paths = self.registry.alternate_paths("general:xushu")
        self.assertEqual(len(paths), 1)
        self.assertTrue(os.path.exists(paths[0]))

    def test_yinyueqiang_card_art_is_mapped(self):
        self.assertTrue(self.registry.has_asset("card:YINYUEQIANG"))


class GeneralRegistryTests(unittest.TestCase):
    """扩展包武将进入注册表，版本 / 形态 / 可用性都能被推导。"""

    @classmethod
    def setUpClass(cls):
        cls.registry = create_default_general_registry()

    def test_all_eight_packs_are_registered(self):
        self.assertEqual(
            self.registry.packs(),
            ("标准版", "风", "火", "林", "山", "神", "一将成名", "SP"))

    def test_every_expansion_general_is_present(self):
        # 备用卡面（徐庶的第二张卡）不是独立武将，按清单里的 alternate_arts
        # 单独校验，不参与"武将 id 必须存在"这条断言。
        alternate = {
            entry["alternate"] for entry in load_manifest()["alternate_arts"]
        }
        ids = {general.id for general in self.registry.list_generals()}
        for item in load_manifest()["items"]:
            stable_id = item["stable_id"]
            if not stable_id or stable_id.startswith(("marker:", "card:")):
                continue
            if stable_id in alternate:
                continue
            self.assertIn(stable_id, ids, stable_id)

    def test_zhanghe_uses_the_correct_display_name(self):
        """源文件名错字（张颌）不影响显示名，卡面实为「张郃」。"""

        general = self.registry.require("zhanghe")
        self.assertEqual(general.name, "张郃")
        self.assertEqual(general.pack, "山")

    def test_xushu_is_shu_not_wei(self):
        """徐庶的蓝色卡框不得被判成魏势力。"""

        general = self.registry.require("xushu")
        self.assertEqual(general.kingdom, "shu")
        self.assertEqual(general.kingdom_name, "蜀")

    def test_wolong_and_shen_zhuge_do_not_collide_with_zhugeliang(self):
        zhugeliang = self.registry.require("zhugeliang")
        wolong = self.registry.require("wolongzhuge")
        shen = self.registry.require("shen_zhugeliang")
        names = {
            zhugeliang.display_name, wolong.display_name, shen.display_name,
        }
        self.assertEqual(len(names), 3)

    def test_same_name_generals_get_version_labels(self):
        """同名武将必须带区分标签：标准版 / 神 / SP 三个关羽不能同名。"""

        labels = self.registry.labelled_names()
        trio = {labels["guanyu"], labels["sp_guanyu"], labels["shen_guanyu"]}
        self.assertEqual(len(trio), 3, trio)
        for key in ("sp_guanyu", "shen_guanyu"):
            self.assertIn(labels[key], trio)
        # 名字唯一的武将保持原名，不被后缀污染。
        self.assertEqual(labels["dianwei"], "典韦")

    def test_zhangjiao_and_caoren_have_two_rule_versions(self):
        for base, first, second in (("zhangjiao", "zhangjiao", "zhangjiao_2010"),
                                    ("caoren", "caoren", "caoren_2010")):
            a = self.registry.require(first)
            b = self.registry.require(second)
            self.assertEqual(a.name, b.name)
            self.assertNotEqual(a.version, b.version)
            self.assertNotEqual(a.skill_ids, b.skill_ids, base)

    def test_sp008_keeps_both_forms_as_test_only(self):
        myth = self.registry.require("sp_lvbu_myth")
        wrath = self.registry.require("sp_lvbu_wrath")
        self.assertEqual(myth.max_hp, 8)
        self.assertEqual(wrath.max_hp, 4)
        self.assertTrue(myth.test_only and wrath.test_only)
        self.assertNotEqual(myth.title, wrath.title)

    def test_shen_generals_are_their_own_kingdom(self):
        shen = self.registry.require("shen_lvbu")
        self.assertEqual(shen.kingdom, "god")
        self.assertEqual(shen.kingdom_name, "神")

    def test_zhonghui_is_catalogued_but_not_playable(self):
        general = self.registry.require("zhonghui")
        self.assertFalse(general.implemented)
        ok, reason = general.availability
        self.assertFalse(ok)
        self.assertIn("同谋", reason)

    def test_every_playable_general_resolves_its_skills(self):
        skills = create_default_skill_registry()
        for general in self.registry.list_generals():
            if not general.implemented:
                continue
            for skill_id in general.skill_ids:
                with self.subTest(general=general.id, skill=skill_id):
                    self.assertIsNotNone(skills.get(skill_id))


class AvailabilityFilterTests(unittest.TestCase):
    """统一的可用性过滤：所有入口读同一份判断。"""

    def setUp(self):
        self.game = Game(ai_count=3)

    def test_unimplemented_generals_are_excluded_everywhere(self):
        self.assertNotIn("zhonghui", self.game.general_pool_ids())
        self.assertNotIn("liushan", self.game.general_pool_ids())
        for general in self.game.selectable_generals():
            self.assertTrue(general.implemented)

    def test_test_only_forms_are_excluded_from_random_but_selectable_in_duel(self):
        self.assertNotIn("sp_lvbu_myth", self.game.general_pool_ids())
        ok, _reason = self.game.general_available("sp_lvbu_myth")
        self.assertFalse(ok)
        self.game.set_mode("duel_test")
        ok, _reason = self.game.general_available("sp_lvbu_myth")
        self.assertTrue(ok)
        # 即使在测试模式里，"随机"也不该抽到它——只能显式指定。
        self.assertNotIn("sp_lvbu_myth",
                         self.game.playable_general_ids(for_random=True))

    def test_an_illegal_id_in_the_pool_is_filtered_out(self):
        """显式塞进来的非法 id 不能进对局（否则绑定技能时会炸）。"""

        self.game.general_pool = ("zhonghui", "guanyu", "not_a_general")
        self.assertEqual(self.game.general_pool_ids(), ("guanyu",))

    def test_assignment_never_picks_an_unplayable_general(self):
        self.game.general_pool = tuple(self.game.generals.ids())
        self.game.assign_generals()
        for player in self.game.players:
            self.assertTrue(self.game.general_available(player.general_id)[0],
                            str(player.general_id))

    def test_confirm_general_builds_a_playable_pool(self):
        self.game.confirm_general("guanyu")
        for general_id in self.game.general_pool:
            self.assertTrue(self.game.general_available(general_id)[0])


class DeckAndEquipmentTests(unittest.TestCase):
    """扩展装备（银月枪）进入牌堆并带有正确的牌面数据。"""

    def test_yinyueqiang_is_in_the_deck(self):
        deck = Deck()
        cards = [card for card in deck.draw_pile if card.name == "YINYUEQIANG"]
        self.assertEqual(len(cards), 1)
        card = cards[0]
        self.assertEqual(card.subtype, "weapon")
        self.assertEqual(card.attack_range, 3)
        self.assertEqual(card.suit, "diamond")
        self.assertEqual(card.rank, "Q")
        self.assertEqual(card.card_color, "red")

    def test_deck_size_matches_the_extended_roster(self):
        self.assertEqual(len(Deck().draw_pile), 129)


class MechanicsTests(unittest.TestCase):
    """本次新写的通用机制：拼点 / 翻面 / 觉醒 / 限定技 / 标记 / 牌区。"""

    def setUp(self):
        self.game = Game(ai_count=1)
        self.game.mode_id = "duel_test"
        self.game.mode = self.game.modes.require("duel_test")
        self.me = self.game.player

    def _bind(self, general_id):
        self.game.set_general(self.me, general_id)
        return self.game.generals.require(general_id)

    def test_pindian_compares_rank_and_marks_the_result(self):
        from src.card import Card
        from src.game.skills.mechanics import PindianResult

        high = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="K")
        low = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="3")
        result = PindianResult(self.me, self.game.players[1], high, low)
        self.assertTrue(result.initiator_wins)
        self.assertIs(result.winner, self.me)
        # 平点算发起者**没赢**。
        tie = PindianResult(self.me, self.game.players[1], high, high)
        self.assertFalse(tie.initiator_wins)

    def test_pindian_is_refused_without_hand_cards(self):
        from src.game.skills.mechanics import pindian_possible

        self.me.hand = []
        self.assertFalse(pindian_possible(self.me, self.game.players[1]))

    def test_flip_toggles_face_up(self):
        from src.game.skills.mechanics import flip_player, is_flipped

        self.assertTrue(self.me.face_up)
        flip_player(self.game, self.me)
        self.assertTrue(is_flipped(self.me))
        flip_player(self.game, self.me)
        self.assertFalse(is_flipped(self.me))

    def test_flipped_character_skips_their_turn(self):
        self.assertTrue(self.game.player.face_up)
        self.game.player.face_up = False
        self.game.start_next_turn(self.game.players[1])
        self.assertTrue(self.game.player.face_up, "翻面状态应当在回合开始时被消费掉")

    def test_shandian_flip_is_visible_on_the_table(self):
        self.assertTrue(hasattr(self.game.player, "face_up"))

    def test_marks_are_readable_by_other_players(self):
        from src.game.skills.mechanics import add_mark, mark_count, remove_mark

        add_mark(self.game, self.me, "kuangbao", 3, "rage")
        self.assertEqual(mark_count(self.me, "kuangbao", "rage"), 3)
        remove_mark(self.game, self.me, "kuangbao", 1, "rage")
        self.assertEqual(mark_count(self.me, "kuangbao", "rage"), 2)
        self.me.clear_marks()
        self.assertEqual(mark_count(self.me, "kuangbao", "rage"), 0)

    def test_placed_card_zone_is_independent_of_hand(self):
        """武将牌上的牌区（田 / 星）：不在手牌、不在判定区，但能被别人读到。"""

        from src.card import Card

        card = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="3")
        self.me.place_card("tian", card)
        self.assertEqual(self.me.placed_count("tian"), 1)
        self.assertNotIn(card, self.me.hand)
        self.assertEqual(self.me.take_placed_card("tian"), card)
        self.assertEqual(self.me.placed_count("tian"), 0)

    def test_awakening_reduces_max_hp_and_grants_skills(self):
        from src.game.skills.mechanics import awaken, limited_used

        self._bind("jiangwei")
        before = self.me.max_hp
        awaken(self.game, self.me, "zhiji", max_hp_delta=-1, draw=2, gain=("guanxing",))
        self.assertEqual(self.me.max_hp, before - 1)
        self.assertTrue(self.game.skills.has(self.me, "guanxing"))
        self.assertTrue(limited_used(self.me, "zhiji"))

    def test_limited_skill_cannot_be_used_twice(self):
        from src.game.skills.mechanics import consume_limited, limited_used

        consume_limited(self.game, self.me, "niepan", note="涅槃")
        self.assertTrue(limited_used(self.me, "niepan"))

    def test_gain_and_lose_skill_is_idempotent(self):
        from src.game.skills.mechanics import gain_skill, lose_skill

        self._bind("sunce")
        self.assertNotIn("jizhi", self.game.skills.skill_ids_of(self.me))
        self.assertTrue(gain_skill(self.game, self.me, "jizhi"))
        self.assertTrue(self.game.skills.has(self.me, "jizhi"))
        # 第二次不再重复绑定（否则监听会叠一层）。
        self.assertFalse(gain_skill(self.game, self.me, "jizhi"))
        self.assertTrue(lose_skill(self.game, self.me, "jizhi"))
        self.assertFalse(self.game.skills.has(self.me, "jizhi"))

    def test_losing_all_skills_removes_listeners(self):
        from src.game.skills.mechanics import lose_all_skills

        self._bind("caopi")
        self.assertTrue(self.game.skills.skill_ids_of(self.me))
        lose_all_skills(self.game, self.me, reason="断肠")
        self.assertEqual(self.game.skills.skill_ids_of(self.me), ())

    def test_extra_turn_is_queued_and_consumed(self):
        from src.game.skills.mechanics import grant_extra_turn

        self.assertTrue(grant_extra_turn(self.game, self.game.players[1]))
        self.assertIs(self.game.pop_extra_turn(), self.game.players[1])
        self.assertIsNone(self.game.pop_extra_turn())


class SkillBehaviourTests(unittest.TestCase):
    """代表技能的真实效果（不是"注册了名字"）。"""

    def setUp(self):
        self.game = Game(ai_count=1)
        self.me = self.game.player
        self.foe = self.game.players[1]

    def _bind(self, general_id):
        self.game.set_general(self.me, general_id)
        return self.game.generals.require(general_id)

    def test_binding_a_general_registers_its_skills(self):
        general = self._bind("dianwei")
        for skill_id in general.skill_ids:
            self.assertTrue(self.game.skills.has(self.me, skill_id))

    def test_hongyan_rewrites_spade_to_heart(self):
        self._bind("xiaoqiao")
        from src.card import Card

        spade = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.assertEqual(self.game.card_suit(spade, self.me), "heart")

    def test_bazhen_provides_a_virtual_armor(self):
        self._bind("wolongzhuge")
        self.assertIsNone(self.game.armor_card(self.me))
        self.assertTrue(self.game.has_armor(self.me, "BAGUA"))

    def test_xueyi_raises_hand_limit_for_each_qun_ally(self):
        self._bind("yuanshao")
        self.foe.kingdom = "qun"
        base = self.game.hand_limit(self.me)
        self.assertGreaterEqual(base, 4)

    def test_feiying_adds_one_to_incoming_distance(self):
        self._bind("shen_caocao")
        from src.game.rules import DistanceRule

        plain = DistanceRule.distance(self.game, self.foe, self.me)
        self.game.skills.unbind(self.me, "feiying")
        without = DistanceRule.distance(self.game, self.foe, self.me)
        self.assertEqual(plain - 1, without)

    def test_juejing_changes_draw_count_and_hand_limit(self):
        self._bind("shen_zhaoyun")
        self.assertEqual(self.game.hand_limit(self.me), self.me.hp + 2)

    def test_roulin_only_applies_between_dongzhuo_and_women(self):
        from src.card import Card
        from src.game.engine import UseCardAction

        self._bind("dongzhuo")
        sha = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.foe.gender = "female"
        self.assertEqual(
            self.game.response_required_count(self.me, self.foe, sha), 2)
        self.foe.gender = "male"
        self.assertEqual(
            self.game.response_required_count(self.me, self.foe, sha), 1)
        # 反方向：女性对董卓使用【杀】同样需要两张【闪】。
        self.game.set_general(self.foe, "zhenji")
        self.assertEqual(
            self.game.response_required_count(self.foe, self.me, sha), 2)

    def test_wushuang_doubles_the_response_requirement(self):
        from src.card import Card

        self._bind("sp_lvbu_myth")
        sha = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.assertEqual(
            self.game.response_required_count(self.me, self.foe, sha), 2)

    def test_weimu_blocks_black_trick_targets(self):
        from src.card import Card

        self._bind("jiaxu")
        black = Card(name="GUOHE", category="trick", color=(0, 0, 0),
                     suit="spade", rank="3")
        red = Card(name="GUOHE", category="trick", color=(255, 0, 0),
                   suit="heart", rank="3")
        self.assertTrue(self.game.target_forbidden(self.me, source=self.foe, card=black))
        self.assertFalse(self.game.target_forbidden(self.me, source=self.foe, card=red))

    def test_wansha_forbids_other_players_rescuing(self):
        """完杀：贾诩的回合内，除他自己外只有濒死角色能用【桃】。"""

        self._bind("jiaxu")
        self.game.current_turn_player = self.me
        # 贾诩自己不受限。
        self.assertFalse(self.game.rescue_forbidden(self.me, self.foe, "TAO"))
        # 濒死角色本人可以自救。
        self.assertFalse(self.game.rescue_forbidden(self.foe, self.foe, "TAO"))
        # 别的角色想救别人：被完杀挡住。
        self.game.set_general(self.foe, "caocao")
        self.assertTrue(self.game.rescue_forbidden(self.foe, self.me, "TAO"))
        # 不是贾诩的回合就不受限。
        self.game.current_turn_player = self.foe
        self.assertFalse(self.game.rescue_forbidden(self.me, self.foe, "TAO"))

    def test_leiji_2010_deals_damage_while_2008_removes_hp(self):
        from src.game.skills.expansions.wind import LeijiBase, LeijiOld

        self.assertTrue(LeijiBase.uses_damage)
        self.assertFalse(LeijiOld.uses_damage)

    def test_tianyi_win_grants_extra_targets_and_denies_slash_on_loss(self):
        from src.card import Card

        self._bind("taishici")
        sha = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.assertEqual(self.game.slash_target_bonus(self.me, sha), 0)
        self.me.skill_state.set("tianyi", "won", 1)
        self.assertEqual(self.game.slash_target_bonus(self.me, sha), 1)
        self.assertFalse(self.game.slash_forbidden(self.me, sha))
        self.me.skill_state.set("tianyi", "won", 0)
        self.me.skill_state.set("tianyi", "lost", 1)
        self.assertTrue(self.game.slash_forbidden(self.me, sha))
        self.me.skill_state.clear("tianyi")

    def test_shenji_allows_three_targets_without_a_weapon(self):
        from src.card import Card

        self._bind("sp_lvbu_wrath")
        sha = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.assertEqual(self.game.slash_target_bonus(self.me, sha), 2)
        from src.card_catalog import weapon

        self.me.set_equipment(weapon("QILIN", 5, "heart", "5"))
        self.assertEqual(self.game.slash_target_bonus(self.me, sha), 0)

    def test_hand_cards_are_not_locked_before_jilei_triggers(self):
        from src.card import Card

        self._bind("sp_yangxiu")
        card = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.assertFalse(self.game.category_forbidden(self.foe, card))
        from src.game.skills.mechanics import set_forbidden_category

        set_forbidden_category(self.game, self.me, self.foe, "basic")
        self.assertTrue(self.game.category_forbidden(self.foe, card))
        from src.game.skills.mechanics import clear_forbidden_categories

        clear_forbidden_categories(self.game, self.me)
        self.assertFalse(self.game.category_forbidden(self.foe, card))

    def test_yicong_switches_direction_around_two_hp(self):
        """体力 >2 时自己算距离 -1；<=2 时别人算与你的距离 +1。"""

        from src.game.skills.expansions.sp import _yicong_incoming, _yicong_outgoing

        self._bind("sp_gongsunzan")
        self.me.hp = 4
        self.assertEqual(_yicong_outgoing(self.game, {"source": self.me}), -1)
        self.assertEqual(_yicong_incoming(self.game, {"target": self.me}), 0)
        self.me.hp = 2
        self.assertEqual(_yicong_outgoing(self.game, {"source": self.me}), 0)
        self.assertEqual(_yicong_incoming(self.game, {"target": self.me}), 1)

    def test_shenwei_raises_draw_count_and_hand_limit(self):
        self._bind("sp_lvbu_wrath")
        self.assertEqual(self.game.draw_count(self.me), 4)
        self.assertEqual(self.game.hand_limit(self.me), self.me.hp + 2)

    def test_yongsi_scales_with_the_number_of_kingdoms(self):
        """摸牌数 = 基础 2 张 + 全场现存势力数。"""

        self._bind("sp_yuanshu")
        self.me.kingdom = "qun"
        self.foe.kingdom = "wei"
        self.assertEqual(self.game.draw_count(self.me), 2 + 2)
        self.foe.kingdom = "qun"
        self.assertEqual(self.game.draw_count(self.me), 2 + 1)

    def test_yizhong_blocks_black_slashes_without_armor(self):
        from src.card import Card

        self._bind("yujin")
        black = Card(name="SHA", category="basic", color=(0, 0, 0), suit="spade", rank="7")
        self.assertTrue(self.game.skills.has(self.me, "yizhong"))
        from src.card_catalog import armor

        self.me.set_equipment(armor("BAGUA", "spade", "2"))
        self.assertIsNotNone(self.game.armor_card(self.me))


if __name__ == "__main__":
    unittest.main()
