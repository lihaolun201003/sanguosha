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
        on_finish=None
    ):

        self.card = card

        self.start_rect = tuple(start_rect)
        self.end_rect = tuple(end_rect)

        self.duration = max(duration, 0.001)

        self.elapsed = 0.0

        self.on_finish = on_finish

        self.finished_callback = False


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
            self.start_rect,
            self.end_rect
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


    @property
    def busy(self):

        return (
            self.current is not None
            or len(self.queue) > 0
        )


    def clear(self):

        self.queue.clear()

        self.current = None


    def add(self, action):

        self.queue.append(action)


    def update(self, dt):

        if self.current is None and self.queue:

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