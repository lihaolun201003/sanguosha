"""判定优先闸门（Judge Priority Gate）。

只要存在**尚未走完**的判定，整场游戏就以判定为最高优先级：唯一允许的输入
是当前判定流程自己要的那个回答。其余一切——出牌、主动技、结束阶段、选目标、
操作手牌 / 装备 / 公共牌池、进入另一条无关 Action——全部无效。

# 三个必须分开的状态

判定有三种"占用"，它们的后果不一样，混在一起写就会出 bug：

    JudgeFlow logical pending      规则上判定还没结束（JudgeFlow 还在跑）
    Judge replacement pending      判定正停在改判窗口上等某个人出牌
    Judge UI presentation active   规则上判定已经结算完，界面还在演

前两个由规则层就能回答：活跃 JudgeFlow 的注册表 + 当前 ``PendingRequest``。
第三个只有表现层知道（"判定牌还在屏幕中央"），因此由表现层用
``mark_presentation()`` 汇报。

# 两个消费点，规则层不再散落 if

    JudgeGate.accepts(action)        引擎提交动作时的唯一判据（真人 / AI / LAN 同路）
    JudgeGate.allows_local_input()   UI 查询"能不能点"的统一入口

# 为什么 presentation 阶段不拦引擎

规则上判定已经结束，AI 的后续结算必须能继续——拦了就会把 AI 的回合卡死
（AI 的推进路径不是每一条都会在失败后重试）。这一阶段要保证的只是"玩家别在
判定牌还在屏幕中央时提前出下一张牌"，所以它只作用于真人输入。动作队列那一层
的压制另由 ``JudgePanel.holds_actions`` 负责。
"""

IDLE = "idle"
LOGICAL = "logical"
REPLACEMENT = "replacement"
PRESENTATION = "presentation"

#: JudgeFlow 处于这些状态时才算"判定还没走完"。
#: 故意不含 READY：**构造出来但还没 start** 的流程不是"正在判定"，
#: 占用闸门会让一次未发生的判定锁死整局（流程在 _draw 里才登记）。
_LIVE_STATUS = frozenset({"running", "waiting"})

_REPLACEMENT_REASON = "judge_replacement"


class JudgeGate:
    """判定优先闸门；通过 ``Game.judge_gate`` 访问。"""

    def __init__(self, game):
        self.game = game
        #: 还在跑的判定流程（由 JudgeFlow 自己在 __init__ 里登记）。
        self._judges = []
        #: 表现层的汇报来源（键是表现层自己的身份，例如 JudgePanel 对象）。
        #: 用集合而不是计数器：同一块面板重复汇报不会累加。
        self._presentations = {}

    # ==================================================
    # 登记（规则层 / 表现层）
    # ==================================================

    def register_judge(self, flow):
        if flow is not None and not any(item is flow for item in self._judges):
            self._judges.append(flow)

    def unregister_judge(self, flow):
        self._judges = [item for item in self._judges if item is not flow]

    def mark_presentation(self, source, active):
        """表现层汇报"判定还在演"；``source`` 是汇报者的稳定身份。"""

        if source is None:
            return
        if active:
            self._presentations[source] = True
        else:
            self._presentations.pop(source, None)

    def clear_presentation(self):
        self._presentations.clear()

    # ==================================================
    # 状态查询
    # ==================================================

    @property
    def active_judge(self):
        """还在跑的判定流程；完成 / 取消过的会被顺手剔除。

        惰性剔除是必需的：判定的收尾路径不止一条（改判窗口、判定后技能开的
        嵌套窗口、异常取消），任何一条漏了 unregister 都会让整局永久锁死。
        """

        alive = []
        found = None
        for flow in self._judges:
            if self._is_live(flow):
                alive.append(flow)
                if found is None:
                    found = flow
        if len(alive) != len(self._judges):
            self._judges = alive
        return found

    @staticmethod
    def _is_live(flow):
        if getattr(flow, "settled", False):
            return False
        status = getattr(getattr(flow, "status", None), "value", None)
        return status in _LIVE_STATUS

    @property
    def logical_pending(self):
        """规则上判定还没结束。"""

        return self.active_judge is not None

    @property
    def current_request(self):
        """引擎当前在等的请求（可能是判定自己的，也可能是别人的）。"""

        engine = getattr(self.game, "engine", None)
        if engine is None:
            return None
        return engine.pending.current

    @property
    def judge_request(self):
        """判定流程自己要的那条请求；判定没在等输入时返回 None。

        判定期间 ``PendingRequest`` 栈顶就是判定流程发出的那一条（改判窗口
        / 判定后技能的确认窗口）。判定流程内部再开的子窗口（颂威的"是否让
        判定生效"）也是同一条路径——它们都由判定自己在等。
        """

        if not self.logical_pending:
            return None
        request = self.current_request
        if request is None or getattr(request, "status", "") != "pending":
            return None
        return request

    @property
    def replacement_pending(self):
        request = self.current_request
        if request is None:
            return False
        return str(request.context.get("reason") or "") == _REPLACEMENT_REASON

    @property
    def presentation_active(self):
        return bool(self._presentations)

    @property
    def phase(self):
        if self.replacement_pending:
            return REPLACEMENT
        if self.logical_pending:
            return LOGICAL
        if self.presentation_active:
            return PRESENTATION
        return IDLE

    @property
    def active(self):
        return self.phase is not IDLE

    # ==================================================
    # 引擎侧判据
    # ==================================================

    @property
    def blocks(self):
        """现在是不是"全场只允许判定输入"（引擎侧权威判据）。"""

        return self.logical_pending

    def accepts(self, action):
        """这个动作是不是"当前判定流程要求的输入"。

        判据只有一条：它的 ``request_id`` 就是判定流程正在等的那条请求，
        且提交者确实是这条请求许可的回答者。因此出牌（无 request_id）、
        主动技、结束阶段、选普通目标全部天然落空。
        """

        request = self.judge_request
        if request is None:
            return False
        request_id = getattr(action, "request_id", None)
        if request_id is None or request_id != request.request_id:
            return False
        actor = getattr(action, "actor", None)
        if actor is None:
            return False
        return bool(request.is_member(actor))

    def guard(self, action):
        """引擎入口的统一门控：被封锁时返回原因字符串，放行返回空串。"""

        if not self.blocks:
            return ""
        if self.accepts(action):
            return ""
        return self.blocks_message()

    # ==================================================
    # UI 侧判据
    # ==================================================

    def allows_local_input(self, local=None):
        """本机玩家现在还能不能操作牌桌。

        判定的两种占用（规则上的 / 视觉上的）都不允许普通操作；唯一的例外是
        "当前这条判定请求问的正是本机玩家"——那是判定流程自己要求的输入，
        例如司马懿在改判窗口里挑一张手牌替换判定牌。
        """

        if not self.active:
            return True
        request = self.judge_request
        if request is None:
            return False
        player = local if local is not None else getattr(self.game, "player", None)
        if player is None:
            return False
        if request.is_group:
            return (request.is_member(player)
                    and request.member_status(player) == "pending")
        return request.target is player

    def local_request_id(self, local=None):
        """本机玩家现在可以回答的判定请求 id；没有就返回 None。"""

        if not self.allows_local_input(local):
            return None
        request = self.judge_request
        return None if request is None else request.request_id

    # ==================================================
    # 提示文案
    # ==================================================

    def blocks_message(self):
        phase = self.phase
        if phase is REPLACEMENT:
            request = self.current_request
            who = getattr(getattr(request, "target", None), "name", "")
            return ("判定改判中：" + who + " 正在决定是否替换判定牌") if who \
                else "判定改判中，请等待改判结果"
        if phase is LOGICAL:
            return "判定尚未结束，请等待判定结果"
        if phase is PRESENTATION:
            return "判定结果展示中，请稍候"
        return ""


def judge_gate(game):
    """取某局的判定闸门；没有（只读视图 / 手工构造的对象）返回 None。"""

    return getattr(game, "judge_gate", None)
