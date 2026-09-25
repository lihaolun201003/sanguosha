"""Phase 11.5：局域网游客端的完整可玩性 / 状态稳定 / 动画修复回归。

这一份测试**只验证真实效果**，不验证"界面出现过了"：

* 五谷丰登：游客在公共区**点一张牌**，这张牌必须真的进他的手牌、从公共池
  消失、流程继续到结束（旧验收只看"收到过 SELECT_CARDS"，所以漏掉了
  公共牌被当成装备牌、点击无效的那个 P1 bug）；
* 多种用法：游客点了第二种用法，房主收到的必须是那一种（action_id）；
* 多来源响应：两张手牌当【闪】打出时，选第一张**不能**提交，选满两张才提交；
* 提交 ≠ 接受：房主拒绝一条回答时，客户端必须拿回可操作的面板；
* 房主没能把答案落到游戏上时（resolve False），决策不能被错误关闭；
* 结算页的「返回主菜单」可用、「重新开始」由房主决定；
* 动画：飞牌永远是**一张牌**的大小（不是整个手牌区）、同一段移动只登记一次、
  摸牌从牌堆起飞、响应牌落在响应位、弃牌堆封面永远是卡背。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_engine_v2_phase11_5_lan_client_full_fix``
"""

import time
import unittest

import pygame

from src.network.decisions import (
    ACTION_END_PHASE,
    ACTION_PASS,
    DecisionKind,
    DecisionResult,
)
from src.network.protocol import MessageType
from src.player import ControllerType

from tools.lan_playability_sync import UiClient
from tools.lan_view_harness import (
    RECT,
    MatchSession,
    force_hand,
    take_card,
)


# ==================================================
# 通用工具
# ==================================================

def decision_of(client, kind=None):
    match = client.match
    if match is None or match.decision is None:
        return None
    request = match.decision
    if kind is not None and request.get("kind") != kind:
        return None
    return request


def auto_play_host(session):
    """房主的本地窗口自动收尾：公共区选第一张 / 放弃响应 / 结束回合。"""

    game = session.game
    if game.pending_selection is not None:
        owner = game.pending_selection.get("owner")
        if owner is game.player:
            entries = game.selection_pool_entries() or [
                (card, None) for card in game.public_card_pool]
            if entries:
                game.select_pending_card(entries[0][0], RECT, key=entries[0][1])
                return True
    if game.response.active:
        game.pass_response()
        return True
    if game.choice.active:
        game.choice.choose_no()
        return True
    return False


def pump_until(session, predicate, timeout=8.0):
    """推进对局直到条件成立；期间替房主收尾它自己的本地窗口。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        auto_play_host(session)
        session.pump(0.1)
    return bool(predicate())


def pool_ids(game):
    return [str(card.id) for card in game.public_card_pool]


def hand_ids(player):
    return [str(card.id) for card in player.hand]


# ==================================================
# 1. 五谷丰登：游客真的拿到公共牌
# ==================================================

class WuguGuestSelectionTests(unittest.TestCase):
    """P1：公共牌是 public_pool，不是"我的装备牌"。"""

    def test_guest_takes_a_public_card_through_a_real_click(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)
            self.assertEqual(remote.controller_type, ControllerType.REMOTE_HUMAN)

            # 确定性：别人手里没有【无懈可击】，否则无懈链会停下来等回答。
            session.fix_hands(["SHA", "TAO"], keep=remote)
            force_hand(game, remote, ["SHAN", "TAO"])
            force_hand(game, game.player, ["WUGU", "SHA", "TAO"])
            game.current_turn_player = game.player
            game.phase = "play"
            index = next(i for i, card in enumerate(game.player.hand)
                         if card.name == "WUGU")
            game.player_use_card(index, RECT)
            pump(0.6)

            initial_pool = pool_ids(game)
            self.assertTrue(initial_pool, "五谷丰登没有开出公共牌池")

            # 等游客收到选牌请求，并且候选**明确标成公共池**。
            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.SELECT_CARDS) is not None,
                timeout=10.0), "游客没有收到五谷的选牌请求")
            request = decision_of(client, DecisionKind.SELECT_CARDS)
            zones = {str(item.get("zone")) for item in request.get("cards") or ()}
            self.assertEqual(zones, {"public_pool"},
                             "五谷的候选必须标成公共池，实际是 " + str(zones))

            # 等轮到游客取牌（房主先取）。
            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.SELECT_CARDS) is not None
                and len(pool_ids(game)) < len(initial_pool),
                timeout=10.0), "房主取牌之后没有轮到游客")
            request = decision_of(client, DecisionKind.SELECT_CARDS)
            entry = next(item for item in request["cards"]
                         if item.get("zone") == "public_pool")
            card_id = str(entry["card_id"])
            pool_before = pool_ids(game)
            hand_before = hand_ids(remote)
            self.assertIn(card_id, pool_before, "候选牌不在公共池里")

            # **真实点击**公共区的那张牌。
            self.assertTrue(ui.click_pool_card(card_id),
                            "公共区的牌点不到（几何与绘制不一致）")
            self.assertTrue(pump_until(
                session,
                lambda: card_id in hand_ids(remote) and card_id not in pool_ids(game),
                timeout=8.0),
                "点了公共牌之后没有真的拿到（手牌 %s / 池 %s）"
                % (hand_ids(remote), pool_ids(game)))

            self.assertNotIn(card_id, pool_ids(game), "公共池没有减少")
            self.assertEqual(len(pool_ids(game)), len(pool_before) - 1)
            self.assertNotIn(card_id, hand_before)
            self.assertIn(card_id, hand_ids(remote), "卡牌没有进入游客手牌")
            self.assertFalse(session.host.match.registry.rejected,
                             "房主拒绝了五谷的响应：" + str(
                                 session.host.match.registry.rejected))
            # 流程继续：池子取空之后五谷结束，不再有选牌请求。
            self.assertTrue(pump_until(
                session,
                lambda: not pool_ids(game)
                and decision_of(client, DecisionKind.SELECT_CARDS) is None,
                timeout=10.0), "五谷没有正常结束（流程卡住）")


# ==================================================
# 2. 一张牌多种用法：游客选第二种
# ==================================================

class WayChoiceTests(unittest.TestCase):
    """点第二种用法，房主收到的必须是第二种（而不是默默用第一种）。"""

    def test_guest_can_pick_the_second_way(self):
        from src.game.skills.conversion_probes import (
            PAIR_PROBE_ID,
            bind_conversion_probes,
        )

        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "SHA", "TAO"])
            # 双刃：任意两张手牌当【杀】使用（多来源转化的标准场景）。
            bind_conversion_probes(game, remote, PAIR_PROBE_ID)
            for player in game.players:
                player.clear_turn_state()
                player.sha_used = False
            game.skipped_phases = set()
            game.phase = "play"
            game.current_turn_player = remote
            game.message = remote.name + " 的出牌阶段"
            controller = game.get_controller(remote)
            controller._turn = None
            controller._idle_retries = 0
            controller.take_turn(lambda: None)
            pump(0.6)

            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.PLAY_PHASE) is not None),
                "游客没有收到出牌阶段请求")
            request = decision_of(client, DecisionKind.PLAY_PHASE)
            entry = next(item for item in request["cards"]
                         if len(item.get("options") or ()) > 1)
            self.assertEqual(len(entry["options"]), 2,
                             "这张牌应当有两种用法（普通使用 / 双刃）")
            second = entry["options"][1]
            self.assertEqual(second.get("skill_id"), PAIR_PROBE_ID)
            self.assertEqual(int(second.get("min_sources") or 1), 2)

            # 点第一张牌 → 弹出"选择操作"面板（与单机同一个面板）。
            first_id = str(entry["card_id"])
            self.assertTrue(ui.click_hand_card(first_id), "点不到第一张牌")
            self.assertTrue(client.scene.view.card_action_picker(),
                            "多种用法时没有弹出「选择操作」面板")
            self.assertFalse(client.match.answered, "选了牌就提交了（不该发生）")

            # 点第二种用法。
            picker = client.renderer.action_picker
            picker.sync_layout(client.renderer.metrics,
                               len(client.scene.view.card_action_picker()))
            options = client.scene.view.card_action_picker()
            target_row = picker.rects[options.index(
                next(item for item in options
                     if item.action_id == second["action_id"]))]
            ui.click(target_row.center)
            self.assertEqual(client.scene.decision.action_id,
                             second["action_id"], "没有记下第二种用法")
            self.assertTrue(client.scene.decision.way_chosen)

            # 再选第二张实体牌（多来源）→ 来源凑齐之后进入目标选择。
            others = [card.id for card in ui.view.player.hand
                      if card is not None and card.id != first_id]
            self.assertTrue(others, "手里没有第二张牌")
            ui.click_hand_card(others[0])
            self.assertFalse(client.match.answered,
                             "来源凑齐但目标还没选就提交了")
            self.assertTrue(pump_until(
                session,
                lambda: ui.view.pending_target_selection is not None,
                timeout=6.0), "选满两张来源之后没有进入目标选择")
            selection = ui.view.pending_target_selection
            self.assertEqual(selection["maximum"], 1)
            ui.click_seat(selection["candidates"][0].player_id)
            self.assertTrue(pump_until(
                session,
                lambda: client.match.answered or client.match.waiting_ack
                or client.match.decision is None, timeout=6.0),
                "选了目标之后仍然没有提交")

            answer = (client.match.last_answer or {}).get("result") or {}
            self.assertEqual(answer.get("skill_id"), PAIR_PROBE_ID,
                             "提交的方式不是玩家点的那一种")
            self.assertEqual(answer.get("action_id"), second["action_id"])
            self.assertEqual(len(answer.get("card_ids") or ()), 2,
                             "多来源方式应当提交两张实体牌")
            self.assertTrue(pump_until(
                session,
                lambda: not session.host.match.registry.open_requests()
                and not session.host.match.registry.rejected, timeout=8.0),
                "房主没有接受这次出牌：" + str(
                    session.host.match.registry.rejected))


# ==================================================
# 3. CHOOSE_OPTION：1 个选项 / N 个选项
# ==================================================

class FakeMatch:
    """只实现 ``answer`` 的最小替身（选项面板不碰网络）。"""

    def __init__(self):
        self.answers = []
        self.ready = True
        self.answered = False
        self.decision = None
        self.view = None
        self.aborted = ""
        self.reject_reason = ""
        self.rejected = False

    def answer(self, result):
        self.answers.append(result)
        self.answered = True
        return True


class ChoiceOptionTests(unittest.TestCase):
    """选择框必须支持任意数量的选项，而且点了就真的提交。"""

    @classmethod
    def setUpClass(cls):
        pygame.display.init()
        pygame.font.init()

    def _scene(self):
        from src.renderer import Renderer
        from src.ui.remote_table import RemoteTableScene

        screen = pygame.display.set_mode((1600, 1000))
        renderer = Renderer(screen)
        scene = RemoteTableScene(screen, renderer)
        scene.sync_layout(renderer.metrics)
        return scene

    def _click(self, scene, match, rect):
        event = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN,
            {"pos": (int(rect.centerx), int(rect.centery)), "button": 1})
        scene.handle_event(event, match)

    def test_single_option_can_be_submitted(self):
        scene = self._scene()
        match = FakeMatch()
        request = {
            "request_id": 501, "kind": DecisionKind.CHOOSE_OPTION,
            "prompt": "【雌雄双股剑】：弃一张手牌，或令对方摸一张",
            "options": [{"value": "draw", "label": "draw"}],
            "constraints": {"allow_cancel": False},
        }
        match.decision = request
        scene._sync_overlay(match)
        self.assertTrue(scene.choice.active,
                        "只有一个选项时也要给出可点的选择（不能静默清掉）")
        scene.choice_overlay.sync_layout(scene.renderer.metrics)
        self._click(scene, match, scene.choice_overlay.yes_rect)
        self.assertEqual([result.option for result in match.answers], ["draw"],
                         "点了唯一选项之后必须真的提交")

    def test_two_options_submit_the_clicked_one(self):
        scene = self._scene()
        match = FakeMatch()
        request = {
            "request_id": 502, "kind": DecisionKind.CHOOSE_OPTION,
            "prompt": "【雌雄双股剑】", "constraints": {"allow_cancel": False},
            "options": [{"value": "discard", "label": "弃一张手牌"},
                        {"value": "draw", "label": "令对方摸一张"}],
        }
        match.decision = request
        scene._sync_overlay(match)
        scene.choice_overlay.sync_layout(scene.renderer.metrics)
        self._click(scene, match, scene.choice_overlay.no_rect)
        self.assertEqual([result.option for result in match.answers], ["draw"])

    def test_many_options_are_all_clickable(self):
        scene = self._scene()
        match = FakeMatch()
        request = {
            "request_id": 503, "kind": DecisionKind.CHOOSE_OPTION,
            "prompt": "多选一", "constraints": {"allow_cancel": False},
            "options": [{"value": index, "label": "选项%d" % index}
                        for index in (0, 1, 2, 3)],
        }
        match.decision = request
        scene._sync_overlay(match)
        scene.choice_overlay.sync_layout(scene.renderer.metrics, 4)
        self.assertEqual(len(scene.choice_overlay.option_rects), 4)
        self._click(scene, match, scene.choice_overlay.option_rects[2])
        self.assertEqual([result.option for result in match.answers], [2],
                         "点第三个选项应当提交第三个值")


# ==================================================
# 4. 多来源响应：两张手牌当【闪】
# ==================================================

class MultiSourceResponseTests(unittest.TestCase):
    """选第一张不能提交；选满两张才提交，房主按两张实体牌结算。"""

    def test_two_card_response_needs_both_cards(self):
        from src.game.skills.conversion_probes import (
            PAIR_SHAN_PROBE_ID,
            bind_conversion_probes,
        )

        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHA", "TAO"])
            bind_conversion_probes(game, remote, PAIR_SHAN_PROBE_ID)
            force_hand(game, game.player, ["SHA", "TAO"])
            for player in game.players:
                player.clear_turn_state()
                player.sha_used = False
            game.current_turn_player = game.player
            game.phase = "play"
            index = next(i for i, card in enumerate(game.player.hand)
                         if card.name == "SHA")
            game.player_use_card(index, RECT)
            pump(0.8)

            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.RESPOND_CARD) is not None,
                timeout=10.0), "游客没有收到【杀】的响应请求")
            request = decision_of(client, DecisionKind.RESPOND_CARD)
            self.assertEqual(int(request["constraints"]["max_cards"]), 2,
                             "响应窗口必须允许两张实体牌（多来源转化）")
            entries = [item for item in request["cards"]
                       if (item.get("options") or ())]
            self.assertTrue(entries, "响应窗口没有给出任何可用方式")
            self.assertTrue(all(
                int((item["options"][0]).get("min_sources") or 1) == 2
                for item in entries),
                "多来源方式应当明确要求两张来源牌")

            first, second = entries[0], entries[1]
            hp_before = remote.hp
            hand_before = hand_ids(remote)

            self.assertTrue(ui.click_hand_card(str(first["card_id"])),
                            "点不到第一张响应牌")
            pump(0.3)
            self.assertFalse(client.match.answered,
                             "只选了一张就提交了（多来源响应被截断）")

            self.assertTrue(ui.click_hand_card(str(second["card_id"])),
                            "点不到第二张响应牌")
            self.assertTrue(pump_until(
                session,
                lambda: client.match.decision is None
                or client.match.waiting_ack, timeout=8.0),
                "选满两张之后没有提交")
            answer = (client.match.last_answer or {}).get("result") or {}
            self.assertEqual(len(answer.get("card_ids") or ()), 2,
                             "应当提交两张实体牌")
            self.assertEqual(answer.get("skill_id"), PAIR_SHAN_PROBE_ID)
            self.assertTrue(pump_until(
                session,
                lambda: not session.host.match.registry.rejected, timeout=4.0),
                "房主拒绝了合法响应：" + str(session.host.match.registry.rejected))
            pump(0.6)
            for card_id in (str(first["card_id"]), str(second["card_id"])):
                self.assertNotIn(card_id, hand_ids(remote),
                                 "响应牌没有离开手牌：" + card_id)
            self.assertEqual(remote.hp, hp_before, "两张手牌当【闪】应当闪掉这张杀")
            self.assertNotEqual(hand_before, hand_ids(remote))


# ==================================================
# 5 / 6. 提交 ≠ 接受：拒绝之后客户端必须能重来
# ==================================================

class RejectRecoveryTests(unittest.TestCase):
    def test_illegal_answer_is_rejected_and_the_client_can_retry(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)

            session.fix_hands(["TAO"], keep=remote)
            force_hand(game, remote, ["SHAN", "TAO"])
            force_hand(game, game.player, ["SHA", "TAO"])
            game.current_turn_player = game.player
            game.phase = "play"
            index = next(i for i, card in enumerate(game.player.hand)
                         if card.name == "SHA")
            game.player_use_card(index, RECT)
            pump(0.6)

            self.assertTrue(pump_until(
                session,
                lambda: decision_of(client, DecisionKind.RESPOND_CARD) is not None,
                timeout=10.0), "游客没有收到响应请求")
            request = decision_of(client, DecisionKind.RESPOND_CARD)
            request_id = request["request_id"]

            # 故意发一条**不存在的牌**（模拟过期 / 篡改的答案）。
            client.session.send_to_host(
                "DECISION_RESPONSE", match_id=client.match.match_id,
                request_id=request_id, player_id=client.match.my_player_id,
                result={"action": "submit", "card_ids": ["card-nope"]})

            self.assertTrue(pump_until(
                session,
                lambda: bool(session.host.match.registry.rejected), timeout=6.0),
                "房主没有记录任何拒绝")
            rejected = session.host.match.registry.rejected[-1]
            self.assertEqual(rejected["code"], "illegal_card",
                             "拒绝原因应当区分得出「牌不合法」（实际 %s）"
                             % rejected["code"])
            self.assertTrue(session.host.match.registry.current_for(
                remote.player_id) is not None,
                "被拒绝之后房主必须保留这条决策（否则游客无处可答）")

            # 客户端：面板必须回来，可以直接重答。
            self.assertTrue(pump_until(
                session,
                lambda: client.match.decision is not None
                and not client.match.answered, timeout=6.0),
                "客户端被拒绝之后没有拿回可操作的面板")
            self.assertEqual(client.match.reject_code, "illegal_card")
            self.assertIn("重新选择", client.match.reject_reason,
                          "客户端要显示一行能看懂的中文原因")
            self.assertEqual(client.match.reject_detail, "选择的牌不在允许范围内",
                             "房主的具体说明要留在 detail 里（日志用）")

            # 重答一次**合法**的：出【闪】。
            shan = next(item for item in client.match.decision["cards"]
                        if item.get("name") == "SHAN")
            self.assertTrue(client.match.answer(DecisionResult(
                action="submit", card_ids=[shan["card_id"]])))
            self.assertTrue(pump_until(
                session,
                lambda: not session.host.match.registry.open_requests(),
                timeout=8.0), "重答之后房主没有接受")
            self.assertNotIn(str(shan["card_id"]), hand_ids(remote),
                             "重答的【闪】没有被真正打出")

    def test_resolve_false_keeps_the_decision_open(self):
        """房主没把答案落到游戏上时，决策**不能**被标记为完成。"""

        from src.network.decisions import DecisionRegistry, DecisionRequest

        registry = DecisionRegistry("M1")
        request = DecisionRequest(
            match_id="M1", request_id=7, player_id="p2",
            kind=DecisionKind.SELECT_CARDS, prompt="选牌",
            cards=[{"card_id": "card-1"}],
            constraints={"min_cards": 1, "max_cards": 1, "allow_pass": True})
        pending = registry.open(request, local=("pending", object()))
        registry.note_answer("p2", 7)
        # 模拟"协议校验通过、但房主没能把它落到游戏上"。
        registry.reject("p2", 7, _Error("operation_failed"))
        self.assertTrue(pending.open, "失败的提交不能关闭决策")
        self.assertIsNotNone(registry.current_for("p2"))
        # 玩家重来：这一次成功。
        registry.resolve("p2", 7, "M1",
                         DecisionResult(action="submit", card_ids=["card-1"]))
        self.assertFalse(pending.open)
        self.assertIsNone(registry.current_for("p2"))


class _Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


# ==================================================
# 7. 结算页：返回主菜单 / 重新开始
# ==================================================

class ResultOverlayTests(unittest.TestCase):
    def test_menu_leaves_and_restart_goes_through_the_host(self):
        with MatchSession(client_count=1) as session:
            game, pump = session.game, session.pump
            remote = session.remote()
            client = session.client_of(remote)
            ui = UiClient(client)

            game.game_over = True
            game.message = "对局结束"
            pump(0.8)
            self.assertTrue(pump_until(
                session, lambda: client.match.finished, timeout=8.0),
                "客户端没有收到结算")

            overlay = client.renderer.result_overlay
            overlay.layout(client.renderer.metrics)

            # 「重新开始」：只发请求，重开由房主决定。
            restart_rect = overlay.restart_rect()
            event = pygame.event.Event(
                pygame.MOUSEBUTTONDOWN,
                {"pos": restart_rect.center, "button": 1})
            client.scene.handle_event(event, client.match)
            self.assertTrue(pump_until(
                session,
                lambda: str(remote.player_id) in session.host.match.restart_votes,
                timeout=6.0), "房主的重开请求投票没有收到")
            self.assertTrue(client.match.restart_requested)

            # 「返回主菜单」：离开这一局（不影响房主仍在线）。
            menu_rect = overlay.main_menu_rect()
            event = pygame.event.Event(
                pygame.MOUSEBUTTONDOWN, {"pos": menu_rect.center, "button": 1})
            action = client.scene.handle_event(event, client.match)
            self.assertEqual(action, "leave", "结算页的返回主菜单必须真的离开")


# ==================================================
# 8～12. 动画几何与去重
# ==================================================

class AnimationGeometryTests(unittest.TestCase):
    """飞牌动画必须用**一张牌**的尺寸，落点也不能全塞进中央。"""

    @classmethod
    def setUpClass(cls):
        pygame.display.init()
        pygame.font.init()

    def setUp(self):
        from src.renderer import Renderer
        from src.ui.view_adapter import RemoteGameView

        self.screen = pygame.display.set_mode((1600, 1000))
        self.renderer = Renderer(self.screen)
        self.metrics = self.renderer.metrics
        self.view = RemoteGameView()
        self.view.ui_metrics = self.metrics
        self.feed = self.renderer.effects
        from src.ui.client_fx import ClientPresentation

        self.presentation = ClientPresentation(self.renderer.effects)
        self.presentation.bind_view(self.view)
        # 表现层需要一份"当前布局"才能算落点（真实主循环里由 Renderer 写入）。
        self.renderer.table_layout = None

    def _layout(self):
        from src.ui.layout import TableLayout

        layout = TableLayout(self.view, self.metrics)
        self.renderer.effects.set_layout(layout)
        self.view.ui_rects = self.metrics.animation_rects()
        return layout

    def test_zone_rect_is_one_card_wide(self):
        from src.ui.view_adapter import ViewPlayer

        layout = self._layout()
        me = ViewPlayer("p1", 0, "我")
        other = ViewPlayer("p2", 1, "对手")
        self.view.player = me
        self.view.players = [me, other]
        self.view._players = {"p1": me, "p2": other}
        layout.game = self.view

        width, height = self.metrics.hand_card_size()
        hand = self.presentation.zone_rect("hand", me)
        self.assertIsNotNone(hand)
        self.assertLessEqual(hand[2], width,
                             "手牌区域只提供锚点：飞牌宽度不能是整个手牌区")
        seat = self.presentation.zone_rect("hand", other)
        self.assertLessEqual(seat[2], width, "别人座位的飞牌也必须是单张大小")

        response = self.presentation.zone_rect("response_card", me)
        self.assertEqual(tuple(response), tuple(self.metrics.response_card_rect),
                         "响应牌必须落在响应牌位（不是中央出牌位）")

    def test_draw_origin_is_the_draw_pile(self):
        from src.ui.view_adapter import ViewPlayer

        layout = self._layout()
        me = ViewPlayer("p1", 0, "我")
        self.view.player = me
        self.view._players = {"p1": me}
        layout.game = self.view
        self.view.deck.draw_count = 30
        self.view.deck.draw_pile[:] = [None] * 30

        origin = self.feed._origin_key(self.view.deck.draw_pile)
        self.assertIsNotNone(origin)
        point = self.feed._origin_point(origin)
        expected = self.metrics.to_screen(
            __import__("src.ui.layout", fromlist=["DRAW_PILE_RECT"]).DRAW_PILE_RECT
        ).center
        self.assertEqual(point, expected, "摸牌动画必须从牌堆起飞")

    def test_zone_helpers_pass_the_zone_object(self):
        """``_origin_key`` 的接口约定：传**区域对象**，不是它的 id。"""

        from src.ui import client_fx

        layout = self._layout()
        self.view.deck.draw_count = 3
        self.view.deck.draw_pile[:] = [None] * 3
        key = client_fx._origin_key(self.presentation, "draw_pile", None)
        self.assertIs(key, self.view.deck.draw_pile,
                      "区域对象必须原样传给 FX（它自己取 id 匹配）")

    def _fake_card(self, card_id="card-x", name="SHA"):
        from src.card import Card

        card = Card(name=name, category="basic", color=(200, 200, 200))
        card.id = card_id
        self.renderer.draw(self.view)
        return self.view.cards.get({"card_id": card_id, "name": name,
                                   "label": name, "category": "basic"})

    def test_one_move_is_animated_once(self):
        """同一次出牌：cards_moved(→处置区) 与 card_used 不能各飞一次。"""

        from src.ui.view_adapter import ViewPlayer

        layout = self._layout()
        me = ViewPlayer("p1", 0, "我")
        self.view.player = me
        self.view._players = {"p1": me, "p2": ViewPlayer("p2", 1, "对手")}
        layout.game = self.view
        card = self._fake_card()
        self.presentation.queue.clear()
        before = self.presentation.duplicate_flights
        self.presentation.play([
            {"kind": "cards_moved", "cards": [{"card_id": "card-x"}],
             "from_zone": "hand", "from_player_id": "p1",
             "to_zone": "processing", "to_player_id": "", "reason": "play"},
            {"kind": "card_used", "actor_id": "p1", "target_ids": ["p2"],
             "card": {"card_id": "card-x"}, "label": "杀"},
        ])
        queued = [item for item in list(self.presentation.queue.queue)
                  if getattr(item, "card", None) is card]
        self.assertEqual(len(queued), 1,
                         "同一段移动被登记了两次（白影/重复飞行）")
        self.assertIsNotNone(card)

    def test_same_card_to_same_place_is_deduped(self):
        from src.ui.view_adapter import ViewPlayer

        layout = self._layout()
        me = ViewPlayer("p1", 0, "我")
        self.view.player = me
        self.view._players = {"p1": me}
        layout.game = self.view
        card = self._fake_card()
        self.presentation.queue.clear()
        start = self.presentation.zone_rect("hand", me)
        end = self.presentation.zone_rect("discard_pile", me)
        self.assertIsNotNone(self.presentation.fly(card, start, end))
        self.assertIsNone(self.presentation.fly(card, start, end),
                          "同一张牌飞向同一落位不应重复登记")
        self.assertEqual(self.presentation.duplicate_flights, 1)

    def test_discard_cover_is_always_a_card_back(self):
        from src.ui import cards as card_draw
        from src.ui import table as table_module

        pile = []
        for index in range(3):
            pile.append(self._fake_card("card-%d" % index))
        self.view.deck.discard_pile[:] = pile
        self.view.deck.discard_count = len(pile)
        self.view.deck.draw_pile[:] = [None] * 5
        self.view.deck.draw_count = 5

        calls = {"back": 0, "face": 0}
        original_back = card_draw.draw_card_back
        original_face = card_draw.draw_card

        def back(surface, rect, **kwargs):
            calls["back"] += 1
            return original_back(surface, rect, **kwargs)

        def face(surface, card, rect, fonts, **kwargs):
            calls["face"] += 1
            return original_face(surface, card, rect, fonts, **kwargs)

        card_draw.draw_card_back = back
        card_draw.draw_card = face
        try:
            table_module.draw_piles(self.screen, self.view, self.metrics)
        finally:
            card_draw.draw_card_back = original_back
            card_draw.draw_card = original_face

        discard_rect = self.metrics.to_screen(
            __import__("src.ui.layout", fromlist=["DISCARD_PILE_RECT"]).DISCARD_PILE_RECT)
        self.assertEqual(calls["back"], 2,
                         "牌堆与弃牌堆的封面都应当是卡背（实际 %d 次）"
                         % calls["back"])
        self.assertEqual(calls["face"], 0,
                         "弃牌堆常驻封面不该画最顶弃牌的牌面")

        # 悬停时才展示最近弃牌（详情查看，不影响常驻封面）。
        calls["face"] = 0
        card_draw.draw_card = face
        try:
            table_module.draw_piles(self.screen, self.view, self.metrics,
                                    hover="discard")
        finally:
            card_draw.draw_card = original_face
        self.assertEqual(calls["face"], 1, "悬停预览应当显示最近弃牌")
        self.assertTrue(discard_rect.width > 0)


if __name__ == "__main__":
    unittest.main()
