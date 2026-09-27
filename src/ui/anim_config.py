"""UI 动画时长总表（Presentation System 2.0）。

**全项目只在这里定义"某个界面动作该播多久"。** 组件不许再写 0.12 / 0.3 这样
的字面量，也不许各自再养一个 ``_speed_factor()``：时长只有一份来源，速度档
也只有一处生效点。

# 与 ``fx.FXTiming`` 的分工（不要合并）

    FXTiming      规则结算的**语义阶段**时长（判定翻开 → 改判 → 结论；
                  0.55 秒亮牌、1.15 秒箭头停留…）。它们描述"一次结算演多久"。
    UIAnimationConfig  界面**微交互**时长（悬停 0.12、按下 0.08、选中 0.15、
                  卡牌移动 0.28…）。它们描述"一个控件怎么响应玩家"。

两者都是"本机表现秒"，都受同一个速度档缩放，只是层次不同：把微交互也塞进
FXTiming 会让那张表变成几十个互不相关的数字；反过来把结算时长写进这里，
速度档就会有两个生效点。

# 速度

``Game.speed``（座主）/ ``RemoteGameView.speed``（客户端，见 ``ui.speed``）
是**每台机器自己的**，换算成倍率的方式与 ``ui.storyboard`` 完全一致：

    倍率 = speed / BASE_SPEED        （BASE_SPEED = 0.75，默认档 → 1.0）
    时长 = 基准秒数 / 倍率           （快档更短，慢档更长）

规则 timeout **不受影响**：那些值不在本表里，也没有任何地方拿它去乘倍率。
"""

#: 表现速度基准档（与 ``Game.DEFAULT_SPEED`` / ``storyboard.BASE_SPEED`` 一致）。
BASE_SPEED = 0.75

#: 倍率上下限（防手改出离谱的值），与 storyboard 同源。
MIN_SPEED_FACTOR = 0.35
MAX_SPEED_FACTOR = 2.5

#: 所有界面微交互的基准时长（秒，默认速度档下）。
BASE_DURATIONS = {
    # ---- 控件反馈 ----
    "hover": 0.12,
    "button_press": 0.08,
    "focus": 0.14,
    "disabled": 0.18,
    # ---- 卡牌 ----
    "card_select": 0.15,
    "card_move": 0.28,
    "card_use": 0.35,
    "card_deal": 0.30,
    "hand_reorder": 0.22,
    # ---- 结算反馈 ----
    "damage": 0.30,
    "heal": 0.30,
    "dying": 0.36,
    "death": 0.42,
    "hp_change": 0.32,
    "phase_change": 0.30,
    # ---- 横幅与面板 ----
    "skill_banner": 0.65,
    "turn_banner": 0.55,
    "judge_reveal": 0.45,
    "identity_reveal": 0.70,
    "modal": 0.18,
    "toast": 0.22,
    "banner_in": 0.26,
    "banner_out": 0.30,
    # ---- 遮罩 / 让路 ----
    "dim": 0.24,
    "arrow_draw": 0.34,
    "arrow_fade": 0.28,
    "highlight": 0.20,
    "scale_pop": 0.22,
}

#: 名字 → 中文说明（F1 调试面板与报告用）。
DURATION_LABELS = {
    "hover": "悬停上浮",
    "button_press": "按钮按下",
    "card_select": "卡牌选中",
    "card_move": "卡牌移动",
    "card_use": "出牌",
    "damage": "受伤",
    "heal": "回复",
    "skill_banner": "技能横幅",
    "turn_banner": "回合横幅",
    "judge_reveal": "判定翻开",
    "modal": "模态框",
}


def _clamp_factor(factor):
    value = float(factor or 1.0)
    if value <= 0:
        return 1.0
    return max(MIN_SPEED_FACTOR, min(MAX_SPEED_FACTOR, value))


class UIAnimationConfig:
    """界面时长表的一个快照（绑定当前速度倍率）。

    组件通过 :func:`current` 取全局那一份；表现层每帧把本机速度同步进来
    （:func:`set_speed`）。不持有 Game 的组件（按钮 / tooltip）因此也能拿到
    与演出队列**一致**的节奏，不会出现"动画快了但按钮还是慢的"。
    """

    def __init__(self, factor=1.0):
        self.factor = _clamp_factor(factor)

    # ---- 主入口 ----

    def __call__(self, name, default=0.2):
        """取某个动作的时长（秒，已按速度档缩放）。"""

        base = BASE_DURATIONS.get(str(name), default)
        return base / self.factor

    def seconds(self, name, default=0.2):
        return self(name, default)

    def scaled(self, seconds_value):
        """把一段**已经确定**的基准秒数按速度档缩放（给临时时长用）。"""

        return max(0.0, float(seconds_value)) / self.factor

    def raw(self, name, default=0.2):
        """基准时长（不缩放），只给报表 / 调试面板看。"""

        return BASE_DURATIONS.get(str(name), default)

    def with_factor(self, factor):
        return UIAnimationConfig(factor)

    def describe(self):
        return "speed x%.2f" % (self.factor,)


def speed_factor(speed):
    """把 ``Game.speed`` / 视图的 speed 换算成表现倍率。"""

    if not isinstance(speed, (int, float)) or speed <= 0:
        return 1.0
    return _clamp_factor(float(speed) / BASE_SPEED)


#: 全局那一份：所有不持有 Game 的组件都读它。
_current = UIAnimationConfig(1.0)


def current():
    """当前生效的界面时长表。"""

    return _current


def set_speed(speed):
    """按本机表现速度刷新全局时长表（表现层每帧调用一次）。"""

    global _current
    factor = speed_factor(speed)
    if abs(factor - _current.factor) > 1e-6:
        _current = UIAnimationConfig(factor)
    return _current


def set_factor(factor):
    global _current
    _current = UIAnimationConfig(factor)
    return _current


def duration(name, default=0.2):
    """便捷函数：``anims.duration("hover")``。"""

    return _current(name, default)
