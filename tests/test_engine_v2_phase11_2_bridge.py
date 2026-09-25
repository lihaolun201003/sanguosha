"""Phase 11.2 tests: remote decisions, authority validation, controller bridge.

这里只放**确定性**、值得长期回归的部分：远程决策的校验规则（越权 / 重复 /
过期 / 数量不符）、联网开局建立的角色映射，以及一条打通的远程决策链
（远程结束阶段 → 房主 TurnFlow 真实推进）。

真实双实例的完整验证在 ``tools/lan_gameplay_bridge.py`` 与
``tools/lan_gameplay_ui.py``。
"""

import os
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from src.game import Game
from src.game.controllers.remote import RemoteHumanController
from src.network.decisions import (
    ACTION_CANCEL,
    ACTION_END_PHASE,
    ACTION_PASS,
    DecisionError,
    DecisionKind,
    DecisionRegistry,
    DecisionRequest,
    DecisionResult,
)
from src.network.session import LanSession
from src.player import ControllerType
from tests.legacy_helpers import normal_sha, shan

FRAME = 1.0 / 60.0
RECT = (0, 0, 10, 10)
MATCH = "MATCH-1"


def make_request(**overrides):
    payload = {
        "match_id": MATCH,
        "request_id": 11,
        "player_id": "p2",
        "kind": DecisionKind.RESPOND_CARD,
        "prompt": "请打出一张【闪】",
        "cards": [{"card_id": "card-1", "name": "SHAN", "label": "闪"}],
        "targets": [{"player_id": "p1", "nickname": "房主"}],
        "constraints": {"min_cards": 1, "max_cards": 1,
                        "min_targets": 0, "max_targets": 0,
                        "allow_pass": True},
    }
    payload.update(overrides)
    return DecisionRequest(**payload)


# ==================================================
# 决策校验（纯数据，不打网络）
# ==================================================

class DecisionRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = DecisionRegistry(MATCH)
        self.pending = self.registry.open(make_request(), local=("pending", object()))

    def reject(self, result, player_id="p2", request_id=11, match_id=MATCH):
        with self.assertRaises(DecisionError) as ctx:
            self.registry.resolve(player_id, request_id, match_id, result)
        return ctx.exception.code

    def test_valid_response_resolves_once(self):
        pending, result = self.registry.resolve(
            "p2", 11, MATCH, DecisionResult(action="submit", card_ids=["card-1"]))
        self.assertIs(pending, self.pending)
        self.assertEqual(result.card_ids, ("card-1",))
        self.assertFalse(pending.open)
        self.assertIsNone(self.registry.current_for("p2"))

    def test_unknown_request_is_rejected(self):
        self.assertEqual(
            self.reject(DecisionResult(action=ACTION_PASS), request_id=999),
            "unknown_decision")

    def test_other_players_request_is_rejected(self):
        self.assertEqual(self.reject(DecisionResult(action=ACTION_PASS), player_id="p1"),
                         "wrong_player")

    def test_stale_match_is_rejected(self):
        self.assertEqual(
            self.reject(DecisionResult(action=ACTION_PASS), match_id="OLD-MATCH"),
            "stale_decision")

    def test_duplicate_response_is_rejected(self):
        self.registry.resolve("p2", 11, MATCH, DecisionResult(action=ACTION_PASS))
        self.assertEqual(self.reject(DecisionResult(action=ACTION_PASS)),
                         "duplicate_answer")

    def test_card_outside_the_allowed_set_is_rejected(self):
        self.assertEqual(
            self.reject(DecisionResult(action="submit", card_ids=["card-999"])),
            "illegal_card")

    def test_target_outside_the_allowed_set_is_rejected(self):
        self.assertEqual(
            self.reject(DecisionResult(action="submit", card_ids=["card-1"],
                                       target_ids=["p9"])),
            "illegal_target")

    def test_duplicate_ids_are_collapsed_so_counts_cannot_be_padded(self):
        """同一个 id 发两次只算一次：不能靠重复凑数量。"""

        registry = DecisionRegistry(MATCH)
        registry.open(make_request(
            request_id=17, constraints={"min_cards": 2, "max_cards": 2,
                                        "allow_pass": True}))
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 17, MATCH, DecisionResult(
                action="submit", card_ids=["card-1", "card-1"]))
        self.assertEqual(ctx.exception.code, "bad_card_count")

    def test_missing_required_cards_is_rejected(self):
        self.assertEqual(self.reject(DecisionResult(action="submit")), "bad_card_count")

    def test_cancel_is_rejected_when_not_allowed(self):
        registry = DecisionRegistry(MATCH)
        registry.open(make_request(request_id=12, constraints={"min_cards": 1, "max_cards": 1}))
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 12, MATCH, DecisionResult(action=ACTION_CANCEL))
        self.assertEqual(ctx.exception.code, "cancel_not_allowed")

    def test_pass_is_rejected_when_the_request_requires_a_choice(self):
        registry = DecisionRegistry(MATCH)
        registry.open(make_request(request_id=13, constraints={"min_cards": 0, "max_cards": 1}))
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 13, MATCH, DecisionResult(action=ACTION_PASS))
        self.assertEqual(ctx.exception.code, "cancel_not_allowed")

    def test_confirm_requires_a_boolean(self):
        registry = DecisionRegistry(MATCH)
        registry.open(make_request(request_id=14, kind=DecisionKind.CONFIRM,
                                   constraints={"allow_pass": True}))
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", 14, MATCH, DecisionResult(action="submit"))
        self.assertEqual(ctx.exception.code, "invalid_option")

    def test_option_must_be_offered(self):
        registry = DecisionRegistry(MATCH)
        registry.open(make_request(
            request_id=15, kind=DecisionKind.CHOOSE_OPTION,
            options=[{"value": 0, "label": "甲"}, {"value": 1, "label": "乙"}],
            constraints={"allow_pass": True}))
        self.assertEqual(
            self.reject_for(registry, DecisionResult(action="submit", option=5), 15),
            "invalid_option")

    def reject_for(self, registry, result, request_id):
        with self.assertRaises(DecisionError) as ctx:
            registry.resolve("p2", request_id, MATCH, result)
        return ctx.exception.code

    def test_one_open_decision_per_player(self):
        self.assertIs(self.registry.current_for("p2"), self.pending)
        self.registry.cancel(11, "replaced")
        self.assertIsNone(self.registry.current_for("p2"))

    def test_cancel_all_invalidates_everything(self):
        self.registry.open(make_request(request_id=16, player_id="p3"))
        self.registry.cancel_all("match_over")
        self.assertFalse(self.registry.waiting)
        self.assertTrue(all(p.status != "open" for p in self.registry.open_requests()))

    def test_disconnect_releases_that_players_decision(self):
        self.registry.cancel_player("p2")
        self.assertIsNone(self.registry.current_for("p2"))
        self.assertFalse(self.registry.waiting)


# ==================================================
# 联网开局：大厅成员 → 权威角色
# ==================================================

class NetworkBattleSetupTests(unittest.TestCase):
    def setUp(self):
        self.game = Game(ai_count=1)
        self.game.open_multiplayer_menu()
        self.seats = [
            ("p1", "房主", 0, ControllerType.HUMAN, "conn-1"),
            ("p2", "玩家A", 1, ControllerType.REMOTE_HUMAN, "conn-2"),
        ]

    def test_roles_carry_controller_type_and_connection(self):
        players = self.game.start_networked_battle(self.seats)
        self.assertEqual([p.player_id for p in players], ["p1", "p2"])
        host, guest = players
        self.assertTrue(host.is_human)
        self.assertIs(guest.controller_type, ControllerType.REMOTE_HUMAN)
        self.assertEqual(guest.connection_id, "conn-2")
        self.assertIs(self.game.player, host)
        self.assertEqual(len(guest.hand), 4)

    def test_remote_player_gets_the_remote_controller_from_the_factory(self):
        marker = {}

        def factory(game, player):
            controller = RemoteHumanController(game, player, bridge=None)
            marker["controller"] = controller
            return controller

        self.game.remote_controller_factory = factory
        self.game.start_networked_battle(self.seats)
        guest = self.game.players[1]
        controller = self.game.get_controller(guest)
        self.assertIs(controller, marker["controller"])
        self.assertTrue(controller.asynchronous)

    def test_without_a_factory_remote_seats_fall_back_to_ai(self):
        """没有网络桥时不能让整局永远等一个不会来的响应。"""

        self.game.start_networked_battle(self.seats)
        controller = self.game.get_controller(self.game.players[1])
        self.assertFalse(getattr(controller, "asynchronous", False))


# ==================================================
# 端到端：远程决策真的驱动了引擎
# ==================================================

class RemoteDecisionFlowTests(unittest.TestCase):
    """房主 + 客户端各一个会话，走真实 socket 与真实 TurnFlow。"""

    def setUp(self):
        self.host = LanSession()
        # 这一组用例验证的是**桥接与决策链**，所以用最小局（自由混战 2 人）
        # 保持"房主 + 一个远程真人"的规模；身份局 + AI 补位的联机开局由
        # tests/test_engine_v2_phase11_4_3_lan_identity_runtime.py 覆盖。
        ok, message = self.host.create_room("房主", 2, 0, game_mode="ffa")
        self.assertTrue(ok, message)
        self.game = Game(ai_count=1)
        self.game.ai_pacing = True
        self.host.set_game(self.game)
        self.client = LanSession()
        ok, message = self.client.join_room("玩家A", "127.0.0.1",
                                            self.host.host.bound_port)
        self.assertTrue(ok, message)
        self.pump(2.0)
        self.client.set_ready(True)
        self.wait(lambda: self.host.lobby.can_start(), 5.0)
        ok, message = self.host.start_match()
        self.assertTrue(ok, message)
        # 联机开局现在是两段：先看身份、再选将（与单机一致）。这一组用例关注
        # 桥接与决策链，所以由脚本替两名真人把这两步点完。
        from tools.lan_view_harness import complete_setup

        self.assertTrue(complete_setup(self.host, [self.client], self.pump, 8.0),
                        "开局流程没有走完")
        self.pump(0.6)

    def tearDown(self):
        self.host.leave()
        self.client.leave()

    # ---- 工具 ----

    def pump(self, seconds=1.0):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.host.poll()
            self.client.poll()
            self.game.update(FRAME)
            time.sleep(FRAME / 2)

    def wait(self, predicate, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.pump(0.05)
        return bool(predicate())

    @property
    def remote(self):
        return next(player for player in self.game.players
                    if player.controller_type is ControllerType.REMOTE_HUMAN)

    def wait_decision(self, kind, timeout=10.0):
        self.wait(lambda: (self.client.match.decision or {}).get("kind") == kind, timeout)
        return self.client.match.decision

    def end_host_turn(self):
        """房主（真人）结束自己的出牌阶段，含弃牌。"""

        self.game.end_player_turn(self.game.player)
        guard = 0
        while (self.game.phase == "discard"
               and self.game.current_turn_player is self.game.player and guard < 20):
            self.game.player_discard(0, RECT)
            self.pump(0.3)
            guard += 1

    # ---- 用例 ----

    def test_setup_maps_lobby_members_onto_authoritative_players(self):
        self.assertIsNotNone(self.host.match)
        self.assertEqual(len(self.game.players), 2)
        self.assertIs(self.remote.controller_type, ControllerType.REMOTE_HUMAN)
        self.assertEqual(self.remote.connection_id, self.remote.player_id)
        self.assertTrue(self.client.match.ready)
        self.assertEqual(len(self.client.match.hand), len(self.remote.hand))

    def test_remote_player_ends_the_phase_and_the_turn_really_advances(self):
        self.end_host_turn()
        self.assertTrue(self.wait(
            lambda: self.game.current_turn_player is self.remote, 8.0))
        request = self.wait_decision(DecisionKind.PLAY_PHASE)
        self.assertIsNotNone(request, "远程玩家没有收到出牌阶段请求")
        self.assertTrue(self.game.waiting_for_remote)

        self.client.match.answer(DecisionResult(action=ACTION_END_PHASE))

        # 手牌超过上限时会先进入弃牌阶段（另一条真实交互链）
        discard = self.wait_decision(DecisionKind.SELECT_CARDS, 6.0)
        if discard is not None:
            need = int(discard["constraints"]["min_cards"])
            self.client.match.answer(DecisionResult(
                action="submit", card_ids=[c["card_id"] for c in discard["cards"][:need]]))

        self.assertTrue(self.wait(
            lambda: self.game.current_turn_player is not self.remote, 12.0),
            "远程结束阶段后房主没有推进回合")
        self.assertNotIn("（回合守卫）", " ".join(self.game.game_log))

    def test_remote_player_plays_a_sha_through_the_bridge(self):
        self.remote.hand = [normal_sha(), shan()]
        self.remote.clear_turn_state()
        self.game.phase = "play"
        self.game.current_turn_player = self.remote
        controller = self.game.get_controller(self.remote)
        controller._turn = None
        controller.take_turn(lambda: None)

        request = self.wait_decision(DecisionKind.PLAY_PHASE)
        self.assertIsNotNone(request)
        sha = next(card for card in request["cards"] if card["name"] == "SHA")
        self.assertEqual(len(sha["targets"]), 1)

        before = len(self.remote.hand)
        self.client.match.answer(DecisionResult(
            action="submit", card_ids=[sha["card_id"]],
            target_ids=[sha["targets"][0]["player_id"]]))

        self.assertTrue(self.wait(lambda: len(self.remote.hand) < before, 10.0),
                        "房主没有执行远程出牌")
        # 杀进入了结算：房主（目标）需要响应
        self.assertTrue(self.game.response.active or self.game.engine.pending.active
                        or self.game.processing_zone or self.game.deck.discard_pile)

    def test_illegal_response_is_rejected_without_changing_state(self):
        self.remote.hand = [shan()]
        self.remote.clear_turn_state()
        self.game.current_turn_player = self.game.player
        self.game.phase = "play"
        self.game.player.hand = [normal_sha()]
        self.game.player.sha_used = False
        self.game.player_use_card(0, RECT)

        request = self.wait_decision(DecisionKind.RESPOND_CARD)
        self.assertIsNotNone(request, "远程玩家没有收到响应请求")
        before_hp, before_hand = self.remote.hp, len(self.remote.hand)

        self.client.send_to_host("DECISION_RESPONSE", match_id=self.client.match.match_id,
                                 request_id=request["request_id"],
                                 player_id=self.client.match.my_player_id,
                                 result={"action": "submit", "card_ids": ["card-nope"]})
        self.pump(0.8)
        self.assertTrue(any(item["code"] == "illegal_card"
                            for item in self.host.match.registry.rejected))
        self.assertEqual(self.remote.hp, before_hp)
        self.assertEqual(len(self.remote.hand), before_hand)
        self.assertEqual(self.host.match.aborted, "")

    def test_client_disconnect_releases_the_pending_decision(self):
        self.remote.hand = [shan()]
        self.remote.clear_turn_state()
        self.game.current_turn_player = self.game.player
        self.game.phase = "play"
        self.game.player.hand = [normal_sha()]
        self.game.player.sha_used = False
        self.game.player_use_card(0, RECT)
        self.assertTrue(self.wait(lambda: self.game.waiting_for_remote, 8.0))

        self.client.leave()
        self.assertTrue(self.wait(lambda: self.host.match.aborted != "", 10.0))
        self.assertFalse(self.host.match.registry.waiting)
        self.assertFalse(self.game.waiting_for_remote)


if __name__ == "__main__":
    unittest.main()
