"""Phase 14.1：视图一致性**检查工具本身**的负向测试。

Phase 14 的工具存在四处"假通过"：检查器把违规数据自己抹掉、可操作性只看
回答者、模块开关对已建对局无效、比对不等版本。这个文件把每一处都钉住：
**先构造能让旧实现通过的输入，再断言修好后的工具确实拒绝它**。

允许的做法：构造违规**输入**（一份把对手手牌发过来的视图载荷、一个面板缺失
的客户端状态、一个落后版本）。不允许的做法：mock 掉规则结算或网络链路——
真实场景测试（``tests/test_view_consistency_lan.py``）走的是真实 socket、
真引擎、真 Renderer，一个都没少。

运行：``SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest \\
        tests.test_view_consistency_tooling``
"""

import os
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from src.game import invariants
from src.game.flows.damage import DamageContext, DamageFlow
from src.game.invariants import CardOwnershipError
from src.game.view.view_model import ViewCard, face_down_card
from src.network.protocol import MessageType
from tests.legacy_helpers import make_test_game, normal_sha, tao

from tools.lan_playability_sync import UiClient, answer_via_ui
from tools.lan_view_harness import (
    FRAME,
    MatchSession,
    force_hand,
    give_general,
    wait_for,
)
from tools.view_consistency import (
    FACE_BLANK,
    FACE_REAL,
    FACE_TOKEN,
    STATE_ANSWERED,
    STATE_CLOSED,
    STATE_COVERED,
    STATE_IDLE,
    STATE_OPERABLE,
    STATE_WAITING,
    STATE_WAITING_VIEW,
    STATE_WRONG_PLAYER,
    assert_hidden,
    card_face_kind,
    client_checkpoint,
    client_state,
    compare_at_checkpoint,
    decision_is_operable,
    diff,
    expected_for,
    hand_face_kinds,
    host_checkpoint,
    host_state_matches,
    unauthorized_hand_faces,
    wait_for_sync,
)

LEAK_ID = "card-LEAK-1"


class Recorder:
    """把 ``case.fail`` 变成"记一笔并抛错"，用于负向断言。"""

    def __init__(self):
        self.failures = []

    def fail(self, text):
        self.failures.append(str(text))
        raise AssertionError(str(text))


def host_only_pump(session, seconds=0.2):
    """只推房主、**不推客户端**：模拟"快照还在路上 / 客户端落后"。"""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        session.host.session.poll()
        session.host.game.update(FRAME)
        time.sleep(FRAME / 3)


def host_advancing_pump(session, seconds=0.2):
    """推客户端**之后**再推进房主：让"两端永远不同版本"稳定成立。"""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        session.host.session.poll()
        session.clients[0].session.poll()
        session.host.game.update(FRAME)
        session.host.game.message = "持续推进 %.6f" % time.monotonic()
        session.host.match.push_views(force=True)
        time.sleep(FRAME / 3)


def leaked_view_payload(client, victim_id, source_id="", hand=None):
    """一份"把对手手牌真发过来了"的视图载荷（输入违规，链路真实）。"""

    hand = hand if hand is not None else [
        {"card_id": LEAK_ID, "name": "SHA", "label": "杀",
         "suit": "spade", "rank": "7"},
    ]
    return {
        "match_id": str(getattr(client.match, "match_id", "")),
        "revision": 10 ** 6,                    # 足够新，客户端一定接受
        "local_player_id": source_id or client.match.my_player_id,
        "players": [
            {"player_id": victim_id, "nickname": "受害者", "seat": 1,
             "hp": 4, "max_hp": 4, "alive": True, "hand_count": len(hand),
             "hand": hand},
        ],
    }


# ==================================================
# 问题 1：隐藏信息漏报
# ==================================================


class CardFaceKindTests(unittest.TestCase):
    """三档身份是整套隐藏信息判定的地基，先单独钉住。"""

    def test_real_face_is_real(self):
        card = ViewCard(card_id="card-1", name="SHA", suit="spade", rank="7")
        self.assertEqual(card_face_kind(card), FACE_REAL)

    def test_opaque_selection_token_is_token(self):
        card = ViewCard(card_id="hidden:2:0", face_down=True)
        self.assertEqual(card_face_kind(card), FACE_TOKEN)

    def test_id_only_back_is_token(self):
        self.assertEqual(card_face_kind(face_down_card("card-2")), FACE_TOKEN)

    def test_empty_placeholder_is_blank(self):
        self.assertEqual(card_face_kind(None), FACE_BLANK)
        self.assertEqual(card_face_kind(ViewCard()), FACE_BLANK)

    def test_face_on_a_token_counts_as_a_leak(self):
        """前后矛盾的数据（占位 id 却带牌名）按泄露算，不被占位前缀掩盖。"""

        card = ViewCard(card_id="hidden:9:9", name="SHA", suit="heart",
                        rank="5", face_down=True)
        self.assertEqual(card_face_kind(card), FACE_REAL)

    def test_face_on_a_face_down_card_counts_as_a_leak(self):
        card = ViewCard(card_id="card-3", name="SHA", face_down=True)
        self.assertEqual(card_face_kind(card), FACE_REAL)


class HiddenInfoNegativeTests(unittest.TestCase):
    """把违规数据喂给**真实客户端入口**，检查器必须当场拒绝。"""

    def test_wire_view_leak_is_detected(self):
        with MatchSession(client_count=1) as session:
            pump = session.pump
            client = session.clients[0]
            victim = session.game.player
            victim.hand = [tao()]
            pump(0.4)
            real_id = str(victim.hand[0].id)

            client.match.handle_client_message({
                "type": MessageType.GAME_VIEW_SNAPSHOT,
                "payload": leaked_view_payload(
                    client, victim.player_id, hand=[{
                        "card_id": real_id, "name": "桃", "label": "桃",
                        "suit": "heart", "rank": "3"}])})

            # 1) 工具必须**如实**读出收到的内容（而不是替客户端藏起来）
            entry = client_state(client, source="view")["players"][victim.player_id]
            self.assertEqual(entry["hand_ids"], (real_id,),
                             "收到的对手手牌身份被工具自己抹掉了")
            self.assertEqual(entry["hand_faces"],
                             ((real_id, "桃", "heart", "3"),),
                             "收到的对手牌面没有被读出来")
            self.assertEqual(hand_face_kinds(client, victim.player_id,
                                             source="view"), (FACE_REAL,))
            self.assertEqual(
                unauthorized_hand_faces(client, victim.player_id, source="view"),
                [(real_id, "桃", "heart", "3")])

            # 2) assert_hidden 必须拒绝
            recorder = Recorder()
            with self.assertRaises(AssertionError) as caught:
                assert_hidden(recorder, client, victim.player_id, [real_id],
                              source="view")
            self.assertIn("不该看的东西", str(caught.exception))
            self.assertIn(real_id, str(caught.exception))

            # 3) 普通比对也必须报出来（期望 None vs 实际拿到了 id）
            problems = diff(expected_for(session.game, client.match.my_player_id),
                            client_state(client, source="view"))
            self.assertTrue(
                any(victim.player_id in item and "hand_ids" in item
                    for item in problems),
                "整片比对没有报出泄露：" + str(problems))

    def test_adapter_layer_does_not_hide_the_upstream_leak(self):
        """适配层也要如实反映：不能"上游漏了、适配层抹掉"就看不见。"""

        with MatchSession(client_count=1) as session:
            pump = session.pump
            client = session.clients[0]
            victim = session.game.player
            victim.hand = [tao()]
            pump(0.4)
            real_id = str(victim.hand[0].id)

            client.match.handle_client_message({
                "type": MessageType.GAME_VIEW_SNAPSHOT,
                "payload": leaked_view_payload(
                    client, victim.player_id, hand=[{
                        "card_id": real_id, "name": "桃", "label": "桃",
                        "suit": "heart", "rank": "3"}])})
            # 只推进客户端自己的一帧（视图适配层重建），**不**碰 socket：
            # 不然房主的下一份正常快照会把这口"喂进来的泄露"盖掉。
            client.update_frame(FRAME)

            entry = client_state(client, source="adapter")["players"][
                victim.player_id]
            self.assertEqual(entry["hand_ids"], (real_id,),
                             "适配层把上游泄露抹掉了（这正是要防的掩盖）")
            self.assertEqual(entry["hand_faces"],
                             ((real_id, "桃", "heart", "3"),))
            self.assertEqual(hand_face_kinds(client, victim.player_id),
                             (FACE_REAL,))

            recorder = Recorder()
            with self.assertRaises(AssertionError):
                assert_hidden(recorder, client, victim.player_id, [real_id])

    def test_legit_anonymous_back_is_not_reported(self):
        """正向：合法的匿名占位（选择 token / 空牌背）不算泄露。"""

        with MatchSession(client_count=1) as session:
            pump = session.pump
            client = session.clients[0]
            session.fix_hands(["TAO"])
            pump(0.4)
            # 受害者必须是**别人**：只开一台客户端，它就是本人，不能拿自己
            # 当被审计对象（那会撞上"用错视角"的保护）。
            victim = session.game.player
            victim.hand = [tao()]

            client.match.handle_client_message({
                "type": MessageType.GAME_VIEW_SNAPSHOT,
                "payload": leaked_view_payload(
                    client, victim.player_id,
                    hand=[{"card_id": "hidden:5:0", "face_down": True}, {}])})

            entry = client_state(client, source="view")["players"][victim.player_id]
            self.assertIsNone(entry["hand_ids"], "合法匿名被当成了内容")
            self.assertEqual(entry["hand_faces"], ())
            kinds = hand_face_kinds(client, victim.player_id, source="view")
            self.assertEqual(kinds, (FACE_TOKEN, FACE_BLANK))
            self.assertEqual(unauthorized_hand_faces(client, victim.player_id,
                                                     source="view"), [])

            recorder = Recorder()
            self.assertTrue(
                assert_hidden(recorder, client, victim.player_id, [],
                              source="view"))
            self.assertEqual(recorder.failures, [])


# ==================================================
# 问题 2：可操作性假通过
# ==================================================


class DecisionOperabilityTests(unittest.TestCase):
    """区分：等待、可操作、被覆盖、已提交待确认、已结束、不归他答。"""

    def _beige_session(self, session, *, both=True):
        """一次【杀】伤害触发【悲歌】：两名远程都发时形成嵌套。"""

        game, pump = session.game, session.pump
        first, second = session.remote(0), session.remote(1)
        give_general(game, first, "caiwenji")
        if both:
            give_general(game, second, "sp_caiwenji")
        for player in (first, second, game.player):
            force_hand(game, player, ["SHA", "TAO"])
        pump(0.4)
        DamageFlow(game.engine, DamageContext(
            source=game.player, target=first, amount=1,
            card=normal_sha())).start()
        return first, second

    def test_missing_panel_is_not_operable(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, _second = self._beige_session(session, both=False)
            client = session.client_of(first)

            # 请求已经在引擎里，但面板还没送到：不算"可以操作"。
            top = game.engine.pending.current
            self.assertIsNotNone(top)
            client.match.decision = None
            client.match._held_decision = None
            result = decision_is_operable(session.host, client)
            self.assertFalse(result.operable,
                            "面板缺失却判成可操作：" + result.describe())
            self.assertIn(result.state, (STATE_WAITING, STATE_WAITING_VIEW))
            self.assertTrue(result.mine, "这条请求本来就是他的：" + result.describe())

            # 送到之后必须变成可操作（证明上一条不是因为"永远判否"）。
            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(
                    session.host, client).state == STATE_OPERABLE, 10.0, ""),
                "面板送达之后仍然不可操作："
                + decision_is_operable(session.host, client).describe())

    def test_request_for_someone_else_is_not_mine(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, second = self._beige_session(session, both=False)
            owner = session.client_of(first)
            other = session.client_of(second)
            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(
                    session.host, owner).operable, 10.0, ""),
                "请求持有者的面板没到")

            result = decision_is_operable(session.host, other)
            self.assertFalse(result.operable,
                             "不归他答却判成可操作：" + result.describe())
            self.assertFalse(result.mine,
                             "把别人的请求算成了他的：" + result.describe())
            self.assertIn(result.state, (STATE_IDLE, STATE_CLOSED, STATE_WAITING,
                                        STATE_WAITING_VIEW, STATE_WRONG_PLAYER,
                                        STATE_COVERED))

    def test_covered_request_is_not_operable_until_restored(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, second = self._beige_session(session, both=True)
            self.assertTrue(wait_for(
                pump, lambda: len(game.engine.pending._stack) == 2, 10.0, ""),
                "没有形成嵌套请求")
            outer, inner = game.engine.pending._stack
            outer_client = session.client_of(outer.target)
            inner_client = session.client_of(inner.target)
            self.assertEqual(outer.target.player_id, first.player_id)

            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(
                    session.host, inner_client).operable, 10.0, ""),
                "内层面板没到")
            covered = decision_is_operable(session.host, outer_client)
            self.assertFalse(covered.operable,
                             "被覆盖的请求却判成可操作：" + covered.describe())
            self.assertEqual(covered.state, STATE_COVERED)
            self.assertTrue(covered.mine)

    def test_stale_request_id_is_closed(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, _second = self._beige_session(session, both=False)
            client = session.client_of(first)
            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(
                    session.host, client).operable, 10.0, ""),
                "面板没到")

            client.match.decision["request_id"] = 999999        # 伪造成旧请求
            result = decision_is_operable(session.host, client)
            self.assertFalse(result.operable, "旧请求编号却判成可操作")
            self.assertEqual(result.state, STATE_CLOSED)

    def test_answered_waiting_ack_is_not_operable(self):
        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, _second = self._beige_session(session, both=False)
            client = session.client_of(first)
            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(
                    session.host, client).operable, 10.0, ""),
                "面板没到")

            client.match.answered = True                        # 已提交、等确认
            result = decision_is_operable(session.host, client)
            self.assertFalse(result.operable, "已提交待确认却判成可操作")
            self.assertEqual(result.state, STATE_ANSWERED)
            self.assertTrue(result.mine, "提交过了仍然是他的请求")

    def test_nested_recovery_is_operable_and_the_owner_finishes_it(self):
        """内层答完 → 外层恢复为"可操作" → 真实客户端把它答完。"""

        with MatchSession(client_count=2) as session:
            game, pump = session.game, session.pump
            first, second = self._beige_session(session, both=True)
            self.assertTrue(wait_for(
                pump, lambda: len(game.engine.pending._stack) == 2, 10.0, ""),
                "没有形成嵌套请求")
            outer, inner = game.engine.pending._stack
            outer_client = session.client_of(outer.target)
            inner_client = session.client_of(inner.target)

            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(session.host, outer_client).state
                == STATE_COVERED, 10.0, ""), "外层没有被覆盖")
            self.assertTrue(wait_for(
                pump, lambda: decision_is_operable(
                    session.host, inner_client).operable, 10.0, ""),
                "内层面板没到")

            inner_card = [str(item["card_id"]) for item in
                          (inner_client.match.decision or {}).get("cards", ())][0]
            answer_via_ui(UiClient(inner_client), pump, card_id=inner_card)
            self.assertTrue(wait_for(
                pump, lambda: [item.request_id
                               for item in game.engine.pending._stack]
                == [outer.request_id], 12.0), "外层没有被恢复")
            restored = decision_is_operable(session.host, outer_client)
            self.assertTrue(restored.operable,
                            "外层恢复之后仍不可操作：" + restored.describe())
            self.assertEqual(restored.state, STATE_OPERABLE)

            # 真实客户端把外层答完，结算收尾。
            outer_card = [str(item["card_id"]) for item in
                          (outer_client.match.decision or {}).get("cards", ())][0]
            answer_via_ui(UiClient(outer_client), pump, card_id=outer_card)
            self.assertTrue(wait_for(
                pump, lambda: not game.engine.pending.active, 12.0),
                "外层回答之后结算没有收尾")
            idle = decision_is_operable(session.host, outer_client)
            self.assertFalse(idle.operable, "结算完了却还判成可操作")
            self.assertEqual(idle.state, STATE_IDLE)


# ==================================================
# 问题 3：模块调试开关
# ==================================================


def duplicate_game():
    """一局已经存在"重复归属"的对局（手牌 + 弃牌堆同一张牌）。"""

    game = make_test_game(player_hand=[tao()])
    game.deck.discard_pile.append(game.player.hand[0])
    return game


def duplicate_atom():
    """一个会产生重复归属的原子（只往手牌加、不从弃牌堆拿）。"""

    from src.game.atoms_v2 import MoveCardAtom

    game = make_test_game()
    game.player.hand.clear()
    card = tao()
    game.deck.discard_pile.append(card)
    return game, MoveCardAtom(card, destination=game.player.hand)


class SwitchSemanticsTests(unittest.TestCase):
    """统一开关：对局显式设置 > 模块强制 > 环境变量。"""

    def setUp(self):
        self.previous = invariants._FORCED

    def tearDown(self):
        invariants.restore_debug(self.previous)

    def test_default_is_off_and_follows_the_global_switch(self):
        game = duplicate_game()
        self.assertIsNone(game.assert_card_ownership,
                          "对局不应把默认值冻结下来（否则模块开关对它无效）")
        self.assertFalse(invariants.armed_for(game))
        game.update(1 / 60)                      # 关闭：不扫描

    def test_enable_debug_arms_an_already_created_game(self):
        game = duplicate_game()                  # 先建局
        self.assertFalse(invariants.armed_for(game))
        invariants.enable_debug()                # 再开全局开关
        self.assertTrue(invariants.armed_for(game),
                        "enable_debug 对已创建的对局没有生效")
        with self.assertRaises(CardOwnershipError):
            game.update(1 / 60)                  # 帧边界

    def test_enable_debug_arms_the_atom_boundary_too(self):
        game, atom = duplicate_atom()
        invariants.enable_debug()
        with self.assertRaises(CardOwnershipError):
            game.context.apply(atom)

    def test_enable_debug_arms_newly_created_games(self):
        invariants.enable_debug()
        game = duplicate_game()
        self.assertTrue(invariants.armed_for(game))
        with self.assertRaises(CardOwnershipError):
            game.update(1 / 60)

    def test_disable_debug_stops_scanning_but_per_game_enable_still_wins(self):
        invariants.disable_debug()
        quiet = duplicate_game()
        self.assertFalse(invariants.armed_for(quiet))
        quiet.update(1 / 60)                     # 关闭时不做任何牌区扫描

        per_game = duplicate_game()
        per_game.assert_card_ownership = True     # 按局显式启用
        self.assertTrue(invariants.armed_for(per_game),
                        "全局关闭压掉了按局启用")
        with self.assertRaises(CardOwnershipError):
            per_game.update(1 / 60)

    def test_explicit_off_wins_over_global_enable(self):
        """按局显式关闭也要说了算（否则没法"只让大部分对局受检"）。"""

        invariants.enable_debug()
        quiet = duplicate_game()
        quiet.assert_card_ownership = False
        self.assertFalse(invariants.armed_for(quiet))
        quiet.update(1 / 60)

    def test_restore_debug_puts_the_switch_back(self):
        game = duplicate_game()
        previous = invariants.enable_debug()
        invariants.restore_debug(previous)
        self.assertFalse(invariants.armed_for(game),
                         "restore_debug 没有还原开关")
        game.update(1 / 60)

    def test_scanning_is_not_run_when_disabled(self):
        """关闭时不得做牌区扫描：直接数"牌区枚举"被调用了几次。

        数 ``zone_entries`` 而不是 ``assert_card_ownership``：入口是
        ``from .invariants import assert_card_ownership`` 绑进 ``core`` 命名空间
        的，patch 模块属性拦不到那次调用；``zone_entries`` 是模块内按名查找，
        拦得住，而且它才是真正的"扫描"动作。
        """

        scanned = []
        original = invariants.zone_entries

        def spy(game):
            scanned.append(1)
            return original(game)

        invariants.zone_entries = spy
        try:
            game = duplicate_game()
            for _ in range(5):
                game.update(1 / 60)
            game2, atom = duplicate_atom()
            game2.context.apply(atom)
            self.assertEqual(scanned, [], "关闭状态下仍然执行了牌区扫描")

            invariants.enable_debug()
            with self.assertRaises(CardOwnershipError):
                game.update(1 / 60)
            self.assertTrue(scanned, "打开之后牌区扫描没有被执行")
        finally:
            invariants.zone_entries = original


# ==================================================
# 问题 4：同版本检查
# ==================================================


class SyncCheckpointTests(unittest.TestCase):
    """落后但字段相同不得通过；延迟送达要能等到；送不到要明说。"""

    def _aligned_session(self):
        session = MatchSession(client_count=1)
        session.pump(0.5)
        session.fix_hands(["TAO"], keep=session.remote(0))
        session.pump(0.4)
        return session

    def _advance_host_only(self, session, steps=3):
        """只让房主往前走（改 message 会推动状态指纹，但不改被比对的字段）。"""

        for index in range(steps):
            session.game.message = "只推进房主 %d" % index
            session.host.match.push_views(force=True)
            host_only_pump(session, 0.05)

    def test_stale_client_with_equal_fields_is_rejected(self):
        with self._aligned_session() as session:
            client = session.clients[0]
            remote = session.remote(0)

            # 先正常对齐一次，确认基线是好的。
            checkpoint, why = wait_for_sync(session.pump, session.host, client,
                                           timeout=8.0)
            self.assertIsNotNone(checkpoint, "基线就对不齐：" + why)

            # 只推进房主：客户端停在旧版本。
            self._advance_host_only(session)
            self.assertLess(client_checkpoint(client).revision,
                            host_checkpoint(session.host).revision,
                            "装置没生效：客户端不该已经追上")

            # 被比对的语义字段此刻**完全相同**（改的只是 message）。
            problems = diff(expected_for(session.game, remote.player_id),
                            client_state(client))
            self.assertEqual(problems, [],
                             "装置不成立：字段本来就不同，"
                             "这条用例就证明不了'字段相同也会通过'")

            # 旧实现（只比字段）会在这里放行；现在必须拒绝。
            recorder = Recorder()
            fresh = host_checkpoint(session.host)
            with self.assertRaises(AssertionError) as caught:
                compare_at_checkpoint(recorder, session.host, client,
                                      remote.player_id, fresh)
            self.assertIn("还没应用检查点版本", str(caught.exception))

            # 而且 wait_for_sync 也不会给它任何检查点（客户端推不动）。
            none_checkpoint, why2 = wait_for_sync(
                lambda seconds: host_only_pump(session, seconds),
                session.host, client, timeout=1.0, interval=0.05)
            self.assertIsNone(none_checkpoint)
            self.assertIn("落后", why2)

    def test_delayed_delivery_eventually_syncs(self):
        with self._aligned_session() as session:
            client = session.clients[0]
            remote = session.remote(0)
            self._advance_host_only(session)

            blocked, why = wait_for_sync(
                lambda seconds: host_only_pump(session, seconds),
                session.host, client, timeout=1.0, interval=0.05)
            self.assertIsNone(blocked, "客户端落后却拿到了检查点")
            self.assertIn("落后", why)

            # 放行之后必须真的对齐，并且检查点上比对通过。
            checkpoint, why = wait_for_sync(session.pump, session.host, client,
                                           timeout=8.0)
            self.assertIsNotNone(checkpoint, "放行之后没追上：" + why)
            self.assertGreaterEqual(client_checkpoint(client).revision,
                                    checkpoint.revision)
            self.assertTrue(host_state_matches(session.host, checkpoint),
                            "检查点的状态指纹与房主当前状态不一致")

            recorder = Recorder()
            compare_at_checkpoint(recorder, session.host, client,
                                  remote.player_id, checkpoint)
            self.assertEqual(recorder.failures, [])

    def test_never_delivered_is_reported_not_silently_passed(self):
        with self._aligned_session() as session:
            client = session.clients[0]
            self._advance_host_only(session)

            # 客户端永远不被推进：同步条件不可能满足。
            checkpoint, why = wait_for_sync(lambda seconds: None,
                                           session.host, client,
                                           timeout=1.0, interval=0.05)
            self.assertIsNone(checkpoint, "客户端从未收到快照却算同步")
            self.assertIn("落后", why)

    def test_host_state_moved_after_publishing_is_rejected(self):
        """发布之后房主又改了状态（节流窗口）：检查点失效，必须拒绝比对。

        这是"不能混比不同版本"的确定性证据：``revision`` 没变、被比对的字段
        也没变，只有状态指纹变了——只认版本号的实现会在这里放行。
        """

        with self._aligned_session() as session:
            client = session.clients[0]
            remote = session.remote(0)

            checkpoint, why = wait_for_sync(session.pump, session.host, client,
                                           timeout=8.0)
            self.assertIsNotNone(checkpoint, why)
            self.assertTrue(host_state_matches(session.host, checkpoint))

            # 房主状态变了但**不发布**：revision 不动，指纹已经不同。
            session.game.message = "发布之后又变了"
            self.assertEqual(host_checkpoint(session.host).revision,
                             checkpoint.revision, "装置不成立：revision 不该变")
            self.assertFalse(host_state_matches(session.host, checkpoint),
                             "指纹校验没有发现房主状态已经变了")

            recorder = Recorder()
            with self.assertRaises(AssertionError) as caught:
                compare_at_checkpoint(recorder, session.host, client,
                                      remote.player_id, checkpoint)
            self.assertIn("又变了", str(caught.exception))

            # 重新对齐（房主把新状态发布出去）之后必须恢复正常。
            again, why = wait_for_sync(session.pump, session.host, client,
                                      timeout=8.0)
            self.assertIsNotNone(again, "重新对齐失败：" + why)
            self.assertGreater(again.revision, checkpoint.revision)
            recorder = Recorder()
            compare_at_checkpoint(recorder, session.host, client,
                                  remote.player_id, again)
            self.assertEqual(recorder.failures, [])

    def test_host_keeps_advancing_never_yields_a_checkpoint(self):
        """房主持续推进：不返回检查点（宁可失败也不拿旧快照比新客户端）。"""

        with self._aligned_session() as session:
            client = session.clients[0]
            # 先让房主往前走（客户端落后），否则第一次判定时两端本来就是
            # 对齐的——那时返回检查点是对的，证明不了"推进中的处理"。
            self._advance_host_only(session)
            self.assertLess(client_checkpoint(client).revision,
                            host_checkpoint(session.host).revision)
            checkpoint, why = wait_for_sync(
                lambda seconds: host_advancing_pump(session, seconds),
                session.host, client, timeout=1.0, interval=0.05)
            self.assertIsNone(checkpoint,
                              "房主一直在推进，却给了一个检查点（会混比版本）")
            # 说明里必须写清卡在哪：落后 / 到位后房主又推进。
            self.assertTrue("落后" in why or "推进" in why,
                            "失败原因没有说清同步条件：" + why)

    def test_checkpoint_revision_matches_both_sides(self):
        """正常对齐时，检查点的版本必须与两侧**当下**的版本一致。"""

        with self._aligned_session() as session:
            client = session.clients[0]
            remote = session.remote(0)
            session.game.message = "对齐用"
            session.host.match.push_views(force=True)

            checkpoint, why = wait_for_sync(session.pump, session.host, client,
                                           timeout=8.0)
            self.assertIsNotNone(checkpoint, why)
            self.assertEqual(checkpoint, host_checkpoint(session.host))
            self.assertEqual(client_checkpoint(client).revision,
                             checkpoint.revision)
            self.assertTrue(host_state_matches(session.host, checkpoint))
            self.assertEqual(checkpoint.match_id, session.host.match.match_id)
            # 客户端手上那份 ClientGameView 也确实是这一版。
            self.assertEqual(int(client.view.revision), checkpoint.revision)


if __name__ == "__main__":
    unittest.main()
