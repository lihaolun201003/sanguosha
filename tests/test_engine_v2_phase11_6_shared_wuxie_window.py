"""Phase 11.6：共享无懈阶段（房主统一检查、所有有资格的人同时响应）。

这一份测试覆盖四组行为：

A. **规则层**（真引擎，无网络）
   * 全员都没有合法响应 → 不开窗口、直接继续结算；
   * 有资格的人（含锦囊使用者）在**同一轮**里同时获得机会，且只有一条请求；
   * 本轮全员放弃 → 阶段结束、按最终抵消状态继续；
   * 有人打出无懈 → 翻转抵消状态并在同一窗口内开下一轮，**上一轮放弃过的人
     照样可以再响应**；
   * 同轮抢答：第一张合法打出的牌锁定本轮，其他人本轮的提交整条作废
     （不扣牌、不结算）；旧轮的迟到消息也不会被算进下一轮；
   * 普通单人响应（杀 / 闪）的请求形状与归属不变。

B. **提示与操作权限**（prompt / 出牌门控）
   * 有资格：显示"使用【无懈可击】／不出"；
   * 无资格：显示"等待其他玩家响应"，不能出牌、不能结束回合；
   * 已放弃本轮：显示"已放弃本轮，等待其他玩家响应"；
   * 等待期间点手牌 / 结束回合都无效（引擎侧也会拒绝）。

C. **视图信息**：视图只带"我自己的状态"，绝不暴露谁手里有【无懈可击】。

D. **真实 socket（本机双客户端）**：两个人同时拿到独立 request_id 的个人请求、
   只收到自己的候选；先答的人锁定本轮、后答的整条作废且不扣牌；旧轮迟到消息
   同样不扣牌；没有资格的那位只看到等待提示。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_engine_v2_phase11_6_shared_wuxie_window``
"""

import json
import os
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.engine import PassPendingAction, RespondCardAction, UseCardAction
from src.game.engine.events import EventType
from src.network.decisions import ACTION_PASS, ACTION_SUBMIT, DecisionResult
from src.network.protocol import MessageType
from tests.legacy_helpers import canonical_card

from tools.lan_view_harness import (
    RECT,
    MatchSession,
    force_hand,
)


def trick(name):
    return canonical_card(name)


def wuxie_ids(player):
    return [card.id for card in player.hand if card.name == "WUXIE"]


class WuxieWindowTestCase(unittest.TestCase):
    """共享无懈阶段的最小脚手架：真引擎 + 可控手牌。"""

    def make_game(self, ai_count=3, *, pacing=False):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        game.ai_pacing = bool(pacing)
        for player in game.players:
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        game.current_turn_player = game.player
        return game

    def watch_requests(self, game):
        seen = []
        game.context.events.subscribe(
            EventType.PENDING_CREATED,
            lambda _c, event: seen.append(event.payload["request"]))
        self.requests = seen
        return seen

    def start_watching(self, game):
        return self.watch_requests(game)

    def hand_out_wuxie(self, game, *, skip=()):
        for player in game.players:
            if player in skip:
                continue
            player.hand = [trick("WUXIE")]
        return game

    def use_taoyuan(self, game):
        """真人使用【桃园结义】：有益锦囊 → AI 策略一律放弃，窗口停在等人。"""

        card = trick("TAOYUAN")
        game.player.hand.append(card)
        targets = list(game.seats.alive_players_in_order(
            start_after=game.player, include_start=True))
        game.submit_action(UseCardAction(game.player, card, targets))
        return card

    def wuxie_rounds(self, game):
        """已经创建过的无懈请求（每一轮一条）。"""

        return [request for request in getattr(self, "requests", ())
                if request.context.get("reason") == "wuxie_chain"]

    def drain(self, game, steps=400):
        """把动作队列推到空闲；真人的响应窗口按"不出"收尾。"""

        for _ in range(steps):
            if game.response.active:
                game.pass_response()
            if not game.busy and game.pending_request is None:
                return True
            game.update(0.05)
        return not game.busy and game.pending_request is None


# ==================================================
# A 规则层
# ==================================================

class SharedWuxieRulesTests(WuxieWindowTestCase):
    """窗口的推进规则。用 ``ai_pacing`` 把 AI 的回答排进动作队列，
    这样"一轮同时问了好几个人、还没人答"的状态是稳定可观察的。"""

    def _open_window(self, ai_count=3):
        game = self.make_game(ai_count, pacing=True)
        self.hand_out_wuxie(game)
        game.player.hp = 2
        self.start_watching(game)
        self.use_taoyuan(game)
        return game

    def test_nobody_can_respond_opens_no_window(self):
        """全员都没有合法响应：不开无懈窗口，直接继续结算。"""

        game = self.make_game(3)
        self.start_watching(game)
        card = trick("GUOHE")
        game.player.hand = [card]
        game.submit_action(UseCardAction(game.player, card, [game.players[1]]))

        reasons = [request.context.get("reason") for request in self.requests]
        self.assertNotIn("wuxie_chain", reasons,
                         "没有人能无懈时不该出现空询问")
        self.assertFalse(game.response.active, "不该给任何人弹一块只能点「不出」的面板")
        self.assertIsNone(game.pending_request)

    def test_every_eligible_player_is_asked_at_once(self):
        """一轮只建一条请求，成员是**所有**打得出无懈的人（含使用者）。"""

        game = self._open_window()
        rounds = self.wuxie_rounds(game)
        self.assertEqual(len(rounds), 1, "一轮里只能有一条无懈请求")
        request = rounds[0]
        self.assertTrue(request.is_group)
        self.assertEqual([p.seat for p in request.responders], [0, 1, 2, 3],
                         "使用者本人也在检查范围内，按座次排列")
        self.assertEqual(request.context.get("round_id"), 1)
        self.assertEqual(request.member_state,
                         {"P0": "pending", "P1": "pending",
                          "P2": "pending", "P3": "pending"},
                         "有资格的人在同一轮里**同时**被问，而不是排队")
        self.assertTrue(game.response.active, "本地真人的面板已经建好")

    def test_all_passes_end_the_window_and_continue_resolving(self):
        """本轮有资格的人全部放弃 → 阶段结束，锦囊照常结算。"""

        game = self._open_window()
        request = game.pending_request
        game.submit_action(PassPendingAction(game.player, request.request_id))
        self.assertIs(game.pending_request, request, "别人还没答，本轮继续等")
        self.assertEqual(request.member_status(game.player), "passed")

        self.assertTrue(self.drain(game), "全员放弃之后窗口没有结束")
        self.assertEqual(game.player.hp, 3, "桃园结义（回复 1 点）应当结算")
        self.assertEqual(len(self.wuxie_rounds(game)), 1, "全员放弃不该再开新轮")

    def test_counter_wuxie_opens_a_new_round_for_everyone(self):
        """有人打出无懈 → 翻转抵消状态并开下一轮，上一轮放弃过的人可以再响应。"""

        game = self._open_window()
        first = game.pending_request
        game.submit_action(PassPendingAction(game.player, first.request_id))
        self.assertEqual(first.member_status(game.player), "passed")

        # 某个还在等待的成员选择"打出无懈"（这里直接提交，绕过 AI 的策略层：
        # 规则层不关心是谁决定的，只关心这一轮被锁定）。
        roller = game.players[1]
        card = roller.hand[0]
        game.submit_action(RespondCardAction(roller, first.request_id, card, None))

        second = game.pending_request
        self.assertIsNotNone(second)
        self.assertIsNot(second, first, "应当开一条新的轮次请求")
        self.assertEqual(second.context.get("window_id"), first.context.get("window_id"),
                         "窗口号在整个无懈阶段内稳定")
        self.assertEqual(second.context.get("round_id"), 2)
        self.assertTrue(second.context.get("nullified"), "一张无懈把锦囊抵消了")
        self.assertEqual(second.member_status(game.player), "pending",
                         "上一轮放弃过的人，下一轮照样有资格")
        self.assertIn(card, game.deck.discard_pile)

        # 旧轮那些排队的回答不能再动任何牌；新一轮照常收尾。
        self.assertTrue(self.drain(game), "新一轮没有收尾")
        self.assertEqual(
            len([c for c in game.deck.discard_pile if c.name == "WUXIE"]), 1,
            "只有第一个打出的那张无懈被消费")
        self.assertEqual(game.player.hp, 2, "被抵消的桃园结义不该生效")

    def test_same_round_loser_cannot_consume_a_card(self):
        """同轮抢答：第一张锁定了本轮，别人本轮的提交整条作废。"""

        game = self._open_window()
        first = game.pending_request
        winner, loser = game.players[1], game.players[2]
        winner_card, loser_card = winner.hand[0], loser.hand[0]
        game.submit_action(RespondCardAction(winner, first.request_id, winner_card, None))

        with self.assertRaises(ValueError):
            game.submit_action(RespondCardAction(loser, first.request_id, loser_card, None))
        self.assertIn(loser_card, loser.hand, "同轮的落败者一张牌都不能被扣掉")
        self.assertEqual(
            len([c for c in game.deck.discard_pile if c.name == "WUXIE"]), 1)

    def test_stale_round_submission_is_not_carried_into_the_next_round(self):
        """旧轮迟到的提交：不扣牌、不结算，也不算进下一轮。"""

        game = self._open_window()
        first = game.pending_request
        winner, late = game.players[1], game.players[2]
        late_card = late.hand[0]
        game.submit_action(RespondCardAction(winner, first.request_id, winner.hand[0], None))
        self.assertIsNot(game.pending_request, first)

        # 旧轮的 request_id 已经不在等待中了：这条回答什么也不能改。
        with self.assertRaises(ValueError):
            game.submit_action(RespondCardAction(late, first.request_id, late_card, None))
        self.assertIn(late_card, late.hand)
        self.assertEqual(
            len([c for c in game.deck.discard_pile if c.name == "WUXIE"]), 1)
        second = game.pending_request
        self.assertEqual(second.context.get("round_id"), 2)
        self.assertEqual(second.member_status(late), "pending",
                         "迟到的旧消息不能替他在新轮里作答")

    def test_skill_conversion_counts_as_able_to_respond(self):
        """"有资格"要按 Card Action Discovery 判定：技能转化的无懈也算。"""

        from src.game.skills.conversion_probes import (
            WUXIE_PROBE_ID,
            bind_conversion_probes,
        )

        game = self.make_game(2, pacing=True)
        game.player.hp = 2
        bind_conversion_probes(game, game.players[1], WUXIE_PROBE_ID)
        # 只有一张黑牌：没有实体无懈，但【墨守】能把它当无懈打出。
        black = canonical_card("SHA", card_color="black")
        game.players[1].hand = [black]
        self.start_watching(game)
        self.use_taoyuan(game)

        request = game.pending_request
        self.assertIsNotNone(request, "只有技能转化的人也该被问")
        self.assertTrue(request.is_group)
        self.assertTrue(request.is_member(game.players[1]))
        self.assertEqual(game.card_actions.can_respond(
            game.players[1], allowed_names=("WUXIE",)), True)

        # 真打出去：转化的虚拟牌走同一条提交路径，消费的是那张实体黑牌。
        option = next(
            item for item in game.card_actions.respondable_options(
                game.players[1],
                game.card_actions.response_context(
                    game.players[1], allowed_names=("WUXIE",)))
            if item.is_conversion)
        virtual = game.card_actions.effective_card(option)
        game.submit_action(RespondCardAction(
            game.players[1], request.request_id, virtual, None))
        self.assertIn(black, game.deck.discard_pile)
        # 会转化的那个人已经用掉了唯一的黑牌，别人手里没有无懈：窗口收尾，
        # 桃园结义被这次无懈抵消。
        self.assertIsNone(game.pending_request)
        self.assertIn("抵消", game.message)

    def test_incomplete_conversion_is_not_respondable(self):
        """source 还没凑齐的多来源转化不算"能响应"（现在支付不出来）。"""

        from src.game.skills.conversion_probes import (
            PAIR_SHAN_PROBE_ID,
            bind_conversion_probes,
        )

        game = self.make_game(2, pacing=True)
        sha = trick("SHA")
        game.player.hand = [sha]
        bind_conversion_probes(game, game.players[1], PAIR_SHAN_PROBE_ID)
        game.players[1].hand = [trick("TAO")]        # 只有一张：凑不出两张当闪
        game.submit_action(UseCardAction(game.player, sha, [game.players[1]]))
        self.assertIsNotNone(game.pending_request)

        self.assertFalse(
            game.card_actions.can_respond(game.players[1], allowed_names=("SHAN",)),
            "一张牌凑不出「两张当【闪】」，不算能响应")
        game.players[1].hand.append(trick("TAO"))
        self.assertTrue(
            game.card_actions.can_respond(game.players[1], allowed_names=("SHAN",)),
            "凑齐两张之后才算能响应")

    def test_plain_single_response_keeps_its_old_shape(self):
        """普通【杀】→【闪】仍然是单人请求：只有目标能回答。"""

        game = self.make_game(2, pacing=True)
        sha = trick("SHA")
        game.player.hand = [sha]
        game.players[1].hand = [trick("SHAN")]
        game.submit_action(UseCardAction(game.player, sha, [game.players[1]]))

        request = game.pending_request
        self.assertIsNotNone(request)
        self.assertFalse(request.is_group)
        self.assertIs(request.target, game.players[1])
        self.assertEqual(request.responders, ())
        with self.assertRaises(ValueError):
            game.submit_action(RespondCardAction(
                game.player, request.request_id, sha, None))
        self.assertEqual(request.member_status(game.players[1]), "",
                         "单人请求不使用群体成员状态")


# ==================================================
# B 提示与操作权限
# ==================================================

class WuxieWindowPromptTests(WuxieWindowTestCase):

    def _window_with_pending_ai(self, *, human_has_wuxie=False, human_has_play=True):
        """造一个"AI 还在犹豫"的窗口（ai_pacing 让 AI 的回答排队等一会）。"""

        game = self.make_game(2, pacing=True)
        if human_has_wuxie:
            game.player.hand = [trick("WUXIE")]
        if human_has_play:
            game.player.hand = list(game.player.hand) + [trick("SHA")]
        game.players[1].hand = [trick("WUXIE")]
        game.player.hp = 2
        self.start_watching(game)
        self.use_taoyuan(game)
        return game

    def _prompt(self, game):
        from src.ui import prompt as prompt_module

        return prompt_module.describe(game)

    def test_eligible_player_gets_the_wuxie_panel(self):
        game = self._window_with_pending_ai(human_has_wuxie=True)
        self.assertTrue(game.response.active)
        request = game.pending_request
        self.assertEqual(request.member_status(game.player), "pending")
        info = self._prompt(game)
        self.assertEqual(info.title, "使用【无懈可击】／不出")
        self.assertIn("无懈可击", info.body)
        self.assertIn("无懈可击", info.progress)

    def test_waiting_player_cannot_play_or_end_the_turn(self):
        # 真人手里没有无懈 → 不是这轮的成员，只能等。
        game = self._window_with_pending_ai(human_has_wuxie=False)
        request = game.pending_request
        self.assertFalse(request.is_member(game.player))
        info = self._prompt(game)
        self.assertEqual(info.title, "等待其他玩家响应")
        self.assertTrue(info.body.strip(), "等待时也不能是空白正文")
        self.assertFalse(game.local_can_play())

        # 点手牌 / 结束回合都必须无效（引擎侧校验，不只是界面灰掉）。
        hand_before = list(game.player.hand)
        turn_before = game.current_turn_player
        game.player_use_card(0, RECT)
        self.assertEqual(list(game.player.hand), hand_before, "等待期间不能出牌")
        game.end_player_turn()
        self.assertIs(game.current_turn_player, turn_before, "等待期间不能结束回合")

    def test_passed_player_sees_the_passed_prompt(self):
        game = self._window_with_pending_ai(human_has_wuxie=True)
        request = game.pending_request
        game.submit_action(PassPendingAction(game.player, request.request_id))

        self.assertEqual(request.member_status(game.player), "passed")
        info = self._prompt(game)
        self.assertEqual(info.title, "已放弃本轮，等待其他玩家响应")
        self.assertFalse(game.local_can_play())
        self.assertFalse(game.response.active, "放弃之后面板要收掉")

    def test_play_phase_reopens_after_the_window_closes(self):
        game = self._window_with_pending_ai(human_has_wuxie=True)
        request = game.pending_request
        game.submit_action(PassPendingAction(game.player, request.request_id))
        # 让 AI 那一份也走完（pacing 队列推进），并把动画队列排空。
        self.assertTrue(self.drain(game), "窗口没有正常结束")
        self.assertTrue(game.local_can_play(), "窗口结束后应当恢复出牌")


# ==================================================
# C 视图只带"我自己的状态"
# ==================================================

class WuxieWindowViewTests(WuxieWindowTestCase):

    def test_view_reveals_only_my_own_status(self):
        game = self.make_game(2, pacing=True)
        game.player.hand = [trick("WUXIE")]
        game.players[1].hand = [trick("WUXIE")]
        game.players[2].hand = [trick("TAO")]        # 没有无懈：不是成员
        self.start_watching(game)
        self.use_taoyuan(game)

        request = game.pending_request
        self.assertTrue(request.is_group)
        from src.game.view.view_builder import build_view

        member = build_view(game, game.player.player_id, 1)
        self.assertEqual(member.response_window["status"], "pending")
        self.assertEqual(member.response_window["reason"], "wuxie_chain")
        self.assertEqual(member.responding_player_id, "",
                         "共享阶段没有唯一的被问者（谁是第一个有资格的也是手牌信息）")

        outsider = build_view(game, game.players[2].player_id, 1)
        self.assertEqual(outsider.response_window["status"], "",
                         "没有资格的人只看到空状态，界面据此显示等待")

        # 别人的候选材料（手牌 id）绝不能出现在我的视图里。
        other_wuxie = game.players[1].hand[0].id
        text = json.dumps(member.to_payload(), ensure_ascii=False)
        self.assertNotIn(other_wuxie, text)
        self.assertNotIn('"responders"', text)
        self.assertNotIn(other_wuxie, json.dumps(outsider.to_payload(), ensure_ascii=False))


# ==================================================
# D 真实 socket（本机双客户端）
# ==================================================

def pump_until(session, predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        game = session.game
        if game.response.active:
            game.pass_response()
        session.pump(0.1)
    return bool(predicate())


def send_stale_answer(client, request_id, card_id):
    """绕过本地面板，直接把一条"旧消息"投回房主（模拟迟到 / 重复的网络回答）。"""

    match = client.match
    return client.session.send_to_host(
        MessageType.DECISION_RESPONSE,
        match_id=match.match_id,
        request_id=int(request_id),
        player_id=match.my_player_id,
        result=DecisionResult(action=ACTION_SUBMIT, card_ids=[card_id]).to_payload(),
    )


class SharedWuxieLanTests(unittest.TestCase):
    """两个游客同时获得资格：各自独立请求、先答者锁定本轮。"""

    def _open_window(self, session, *, both_hold_wuxie=True):
        game = session.game
        first, second = session.host.remote_players()[:2]
        client_a = session.client_of(first)
        client_b = session.client_of(second)
        session.fix_hands(["TAO"])
        force_hand(game, game.player, ["TAOYUAN", "WUXIE", "TAO"])
        force_hand(game, first, ["WUXIE", "TAO"])
        force_hand(game, second, ["WUXIE", "TAO"] if both_hold_wuxie else ["TAO"])
        game.actions.clear()
        game.phase = "play"
        game.current_turn_player = game.player
        game.player.hp = 2
        card = next(item for item in game.player.hand if item.name == "TAOYUAN")
        targets = list(game.seats.alive_players_in_order(
            start_after=game.player, include_start=True))
        game.submit_action(UseCardAction(game.player, card, targets))
        return first, second, client_a, client_b

    def test_guests_get_independent_requests_and_the_first_answer_wins(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, second, client_a, client_b = self._open_window(session)

            self.assertTrue(pump_until(
                session,
                lambda: client_a.match.waiting_decision
                and client_b.match.waiting_decision, timeout=6.0),
                "两个有资格的人应当**同时**拿到请求")
            req_a = dict(client_a.match.decision)
            req_b = dict(client_b.match.decision)
            self.assertNotEqual(req_a["request_id"], req_b["request_id"],
                                "每人一条独立的网络决策")
            self.assertEqual(req_a["context"]["window_id"],
                             req_b["context"]["window_id"])
            self.assertEqual(req_a["context"]["round_id"],
                             req_b["context"]["round_id"])

            card_a = wuxie_ids(first)[0]
            card_b = wuxie_ids(second)[0]
            self.assertEqual([item["card_id"] for item in req_a["cards"]], [card_a],
                             "每人只收到**自己的**候选牌")
            self.assertEqual([item["card_id"] for item in req_b["cards"]], [card_b])
            self.assertEqual(client_a.leaked_card_ids([card_b]), [],
                             "不能把我的候选之外的手牌泄露给别人")

            # 甲先答：锁定本轮。
            client_a.match.answer(DecisionResult(action=ACTION_SUBMIT, card_ids=[card_a]))
            self.assertTrue(pump_until(
                session,
                lambda: card_a in [card.id for card in game.deck.discard_pile],
                timeout=6.0), "甲的无懈没有被消费")
            self.assertIn(card_b, [c.id for c in second.hand],
                             "乙的无懈一张都不能动")
            self.assertFalse(session.host.match.registry.rejected,
                             "同轮还没提交的人不该被当成非法回答")

            # 乙那条"旧轮"的迟到回答：拒绝、不扣牌。
            send_stale_answer(client_b, req_b["request_id"], card_b)
            pump(0.6)
            self.assertTrue(session.host.match.registry.rejected,
                            "被撤回的旧轮回答必须被拒绝")
            self.assertIn(card_b, [c.id for c in second.hand])
            self.assertNotIn(card_b, [card.id for card in game.deck.discard_pile],
                             "迟到的回答不能扣掉任何一张牌")

            # 新的一轮里乙仍然可以响应（有自己的新请求、新 request_id）。
            self.assertTrue(pump_until(
                session,
                lambda: client_b.match.waiting_decision
                and (client_b.match.decision or {}).get("context", {}).get("round_id") == 2,
                timeout=6.0), "反无懈之后没有开新轮")
            req_b2 = dict(client_b.match.decision)
            self.assertNotEqual(req_b2["request_id"], req_b["request_id"])
            self.assertEqual([item["card_id"] for item in req_b2["cards"]], [card_b])
            self.assertIn(card_b, [c.id for c in second.hand],
                             "迟到消息没有替他在新轮里作答")

            # 乙放弃 + 房主放弃 → 本轮结束，按"被抵消"继续结算。
            client_b.match.answer(DecisionResult(action=ACTION_PASS))
            self.assertTrue(pump_until(
                session,
                lambda: game.pending_request is None and not game.engine.pending.active,
                timeout=6.0), "全员放弃之后窗口没有结束")
            self.assertEqual(game.player.hp, 2, "被无懈抵消的桃园结义不该生效")

    def test_guest_without_wuxie_only_waits(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, second, client_a, client_b = self._open_window(
                session, both_hold_wuxie=False)

            self.assertTrue(pump_until(
                session, lambda: client_a.match.waiting_decision, timeout=6.0),
                "有资格的人应当拿到请求")
            self.assertIsNone(client_b.match.decision,
                              "没有无懈的人不该收到询问")
            pump(0.4)
            window = client_b.view.response_window
            self.assertEqual(window["status"], "", "只带自己的状态")
            from src.ui import prompt as prompt_module

            info = prompt_module.describe(client_b.scene.view)
            self.assertEqual(info.title, "等待其他玩家响应")
            self.assertFalse(client_b.scene.view.local_can_play(),
                             "等待期间客户端不能开放出牌 / 结束回合")

    def test_waiting_guest_sees_the_passed_text_after_giving_up(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, second, client_a, client_b = self._open_window(session)
            self.assertTrue(pump_until(
                session,
                lambda: client_a.match.waiting_decision
                and client_b.match.waiting_decision, timeout=6.0))

            client_b.match.answer(DecisionResult(action=ACTION_PASS))
            self.assertTrue(pump_until(
                session,
                lambda: (client_b.view.response_window or {}).get("status") == "passed",
                timeout=6.0), "放弃之后视图里应当显示「已放弃本轮」")
            from src.ui import prompt as prompt_module

            info = prompt_module.describe(client_b.scene.view)
            self.assertEqual(info.title, "已放弃本轮，等待其他玩家响应")
            self.assertFalse(client_b.scene.view.local_can_play())
            # 别人还在决定：这一轮不能结束。
            self.assertTrue(game.engine.pending.active)


class TableCardPruneTests(WuxieWindowTestCase):
    """顺手修掉的一个崩溃：清理"结算早已结束却还挂在桌面"的残牌时读错了变量。

    ``Game.prune_stale_table_cards`` 的判据写成了嵌套生成器引用未定义的
    ``item``：只要处理区里还有牌（无懈这类瞬间结算之后很常见），清理就会抛
    ``NameError``。这里把那条路径钉住。
    """

    def test_prune_keeps_a_card_whose_source_is_still_processing(self):
        game = self.make_game(1)
        card = trick("TAOYUAN")
        game.player.hand = [card]
        game.add_table_card(card, "table_card")
        game.processing_zone.append(card)      # 还在结算中

        self.assertEqual(game.prune_stale_table_cards(), 0)
        self.assertEqual([entry[0] for entry in game.table_cards], [card])

    def test_prune_removes_a_card_that_is_long_gone(self):
        game = self.make_game(1)
        card = trick("TAOYUAN")
        game.add_table_card(card, "table_card")
        game.deck.discard_pile.append(card)    # 已经进弃牌堆了

        self.assertEqual(game.prune_stale_table_cards(), 1)
        self.assertEqual(list(game.table_cards), [])


if __name__ == "__main__":
    unittest.main()
