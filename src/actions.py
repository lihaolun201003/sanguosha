from collections import deque


class WaitAction:

    def __init__(self, duration):
        self.remaining = duration

    def update(self, dt):
        self.remaining -= dt
        return self.remaining <= 0


class CallbackAction:

    def __init__(self, callback):
        self.callback = callback
        self.called = False

    def update(self, dt):

        if not self.called:
            self.called = True
            self.callback()

        return True


class MoveCardAction:

    def __init__(
        self,
        card,
        start_rect,
        end_rect,
        duration=0.3,
        on_finish=None,
        resolver=None,
        face_down=False
    ):

        self.card = card

        # 飞行途中的表现：``face_down`` 表示"这一趟不该让人看见牌面"
        # （别人的摸牌 / 内容未知的牌）。纯表现标记，不动任何规则数据。
        self.face_down = bool(face_down)

        self.start_rect = tuple(start_rect) if not isinstance(start_rect, str) else start_rect
        self.end_rect = tuple(end_rect) if not isinstance(end_rect, str) else end_rect

        # 落位可以是命名区域（"table_card" 一类）：这时由 resolver 每帧向当前
        # 布局问坐标，窗口 resize / F11 全屏切换后动画落点自动跟着变，
        # 不会停在旧分辨率的像素上。
        self.resolver = resolver

        self.duration = max(duration, 0.001)

        self.elapsed = 0.0

        self.on_finish = on_finish

        self.finished_callback = False

    def _rect(self, value):
        if isinstance(value, str):
            if self.resolver is None:
                raise ValueError("named placement needs a resolver: " + value)
            return tuple(self.resolver(value))
        return value

    def update(self, dt):

        self.elapsed += dt

        done = self.elapsed >= self.duration

        if done and not self.finished_callback:

            self.finished_callback = True

            if self.on_finish is not None:
                self.on_finish()

        return done


    def current_rect(self):

        t = min(
            1.0,
            self.elapsed / self.duration
        )

        # 缓出动画
        t = 1 - (1 - t) ** 3

        values = []

        for start, end in zip(
            self._rect(self.start_rect),
            self._rect(self.end_rect)
        ):

            value = round(
                start + (end - start) * t
            )

            values.append(value)

        return tuple(values)


class ActionQueue:

    def __init__(self):

        self.queue = deque()

        self.current = None

        #: 表现层可以临时把队列压住：这个可调用对象返回 True 时，队列**不开始**
        #: 下一个动作（正在播的那个照常跑完）。判定展示期间用它把后续行动排到
        #: 判定结束之后——"判定还在演，下一件事已经开始"正是玩家看到的那种乱。
        #: 默认 None（无 UI 的测试与批量演算完全不受影响）。
        self.hold = None


    @property
    def busy(self):

        return (
            self.current is not None
            or len(self.queue) > 0
        )


    def held(self):
        """队列是不是被表现层压住了（还没开始下一个动作）。"""

        if self.hold is None or self.current is not None or not self.queue:
            return False
        return bool(self.hold())


    def clear(self):

        self.queue.clear()

        self.current = None


    def add(self, action):

        self.queue.append(action)


    def update(self, dt):

        if self.current is None and self.queue:

            if self.hold is not None and self.hold():
                # 被表现层压住：这一帧什么都不启动（当前动作也没有）。
                return

            self.current = self.queue.popleft()


        while self.current is not None:

            finished = self.current.update(dt)

            dt = 0.0

            if not finished:
                break

            self.current = None

            if self.queue:
                self.current = self.queue.popleft()


    def current_move(self):

        if isinstance(
            self.current,
            MoveCardAction
        ):

            return self.current

        return None


    def animating_card_ids(self):
        """正在被移动动画表现的牌（稳定身份 ``id(card)``）。

        这是表现层的**视觉所有权**查询：出现在这里的一张牌，此刻由移动
        动画独占绘制，任何静态区域（手牌 / 桌面主体 / 公共池 / 弃牌堆顶）
        都不得再画一份，否则同一张牌会出现两个副本。
        """

        ids = set()

        if isinstance(self.current, MoveCardAction):
            ids.add(id(self.current.card))

        for action in self.queue:
            if isinstance(action, MoveCardAction):
                ids.add(id(action.card))

        return ids