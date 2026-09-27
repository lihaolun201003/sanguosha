"""统一演出契约与演出闸门（Phase 18）。

与 ``interaction`` 的分工（必须严格区分）：

    InteractionSchema   玩家**必须做决定** → 规则流程停在原地等回答
    PresentationSchema  玩家**只看**       → 规则照常结算，画面排队演

# 为什么需要演出闸门

在它之前，"规则已经跑到下一个阶段、判定还在屏幕上"只能靠表现层零散的压制
（动作队列 hold + 判定面板 + 视觉账本）挡一挡；这些压制各自知道一小块，
谁都不负责回答"这一整段演出演完了没有"。于是判定面板一关，玩家就能操作，
而判定的结论（"跳过出牌阶段"）还在屏幕上——看起来就是**一闪而过**。

现在只有一处判据：

    PresentationQueue 汇报"还有 blocking 演出在飞"
        → PresentationGate 打开
        → 动作队列不发新动作 / 本机界面让路 / 引擎不唤醒等在这条边界上的回合推进
        → 队列排空 → 闸门关上 → 引擎继续

# LAN 的选择：B（房主控制逻辑时间线，客户端本地按统一时间播放）

不采用"房主等所有客户端播完"（A），理由都是真实的架构事实：

1. 协议里**没有**客户端 → 房主的"演出播完"消息（``protocol.py`` 全表可查），
   加一条就等于引入一个新的全局同步点。
2. 一台卡顿的客人会**永久**卡住整局。需求明确要求不能这样。
3. 演出时长本来就不同步：``Game.speed`` 是座主本地设置、
   ``RemoteGameView.speed`` 是每台客户端自己的设置（``ui/speed.py`` 已声明
   两者互不通信），所以"统一时间线"在契约上就不存在。

因此：**闸门只作用于房主本机的时间线**，客人通过事件流自己按本机速度播放。
客人慢 → 它自己的画面落后，权威状态照常前进，重同步后靠视觉增量账本对齐；
客人卡死 → 只影响它自己，房主与其余客人不受影响。不可能互相锁死。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from .local_input import local_awaiting_input

# ==================================================
# 演出种类
# ==================================================

CARD_USED = "card_used"
CARD_RESPONSE = "card_response"
CARD_REVEALED = "card_revealed"
CARD_COUNT = "card_count_changed"
CARDS_MOVED = "cards_moved"
CARDS_DRAWN = "cards_drawn"
RESPONSE_REQUEST = "response_request"
TABLE_CARDS = "table_cards"
SKILL_ACTIVATED = "skill_activated"
JUDGE_STARTED = "judge_started"
JUDGE_CARD_REVEALED = "judge_card_revealed"
JUDGE_REPLACED = "judge_replaced"
JUDGE_RESULT = "judge_result"
PHASE_SKIPPED = "phase_skipped"
PHASE_STARTED = "phase_started"
TURN_STARTED = "turn_started"
DAMAGE = "damage"
RECOVER = "recover"
HP_LOST = "hp_lost"
DYING = "dying"
DEATH = "death"
CHAIN = "chain"
EQUIPMENT = "equipment"
PUBLIC_POOL = "public_pool"
IDENTITY_REVEAL = "identity_reveal"
GAME_RESULT = "game_result"
LOG = "log"

#: 默认算作"重要演出"的种类：它们出现时整段演出作为一个不可打断的块，
#: 规则推进要等它演完。普通的牌移动 / 摸牌 / 飘字不在此列——那些演出
#: 可以互相重叠，让它们阻塞规则只会把节奏拖垮。
BLOCKING_KINDS = frozenset({
    JUDGE_STARTED, JUDGE_CARD_REVEALED, JUDGE_REPLACED, JUDGE_RESULT,
    PHASE_SKIPPED, SKILL_ACTIVATED, DYING, DEATH, GAME_RESULT,
})

#: 引擎事件名 → 演出种类（本地表现层用）。
KIND_BY_ENGINE_EVENT = {
    "card.used": CARD_USED,
    "card.use_finished": CARD_USED,
    "card.revealed": CARD_REVEALED,
    "card.responded": CARD_RESPONSE,
    "skill.triggered": SKILL_ACTIVATED,
    "judge.started": JUDGE_STARTED,
    "judge.revealed": JUDGE_CARD_REVEALED,
    "judge.replaced": JUDGE_REPLACED,
    "judge.result": JUDGE_RESULT,
    "phase.skipped": PHASE_SKIPPED,
    "phase.start": PHASE_STARTED,
    "turn.start": TURN_STARTED,
    "damage.applied": DAMAGE,
    "dying.entered": DYING,
    "death": DEATH,
    "chain.changed": CHAIN,
    "equipment.lost": EQUIPMENT,
}

#: 网络表现事件名（``game.view.presentation`` 的 ``EV_*``）→ 演出种类。
#: 与本地那张表**同一套词汇**：新增一种演出时两张表一起补，
#: ``missing_fact_kinds()`` 会在启动自检里指出漏掉的那一边。
KIND_BY_FACT = {
    "card_used": CARD_USED,
    "card_response": CARD_RESPONSE,
    "card_revealed": CARD_REVEALED,
    "card_count_changed": CARD_COUNT,
    "cards_moved": CARDS_MOVED,
    "cards_drawn": CARDS_DRAWN,
    "response_request": RESPONSE_REQUEST,
    "table_cards": TABLE_CARDS,
    "skill": SKILL_ACTIVATED,
    "judge": JUDGE_RESULT,
    "phase_skipped": PHASE_SKIPPED,
    "phase": PHASE_STARTED,
    "turn_start": TURN_STARTED,
    "damage": DAMAGE,
    "recover": RECOVER,
    "hp_lost": HP_LOST,
    "dying": DYING,
    "death": DEATH,
    "chain": CHAIN,
    "equipment": EQUIPMENT,
    "public_pool": PUBLIC_POOL,
    "game_over": GAME_RESULT,
    "log": LOG,
}

#: 演出队列里的步骤名（``ui.storyboard.step_kinds()``）→ 演出种类。
KIND_BY_STEP = {
    "card": CARD_USED,
    "judge": JUDGE_STARTED,
    "skill": SKILL_ACTIVATED,
    "result": PHASE_SKIPPED,
    "damage": DAMAGE,
    "lose_hp": HP_LOST,
    "recover": RECOVER,
    "dying": DYING,
    "death": DEATH,
    "chain": CHAIN,
    "turn": TURN_STARTED,
}


def missing_fact_kinds(fact_kinds) -> Tuple[str, ...]:
    """网络侧有哪些 ``EV_*`` 还没进演出词汇表（启动自检 / 报告用）。"""

    return tuple(sorted(str(name) for name in fact_kinds
                        if str(name) not in KIND_BY_FACT))


@dataclass(frozen=True)
class PresentationSchema:
    """一条与视角无关的演出契约。

    ``blocking`` 是本轮的关键字段：只有为真时，规则推进才等它演完。
    ``duration`` 是**本机秒数**（已经按本机速度档缩放），供跨机对齐时参考；
    客户端不重算，直接用自己队列里的时长。
    """

    id: int = 0
    kind: str = ""
    actor_id: str = ""
    target_ids: Tuple[str, ...] = ()
    card: Optional[Any] = None
    skill: Dict[str, Any] = field(default_factory=dict)
    text: str = ""
    detail: str = ""
    tone: str = ""
    duration: float = 0.0
    blocking: bool = False
    payload: Dict[str, Any] = field(default_factory=dict)

    def describe(self) -> str:
        head = self.kind or "?"
        who = self.actor_id or "-"
        extra = (" → " + ",".join(self.target_ids)) if self.target_ids else ""
        return "%s:%s%s" % (head, who, extra)


def is_blocking(kind: str, *, explicit: Optional[bool] = None) -> bool:
    """这条演出要不要占据"规则等它演完"的位置。"""

    if explicit is not None:
        return bool(explicit)
    return str(kind or "") in BLOCKING_KINDS


# ==================================================
# 运行期状态（高层，不替代流程栈）
# ==================================================


class RuntimeState:
    """一局游戏的高层运行状态。

    刻意只有四个：三国杀的全部规则仍在 Flows 里，这里只回答"此刻这条
    时间线在干什么"，供表现层与外部观察者（报告 / 工具 / 测试）读取。
    ``ANIMATING`` 不单列——纯视觉动画按设计**不影响**权威逻辑，单独给它一个
    状态只会诱使别人拿它做规则判断（见 ``presentation`` 模块头的说明）。
    """

    RUNNING = "running"                      # 规则在推进
    WAITING_INTERACTION = "waiting_interaction"  # 有请求在等人回答
    PRESENTING = "presenting"                # 重要演出在飞，规则让路
    GAME_OVER = "game_over"


def runtime_state(game) -> str:
    """从权威状态推导高层运行状态（纯查询，不含任何副作用）。"""

    if getattr(game, "game_over", False):
        return RuntimeState.GAME_OVER
    gate = getattr(game, "presentation_gate", None)
    if gate is not None and gate.presenting:
        return RuntimeState.PRESENTING
    if getattr(game, "pending_request", None) is not None:
        return RuntimeState.WAITING_INTERACTION
    engine = getattr(game, "engine", None)
    if engine is not None and engine.pending.active:
        return RuntimeState.WAITING_INTERACTION
    return RuntimeState.RUNNING


# ==================================================
# 演出闸门
# ==================================================


class PresentationGate:
    """重要演出期间的规则让路闸门；通过 ``Game.presentation_gate`` 访问。

    只有**表现层**能打开它（只有表现层知道演出演到哪儿了），规则层只读。

    三条硬约束（每一条都对应一个真实会发生的故障）：

    1. **有界**：``MAX_HOLD`` 秒后强制放行。演出层出错 / 面板再也不会关闭 /
       没有 UI 的无头对局，都不能让整局永久停住。
    2. **不许压住正在被问的人**：本机玩家正在被要求做决定时让路失效。
       改判窗口就开在判定演出中间——压住它，判定永远拿不到结果（死锁）。
    3. **只影响推进，不改状态**：闸门从不修改任何规则数据，只回答"现在能不能
       开始下一件事"。
    """

    #: 单次让路的最长时间（秒，按本机速度缩放由调用方负责）。
    #: 判定面板自己的兜底上限是 6 秒左右，这里留一段余量。
    MAX_HOLD = 9.0

    def __init__(self, game):
        self.game = game
        #: 汇报者身份 → 正在演的重要演出（``PresentationSchema`` 列表）。
        #: 用集合而不是计数：重复汇报不会累加。
        self._presenting = {}
        #: 每条汇报的时间与已等待时长。
        self._since = {}
        #: 因为超时被强制放行的次数（报告 / 测试用）。
        self.releases = 0
        self.forced = 0

    # ---- 表现层汇报 ----

    def mark(self, source, active, *, blocking: bool = True, schemas=()):
        """汇报"我这里还有重要演出"；``source`` 是汇报者的稳定身份。

        ``schemas`` 是这段演出的契约描述（``PresentationSchema``）：闸门据它
        报告"现在挡路的是什么"，让日志 / 报告不必去猜是谁把规则拦住了。
        """

        if source is None:
            return
        if active and blocking:
            if source not in self._presenting:
                self._since[source] = 0.0
            self._presenting[source] = tuple(schemas or ())
        else:
            self._presenting.pop(source, None)
            self._since.pop(source, None)

    def clear(self):
        self._presenting.clear()
        self._since.clear()

    # ---- 推进 ----

    def tick(self, dt):
        """推进让路计时；超过 ``MAX_HOLD`` 就强制放行。"""

        if not self._presenting:
            return
        expired = []
        for source in list(self._presenting):
            self._since[source] = self._since.get(source, 0.0) + float(dt or 0.0)
            if self._since[source] >= self.MAX_HOLD:
                expired.append(source)
        for source in expired:
            self._presenting.pop(source, None)
            self._since.pop(source, None)
            self.forced += 1
        if expired:
            self.releases += 1

    # ---- 规则层 / UI 读取 ----

    @property
    def presenting(self) -> bool:
        """现在还有重要演出在飞（规则推进应当让路）。"""

        return bool(self._presenting)

    def kinds(self) -> Tuple[str, ...]:
        """此刻挡路的重要演出种类（去重、稳定顺序）。"""

        seen = []
        for schemas in self._presenting.values():
            for schema in schemas:
                name = str(getattr(schema, "kind", "") or "")
                if name and name not in seen:
                    seen.append(name)
        return tuple(seen)

    def holds_local_input(self) -> bool:
        """本机界面要不要为演出让路。

        唯一的例外：**本机玩家正在被要求做一件事**——演出让开，否则那条请求
        没有界面可以回答，流程与演出互相等；更常见的是玩家干等一大段演出，
        点哪都没反应。

        "正在被要求做一件事"必须问 :func:`contracts.local_input.
        local_interaction_slots`，而不是只看 ``pending_request``：**出牌选目标**
        根本不是引擎请求（它是本地收集状态），旧写法把"轮到我选目标"判成
        "没人回答"→ 让路 → 点击全被吞（Phase 18.5 批量试玩实测）。

        判定期间的输入压制不归这里管：``JudgeGate`` 在点击路由的最前面先判
        "现在只允许判定自己要的输入"，那道闸门优先（见 ``ui.interaction``）。
        """

        if not self._presenting:
            return False
        return not local_awaiting_input(self.game)

    def describe(self) -> str:
        kinds = self.kinds()
        return "presenting=%s forced=%d" % ("/".join(kinds) or "?", self.forced)
