"""Phase 8 tests: judgement replacement, card conversion, generals, skills."""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card_catalog import create_development_deck, create_equipment_cards
from src.game import Game
from src.game.conversion import PLAY_CONTEXT, RESPONSE_CONTEXT, ConversionRegistry, VirtualCard
from src.game.engine import PassPendingAction, RespondCardAction, SelectCardsAction, UseCardAction
from src.game.engine.pending import PendingRequestType
from src.game.flows import DamageContext, DamageFlow, TurnFlow
from src.game.flows.judge import JudgeFlow
from src.game.generals import GeneralDef, GeneralRegistry, create_default_general_registry
from src.game.skills import create_default_skill_registry
from tests.legacy_helpers import canonical_card, normal_sha, set_draw_order, shan, tao


def trick(name):
    return canonical_card(name)


def equipment(name):
    return next(card for card in create_equipment_cards() if card.name == name)


def spade(rank="7"):
    return next(
        card for card in create_development_deck()
        if card.suit == "spade" and card.rank == rank and card.name != "SHANDIAN"
    )


def heart(rank=None):
    for card in create_development_deck():
        if card.suit != "heart":
            continue
        if rank is not None and card.rank != rank:
            continue
        return card
    raise AssertionError("no heart card")


class Phase8TestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()

    def make_game(self, ai_count=2, *, pool=None, hand_size=4):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        # 节奏模式：AI 响应排进动作队列，测试才能观察到等待中的 Pending。
        game.ai_pacing = True
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        game.current_turn_player = game.player
        if pool:
            game.general_pool = tuple(pool)
        return game

    def drain(self, game, *, human_select=None, max_steps=400, dt=1 / 60):
        """驱动引擎直到没有 Pending（测试里的真人默认 Pass / 自动选第一张）。"""

        for _ in range(max_steps):
            if game.busy:
                game.update(dt)
                continue
            request = game.pending_request
            if request is None:
                return True
            if getattr(request.target, "is_human", False):
                if request.request_type is PendingRequestType.SELECT_CARDS:
                    if human_select is not None:
                        chosen = human_select(request)
                        if chosen:
                            game.submit_action(SelectCardsAction(request.target, request.request_id, chosen))
                            continue
                    if request.min_cards == 0:
                        game.submit_action(PassPendingAction(request.target, request.request_id))
                        continue
                    game.submit_action(SelectCardsAction(
                        request.target, request.request_id,
                        list(request.context.get("candidates", ()))[:request.min_cards],
                    ))
                    continue
                if request.request_type is PendingRequestType.RESPOND_CARD:
                    game.submit_action(PassPendingAction(request.target, request.request_id))
                    continue
                from src.game.engine import ConfirmPendingAction

                game.submit_action(ConfirmPendingAction(request.target, request.request_id, False))
                continue
            game.engine.present_or_auto_resolve(request)
        raise AssertionError("drain 未能在 %d 步内收敛" % max_steps)


# ==================================================
# General 注册表
# ==================================================


class GeneralCatalogTests(Phase8TestCase):
    def test_eleven_generals_registered(self):
        generals = create_default_general_registry()
        self.assertGreaterEqual(len(generals), 11)
        for general in generals.list_generals():
            self.assertTrue(general.skill_ids, general.id)

    def test_general_ids_are_unique_and_stable(self):
        generals = create_default_general_registry()
        ids = [general.id for general in generals.list_generals()]
        self.assertEqual(len(set(ids)), len(ids))
        self.assertTrue(all(item.isascii() and item.islower() for item in ids))

    def test_every_skill_id_resolves(self):
        generals = create_default_general_registry()
        skills = create_default_skill_registry()
        for general in generals.list_generals():
            if not general.implemented:
                # 图鉴条目（卡面正文无法核实的武将）被明确禁止开局，
                # 它们不需要技能定义；能开局的武将一个都不能缺。
                continue
            for skill_id in general.skill_ids:
                self.assertIn(skill_id, skills, "%s 的 %s 缺少技能定义" % (general.id, skill_id))

    def test_expected_generals_exist(self):
        generals = create_default_general_registry()
        for general_id in (
            "zhangfei", "huangyueying", "xiahoudun", "caocao", "simayi",
            "guojia", "zhangliao", "guanyu", "zhaoyun", "zhouyu", "sunshangxiang",
        ):
            self.assertIn(general_id, generals)

    def test_gender_and_hp_come_from_the_definition(self):
        game = self.make_game(3)
        game.set_general(game.players[1], "sunshangxiang")
        self.assertEqual(game.players[1].max_hp, 3)
        self.assertEqual(game.players[1].gender, "female")
        self.assertEqual(game.players[1].hp, 3)

        game.set_general(game.players[2], "zhaoyun")
        self.assertEqual(game.players[2].max_hp, 4)
        self.assertEqual(game.players[2].gender, "male")


class GeneralAssignmentTests(Phase8TestCase):
    def test_assignment_is_unique_up_to_pool_size(self):
        game = self.make_game(4)
        game.general_pool = tuple(game.generals.ids())
        assigned = game.assign_generals()
        self.assertEqual(len(set(assigned)), len(assigned))

    def test_human_general_is_not_given_to_ai(self):
        game = self.make_game(4)
        game.general_pool = tuple(game.generals.ids())
        assigned = game.assign_generals(human_general="zhaoyun")
        self.assertEqual(assigned[0], "zhaoyun")
        self.assertNotIn("zhaoyun", assigned[1:])

    def test_assignment_is_reproducible_with_a_fixed_seed(self):
        import random

        first = self.make_game(6)
        first.general_pool = tuple(first.generals.ids())
        first.rng = random.Random(20240919)
        assigned_first = first.assign_generals(human_general="guanyu")

        second = self.make_game(6)
        second.general_pool = tuple(second.generals.ids())
        second.rng = random.Random(20240919)
        assigned_second = second.assign_generals(human_general="guanyu")

        self.assertEqual(assigned_first, assigned_second)

    def test_confirm_general_starts_the_battle(self):
        game = self.make_game(3)
        game.begin_general_select()
        self.assertEqual(game.scene, "general_select")
        game.selected_general = "zhaoyun"
        self.assertTrue(game.confirm_general())
        self.assertEqual(game.scene, "game")
        self.assertEqual(game.players[0].general_id, "zhaoyun")
        self.assertTrue(all(player.general_id for player in game.players))


# ==================================================
# 判定替换
# ==================================================


class JudgeReplacementTests(Phase8TestCase):
    def _judge_with_pool(self, game, owner, reason="lebu"):
        judge = JudgeFlow(game.engine, owner, reason)
        outcome = judge.start()
        return judge, outcome

    def test_judgement_is_synchronous_without_replacers(self):
        game = self.make_game(3)
        set_draw_order(game, [spade("7")])
        judge, outcome = self._judge_with_pool(game, game.players[1])
        self.assertEqual(outcome.status.value, "completed")
        self.assertIsNotNone(outcome.value)

    def test_guicai_replaces_the_judgement_card(self):
        game = self.make_game(3)
        simayi = game.players[1]
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        judge, outcome = self._judge_with_pool(game, simayi, "lebu")
        self.assertEqual(outcome.status.value, "waiting")

        index = simayi.hand.index(replacement)
        game.submit_action(SelectCardsAction(simayi, game.pending_request.request_id, [replacement]))

        self.drain(game)
        self.assertIs(judge.result.card, replacement)
        self.assertIs(judge.result.original_card, original)
        self.assertTrue(judge.result.replaced)

    def test_replaced_cards_move_to_real_zones(self):
        game = self.make_game(3)
        simayi = game.players[1]
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        self._judge_with_pool(game, simayi, "lebu")
        game.submit_action(SelectCardsAction(simayi, game.pending_request.request_id, [replacement]))
        self.drain(game)

        self.assertNotIn(replacement, simayi.hand, "替换牌已离开手牌")
        self.assertIn(original, game.deck.discard_pile, "原判定牌进入弃牌堆")
        self.assertIn(replacement, game.deck.discard_pile, "最终判定牌进入弃牌堆")

    def test_human_can_pass_the_replacement(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        judge, outcome = self._judge_with_pool(game, game.players[1], "lebu")
        self.assertEqual(outcome.status.value, "waiting")
        self.assertIs(game.pending_request.target, simayi)

        game.submit_action(PassPendingAction(simayi, game.pending_request.request_id))
        self.drain(game)

        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.card, original)
        self.assertIn(replacement, simayi.hand, "放弃改判不应消耗手牌")

    def test_ai_guicai_replaces_an_unfavourable_judgement(self):
        game = self.make_game(3)
        simayi = game.players[1]
        game.set_general(simayi, "simayi")
        simayi.hand = [heart("K"), spade("3")]
        set_draw_order(game, [spade("7")])

        judge, outcome = self._judge_with_pool(game, simayi, "lebu")
        self.drain(game)

        self.assertTrue(judge.result.replaced, "AI 应当用红桃替换对自己不利的判定")
        self.assertEqual(judge.result.card.suit, "heart")

    def test_replacer_order_starts_from_the_judged_player(self):
        game = self.make_game(3)
        human, ai = game.player, game.players[1]
        game.set_general(human, "simayi")
        game.set_general(ai, "simayi")

        # 判定角色是 AI：顺序应为「AI → … → 真人」
        order = [player for player, _definition in game.skills.judge_replacers(ai)]
        self.assertEqual(order, [ai, human])

        # 判定角色是真人：顺序从真人开始
        order = [player for player, _definition in game.skills.judge_replacers(human)]
        self.assertEqual(order, [human, ai])

    def test_replacer_list_skips_dead_players(self):
        game = self.make_game(3)
        human, ai = game.player, game.players[1]
        game.set_general(human, "simayi")
        game.set_general(ai, "simayi")
        ai.alive = False
        ai.hp = 0
        order = [player for player, _definition in game.skills.judge_replacers(human)]
        self.assertEqual(order, [human])

    def test_a_second_replacer_can_override_the_first(self):
        game = self.make_game(3)
        human, ai = game.player, game.players[1]
        game.set_general(human, "simayi")
        game.set_general(ai, "simayi")
        # 真人在自己的判定上用黑桃替换（仍然不利），AI（判定者本人）随后改成红桃。
        human.hand = [spade("3")]
        ai.hand = [heart("Q")]
        set_draw_order(game, [spade("7"), heart("2"), heart("3")])

        judge, _outcome = self._judge_with_pool(game, human, "lebu")
        game.submit_action(SelectCardsAction(human, game.pending_request.request_id, [human.hand[0]]))
        self.drain(game)

        history = judge.result.replacement_history
        self.assertEqual(len(history), 1)
        self.assertEqual(judge.result.card.suit, "spade", "只有真人替换时判定牌是黑桃")
        self.assertIn(spade("3").suit, ("spade",))

    def test_tiandu_takes_the_final_replaced_card(self):
        game = self.make_game(3)
        simayi, guojia = game.player, game.players[1]
        game.set_general(simayi, "simayi")
        game.set_general(guojia, "guojia")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original, heart("2")])

        self._judge_with_pool(game, guojia, "lebu")
        game.submit_action(SelectCardsAction(simayi, game.pending_request.request_id, [replacement]))
        self.drain(game)

        self.assertIn(replacement, guojia.hand, "天妒应取得最终生效的判定牌")
        self.assertNotIn(original, guojia.hand)
        self.assertIn(original, game.deck.discard_pile)

    def test_ganglie_judgement_can_be_replaced(self):
        game = self.make_game(3)
        simayi, xiahoudun = game.player, game.players[1]
        game.set_general(xiahoudun, "xiahoudun")
        game.set_general(simayi, "simayi")
        simayi.hand = [heart("K"), heart("Q")]
        attacker = game.players[2]
        attacker.hp = attacker.max_hp
        set_draw_order(game, [spade("5"), heart("2")])

        before = attacker.hp
        DamageFlow(game.engine, DamageContext(attacker, xiahoudun, 1, card=normal_sha())).start()
        # 真人司马懿在改判窗口里打出一张红桃手牌
        self.drain(game, human_select=lambda request: [request.target.hand[0]])

        # 原判定是黑桃（会反伤），鬼才换成红桃后不再反伤
        self.assertEqual(attacker.hp, before, "被改判为红桃后刚烈不反伤")

    def test_shandian_judgement_can_be_replaced(self):
        game = self.make_game(3)
        simayi = game.players[1]
        game.set_general(simayi, "simayi")
        simayi.hand = [heart("K")]
        set_draw_order(game, [spade("2"), heart("2"), heart("5")])

        judge = JudgeFlow(game.engine, simayi, "shandian")
        judge.start()
        self.drain(game)
        self.assertTrue(judge.result.replaced)
        self.assertNotEqual(judge.result.card.suit, "spade")

    def test_no_replacer_leaves_the_judgement_untouched(self):
        game = self.make_game(3)
        original = spade("7")
        set_draw_order(game, [original])
        judge, outcome = self._judge_with_pool(game, game.players[1], "lebu")
        self.assertEqual(outcome.status.value, "completed")
        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.card, original)


# ==================================================
# 卡牌转化
# ==================================================


class CardConversionTests(Phase8TestCase):
    def test_wusheng_converts_a_red_card_in_play(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        red = heart("K")
        guanyu.hand = [red]
        victim = game.players[2]
        victim.hand = []
        game.current_turn_player = guanyu
        game.phase = "play"

        options = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)
        self.assertEqual(len(options), 1)
        virtual = options[0][1]
        self.assertEqual(virtual.name, "SHA")
        self.assertIs(virtual.primary_source, red)

        game.submit_action(UseCardAction(guanyu, virtual, [victim]))
        self.drain(game)
        self.assertEqual(victim.hp, victim.max_hp - 1)
        self.assertIn(red, game.deck.discard_pile, "实体源牌进入弃牌堆")
        self.assertNotIn(red, guanyu.hand)

    def test_wusheng_rejects_black_cards(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        black = spade("7")
        guanyu.hand = [black]
        self.assertEqual(game.conversions.options_for(game, guanyu, black, PLAY_CONTEXT), [])

    def test_longdan_turns_sha_into_shan(self):
        game = self.make_game(3)
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        attack_card = normal_sha()
        zhaoyun.hand = [attack_card]

        options = game.conversions.candidates_for(game, zhaoyun, "SHAN", RESPONSE_CONTEXT)
        self.assertEqual(len(options), 1)
        virtual, conversion = options[0]
        self.assertEqual(virtual.name, "SHAN")
        self.assertEqual(conversion.skill_id, "longdan")

    def test_longdan_turns_shan_into_sha(self):
        game = self.make_game(3)
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        dodge = shan()
        zhaoyun.hand = [dodge]

        options = game.conversions.candidates_for(game, zhaoyun, "SHA", PLAY_CONTEXT)
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0][0].name, "SHA")

    def test_converted_response_works_in_a_pending(self):
        game = self.make_game(3)
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        attack_card = normal_sha()
        zhaoyun.hand = [attack_card]
        attacker = game.player
        attacker.hand = [normal_sha()]

        DamageFlow  # noqa: B018  (保持导入可读性)
        game.submit_action(UseCardAction(attacker, attacker.hand[0], [zhaoyun]))
        self.assertEqual(game.pending_request.target, zhaoyun)
        self.drain(game)

        self.assertEqual(zhaoyun.hp, zhaoyun.max_hp, "龙胆杀当闪抵消了这次杀")
        self.assertIn(attack_card, game.deck.discard_pile)

    def test_virtual_card_keeps_source_information(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        red = heart("7")
        guanyu.hand = [red]
        virtual = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)[0][1]
        self.assertEqual(virtual.suit, red.suit)
        self.assertEqual(virtual.rank, red.rank)
        self.assertEqual(virtual.card_color, "red")
        self.assertEqual(virtual.source_cards, (red,))

    def test_virtual_card_does_not_pollute_the_deck(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        red = heart("7")
        guanyu.hand = [red]
        virtual = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)[0][1]
        self.assertNotIn(virtual, game.deck.draw_pile)
        self.assertNotIn(virtual, game.deck.discard_pile)

    def test_conversion_source_must_be_a_real_card(self):
        """虚拟牌不能作为转换源，避免无限递归。"""

        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        red = heart("7")
        guanyu.hand = [red]
        virtual = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)[0][1]
        self.assertEqual(game.conversions.options_for(game, guanyu, virtual, PLAY_CONTEXT), [])
        self.assertTrue(_is_red_guard(virtual))

    def test_conversions_are_unbound_with_the_skill(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        self.assertEqual(len(game.conversions), 1)
        game.skills.unbind(guanyu, "wusheng")
        self.assertEqual(len(game.conversions), 0)

    def test_conversions_clear_on_reset(self):
        game = self.make_game(3)
        game.general_pool = tuple(game.generals.ids())
        game.selected_general = "guanyu"
        game.assign_generals(human_general="guanyu")
        self.assertGreater(len(game.conversions), 0)

        game.reset()
        # reset 后按 selected_general 重新分配：关羽的武圣转换重新注册。
        self.assertGreater(len(game.conversions), 0, "重开后按新的分配重新注册")

        guest = game.players[0]
        before_total = len(game.conversions)
        # 按"归属"统计而不是按某个上下文能匹配到几张牌来统计：
        # 第二批武将里有只在响应上下文可用的转换（如倾国），
        # 用 PLAY 上下文的匹配数推断归属会低估，武将池变大后就会误报。
        before_own = len([
            item for item in game.conversions.sorted_items()
            if item.owner is guest
        ])
        game.clear_general(guest)
        self.assertEqual(
            len(game.conversions), before_total - before_own,
            "解绑只撤销该角色自己的转换",
        )

    def test_virtual_card_reports_display_name(self):
        card = VirtualCard(name="SHA", source_cards=(), nature="fire")
        self.assertEqual(card.display_name, "火杀")
        self.assertTrue(card.is_virtual)


def _is_red_guard(card):
    """公开给测试使用：虚拟牌不会被当作红色实体牌。"""

    from src.game.skills.standard.shu import _is_red
    return not _is_red(card)


# ==================================================
# 武将技能
# ==================================================


class SkillBehaviourTests(Phase8TestCase):
    def test_zhangfei_paoxiao_removes_sha_limit(self):
        game = self.make_game(3)
        zhangfei = game.players[1]
        game.set_general(zhangfei, "zhangfei")
        self.assertTrue(game.can_use_unlimited_sha(zhangfei))
        self.assertFalse(game.can_use_unlimited_sha(game.players[2]))

    def test_huangyueying_jizhi_and_qicai(self):
        game = self.make_game(4)
        yueying = game.players[1]
        game.set_general(yueying, "huangyueying")
        self.assertEqual(game.skills.skill_ids_of(yueying), ("jizhi", "qicai"))
        self.assertTrue(game.ignores_trick_range(yueying))

        yueying.hand = [trick("WUZHONG")]
        game.current_turn_player = yueying
        game.phase = "play"
        before = len(yueying.hand)
        game.submit_action(UseCardAction(yueying, yueying.hand[0], [yueying]))
        self.drain(game)
        # 无中生有摸两张 + 集智摸一张
        self.assertEqual(len(yueying.hand), before - 1 + 3)

    def test_qicai_ignores_shunshou_range(self):
        game = self.make_game(4)
        yueying = game.players[1]
        game.set_general(yueying, "huangyueying")
        far = game.players[3]
        far.hand = [tao()]
        card = trick("SHUNSHOU")
        yueying.hand = [card]
        effect = game.engine.card_effects.require(card)
        action = UseCardAction(yueying, card, [far])
        self.assertTrue(effect.can_use(game, action)[0])

        plain = game.players[2]
        plain.hand = [card]
        # P2 到 P0 的距离是 2，普通角色不能顺手
        self.assertFalse(effect.can_use(game, UseCardAction(plain, card, [game.player]))[0])

    def test_xiahoudun_ganglie_punishes_the_source(self):
        game = self.make_game(3)
        xiahoudun = game.players[1]
        game.set_general(xiahoudun, "xiahoudun")
        attacker = game.player
        set_draw_order(game, [spade("5")])
        before = attacker.hp
        DamageFlow(game.engine, DamageContext(attacker, xiahoudun, 1, card=normal_sha())).start()
        self.drain(game)
        self.assertEqual(attacker.hp, before - 1)

    def test_caocao_jianxiong_gains_the_damage_card(self):
        game = self.make_game(3)
        caocao = game.players[1]
        game.set_general(caocao, "caocao")
        attacker = game.player
        attack = normal_sha()
        attacker.hand = [attack]
        caocao.hand = []
        game.current_turn_player = attacker
        game.phase = "play"

        game.submit_action(UseCardAction(attacker, attack, [caocao]))
        self.drain(game)
        self.assertTrue(
            any(card is attack for card in caocao.hand),
            "奸雄应当获得造成伤害的那张实体牌",
        )

    def test_caocao_jianxiong_handles_converted_damage(self):
        game = self.make_game(3)
        caocao = game.players[1]
        guanyu = game.players[2]
        game.set_general(caocao, "caocao")
        game.set_general(guanyu, "guanyu")
        red = heart("K")
        guanyu.hand = [red]
        victim = caocao
        victim.hand = []
        game.current_turn_player = guanyu
        game.phase = "play"

        virtual = game.conversions.options_for(game, guanyu, red, PLAY_CONTEXT)[0][1]
        game.submit_action(UseCardAction(guanyu, virtual, [victim]))
        self.drain(game)

        self.assertTrue(
            any(card is red for card in caocao.hand),
            "武圣转化的伤害应当让奸雄拿到原始的实体牌",
        )

    def test_simayi_fankui_gains_a_card_from_the_source(self):
        game = self.make_game(3)
        simayi = game.players[1]
        game.set_general(simayi, "simayi")
        attacker = game.players[2]
        stolen = tao()
        attacker.hand = [stolen]
        DamageFlow(game.engine, DamageContext(attacker, simayi, 1, card=normal_sha())).start()
        self.drain(game)
        self.assertTrue(any(card is stolen for card in simayi.hand))

    def test_guojia_yiji_draws_two_per_damage(self):
        game = self.make_game(3)
        guojia = game.players[1]
        game.set_general(guojia, "guojia")
        guojia.hand = []
        set_draw_order(game, [tao(), tao(), tao(), tao()])
        DamageFlow(game.engine, DamageContext(game.player, guojia, 2, card=normal_sha())).start()
        self.drain(game)
        self.assertEqual(len(guojia.hand), 4, "2 点伤害 → 遗计摸 4 张")

    def test_guojia_tiandu_gains_the_judge_card(self):
        game = self.make_game(3)
        guojia = game.players[1]
        game.set_general(guojia, "guojia")
        guojia.hand = []
        card = spade("7")
        set_draw_order(game, [card])
        JudgeFlow(game.engine, guojia, "lebu").start()
        self.drain(game)
        self.assertTrue(any(item is card for item in guojia.hand))

    def test_zhangliao_tuxi_takes_cards_instead_of_drawing(self):
        from src.game.rules import TurnPhase
        from src.game.skills.standard.wei import TuxiFlow, tuxi_can_offer

        game = self.make_game(3)
        zhangliao = game.players[1]
        game.set_general(zhangliao, "zhangliao")
        first, second = game.players[2], game.player
        first.hand = [tao(), tao()]
        second.hand = [shan()]
        zhangliao.hand = []
        set_draw_order(game, [tao(), tao(), tao()])

        # 突袭现在是摸牌阶段的阶段钩子，不再由出牌阶段主动发动。
        offers = game.skills.phase_offers(zhangliao, TurnPhase.DRAW)
        self.assertEqual([definition.id for _player, definition in offers], ["tuxi"])
        self.assertTrue(tuxi_can_offer(game, zhangliao))

        TuxiFlow(game.engine, zhangliao).start()
        self.drain(game)
        self.assertGreater(len(zhangliao.hand), 0, "突袭应当夺取目标的手牌")
        self.assertEqual(zhangliao.skill_state.get("tuxi", "used"), 1)

    def test_guanyu_wusheng_is_registered_on_the_general(self):
        game = self.make_game(3)
        guanyu = game.players[1]
        game.set_general(guanyu, "guanyu")
        self.assertEqual(game.skills.skill_ids_of(guanyu), ("wusheng",))
        self.assertEqual(len(game.conversions), 1)

    def test_zhaoyun_longdan_has_two_directions(self):
        game = self.make_game(3)
        zhaoyun = game.players[1]
        game.set_general(zhaoyun, "zhaoyun")
        self.assertEqual(len(game.conversions), 2)
        names = sorted(item.conversion.name for item in game.conversions.sorted_items())
        self.assertEqual(names, ["SHA", "SHAN"])

    def test_zhouyu_yingzi_and_fanjian(self):
        game = self.make_game(3)
        zhouyu = game.players[1]
        game.set_general(zhouyu, "zhouyu")
        zhouyu.hp = 3
        zhouyu.max_hp = 3
        self.assertEqual(game.draw_count(zhouyu), 3, "英姿让摸牌阶段多摸一张")

        zhouyu.hand = [tao()]
        game.current_turn_player = zhouyu
        game.phase = "play"
        target = game.players[2]
        target.hand = [spade("3")]
        allowed, _reason = game.skills.can_activate(zhouyu, "fanjian")
        self.assertTrue(allowed)
        game.skills.activate(zhouyu, "fanjian", target=target)
        self.drain(game)
        self.drain(game)
        self.assertIn(zhouyu.skill_state.get("fanjian", "used"), (1,))

    def test_sunshangxiang_xiaoji_draws_on_equipment_loss(self):
        game = self.make_game(3)
        sun = game.players[1]
        game.set_general(sun, "sunshangxiang")
        sun.hand = []
        armor = equipment("BAGUA")
        sun.set_equipment(armor)
        set_draw_order(game, [tao(), tao()])

        from src.game.equipment import EquipmentMixin  # noqa: F401

        game.remove_equipment_with_effects(sun, "armor")
        self.drain(game)
        self.assertGreaterEqual(len(sun.hand), 2, "枭姬在失去装备时摸两张")

    def test_sunshangxiang_jieyin_heals_both(self):
        game = self.make_game(3)
        sun = game.players[1]
        game.set_general(sun, "sunshangxiang")
        sun.hp = 1
        sun.hand = [tao(), tao()]
        partner = game.players[2]
        partner.gender = "male"
        partner.hp = 2
        game.current_turn_player = sun
        game.phase = "play"

        allowed, _reason = game.skills.can_activate(sun, "jieyin")
        self.assertTrue(allowed)
        cost = list(sun.hand)
        sun._phase_cost = None
        game.skills.activate(sun, "jieyin", target=partner, cards=cost)
        self.assertEqual(sun.hp, 2)
        self.assertEqual(partner.hp, 3)
        self.assertEqual(len(sun.hand), 0)


# ==================================================
# 组合与核心约束
# ==================================================


class SkillCombinationTests(Phase8TestCase):
    def test_guicai_and_tiandu_together(self):
        game = self.make_game(3)
        guojia, simayi = game.players[1], game.players[2]
        game.set_general(guojia, "guojia")
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        JudgeFlow(game.engine, guojia, "lebu").start()
        game.submit_action(SelectCardsAction(simayi, game.pending_request.request_id, [replacement]))
        self.drain(game)

        self.assertIn(replacement, guojia.hand)
        self.assertIn(original, game.deck.discard_pile)

    def test_fankui_and_ganglie_on_the_same_damage(self):
        game = self.make_game(3)
        xiahoudun, simayi = game.players[1], game.players[2]
        game.set_general(xiahoudun, "xiahoudun")
        game.set_general(simayi, "simayi")
        attacker = game.player
        attacker.hand = [tao()]
        set_draw_order(game, [heart("5")])
        before = attacker.hp

        DamageFlow(game.engine, DamageContext(attacker, xiahoudun, 1, card=normal_sha())).start()
        self.drain(game)
        self.assertEqual(attacker.hp, before, "红桃判定让刚烈不反伤")

    def test_guanyu_attack_with_wusheng_then_caocao_jianxiong(self):
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

        self.assertEqual(caocao.hp, caocao.max_hp - 1)
        self.assertTrue(any(card is red for card in caocao.hand))

    def test_parallel_generals_do_not_share_state(self):
        game = self.make_game(4)
        first, second = game.players[1], game.players[2]
        game.set_general(first, "guojia")
        game.set_general(second, "guojia")
        first.hand = []
        second.hand = []
        set_draw_order(game, [tao() for _ in range(8)])

        DamageFlow(game.engine, DamageContext(game.player, first, 1, card=normal_sha())).start()
        self.drain(game)
        self.assertEqual(len(first.hand), 2)
        self.assertEqual(len(second.hand), 0)

    def test_restart_clears_every_skill_artifact(self):
        game = self.make_game(4)
        game.general_pool = tuple(game.generals.ids())
        game.assign_generals(human_general="zhaoyun")

        game.reset()
        # reset 会按当前武将池重新分配：状态干净、每个技能只有一个实例。
        #
        # 监听数按**每个技能自己声明的 bindings 条数**累加：一个技能可以同时
        # 订阅多个事件（忍戒看受伤与弃牌、固政看阶段与弃牌、智迟看伤害与出牌），
        # 因此"一个技能 = 一条监听"这个假设对扩展包技能不再成立。真正要守的
        # 不变量是"同一个技能没有第二个实例"。
        for player in game.players:
            self.assertEqual(len(player.skill_state), 0)
            expected = 0
            seen_ids = []
            for instance in game.skills.skills_of(player):
                expected += len(instance.bindings())
                seen_ids.append(instance.id)
            self.assertEqual(
                len(seen_ids), len(set(seen_ids)),
                "同一个技能被绑定了多个实例：" + str(sorted(seen_ids)))
            self.assertEqual(
                game.skills.listeners_for(player), expected,
                "每个触发式技能只应有一个实例",
            )
        self.assertEqual(game.judge_context, None)


class CoreIsolationTests(Phase8TestCase):
    def test_core_files_do_not_mention_specific_generals(self):
        import pathlib
        import re

        forbidden = re.compile(
            r'general_id\s*==|general\.name\s*==|general_id\s*in\s*\('
        )
        core_files = [
            "flows/damage.py", "flows/turn.py", "flows/dying.py", "flows/death.py",
            "flows/judge.py", "flows/use_card.py", "flows/wuxie.py",
            "rules/distance.py", "rules/seats.py", "rules/targeting.py",
            "equipment.py", "conversion.py",
            "engine/runtime.py", "engine/skills.py",
        ]
        offenders = []
        root = pathlib.Path("src/game")
        for relative in core_files:
            path = root / relative
            if not path.exists():
                continue
            for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if forbidden.search(line):
                    offenders.append("%s:%d" % (relative, index))
        for path in (root / "card_effects").glob("*.py"):
            for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if forbidden.search(line):
                    offenders.append("%s:%d" % (path, index))
        self.assertEqual(offenders, [])

    def test_ui_does_not_mention_specific_generals(self):
        import pathlib
        import re

        forbidden = re.compile(r'general_id\s*==|general\.name\s*==')
        offenders = []
        for path in pathlib.Path("src/ui").glob("*.py"):
            for index, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if forbidden.search(line):
                    offenders.append("%s:%d" % (path, index))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
