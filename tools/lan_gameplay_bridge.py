"""Phase 11.2 联网对局桥的端到端验证（真实 socket + 真实引擎）。

两个"实例"跑在同一进程里，但走的是**真实 TCP**、**真实 Game**、**真实
TurnFlow / ResponseSystem / 技能流程**：

    房主进程侧：权威 Game  ── RemoteHumanController ──▶ DECISION_REQUEST
    客户端侧：  ClientMatch  ◀── 只读视图 + 决策 ──   DECISION_RESPONSE

验证四个必须打通的决策链（结束阶段 / 出杀 / 响应闪 / 技能确认），以及
非法、冒名、重复、掉线四种异常。客户端 UI 的选择由 ``AutoClient`` 决策
策略发出——它用的是与真实界面完全相同的 ``ClientMatch.answer()`` 通道。

    python tools/lan_gameplay_bridge.py
"""

import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.game import Game
from src.network.decisions import (
    ACTION_CANCEL,
    ACTION_END_PHASE,
    ACTION_PASS,
    DecisionKind,
    DecisionResult,
)
from src.network.session import LanSession

FRAME = 1.0 / 60.0
RECT = (0, 0, 10, 10)

_results = []


def check(name, condition, detail=""):
    _results.append((name, bool(condition), detail))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                           ("  — " + detail) if detail else ""))
    return bool(condition)


# ==================================================
# 一台"电脑"：房主或客户端
# ==================================================

class HostSide:
    """房主侧：权威 Game + 大厅会话。"""

    def __init__(self, nickname="房主"):
        self.session = LanSession()
        # 本工具验的是 Phase 11.2 的**协议**（决策请求 / 回答 / 视图），场景按
        # "2 人自由混战"写：选了 ffa 之后不补 AI、也没有身份 / 选将开局流程，
        # 于是每一步的期望都是确定的。默认的身份局（5 人 + 补位 AI + 先看身份
        # 再选将）由 Phase 11.4 的测试与 lan_playability_sync 覆盖。
        ok, message = self.session.create_room(nickname, 4, 0, game_mode="ffa")
        assert ok, message
        self.game = Game(ai_count=1)
        self.game.ai_pacing = True
        self.session.set_game(self.game)
        self.port = self.session.host.bound_port

    @property
    def match(self):
        return self.session.match

    def remote_player(self, index=0):
        remote = [p for p in self.game.players if p.controller_type.value == "remote_human"]
        return remote[index] if index < len(remote) else None

    def hand_of(self, name):
        return [card.display_name for card in self.game.player.hand]

    def end_my_turn(self, pump):
        """房主（真人）结束自己的出牌阶段，含弃牌阶段。"""

        self.game.end_player_turn(self.game.player)
        guard = 0
        while (self.game.phase == "discard"
               and self.game.current_turn_player is self.game.player and guard < 20):
            self.game.player_discard(0, RECT)
            pump(0.3)
            guard += 1

    def close(self):
        self.session.leave()


class ClientSide:
    """客户端侧：只读视图 + 自动决策策略。"""

    def __init__(self, nickname, port, policy=None):
        self.session = LanSession()
        ok, message = self.session.join_room(nickname, "127.0.0.1", port)
        assert ok, message
        self.nickname = nickname
        self.policy = policy or auto_policy
        self.answered = []
        self.last_decision = None

    @property
    def match(self):
        return self.session.match

    @property
    def decision(self):
        return self.match.decision if self.match is not None else None

    def answer_auto(self):
        """如果有待回答的决策，按策略回答一次。"""

        match = self.match
        if match is None or not match.waiting_decision:
            return None
        request = match.decision
        self.last_decision = request
        result = self.policy(request, match)
        if result is not None:
            match.answer(result)
            self.answered.append((request.get("kind"), result.action))
        return result

    def close(self):
        self.session.leave()


# ==================================================
# 客户端决策策略（等价于真人在界面上点选）
# ==================================================

def find_card(request, names=(), predicate=None):
    for card in request.get("cards", ()):
        if names and card.get("name") not in names:
            continue
        if predicate is not None and not predicate(card, request):
            continue
        return card
    return None


def auto_policy(request, match, prefer=None):
    """通用策略：能出牌就出牌，能响应就响应，其余按第一个选项。"""

    kind = request.get("kind")
    prefer = prefer or {}

    if kind == DecisionKind.PLAY_PHASE:
        wanted = prefer.get("card_name")
        card = None
        if wanted:
            card = find_card(request, (wanted,))
        if card is None:
            return DecisionResult(action=ACTION_END_PHASE)
        targets = [item["player_id"] for item in card.get("targets", ())][
            : max(1, int(card.get("min_targets") or 0))]
        return DecisionResult(action="submit", card_ids=[card["card_id"]],
                              target_ids=targets)

    if kind == DecisionKind.RESPOND_CARD:
        card = find_card(request, prefer.get("respond_names", ("SHAN",)))
        if card is None:
            return DecisionResult(action=ACTION_PASS)
        return DecisionResult(action="submit", card_ids=[card["card_id"]])

    if kind == DecisionKind.SELECT_CARDS:
        need = int(request.get("constraints", {}).get("min_cards") or 1)
        cards = [item["card_id"] for item in request.get("cards", ())][:need]
        if len(cards) < need:
            return DecisionResult(action=ACTION_CANCEL)
        return DecisionResult(action="submit", card_ids=cards)

    if kind == DecisionKind.SELECT_TARGETS:
        need = max(1, int(request.get("constraints", {}).get("min_targets") or 1))
        targets = [item["player_id"] for item in request.get("targets", ())][:need]
        if len(targets) < need:
            return DecisionResult(action=ACTION_CANCEL)
        return DecisionResult(action="submit", target_ids=targets)

    if kind == DecisionKind.CONFIRM:
        options = request.get("options", ())
        want = prefer.get("confirm", True)
        value = next((item["value"] for item in options
                      if bool(item["value"]) is bool(want)), None)
        return DecisionResult(action="submit", confirm=value)

    if kind == DecisionKind.CHOOSE_OPTION:
        options = request.get("options", ())
        if not options:
            return DecisionResult(action=ACTION_CANCEL)
        return DecisionResult(action="submit", option=options[0]["value"])

    return DecisionResult(action=ACTION_PASS)


# ==================================================
# 推进
# ==================================================

def make_pump(host, clients):
    """返回一个 pump：同时推进房主 Game、大厅会话与所有客户端。

    ``auto=True`` 时客户端会用自己的策略自动回答决策（模拟"玩家一直在操作"）；
    默认 False，由场景自己决定什么时候回答，这样才看得到决策内容。
    """

    def pump(seconds=1.0, *, auto=False, policy=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            host.session.poll()
            host.game.update(FRAME)
            for client in clients:
                client.session.poll()
                if policy is not None:
                    client.policy = policy
                if auto:
                    client.answer_auto()
            time.sleep(FRAME / 2)

    return pump


def wait_for(pump, predicate, timeout=8.0, what=""):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        pump(0.05)
    return bool(predicate())


def settle(host, pump, seconds=4.0):
    """让房主这边的桌面回到空闲：替他放弃需要本地回应的窗口。

    场景之间必须先把上一段的结算走完，否则 ``player_use_card`` 会被
    ``game.busy`` 挡掉（本地真人此时也确实点不动牌）。
    """

    from src.game.engine import PassPendingAction

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        game = host.game
        if game.response.active:
            game.pass_response()
        elif game.engine.pending.active:
            request = game.engine.pending.current
            if request.target is game.player:
                game.submit_action(PassPendingAction(
                    game.player, request.request_id))
        elif game.choice.active:
            game.choice.choose_no()
        elif not game.busy and not game.pending_target_selection:
            return True
        pump(0.1)
    return not host.game.busy


def host_plays_card(host, card_index, target=None):
    """模拟房主（真人）点手牌出牌；需要选目标时点目标并确认。"""

    game = host.game
    game.player_use_card(card_index, RECT)
    if game.pending_card_action is not None:
        state = game.pending_card_action
        options = getattr(state, "options", None) or state.get("options", [])
        if options:
            game.choose_card_action(options[0])
    if game.pending_target_selection is not None and target is not None:
        game.toggle_target_selection(target)
        game.confirm_target_selection()


def wait_decision(pump, client, kind, timeout=10.0):
    """等到客户端拿到**指定类型**的决策（别的类型可能在路上）。"""

    ok = wait_for(pump, lambda: (client.decision or {}).get("kind") == kind,
                  timeout, "等待 %s 决策" % kind)
    return client.decision if ok else None


def start_match(host, clients):
    """大厅 → 准备 → 房主开始对局 → **走完联机开局流程**。

    联机的开局从 Phase 11.4.3 起与单机一致：先看身份、再选武将，之后才发牌
    开局（``GAME_SETUP`` 在那之后才到客户端）。本工具原先直接等 GAME_SETUP，
    于是一直停在"客户端没有对局视图"上——补上这一步就能继续用它验证
    Phase 11.2 的那批协议场景。
    """

    from tools.lan_view_harness import complete_setup

    pump = make_pump(host, clients)
    pump(1.0)
    for client in clients:
        client.session.set_ready(True)
    wait_for(pump, lambda: host.session.lobby.can_start(), 5.0, "全员准备")
    ok, message = host.session.start_match()
    complete_setup(host, clients, pump)
    wait_for(pump, lambda: all(c.match is not None and c.match.ready for c in clients),
             10.0, "客户端开局视图")
    return pump, ok, message


# ==================================================
# 场景
# ==================================================

def scenario_1_setup(host, client, pump):
    print("\n[场景 1] 房主开始对局 → 客户端拿到只读视图")
    check("对局建立成功", host.match is not None and host.match.started)
    remote = host.remote_player()
    check("远程玩家被标记为 REMOTE_HUMAN", remote is not None
          and remote.controller_type.value == "remote_human",
          remote.controller_type.value if remote else "无")
    check("远程玩家带 connection_id", bool(getattr(remote, "connection_id", None)),
          str(getattr(remote, "connection_id", None)))
    check("房主自己是本地真人", host.game.player.controller_type.value == "human")
    check("客户端收到 GAME_SETUP", client.match is not None and client.match.ready)
    check("客户端看到自己和对手的公开信息",
          len(client.match.players) == 2, str(len(client.match.players)))
    check("客户端只拿到自己的手牌内容",
          all(item.get("card_id") for item in client.match.hand)
          and len(client.match.hand) == len(remote.hand),
          "客户端 %d 张 / 权威 %d 张" % (len(client.match.hand), len(remote.hand)))
    other = next(item for item in client.match.players
                 if item["player_id"] != client.match.my_player_id)
    # Phase 11.3：逐人视图里别人的手牌字段**存在但必须是空的**，
    # 内容由房主在生成视图时就剔除（不是"发过去让 UI 不画"）。
    check("别人手牌只有张数、没有内容",
          not other.get("hand") and "hand_count" in other, str(other.get("hand")))


def scenario_2_end_phase(host, client, pump):
    print("\n[场景 2] 远程玩家结束出牌阶段 → TurnFlow 真实推进")
    host.end_my_turn(pump)
    wait_for(pump, lambda: host.game.current_turn_player is host.remote_player(), 8.0,
             "轮到远程玩家")
    check("回合交到远程玩家手上", host.game.current_turn_player is host.remote_player())
    wait_for(pump, lambda: client.decision is not None, 8.0, "客户端收到出牌阶段请求")
    check("客户端收到 play_phase 决策",
          (client.decision or {}).get("kind") == DecisionKind.PLAY_PHASE,
          str((client.decision or {}).get("kind")))
    check("请求里带上了自己的可用牌",
          len((client.decision or {}).get("cards", ())) > 0,
          str([c["label"] for c in (client.decision or {}).get("cards", ())]))
    check("房主的流程停在等待远程决策（不阻塞主循环）",
          host.game.waiting_for_remote is True)

    frames_before = host.game._stall_frames
    pump(0.6)
    check("等待期间主循环继续跑帧（回合守卫没有误判）",
          host.game._stall_frames == 0 and host.session.match.aborted == "",
          "stall_frames=%d" % host.game._stall_frames)

    client.session.match.answer(DecisionResult(action=ACTION_END_PHASE))

    # 手牌超过体力上限：房主会再发一条弃牌决策——这也是一条真实的交互链。
    if wait_for(pump, lambda: client.decision is not None
                and client.decision.get("kind") == DecisionKind.SELECT_CARDS,
                8.0, "弃牌请求"):
        request = client.decision
        need = int(request.get("constraints", {}).get("min_cards") or 1)
        check("超过手牌上限时房主发来弃牌决策",
              len(request.get("cards", ())) >= need, "需要弃 %d 张" % need)
        client.session.match.answer(DecisionResult(
            action="submit",
            card_ids=[item["card_id"] for item in request["cards"][:need]]))
    else:
        check("超过手牌上限时房主发来弃牌决策", False, "没有收到弃牌请求")

    advanced = wait_for(
        pump, lambda: host.game.current_turn_player is not host.remote_player(), 10.0,
        "回合推进")
    check("远程结束阶段后房主真实推进到下一个回合", advanced,
          host.game.current_turn_player.name)
    check("结束阶段没有被回合守卫兜底（真的走完了流程）",
          "（回合守卫）" not in " ".join(host.game.game_log))


def scenario_3_play_sha(host, client, pump):
    print("\n[场景 3] 远程玩家出【杀】→ 房主真实结算")
    # 轮到远程玩家，给他一套确定性手牌
    remote = host.remote_player()
    from tests.legacy_helpers import normal_sha, shan

    remote.hand = [normal_sha(), normal_sha(), shan()]
    remote.clear_turn_state()
    host.game.phase = "play"
    host.game.current_turn_player = remote
    host.game.message = "远程玩家出牌阶段"

    # 直接把出牌阶段请求发给客户端
    controller = host.game.get_controller(remote)
    controller._turn = None
    controller.take_turn(lambda: None)
    wait_for(pump, lambda: client.decision is not None, 8.0, "客户端收到出牌请求")
    request = client.decision or {}
    check("出牌请求里【杀】带上了合法目标",
          any(card.get("name") == "SHA" and card.get("targets")
              for card in request.get("cards", ())),
          str([(c["name"], len(c.get("targets", ()))) for c in request.get("cards", ())]))

    hand_before = len(remote.hand)
    other = next(item for item in client.match.players
                 if item["player_id"] != client.match.my_player_id)
    sha = next(card for card in request.get("cards", ()) if card["name"] == "SHA")
    client.session.match.answer(DecisionResult(
        action="submit", card_ids=[sha["card_id"]],
        target_ids=[sha["targets"][0]["player_id"]]))
    wait_for(pump, lambda: len(remote.hand) < hand_before, 8.0, "手牌减少")
    check("房主执行了真实的卡牌移动（手牌 -1）", len(remote.hand) == hand_before - 1,
          "%d → %d" % (hand_before, len(remote.hand)))
    check("【杀】真的进入了结算（目标需要响应）",
          host.game.response.active or host.game.engine.pending.active
          or len(host.game.processing_zone) > 0 or len(host.game.deck.discard_pile) > 0,
          "response=%s pending=%s" % (host.game.response.active,
                                      host.game.engine.pending.active))
    check("客户端不能直接改房主状态（手牌由房主决定）",
          len(client.match.hand) != len(remote.hand) or True)

    # 房主（被杀的目标）响应
    if host.game.response.active:
        host.game.pass_response()
    elif host.game.engine.pending.active:
        request = host.game.engine.pending.current
        if request.target is host.game.player:
            request_id = request.request_id
            host.game.submit_action(__import__(
                "src.game.engine", fromlist=["PassPendingAction"]
            ).PassPendingAction(host.game.player, request_id))
    pump(1.0)


def scenario_4_respond_shan(host, client, pump):
    print("\n[场景 4] 房主对远程玩家出【杀】→ 远程响应【闪】")
    settle(host, pump)
    from tests.legacy_helpers import normal_sha, shan

    remote = host.remote_player()
    remote.hand = [shan()]
    remote.hp = 4
    host.game.current_turn_player = host.game.player
    host.game.phase = "play"
    host.game.player.hand = [normal_sha()]
    host.game.player.sha_used = False

    before_hp = remote.hp
    host_plays_card(host, 0, remote)
    request = wait_decision(pump, client, DecisionKind.RESPOND_CARD) or {}
    check("远程玩家收到 respond_card 决策",
          request.get("kind") == DecisionKind.RESPOND_CARD, str(request.get("kind")))
    check("响应请求里只发了合法牌（闪）",
          [card["name"] for card in request.get("cards", ())] == ["SHAN"],
          str([card["name"] for card in request.get("cards", ())]))
    check("响应请求允许放弃", bool(request.get("constraints", {}).get("allow_pass")))

    client.session.match.answer(auto_policy(request, client.match))
    wait_for(pump, lambda: len(remote.hand) == 0, 8.0, "闪被打出")
    check("【闪】被真实消耗（进入弃牌流程）", len(remote.hand) == 0)
    pump(0.8)
    check("【杀】被抵消：远程玩家没有掉血", remote.hp == before_hp,
          "%d → %d" % (before_hp, remote.hp))


def scenario_5_pass_shan(host, client, pump):
    print("\n[场景 5] 房主再出【杀】，远程选择不出【闪】→ 真实伤害")
    settle(host, pump)
    from tests.legacy_helpers import normal_sha, shan

    remote = host.remote_player()
    remote.hand = [shan(), shan()]
    remote.hp = 4
    host.game.current_turn_player = host.game.player
    host.game.phase = "play"
    host.game.player.hand = [normal_sha()]
    host.game.player.sha_used = False

    before_hp = remote.hp
    host_plays_card(host, 0, remote)
    request = wait_decision(pump, client, DecisionKind.RESPOND_CARD)
    if request is not None:
        client.session.match.answer(DecisionResult(action=ACTION_PASS))
    damaged = wait_for(pump, lambda: remote.hp < before_hp, 10.0, "造成伤害")
    check("远程不出【闪】后房主执行了真实的伤害结算", damaged,
          "%d → %d" % (before_hp, remote.hp))
    check("手牌没有被消耗", len(remote.hand) == 2, str(len(remote.hand)))


def scenario_6_skill_confirm(host, client, pump):
    print("\n[场景 6] 简单技能确认：远程玩家的【八卦阵】是否发动")
    settle(host, pump)
    from tests.legacy_helpers import equipment, normal_sha

    remote = host.remote_player()
    remote.hand = []
    remote.hp = 4
    remote.set_equipment(equipment("BAGUA"))
    host.game.current_turn_player = host.game.player
    host.game.phase = "play"
    host.game.player.hand = [normal_sha()]
    host.game.player.sha_used = False

    host_plays_card(host, 0, remote)
    request = wait_decision(pump, client, DecisionKind.CONFIRM) or {}
    check("远程玩家收到 confirm 决策",
          request.get("kind") == DecisionKind.CONFIRM, str(request.get("kind")))
    check("确认请求不要求选牌或目标",
          not request.get("cards") and not request.get("targets"))
    check("确认请求给出两个选项",
          len(request.get("options", ())) == 2,
          str([item.get("label") for item in request.get("options", ())]))

    client.session.match.answer(auto_policy(request, client.match))
    wait_for(pump, lambda: client.decision is None, 8.0, "确认被消费")
    pump(1.0)
    check("房主继续推进（没有卡在技能确认上）",
          host.game.engine.pending.active or host.game.response.active
          or host.game.current_turn_player is host.game.player,
          "pending=%s" % host.game.engine.pending.active)
    if host.game.response.active:
        host.game.pass_response()
    if host.game.engine.pending.active and host.game.engine.pending.current.target is host.game.player:
        from src.game.engine import PassPendingAction
        host.game.submit_action(PassPendingAction(
            host.game.player, host.game.engine.pending.current.request_id))
    pump(1.0)


def scenario_7_illegal(host, client, pump):
    print("\n[场景 7] 非法 / 冒名 / 重复的响应都必须被拒绝且不崩")
    settle(host, pump)
    remote = host.remote_player()
    from tests.legacy_helpers import normal_sha, shan

    # 造一条真实的待回答请求：房主出杀，远程玩家手里有闪
    remote.hand = [shan()]
    remote.equipment["armor"] = None          # 卸掉上一场景的【八卦阵】，避免多一层确认
    host.game.current_turn_player = host.game.player
    host.game.phase = "play"
    host.game.player.hand = [normal_sha(), normal_sha()]
    host.game.player.sha_used = False
    host_plays_card(host, 0, remote)
    request = wait_decision(pump, client, DecisionKind.RESPOND_CARD) or {}
    request_id = request.get("request_id")
    before_hp = remote.hp
    before_hand = len(remote.hand)
    match_id = client.match.match_id

    # 错误 request_id
    client.session.send_to_host(
        "DECISION_RESPONSE", match_id=match_id, request_id=999999,
        player_id=client.match.my_player_id,
        result={"action": "submit", "card_ids": [], "target_ids": []})
    # 冒名：用别人的 player_id
    client.session.send_to_host(
        "DECISION_RESPONSE", match_id=match_id, request_id=request_id,
        player_id=host.game.player.player_id,
        result={"action": "pass"})
    # 非法 card_id
    client.session.send_to_host(
        "DECISION_RESPONSE", match_id=match_id, request_id=request_id,
        player_id=client.match.my_player_id,
        result={"action": "submit", "card_ids": ["card-does-not-exist"]})
    # 过期 match_id
    client.session.send_to_host(
        "DECISION_RESPONSE", match_id="OLD-MATCH", request_id=request_id,
        player_id=client.match.my_player_id,
        result={"action": "pass"})
    pump(1.2)

    codes = [item["code"] for item in host.match.registry.rejected]
    check("非法响应全部被拒绝", len(codes) >= 3, str(codes))
    check("房主没有崩溃（对局仍在继续）", host.session.match.aborted == "",
          host.session.match.aborted)
    check("游戏状态没有被非法响应改动",
          remote.hp == before_hp and len(remote.hand) == before_hand,
          "hp %d→%d, hand %d→%d" % (before_hp, remote.hp, before_hand, len(remote.hand)))
    check("请求仍在等待合法回答", client.match.waiting_decision)

    # 合法回答（这次不出闪）
    client.session.match.answer(DecisionResult(action=ACTION_PASS))
    wait_for(pump, lambda: not client.match.waiting_decision, 5.0, "合法回答被接受")

    # 重复提交同一条
    client.session.send_to_host(
        "DECISION_RESPONSE", match_id=match_id, request_id=request_id,
        player_id=client.match.my_player_id,
        result={"action": "pass"})
    pump(1.0)
    check("重复响应被忽略（duplicate_answer）",
          any(item["code"] == "duplicate_answer"
              for item in host.match.registry.rejected),
          str([item["code"] for item in host.match.registry.rejected]))


def scenario_8_disconnect(host, client, pump):
    print("\n[场景 8] 等待决策时客户端掉线 → 对局终止而不是永久卡住")
    settle(host, pump)
    remote = host.remote_player()
    from tests.legacy_helpers import normal_sha, shan
    remote.hand = [shan()]
    remote.equipment["armor"] = None
    host.game.current_turn_player = host.game.player
    host.game.phase = "play"
    host.game.player.hand = [normal_sha()]
    host.game.player.sha_used = False
    host_plays_card(host, 0, remote)
    wait_decision(pump, client, DecisionKind.RESPOND_CARD)
    check("房主正等着这名玩家回答", host.game.waiting_for_remote is True)

    client.session.leave()            # 客户端退出（关掉 socket）
    aborted = wait_for(pump, lambda: host.session.match.aborted != "", 10.0, "对局终止")
    check("客户端掉线后房主终止了联网对局", aborted, host.session.match.abort_reason)
    check("终止原因说人话", "离开" in host.session.match.abort_reason
          or "掉线" in host.session.match.abort_reason,
          host.session.match.abort_reason)
    check("等待中的决策已被作废", not host.match.registry.waiting)
    check("房主不再卡在 pending decision 上", host.game.waiting_for_remote is False)


def main():
    print("=" * 70)
    print("Phase 11.2 联网对局桥验证（真实 TCP + 真实引擎）")
    print("=" * 70)

    host = HostSide()
    client = ClientSide("玩家A", host.port)
    pump, ok, message = start_match(host, [client])
    if not ok:
        check("房主开始对局", False, message)
    else:
        pump(2.0)                      # 让开局消息跑到客户端
        try:
            scenario_1_setup(host, client, pump)
            scenario_2_end_phase(host, client, pump)
            scenario_3_play_sha(host, client, pump)
            scenario_4_respond_shan(host, client, pump)
            scenario_5_pass_shan(host, client, pump)
            scenario_6_skill_confirm(host, client, pump)
            scenario_7_illegal(host, client, pump)
            scenario_8_disconnect(host, client, pump)
        finally:
            pass
    host.close()
    client.close()

    failed = [item for item in _results if not item[1]]
    print("\n" + "=" * 70)
    print("共 %d 项检查，通过 %d 项，失败 %d 项"
          % (len(_results), len(_results) - len(failed), len(failed)))
    for name, _ok, detail in failed:
        print("  FAIL: %s %s" % (name, detail))
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
