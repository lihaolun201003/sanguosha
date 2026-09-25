"""Phase 11.5 收尾：静态审核查出的三处联机遗留（游客端）。

三项都是"单机有、游客端没有"的缺口，验证方式也分成两层：

A. **表现载荷的意图**（真引擎 + 真表现桥）：无懈窗口的响应请求以前和"逐目标
   结算"的响应请求长得一模一样，客户端无从分辨，于是给一次无懈询问也牵了
   一根指向箭头。现在房主把 ``reason`` / ``sequential`` 一起下发。
B. **客户端表现层**：只对逐目标结算的请求牵箭头（与本地 FX 同一判据）。
C. **游客端的【铁索连环】**（真 socket + 真引擎 + 真点击）：
   1. 重铸：本地有"连环／重铸"二选一，远程流程里原本没有重铸这条路；
   2. 全员横置：目标判定只拿未横置的当样本，于是"解除横置"的合法用法被误判成
      "现在没有合法目标"，连正常使用都被挡住。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_engine_v2_phase11_5_lan_recast_and_wuxie_arrow``
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.game.controllers.remote import RECAST_ACTION_ID
from src.game.engine import UseCardAction
from src.game.view.presentation import EV_RESPONSE_REQUEST, PresentationBridge
from src.network.decisions import DecisionKind
from tests.legacy_helpers import canonical_card

from tools.lan_playability_sync import UiClient, wait_decision
from tools.lan_view_harness import (
    RECT,
    MatchSession,
    force_hand,
    start_remote_turn,
)


# ==================================================
# 通用工具（沿用 phase11_5 的既有写法）
# ==================================================

def decision_of(client, kind=None):
    """客户端当前**待回答**的请求（已提交 / 等房主确认的不算）。"""

    match = client.match
    if match is None or match.decision is None or match.answered:
        return None
    request = match.decision
    if kind is not None and request.get("kind") != kind:
        return None
    return request


def auto_play_host(session):
    """房主的本地窗口自动收尾：放弃响应 / 选公共牌 / 关掉选择框。"""

    game = session.game
    if game.response.active:
        game.pass_response()
        return True
    if game.choice.active:
        game.choice.choose_no()
        return True
    return False


def pump_until(session, predicate, timeout=8.0):
    """推进对局直到条件成立；期间替房主收尾它自己的本地窗口。"""

    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        auto_play_host(session)
        session.pump(0.1)
    return bool(predicate())


def hand_ids(player):
    return [card.id for card in player.hand]


def option_of(entry, action_id):
    return next((item for item in entry.get("options") or ()
                 if str(item.get("action_id") or "") == action_id), None)


def click_picker_row(client, ui, action_id):
    """在「选择操作」面板里点某一行（与 phase11_5 的多方式测试同一套几何）。"""

    picker = client.renderer.action_picker
    picker.sync_layout(client.renderer.metrics,
                       len(client.scene.view.card_action_picker()))
    options = client.scene.view.card_action_picker()
    row = picker.rects[options.index(
        next(item for item in options if item.action_id == action_id))]
    return ui.click(row.center)


def use_entry_option(entry):
    """这张牌的"正常使用"那一项（重铸之外的用法）。"""

    return next((item for item in entry.get("options") or ()
                 if str(item.get("action_id") or "") != RECAST_ACTION_ID), None)


# ==================================================
# A. 表现载荷带上了请求的意图
# ==================================================

class ResponseRequestIntentTests(unittest.TestCase):
    """无懈询问与逐目标结算，在表现载荷里必须是两种东西。"""

    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1600, 900))

    def tearDown(self):
        pygame.display.quit()

    def make_game(self, ai_count=2):
        game = Game(ai_count=ai_count)
        game.scene = "game"
        game.actions.clear()
        game.engine.reset()
        for player in game.players:
            # 手牌清空 → 无懈链上没有人有牌可出，也不会弹出真人的窗口。
            player.hand = []
            player.hp = player.max_hp
            player.alive = True
        game.phase = "play"
        return game

    def settle(self, game, limit=4000):
        for _ in range(limit):
            if game.response.active:
                game.pass_response()
            elif game.choice.active:
                game.choice.choose_no()
            elif game.pending_request is None and not game.busy:
                break
            game.update(1 / 60)

    def facts_of(self, game, use):
        """跑一次真实动作，收集表现桥下发的全部响应请求载荷。"""

        bridge = PresentationBridge(game, "m1").attach()
        try:
            use(game)
            self.settle(game)
        finally:
            bridge.detach()
        return [dict(fact.base) for fact in bridge.facts
                if fact.kind == EV_RESPONSE_REQUEST]

    def test_wuxie_window_is_marked_as_a_chain_not_a_target(self):
        game = self.make_game(2)
        card = canonical_card("TAOYUAN")
        # 手里有无懈的人才会被问（共享阶段只问真有牌可打的人）。
        game.player.hand = [card, canonical_card("WUXIE")]
        targets = list(game.seats.alive_players_in_order(
            start_after=game.player, include_start=True))
        facts = self.facts_of(
            game, lambda g: g.submit_action(UseCardAction(g.player, card, targets)))

        wuxie = [fact for fact in facts if fact.get("reason") == "wuxie_chain"]
        self.assertTrue(wuxie, "没有采集到无懈窗口的响应请求")
        for fact in wuxie:
            self.assertFalse(fact.get("sequential"),
                             "无懈询问不是逐目标结算，不能带箭头标记")
            self.assertEqual(fact.get("target_id"), "",
                             "共享阶段没有唯一的被问者：不能把「谁有资格」发出去")

    def test_mass_trick_target_requests_stay_sequential(self):
        game = self.make_game(2)
        actor = game.players[1]
        card = canonical_card("NANMAN")
        actor.hand = [card]
        targets = list(game.seats.alive_players_in_order(start_after=actor))
        facts = self.facts_of(
            game, lambda g: g.submit_action(UseCardAction(actor, card, targets)))

        nanman = [fact for fact in facts if fact.get("reason") == "nanman"]
        self.assertTrue(nanman, "没有采集到逐目标响应请求")
        for fact in nanman:
            self.assertTrue(fact.get("sequential"),
                            "逐目标结算的请求必须仍然带箭头标记")


# ==================================================
# B. 客户端表现层：只有逐目标结算才牵箭头
# ==================================================

class ClientWuxieArrowTests(unittest.TestCase):
    """游客端的箭头判据与本地 FX 完全一致。"""

    @classmethod
    def setUpClass(cls):
        pygame.display.init()
        pygame.font.init()

    def setUp(self):
        from src.renderer import Renderer
        from src.ui.client_fx import ClientPresentation
        from src.ui.view_adapter import RemoteGameView, ViewPlayer

        self.screen = pygame.display.set_mode((1600, 1000))
        self.renderer = Renderer(self.screen)
        self.view = RemoteGameView()
        self.view.ui_metrics = self.renderer.metrics
        self.me = ViewPlayer("p1", 0, "我")
        self.other = ViewPlayer("p2", 1, "对手")
        self.view.player = self.me
        self.view.players = [self.me, self.other]
        self.view._players = {"p1": self.me, "p2": self.other}
        self.presentation = ClientPresentation(self.renderer.effects)
        self.presentation.bind_view(self.view)
        self.feed = self.renderer.effects
        self.renderer.draw(self.view)
        self.view.cards.get({"card_id": "card-x", "name": "NANMAN",
                            "label": "南蛮入侵", "category": "trick"})

    def live_arrows(self):
        return [arrow for arrow in self.feed.arrows if not arrow.released]

    def request_event(self, **extra):
        event = {
            "kind": EV_RESPONSE_REQUEST,
            "target_id": "p2", "source_id": "p1",
            "allowed": ["WUXIE"], "prompt": "【南蛮入侵】即将生效：可使用【无懈可击】",
            "card": {"card_id": "card-x"},
        }
        event.update(extra)
        return event

    def test_wuxie_request_draws_no_arrow(self):
        self.presentation.play([self.request_event(
            reason="wuxie_chain", sequential=False)])
        self.assertEqual(self.live_arrows(), [],
                         "无懈询问被画成了一根指向箭头")

    def test_plain_response_request_draws_no_arrow(self):
        """普通【杀】→【闪】：那条箭头已经在 CARD_USED 时牵好了。"""

        self.presentation.play([self.request_event(reason="sha", sequential=False)])
        self.assertEqual(self.live_arrows(), [])

    def test_sequential_target_request_still_draws_the_arrow(self):
        self.presentation.play([self.request_event(
            reason="nanman", sequential=True)])
        arrows = self.live_arrows()
        self.assertEqual(len(arrows), 1, "逐目标结算的箭头不能少")
        self.assertIs(arrows[0].source, self.me)
        self.assertIs(arrows[0].target, self.other)


# ==================================================
# C1. 游客点"重铸"：房主必须真的重铸
# ==================================================

class RemoteRecastTests(unittest.TestCase):
    """重铸不是"多加一个按钮"：房主要收到可点的选项，并把答案变回真实动作。"""

    def test_guest_recasts_tiesuo_through_real_clicks(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            session.fix_hands(["SHA", "TAO"], keep=remote)
            force_hand(game, remote, ["TIESUO", "SHA"])
            start_remote_turn(session.host, pump)

            request = wait_decision(client, pump, DecisionKind.PLAY_PHASE)
            self.assertIsNotNone(request, "游客没有收到出牌阶段请求")
            entry = next((item for item in request["cards"]
                          if item.get("name") == "TIESUO"), None)
            self.assertIsNotNone(entry, "房主没有把【铁索连环】列为可出的牌")
            option = option_of(entry, RECAST_ACTION_ID)
            self.assertIsNotNone(option, "房主没有下发「重铸」这一条用法")
            self.assertEqual(int(option.get("min_targets") or 0), 0)
            self.assertEqual(int(option.get("max_targets") or 0), 0)
            self.assertTrue(option.get("enabled", True))

            # 点手牌 → 弹出「选择操作」（使用 / 重铸）→ 点"重铸"那一行。
            # 这一条用法没有目标，点下去即提交；本地面板在提交后会被清掉
            # （SUBMITTED），所以取证看的是**发回房主的那条回答**。
            card_id = str(entry["card_id"])
            self.assertTrue(ui.click_hand_card(card_id), "点不到【铁索连环】")
            self.assertTrue(client.scene.view.card_action_picker(),
                            "多种用法时没有弹出「选择操作」面板")
            hand_before = hand_ids(remote)
            discarded_before = [card.id for card in game.deck.discard_pile]
            click_picker_row(client, ui, RECAST_ACTION_ID)

            answer = (client.match.last_answer or {}).get("result") or {}
            self.assertEqual(answer.get("action_id"), RECAST_ACTION_ID,
                             "回给房主的方式不是重铸")
            self.assertEqual(list(answer.get("card_ids") or ()), [card_id])

            # 真效果：这张牌进了弃牌堆，并且真的摸了一张（手牌数不变）。
            self.assertTrue(pump_until(
                session,
                lambda: card_id not in hand_ids(remote)
                and card_id in [card.id for card in game.deck.discard_pile],
                timeout=8.0),
                "重铸没有真的发生：手牌 %s / 弃牌堆 %s"
                % (hand_ids(remote),
                   [card.id for card in game.deck.discard_pile][-3:]))
            self.assertNotIn(card_id, discarded_before,
                             "这张牌本来就在弃牌堆里，看不出重铸")
            self.assertEqual(len(remote.hand), len(hand_before),
                             "重铸应当是「出一张摸一张」")
            self.assertTrue(any(card.id not in hand_before for card in remote.hand),
                            "重铸之后没有摸到新牌")
            self.assertFalse(session.host.match.registry.rejected,
                             "房主拒绝了重铸：" + str(
                                 session.host.match.registry.rejected[-2:]))

            # 回合没有停：房主把出牌阶段面板补回来了。
            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.PLAY_PHASE) is not None,
                timeout=8.0), "重铸之后游客拿不回出牌阶段面板")


# ==================================================
# C2. 全员横置：【铁索连环】仍然可用（那是解除横置）
# ==================================================

class AllChainedTiesuoTests(unittest.TestCase):
    """合法目标就是引擎给出的完整集合，不能只挑未横置的当样本。"""

    def test_all_chained_guest_can_still_unchain(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            session.fix_hands(["SHA", "TAO"], keep=remote)
            force_hand(game, remote, ["TIESUO", "SHA"])
            for player in game.players:
                player.chained = True
            start_remote_turn(session.host, pump)

            request = wait_decision(client, pump, DecisionKind.PLAY_PHASE)
            self.assertIsNotNone(request, "游客没有收到出牌阶段请求")
            entry = next((item for item in request["cards"]
                          if item.get("name") == "TIESUO"), None)
            self.assertIsNotNone(entry, "全员横置时【铁索连环】被判成不能出")
            use = use_entry_option(entry)
            self.assertIsNotNone(use, "缺少「正常使用」这一条用法")
            self.assertTrue(use.get("enabled", True),
                            "全员横置时正常使用【铁索连环】被灰掉了："
                            + str(use.get("disabled_reason")))
            targets = [item["player_id"] for item in use.get("targets") or ()]
            self.assertTrue(targets, "全员横置时合法目标被清空了")

            # 真的用出去：点牌 → 选"使用" → 点一个别人 → 确认目标。
            card_id = str(entry["card_id"])
            self.assertTrue(ui.click_hand_card(card_id), "点不到【铁索连环】")
            self.assertTrue(client.scene.view.card_action_picker(),
                            "多种用法时没有弹出「选择操作」面板")
            click_picker_row(client, ui, str(use.get("action_id")))
            self.assertTrue(pump_until(
                session,
                lambda: ui.view.pending_target_selection is not None, timeout=6.0),
                "选完「使用」之后没有进入目标选择")

            victim_id = next((player_id for player_id in targets
                              if player_id != ui.view.player.player_id),
                             targets[0])
            victim = session.host.player(victim_id)
            self.assertTrue(victim.chained, "准备条件：目标应当是横置的")
            ui.click_seat(victim_id)
            ui.click_primary()                       # 确认目标

            self.assertTrue(pump_until(
                session, lambda: not victim.chained, timeout=8.0),
                "【铁索连环】没有结算：目标仍然是横置的")
            self.assertFalse(session.host.match.registry.rejected,
                             "房主拒绝了这次出牌：" + str(
                                 session.host.match.registry.rejected[-2:]))


if __name__ == "__main__":
    unittest.main()
