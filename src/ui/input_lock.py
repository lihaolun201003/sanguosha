"""输入优先级与输入锁（Presentation System 2.0）。

演出期间**不做全局粗暴禁用**：这张牌桌上永远有"该能点的东西"和"现在不该
能点的东西"两类。全局禁用会让玩家觉得界面坏了（点了没反应），局部放行才能
既保住顺序、又不卡住人。本模块给出**唯一**的判据。

# 层级（数字越大越紧急，能压住下面所有层）

    JUDGE                100   判定：判定面板与判定流程自己的输入
    MANDATORY_RESPONSE    80   必须回答：引擎请求 / 出闪 / 求桃 / 无懈 / 二选一
    MODAL                 70   模态选择：用牌方式 / 技能选择 / 视为技来源 / 火攻
    TARGET_SELECTION      60   选目标
    CARD_SELECTION        50   选牌（五谷 / 弃牌 / 技能费用）
    NORMAL_PLAY           20   正常出牌 / 弃牌 / 结束回合
    AMBIENT               10   与规则无关的常驻控件（投降 / 战报…）
    ALWAYS                 0   **永远可点**：动画速度、跳过演出、调试开关

# 两条硬规则

1. **判定期间**：判定层最高。本机玩家不能普通出牌、不能选目标；只有判定
   流程自己要的输入（改判窗口的「跳过」之类）能进去——那一条由
   ``judge_gate`` 判定，本模块只是转述。
2. **演出让路不是禁用**：重要演出在播时（技能横幅 / 判定结论），
   只压"与规则有关的操作"，``ALWAYS`` 层照常（玩家随时能调速度或跳过演出）。
   这是"点了没反应"这类反馈的主要来源，必须在架构上留出口。

# 只读

本模块纯查询：不改任何规则数据、不推进任何流程。``game`` 可以是权威 Game，
也可以是联机客户端的只读视图（两者提供同一组槽位字段）。
"""

from src.game.contracts.local_input import local_interaction_slots

#: 永远可点的层：本地表现设置与调试，与规则无关。
ALWAYS = "always"


class InputPriority:
    """层级数值（也是各层之间的比较依据）。"""

    JUDGE = 100
    MANDATORY_RESPONSE = 80
    MODAL = 70
    TARGET_SELECTION = 60
    CARD_SELECTION = 50
    NORMAL_PLAY = 20
    AMBIENT = 10
    ALWAYS_RANK = 0

    #: 层名 → 数值。
    RANKS = {
        "judge": JUDGE,
        "mandatory": MANDATORY_RESPONSE,
        "modal": MODAL,
        "target": TARGET_SELECTION,
        "selection": CARD_SELECTION,
        "normal": NORMAL_PLAY,
        "ambient": AMBIENT,
        ALWAYS: ALWAYS_RANK,
    }

    #: 层名 → 中文说明（调试面板 / 日志 / 让路提示）。
    LABELS = {
        "judge": "判定输入",
        "mandatory": "必须回答",
        "modal": "模态选择",
        "target": "选择目标",
        "selection": "选择卡牌",
        "normal": "普通操作",
        "ambient": "常驻控件",
        ALWAYS: "始终可用",
        "presentation": "演出播放中",
        "game_over": "对局结束",
    }

    #: 特殊层：不是"数值比较"，而是"只允许这几层"。
    RESTRICTED = {
        # 演出让路：只留本地表现设置与调试（速度 / 跳过 / F1）。
        "presentation": (ALWAYS,),
        # 对局结束：结算界面自己的按钮，加上本地设置。
        "game_over": (ALWAYS, "ambient"),
        # 判定：判定流程自己的输入由 judge_gate 放行后才轮到这里；
        # 未被放行时连速度控件都保留（玩家仍可调自己的动画速度）。
        "judge": (ALWAYS,),
    }


#: 一张需要被问"现在这个能不能点"的界面元素的层。
#: 组件的 ``layer`` 参数用这些名字，不要自己造字符串。
LAYER_ORDER = ("judge", "mandatory", "modal", "target", "selection", "normal",
               "ambient", ALWAYS)


def rank_of(layer):
    return InputPriority.RANKS.get(str(layer or ""), 0)


class InputLock:
    """某一帧的输入锁快照（**只读**，每帧重建一次即可）。"""

    def __init__(self, layer="normal", *, reason="", slots=(), holding=False):
        self.layer = str(layer or "normal")
        self.reason = str(reason or "")
        self.slots = tuple(slots or ())
        self.holding = bool(holding)

    # ---- 查询 ----

    @property
    def priority(self):
        if self.layer in InputPriority.RESTRICTED:
            return max(InputPriority.RANKS.values()) + 1
        return rank_of(self.layer)

    @property
    def label(self):
        return InputPriority.LABELS.get(self.layer, self.layer)

    def allows(self, layer):
        """``layer`` 这个控件现在能不能点。

        判据只有两条：``ALWAYS`` 层永远放行；特殊层按白名单；其余按数值比较
        （"当前层及更紧急的层"可以点）。
        """

        name = str(layer or "normal")
        if name == ALWAYS:
            return True
        allowed = InputPriority.RESTRICTED.get(self.layer)
        if allowed is not None:
            return name in allowed
        return rank_of(name) >= self.priority

    def allows_play(self):
        """现在能不能正常出牌 / 弃牌 / 结束回合。"""

        return self.allows("normal")

    def blocks_play(self):
        return not self.allows_play()

    def describe(self):
        head = "%s(%d)" % (self.label, self.priority)
        if self.reason:
            head += " · " + self.reason
        if self.slots:
            head += " · " + "/".join(self.slots)
        return head

    def diagnostics(self):
        """F1 调试面板要的一行字段。"""

        return {
            "layer": self.layer,
            "label": self.label,
            "priority": self.priority,
            "reason": self.reason,
            "slots": list(self.slots),
            "holding": self.holding,
        }


def resolve(game, effects=None):
    """推导本机现在的输入层（唯一入口，每帧算一次）。

    顺序**就是**优先级顺序，从最紧急的一层往下问：

    1. 结算界面
    2. 判定（``judge_gate`` 说了算）
    3. 必须回答（引擎请求 / 响应窗口 / 二选一）
    4. 模态选择（方式 / 技能 / 来源 / 火攻）
    5. 选目标
    6. 选牌
    7. 演出让路（只留 ``ALWAYS``）
    8. 普通操作
    """

    if game is None:
        return InputLock("normal")

    # ---- 1. 对局结束：结算界面接管 ----
    if getattr(game, "game_over", False):
        return InputLock("game_over", reason="对局结束")

    # ---- 2. 判定：最高层 ----
    gate = getattr(game, "judge_gate", None)
    if gate is not None and not gate.allows_local_input():
        return InputLock("judge", reason="判定展示中")

    slots = local_interaction_slots(game)

    # ---- 3. 必须回答 ----
    if "pending_request" in slots or "response" in slots or "choice" in slots:
        return InputLock("mandatory", reason="等待本机回答", slots=slots)

    # ---- 4. 模态选择 ----
    picker = getattr(game, "card_action_picker", None)
    if callable(picker) and picker():
        return InputLock("modal", reason="选择用牌方式", slots=slots)
    if getattr(game, "pending_skill_picker", None):
        return InputLock("modal", reason="选择技能", slots=slots)
    if getattr(game, "pending_view_as", None) is not None:
        return InputLock("modal", reason="选择视为技来源", slots=slots)
    if getattr(game, "pending_card_action", None) is not None:
        return InputLock("modal", reason="收集费用牌", slots=slots)
    huogong = getattr(effects, "huogong", None)
    if huogong is not None and getattr(huogong, "active", False):
        return InputLock("modal", reason="火攻选牌", slots=slots)

    # ---- 5. 选目标 ----
    if getattr(game, "pending_target_selection", None) is not None:
        return InputLock("target", reason="选择目标", slots=slots)
    skill_input = getattr(game, "pending_skill_input", None)
    if skill_input is not None and skill_input.get("needs_target"):
        return InputLock("target", reason="技能选目标", slots=slots)

    # ---- 6. 选牌 ----
    if getattr(game, "pending_selection", None) is not None:
        return InputLock("selection", reason="选择卡牌", slots=slots)
    if skill_input is not None:
        return InputLock("selection", reason="选择技能费用", slots=slots)

    # ---- 7. 演出让路（不是全局禁用：ALWAYS 层照常） ----
    if effects is not None:
        hold = getattr(effects, "interaction_hold", None)
        if callable(hold) and hold():
            return InputLock("presentation", reason=hold_source(effects), slots=slots)

    # ---- 8. 普通操作 ----
    return InputLock("normal", slots=slots)


def hold_source(effects):
    """谁在让路（``storyboard`` / ``presentation_gate``），给诊断用。"""

    if effects is None:
        return "演出播放中"
    if hasattr(effects, "hold_source"):
        source = effects.hold_source() or ""
        return "技能演出" if source == "storyboard" else (
            "结算演出" if source == "presentation_gate" else "演出播放中")
    return "演出播放中"
