"""Phase 8 第一批 11 名武将的收官测试。

覆盖本次真正补齐的通用能力（阶段替代 / 选择目标 / 装备区离开事件）与
11 名武将的关键回归：

    注册表 · 奸雄 · 突袭 · 英姿 · 枭姬 · 已有技能回归
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card_catalog import create_development_deck
from src.game.conversion import PLAY_CONTEXT, RESPONSE_CONTEXT
from src.game.engine import (
    ConfirmPendingAction,
    SelectTargetsAction,
    UseCardAction,
)
from src.game.engine.pending import PendingRequestType
from src.game.flows import DamageContext, DamageFlow, TurnFlow
from src.game.rules import TurnPhase
from src.game.skills.modifiers import Modifier, ModifierKind
from src.game.skills.standard.wei import TuxiFlow, tuxi_can_offer, tuxi_targets
from tests.legacy_helpers import (
    canonical_card,
    equipment,
    normal_sha,
    set_draw_order,
    shan,
    tao,
)
from tests.test_engine_v2_phase8_generals import Phase8TestCase, heart, spade

ROSTER = (
    ("zhangfei", "张飞", ("paoxiao",)),
    ("huangyueying", "黄月英", ("jizhi", "qicai")),
    ("xiahoudun", "夏侯惇", ("ganglie",)),
    ("caocao", "曹操", ("jianxiong",)),
    ("simayi", "司马懿", ("fankui", "guicai")),
    ("guojia", "郭嘉", ("tiandu", "yiji")),
    ("zhangliao", "张辽", ("tuxi",)),
    ("guanyu", "关羽", ("wusheng",)),
    ("zhaoyun", "赵云", ("longdan",)),
    ("zhouyu", "周瑜", ("yingzi", "fanjian")),
    ("sunshangxiang", "孙尚香", ("jieyin", "xiaoji")),
)


def trick(name):
    return canonical_card(name)


class FirstRosterTestCase(Phase8TestCase):
    """在通用测试基类上补一个能处理"选择目标"请求的驱动。"""

    def drain(self, game, *, choose_targets=None, choose_cards=None, max_steps=800, dt=1 / 60):
        for _ in range(max_steps):
            if game.busy:
                game.update(dt)
                continue
            request = game.pending_request
            if request is None:
                return True
            if getattr(request.target, "is_human", False):
                self._drive_human(game, request, choose_targets, choose_cards)
                continue
            game.engine.present_or_auto_resolve(request)
        raise AssertionError("drain 未能在 %d 步内收敛" % max_steps)

    def _drive_human(self, game, request, choose_targets, choose_cards):
        if request.request_type is PendingRequestType.SELECT_TARGETS:
            selection = game.pending_target_selection
            if selection is None:
                return
            picked = (
                choose_targets(request) if choose_targets is not None
                else selection["candidates"][: request.max_cards]
            )
            if picked is None:
                game.cancel_target_selection()
                return
            for target in picked:
                game.toggle_target_selection(target)
            if game.pending_target_selection is not None:
                game.confirm_target_selection()
            return
        if request.request_type is PendingRequestType.SELECT_CARDS:
            selection = game.pending_selection
            if selection is None:
                return
            if choose_cards is not None:
                for card in choose_cards(request) or []:
                    if game.pending_selection is None:
                        break
                    self.select_candidate(game, card)
                return
            if game.can_cancel_pending_selection():
                game.cancel_pending_selection()
                return
            candidates = [item[0] for item in selection["candidates"]]
            for card in candidates[: max(1, request.min_cards)]:
                if game.pending_selection is None:
                    break
                self.select_candidate(game, card)
            return
        game.submit_action(ConfirmPendingAction(request.target, request.request_id, False))

    def select_candidate(self, game, card):
        """按 UI 的方式选中一张候选牌（装备候选要带上槽位 key）。"""

        selection = game.pending_selection
        key = None
        for candidate, candidate_key in selection["candidates"]:
            if candidate is card:
                key = candidate_key
                break
        game.select_pending_card(card, (0, 0, 80, 120), key=key)

    def turn_for(self, game, player, *, on_play=None):
        """开一个交互式回合并驱动到稳定状态。"""

        flow = TurnFlow(game.engine, player)
        flow.begin_interactive()
        return flow


# ==================================================
# A. 第一批武将注册
# ==================================================


class FirstRosterRegistrationTests(FirstRosterTestCase):
    def test_all_eleven_generals_are_registered_with_their_skills(self):
        game = self.make_game(3)
        for general_id, name, skills in ROSTER:
            general = game.generals.get(general_id)
            self.assertIsNotNone(general, "缺少武将：" + general_id)
            self.assertEqual(general.name, name)
            self.assertEqual(tuple(general.skill_ids), skills, general_id)
            for skill_id in skills:
                self.assertIsNotNone(
                    game.skill_registry.get(skill_id),
                    "缺少技能实现：" + skill_id,
                )

    def test_every_roster_skill_can_be_bound_to_a_player(self):
        game = self.make_game(3)
        player = game.players[1]
        for general_id, name, skills in ROSTER:
            game.set_general(player, general_id)
            self.assertEqual(game.skills.skill_ids_of(player), skills, name)
        game.clear_general(player)
        self.assertEqual(game.skills.skill_ids_of(player), ())

    def test_roster_skill_definitions_are_self_describing(self):
        game = self.make_game(2)
        seen = {}
        for _general_id, _name, skills in ROSTER:
            for skill_id in skills:
                definition = game.skill_registry.get(skill_id)
                self.assertTrue(definition.name)
                self.assertTrue(definition.description, skill_id)
                seen[skill_id] = definition.name
        self.assertEqual(len(seen), 16, "第一批技能总数应为 16 个")


# ==================================================
# B. 曹操 · 奸雄
# ==================================================


class JianxiongTests(FirstRosterTestCase):
    def _attack(self, game, attacker, victim, card):
        attacker.hand = [card]
        game.current_turn_player = attacker
        game.phase = "play"
        game.submit_action(UseCardAction(attacker, card, [victim]))
        self.drain(game)

    def test_jianxiong_gains_the_plain_sha(self):
        game = self.make_game(3)
        caocao = game.players[1]
        game.set_general(caocao, "caocao")
        caocao.hand = []
        attack = normal_sha()

        self._attack(game, game.player, caocao, attack)

        self.assertEqual(caocao.hp, caocao.max_hp - 1)
        self.assertIn(attack, caocao.hand, "奸雄取得造成伤害的实体牌")
        self.assertNotIn(attack, game.deck.discard_pile, "被取得后不再进弃牌堆")

    def test_jianxiong_never_copies_the_card(self):
        game = self.make_game(3)
        caocao = game.players[1]
        game.set_general(caocao, "caocao")
        caocao.hand = []
        attack = normal_sha()

        self._attack(game, game.player, caocao, attack)

        occurrences = sum(
            1 for card in list(caocao.hand) + list(game.deck.discard_pile)
            if card is attack
        )
        self.assertEqual(occurrences, 1, "同一张实体牌不能出现在两个区域")

    def test_jianxiong_gains_a_fire_sha(self):
        game = self.make_game(3)
        caocao = game.players[1]
        game.set_general(caocao, "caocao")
        caocao.hand = []
        fire = next(
            card for card in create_development_deck()
            if card.name == "SHA" and card.nature == "fire"
        )

        self._attack(game, game.player, caocao, fire)

        self.assertIn(fire, caocao.hand, "火杀同样被奸雄取得")
        self.assertIn(fire, caocao.hand)

    def test_jianxiong_gains_the_source_card_of_a_virtual_sha(self):
        game = self.make_game(3)
        guanyu, caocao = game.players[1], game.players[2]
        game.set_general(guanyu, "guanyu")
        game.set_general(caocao, "caocao")
        red = heart("K")
        guanyu.hand = [red]
        caocao.hand = []
        game.current_turn_player = guanyu
        game.phase = "play"

        virtual = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)[0][1]
        game.submit_action(UseCardAction(guanyu, virtual, [caocao]))
        self.drain(game)

        self.assertIn(red, caocao.hand, "武圣的虚拟杀要还原成原始实体牌")
        self.assertNotIn(virtual, caocao.hand, "不能把虚拟牌本身塞给曹操")

    def test_jianxiong_does_not_trigger_without_damage(self):
        game = self.make_game(3)
        caocao = game.players[1]
        game.set_general(caocao, "caocao")
        caocao.hand = []
        attack = normal_sha()
        game.deck.discard_pile.append(attack)

        # 0 点伤害：条件不满足，技能不该动这张牌。
        DamageFlow(game.engine, DamageContext(game.player, caocao, 0, card=attack)).start()
        self.drain(game)

        self.assertNotIn(attack, caocao.hand)
        self.assertIn(attack, game.deck.discard_pile)

    def test_jianxiong_skips_a_card_that_already_left_the_legal_zones(self):
        game = self.make_game(3)
        caocao = game.players[1]
        game.set_general(caocao, "caocao")
        caocao.hand = []
        attack = normal_sha()

        # 伤害牌已经不在了（例如被别的技能拿走）：不能凭空取得。
        DamageFlow(game.engine, DamageContext(game.player, caocao, 1, card=attack)).start()
        self.drain(game)

        self.assertNotIn(attack, caocao.hand)
        self.assertEqual(game.processing_zone, [])


# ==================================================
# C. 张辽 · 突袭
# ==================================================


class TuxiTests(FirstRosterTestCase):
    def _zhangliao_turn(self, game, hand_size=4):
        zhangliao = game.players[1]
        game.set_general(zhangliao, "zhangliao")
        zhangliao.hand = []
        set_draw_order(game, [tao() for _ in range(hand_size)])
        return zhangliao

    def test_tuxi_is_offered_at_the_draw_phase(self):
        game = self.make_game(3)
        zhangliao = self._zhangliao_turn(game)
        game.players[2].hand = [tao()]
        game.player.hand = [shan()]

        offers = game.skills.phase_offers(zhangliao, TurnPhase.DRAW)
        self.assertEqual([definition.id for _player, definition in offers], ["tuxi"])

    def test_tuxi_targets_exclude_self_dead_and_empty_hands(self):
        game = self.make_game(4)
        zhangliao = self._zhangliao_turn(game)
        with_cards, dead, empty = game.players[2], game.players[3], game.player
        with_cards.hand = [tao()]
        dead.hand = [tao()]
        dead.alive = False
        dead.hp = 0
        empty.hand = []

        targets = tuxi_targets(game, zhangliao)
        self.assertEqual(targets, [with_cards])

    def test_tuxi_is_not_offered_when_nobody_has_cards(self):
        game = self.make_game(3)
        zhangliao = self._zhangliao_turn(game)
        for other in game.players:
            if other is not zhangliao:
                other.hand = []
        # 技能仍在摸牌阶段的候选里，但 can_offer 会把它挡掉。
        self.assertFalse(tuxi_can_offer(game, zhangliao))

        set_draw_order(game, [tao(), tao()])
        flow = self.turn_for(game, zhangliao)
        self.drain(game)

        self.assertFalse(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(zhangliao.hand), 2, "没有目标时照常摸牌")

    def test_ai_tuxi_takes_two_cards_and_skips_the_draw_phase(self):
        game = self.make_game(3)
        zhangliao = self._zhangliao_turn(game)
        first, second = game.players[2], game.player
        first.hand = [tao(), tao()]
        second.hand = [shan()]

        flow = self.turn_for(game, zhangliao)
        self.drain(game)

        self.assertEqual(len(zhangliao.hand), 2, "突袭拿走两张，摸牌阶段整体跳过")
        self.assertTrue(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(first.hand), 1)
        self.assertEqual(len(second.hand), 0)

    def test_human_tuxi_can_choose_a_single_target(self):
        game = self.make_game(3)
        zhangliao = game.player
        game.set_general(zhangliao, "zhangliao")
        zhangliao.max_hp = 4
        zhangliao.hp = 4
        zhangliao.hand = []
        target = game.players[1]
        target.hand = [tao()]
        untouched = game.players[2]
        untouched.hand = [shan()]
        set_draw_order(game, [tao() for _ in range(4)])

        self.turn_for(game, zhangliao)
        # 摸牌阶段的确认请求
        self.assertEqual(game.pending_request.context.get("reason"), "phase_replacement")
        game.submit_action(ConfirmPendingAction(
            zhangliao, game.pending_request.request_id, True))
        self.drain(game, choose_targets=lambda request: [target])

        self.assertEqual(len(zhangliao.hand), 1)
        self.assertEqual(len(target.hand), 0, "被选中的角色失去一张手牌")
        self.assertEqual(len(untouched.hand), 1, "没被选中的角色手牌不动")

    def test_human_can_cancel_tuxi_and_draw_normally(self):
        game = self.make_game(3)
        zhangliao = game.player
        game.set_general(zhangliao, "zhangliao")
        zhangliao.max_hp = 4
        zhangliao.hp = 4
        zhangliao.hand = []
        target = game.players[1]
        target.hand = [tao(), tao()]
        set_draw_order(game, [shan(), shan(), shan()])

        flow = self.turn_for(game, zhangliao)
        game.submit_action(ConfirmPendingAction(
            zhangliao, game.pending_request.request_id, True))
        self.drain(game, choose_targets=lambda request: None)

        self.assertFalse(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(target.hand), 2, "取消后不能拿走别人的牌")
        self.assertEqual(len(zhangliao.hand), 2, "取消后正常摸牌")

    def test_human_can_decline_at_the_confirmation(self):
        game = self.make_game(3)
        zhangliao = game.player
        game.set_general(zhangliao, "zhangliao")
        zhangliao.max_hp = 4
        zhangliao.hp = 4
        zhangliao.hand = []
        game.players[1].hand = [tao()]
        set_draw_order(game, [shan(), shan()])

        flow = self.turn_for(game, zhangliao)
        game.submit_action(ConfirmPendingAction(
            zhangliao, game.pending_request.request_id, False))
        self.drain(game)

        self.assertFalse(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(zhangliao.hand), 2)

    def test_tuxi_is_once_per_turn(self):
        game = self.make_game(3)
        zhangliao = self._zhangliao_turn(game)
        game.players[2].hand = [tao()]
        game.player.hand = [shan()]

        flow = self.turn_for(game, zhangliao)
        self.drain(game)
        self.assertTrue(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(zhangliao.skill_state.get("tuxi", "used"), 1)
        self.assertFalse(tuxi_can_offer(game, zhangliao))

    def test_engine_rejects_an_illegal_tuxi_target(self):
        game = self.make_game(3)
        zhangliao = self._zhangliao_turn(game)
        legal = game.players[2]
        legal.hand = [tao()]
        game.player.hand = [shan()]

        flow = TuxiFlow(game.engine, zhangliao)
        flow.start()
        request = game.pending_request
        with self.assertRaises(ValueError):
            game.submit_action(SelectTargetsAction(zhangliao, request.request_id, [zhangliao]))
        with self.assertRaises(ValueError):
            game.submit_action(SelectTargetsAction(
                zhangliao, request.request_id, [legal, legal]))


# ==================================================
# D. 周瑜 · 英姿
# ==================================================


class YingziTests(FirstRosterTestCase):
    def test_yingzi_adds_one_draw_through_the_modifier(self):
        game = self.make_game(3)
        zhouyu = game.players[1]
        other = game.players[2]
        self.assertEqual(game.draw_count(other), 2)

        game.set_general(zhouyu, "zhouyu")
        self.assertEqual(game.draw_count(zhouyu), 3, "英姿 = DRAW_COUNT +1")

        game.clear_general(zhouyu)
        self.assertEqual(game.draw_count(zhouyu), 2, "技能解绑后修正消失")

    def test_draw_count_modifier_works_for_any_owner(self):
        game = self.make_game(3)
        target = game.players[2]
        modifier = Modifier(kind=ModifierKind.DRAW_COUNT, value=2, owner=target, roles=("player",))
        game.modifiers.register(modifier)
        self.assertEqual(game.draw_count(target), 4)
        game.modifiers.unregister(modifier)
        self.assertEqual(game.draw_count(target), 2)

    def test_draw_count_modifier_and_phase_replacement_combine(self):
        game = self.make_game(3)
        zhangliao = game.players[1]
        game.set_general(zhangliao, "zhangliao")
        # 用通用 modifier 模拟第二个摸牌加成技能（不真的给张辽英姿）。
        game.modifiers.register(Modifier(
            kind=ModifierKind.DRAW_COUNT, value=1, owner=zhangliao, roles=("player",)))
        zhangliao.hand = []
        game.players[2].hand = [tao(), tao()]
        game.player.hand = [shan()]
        set_draw_order(game, [shan(), shan(), shan(), shan()])

        self.assertEqual(game.draw_count(zhangliao), 3, "修正与阶段替换互不覆盖")

        flow = self.turn_for(game, zhangliao)
        self.drain(game)

        self.assertTrue(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(zhangliao.hand), 2, "突袭取两张，计划摸牌数被整体放弃")
        self.assertEqual(game.draw_count(zhangliao), 3, "回合结束后修正依然在")

    def test_cancelled_tuxi_still_draws_the_modified_amount(self):
        game = self.make_game(3)
        zhangliao = game.player
        game.set_general(zhangliao, "zhangliao")
        zhangliao.max_hp = 4
        zhangliao.hp = 4
        game.modifiers.register(Modifier(
            kind=ModifierKind.DRAW_COUNT, value=1, owner=zhangliao, roles=("player",)))
        zhangliao.hand = []
        game.players[1].hand = [tao()]
        set_draw_order(game, [shan(), shan(), shan()])

        flow = self.turn_for(game, zhangliao)
        game.submit_action(ConfirmPendingAction(
            zhangliao, game.pending_request.request_id, False))
        self.drain(game)

        self.assertFalse(flow.phase_control.is_skipped(TurnPhase.DRAW))
        self.assertEqual(len(zhangliao.hand), 3, "取消突袭 → 按修正后的 3 张摸牌")


# ==================================================
# E. 孙尚香 · 枭姬
# ==================================================


class XiaojiTests(FirstRosterTestCase):
    def _sun(self, game, index=1):
        sun = game.players[index]
        game.set_general(sun, "sunshangxiang")
        sun.hand = []
        return sun

    def test_xiaoji_triggers_when_an_equipment_is_replaced(self):
        game = self.make_game(3)
        sun = self._sun(game)
        old = equipment("BAGUA")
        sun.set_equipment(old)
        set_draw_order(game, [tao(), tao()])

        game.remove_equipment_with_effects(sun, "armor")
        self.drain(game)

        self.assertEqual(len(sun.hand), 2, "旧装备离开装备区 → 枭姬摸两张")
        self.assertNotIn(old, sun.equipment.values())

    def test_xiaoji_triggers_when_guohe_discards_the_armor(self):
        game = self.make_game(3)
        sun = self._sun(game)
        armor = equipment("BAGUA")
        sun.set_equipment(armor)
        set_draw_order(game, [tao(), tao()])
        guohe = trick("GUOHE")
        game.player.hand = [guohe]
        game.current_turn_player = game.player
        game.phase = "play"

        game.submit_action(UseCardAction(game.player, guohe, [sun]))
        self.drain(game, choose_cards=lambda request: [armor])

        self.assertEqual(len(sun.hand), 2, "被过河拆桥拆掉装备也要触发枭姬")
        self.assertIn(armor, game.deck.discard_pile)

    def test_xiaoji_triggers_when_shunshou_takes_the_armor(self):
        game = self.make_game(3)
        sun = self._sun(game)
        armor = equipment("BAGUA")
        sun.set_equipment(armor)
        set_draw_order(game, [tao(), tao()])
        shunshou = trick("SHUNSHOU")
        game.player.hand = [shunshou]
        game.current_turn_player = game.player
        game.phase = "play"

        game.submit_action(UseCardAction(game.player, shunshou, [sun]))
        self.drain(game, choose_cards=lambda request: [armor])

        self.assertEqual(len(sun.hand), 2, "装备被顺走同样触发枭姬")
        self.assertIn(armor, game.player.hand)

    def test_xiaoji_triggers_when_fankui_takes_the_armor(self):
        game = self.make_game(3)
        sun, simayi = game.players[1], game.players[2]
        game.set_general(sun, "sunshangxiang")
        game.set_general(simayi, "simayi")
        armor = equipment("BAGUA")
        sun.set_equipment(armor)
        sun.hand = []
        set_draw_order(game, [tao(), tao()])

        DamageFlow(game.engine, DamageContext(sun, simayi, 1, card=normal_sha())).start()
        self.drain(game)

        self.assertIn(armor, simayi.hand, "反馈拿走装备")
        self.assertEqual(len(sun.hand), 2, "失去装备触发枭姬")

    def test_xiaoji_ignores_cards_entering_the_equipment_zone(self):
        game = self.make_game(3)
        sun = self._sun(game)
        armor = equipment("BAGUA")
        sun.hand = [armor]
        set_draw_order(game, [tao(), tao()])
        game.current_turn_player = sun
        game.phase = "play"

        game.submit_action(UseCardAction(sun, armor, []))
        self.drain(game)

        self.assertEqual(len(sun.hand), 0, "装备进入装备区不是失去装备")

    def test_xiaoji_ignores_non_equipment_card_movement(self):
        game = self.make_game(3)
        sun = self._sun(game)
        armor = equipment("BAGUA")
        sun.set_equipment(armor)
        held = tao()
        sun.hand = [held]
        set_draw_order(game, [tao(), tao()])

        game.deck.discard_pile.append(sun.hand.pop())
        self.drain(game)

        self.assertEqual(len(sun.hand), 0, "手牌离开不触发枭姬")
        self.assertIn(held, game.deck.discard_pile)

    def test_xiaoji_triggers_exactly_once_per_lost_equipment(self):
        game = self.make_game(3)
        sun = self._sun(game)
        armor = equipment("BAGUA")
        sun.set_equipment(armor)
        set_draw_order(game, [tao(), tao(), tao(), tao()])

        game.remove_equipment_with_effects(sun, "armor")
        self.drain(game)

        self.assertEqual(len(sun.hand), 2, "同一件装备离开只触发一次（不是 4 张）")

    def test_xiaoji_does_not_trigger_for_a_dead_owner(self):
        game = self.make_game(3)
        sun = self._sun(game)
        sun.set_equipment(equipment("BAGUA"))
        set_draw_order(game, [tao(), tao()])

        game.engine.run_death(sun)
        self.drain(game)

        self.assertEqual(len(sun.hand), 0, "阵亡清理装备不算失去装备")


# ==================================================
# F. 已有技能回归
# ==================================================


class ExistingSkillRegressionTests(FirstRosterTestCase):
    def test_paoxiao_removes_the_sha_limit(self):
        game = self.make_game(3)
        zhangfei = game.players[1]
        game.set_general(zhangfei, "zhangfei")
        other = game.players[2]
        other.hp = 4
        zhangfei.hand = [normal_sha(), normal_sha()]
        game.current_turn_player = zhangfei
        game.phase = "play"

        game.submit_action(UseCardAction(zhangfei, zhangfei.hand[0], [other]))
        self.drain(game)
        game.submit_action(UseCardAction(zhangfei, zhangfei.hand[0], [other]))
        self.drain(game)

        self.assertEqual(other.hp, 2, "咆哮后可以连续出两张杀")

    def test_jizhi_draws_on_a_trick(self):
        game = self.make_game(3)
        yueying = game.players[1]
        game.set_general(yueying, "huangyueying")
        wuzhong = trick("WUZHONG")
        yueying.hand = [wuzhong]
        set_draw_order(game, [tao(), tao(), tao()])
        game.current_turn_player = yueying
        game.phase = "play"
        game.submit_action(UseCardAction(yueying, wuzhong, [yueying]))
        self.drain(game)

        self.assertEqual(len(yueying.hand), 3, "无中生有 2 张 + 集智 1 张")

    def test_ganglie_punishes_on_a_non_heart_judgement(self):
        game = self.make_game(3)
        xiahoudun = game.players[1]
        game.set_general(xiahoudun, "xiahoudun")
        attacker = game.players[2]
        attacker.hp = attacker.max_hp
        set_draw_order(game, [spade("5"), tao(), tao()])

        DamageFlow(game.engine, DamageContext(attacker, xiahoudun, 1, card=normal_sha())).start()
        self.drain(game)

        self.assertEqual(attacker.hp, attacker.max_hp - 1)

    def test_wusheng_conversion_is_explicit_only(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        red = heart("K")
        guanyu.hand = [red]
        game.current_turn_player = guanyu
        game.phase = "play"

        self.assertEqual(game.skills.view_as_skill_ids(guanyu), ("wusheng",))
        options = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)
        self.assertTrue(options, "武圣提供了转换选项")

        # 实体牌本身没有被改名：只有显式生成的 VirtualCard 才是【杀】。
        self.assertNotEqual(red.name, "SHA")
        virtual = options[0][1]
        self.assertEqual(virtual.name, "SHA")
        self.assertTrue(getattr(virtual, "_virtual", False))

    def test_longdan_converts_in_both_directions(self):
        game = self.make_game(3)
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        attack, dodge = normal_sha(), shan()

        play_options = game.conversions.options_for(game, zhaoyun, dodge, PLAY_CONTEXT)
        response_options = game.conversions.options_for(game, zhaoyun, attack, RESPONSE_CONTEXT)
        self.assertTrue(any(item.name == "SHA" for _label, item in play_options), "闪当杀")
        self.assertTrue(any(item.name == "SHAN" for _label, item in response_options), "杀当闪")

    def test_fanjian_punishes_a_target_without_matching_suit(self):
        game = self.make_game(3)
        zhouyu = game.players[1]
        game.set_general(zhouyu, "zhouyu")
        zhouyu.hp = 3
        zhouyu.max_hp = 3
        zhouyu.hand = [heart("5")]
        target = game.players[2]
        target.hp = 4
        target.hand = [spade("3")]
        game.current_turn_player = zhouyu
        game.phase = "play"

        allowed, reason = game.skills.can_activate(zhouyu, "fanjian")
        self.assertTrue(allowed, reason)
        game.skills.activate(zhouyu, "fanjian", target=target)
        self.drain(game)

        self.assertEqual(target.hp, 3, "没有同花色手牌 → 受到反间伤害")
        self.assertEqual(zhouyu.skill_state.get("fanjian", "used"), 1)
        self.assertFalse(game.skills.can_activate(zhouyu, "fanjian")[0], "每阶段限一次")

    def test_jieyin_heals_both_and_spends_two_cards(self):
        game = self.make_game(3)
        sun = game.players[1]
        game.set_general(sun, "sunshangxiang")
        sun.hp = 1
        sun.max_hp = 3
        sun.hand = [tao(), tao(), tao()]
        partner = game.players[2]
        partner.gender = "male"
        partner.hp = partner.max_hp - 1
        game.current_turn_player = sun
        game.phase = "play"

        allowed, reason = game.skills.can_activate(sun, "jieyin")
        self.assertTrue(allowed, reason)
        game.skills.activate(sun, "jieyin", target=partner, cards=list(sun.hand[:2]))
        self.drain(game)

        self.assertEqual(sun.hp, 2)
        self.assertEqual(partner.hp, partner.max_hp)
        self.assertEqual(len(sun.hand), 1, "支付了两张手牌")

    def test_guicai_and_tiandu_still_work(self):
        game = self.make_game(3)
        simayi, guojia = game.player, game.players[1]
        game.set_general(simayi, "simayi")
        game.set_general(guojia, "guojia")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        from src.game.flows.judge import JudgeFlow

        JudgeFlow(game.engine, guojia, "lebu").start()
        game.select_pending_card(replacement, (0, 0, 80, 120))
        self.drain(game)

        self.assertIn(replacement, guojia.hand, "天妒拿到鬼才替换后的最终判定牌")
        self.assertNotIn(original, guojia.hand)


# ==================================================
# G. 通用扩展点
# ==================================================


class GenericExtensionTests(FirstRosterTestCase):
    def test_equipment_lost_fires_for_every_removal_path(self):
        from src.game.engine import EventType

        game = self.make_game(3)
        seen = []
        game.context.events.subscribe(
            EventType.EQUIPMENT_LOST,
            lambda context, event: seen.append(event.payload["card"]),
        )

        keeper = game.players[1]
        first_armor = equipment("BAGUA")
        keeper.set_equipment(first_armor)
        game.remove_equipment_with_effects(keeper, "armor")
        self.assertEqual(seen, [first_armor])

        second_armor = equipment("RENWANG")
        keeper.set_equipment(second_armor)
        keeper.hand = [third := equipment("TENGJIA")]
        game.current_turn_player = keeper
        game.phase = "play"
        game.submit_action(UseCardAction(keeper, third, []))
        self.drain(game)
        self.assertIn(second_armor, seen, "换装也走同一个事件")

    def test_unequip_atom_refuses_an_empty_slot(self):
        from src.game.atoms_v2 import UnequipAtom

        game = self.make_game(2)
        with self.assertRaises(ValueError):
            game.context.apply(UnequipAtom(game.players[1], "armor"))

    def test_select_targets_action_is_validated_by_the_engine(self):
        game = self.make_game(3)
        zhangliao = game.players[1]
        game.set_general(zhangliao, "zhangliao")
        legal = game.players[2]
        legal.hand = [tao()]

        flow = TuxiFlow(game.engine, zhangliao)
        flow.start()
        request = game.pending_request

        # 数量下限：0 个目标直接提交必须被拒绝
        with self.assertRaises(ValueError):
            game.submit_action(SelectTargetsAction(zhangliao, request.request_id, []))

    def test_phase_replacement_can_hand_off_to_a_child_flow(self):
        game = self.make_game(3)
        zhangliao = self._ready_zhangliao(game)
        flow = self.turn_for(game, zhangliao)
        game.submit_action(ConfirmPendingAction(
            zhangliao, game.pending_request.request_id, True))

        self.assertEqual(flow.status.value, "waiting", "发动后本回合挂起等子流程")
        self.assertEqual(game.pending_request.request_type, PendingRequestType.SELECT_TARGETS)

    def _ready_zhangliao(self, game):
        zhangliao = game.players[1]
        game.set_general(zhangliao, "zhangliao")
        zhangliao.hand = []
        game.players[2].hand = [tao()]
        set_draw_order(game, [tao(), tao()])
        return zhangliao


if __name__ == "__main__":
    unittest.main()
