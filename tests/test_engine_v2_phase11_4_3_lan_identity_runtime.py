"""Phase 11.4.3：联机开局 = **原来的身份局** + RemoteHumanController。

这一份测试跑的是**真实开局路径**，不是自己搭的脚手架：

    真实 socket → LanSession → HostServer → HostMatch.start()
        → Game.start_networked_battle() → Game._apply_opening_rules()

覆盖（对应任务书 §17 的场景 A～H）：

* A 2 名真人 + 3 个 AI = 5 人身份局
* B 身份配比正确（1 主公 / 1 忠臣 / 2 反贼 / 1 内奸）
* C 主公身份开局公开
* D 每个远程观众只能看到"自己 + 主公 + 已公开"的身份（连原始载荷一起查）
* E 所有玩家都有真实武将、体力上限与已注册的技能
* F 远程真人的技能栏非空（与武将技能表逐项一致）
* G AI 玩家继续正常行动
* H 真人与 AI 混合完整轮转至少 2 轮

"真人"= 房主（本地真人）+ 1 名远程真人；AI 是补位的那 3 个。默认模式是
标准身份局，所以 2 名真人开不出"二人裸局"——那正是本阶段修掉的东西。
"""

import time
import unittest

import pygame

from src.game import Game
from src.game.identity import Identity, identity_name
from src.network.decisions import (
    ACTION_END_PHASE,
    ACTION_PASS,
    DecisionKind,
    DecisionResult,
)
from src.network.session import LanSession
from src.player import ControllerType

FRAME = 0.02


class LanIdentityRuntimeTestCase(unittest.TestCase):
    """房主 + 1 个远程真人：真实 socket、真实开局、真实控制器。"""

    timeout = 12.0

    def setUp(self):
        self.sessions = []
        self.host = LanSession()
        ok, message = self.host.create_room("房主", 8, 0, game_mode="identity")
        self.assertTrue(ok, message)
        self.game = Game(ai_count=1)
        # 规则与轮转验证用同步语义（与既有引擎测试一致）：节奏模式只影响
        # AI 响应出现的快慢，不影响"谁行动、行动几次"。
        self.game.ai_pacing = False
        self.host.set_game(self.game)
        self.sessions.append(self.host)

        self.client = self.join("远程玩家")
        # 记录**从 socket 上收到的每一条消息**（解码后的 JSON 消息）：用来
        # 证明隐蔽信息根本没发出来。会话每帧会把它们 drain 走，所以在这里
        # 包一层留底，而不是事后去读已经被取空的队列。
        self.wire = []
        original = self.client._route_client_game_message

        def route(message, _original=original):
            self.wire.append(message)
            return _original(message)

        self.client._route_client_game_message = route
        self.pump(1.5)
        self.assertTrue(self.client.connected, self.client.error)
        self.client.set_ready(True)
        self.assertTrue(self.wait(lambda: self.host.lobby.can_start(), 6.0),
                        "房主 + 1 名真人应当可以开局（不足的人由 AI 补位）")
        ok, message = self.host.start_match()
        self.assertTrue(ok, message)
        # 联机的开局流程与单机一致：先看身份 → 再选将。这一组用例验的是"开局
        # 之后那一局是什么样"，所以由脚本替两名真人点完这两步（真实流程一步
        # 不少，只是代点）；身份与选将本身由 SetupFlowTests 单独验证。
        from tools.lan_view_harness import complete_setup

        self.assertTrue(complete_setup(self.host, [self.client], self.pump, 10.0),
                        "联机开局流程没有走完")
        # 等客户端真正拿到开局视图（而不是固定睡一段时间：睡过头会把开局
        # 第一轮行动睡过去，轮转统计就要多等一整圈）。
        self.assertTrue(self.wait(lambda: self.client.match is not None
                                  and self.client.match.ready, 8.0),
                        "客户端没有收到开局视图")

    def tearDown(self):
        for session in self.sessions:
            session.leave()
        for session in self.sessions:
            session.poll()
        pygame.display.quit()

    # ---- 脚手架 ----

    def join(self, nickname):
        session = LanSession()
        ok, message = session.join_room(nickname, "127.0.0.1",
                                        self.host.host.bound_port)
        self.assertTrue(ok, message)
        self.sessions.append(session)
        return session

    def pump(self, seconds=0.5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for session in self.sessions:
                session.poll()
            self.game.update(FRAME)
            time.sleep(FRAME / 2)

    def wait(self, predicate, timeout=None):
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while time.monotonic() < deadline:
            if predicate():
                return True
            self.pump(0.05)
        return bool(predicate())

    def wait_decision(self, kind, timeout=10.0):
        self.wait(lambda: (self.client.match.decision or {}).get("kind") == kind,
                  timeout)
        return self.client.match.decision

    def answer(self, **kwargs):
        return self.client.match.answer(DecisionResult(**kwargs))

    # ---- 查询 ----

    @property
    def remote(self):
        return next(player for player in self.game.players
                    if player.controller_type is ControllerType.REMOTE_HUMAN)

    @property
    def ai_players(self):
        return [player for player in self.game.players
                if player.controller_type is ControllerType.AI]

    def players_of(self, identity):
        return [player for player in self.game.players
                if getattr(player, "identity", None) is identity]

    def remote_payloads(self):
        """这次对局里客户端**真的收到过**的原始载荷（视图 + 开局信息）。"""

        payloads = []
        for message in self.wire:
            payload = message.get("payload") if isinstance(message, dict) else None
            if isinstance(payload, dict):
                payloads.append(payload)
        return payloads


# ==================================================
# 场景 A / B / C：人数、身份配比、主公公开
# ==================================================

class IdentityBattleCompositionTests(LanIdentityRuntimeTestCase):

    def test_scenario_a_two_humans_plus_three_ai_is_a_five_player_identity_battle(self):
        players = self.game.players
        humans = [player for player in players if player.is_human]
        remote = [player for player in players
                  if player.controller_type is ControllerType.REMOTE_HUMAN]

        self.assertEqual(len(players), 5, "身份局不是 2 人裸局")
        self.assertEqual(len(humans) + len(remote), 2, "真人总数（房主 + 远程）")
        self.assertEqual(len(self.ai_players), 3, "AI 补位数量")
        self.assertEqual(self.game.mode_id, "identity", "模式必须真的是身份局")
        self.assertTrue(self.game.mode.uses_identities)

    def test_scenario_b_identity_distribution_is_the_standard_five_player_one(self):
        self.assertEqual(len(self.players_of(Identity.LORD)), 1)
        self.assertEqual(len(self.players_of(Identity.LOYALIST)), 1)
        self.assertEqual(len(self.players_of(Identity.REBEL)), 2)
        self.assertEqual(len(self.players_of(Identity.RENEGADE)), 1)
        self.assertTrue(all(player.identity is not None
                            for player in self.game.players),
                        "每个人都必须拿到身份")

    def test_scenario_c_lord_identity_is_public_from_the_start(self):
        lord = self.players_of(Identity.LORD)[0]
        self.assertTrue(lord.identity_revealed, "主公身份开局公开")
        self.assertTrue(self.game.mode.public_identity_of(lord) is Identity.LORD)
        # 主公体力上限 +1 是身份模式的开局规则（必须与单机一致）。
        general = self.game.generals.get(lord.general_id)
        self.assertIsNotNone(general)
        self.assertEqual(lord.max_hp, general.max_hp + 1)

    def test_first_turn_belongs_to_the_lord(self):
        """主公先手：开局的首行动角色必须是主公（与单机同一条规则）。"""

        lord = self.players_of(Identity.LORD)[0]
        self.assertIs(self.game.current_turn_player, lord)
        self.assertEqual(self.game.mode.first_player(), lord)


# ==================================================
# 场景 D：身份保密（视图 + 原始载荷）
# ==================================================

class IdentitySecrecyTests(LanIdentityRuntimeTestCase):

    def test_scenario_d_remote_viewer_only_sees_self_lord_and_revealed(self):
        self.host.match.push_views(force=True)
        self.pump(0.8)
        self.assertIsNotNone(self.client.match)
        self.assertTrue(self.client.match.ready)

        lord = self.players_of(Identity.LORD)[0]
        remote_id = self.client.match.my_player_id
        self.assertEqual(remote_id, self.remote.player_id)

        for player in self.game.players:
            view = self.client.match.player(player.player_id)
            self.assertIsNotNone(view, "视图里必须有每个座位")
            if player is lord:
                self.assertEqual(view["identity"], Identity.LORD.value,
                                 "主公身份公开")
            elif player.player_id == remote_id:
                self.assertEqual(view["identity"], player.identity.value,
                                 "自己知道自己的身份")
            else:
                self.assertIsNone(
                    view.get("identity"),
                    "%s 的隐藏身份不该进视图" % player.name)

    def test_scenario_d_hidden_identities_never_reach_the_wire(self):
        """原始载荷（未经 UI 过滤）里也不许出现别人的隐藏身份。"""

        lord = self.players_of(Identity.LORD)[0]
        hidden = {}
        for player in self.game.players:
            if player is lord or player.player_id == self.remote.player_id:
                continue
            hidden[player.player_id] = player.identity.value
        self.assertTrue(hidden, "5 人局必然有隐藏身份")

        payloads = self.remote_payloads()
        self.assertTrue(payloads, "客户端至少收到过一条对局消息")
        leaked = []
        for payload in payloads:
            if payload.get("players") is None:
                continue
            for entry in payload.get("players") or ():
                identity = entry.get("identity")
                if identity and entry.get("player_id") in hidden:
                    leaked.append((entry.get("player_id"), identity))
        self.assertEqual(leaked, [], "隐藏身份绝不能出现在网络载荷里")


# ==================================================
# 场景 E / F：武将、体力、技能
# ==================================================

class GeneralAndSkillTests(LanIdentityRuntimeTestCase):

    def test_scenario_e_everyone_has_a_real_general_and_skills_registered(self):
        for player in self.game.players:
            general_id = player.general_id
            self.assertTrue(general_id, "%s 必须有真实武将" % player.name)
            general = self.game.generals.get(general_id)
            self.assertIsNotNone(general, "武将必须来自真实武将表")
            self.assertGreater(player.max_hp, 0)
            # 武将绑定必须真的登记进 SkillManager（不是只写了个 id）。
            # 主公技只在主公身上生效，所以非主公少一个主公技是**正确**的。
            expected = set(general.skill_ids)
            for skill_id in list(expected):
                definition = self.game.skill_registry.get(skill_id)
                if (definition is not None and definition.is_lord_skill
                        and player.identity is not Identity.LORD):
                    expected.discard(skill_id)
            self.assertEqual(
                set(self.game.skills.skill_ids_of(player)), expected,
                "%s 的技能没有正确注册" % player.name)

    def test_scenario_e_lord_carries_its_lord_skill(self):
        """主公技是身份局的一部分：主公身上必须绑得上。"""

        lord = self.players_of(Identity.LORD)[0]
        general = self.game.generals.get(lord.general_id)
        lord_skills = [
            skill_id for skill_id in general.skill_ids
            if (self.game.skill_registry.get(skill_id) is not None
                and self.game.skill_registry.get(skill_id).is_lord_skill)
        ]
        bound = set(self.game.skills.skill_ids_of(lord))
        for skill_id in lord_skills:
            self.assertIn(skill_id, bound, "主公技能必须绑在主公身上")

    def test_scenario_e_generals_are_unique_inside_one_battle(self):
        assigned = [player.general_id for player in self.game.players]
        self.assertEqual(len(assigned), len(set(assigned)),
                         "同一局里武将不重复")

    def test_scenario_f_remote_skill_bar_matches_the_host(self):
        self.host.match.push_views(force=True)
        self.pump(0.8)
        remote = self.remote
        general = self.game.generals.get(remote.general_id)
        self.assertIsNotNone(general)

        view = self.client.match.player(remote.player_id)
        self.assertIsNotNone(view)
        self.assertEqual(view["general_id"], remote.general_id,
                         "客户端看到的武将就是房主分配的武将")
        # 期望值按引擎的真实绑定规则算：主公技（孙权的【救援】）只有在
        # "这名角色是主公"时才绑定，随机抽到非主公的孙权时它本来就不该出现。
        expected = {
            skill_id for skill_id in general.skill_ids
            if self.game.skills.has(remote, skill_id)
            or not getattr(self.game.skill_registry.get(skill_id),
                           "is_lord_skill", False)
        }
        self.assertEqual(set(view.get("skills") or ()), expected,
                         "客户端的技能栏来自真实武将技能表")
        if general.skill_ids:
            self.assertTrue(view.get("skills"), "有技能的武将技能栏不为空")

    def test_scenario_f_host_view_carries_identity_general_and_phase(self):
        """房主自己那一份视图（= 权威状态）带齐界面需要的一切。"""

        from src.game.view.view_builder import build_view

        view = build_view(self.game, self.game.player.player_id, 1)
        payload = view.to_payload()
        self.assertEqual(payload["game_mode"], "identity")
        self.assertEqual(len(payload["players"]), 5)
        me = next(item for item in payload["players"]
                  if item["player_id"] == self.game.player.player_id)
        self.assertTrue(me["general_id"])
        self.assertEqual(me["identity"], self.game.player.identity.value)
        self.assertTrue(payload["current_phase"])
        self.assertTrue(payload["skills"])

    def test_scenario_f_no_player_is_a_bare_placeholder(self):
        for player in self.game.players:
            self.assertTrue(player.general_id, "严禁空武将 / 占位武将")
            self.assertNotIn(str(player.general_id).lower(),
                             ("none", "placeholder", "unknown"))


# ==================================================
# 场景 G / H：AI 与真人混合行动
# ==================================================

class MixedTurnFlowTests(LanIdentityRuntimeTestCase):

    def auto_answer_remote(self):
        """远程玩家的自动应答：只做"不冒险"的合法选择（相当于接管鼠标）。"""

        match = self.client.match
        request = getattr(match, "decision", None)
        if not request or match.answered:
            return False
        kind = request.get("kind")
        if kind == DecisionKind.PLAY_PHASE:
            if not match.answer(DecisionResult(action=ACTION_END_PHASE)):
                return False
        elif kind == DecisionKind.RESPOND_CARD:
            if not match.answer(DecisionResult(action=ACTION_PASS)):
                return False
        elif kind == DecisionKind.CONFIRM:
            if not match.answer(DecisionResult(confirm=False)):
                return False
        elif kind == DecisionKind.CHOOSE_OPTION:
            options = request.get("options") or ()
            if not options:
                return False
            if not match.answer(DecisionResult(option=options[-1]["value"])):
                return False
        elif kind == DecisionKind.SELECT_CARDS:
            need = int((request.get("constraints") or {}).get("min_cards") or 0)
            cards = [item["card_id"] for item in request.get("cards") or ()]
            if not match.answer(DecisionResult(
                    action=ACTION_PASS if need == 0 else "submit",
                    card_ids=cards[:need])):
                return False
        elif kind == DecisionKind.SELECT_TARGETS:
            if not match.answer(DecisionResult(action=ACTION_PASS)):
                return False
        else:                                            # pragma: no cover - 兜底
            return False
        return True

    def auto_answer_host(self):
        """房主（本地真人）的自动应答：等价于真人在牌桌上点「不出 / 结束回合」。

        真人被 AI 打了要么出【闪】要么点「不出」，轮到自己的出牌阶段要么出牌
        要么点「结束回合」——都是真实操作。不处理的话整局会停在这个窗口上，
        永远轮不到下一个座位。
        """

        from src.game.engine import (
            ChooseOptionAction,
            ConfirmPendingAction,
            PassPendingAction,
            SelectCardsAction,
            SelectTargetsAction,
        )
        from src.game.engine.pending import PendingRequestType

        game = self.game
        if game.busy:
            return False
        if getattr(game.response, "active", False):
            game.pass_response()
            return True
        if getattr(game.choice, "active", False):
            game.choice.choose_no()
            return True

        request = game.pending_request
        if request is not None and request.target is game.player:
            kind = request.request_type
            if kind is PendingRequestType.SELECT_CARDS:
                # 候选来自请求本身（可能是别人的手牌 / 公共区），不是自己的手牌。
                candidates = list((request.context or {}).get("candidates") or ())
                if not candidates:
                    candidates = list(game.player.hand or ())
                cards = candidates[:int(request.min_cards or 0)]
                game.submit_action(SelectCardsAction(
                    game.player, request.request_id, cards))
            elif kind is PendingRequestType.CONFIRM:
                game.submit_action(ConfirmPendingAction(
                    game.player, request.request_id, False))       # 不发动
            elif kind is PendingRequestType.CHOOSE_OPTION:
                options = list(getattr(request, "options", ()) or ())
                game.submit_action(ChooseOptionAction(
                    game.player, request.request_id,
                    getattr(options[-1], "value", options[-1]) if options else None))
            elif kind is PendingRequestType.SELECT_TARGETS:
                game.submit_action(SelectTargetsAction(
                    game.player, request.request_id, ()))          # 这次不发动
            else:
                game.submit_action(PassPendingAction(
                    game.player, request.request_id))
            return True

        if game.current_turn_player is not game.player:
            return False
        if game.phase == "play" and request is None:
            game.end_player_turn(game.player)          # 点「结束回合」
            return True
        if game.phase == "discard" and game.player.hand:
            game.player_discard(0, (0, 0, 10, 10))     # 弃到上限
            return True
        return False

    def keep_everyone_durable(self):
        """把所有人的体力调成"耐打"，并把节奏调到最快，好观察完整轮转。

        身份局可能在一圈之内就分出胜负（主公阵亡 / 反贼全灭），那样"两轮
        轮转"根本来不及发生。这里只改体力数值与动画倍率（规则本来就允许），
        让对局活得够久、跑得够快；死人与胜负判定本身仍然按原规则走。
        """

        self.game.set_speed(self.game.SPEED_STEPS[-1])
        for player in self.game.players:
            player.max_hp = max(player.max_hp, 8)
            player.hp = player.max_hp

    def advance_battle(self, rounds, timeout=180.0):
        """推进对局，统计每名角色被轮到过的次数（真人由脚本代答）。

        返回 ``(seen, turns)``：``seen`` 是每个座位的轮次数，``turns`` 是观察
        期内发生过的回合总数。
        """

        seen = {player.player_id: 0 for player in self.game.players}
        current = None
        turns = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            player = self.game.current_turn_player
            if player is not None and player.player_id != current:
                current = player.player_id
                turns += 1
                seen[current] = seen.get(current, 0) + 1
            self.pump(0.05)
            self.auto_answer_remote()
            self.auto_answer_host()
            if all(count >= rounds for count in seen.values()):
                return seen, turns
            if self.game.game_over:
                break
        return seen, turns

    def test_scenario_g_ai_players_keep_taking_their_turns(self):
        """AI 座位真的在行动：模型之外的座位没有变成"看客"。"""

        self.keep_everyone_durable()
        seen = self.advance_battle(rounds=1)[0]
        for ai in self.ai_players:
            self.assertGreaterEqual(seen.get(ai.player_id, 0), 1,
                                    "%s 没有行动" % ai.name)
        self.assertNotIn("（回合守卫）", " ".join(self.game.game_log),
                         "AI 回合不该靠卡死兜底推进")

    def test_scenario_h_humans_and_ai_rotate_for_two_rounds(self):
        """真人与 AI 混合轮转：每个座位都能轮到，且至少跑满两圈。"""

        self.keep_everyone_durable()
        seen, turns = self.advance_battle(rounds=2)
        self.assertFalse(self.game.game_over,
                         "对局不该在被调成耐打的观察窗口里结束：%s" % self.game.message)
        for player in self.game.players:
            self.assertGreaterEqual(seen.get(player.player_id, 0), 2,
                                    "%s 没有完成两轮行动" % player.name)
        self.assertGreaterEqual(turns, len(self.game.players) * 2,
                                "两圈混合轮转没有真的跑满")
        # 两轮里既有真人也有 AI（不是只有一边在动）。
        acted = {player.player_id for player in self.game.players
                 if seen.get(player.player_id, 0) >= 2}
        self.assertIn(self.game.player.player_id, acted)
        self.assertIn(self.remote.player_id, acted)
        self.assertTrue({player.player_id for player in self.ai_players} <= acted)

    def test_scenario_h_remote_player_really_plays_through_the_bridge(self):
        """轮到远程真人时，房主停下来等他，并且他的回答真的改了权威状态。"""

        # 把回合交给远程真人：请求必须真的发到他手上，房主必须停下来等。
        self.game.actions.clear()
        self.game.start_turn(self.remote)
        self.assertTrue(self.wait(lambda: self.game.current_turn_player
                                  is self.remote, 6.0))
        request = self.wait_decision(DecisionKind.PLAY_PHASE)
        self.assertIsNotNone(request, "远程真人没有收到出牌阶段请求")
        self.assertTrue(self.game.waiting_for_remote)

        # 远程真人的这条路要一路答到底：回合里可能先来触发技的确认（【裸衣】
        # 一类）、再来出牌阶段、最后是弃牌 —— 真人也是这么点过来的。
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if self.game.current_turn_player is not self.remote:
                break
            self.auto_answer_remote()
            self.auto_answer_host()
            self.pump(0.1)
        self.assertIsNot(self.game.current_turn_player, self.remote,
                         "远程真人的回答没有推进权威对局")

    def test_scenario_h_remote_player_receives_its_own_turn_again(self):
        """第二轮里远程真人再收到一次请求：不是只有第一轮能打通。

        身份局可能在一圈之内就分出胜负（主公阵亡 / 反贼全灭），那时阵亡的
        座位自然不再行动——所以对局已经结束时按"死前参与过"计。
        """

        self.keep_everyone_durable()
        seen, _turns = self.advance_battle(rounds=2)
        alive = {player.player_id for player in self.game.players if player.alive}
        if self.game.game_over and self.remote.player_id not in alive:
            self.assertGreaterEqual(seen.get(self.remote.player_id, 0), 1,
                                    "远程真人在阵亡前应当参与过轮转")
            return
        self.assertGreaterEqual(seen.get(self.remote.player_id, 0), 2,
                                "远程真人没有走完两轮")


# ==================================================
# 房主是唯一权威：客户端不建第二套对局
# ==================================================

class HostAuthorityTests(LanIdentityRuntimeTestCase):

    def test_client_does_not_build_its_own_game(self):
        match = self.client.match
        self.assertEqual(type(match).__name__, "ClientMatch")
        self.assertFalse(hasattr(match, "game"), "客户端没有权威 Game")
        self.assertFalse(hasattr(match, "start_networked_battle"))
        self.assertFalse(hasattr(match, "deck"))

    def test_client_view_comes_from_the_host(self):
        self.host.match.push_views(force=True)
        self.pump(0.6)
        view = self.client.match.view
        self.assertEqual(view.game_mode, "identity")
        self.assertEqual(len(view.players), 5)
        self.assertEqual(view.match_id, self.host.match.match_id)

    def test_client_draws_the_identity_table_without_asking_for_rules(self):
        """客户端把身份局牌桌**真的画一帧**：拿到身份 / 武将 / 技能 / 体力 / 相位。

        只读视图没有规则层（没有第二个 Game），所以这一帧也在证明"渲染路径
        没有偷偷去问房主才有的东西"——例如 hand_limit 这类规则查询。
        """

        import pygame

        from src.renderer import Renderer
        from src.ui.remote_table import RemoteTableScene

        self.host.match.push_views(force=True)
        self.pump(0.6)
        pygame.display.init()
        pygame.font.init()
        screen = pygame.display.set_mode((1600, 1000))
        renderer = Renderer(screen)
        scene = RemoteTableScene(screen, renderer)
        scene.sync_layout(renderer.metrics)
        try:
            scene.update(0.02, self.client.match, advance_effects=True)
            scene.draw(self.client.match, renderer.metrics)
        finally:
            pygame.display.quit()

        view = scene.view
        self.assertEqual(len(view.players), 5, "客户端画出了五个座位")
        remote_view = view.player
        self.assertTrue(remote_view.general_id, "自己的武将在客户端可见")
        self.assertTrue(view.skills, "自己的技能栏非空")
        self.assertEqual(remote_view.seat, self.remote.seat)
        # 自己的身份在客户端的视图里是可见的（别人仍然看不到）。
        self.assertEqual(remote_view.identity, self.remote.identity.value)
        lord = self.players_of(Identity.LORD)[0]
        lord_view = next(item for item in view.players
                         if item.player_id == lord.player_id)
        self.assertEqual(lord_view.identity, Identity.LORD.value)

    def test_runtime_marker_reports_the_real_battle(self):
        """开发期 Marker 报告的就是真实开局结果（不是另一条路径的结论）。"""

        from src.game.runtime_marker import describe_battle

        text = describe_battle(self.game, origin="test")
        self.assertIn("game_mode = identity", text)
        self.assertIn("players = 5", text)
        self.assertIn("human = 2", text)
        self.assertIn("ai = 3", text)
        self.assertIn("identities_assigned = True", text)
        self.assertIn("generals_assigned = True", text)
        self.assertIn("主公 x1", text)

    def test_lobby_shows_the_real_battle_size_before_starting(self):
        """大厅显示的人数就是真正开局的人数（真人 + AI 补位）。"""

        # 重新开一间房来检查"开局之前"的状态。
        session = LanSession()
        ok, message = session.create_room("房主", 8, 0, game_mode="identity")
        self.assertTrue(ok, message)
        session.set_game(Game(ai_count=1))
        self.sessions.append(session)
        guest = LanSession()
        ok, message = guest.join_room("甲", "127.0.0.1", session.host.bound_port)
        self.assertTrue(ok, message)
        self.sessions.append(guest)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline and not guest.connected:
            session.poll()
            guest.poll()
            time.sleep(0.02)

        self.assertEqual(session.lobby.count, 2)
        self.assertEqual(session.planned_battle_size, 5)
        self.assertEqual(session.planned_ai_count, 3)
        self.assertEqual(guest.planned_battle_size, 5,
                         "客户端用大厅镜像算出的人数必须一致")


# ==================================================
# 模式：联机默认身份局，仍然可以选自由混战
# ==================================================

class MatchModeTests(unittest.TestCase):

    def test_default_lan_mode_is_identity(self):
        session = LanSession()
        self.assertEqual(session.game_mode, "identity")

    def test_free_for_all_room_keeps_two_players_and_no_identities(self):
        """房主显式选自由混战时，联机仍然是原来的 2 人无身份对局。"""

        session = LanSession()
        ok, message = session.create_room("房主", 2, 0, game_mode="ffa")
        self.assertTrue(ok, message)
        game = Game(ai_count=1)
        session.set_game(game)
        session.host.lobby.add_player("玩家A", player_id="p2")
        session.host.lobby.set_ready("p2", True)
        self.assertEqual(session.planned_battle_size, 2)
        self.assertEqual(session.planned_ai_count, 0)
        ok, message = session.start_match()
        self.assertTrue(ok, message)
        try:
            self.assertEqual(game.mode_id, "ffa")
            self.assertEqual(len(game.players), 2)
            self.assertTrue(all(player.identity is None
                                for player in game.players))
            # 自由混战联机沿用"不指定武将"的既有语义（联机没有选将界面）；
            # 身份局才是"必须有武将"的那一边。
            self.assertTrue(all(player.general_id is None
                                for player in game.players))
        finally:
            session.leave()

    def test_ai_seats_use_the_original_ai_controller(self):
        session = LanSession()
        ok, message = session.create_room("房主", 8, 0, game_mode="identity")
        self.assertTrue(ok, message)
        game = Game(ai_count=1)
        game.ai_pacing = True
        session.set_game(game)
        session.host.lobby.add_player("玩家A", player_id="p2")
        session.host.lobby.set_ready("p2", True)
        ok, message = session.start_match()
        self.assertTrue(ok, message)
        try:
            ai = [player for player in game.players
                  if player.controller_type is ControllerType.AI]
            self.assertEqual(len(ai), 3)
            controller = game.get_controller(ai[0])
            self.assertEqual(type(controller).__name__, "AIController")
            remote = next(player for player in game.players
                          if player.controller_type is ControllerType.REMOTE_HUMAN)
            self.assertEqual(type(game.get_controller(remote)).__name__,
                             "RemoteHumanController")
            self.assertEqual(type(game.get_controller(game.player)).__name__,
                             "HumanController")
        finally:
            session.leave()


class SkippedPlayPhaseTests(unittest.TestCase):
    """出牌阶段被跳过时的收尾（LAN 身份局跑通的前提）。

    【乐不思蜀】判定命中会把回合直接推进到弃牌阶段。AI 与远程真人各自由
    控制器收尾，本地真人原本**没有任何动作能结束这个回合**——整局会停在
    这里。这一组用例把它钉住。
    """

    def setUp(self):
        self.game = Game(ai_count=1)
        self.game.start_single_player()
        self.game.actions.clear()
        self.game.current_turn_player = self.game.player
        self.game.phase = "discard"

    def test_local_human_can_end_a_skipped_turn_when_hand_is_small(self):
        self.game.player.hand = []
        self.game.end_player_turn(self.game.player)
        self.assertIsNot(self.game.current_turn_player, self.game.player,
                         "本地真人必须能结束被跳过的回合")

    def test_local_human_must_discard_before_ending_an_over_limit_turn(self):
        from tests.legacy_helpers import canonical_card

        self.game.player.hp = 1
        self.game.player.hand = [canonical_card("SHA") for _ in range(3)]
        self.game.end_player_turn(self.game.player)
        self.assertIs(self.game.current_turn_player, self.game.player,
                      "手牌超过上限时必须先弃牌")
        self.assertIn("请弃置", self.game.message)

        self.game.actions.clear()
        guard = 0
        while (self.game.current_turn_player is self.game.player
               and self.game.phase == "discard" and guard < 20):
            guard += 1
            self.game.player_discard(0, (0, 0, 10, 10))
            for _ in range(400):
                if not self.game.busy:
                    break
                self.game.update(0.05)
        self.assertIsNot(
            self.game.current_turn_player, self.game.player,
            "弃够张数后回合应当收尾（手牌 %d / 上限 %d，消息：%s）" % (
                len(self.game.player.hand),
                self.game.hand_limit(self.game.player), self.game.message))


class SetupFlowTests(LanIdentityRuntimeTestCase):
    """联机开局流程：先看身份、再选将（与单机一致）。

    这一组**不跳过**流程，而是按真实顺序一步步走：房主确认身份 → 远程确认
    身份 → 各自从候选里选一个 → 才发牌开局。
    """

    def setUp(self):
        # 基类的 setUp 会把流程直接走完；这一组要自己控制节奏，所以从零建局。
        self.sessions = []
        self.host = LanSession()
        ok, message = self.host.create_room("房主", 8, 0, game_mode="identity")
        self.assertTrue(ok, message)
        self.game = Game(ai_count=1)
        self.host.set_game(self.game)
        self.sessions.append(self.host)
        self.client = self.join("远程玩家")
        self.wire = []
        original = self.client._route_client_game_message

        def route(message, _original=original):
            self.wire.append(message)
            return _original(message)

        self.client._route_client_game_message = route
        self.pump(1.5)
        self.assertTrue(self.client.connected, self.client.error)
        self.client.set_ready(True)
        self.assertTrue(self.wait(lambda: self.host.lobby.can_start(), 6.0))
        ok, message = self.host.start_match()
        self.assertTrue(ok, message)
        self.pump(1.0)
        self.match = self.host.match
        self.assertIsNotNone(self.match)

    def test_identity_arrives_before_any_card_is_dealt(self):
        """开局先看身份：牌还没发，身份已经到手。"""

        self.assertEqual(self.match.setup_stage, "identity")
        self.assertEqual(self.game.scene, "identity_reveal")
        self.assertEqual(self.client.match.my_identity,
                         self.remote.identity.value,
                         "远程玩家应当已经收到自己的身份")
        self.assertTrue(self.client.match.my_identity_name)
        self.assertEqual(self.client.match.lord_name,
                         self.game.mode.lord().name)
        # 还没发牌：手牌是空的，武将也没分配。
        self.assertEqual(sum(len(player.hand) for player in self.game.players), 0)
        self.assertTrue(all(not player.general_id for player in self.game.players))

    def test_only_your_own_identity_is_sent(self):
        """身份包只带**收件人自己**的身份：别人的身份一个都不发。"""

        payloads = [message.get("payload") or {} for message in self.wire
                    if (message.get("payload") or {}).get("identity")]
        self.assertTrue(payloads, "客户端至少收到过一条身份分配")
        for payload in payloads:
            self.assertEqual(payload.get("viewer_id"), self.remote.player_id)
            self.assertEqual(payload.get("identity"), self.remote.identity.value)
            # 载荷里只有"我自己的身份"这两个字段：别人的身份（哪怕是同一种
            # 身份）根本不以任何形式出现，更没有 player_id → identity 的映射。
            keys = {key for key in payload if "identit" in str(key).lower()}
            self.assertEqual(keys, {"identity", "identity_name"})
            self.assertNotIn("players", payload)

    def test_candidates_come_only_after_both_sides_confirmed(self):
        """身份没确认完，候选一个都不发。"""

        self.match.host_identity_confirmed()
        self.pump(0.4)
        self.assertFalse(self.client.match.candidates_ready,
                         "房主一个人确认还不该进入选将")
        self.assertEqual(self.match.setup_stage, "identity_done")

        self.match.identity_confirmed(self.remote.player_id)
        self.pump(0.5)
        self.assertEqual(self.match.setup_stage, "choosing")
        self.assertTrue(self.client.match.candidates_ready)
        self.assertEqual(len(self.client.match.candidates), 3,
                         "候选数量与模式声明一致")

    def test_candidates_are_disjoint_between_humans(self):
        self.match.host_identity_confirmed()
        self.match.identity_confirmed(self.remote.player_id)
        self.pump(0.5)
        host_candidates = set(self.match.human_candidates[self.match.host_player_id])
        remote_candidates = {
            item["general_id"] for item in self.client.match.candidates}
        self.assertTrue(host_candidates and remote_candidates)
        self.assertFalse(host_candidates & remote_candidates,
                         "两名真人的候选不该重叠（否则会出现两个相同的武将）")

    def test_pick_outside_the_candidates_is_refused(self):
        self.match.host_identity_confirmed()
        self.match.identity_confirmed(self.remote.player_id)
        self.pump(0.5)
        self.assertFalse(self.match.general_picked(self.remote.player_id, "caocao")
                         if "caocao" not in self.match.human_candidates[
                             self.remote.player_id]
                         else False)
        self.assertFalse(self.match.general_picked(self.remote.player_id, "no_such"))
        self.assertEqual(self.match.general_picks, {},
                         "非法选择不许写进权威状态")

    def test_host_can_pick_before_the_other_side_confirms(self):
        """房主比对方早一步点「确认出战」：选择要被记住、并在进入选将时自动补交。

        之前这种情况按钮毫无反应、提示还念着"点继续"，看起来整局卡死了。
        """

        pick = self.match.human_candidates[self.match.host_player_id][0]
        self.assertFalse(self.match.host_general_picked(pick),
                         "还没到选将阶段，不能真的开局")
        self.assertEqual(self.match.host_pick, pick, "选择必须先记下来")
        self.assertEqual(self.match.general_picks, {},
                         "还没到选将阶段，不能写进权威状态")

        self.match.host_identity_confirmed()
        self.assertEqual(self.match.setup_stage, "identity_done")
        self.match.identity_confirmed(self.remote.player_id)
        self.pump(0.4)
        self.assertEqual(self.match.setup_stage, "choosing")
        self.assertEqual(self.match.general_picks.get(self.match.host_player_id),
                         pick, "进入选将阶段后要自动补交房主的选择")

    def test_wait_message_names_who_is_being_waited_on(self):
        """等待提示必须说清楚在等谁、等什么（否则玩家只会觉得卡住了）。"""

        message = self.match.wait_message()
        self.assertIn(self.remote.name, message)
        self.assertIn("确认身份", message)

        self.match.host_identity_confirmed()
        self.match.identity_confirmed(self.remote.player_id)
        self.pump(0.4)
        self.assertIn("选武将", self.match.wait_message())

        self.match.host_general_picked(
            self.match.human_candidates[self.match.host_player_id][0])
        self.match.general_picked(self.remote.player_id,
                                  self.client.match.candidates[0]["general_id"])
        self.assertEqual(self.match.wait_message(), "",
                         "流完走完后不该还显示等待")

    def test_host_confirming_identity_through_the_ui_entry_sends_candidates(self):
        """房主点「继续」走的是**同一个入口**：切屏 + 通知网络桥 + 对方收到候选。

        曾经这里只切了屏、没通知网络桥，于是流程一直停在"等身份确认"，
        两边都显示等待、对方永远收不到候选武将——真实双开时就是这么卡住的。
        """

        from src.ui.lan_scene import LanScene

        import pygame

        pygame.display.init()
        pygame.font.init()
        screen = pygame.display.set_mode((1280, 720))
        scene = LanScene(screen)
        scene.bind_game(self.game)
        scene.session = self.host          # 复用已经连上的会话
        scene.session.set_game(self.game)

        # 客户端先确认身份，房主后点「继续」。
        self.assertTrue(self.client.match.confirm_identity())
        self.pump(0.4)
        self.assertEqual(self.match.setup_stage, "identity",
                         "房主还没确认，流程不该往下走")

        scene.host_confirm_identity(self.game)
        self.assertEqual(self.game.scene, "general_select")
        self.pump(0.6)
        self.assertEqual(self.match.setup_stage, "choosing")
        self.assertTrue(self.client.match.candidates_ready,
                        "房主确认之后，对方必须收到候选武将")
        self.assertEqual(len(self.client.match.candidates), 3)

    def test_battle_starts_only_after_everyone_picked_and_uses_their_choice(self):
        self.match.host_identity_confirmed()
        self.match.identity_confirmed(self.remote.player_id)
        self.pump(0.5)
        host_pick = self.match.human_candidates[self.match.host_player_id][0]
        remote_pick = self.client.match.candidates[0]["general_id"]

        self.assertFalse(self.match.host_general_picked(host_pick),
                         "只选了一个人还不该开局")
        self.assertEqual(self.match.setup_stage, "choosing")
        self.assertTrue(self.match.general_picked(self.remote.player_id, remote_pick))
        self.assertEqual(self.match.setup_stage, "battle")
        self.pump(0.8)

        self.assertEqual(self.game.player.general_id, host_pick,
                         "房主拿到的是自己选的武将")
        self.assertEqual(self.remote.general_id, remote_pick,
                         "远程玩家拿到的是自己选的武将")
        # 开局就发 4 张；如果这期间已经轮到他，还会再多摸（所以用 >=）。
        self.assertGreaterEqual(len(self.remote.hand), 4, "开局后发牌")
        self.assertTrue(self.client.match.ready)
        self.assertEqual(self.game.scene, "game")


class CardAnimationVisibilityTests(unittest.TestCase):
    """卡牌动画的可见性：别人的摸牌一路画牌背。

    之前是无条件画牌面，于是
    * 房主侧：AI 摸到什么牌顺着动画全漏出来了；
    * 客户端：别人的摸牌用的是"内容未知"的占位牌（没有名字、没有花色），
      画出来是一片空白，满屏白牌飞来飞去。
    """

    def setUp(self):
        from src.game import Game

        self.game = Game(ai_count=1)
        self.game.start_single_player()
        self.game.actions.clear()

    def flying_moves(self):
        queue = self.game.actions
        moves = [action for action in list(queue.queue)
                 if hasattr(action, "face_down")]
        queue.clear()
        return moves

    def test_other_players_draws_are_not_animated(self):
        """别人的摸牌**不播飞行动画**：牌直接进手牌，界面上只有"手牌 ×N"跳动。

        这是当前实现的明确选择（见 ``core.queue_draw_cards`` 的说明：牌背在
        深色桌面上是一串浅色方块飞过，既没有信息量又很吵）。旧版本这条测试
        要求"两张牌背动画"，与实现不一致——因此这里断言的是**没有**动画，
        以及"牌确实进了对方的手牌"。
        """

        before = len(self.game.players[1].hand)
        self.game.queue_draw_cards(self.game.players[1], 2, (0, 0, 10, 10))
        moves = self.flying_moves()
        self.assertEqual(len(moves), 0,
                         "别人的摸牌不该产生飞行动画（更不该画出牌面）")
        self.assertEqual(len(self.game.players[1].hand), before + 2,
                         "牌必须直接进入对方手牌")

    def test_own_draws_show_the_face(self):
        self.game.queue_draw_cards(self.game.player, 2, (0, 0, 10, 10))
        moves = self.flying_moves()
        self.assertEqual(len(moves), 2)
        self.assertFalse(any(move.face_down for move in moves),
                         "自己摸的牌要看得到牌面")

    def test_client_hidden_cards_are_face_down(self):
        """客户端造出来的"内容未知"占位牌必须带 face_down（渲染层据此画牌背）。"""

        from src.ui.view_adapter import CardCache

        card = CardCache().placeholder()
        self.assertTrue(card.face_down)
        self.assertEqual(card.name, "")

    def test_discard_animation_still_shows_the_card(self):
        """弃牌是公开信息：弃牌动画照旧画牌面。"""

        from tests.legacy_helpers import normal_sha

        self.game.queue_to_discard(normal_sha(), (0, 0, 10, 10))
        moves = self.flying_moves()
        self.assertEqual(len(moves), 1)
        self.assertFalse(moves[0].face_down)


class MainEntryPointTests(unittest.TestCase):
    """main.py 是脚本，事件循环跑不了单测；这里守住它**必须调用**的那几个入口。

    房主点「继续」曾经只切了屏、没通知网络桥，真实双开时两边就一直互相等，
    看起来像整局卡死。所以那一行必须存在。
    """

    def setUp(self):
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "main.py"), encoding="utf-8") as handle:
            self.source = handle.read()

    def test_host_identity_confirm_goes_through_the_shared_entry(self):
        self.assertIn("lan_scene.host_confirm_identity(game)", self.source,
                      "房主确认身份必须走统一入口（切屏 + 通知网络桥）")

    def test_host_general_pick_is_reported_to_the_bridge(self):
        self.assertIn("lan_scene.host_pick_general(game)", self.source,
                      "房主选将必须回给网络桥，而不是开单机局")


if __name__ == "__main__":
    unittest.main()
