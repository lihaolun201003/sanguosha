"""实体牌唯一归属不变量（任务 A）。

检测同一张实体牌是否**同时**属于多个真实牌区。检查器
（``src/game/invariants.py``）默认关闭，本文件全程开启它，因此下面每个用例
都在真实规则路径上顺带跑了一遍检查：

* 正常移动 / 弃牌 / 装备更换 → 必须不误报；
* 转化牌（丈八两张素材、龙胆一张素材）→ 虚拟牌与素材牌不能互相误判；
* 人为制造重复 → 必须失败，且报出实体牌标识、牌名与冲突位置；
* "已移出、尚未移入"与"正在等玩家回答" → 都不算重复。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_card_ownership_invariants``
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from src.game import Game
from src.game.atoms_v2 import (
    DrawCardsAtom,
    EquipCardAtom,
    MoveCardAtom,
    UnequipAtom,
)
from src.game.conversion import VirtualCard
from src.game.engine.pending import PendingRequestType
from src.game.invariants import (
    CardOwnershipError,
    assert_card_ownership,
    card_label,
    find_duplicate_ownership,
    zone_entries,
)
from tests.legacy_helpers import (
    drain_actions,
    equipment,
    make_test_game,
    normal_sha,
    play_player_card,
    shan,
    tao,
)


class ArmedTestCase(unittest.TestCase):
    """所有用例共用的装置：建局时就把检查器打开。"""

    def armed(self, **kwargs):
        game = make_test_game(**kwargs)
        game.assert_card_ownership = True
        return game

    def assert_ok(self, game):
        report = assert_card_ownership(game)
        self.assertTrue(report.ok, report.describe())
        return report


# ==================================================
# 1. 正常移动 / 弃牌 / 装备更换：不许误报
# ==================================================


class NormalMovementTests(ArmedTestCase):

    def test_draw_moves_from_pile_to_hand(self):
        game = self.armed(draw_order=[tao(), shan()])
        before = len(game.deck.draw_pile)

        game.context.apply(DrawCardsAtom(game.player, 2))

        self.assertEqual(len(game.player.hand), 2)
        self.assertEqual(len(game.deck.draw_pile), before - 2)
        self.assert_ok(game)

    def test_playing_a_card_moves_it_hand_to_discard(self):
        game = self.armed(player_hand=[normal_sha()], enemy_hand=[shan()])
        sha = game.player.hand[0]

        play_player_card(game, sha)
        drain_actions(game)

        self.assertNotIn(sha, game.player.hand)
        self.assertTrue(any(card is sha for card in game.deck.discard_pile),
                        "打出的【杀】应当只出现在弃牌堆")
        self.assert_ok(game)

    def test_card_moves_into_every_zone_once(self):
        """逐一走一遍所有真实牌区：每张牌每步都只有一个归属。"""

        game = self.armed(player_hand=[normal_sha(), tao(), shan()])
        hand, judgement, pool = (game.player.hand[0],
                                 game.player.hand[1],
                                 game.player.hand[2])

        # 手牌 → 判定区（【乐不思蜀】的落点）
        game.context.apply(MoveCardAtom(
            hand, source=game.player.hand, destination=game.player.judgement_zone))
        self.assertTrue(any(card is hand for card in game.player.judgement_zone))
        self.assert_ok(game)

        # 手牌 → 公共牌池（【五谷丰登】的落点）
        game.context.apply(MoveCardAtom(
            judgement, source=game.player.hand,
            destination=game.public_card_pool))
        self.assert_ok(game)

        # 手牌 → 武将置牌区（【屯田】的"田"）
        game.context.apply(MoveCardAtom(
            pool, source=game.player.hand,
            destination=game.player.placed_zone("tian")))
        self.assertEqual(game.player.placed_count("tian"), 1)
        self.assert_ok(game)

        # 判定区 / 公共牌池 / 置牌区 → 弃牌堆
        for card, source in ((hand, game.player.judgement_zone),
                             (judgement, game.public_card_pool),
                             (pool, game.player.placed_zone("tian"))):
            game.context.apply(MoveCardAtom(
                card, source=source, destination=game.deck.discard_pile))
        self.assert_ok(game)
        self.assertEqual(game.player.hand, [])

    def test_replacing_equipment_keeps_one_owner_each(self):
        """换装：新装备进槽、旧装备进弃牌堆，中间没有两张牌同槽。"""

        game = self.armed(player_hand=[equipment("QINGLONG")],
                          player_equipment=[equipment("ZHANGBA")])
        old = game.player.get_equipment("weapon")
        new = game.player.hand[0]

        play_player_card(game, new)
        drain_actions(game)

        self.assertIs(game.player.get_equipment("weapon"), new)
        self.assertTrue(any(card is old for card in game.deck.discard_pile),
                        "被换下的旧武器应当进弃牌堆")
        self.assertNotIn(old, [game.player.get_equipment(slot)
                               for slot in game.player.equipment])
        self.assert_ok(game)

    def test_unequipping_into_a_zone_does_not_double_place(self):
        """``UnequipAtom(destination=...)`` 与随后的移动不能各放一次。"""

        game = self.armed(player_equipment=[equipment("ZHANGBA")])
        spear = game.player.get_equipment("weapon")

        game.context.apply(UnequipAtom(game.player, "weapon"))
        self.assert_ok(game)

        game.context.apply(MoveCardAtom(spear, destination=game.deck.discard_pile))
        self.assertEqual(sum(1 for card in game.deck.discard_pile if card is spear), 1)
        self.assert_ok(game)


# ==================================================
# 2. 转化牌：虚拟牌与实体素材牌必须区分
# ==================================================


class ViewAsMaterialTests(ArmedTestCase):

    def test_zhangba_two_materials_are_discarded_once(self):
        """丈八蛇矛：两张素材各进弃牌堆一次，虚拟【杀】不属于任何牌区。"""

        from src.game.skills.mechanics import use_virtual

        game = self.armed(player_hand=[tao(), shan()],
                          enemy_hand=[tao(), tao()])
        materials = list(game.player.hand)

        use_virtual(game, game.player, "SHA",
                    sources=materials, targets=[game.enemy])
        drain_actions(game)

        for material in materials:
            count = sum(1 for card in game.deck.discard_pile if card is material)
            self.assertEqual(count, 1, "素材牌在弃牌堆里出现了 %d 次" % count)
            self.assertNotIn(material, game.player.hand)
        self.assert_ok(game)

    def test_single_material_conversion(self):
        """龙胆 / 武圣：一张素材牌转化出虚拟牌，同样只结算一次。"""

        from src.game.skills.mechanics import use_virtual

        game = self.armed(player_hand=[shan()], enemy_hand=[tao(), tao()])
        material = game.player.hand[0]

        use_virtual(game, game.player, "SHA",
                    sources=[material], targets=[game.enemy])
        drain_actions(game)

        self.assertEqual(
            sum(1 for card in game.deck.discard_pile if card is material), 1)
        self.assert_ok(game)

    def test_virtual_card_is_never_a_zone_member(self):
        """虚拟牌本身不算牌区归属，也不会跟自己的素材牌互相误判。"""

        game = self.armed(player_hand=[tao(), shan()])
        materials = tuple(game.player.hand)
        virtual = VirtualCard(name="SHA", source_cards=materials,
                              skill_id="equipment.zhangba", owner=game.player)
        before = find_duplicate_ownership(game).scanned

        # 桌面展示的正是这张虚拟牌（真实对局里它会登记在 table_cards）。
        game.table_cards.append((virtual, "table_card"))
        report = self.assert_ok(game)

        self.assertEqual(report.scanned, before,
                         "虚拟牌被算成了实体牌，实体牌总数凭空变多")
        scanned_cards = [card for _label, card in zone_entries(game)]
        self.assertNotIn(virtual, scanned_cards,
                         "虚拟牌不该被当成牌区成员")
        # 两张素材是各自独立的实体牌：都要被算到，而且不是重复。
        for material in materials:
            self.assertIn(material, scanned_cards)
        self.assertEqual(len({id(card) for card in scanned_cards}), len(scanned_cards),
                         "同一张实体牌在扫描结果里出现了两次")

    def test_same_object_in_two_zones_is_still_caught_for_materials(self):
        """素材牌本身若真的重复归属，照样要被查出来（不因转化而豁免）。"""

        game = self.armed(player_hand=[tao(), shan()])
        material = game.player.hand[0]
        game.deck.discard_pile.append(material)   # 人为：还在手里又进了弃牌堆

        with self.assertRaises(CardOwnershipError):
            assert_card_ownership(game)


# ==================================================
# 3. 人为制造重复：必须失败并指出冲突位置
# ==================================================


class DuplicateDetectionTests(ArmedTestCase):

    def test_same_card_in_hand_and_discard_pile(self):
        game = self.armed(player_hand=[normal_sha()])
        card = game.player.hand[0]
        game.deck.discard_pile.append(card)

        with self.assertRaises(CardOwnershipError) as caught:
            assert_card_ownership(game)

        text = str(caught.exception)
        self.assertIn(card.id, text, "报错里没有实体牌标识")
        self.assertIn("杀", text, "报错里没有牌名")
        self.assertIn("P0.手牌", text, "报错里没有冲突位置之一")
        self.assertIn("弃牌堆", text, "报错里没有冲突位置之二")

    def test_equipment_slot_conflict_names_the_slot(self):
        game = self.armed(player_equipment=[equipment("ZHANGBA")])
        spear = game.player.get_equipment("weapon")
        game.processing_zone.append(spear)      # 人为：既装备着又在处理区

        with self.assertRaises(CardOwnershipError) as caught:
            assert_card_ownership(game)
        text = str(caught.exception)
        self.assertIn("装备(weapon)", text)
        self.assertIn("处理区", text)

    def test_placed_zone_conflict_is_reported(self):
        game = self.armed(player_hand=[tao()])
        card = game.player.hand[0]
        game.player.place_card("tian", card)    # 人为：手里一张、田里一张

        report = find_duplicate_ownership(game)
        self.assertFalse(report.ok)
        (conflict,) = report.conflicts
        self.assertEqual(conflict.label, card_label(card))
        self.assertEqual(set(conflict.locations),
                         {"P0.手牌", "P0.置牌区(tian)"})

    def test_conflict_identifies_the_right_player(self):
        game = self.armed(player_hand=[normal_sha()])
        card = game.player.hand[0]
        game.enemy.hand.append(card)            # 人为：同一个人手里有两份

        with self.assertRaises(CardOwnershipError) as caught:
            assert_card_ownership(game)
        text = str(caught.exception)
        self.assertIn("P0.手牌", text)
        self.assertIn("P1.手牌", text)

    def test_atom_boundary_raises_as_soon_as_it_happens(self):
        """检查器挂在原子边界上：错误在产生它的那一步就炸，不用等下一帧。"""

        game = self.armed(player_hand=[tao()])
        card = game.player.hand[0]
        # 先让这张牌只属于弃牌堆，此时状态是自洽的。
        game.player.hand.remove(card)
        game.deck.discard_pile.append(card)
        self.assert_ok(game)

        with self.assertRaises(CardOwnershipError):
            # 这个原子只往手牌里加、没从弃牌堆拿走 → 立刻变成两份。
            game.context.apply(MoveCardAtom(card, destination=game.player.hand))

    def test_frame_boundary_catches_legacy_moves(self):
        """遗留的非原子路径（直接改列表）由帧边界兜住。"""

        game = self.armed(player_hand=[tao()])
        card = game.player.hand[0]
        game.deck.discard_pile.append(card)     # 绕过原子，直接写容器

        with self.assertRaises(CardOwnershipError):
            game.update(1 / 60)


# ==================================================
# 4. 边界语义：中间态与等待状态都不算重复
# ==================================================


class BoundarySemanticsTests(ArmedTestCase):

    def test_removed_but_not_yet_placed_is_not_a_conflict(self):
        """装备结算的中间步骤：先移出处理区、再进装备槽。"""

        game = self.armed(player_equipment=[equipment("ZHANGBA")])
        spear = game.player.get_equipment("weapon")

        # 真实顺序的第一步：摘下来，先不放任何地方（EquipEffect/甘露都会这样）。
        game.context.apply(UnequipAtom(game.player, "weapon"))
        self.assertIsNone(game.player.get_equipment("weapon"))
        self.assert_ok(game)          # 暂时无归属 ≠ 重复归属

        # 第二步：落进新位置。
        game.context.apply(EquipCardAtom(game.enemy, spear))
        self.assertIs(game.enemy.get_equipment("weapon"), spear)
        self.assert_ok(game)

    def test_waiting_for_a_player_answer_is_a_legal_stable_state(self):
        """正常等玩家回答时，牌区照样是自洽的——不要求 Pending 清空。"""

        game = self.armed(player_hand=[tao(), shan()])
        request = game.engine.pending.create(
            PendingRequestType.SELECT_CARDS,
            source=game.player, target=game.player,
            prompt="请选择一张牌",
            owner_flow=None,
            min_cards=1, max_cards=1,
            request_context={"candidates": list(game.player.hand)},
        )
        game.game_log.append("等待玩家选择")

        self.assertIs(game.pending_request, request)
        self.assert_ok(game)
        self.assertIs(game.pending_request, request, "检查不得改动请求栈")

        game.engine.pending.clear()

    def test_check_does_not_mutate_anything(self):
        game = self.armed(player_hand=[normal_sha(), tao()],
                          player_equipment=[equipment("ZHANGBA")])

        before = {
            "hand": list(game.player.hand),
            "discard": list(game.deck.discard_pile),
            "draw": list(game.deck.draw_pile),
            "processing": list(game.processing_zone),
            "equipment": dict(game.player.equipment),
        }
        assert_card_ownership(game)

        self.assertEqual(game.player.hand, before["hand"])
        self.assertEqual(game.deck.discard_pile, before["discard"])
        self.assertEqual(game.deck.draw_pile, before["draw"])
        self.assertEqual(game.processing_zone, before["processing"])
        self.assertEqual(game.player.equipment, before["equipment"])


# ==================================================
# 5. 开关：默认关闭，普通对局不受影响
# ==================================================


class SwitchTests(unittest.TestCase):

    def test_default_follows_the_debug_switch(self):
        """新建对局**不冻结**默认值：属性是 ``None``（= 跟随全局开关）。

        Phase 14.1 问题 3 的修复点。旧写法在构造时把开关取值抄进属性，于是
        ``invariants.enable_debug()`` 对已经建好的对局完全无效。现在的判定
        统一走 ``invariants.armed_for``：
        **对局显式设置 > 模块临时强制 > 环境变量**。
        """

        from src.game import invariants

        game = make_test_game(player_hand=[tao()])
        self.assertIsNone(game.assert_card_ownership,
                          "默认值不该被冻结成具体布尔值")
        if invariants._FORCED is not None:
            self.assertEqual(invariants.armed_for(game), invariants._FORCED,
                             "没有显式设置时应当跟随模块开关")
        elif not invariants._ENV_ENABLED:
            self.assertFalse(invariants.armed_for(game),
                             "默认必须关闭，否则普通对局要白跑检查")
        else:
            self.assertTrue(invariants.armed_for(game),
                            "环境变量要求开启时应当开启")

    def test_disabled_game_is_never_disturbed(self):
        """显式关掉之后，场上有重复归属也不会打扰对局。"""

        game = make_test_game(player_hand=[tao()])
        game.assert_card_ownership = False
        card = game.player.hand[0]
        game.deck.discard_pile.append(card)      # 人为制造重复
        game.update(1 / 60)                      # 不开检查：不炸

    def test_enabling_only_affects_that_game(self):
        quiet = make_test_game(player_hand=[tao()])
        quiet.assert_card_ownership = False
        noisy = make_test_game(player_hand=[normal_sha()])
        noisy.assert_card_ownership = True

        card = noisy.player.hand[0]
        noisy.deck.discard_pile.append(card)
        enemy_card = quiet.player.hand[0]
        quiet.deck.discard_pile.append(enemy_card)

        with self.assertRaises(CardOwnershipError):
            noisy.update(1 / 60)
        quiet.update(1 / 60)          # 这一局没开检查，不受影响


# ==================================================
# 6. 长局审计：一整局打下来不许出现重复归属
# ==================================================


class FullBattleAuditTests(unittest.TestCase):

    #: 多种子 × 两种模式：一局太短（有人被集火秒掉），单跑一局覆盖面不够。
    CASES = (("identity", 20240925), ("identity", 7), ("ffa", 13), ("ffa", 404))

    def _play_one(self, mode, seed):
        """打完一整局（本机那一座换成 AI），返回 (game, 状态变更次数)。"""

        from src.game.engine.events import EventType
        from src.player import ControllerType

        game = Game(ai_count=4)
        game.assert_card_ownership = True
        game.set_mode(mode)
        game.rng.seed(seed)
        game.start_local_battle(4)
        # 本机真人那一座必须换成 AI：否则对局会停在"等你出牌"，看着跑了两万
        # 帧，实际只有开局发牌那两条战报——那样的审计等于什么都没验。
        game.player.controller_type = ControllerType.AI
        game.controllers.clear()
        game.ai_pacing = True

        atoms = []

        def count_atom(_context, _event):
            atoms.append(1)

        game.context.events.subscribe(EventType.ATOM_AFTER, count_atom)

        frames = 0
        while frames < 30000 and not game.game_over:
            game.update(1 / 60)
            frames += 1
        return game, len(atoms)

    def test_whole_ai_battles_stay_consistent(self):
        """检查器全程开启，多种子打完数局（含判定、装备、伤害、死亡）。

        每一局都要"真的打完了"：走完胜负条件、有人阵亡、弃牌堆不空。否则这个
        用例会在"对局根本没动"的情况下通过。
        """

        total_atoms = 0
        for mode, seed in self.CASES:
            with self.subTest(mode=mode, seed=seed):
                game, atoms = self._play_one(mode, seed)
                total_atoms += atoms

                report = assert_card_ownership(game)
                self.assertTrue(report.ok, report.describe())
                self.assertGreater(report.scanned, 0)
                self.assertTrue(game.game_over,
                                "%s/seed=%s 没有打完，审计覆盖面不足" % (mode, seed))
                self.assertGreaterEqual(
                    sum(1 for player in game.players if not player.alive), 1,
                    "%s/seed=%s 一个人都没死，不像是打完了" % (mode, seed))
                self.assertGreater(len(game.deck.discard_pile), 3,
                                   "%s/seed=%s 弃牌堆几乎是空的" % (mode, seed))

        # 几局加起来的状态变更规模（每一条都在原子边界被查过一次）。
        self.assertGreater(total_atoms, 100,
                           "合计状态变更太少，审计覆盖面不足")


if __name__ == "__main__":
    unittest.main()
