"""Phase 8：通用判定改判（鬼才）+ 天妒 的确定性测试。

覆盖范围：

* 基础 JudgeFlow（无改判者）
* 单个改判者的 Pass / Replace（含真人 UI 入口）
* 判定牌与替换牌的实体区域移动、区域唯一性
* 连续改判 A → B → C
* 刚烈 / 闪电 / 乐不思蜀 / 八卦阵 按「最终判定牌」结算
* 郭嘉【天妒】取得最终生效的判定牌
* 引擎侧对改判请求的二次校验
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.card_catalog import create_development_deck, create_equipment_cards
from src.game import Game
from src.game.engine import (
    ConfirmPendingAction,
    FlowStatus,
    PassPendingAction,
    SelectCardsAction,
    UseCardAction,
)
from src.game.engine.pending import PendingRequestType
from src.game.flows import DamageContext, DamageFlow, TurnFlow
from src.game.flows.judge import JudgeFlow
from src.game.rules import TurnPhase
from tests.legacy_helpers import canonical_card, normal_sha, set_draw_order, shan, tao

CARD_RECT = (0, 0, 80, 120)


def card_of(suit, rank=None, *, exclude=()):
    for card in create_development_deck():
        if card.suit != suit:
            continue
        if rank is not None and card.rank != rank:
            continue
        if card.name in exclude:
            continue
        return card
    raise AssertionError("no %s card found" % suit)


def spade(rank=None):
    return card_of("spade", rank, exclude=("SHANDIAN",))


def heart(rank=None):
    return card_of("heart", rank)


def club(rank=None):
    return card_of("club", rank)


def bagua_armor():
    return next(card for card in create_equipment_cards() if card.name == "BAGUA")


class JudgeReplacementTestCase(unittest.TestCase):
    def setUp(self):
        pygame.init()

    def make_game(self, ai_count=2, *, hand_size=4):
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
        return game

    # ==================================================
    # 驱动：真人一律走 UI 入口（选牌 / 跳过），AI 交给引擎
    # ==================================================

    def drain(self, game, *, human_select=None, max_steps=800, dt=1 / 60):
        for _ in range(max_steps):
            if game.busy:
                game.update(dt)
                continue
            request = game.pending_request
            if request is None:
                return True
            if getattr(request.target, "is_human", False):
                if self._drive_human(game, request, human_select):
                    continue
                return True
            game.engine.present_or_auto_resolve(request)
        raise AssertionError("drain 未能在 %d 步内收敛" % max_steps)

    def _drive_human(self, game, request, human_select):
        if request.request_type is PendingRequestType.SELECT_CARDS:
            if human_select is not None and game.pending_selection is not None:
                chosen = human_select(request) or []
                needed = max(1, request.min_cards)
                for card in chosen[:needed]:
                    if game.pending_selection is None:
                        break
                    # 真人的正常入口：点手上的牌（number=0 时点一张立即回调）。
                    game.select_pending_card(card, CARD_RECT)
                return True
            if game.can_cancel_pending_selection():
                # 真人的「跳过」按钮：放弃本次改判。
                game.cancel_pending_selection()
                return True
            candidates = list(request.context.get("candidates", ()))
            for card in candidates[: max(1, request.min_cards)]:
                if game.pending_selection is None:
                    break
                game.select_pending_card(card, CARD_RECT)
            return True
        if request.request_type is PendingRequestType.RESPOND_CARD:
            game.submit_action(PassPendingAction(request.target, request.request_id))
            return True
        game.submit_action(
            ConfirmPendingAction(request.target, request.request_id, False)
        )
        return True

    # ==================================================
    # 区域辅助
    # ==================================================

    def zones_of(self, game, card):
        """实体牌当前所在的全部区域；正常情况必须恰好一个。"""

        zones = []
        for player in game.players:
            if any(item is card for item in player.hand):
                zones.append(("hand", player.seat))
            for slot, equipped in player.equipment.items():
                if equipped is card:
                    zones.append(("equipment", slot))
            if any(item is card for item in player.judgement_zone):
                zones.append(("judgement", player.seat))
        if any(item is card for item in game.processing_zone):
            zones.append(("processing",))
        if any(item is card for item in game.deck.discard_pile):
            zones.append(("discard", sum(1 for item in game.deck.discard_pile if item is card)))
        if any(item is card for item in game.deck.draw_pile):
            zones.append(("draw_pile", sum(1 for item in game.deck.draw_pile if item is card)))
        if any(item is card for item in game.table_cards):
            zones.append(("table",))
        if any(item is card for item in game.public_card_pool):
            zones.append(("public_pool",))
        return zones

    def assert_single_zone(self, game, card, label=""):
        zones = self.zones_of(game, card)
        self.assertEqual(
            len(zones), 1,
            "%s 应只存在于一个区域，实际：%s" % (label or card.display_name, zones),
        )
        return zones[0][0]


# ==================================================
# A. 基础 JudgeFlow
# ==================================================


class BasicJudgeFlowTests(JudgeReplacementTestCase):
    def test_judgement_completes_without_replacers(self):
        game = self.make_game(3)
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        outcome = judge.start()

        self.assertIs(outcome.status, FlowStatus.COMPLETED)
        self.assertIs(judge.result.card, original)
        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.original_card, original)
        self.assertEqual(judge.result.replacement_history, ())

    def test_judgement_card_ends_in_the_discard_pile(self):
        game = self.make_game(3)
        original = spade("7")
        set_draw_order(game, [original])

        JudgeFlow(game.engine, game.players[1], "lebu").start()

        self.assertIn(original, game.deck.discard_pile)
        self.assertNotIn(original, game.processing_zone)
        self.assertEqual(game.judge_context, None)
        self.assertEqual(game.judge_card, None)
        self.assert_single_zone(game, original)


# ==================================================
# B / C. 单个改判者、实体牌移动
# ==================================================


class SingleReplacerTests(JudgeReplacementTestCase):
    def test_human_pass_leaves_everything_untouched(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        held = heart("K")
        simayi.hand = [held]
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        judge.start()
        self.assertIs(game.pending_request.target, simayi)

        game.submit_action(PassPendingAction(simayi, game.pending_request.request_id))
        self.drain(game)

        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.card, original)
        self.assertIn(held, simayi.hand)

    def test_human_pass_through_the_ui_skip_button(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        held = heart("K")
        simayi.hand = [held]
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        judge.start()
        self.assertTrue(game.can_cancel_pending_selection(), "改判窗口应允许整单跳过")

        game.cancel_pending_selection()
        self.drain(game)

        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.card, original)
        self.assertIn(held, simayi.hand)
        self.assertIsNone(game.pending_selection)

    def test_human_replaces_by_clicking_a_hand_card(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        judge.start()
        game.select_pending_card(replacement, CARD_RECT)
        self.drain(game)

        self.assertTrue(judge.result.replaced)
        self.assertIs(judge.result.card, replacement)
        self.assertIs(judge.result.original_card, original)

    def test_replacement_moves_real_cards_between_zones(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        judge.start()
        game.select_pending_card(replacement, CARD_RECT)
        self.drain(game)

        self.assertNotIn(replacement, simayi.hand, "替换牌必须离开手牌")
        self.assertEqual(self.assert_single_zone(game, replacement), "discard")
        self.assertEqual(self.assert_single_zone(game, original), "discard")
        self.assertEqual(
            sum(1 for item in game.deck.discard_pile if item is original), 1,
            "原判定牌只能进入弃牌堆一次",
        )
        self.assertEqual(
            sum(1 for item in game.deck.discard_pile if item is replacement), 1,
            "最终判定牌只能进入弃牌堆一次",
        )

    def test_two_guicai_players_replace_one_after_another(self):
        """A（AI 判定者）→ B（真人）→ C（AI） 的连续改判。"""

        game = self.make_game(3)
        ai_judged, human = game.players[1], game.player
        game.set_general(human, "simayi")
        game.set_general(ai_judged, "simayi")

        first = heart("Q")
        second = club("9")
        ai_judged.hand = [first]
        human.hand = [second]
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, ai_judged, "lebu")
        judge.start()
        self.assertTrue(judge.result is None)

        self.drain(game, human_select=lambda request: [second])

        history = judge.result.replacement_history
        self.assertEqual(len(history), 2, "两次改判都要进入历史")
        self.assertIs(history[0][2], original)
        self.assertIs(history[0][3], first)
        self.assertIs(history[1][2], first)
        self.assertIs(history[1][3], second)
        self.assertIs(judge.result.card, second, "最终判定牌是最后替换的那张")
        self.assertIs(judge.result.original_card, original)
        self.assertEqual(self.assert_single_zone(game, original), "discard")
        self.assertEqual(self.assert_single_zone(game, first), "discard")
        self.assertEqual(self.assert_single_zone(game, second), "discard")

    def test_engine_rejects_a_card_that_left_the_hand(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        judge = JudgeFlow(game.engine, game.players[1], "lebu")
        judge.start()
        # 窗口打开后这张牌被别处拿走：引擎必须拒绝这次改判而不是凭空移动。
        simayi.hand.remove(replacement)
        game.submit_action(
            SelectCardsAction(simayi, game.pending_request.request_id, [replacement])
        )
        self.drain(game)

        self.assertFalse(judge.result.replaced)
        self.assertIs(judge.result.card, original)
        self.assertNotIn(replacement, game.deck.discard_pile)


# ==================================================
# E. 刚烈 + 鬼才
# ==================================================


class GanglieReplacementTests(JudgeReplacementTestCase):
    def _run_ganglie(self, game, judged_card, human_select=None):
        simayi, xiahoudun = game.player, game.players[1]
        game.set_general(xiahoudun, "xiahoudun")
        game.set_general(simayi, "simayi")
        attacker = game.players[2]
        attacker.hp = attacker.max_hp
        set_draw_order(game, [judged_card, tao(), tao(), tao()])

        DamageFlow(
            game.engine,
            DamageContext(attacker, xiahoudun, 1, card=normal_sha()),
        ).start()
        self.drain(game, human_select=human_select)
        return attacker

    def test_ganglie_reflects_after_the_card_is_replaced_into_a_spade(self):
        """原判定是红桃（不反伤），鬼才换成黑桃后刚烈必须反伤。"""

        game = self.make_game(3)
        simayi = game.player
        replacement = spade("5")
        simayi.hand = [replacement]

        attacker = self._run_ganglie(
            game, heart("3"), human_select=lambda request: [replacement]
        )

        self.assertEqual(attacker.hp, attacker.max_hp - 1, "刚烈按最终判定牌结算")
        self.assertEqual(self.assert_single_zone(game, replacement), "discard")

    def test_ganglie_does_not_reflect_after_the_card_is_replaced_into_a_heart(self):
        """原判定是黑桃（要反伤），鬼才换成红桃后不反伤。"""

        game = self.make_game(3)
        simayi = game.player
        replacement = heart("K")
        simayi.hand = [replacement]

        attacker = self._run_ganglie(
            game, spade("5"), human_select=lambda request: [replacement]
        )

        self.assertEqual(attacker.hp, attacker.max_hp, "红桃判定不反伤")

    def test_ganglie_without_replacement_uses_the_original_card(self):
        game = self.make_game(3)
        xiahoudun = game.player
        game.set_general(xiahoudun, "xiahoudun")
        attacker = game.players[2]
        attacker.hp = attacker.max_hp
        set_draw_order(game, [spade("5"), tao(), tao()])

        DamageFlow(
            game.engine,
            DamageContext(attacker, xiahoudun, 1, card=normal_sha()),
        ).start()
        self.drain(game)

        self.assertEqual(attacker.hp, attacker.max_hp - 1)


# ==================================================
# F. 闪电 + 鬼才
# ==================================================


class ShandianReplacementTests(JudgeReplacementTestCase):
    def _start_turn_with_shandian(self, game, judged_card, replacement_nature=None):
        simayi = game.player
        game.set_general(simayi, "simayi")
        game.player.hp = 4
        lightning = canonical_card("SHANDIAN")
        game.player.judgement_zone.append(lightning)
        set_draw_order(game, [judged_card, tao(), tao(), tao()])
        flow = TurnFlow(game.engine, game.player)
        flow.begin_interactive()
        return lightning, flow

    def test_shandian_hits_when_replaced_into_a_spade(self):
        game = self.make_game(2)
        hit_card = spade("5")
        game.player.hand = [hit_card]

        lightning, _flow = self._start_turn_with_shandian(game, heart("3"))
        self.drain(game, human_select=lambda request: [hit_card])

        self.assertEqual(game.player.hp, 1, "闪电按最终判定牌命中，造成 3 点雷电伤害")
        self.assertEqual(self.assert_single_zone(game, lightning), "discard")
        self.assertEqual(self.assert_single_zone(game, hit_card), "discard")

    def test_shandian_misses_when_replaced_out_of_range(self):
        game = self.make_game(2)
        pass_card = heart("K")
        game.player.hand = [pass_card]

        lightning, _flow = self._start_turn_with_shandian(game, spade("5"))
        self.drain(game, human_select=lambda request: [pass_card])

        self.assertEqual(game.player.hp, 4, "改判成红桃后闪电不再命中")
        self.assertFalse(any(card is lightning for card in game.deck.discard_pile))
        self.assert_single_zone(game, lightning)


# ==================================================
# G. 乐不思蜀 + 鬼才
# ==================================================


class LebuReplacementTests(JudgeReplacementTestCase):
    def _start_turn_with_lebu(self, game, judged_card):
        simayi = game.player
        game.set_general(simayi, "simayi")
        lebu = canonical_card("LEBU")
        game.player.judgement_zone.append(lebu)
        set_draw_order(game, [judged_card, tao(), tao(), tao()])
        flow = TurnFlow(game.engine, game.player)
        flow.begin_interactive()
        return lebu, flow

    def test_lebu_skips_the_play_phase_with_a_black_final_card(self):
        game = self.make_game(2)
        game.player.hand = [heart("K")]

        self._start_turn_with_lebu(game, spade("7"))
        self.drain(game)

        self.assertIn(TurnPhase.PLAY, game.skipped_phases)
        self.assertEqual(game.phase, "discard")

    def test_lebu_is_cancelled_when_replaced_into_a_heart(self):
        game = self.make_game(2)
        heart_card = heart("K")
        game.player.hand = [heart_card]

        self._start_turn_with_lebu(game, spade("7"))
        self.drain(game, human_select=lambda request: [heart_card])

        self.assertNotIn(TurnPhase.PLAY, game.skipped_phases)
        self.assertEqual(game.phase, "play")
        self.assertEqual(self.assert_single_zone(game, heart_card), "discard")


# ==================================================
# H. 八卦阵 + 鬼才
# ==================================================


class BaguaReplacementTests(JudgeReplacementTestCase):
    def _defender(self, game, hand):
        """AI 司马懿 + 八卦阵：set_general 会重置体力，所以顺序不能反。"""

        target = game.players[1]
        game.set_general(target, "simayi")
        target.hp = 4
        target.max_hp = 4
        target.set_equipment(bagua_armor())
        target.hand = list(hand)
        return target

    def _attack(self, game, target, judged_card):
        attacker = game.player
        attack = normal_sha()
        attacker.hand = [attack]
        set_draw_order(game, [judged_card, tao(), tao()])
        game.submit_action(UseCardAction(attacker, attack, [target]))
        self.drain(game)

    def test_bagua_success_uses_the_card_chosen_by_guicai(self):
        game = self.make_game(2)
        red = heart("9")
        target = self._defender(game, [red, spade("4")])

        self._attack(game, target, spade("5"))

        self.assertEqual(target.hp, 4, "鬼才把判定改成红色后八卦阵生效，闪避成功")
        self.assertEqual(self.assert_single_zone(game, red), "discard")

    def test_bagua_fails_when_the_final_card_stays_black(self):
        game = self.make_game(2)
        # 手上只有一张黑牌：无法把判定改红，八卦阵判定失败且没有闪。
        target = self._defender(game, [spade("4")])

        self._attack(game, target, spade("5"))

        self.assertEqual(target.hp, 3, "最终判定牌为黑色，八卦阵失败")


# ==================================================
# I. 天妒
# ==================================================


class TianduTests(JudgeReplacementTestCase):
    def test_tiandu_takes_the_judgement_card_without_replacement(self):
        game = self.make_game(2)
        guojia = game.players[1]
        game.set_general(guojia, "guojia")
        original = spade("7")
        set_draw_order(game, [original])

        JudgeFlow(game.engine, guojia, "lebu").start()

        self.assertIn(original, guojia.hand)
        self.assertNotIn(original, game.deck.discard_pile)
        self.assertEqual(self.assert_single_zone(game, original), "hand")

    def test_tiandu_takes_the_replacement_not_the_original(self):
        game = self.make_game(2)
        simayi, guojia = game.player, game.players[1]
        game.set_general(simayi, "simayi")
        game.set_general(guojia, "guojia")
        replacement = heart("K")
        simayi.hand = [replacement]
        original = spade("7")
        set_draw_order(game, [original])

        JudgeFlow(game.engine, guojia, "lebu").start()
        game.select_pending_card(replacement, CARD_RECT)
        self.drain(game)

        self.assertIn(replacement, guojia.hand, "天妒必须取得最终生效的判定牌")
        self.assertNotIn(original, guojia.hand)
        self.assertEqual(self.assert_single_zone(game, original), "discard")
        self.assertEqual(
            sum(1 for item in game.deck.discard_pile if item is replacement), 0,
            "被天妒取走的判定牌不再进入弃牌堆",
        )
        self.assertEqual(self.assert_single_zone(game, replacement), "hand")


# ==================================================
# J. 区域唯一性
# ==================================================


class ZoneIntegrityTests(JudgeReplacementTestCase):
    def test_every_card_of_a_replaced_judgement_sits_in_exactly_one_zone(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        replacement = heart("K")
        simayi.hand = [replacement, spade("3"), tao()]
        original = spade("7")
        set_draw_order(game, [original])

        JudgeFlow(game.engine, game.players[1], "lebu").start()
        game.select_pending_card(replacement, CARD_RECT)
        self.drain(game)

        for card, label in (
            (original, "原判定牌"),
            (replacement, "替换牌"),
            (simayi.hand[0], "剩余手牌"),
        ):
            self.assert_single_zone(game, card, label)

    def test_skipped_replacement_leaves_no_trace_in_the_processing_zone(self):
        game = self.make_game(2)
        simayi = game.player
        game.set_general(simayi, "simayi")
        simayi.hand = [heart("K")]
        original = spade("7")
        set_draw_order(game, [original])

        JudgeFlow(game.engine, game.players[1], "lebu").start()
        self.assertTrue(any(card is original for card in game.processing_zone))
        game.cancel_pending_selection()
        self.drain(game)

        self.assertEqual(list(game.processing_zone), [])
        self.assertEqual(list(game.table_cards), [])


if __name__ == "__main__":
    unittest.main()
