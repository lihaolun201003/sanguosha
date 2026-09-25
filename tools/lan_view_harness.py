"""Phase 11.3 联机验证的公共脚手架（真实 socket + 真实引擎 + 真实渲染）。

两个"进程"跑在同一个 Python 进程里，但走的是真实 TCP：

    房主侧：权威 Game  ── 逐人 ViewBuilder ── GAME_VIEW_SNAPSHOT
                        └─ 表现事件桥 ────── GAME_EVENT
    客户端：ClientGameView ── 渲染适配层 ── 既有 Renderer / FX
                                       └── DECISION_RESPONSE

客户端侧不是"假客户端"：它真的建了一个 ``Renderer``（SDL dummy 驱动），
真的用 ``RemoteTableScene`` 画每一帧，决策也是真的从牌桌上点出来的
（``scene.handle_event`` / ``match.answer``），不走任何后门。

**重要工具**：``ClientSide.raw_messages`` 记录该客户端从 socket 上收到的每一条
消息（解码后的 JSON）。隐藏信息是靠比对**原始载荷**证明的，不是靠 UI 没画。
"""

import json
import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame                                              # noqa: E402

from src.game import Game                                   # noqa: E402
from src.network.decisions import (                         # noqa: E402
    ACTION_END_PHASE,
    ACTION_PASS,
    DecisionKind,
    DecisionResult,
)
from src.network.session import LanSession                  # noqa: E402

FRAME = 1.0 / 60.0
RECT = (0, 0, 10, 10)
SCREEN_SIZE = (1600, 1000)

_results = []


def check(name, condition, detail=""):
    _results.append((name, bool(condition), detail))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                           ("  — " + detail) if detail else ""))
    return bool(condition)


def summary(title):
    passed = sum(1 for _n, ok, _d in _results if ok)
    failed = [name for name, ok, _d in _results if not ok]
    print("\n" + "=" * 60)
    print("%s：%d/%d 通过" % (title, passed, len(_results)))
    if failed:
        print("失败项：")
        for name in failed:
            print("  - " + name)
    print("=" * 60)
    return not failed


# ==================================================
# 房主侧
# ==================================================

class HostSide:
    """房主侧：权威 Game + 网络会话。"""

    def __init__(self, nickname="房主", ai_count=1, game_mode="ffa", roster_seed=0):
        self.session = LanSession()
        # 模式属于**大厅**（联机开局的唯一来源）：先选好模式再建局，HostMatch
        # 会把它真正应用到权威 Game 上。这里不再直接改 ``game.mode_id``，否则
        # "大厅显示的模式"与"真正在跑的模式"就会分叉——那正是 Phase 11.4.3
        # 查出的根因之一。
        ok, message = self.session.create_room(nickname, 8, 0, game_mode=game_mode)
        assert ok, message
        self.game = Game(ai_count=ai_count)
        self.game.ai_pacing = True
        self.session.set_game(self.game)
        self.port = self.session.host.bound_port

    @property
    def match(self):
        return self.session.match

    def player(self, player_id):
        for item in self.game.players:
            if item.player_id == player_id:
                return item
        return None

    def remote_players(self):
        return [p for p in self.game.players
                if p.controller_type.value == "remote_human"]

    def close(self):
        self.session.leave()


# ==================================================
# 客户端侧（真实渲染）
# ==================================================

class ClientSide:
    """客户端侧：只读视图 + 真实 Renderer + 决策策略。"""

    def __init__(self, nickname, port, policy=None, render=False):
        self.session = LanSession()
        ok, message = self.session.join_room(nickname, "127.0.0.1", port)
        assert ok, message
        self.nickname = nickname
        self.policy = policy or (lambda request: DecisionResult(action=ACTION_PASS))
        self.answered = []
        self.raw_messages = []
        self.last_decision = None
        #: 已经用过的 request_id：场景之间不会再把旧请求当成新请求。
        self.seen_requests = set()
        self.renderer = None
        self.scene = None
        self.screen = None
        self.frame_errors = 0
        if render:
            self._build_renderer()
        # 记录**从 socket 上收到的每一条消息**（解码后的 JSON 载荷），
        # 用来证明隐藏信息根本没发出来。
        original = self.session._route_client_game_message

        def route(message, _original=original):
            self.raw_messages.append(message)
            return _original(message)

        self.session._route_client_game_message = route

    # ---- 真实渲染链路 ----

    def _build_renderer(self):
        from src.renderer import Renderer
        from src.ui.remote_table import RemoteTableScene

        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode(SCREEN_SIZE)
        self.renderer = Renderer(self.screen)
        self.scene = RemoteTableScene(self.screen, self.renderer)
        self.scene.sync_layout(self.renderer.metrics)

    def draw_frame(self):
        """真的画一帧（既有 Renderer + 客户端决策层）。"""

        if self.scene is None:
            return False
        match = self.match
        if match is None or not match.ready:
            return False
        try:
            self.scene.draw(match, self.renderer.metrics)
        except Exception as error:                        # pragma: no cover
            self.frame_errors += 1
            if self.frame_errors <= 1:
                import traceback
                print("      [渲染异常] %s: %s" % (type(error).__name__, error))
                traceback.print_exc()
            return False
        return True

    def update_frame(self, dt=FRAME):
        """走一遍客户端真实的一帧：消费表现事件 + 推进动画 + 绘制。"""

        if self.scene is None:
            return False
        match = self.match
        if match is not None and match.ready:
            try:
                self.scene.update(dt, match, advance_effects=True)
            except Exception as error:                    # pragma: no cover
                self.frame_errors += 1
                if self.frame_errors <= 1:
                    import traceback
                    print("      [表现异常] %s: %s" % (type(error).__name__, error))
                    traceback.print_exc()
        return self.draw_frame()

    def snapshot(self, name, prefix="phase11_3_"):
        """把当前客户端画面存成 PNG（可视证据）。

        先自己画一帧：同一个进程里所有"客户端"共用一块显示面，不重画的话
        存下来的可能是**另一台客户端**最后一帧画的内容。
        """

        if self.screen is None:
            return ""
        self.draw_frame()
        folder = os.path.join(ROOT, "tools", "ui_snapshots")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, prefix + name + ".png")
        pygame.image.save(self.screen, path)
        return path

    # ---- 只读访问 ----

    @property
    def match(self):
        return self.session.match

    @property
    def view(self):
        match = self.match
        return match.view if match is not None else None

    @property
    def decision(self):
        match = self.match
        return match.decision if match is not None else None

    def hand_ids(self):
        view = self.view
        return [card.card_id for card in view.hand] if view is not None else []

    def hand_names(self):
        view = self.view
        return [card.name for card in view.hand] if view is not None else []

    def player_view(self, player_id):
        view = self.view
        return view.player(player_id) if view is not None else None

    def answer_auto(self, prefer=None):
        match = self.match
        if match is None or not match.waiting_decision:
            return None
        request = match.decision
        self.last_decision = request
        result = self.policy(request, prefer or {})
        if result is not None:
            match.answer(result)
            self.answered.append((request.get("kind"), result.action))
        return result

    # ---- 原始载荷审计 ----

    def payload_text(self, kinds=None):
        """收到的全部消息的 JSON 文本（可按消息类型过滤）。"""

        items = []
        for message in self.raw_messages:
            if kinds and message.get("type") not in kinds:
                continue
            items.append(json.dumps(message, ensure_ascii=False))
        return "\n".join(items)

    def leaked_card_ids(self, card_ids, kinds=None):
        """这些牌 id 有没有出现在我的网络载荷里（隐藏信息审计）。"""

        text = self.payload_text(kinds)
        return [item for item in card_ids if item and item in text]

    def audit_hidden_cards(self, owner_id, hidden):
        """结构化审计：属于 ``owner_id`` 的那些牌，内容有没有被我收到。

        ``hidden`` 是 ``[{"card_id": …, "label": …}]``。判定规则：

        1. 载荷任何位置都不许出现这些牌的 ``card_id``；
        2. 形状像"玩家对象"（有 seat / nickname）且 ``player_id`` 是 owner 的
           条目，``hand`` 必须是空、``hand_count`` 必须给出；
        3. 表现事件里 ``player_id`` 是 owner 的那些，不许带非空的 ``cards``
           （除了"这个人自己就是观众"的情况——那种情况下本来就该看到）。
        """

        hidden_ids = {str(item.get("card_id")) for item in hidden}
        problems = []
        mine = self.view.local_player_id if self.view is not None else ""
        viewer_is_owner = str(owner_id) == str(mine)

        def walk(node, path):
            if isinstance(node, dict):
                card_id = node.get("card_id")
                if card_id and str(card_id) in hidden_ids:
                    problems.append("载荷里出现隐藏牌的 card_id：" + path)
                if str(node.get("player_id")) == str(owner_id) and not viewer_is_owner:
                    if "seat" in node or "nickname" in node:
                        if node.get("hand"):
                            problems.append("玩家对象里带了 hand 内容：" + path)
                        if node.get("hand_count") is None:
                            problems.append("玩家对象缺 hand_count：" + path)
                    if node.get("kind") and node.get("cards"):
                        problems.append("表现事件里夹带了牌面：" + path)
                for key, value in node.items():
                    walk(value, path + "/" + str(key))
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, path + "/" + str(index))

        for message in self.raw_messages:
            walk(message, message.get("type", "?"))
        return problems

    def close(self):
        self.session.leave()


# ==================================================
# 推进
# ==================================================

def make_pump(host, clients):
    def pump(seconds=1.0, *, auto=False, draw=True):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            host.session.poll()
            host.game.update(FRAME)
            for client in clients:
                client.session.poll()
                if draw:
                    client.update_frame(FRAME)
                if auto:
                    client.answer_auto()
            time.sleep(FRAME / 3)

    return pump


def wait_for(pump, predicate, timeout=8.0, what=""):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        pump(0.05)
    return bool(predicate())


def _settle_pending(host, request):
    """替本机真人回答一条还在等的请求（共享无懈阶段只放弃自己那一份）。"""

    from src.game.engine import PassPendingAction

    game = host.game
    if getattr(request, "is_group", False):
        if request.member_status(game.player) == "pending":
            game.submit_action(PassPendingAction(game.player, request.request_id))
        return True
    if request.target is game.player:
        game.submit_action(PassPendingAction(game.player, request.request_id))
        return True
    return False


def settle(host, pump, seconds=4.0):
    """把房主桌面推回空闲：替他放弃需要本地回应的窗口。"""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        game = host.game
        if game.response.active:
            game.pass_response()
        elif game.engine.pending.active:
            _settle_pending(host, game.engine.pending.current)
        elif game.choice.active:
            game.choice.choose_no()
        elif not game.busy and not game.pending_target_selection:
            return True
        pump(0.1)
    return not host.game.busy


def clear_windows(host, clients, pump, seconds=4.0):
    """把整桌推回空闲：房主放弃本地窗口，客户端也放弃它们的窗口。

    场景之间必须清干净，否则上一场景的响应窗口会一直挂着——下一场景的
    "出牌阶段"请求根本建不出来（引擎正等着一份永远不会来的响应）。
    """

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        game = host.game
        if game.response.active:
            game.pass_response()
        elif game.engine.pending.active:
            request = game.engine.pending.current
            _settle_pending(host, request)
            if getattr(request, "is_group", False):
                # 共享响应阶段：每台客户端放弃**自己那一份**（同轮的其他人
                # 由引擎驱动，谁都不能把整局卡在等待里）。
                for client in clients:
                    _pass_group_decision(client, request)
            else:
                target = getattr(request, "target", None)
                for client in clients:
                    if not client.match or not client.match.waiting_decision:
                        continue
                    if client.view.local_player_id != getattr(target, "player_id", None):
                        continue
                    client.match.answer(DecisionResult(action=ACTION_PASS))
        elif game.choice.active:
            game.choice.choose_no()
        elif game.pending_target_selection is not None:
            game.cancel_target_selection()
        elif not game.busy:
            return True
        pump(0.1)
    return not host.game.busy


def _pass_group_decision(client, request):
    """这台客户端手里那份"属于本窗口本轮的"决策，放弃掉。"""

    match = client.match
    if match is None or not match.waiting_decision:
        return False
    decision = match.decision or {}
    context = decision.get("context") or {}
    if int(context.get("round_id") or 0) != int(request.context.get("round_id") or 0):
        return False
    if int(context.get("window_id") or 0) != int(request.context.get("window_id") or 0):
        return False
    if str(decision.get("player_id") or "") != str(client.view.local_player_id or ""):
        return False
    match.answer(DecisionResult(action=ACTION_PASS))
    return True


def start_match(host, clients, pump=None):
    """大厅 → 准备 → 房主开始对局 → **走完开局流程**（看身份 → 选将）。"""

    pump = pump or make_pump(host, clients)
    pump(1.0)
    for client in clients:
        client.session.set_ready(True)
    if not wait_for(pump, lambda: host.session.lobby.can_start(), 8.0, "全员准备"):
        return pump, False, "大厅没有进入可开始状态"
    ok, message = host.session.start_match()
    complete_setup(host, clients, pump)
    wait_for(pump, lambda: all(c.match is not None and c.match.ready for c in clients),
             10.0, "客户端开局视图")
    return pump, ok, message


def _session_of(holder):
    """``HostSide`` / ``ClientSide`` 包着会话，测试直接传 ``LanSession``。"""

    return getattr(holder, "session", holder)


def complete_setup(host, clients, pump, timeout=12.0):
    """替所有真人点完联机的开局流程：看身份 → 选将（都取第一个候选）。

    真实流程一步不少（身份要确认、武将要选），只是由脚本代点——工具与测试
    需要的是"进入牌桌"，而不是验证这两屏的手感。
    """

    if pump is None:
        pump = make_pump(host, clients)
    host_session = _session_of(host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        match = host_session.match
        if match is None:
            pump(0.1)
            continue
        stage = str(getattr(match, "setup_stage", ""))
        if stage == "battle":
            pump(0.3)
            return True
        if stage in ("identity", "identity_done"):
            match.host_identity_confirmed()
            for client in clients:
                client_match = _session_of(client).match
                if client_match is not None and client_match.my_identity:
                    client_match.confirm_identity()
        elif stage in ("choosing", "picking"):
            host_id = match.host_player_id
            if host_id not in match.general_picks and match.game.general_candidates:
                match.host_general_picked(match.game.general_candidates[0])
            for client in clients:
                client_match = _session_of(client).match
                if client_match is None or not client_match.candidates:
                    continue
                if not client_match.picked_general:
                    client_match.pick_general(
                        client_match.candidates[0]["general_id"])
        pump(0.1)
    return str(getattr(host_session.match, "setup_stage", "")) == "battle"


# ==================================================
# 确定性局面脚手架
# ==================================================

def take_card(game, name, *, suit=None, rank=None, exclude=()):
    """把一张指定牌名的牌从**任何位置**拿到手（不改动任何规则状态）。

    牌堆是随机洗过的，开局发牌可能正好把要用的牌发到别人手里，所以这里
    依次找：牌堆 → 弃牌堆 → 所有角色的手牌 / 装备区 / 判定区。
    """

    def matches(card):
        if card is None or card.name != name:
            return False
        if suit is not None and card.suit != suit:
            return False
        if rank is not None and str(card.rank) != str(rank):
            return False
        return card not in exclude

    for card in game.deck.draw_pile:
        if matches(card):
            game.deck.draw_pile.remove(card)
            return card
    for card in game.deck.discard_pile:
        if matches(card):
            game.deck.discard_pile.remove(card)
            return card
    for player in game.players:
        for card in player.hand:
            if matches(card):
                player.hand.remove(card)
                return card
        for slot, card in list((player.equipment or {}).items()):
            if matches(card):
                player.remove_equipment(slot)
                return card
        for card in player.judgement_zone:
            if matches(card):
                player.judgement_zone.remove(card)
                return card
    raise AssertionError("场上找不到可用的 " + str(name))


def force_hand(game, player, names):
    """把某人的手牌换成指定牌名组成的确定性手牌。"""

    from src.deck import Deck

    for card in list(player.hand):
        game.deck.discard_pile.append(card)
    player.hand = [take_card(game, name) for name in names]
    player.clear_turn_state()
    return player.hand


def put_in_judge_area(game, player, name):
    """往某人判定区放一张延时锦囊。"""

    card = take_card(game, name)
    player.judgement_zone.append(card)
    return card


def give_general(game, player, general_id):
    """指定武将（技能由 SkillManager 按绑定表重新绑定）。"""

    player.general_id = general_id
    general = game.generals.get(general_id)
    if general is not None:
        player.max_hp = general.max_hp
        player.hp = min(player.hp or general.max_hp, general.max_hp)
    game.skills.bind_general(player)
    return player


def start_remote_turn(host, pump, *, phase="play"):
    """让某个远程玩家成为当前回合玩家并发起出牌阶段请求。

    测试脚手架：把回合状态清干净（出杀次数、酒、各种窗口），否则上一场景的
    限制会一路带过来，看起来像"客户端拿不到可出的牌"。
    """

    remote = host.remote_players()[0]
    for player in host.game.players:
        player.clear_turn_state()
        player.sha_used = False
    host.game.skipped_phases = set()
    host.game.phase = phase
    host.game.current_turn_player = remote
    host.game.message = remote.name + " 的出牌阶段"
    controller = host.game.get_controller(remote)
    controller._turn = None
    controller._idle_retries = 0
    controller.take_turn(lambda: None)
    pump(0.4)
    return remote


def close_all(host, clients):
    for client in clients:
        client.close()
    host.close()


class MatchSession:
    """一次完整的"房主 + N 个客户端"对局（用完自动关掉）。

    每个验证场景都开一局新的：场景之间不会互相污染（上一场景的响应窗口、
    出杀次数、手牌限制都不会带进下一场景）。
    """

    def __init__(self, client_count=2, *, nickname="房主", mode=None, names=None):
        names = names or ["玩家%s" % chr(ord("甲") + index) for index in range(client_count)]
        # 模式随大厅一起交给房主开局（不再在开局前偷偷改 game.mode_id）。
        self.host = HostSide(nickname=nickname, game_mode=mode or "ffa")
        self.clients = [ClientSide(name, self.host.port, render=True) for name in names]
        self.pump, ok, message = start_match(self.host, self.clients)
        self.ok = ok
        self.message = message
        # 开局的发牌 / 摸牌动画要播完，桌面才可能接受出牌：动画期间真人点不动
        # 牌，场景脚本也一样（``player_use_card`` 在 busy 时直接返回 None）。
        # 这里等"真正空闲"，而不是固定睡 0.4 秒——开局动画约 0.66 秒，睡不够
        # 会让第一个场景偶发失败（重跑即过的老问题）。
        wait_for(self.pump, lambda: not self.host.game.busy, 4.0, "开局桌面空闲")
        self.pump(0.2)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        try:
            self.close()
        except Exception:                              # pragma: no cover
            pass
        return False

    @property
    def game(self):
        return self.host.game

    def remote(self, index=0):
        return self.host.remote_players()[index]

    def client_of(self, player):
        for client in self.clients:
            if client.view is not None and client.view.local_player_id == player.player_id:
                return client
        return self.clients[0]

    def close(self):
        close_all(self.host, self.clients)

    def fix_hands(self, hands, *, keep=None):
        """把所有人（除 ``keep``）的手牌换成确定性手牌。

        锦囊会开【无懈可击】窗口，而窗口按座次挨个问：只要某个角色手里恰好
        有【无懈可击】，整条链就会停下来等一个不会来的回答。验证工具必须把
        这种随机性钉死，否则场景会偶发地"等不到下一步"。
        """

        game = self.host.game
        for player in game.players:
            if keep is not None and player.player_id == keep.player_id:
                continue
            force_hand(game, player, list(hands))
        return self


def find_card(request, names=(), *, skill_id="", require_targets=False):
    for card in request.get("cards", ()):
        if names and card.get("name") not in names:
            continue
        options = card.get("options") or ()
        if skill_id:
            match = next((item for item in options
                          if item.get("skill_id") == skill_id
                          and item.get("enabled")), None)
            if match is None:
                continue
            if require_targets and not match.get("targets"):
                continue
            return card, match
        if require_targets and not card.get("targets"):
            continue
        return card, (options[0] if options else None)
    return None, None


def option_targets(card, option):
    """这张牌这一次方式的合法目标 id。"""

    if option is not None and option.get("targets"):
        return [item["player_id"] for item in option["targets"]]
    return [item["player_id"] for item in (card or {}).get("targets", ())]
