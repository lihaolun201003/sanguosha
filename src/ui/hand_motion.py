"""手牌几何的补间（Presentation System 2.0）。

手牌是这块牌桌上被点得最多、也最容易被"瞬间跳一下"毁掉观感的地方。
在此之前 ``TableLayout._build_hand`` 每帧按 ``-hover_lift`` / ``-selected_lift``
直接算坐标：悬停、取消、选中、取消、出牌后的整体重排**全是瞬移**。
本模块把这些位移变成补间，并保证**命中测试用的仍然是画出来的那一份 rect**。

# 一张牌只有一个终值

    显示位置 = 基础排列位置 − 上浮量
    上浮量   = 选中 40 / 悬停 28 / 无 0（设计坐标，见 ``layout``）

位置与上浮合成**同一条**补间：两套补间会让"选中时同时发生重排"的牌走两条
不同节奏的路径，看起来像在抽搐。

# 时长怎么选

* **水平位置变了** → 这是一次重排（出牌 / 摸牌 / 排序），用 ``hand_reorder``；
* **只有垂直变了** → 这是悬停 / 选中反馈，用 ``hover`` / ``card_select``。

两类用不同时长，才不会出现"悬停慢半拍"或"重排一闪而过"。

# 新出现的牌不做位移动画

摸牌 / 开局发牌由 ``ui.card_transfer`` 的飞行动画负责，手牌区只负责"落位"。
让两者都动起来，玩家会看到同一张牌从两个方向飘。
"""

from . import anim_config
from .tween import PointTrack


class HandMotion:
    """一份手牌的显示位置补间。"""

    def __init__(self):
        self.positions = PointTrack(anim_config.duration("hand_reorder"), "ease_out_cubic")

    # ---- 查询 ----

    def offset(self, card):
        """这张牌当前的**绝对**显示位置 ``(x, y)``；未知返回 None。

        返回的是位置而不是偏移：``TableLayout`` 用基础 rect 的左上角与它比较，
        基础排列一变（少了一张牌）就自然表现为"平滑移动到新位置"。
        """

        return self.positions.value(id(card))

    def busy(self):
        return self.positions.busy()

    def describe(self):
        return "hand=%d moving=%s" % (len(self.positions), self.busy())

    # ---- 每帧 ----

    def advance(self, cards, base_rects, *, hover_index=None, selected_keys=(),
                hover_lift=0.0, selected_lift=0.0, dt=0.0, skip=False):
        """设目标并推进一帧。

        ``base_rects`` 是这一帧的基础排列（不含上浮），与 ``cards`` 一一对应。
        返回这一帧每张牌的显示位置（与 ``cards`` 同序）。
        """

        config = anim_config.current()
        cards = list(cards or ())
        base_rects = list(base_rects or ())
        if len(cards) != len(base_rects):
            return []
        keys = [id(card) for card in cards]
        # 离手的牌：立刻丢掉它的位置（牌已经不在手牌里了，位置没有意义）。
        self.positions.keep_only(keys)

        selected_keys = set(selected_keys or ())
        for index, card in enumerate(cards):
            key = id(card)
            rect = base_rects[index]
            lift = 0.0
            duration = config("hand_reorder")
            if index in selected_keys:
                lift = float(selected_lift)
                duration = config("card_select")
            elif hover_index is not None and index == hover_index:
                lift = float(hover_lift)
                duration = config("hover")

            target = (float(rect.x), float(rect.y - lift))
            current = self.positions.value(key)
            moved_horizontally = (current is not None
                                  and abs(current[0] - target[0]) > 0.5)
            if moved_horizontally:
                # 水平位移 = 重排：无论触发原因是悬停还是出牌，都按重排的节奏。
                duration = config("hand_reorder")
            if skip:
                self.positions.place(key, target)
            else:
                self.positions.to(key, target, duration)

        if skip:
            self.positions.finish()
            self.positions.update(0.0)
        else:
            self.positions.update(dt)
        return [self.positions.value(id(card), (rect.x, rect.y))
                for card, rect in zip(cards, base_rects)]

    def place_all(self, cards, base_rects):
        """不做动画，直接把所有牌放到基础位置（首帧 / 重开一局）。"""

        for card, rect in zip(list(cards or ()), list(base_rects or ())):
            self.positions.place(id(card), (rect.x, rect.y))

    def refresh_durations(self):
        """速度档变化后刷新默认时长（补间创建时就记下了时长）。"""

        self.positions.default_duration = anim_config.current()("hand_reorder")

    def finish_all(self):
        """跳过演出：所有牌立刻到位。"""

        self.positions.finish()

    def clear(self):
        self.positions.clear()
