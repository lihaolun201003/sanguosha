"""联网对局桥：把大厅连接与**房主的权威对局**接起来（Phase 11.2 + 11.3）。

这是唯一同时认识两侧的模块：

* 房主侧 ``HostMatch`` 持有权威 ``Game``，负责
  1. 把引擎的决策请求翻译成 ``DECISION_REQUEST`` 发给对应客户端；
  2. 为**每一名**客户端单独生成只读视图 ``GAME_VIEW_SNAPSHOT``；
  3. 把引擎事件过滤成每名客户端有权看到的 ``GAME_EVENT`` 表现事件；
  4. 维护单调递增的 ``revision`` 与 ``STATE_RESYNC_REQUEST`` 重同步。
* 客户端侧 ``ClientMatch`` 只保存一份**只读**视图模型（``ClientGameView``）
  与一串待播放的表现事件；它没有任何规则、没有牌堆、没有流程。

两条不可违反的规则：

1. **同一帧的权威 Game，对不同玩家生成不同的视图**。手牌内容只发给本人，
   未公开身份根本不进网络包——不是"发过去让 UI 不画"。
2. **客户端永远不是第二个规则引擎**。回答只携带"选了哪些 id"，由房主重新
   校验并提交真实的 GameAction。
"""

import time
import uuid

from src.game.controllers.remote import RemoteHumanController
from src.game.runtime_marker import print_battle_marker
from src.game.view.presentation import PresentationBridge
from src.game.view.view_builder import build_result_view, build_view
from src.game.view.view_model import ClientGameView
from src.player import ControllerType

from .decisions import (
    ERR_OPERATION_FAILED,
    ERR_WRONG_PLAYER,
    ERR_MALFORMED_PAYLOAD,
    DecisionError,
    DecisionRegistry,
    DecisionResult,
)
from .lobby import planned_battle_size
from .protocol import MessageType, message_payload, message_type

#: 两次状态广播之间的最小间隔（秒）。状态一直在变时（AI 连续行动）最多
#: 每秒十几次；决策前会强制刷新，所以不会让客户端拿旧视图做决定。
SYNC_INTERVAL = 0.08

#: 大厅模式非法（或没选）时本局使用的模式：标准身份局。
DEFAULT_MATCH_MODE = "identity"


class MatchAborted(Exception):
    """房主侧对局终止（掉线 / 决策应用失败）。"""


class HostMatch:
    """房主侧：权威对局 + 逐人视图 + 决策桥。"""

    #: 每帧最多推几次视图（保留旧常量，供 11.2 的调用点复用）。
    VIEW_PUSH_LIMIT = 30
    #: 同一条卡住的请求最多每隔这么久重新驱动一次（避免刷屏）。
    RECOVER_INTERVAL = 1.0
    #: 一条决策开了这么久还没被回答，就重发一次（客户端丢面板时的兜底）。
    RESEND_INTERVAL = 3.0

    def __init__(self, session, game, lobby):
        self.session = session
        self.game = game
        self.lobby = lobby
        self.match_id = uuid.uuid4().hex[:8].upper()
        self.registry = DecisionRegistry(self.match_id)
        self.controllers = {}
        self.started = False
        self.aborted = ""
        self.abort_reason = ""
        self.revision = 0
        self.host_player_id = ""
        self.bridge = None
        self._event_seq = 0
        self._fingerprint = None
        self._last_sync = 0.0
        self._result_sent = False
        self._started_at = time.monotonic()
        self._recover_target = None
        self._recover_at = 0.0
        #: request_id → 最近一次"重发这条决策"的时刻（卡住时的兜底）。
        self._resent = {}
        #: 结算之后想再来一局的客户端（房主仍然拥有最终决定权）。
        self.restart_votes = set()
        #: 开局流程的阶段：identity（等所有人看身份）→ choosing（等选将）
        #: → battle（正式开局）。``poll`` 的广播只在 battle 之后才动。
        self.setup_stage = "identity"
        #: 每个真人的候选武将：{player_id: (general_id, …)}
        self.human_candidates = {}
        #: 每个真人的选择：{player_id: general_id}
        self.general_picks = {}
        #: 房主自己的选择（流程还没到选将阶段时先记着，到了自动提交）。
        self.host_pick = ""
        self.pending_identity = set()
        self.pending_pick = set()
        #: 供报告 / 测试检查：每名客户端收到过多少次视图与事件。
        self.stats = {}

    # ==================================================
    # 开局
    # ==================================================

    def start(self):
        """按大厅名单建立权威对局，并进入**与单机一致的开局流程**。

        整个流程分两段，中间夹着"看身份 → 选将"：

        1. ``begin_networked_setup``：建座位、发身份（主公公开），不发牌、不开局；
        2. 每个真人先看自己的身份（房主本地看，远程玩家收 ``IDENTITY_ASSIGN``），
           然后各自从候选武将里选一个（收 ``GENERAL_CANDIDATES`` / 回 ``GENERAL_PICKED``）；
        3. 所有真人都选完 → ``finish_networked_setup``：发牌 → 开局规则 → 首行动，
           再把开局视图发给每个远程玩家。

        在这之前联机是"看不见身份、系统代选武将"的，而单机一直是"先看身份、
        再选将"——同一局游戏不该有两种开局。
        """

        self.game.set_mode(self.match_mode())
        seats = self.seat_specs()
        self.game.remote_controller_factory = self.make_controller
        self.game.match_id = self.match_id
        # 武将分配用房间号做种子：同一间房每次开局得到同一份分配，报告与
        # 测试可以复现，而不是每局不可预测地换一套武将。
        self._seed_generals()
        self.game.begin_networked_setup(seats, general_pool=self.general_pool())
        self.started = True
        self.host_player_id = str(getattr(self.game.player, "player_id", "") or "")
        self.bridge = PresentationBridge(self.game, self.match_id).attach()
        self.revision = 1
        self._fingerprint = self.state_fingerprint()
        self._begin_setup_flow()
        return True, ""

    # ==================================================
    # 开局流程：身份 → 选将
    # ==================================================

    def _begin_setup_flow(self):
        """把"你的身份"发给每个远程真人，并把候选武将准备好。

        房主自己的身份屏由 UI 场景（``identity_reveal``）显示，他点「继续」后
        调用 ``host_identity_confirmed()``；远程玩家点「继续」后回
        ``IDENTITY_READY``。两边都确认了才进入选将。
        """

        self.setup_stage = "identity"
        self.human_candidates = self._roll_candidates()
        self.general_picks = {}
        self.pending_identity = set()
        self.pending_pick = set()
        # 房主自己的候选与单机同一条路径（选将界面读 general_candidates）。
        self.game.general_candidates = tuple(
            self.human_candidates.get(self.host_player_id, ()))
        if not getattr(self.game.mode, "uses_identities", False):
            # 自由混战没有身份可看、也没有武将池要选：直接正式开局，
            # 与联机的既有行为一致。
            self.setup_stage = "battle"
            self._enter_battle()
            return
        for player_id in self.remote_player_ids():
            self.pending_identity.add(player_id)
            self.pending_pick.add(player_id)
            self.send_identity(player_id)
        print_battle_marker(
            self.game, tag="IDENTITY SETUP",
            origin="src/network/match.py:HostMatch.start() → Game.begin_networked_setup()")

    def _roll_candidates(self):
        """给每个真人一份**互斥**的候选：同一局不会出现两个相同的选择。"""

        pool = list(self.game.general_pool_ids())
        self.game.rng.shuffle(pool)
        size = max(1, int(getattr(self.game.mode, "general_choice_count", 3) or 3))
        result = {}
        for index, player_id in enumerate(self.human_player_ids()):
            start = index * size
            chunk = tuple(pool[start:start + size])
            if len(chunk) < size:                       # 池子不够就从头复用
                chunk = tuple(pool[:size])
            result[player_id] = chunk
        return result

    def human_player_ids(self):
        """本局的真人座位（房主 + 远程真人），按座次排序。"""

        return [
            player.player_id for player in sorted(
                self.game.players, key=lambda item: item.seat)
            if player.controller_type is not ControllerType.AI
        ]

    def remote_player_ids(self):
        return [
            player.player_id for player in sorted(
                self.game.players, key=lambda item: item.seat)
            if player.controller_type is ControllerType.REMOTE_HUMAN
        ]

    def _player_of(self, player_id):
        for player in self.game.players:
            if player.player_id == player_id:
                return player
        return None

    # ---- 身份 ----

    def send_identity(self, player_id):
        """只发**这名玩家自己**的身份：别人的身份一个字节都不上网。"""

        player = self._player_of(player_id)
        if player is None:
            return False
        from src.game.identity import identity_name

        identity = getattr(player, "identity", None)
        lord = self.game.mode.lord() if hasattr(self.game.mode, "lord") else None
        return self.session.send_to_player(
            player_id, MessageType.IDENTITY_ASSIGN,
            match_id=self.match_id,
            viewer_id=player_id,
            identity=getattr(identity, "value", "") or "",
            identity_name=identity_name(identity),
            lord_name=(lord.name if lord is not None else ""),
        )

    def host_identity_confirmed(self):
        """房主看完了自己的身份（本地身份屏点了「继续」）。"""

        if self.setup_stage != "identity":
            return False
        self.setup_stage = "identity_done"
        return self._maybe_send_candidates()

    def identity_confirmed(self, player_id):
        """远程玩家确认看过身份了。"""

        self.pending_identity.discard(str(player_id))
        return self._maybe_send_candidates()

    def _maybe_send_candidates(self):
        if self.setup_stage == "identity" or self.pending_identity:
            return False
        self.setup_stage = "choosing"
        for player_id in self.remote_player_ids():
            self.send_candidates(player_id)
        # 房主要是已经选好了（他在等别人确认身份时就点了「确认出战」），
        # 现在补交上去——否则那个选择会被一直搁着。
        self._submit_host_pick()
        return True

    # ---- 选将 ----

    def send_candidates(self, player_id):
        """把候选武将发给这名玩家（武将信息本来就是公开数据）。"""

        general_ids = tuple(self.human_candidates.get(player_id, ()))
        entries = []
        for general_id in general_ids:
            general = self.game.generals.get(general_id)
            if general is None:
                continue
            entries.append({
                "general_id": general.id,
                "name": general.name,
                "kingdom": general.kingdom_name,
                "max_hp": int(general.max_hp),
                "skill_names": [
                    self._skill_name(skill_id) for skill_id in general.skill_ids],
            })
        return self.session.send_to_player(
            player_id, MessageType.GENERAL_CANDIDATES,
            match_id=self.match_id, viewer_id=player_id, candidates=entries)

    def _skill_name(self, skill_id):
        definition = self.game.skill_registry.get(skill_id)
        return str(getattr(definition, "name", "") or skill_id)

    def host_general_picked(self, general_id):
        """房主选好了自己的武将（本地选将屏点了「确认出战」）。

        流程可能还停在"等别人确认身份"那一步：这时**先把选择记下来**，等进入
        选将阶段自动提交。否则房主点了按钮毫无反应，看起来就像整局卡住了。
        """

        general_id = str(general_id or "")
        if not general_id:
            return False
        self.host_pick = general_id
        return self._submit_host_pick()

    def _submit_host_pick(self):
        if not self.host_pick:
            return False
        if self.setup_stage not in ("choosing", "picking"):
            return False
        if self.host_player_id in self.general_picks:
            return False
        self.general_picks[self.host_player_id] = self.host_pick
        return self._maybe_begin_battle()

    def general_picked(self, player_id, general_id):
        """远程玩家选好了武将：必须是房主发给他的候选之一。"""

        player_id = str(player_id)
        general_id = str(general_id or "")
        if self.setup_stage not in ("choosing", "picking"):
            return False
        allowed = tuple(self.human_candidates.get(player_id, ()))
        if not general_id or general_id not in allowed:
            self.session.note("已忽略一条不在候选里的选将")
            return False
        self.general_picks[player_id] = general_id
        self.pending_pick.discard(player_id)
        return self._maybe_begin_battle()

    # ---- 等待状态（UI 用它显示"还差谁"）----

    def waiting_for(self):
        """还在等哪些真人（确认身份 / 选将），返回 [(昵称, 在等什么)]。"""

        from .protocol import MessageType                        # noqa: F401

        waiting = []
        for player_id in sorted(self.pending_identity):
            waiting.append((self._display_name(player_id), "确认身份"))
        if not self.pending_identity:
            for player_id in sorted(self.pending_pick):
                if player_id in self.general_picks:
                    continue
                waiting.append((self._display_name(player_id), "选武将"))
        return waiting

    def _display_name(self, player_id):
        player = self._player_of(player_id)
        if player is not None:
            return str(player.name)
        member = self.lobby.player(player_id) if self.lobby is not None else None
        return str(getattr(member, "nickname", "") or player_id)

    def wait_message(self):
        """一行中文等待提示（自己这一步做完了、还在等别人时显示）。"""

        waiting = self.waiting_for()
        if not waiting:
            return ""
        parts = ["%s（%s）" % (name, what) for name, what in waiting]
        return "等待 " + "、".join(parts) + " …"

    def _maybe_begin_battle(self):
        """每个真人都选完了 → 正式开局（发牌 / 开局规则 / 首行动）。"""

        if self.setup_stage not in ("choosing", "picking"):
            return False
        if len(self.general_picks) < len(self.human_player_ids()):
            present = [pid for pid in self.human_player_ids()
                       if pid in self.general_picks]
            self.session.note("等待其他玩家选将（%d/%d）" % (
                len(present), len(self.human_player_ids())))
            return False
        self.setup_stage = "battle"
        self._enter_battle()
        return True

    def _enter_battle(self):
        """真正开局：发牌 → 开局规则 → 首行动，并把开局视图发给远程玩家。"""

        self.game.finish_networked_setup(general_picks=self.general_picks)
        # 开局后的第一份视图 + 状态指纹（此时才第一次有牌可看）。
        self.revision += 1
        self._fingerprint = self.state_fingerprint()
        for player_id in self.remote_player_ids():
            self.send_setup(player_id)
        print_battle_marker(
            self.game, tag="IDENTITY RUNTIME",
            origin="src/network/match.py:HostMatch._enter_battle() → Game.finish_networked_setup()")
        return True

    # ==================================================
    # 座位表：真人 + AI 补位
    # ==================================================

    def match_mode(self):
        """本局真正使用的模式 id：大厅里选的模式，非法时退回标准身份。"""

        mode_id = str(getattr(self.lobby, "game_mode", "") or "").strip()
        if mode_id and mode_id in self.game.modes:
            return mode_id
        return DEFAULT_MATCH_MODE

    def battle_size(self, humans=None):
        """本局总人数：模式允许人数中 ≧ 真人数的**最小值**。

        身份局只有 5～8 人（5 人 = 主公/忠臣/反贼/反贼/内奸），所以 2 名真人
        进入房间时补 3 个 AI，而不是退化成 2 人无身份对局；5 名真人则一人一座，
        不补 AI。自由混战沿用"有几名真人就是几人"的旧行为。
        """

        humans = self.human_seats() if humans is None else list(humans)
        return planned_battle_size(self.game.mode, len(humans))

    def human_seats(self):
        """大厅里的真人（房主 + 其他玩家），按座位号排序。"""

        return list(self.lobby.ordered())

    def ai_count(self):
        """本局要补几个 AI（大厅界面也用它显示"AI 补位 N"）。"""

        return max(0, self.battle_size() - len(self.human_seats()))

    def seat_specs(self):
        """``[(player_id, 名字, seat, controller_type, connection_id)]``。

        真人用自己的 player_id（连接身份就是它）；补位 AI 用 ``AI-<座位号>``
        这类稳定 id，房间重开也不会与真人 id 撞车。
        """

        seats = []
        for member in self.human_seats():
            controller_type = (
                ControllerType.HUMAN if member.is_host else ControllerType.REMOTE_HUMAN)
            seats.append((
                member.player_id,
                member.nickname,
                member.seat,
                controller_type,
                member.player_id,      # connection_id：大厅玩家 id 就是连接身份
            ))

        size = self.battle_size()
        taken = {int(member.seat) for member in self.human_seats()}
        free = [seat for seat in range(size) if seat not in taken]
        for index, seat in enumerate(free[:self.ai_count()], start=1):
            player_id = "AI-%d" % seat
            seats.append((
                player_id,
                "AI %d" % index,
                seat,
                ControllerType.AI,
                None,
            ))
        return sorted(seats, key=lambda item: int(item[2]))

    def _seed_generals(self):
        seed = "lan-identity:" + str(
            getattr(self.lobby, "room_id", "") or self.match_id)
        try:
            self.game.rng.seed(seed)
        except (TypeError, ValueError):               # pragma: no cover - 兜底
            pass

    def general_pool(self):
        """本局用哪些武将：**身份局必须有武将**，其余模式沿用旧语义。

        身份、技能、技能栏、AI 的战斗力全部长在武将上，所以联机的身份局与
        单机一样必须分配武将——一局没有武将的身份局等于把规则砍掉一半
        （Phase 11.4.3 之前联机的真实状况）。

        自由混战联机没有选将界面，沿用"不指定武将"的既有语义：临时给它塞
        一套随机武将，会让触发类技能（例如【裸衣】的确认窗口）插进既有的
        联机交互链，属于本阶段之外的行为改变。
        """

        # 以**权威对局的模式**为准（真正在跑的那个，而不是大厅里的显示串）。
        mode = getattr(self.game, "mode", None)
        if mode is not None and getattr(mode, "uses_identities", False):
            # 身份局必须发武将；发给玩家与随机分配的池子一律走
            # **同一份可用性过滤**，否则联机侧会把未实现的武将发给真人。
            return self.game.playable_general_ids(for_random=True)
        return ()

    def make_controller(self, game, player):
        """注入给 Game 的控制器工厂：远程座位一律用 RemoteHumanController。"""

        controller = RemoteHumanController(game, player, bridge=self)
        self.controllers[player.player_id] = controller
        return controller

    # ==================================================
    # 视图
    # ==================================================

    def remote_ids(self):
        return [player.player_id for player in self.game.players
                if player.controller_type is ControllerType.REMOTE_HUMAN]

    def view_for(self, player_id):
        """为这名玩家生成当前时刻的只读视图（纯读，不改状态）。"""

        pending = self.registry.current_for(player_id)
        return build_view(
            self.game, player_id, self.revision,
            host_player_id=self.host_player_id,
            action=(self.bridge.action if self.bridge is not None else None),
            response=(self.bridge.response if self.bridge is not None else None),
            judge=(self.bridge.judge if self.bridge is not None else None),
            decision=(pending.request.to_payload() if pending is not None else None),
        )

    def send_setup(self, player_id):
        """开局：一条消息同时给出"我是谁"和第一份完整视图。"""

        player = self._player(player_id)
        if player is None:
            return False
        payload = self.view_for(player_id).to_payload()
        payload["seat"] = player.seat
        payload["nickname"] = player.name
        payload["my_player_id"] = player_id
        payload["snapshot"] = True
        self._count(player_id, "setup")
        return self.session.send_to_player(player_id, MessageType.GAME_SETUP, **payload)

    def send_view(self, player_id, view):
        """把一份逐人视图当快照发出去。"""

        payload = view.to_payload() if isinstance(view, ClientGameView) else dict(view)
        payload["snapshot"] = True
        self._count(player_id, "snapshot")
        return self.session.send_to_player(
            player_id, MessageType.GAME_VIEW_SNAPSHOT, **payload)

    def send_events(self, player_id, facts, revision):
        """把已经按可见性过滤好的表现事件发给一名客户端。"""

        payloads = []
        for fact in facts:
            data = fact.for_viewer(str(player_id))
            if data is None:
                continue
            data["kind"] = fact.kind
            data["revision"] = int(revision)
            data["event_id"] = int(fact.event_id)
            payloads.append(data)
        if not payloads:
            return 0
        self._count(player_id, "events", len(payloads))
        return self.session.send_to_player(
            player_id, MessageType.GAME_EVENT, events=payloads)

    def _count(self, player_id, key, amount=1):
        stats = self.stats.setdefault(
            str(player_id), {"setup": 0, "snapshot": 0, "events": 0})
        stats[key] = stats.get(key, 0) + int(amount)

    # ==================================================
    # 状态指纹与广播
    # ==================================================

    def state_fingerprint(self):
        """所有"看得见的状态"的紧凑指纹（只在房主本地用，不上网）。

        它包含每个人手牌的**具体身份**——因为顺手牵羊这类操作会改变某人的
        手牌内容而张数不变，只有内容也参与比较才能发现"该同步了"。
        这份指纹永远不会被发送，所以不构成信息泄露。
        """

        game = self.game
        result = getattr(game, "result", None)
        parts = [
            str(getattr(game, "phase", "")),
            str(getattr(game, "current_player_id", "") or ""),
            bool(getattr(game, "game_over", False)),
            str(getattr(game, "message", "") or ""),
            len(getattr(game.deck, "draw_pile", ()) or ()),
            len(getattr(game.deck, "discard_pile", ()) or ()),
            len(getattr(game, "processing_zone", ()) or ()),
            len(game.public_card_pool or ()),
            len(getattr(game, "game_log", ()) or ()),
            str(getattr(result, "reason", "") or ""),
            str(getattr(result, "winner_player_id", "") or ""),
        ]
        for player in game.players:
            parts.append((
                str(player.player_id), int(player.hp), int(player.max_hp),
                bool(player.alive), bool(getattr(player, "chained", False)),
                tuple(str(card.id) for card in player.hand or ()),
                tuple(
                    (slot, str(getattr(card, "id", "")))
                    for slot, card in sorted((player.equipment or {}).items())
                    if card is not None
                ),
                tuple(str(card.id) for card in player.judgement_zone or ()),
                bool(getattr(player, "identity_revealed", False)),
                str(getattr(player, "general_id", "") or ""),
            ))
        parts.append(tuple(
            str(getattr(card, "id", ""))
            for card, _slot in getattr(game, "table_cards", ()) or ()))
        parts.append(self._pending_fingerprint())
        parts.append(self._selection_fingerprint())
        parts.append(self._bridge_fingerprint())
        return tuple(parts)

    def _pending_fingerprint(self):
        pending = self.game.engine.pending.current
        request = getattr(self.game, "pending_request", None)
        return (
            str(getattr(pending, "request_id", "")),
            str(getattr(request, "prompt", "")),
            tuple(str(getattr(card, "id", ""))
                  for card in getattr(request, "allowed_cards", ()) or ()),
            tuple(sorted(str(item.request_id) for item in self.registry.open_requests())),
        )

    def _selection_fingerprint(self):
        selection = getattr(self.game, "pending_selection", None)
        if not selection:
            return None
        return (
            str(selection.get("zone")),
            int(selection.get("number") or 0),
            len(selection.get("selected") or ()),
            tuple(str(getattr(card, "id", ""))
                  for card, _key in selection.get("candidates", ())),
        )

    def _bridge_fingerprint(self):
        if self.bridge is None:
            return None
        judge = self.bridge.judge
        return (
            (str(judge.get("stage")), str(judge.get("reason")),
             str(getattr(judge.get("card"), "id", "")),
             len(judge.get("replacements") or ())) if judge else None,
            (str(getattr(self.bridge.action.get("card"), "id", ""))
             if self.bridge.action else ""),
            (str(getattr(self.bridge.response.get("card"), "id", ""))
             if self.bridge.response else ""),
        )

    @property
    def published_fingerprint(self):
        """最近一次**发布出去**的快照所对应的状态指纹（房主本地用，不上网）。

        它把 ``revision`` 与"当时的权威状态"绑在一起：``push_views`` 是节流的，
        两次发布之间房主的状态可以继续变而 ``revision`` 不动，所以"客户端已到
        rN"这句话只有在指纹也一致时才真的说明"客户端看到的就是现在这份状态"。
        校验工具用它建立同步检查点（见 tools/view_consistency.py）。
        """

        return self._fingerprint

    def push_views(self, force=False):
        """状态有变化就广播一次（比对指纹，绝不做每帧全量序列化）。"""

        if not self.started or self.aborted:
            return 0
        now = time.monotonic()
        if not force and now - self._last_sync < SYNC_INTERVAL:
            return 0
        fingerprint = self.state_fingerprint()
        if not force and fingerprint == self._fingerprint:
            return 0
        self._last_sync = now
        self._fingerprint = fingerprint
        self.revision += 1
        self.registry.note("NEXT SNAPSHOT", revision=self.revision)
        return self.broadcast()

    def broadcast(self):
        """把当前 revision 的逐人视图 + 本批表现事件发给每一名客户端。"""

        if self.aborted or self.bridge is None:
            return 0
        facts = self.bridge.take_events()
        for fact in facts:
            self._event_seq += 1
            fact.event_id = self._event_seq
        sent = 0
        for player_id in self.remote_ids():
            if self._player(player_id) is None:
                continue
            self.send_view(player_id, self.view_for(player_id))
            self.send_events(player_id, facts, self.revision)
            sent += 1
            if sent >= self.VIEW_PUSH_LIMIT:
                break
        self._maybe_send_result()
        return sent

    def _maybe_send_result(self):
        """对局结束：发一条 GAME_RESULT（客户端不自己判断谁赢）。"""

        if self._result_sent or not getattr(self.game, "game_over", False):
            return
        self._result_sent = True
        result = build_result_view(self.game)
        payload = result.to_payload() if result is not None else {}
        payload["match_id"] = self.match_id
        payload["revision"] = self.revision
        self.session.send_to_all(MessageType.GAME_RESULT, **payload)

    # ==================================================
    # 发送 / 接收
    # ==================================================

    def send_decision(self, player, request):
        """把一条决策发给客户端：**先**把权威视图刷到最新，再带上 base_revision。

        这样客户端拿到的请求一定对应它已经收到的视图，不会用"手上还没有的
        card_id"做选择（§29）。
        """

        self.push_views(force=True)
        return self.send_pending_decision(player.player_id, request)

    def send_pending_decision(self, player_id, request):
        """发送 / 重发一条决策（重发走同一条路径，语义完全一致）。"""

        payload = request.to_payload()
        payload["base_revision"] = int(self.revision)
        self.registry.note("SEND", request_id=request.request_id,
                           kind=request.kind, player_id=player_id)
        return self.session.send_to_player(
            player_id, MessageType.DECISION_REQUEST, request=payload)

    def send_decision_result(self, player_id, request_id, accepted, code="",
                             message=""):
        """告诉客户端这条回答是被接受还是被拒绝（"提交 ≠ 接受"）。

        接受 → 客户端才正式关掉这条决策；拒绝 → 客户端恢复面板并显示一行
        中文原因，玩家可以直接重来。**绝不允许只写日志不通知**：那样游客会
        停在"等待结算"，而房主还在等输入。

        ``reason`` 是给玩家看的一句话（按拒绝码规范化）；房主的具体说明放在
        ``detail`` 里，客户端只把它记进日志。
        """

        from .decisions import reject_text

        if accepted:
            return self.session.send_to_player(
                player_id, MessageType.DECISION_ACCEPTED,
                match_id=self.match_id, request_id=int(request_id))
        return self.session.send_to_player(
            player_id, MessageType.DECISION_REJECTED,
            match_id=self.match_id, request_id=int(request_id),
            code=str(code or ""),
            reason=reject_text(code, message),
            detail=str(message or ""))

    def handle_host_message(self, player_id, message):
        kind = message_type(message)
        if kind == MessageType.DECISION_RESPONSE:
            self._handle_response(player_id, message_payload(message))
        elif kind == MessageType.STATE_RESYNC_REQUEST:
            self.resync_player(player_id)
        elif kind == MessageType.RESTART_REQUEST:
            self.request_restart(player_id)
        elif kind == MessageType.IDENTITY_READY:
            self.identity_confirmed(player_id)
        elif kind == MessageType.GENERAL_PICKED:
            payload = message_payload(message)
            # 身份以**连接**为准：消息里自称的 player_id 必须与它一致。
            claimed = str(payload.get("player_id") or "")
            if claimed and claimed != str(player_id):
                self.session.note("已拒绝一条冒充他人的选将")
                return
            self.general_picked(player_id, payload.get("general_id"))
        # 其余类型（含客户端越权发来的东西）一律忽略：权威状态只由房主改。

    def request_restart(self, player_id):
        """客户端希望再来一局：记一票，真正的决定权在房主手里。

        客户端不会自己重启任何东西（它没有权威 Game）；房主看到这行提示之后
        点「重新开始」就会带着所有人回大厅重开一局。
        """

        if not getattr(self.game, "game_over", False):
            return False
        if self._player(player_id) is None:
            return False
        self.restart_votes.add(str(player_id))
        self.session.note("%s 希望重新开始" % self._display_name(player_id))
        return True

    def restart_waiting_for(self):
        """还差哪些真人表态（房主自己不用表态）。"""

        return [self._display_name(pid) for pid in self.remote_player_ids()
                if pid not in self.restart_votes]

    def return_to_lobby(self, reason="房主重新开局"):
        """房主决定重开：把所有人带回大厅（权威对局交回大厅流程重建）。

        与 ``abort`` 的区别：这不是错误路径，客户端不会被当成"对局终止"，
        而是收到 ``MATCH_RESTART`` 之后回大厅等下一局的 GAME_SETUP。
        """

        if self.aborted:
            return False
        self.registry.cancel_all(reason)
        for player_id in list(self.remote_player_ids()):
            self.session.send_to_player(
                player_id, MessageType.MATCH_RESTART,
                match_id=self.match_id, reason=reason)
        if self.bridge is not None:
            self.bridge.detach()
        self.started = False
        self.setup_stage = "identity"
        self.general_picks = {}
        self.host_pick = ""
        self.pending_identity = set()
        self.pending_pick = set()
        self.restart_votes = set()
        self._result_sent = False
        if self.lobby is not None:
            self.lobby.started = False
            self.session.send_to_all(
                MessageType.LOBBY_STATE, lobby=self.lobby.to_dict())
        self.session.note(reason)
        return True

    def resync_player(self, player_id):
        """重发这名玩家的完整视图（不踢人，也不改任何状态）。"""

        if not self.started or self.aborted or self._player(player_id) is None:
            return False
        self.send_view(player_id, self.view_for(player_id))
        pending = self.registry.current_for(player_id)
        if pending is not None:
            self.send_pending_decision(player_id, pending.request)
        self.session.note("已为一名客户端重新同步牌局视图")
        return True

    def resend_decision(self, player_id, pending):
        """只重发一条决策（客户端把它弄丢了 / 被拒绝之后要重来）。"""

        if pending is None or not pending.open or self.aborted:
            return False
        if self._player(player_id) is None:
            return False
        self.send_pending_decision(player_id, pending.request)
        return True

    def _handle_response(self, player_id, payload):
        """客户端的一条回答：协议校验 → 落到 Game 上 → 才真正关闭决策。

        两级成功（Phase 11.5 §7）：协议层通过**不等于**游戏层接受。
        ``controller.resolve()`` 可能返回 False（局面变了 / 牌不对），这时
        决策必须保持 OPEN 并明确 REJECT 给客户端；只有真的提交成功才 ACK。
        这样"房主仍在等输入，而客户端手里没有可操作的决策"这种死锁不会再出现。
        """

        request_id = payload.get("request_id")
        # 身份以**连接**为准：消息里自称的 player_id 必须与它一致，
        # 否则就是在冒充别人。
        claimed = payload.get("player_id")
        if claimed and str(claimed) != str(player_id):
            error = DecisionError(ERR_WRONG_PLAYER, "冒充其他玩家")
            self.registry.reject(player_id, request_id, error)
            self.send_decision_result(player_id, request_id or 0, False,
                                      error.code, error.message)
            self.session.note("已拒绝一条冒充他人的响应")
            return
        self.registry.note_answer(player_id, request_id)
        self.registry.note("RECEIVE", player_id=player_id, request_id=request_id)
        try:
            result = DecisionResult.from_payload(payload.get("result"))
            raw = payload.get("result")
            if raw is not None and not isinstance(raw, dict):
                raise DecisionError(ERR_MALFORMED_PAYLOAD, "回答不是一个对象")
            pending, result = self.registry.validate(
                player_id, request_id, payload.get("match_id"), result)
        except DecisionError as error:
            # 非法回答：记一条记录、明确拒绝客户端，**决策保持 OPEN**。
            self.registry.reject(player_id, request_id, error)
            self.send_decision_result(player_id, request_id or 0, False,
                                      error.code, error.message)
            self.session.note("已拒绝一条非法响应：" + error.code)
            return
        except (TypeError, ValueError):
            # 结构坏掉的响应（例如 request_id 不是数字）：拒绝，不改状态。
            error = DecisionError(ERR_MALFORMED_PAYLOAD, "响应结构不合法")
            self.registry.reject(player_id, request_id, error)
            self.send_decision_result(player_id, request_id or 0, False,
                                      error.code, error.message)
            return

        controller = self.controllers.get(player_id)
        if controller is None:
            self._finish_answer(player_id, pending, accepted=False,
                                code=ERR_OPERATION_FAILED,
                                message="这名玩家的控制器已经不在")
            return
        try:
            applied = controller.resolve(pending, result)
        except MatchAborted as error:
            self.abort(str(error))
            return
        except Exception as error:                  # pragma: no cover - 兜底
            # 房主自己的映射出了意外：不能把整局拖死在等待里。
            self.abort("决策应用失败：" + str(error))
            return

        if not applied:
            code = getattr(controller, "last_local_code", "") or ERR_OPERATION_FAILED
            message = getattr(controller, "last_local_error", "")
            self._finish_answer(player_id, pending, accepted=False,
                                code=code, message=message)
            return
        self._finish_answer(player_id, pending, accepted=True)

    def _finish_answer(self, player_id, pending, *, accepted, code="", message=""):
        """一条回答的收尾：ACK / REJECT + 生命周期日志 + 视图刷新。"""

        if accepted:
            self.registry.ack(pending)
            self.registry.note_accept(pending)
        else:
            self.registry.reject(
                player_id, pending.request_id,
                DecisionError(code or ERR_OPERATION_FAILED, message))
        self.send_decision_result(player_id, pending.request_id, accepted,
                                  code, message)
        if accepted:
            self.push_views(force=True)
        else:
            # 被拒绝：把当前面板连同最新视图再发一次，客户端一定能重来。
            self.push_views(force=True)
            self.resend_decision(player_id, pending)

    # ==================================================
    # 每帧
    # ==================================================

    def poll(self):
        """掉线检查 + 状态广播（主线程每帧调用）。"""

        if self.aborted or not self.started:
            return
        game_over = bool(getattr(self.game, "game_over", False))
        for player_id in list(self.controllers):
            member = self.lobby.player(player_id)
            if member is None:
                if game_over or self._result_sent:
                    # 对局已经结束：这时候离开房间只是"看完成绩走了"，
                    # 不该把别人的结算画面一起踢回大厅。
                    self._drop_controller(player_id)
                    continue
                self.abort("玩家已离开房间")
                return
        # 开局流程（看身份 / 选将）期间没有权威状态可广播：那一段由
        # IDENTITY_ASSIGN / GENERAL_CANDIDATES 两条消息驱动。
        if self.setup_stage != "battle":
            return
        self.recover_stalled_decisions()
        self.push_views()

    def _drop_controller(self, player_id):
        """把一名的玩家的控制器摘掉（对局结束后离开房间）。"""

        controller = self.controllers.pop(player_id, None)
        if controller is not None:
            controller.abort()
        return controller

    def recover_stalled_decisions(self):
        """兜底：引擎正等着某个远程玩家，而客户端手里没有对应请求。

        和引擎自己的 ``_drive_pending_front`` 一个思路：请求有可能在"另一条
        决策正开着"的瞬间被创建，那一次 present 就被跳过了。这里定期重新驱动
        一次——宁可多发一次决策请求，也不能让整局停在那里等一个永远不出现的
        面板。已经发出去（``controller.waiting``）的请求不会被打扰。

        第二种情况（Phase 11.5）：请求确实已经发过了（``waiting`` 为真），但
        客户端因为任何原因没有它（丢包之后的界面状态、被拒绝之后没恢复）。
        这里按"这条决策开了多久"做一次定期重发，保证**房主还在等输入时，
        远端一定有一条可操作的决策**。
        """

        pending = self.game.engine.pending.current
        if pending is None or getattr(pending, "status", "") != "pending":
            self._recover_target = None
            # 引擎空闲时还有一件事：轮到远程真人、他手里却没有出牌阶段面板
            # （面板被共享无懈阶段一类的窗口挤掉过）。这里定期补发一次，
            # 否则他的回合会停在"没有面板"，谁都推不动。
            self._reprompt_remote_turns()
            return
        controller = self.controllers.get(
            getattr(getattr(pending, "target", None), "player_id", ""))
        if controller is None:
            return
        now = time.monotonic()
        if not controller.waiting:
            if (self._recover_target == pending.request_id
                    and now - self._recover_at < self.RECOVER_INTERVAL):
                return
            self._recover_target = pending.request_id
            self._recover_at = now
            self.game.engine.present_or_auto_resolve(pending)
            return
        # 已经发过了：只在它"开得太久"且客户端还没有回答时重发一次。
        self._resend_if_stale(controller, now)

    def _reprompt_remote_turns(self):
        """补发轮次面板（每 ``RECOVER_INTERVAL`` 检查一次，不做每帧扫描）。"""

        now = time.monotonic()
        if now - self._recover_at < self.RECOVER_INTERVAL:
            return
        self._recover_at = now
        for player_id in self.remote_ids():
            controller = self.controllers.get(player_id)
            reprompt = getattr(controller, "maybe_reprompt_turn", None)
            if callable(reprompt):
                reprompt()

    def _resend_if_stale(self, controller, now):
        """决策开得太久、客户端又还没回答 → 定期重发一次（幂等）。"""

        player_id = controller.player.player_id
        decision = self.registry.current_for(player_id)
        if decision is None or not decision.open:
            return
        age = now - decision.created_at
        if age < self.RESEND_INTERVAL:
            return
        last = self._resent.get(decision.request_id, 0.0)
        if now - max(last, decision.created_at) < self.RESEND_INTERVAL:
            return
        self._resent[decision.request_id] = now
        if len(self._resent) > 64:
            for item in list(self._resent)[:-32]:
                self._resent.pop(item, None)
        self.session.note("已为一名客户端重发卡住的决策请求")
        self.resend_decision(player_id, decision)

    def abort(self, reason):
        """终止联网对局：作废等待中的决策，通知所有客户端。"""

        if self.aborted:
            return self.aborted
        self.aborted = reason
        self.abort_reason = reason
        self.registry.cancel_all(reason)
        # 引擎里可能还压着一条等待中的请求（共享无懈阶段 / 别人的响应窗口）：
        # 对局既然终止了，就把它和真人面板一起清掉，别让流程挂在半路。
        game = getattr(self, "game", None)
        if game is not None:
            game.engine.clear_pending_ui()
        # 先取快照：controller.abort() 会把控制器从注册表里摘掉（掉线清理），
        # 直接遍历字典会在「对局终止」这条最常见的路径上抛 RuntimeError。
        for controller in list(self.controllers.values()):
            controller.abort()
        if self.bridge is not None:
            self.bridge.detach()
        self.session.send_to_all(
            MessageType.GAME_ABORTED,
            match_id=self.match_id, reason=reason)
        # 回到大厅：把 started 放开，房间可以重新开始（掉线玩家已被移除）。
        if self.lobby is not None:
            self.lobby.started = False
            self.session.send_to_all(
                MessageType.LOBBY_STATE, lobby=self.lobby.to_dict())
        self.session.note("联网对局终止：" + reason)
        return reason

    # ==================================================
    # 工具
    # ==================================================

    def _player(self, player_id):
        for player in self.game.players:
            if player.player_id == player_id:
                return player
        return None


class ClientMatch:
    """客户端侧：一份只读视图 + 一串待播放的表现事件 + 一条待回答的决策。"""

    #: 待播放事件的上限（UI 每帧取走；积压说明这一帧卡了很久）。
    MAX_PENDING_EVENTS = 256

    def __init__(self, session):
        self.session = session
        self.match_id = ""
        self.ready = False            # 收到 GAME_SETUP
        self.aborted = ""
        self.finished = False         # 收到 GAME_RESULT
        self.nickname = ""
        #: 只读视图模型（房主是唯一权威；本地不推导任何规则）。
        self.view = ClientGameView()
        #: 原始载荷（与 view 同源）：给"按 dict 取值"的调用点用。
        self.players = []
        self.hand = []
        self.my_player_id = ""
        self.my_seat = 0
        self.turn_player_id = ""
        self.phase = ""
        self.message = ""
        self.revision = 0
        self.decision = None          # 待回答的 decision payload（dict）
        #: 已经提交、正在等房主确认（提交 ≠ 接受）。
        self.answered = False
        self.waiting_ack = False
        #: 房主拒绝的原因（机器码 + 中文 + 具体说明），供 UI 与日志取证。
        self.reject_code = ""
        self.reject_reason = ""
        self.reject_detail = ""
        #: 房主重新开局（MATCH_RESTART）之后置位，场景据此回大厅。
        self.restarted = False
        #: 我是否已经请求重新开始（房主拥有最终决定权）。
        self.restart_requested = False
        #: 正在等房主确认的那条 request_id。
        self._pending_answer_id = None
        self.last_answer = None
        self.notices = []
        #: 待播放的表现事件（每帧被 UI 取走；永远不会成为权威状态来源）。
        self.events = []
        self.last_event_id = 0
        self.dropped_events = 0
        #: revision 诊断：收到的 gap 次数 / 发出的重同步请求次数。
        self.gaps = 0
        self.resync_requests = 0
        self.result = None
        self.stats = {}
        self._held_decision = None
        self._resync_pending = False
        #: 开局流程：我自己的身份 / 我的候选武将 / 我已经选了谁。
        self.setup_started = False
        self.my_identity = ""
        self.my_identity_name = ""
        self.lord_name = ""
        self.candidates = []
        self.candidates_ready = False
        self.identity_seen = False
        self.picked_general = ""
        #: 选将结果回来时通知 UI（由场景注册）。
        self.on_identity = None
        self.on_candidates = None

    # ---- 查询 ----

    @property
    def my_turn(self):
        return bool(self.my_player_id) and self.turn_player_id == self.my_player_id

    def player(self, player_id):
        for item in self.players:
            if item.get("player_id") == player_id:
                return item
        return None

    def card(self, card_id):
        for item in self.hand:
            if item.get("card_id") == card_id:
                return item
        return None

    @property
    def waiting_decision(self):
        return self.decision is not None and not self.answered

    def _note(self, text):
        if text:
            self.notices.append(text)
            del self.notices[:-4]

    def _count(self, key, amount=1):
        self.stats[key] = self.stats.get(key, 0) + int(amount)

    # ---- 接收 ----

    def handle_client_message(self, message):
        kind = message_type(message)
        payload = message_payload(message)

        if kind == MessageType.GAME_SETUP:
            self.match_id = str(payload.get("match_id") or "")
            self.my_player_id = str(payload.get("my_player_id") or "")
            self.my_seat = int(payload.get("seat") or 0)
            self.nickname = str(payload.get("nickname") or "")
            self._apply_view(payload, initial=True)
            self.ready = True
            self._note("对局开始")
            return

        if kind == MessageType.IDENTITY_ASSIGN:
            self.match_id = str(payload.get("match_id") or "")
            # 身份分配早于牌局视图，客户端要在这里就知道"我是谁"。
            self.my_player_id = str(payload.get("viewer_id") or self.my_player_id)
            self.my_identity = str(payload.get("identity") or "")
            self.my_identity_name = str(payload.get("identity_name") or "")
            self.lord_name = str(payload.get("lord_name") or "")
            self.setup_started = True
            self._note("你的身份：" + (self.my_identity_name or "？"))
            if callable(self.on_identity):
                self.on_identity(self)
            return

        if kind == MessageType.GENERAL_CANDIDATES:
            self.my_player_id = str(payload.get("viewer_id") or self.my_player_id)
            self.candidates = [
                dict(item) for item in (payload.get("candidates") or ())
                if isinstance(item, dict)
            ]
            self.candidates_ready = True
            self._note("请选择你的武将")
            if callable(self.on_candidates):
                self.on_candidates(self)
            return

        if kind in (MessageType.GAME_VIEW, MessageType.GAME_VIEW_SNAPSHOT):
            if self.match_id and payload.get("match_id") not in (None, "", self.match_id):
                return                      # 上一局的迟到视图
            self._apply_view(payload)
            return

        if kind == MessageType.GAME_EVENT:
            self._apply_events(payload)
            return

        if kind == MessageType.DECISION_REQUEST:
            request = payload.get("request")
            request = request if isinstance(request, dict) else {}
            if self.match_id and request.get("match_id") not in (None, "", self.match_id):
                return
            self._offer_decision(request)
            return

        if kind == MessageType.DECISION_CANCELLED:
            request_id = payload.get("request_id")
            if self._held_decision is not None \
                    and self._held_decision.get("request_id") == request_id:
                self._held_decision = None
            if self.decision is not None and self.decision.get("request_id") == request_id:
                self.decision = None
                self.answered = False
                self.waiting_ack = False
            self._note("房主撤回了这次请求")
            return

        if kind == MessageType.DECISION_ACCEPTED:
            # 房主已经把这个答案落到游戏上了：现在才真正关掉这条决策。
            request_id = payload.get("request_id")
            if self._pending_answer_id == request_id:
                self._pending_answer_id = None
                self.waiting_ack = False
            if self.decision is None or self.decision.get("request_id") == request_id:
                self.decision = None
                self.answered = False
            self.reject_reason = ""
            self._count("accepted")
            self._note("已结算")
            return

        if kind == MessageType.DECISION_REJECTED:
            # 房主没接受：把面板还给玩家，并说明原因（绝不静默等待）。
            request_id = payload.get("request_id")
            if self._pending_answer_id == request_id:
                self._pending_answer_id = None
                self.waiting_ack = False
                self.answered = False
            if self.decision is None \
                    or self.decision.get("request_id") == request_id:
                # 这条请求还在手上（房主没有关掉它）：原样恢复，玩家重来。
                self.decision = self._rejected_request(request_id)
                self._held_decision = None
                self.answered = False
                self.waiting_ack = False
                self.last_answer = None
            self.reject_code = str(payload.get("code") or "")
            self.reject_reason = str(payload.get("reason") or "房主拒绝了这次操作")
            self.reject_detail = str(payload.get("detail") or "")
            self._count("rejected")
            self._note("已被拒绝：" + self.reject_reason)
            return

        if kind == MessageType.MATCH_RESTART:
            # 房主重新开局：这不是对局终止，客户端回大厅等下一局即可。
            self.restarted = True
            self.aborted = ""
            self.finished = False
            self.ready = False
            self.decision = None
            self._held_decision = None
            self.answered = False
            self.waiting_ack = False
            self._pending_answer_id = None
            self.result = None
            self._note(str(payload.get("reason") or "房主重新开局"))
            return

        if kind == MessageType.GAME_RESULT:
            self.result = dict(payload)
            self.finished = True
            self.decision = None
            self._held_decision = None
            self.waiting_ack = False
            self._note(str(payload.get("headline") or "对局结束"))
            return

        if kind == MessageType.GAME_ABORTED:
            self.aborted = str(payload.get("reason") or "联网对局已终止")
            self.decision = None
            self._held_decision = None
            self.answered = False
            self.waiting_ack = False
            self._note(self.aborted)
            return

    def _apply_view(self, payload, *, initial=False):
        revision = int(payload.get("revision") or 0)
        previous = self.revision
        if revision and self.revision and revision < self.revision:
            # 旧快照：忽略（迟到的消息不能把客户端拉回过去）。
            self._count("stale")
            return
        if revision and previous and not initial and revision > previous + 1:
            # revision 跳跃：说明中间有快照没收到。快照本身是全量的，先采用
            # 它保证状态正确，同时向房主请求一次重同步（§37：不踢人）。
            # 同一轮里只请求一次，避免"重发 → 又当成跳跃"的风暴。
            self.gaps += 1
            self._count("gap", revision - previous - 1)
            if not self._resync_pending:
                self._resync_pending = True
                self.request_resync()
        self._resync_pending = False
        self.revision = revision or self.revision
        self.view = ClientGameView.from_payload(payload)
        self.match_id = self.view.match_id or self.match_id
        self.my_player_id = self.view.local_player_id or self.my_player_id
        self.players = list(payload.get("players") or [])
        self.hand = list(payload.get("hand") or [])
        self.my_seat = int(payload.get("seat") or self.my_seat)
        self.nickname = str(payload.get("nickname") or self.nickname)
        self.turn_player_id = self.view.current_player_id
        self.phase = self.view.current_phase
        self.message = self.view.message
        # 快照里带着"房主认为我该做的决定"：本地还没有这条请求时补上，
        # 避免重同步之后面板消失（§29 的顺序保证）。
        if self.view.decision:
            self._offer_decision(self.view.decision, from_snapshot=True)
        self._flush_held_decision()

    def _apply_events(self, payload):
        events = payload.get("events")
        if not isinstance(events, list):
            return
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = int(event.get("event_id") or 0)
            if event_id and event_id <= self.last_event_id:
                # 重复 / 乱序的旧事件：同一条表现绝不播两次。
                self.dropped_events += 1
                continue
            if event_id:
                self.last_event_id = event_id
            self.events.append(event)
        if len(self.events) > self.MAX_PENDING_EVENTS:
            overflow = len(self.events) - self.MAX_PENDING_EVENTS
            del self.events[:overflow]
            self.dropped_events += overflow

    def take_events(self):
        """UI 每帧取走待播放事件。"""

        events = self.events
        self.events = []
        return events

    # ---- 决策 ----

    def _offer_decision(self, request, from_snapshot=False):
        if not request:
            return
        if self.decision is not None \
                and self.decision.get("request_id") == request.get("request_id"):
            return                              # 同一条请求：不重置已做的选择
        base = int(request.get("base_revision") or 0)
        if base and self.view.revision < base:
            # 视图还没到位：先把请求挂起，请求重同步，等视图到了再开放（§29）。
            self._held_decision = dict(request)
            if not from_snapshot:
                self.request_resync()
            return
        self._held_decision = None
        self.decision = dict(request)
        self.answered = False
        self.waiting_ack = False

    def _flush_held_decision(self):
        held = self._held_decision
        if held is None:
            return
        base = int(held.get("base_revision") or 0)
        if self.view.revision >= base:
            self._held_decision = None
            self.decision = dict(held)
            self.answered = False

    def request_resync(self):
        """请房主重发一份完整视图（不踢人、不重开对局）。"""

        self.resync_requests += 1
        return self.session.send_to_host(
            MessageType.STATE_RESYNC_REQUEST,
            match_id=self.match_id,
            player_id=self.my_player_id,
            revision=self.revision,
        )

    # ---- 开局流程 ----

    def confirm_identity(self):
        """「继续」：告诉房主我看过自己的身份了（幂等）。"""

        if self.identity_seen:
            return False
        sent = self.session.send_to_host(
            MessageType.IDENTITY_READY,
            match_id=self.match_id, player_id=self.my_player_id)
        if sent:
            self.identity_seen = True
        return sent

    def pick_general(self, general_id):
        """选将：把选择发回房主（幂等；只能是房主给过的候选之一）。"""

        general_id = str(general_id or "")
        if not general_id or self.picked_general:
            return False
        allowed = {item.get("general_id") for item in self.candidates}
        if general_id not in allowed:
            return False
        sent = self.session.send_to_host(
            MessageType.GENERAL_PICKED,
            match_id=self.match_id, player_id=self.my_player_id,
            general_id=general_id)
        if sent:
            self.picked_general = general_id
        return sent

    # ---- 回答 ----

    def answer(self, result):
        """把答案发回房主。

        注意：**提交不等于接受**。发出去之后客户端进入"等待房主确认"
        （``answered``），但 ``decision`` 不会被清掉——房主可能拒绝（局面变了、
        牌不合法），那时客户端要能原样恢复面板让玩家重来。只有收到
        ``DECISION_ACCEPTED``（或房主换了一条新请求）才真的关闭它。
        """

        if self.decision is None or self.answered:
            return False
        request_id = self.decision.get("request_id")
        if self._pending_answer_id == request_id:
            return False                    # 同一条决策已经发过一次了
        payload = {
            "match_id": self.match_id,
            "request_id": request_id,
            "player_id": self.my_player_id,
            "result": result.to_payload(),
        }
        sent = self.session.send_to_host(MessageType.DECISION_RESPONSE, **payload)
        if sent:
            self.answered = True
            self.waiting_ack = True
            #: 正在等房主确认的那条 request_id（房主可能拒绝，那时要恢复）。
            self._pending_answer_id = request_id
            self.last_answer = payload
            self.reject_reason = ""
            self.reject_code = ""
            self._count("answer")
        return sent

    def _rejected_request(self, request_id):
        """被拒绝之后重新拿到的可操作面板。

        正常情况下挂起的那条请求还在（房主没关它），直接还给玩家；万一它
        已经不在了（房主换了别的事情），就退回当前等待中的请求。
        """

        if self.decision is not None \
                and self.decision.get("request_id") == request_id:
            return self.decision
        if self._held_decision is not None \
                and self._held_decision.get("request_id") == request_id:
            return dict(self._held_decision)
        return self.decision

    def request_restart(self):
        """「重新开始」：请房主再来一局（客户端不自己重启任何东西）。"""

        self.restart_requested = True
        return self.session.send_to_host(
            MessageType.RESTART_REQUEST,
            match_id=self.match_id, player_id=self.my_player_id)
