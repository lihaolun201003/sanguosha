"""统一补间动画（Presentation System 2.0）。

全项目的**唯一**补间实现：位置、缩放、透明度、旋转、数值都走这里。
在此之前每个动画各自写 ``elapsed / duration`` 与自己的 ``_ease_out``
（``fx.DealFlight`` / ``actions.MoveCardAction`` / 各处 ``alpha = 剩余/总量``），
同一个"缓出"在四个文件里四份实现，改节奏要满仓库找。

# 三层

    EASINGS          缓动函数表（名字 → 纯函数，输入 0..1）
    Tween            一个标量：起点 → 终点，按缓动推进；可中途改目标
    TweenTrack       按 key 管一组 Tween（"这张牌的上浮量" / "这个按钮的缩放"）
    TweenManager     一个场景的所有 Track + 关键帧序列

# 约定

* **只读表现**：本模块不导入 ``pygame``，也不知道规则。它算出来的数字谁用谁负责。
* **改目标不打断**：``Tween.retarget`` 从**当前值**出发，所以"悬停中途取消"、
  "手牌重新排序"都不会跳一下。
* **时长一律由调用方从 ``ui.anim_config`` 取**：不要在调用点写 0.2 这样的数字。
* **速度档**：``TweenManager.update`` 收到的是**本机表现秒**（已经乘过速度倍率），
  缩放由调用方（表现层）完成，这里的时长就是"这一台机器上要播多久"。
"""

import math

# ==================================================
# 缓动
# ==================================================


def _clamp01(value):
    if value <= 0.0:
        return 0.0
    if value >= 1.0:
        return 1.0
    return float(value)


def linear(t):
    return _clamp01(t)


def ease_in_quad(t):
    t = _clamp01(t)
    return t * t


def ease_out_quad(t):
    t = _clamp01(t)
    return 1.0 - (1.0 - t) * (1.0 - t)


def ease_in_out_quad(t):
    t = _clamp01(t)
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 2 / 2.0


def ease_in_cubic(t):
    t = _clamp01(t)
    return t * t * t


def ease_out_cubic(t):
    t = _clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t):
    t = _clamp01(t)
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def ease_out_back(t, overshoot=1.70158):
    """回弹收尾：略微冲过终点再退回（选中 / 落位用）。"""

    t = _clamp01(t)
    c3 = overshoot + 1.0
    return 1.0 + c3 * (t - 1.0) ** 3 + overshoot * (t - 1.0) ** 2


def ease_in_back(t, overshoot=1.70158):
    t = _clamp01(t)
    c3 = overshoot + 1.0
    return c3 * t * t * t - overshoot * t * t


def ease_out_elastic(t):
    """落位的轻微弹跳（幅度刻意很小，牌桌不需要弹簧玩具）。"""

    t = _clamp01(t)
    if t in (0.0, 1.0):
        return t
    c4 = (2.0 * math.pi) / 3.0
    return 2.0 ** (-10.0 * t) * math.sin((t * 10.0 - 0.75) * c4) + 1.0


EASINGS = {
    "linear": linear,
    "ease_in_quad": ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_in_cubic": ease_in_cubic,
    "ease_out_cubic": ease_out_cubic,
    "ease_in_out_cubic": ease_in_out_cubic,
    "ease_out_back": ease_out_back,
    "ease_in_back": ease_in_back,
    "ease_out_elastic": ease_out_elastic,
}

#: 默认缓动：绝大多数 UI 位移用它（起步快、收尾稳）。
DEFAULT_EASING = "ease_out_cubic"


def easing(name):
    """按名字取缓动函数；未注册的名字退回默认缓动（绝不抛异常）。

    表现层不允许因为一个拼错的缓动名整帧画不出来。
    """

    if callable(name):
        return name
    return EASINGS.get(str(name or ""), EASINGS[DEFAULT_EASING])


# ==================================================
# 一条补间
# ==================================================


class Tween:
    """一个标量从 ``start`` 到 ``end``，按 ``easing`` 推进。

    ``duration <= 0`` 视为"瞬时"：第一帧就到达终点（``done`` 为真）。
    ``delay`` 之前的 ``value`` 恒为 ``start``——它让"先亮牌、再位移"这类
    顺序不需要调用方自己再养一个计时器。
    """

    __slots__ = ("start", "end", "duration", "easing_name", "delay", "elapsed",
                 "on_complete", "finished")

    def __init__(self, start, end, duration, easing_name=DEFAULT_EASING, *,
                 delay=0.0, on_complete=None):
        self.start = float(start)
        self.end = float(end)
        self.duration = max(0.0, float(duration))
        self.easing_name = easing_name
        self.delay = max(0.0, float(delay))
        self.elapsed = 0.0
        self.on_complete = on_complete
        self.finished = False

    # ---- 查询 ----

    @property
    def _progress(self):
        if self.duration <= 0:
            return 1.0 if self.elapsed >= self.delay else 0.0
        return _clamp01((self.elapsed - self.delay) / self.duration)

    @property
    def value(self):
        if self.elapsed < self.delay:
            return self.start
        if self.duration <= 0:
            return self.end
        ratio = easing(self.easing_name)(self._progress)
        return self.start + (self.end - self.start) * ratio

    @property
    def done(self):
        if self.duration <= 0:
            return self.elapsed >= self.delay
        return self.elapsed >= self.delay + self.duration

    # ---- 推进 ----

    def update(self, dt):
        """推进一帧；返回"还在跑吗"（False = 已经结束）。"""

        if self.finished:
            return False
        self.elapsed += max(0.0, float(dt))
        if self.done:
            self.finished = True
            self.start = self.end = self.end
            if self.on_complete is not None:
                self.on_complete()
            return False
        return True

    def finish(self):
        """立刻到达终点（跳过演出的用）。"""

        self.elapsed = self.delay + self.duration
        self.finished = True

    # ---- 改目标 ----

    def retarget(self, end, duration=None, easing_name=None):
        """把终点换成新的，**从当前值出发**（不跳变）。"""

        current = self.value
        self.start = current
        self.end = float(end)
        if duration is not None:
            self.duration = max(0.0, float(duration))
        if easing_name is not None:
            self.easing_name = easing_name
        self.elapsed = 0.0
        self.delay = 0.0
        self.finished = False
        return self


class PointTween:
    """二维点（位置）的补间：内部两条标量 Tween，x / y 同步推进。"""

    def __init__(self, start, end, duration, easing_name=DEFAULT_EASING, *,
                 delay=0.0, on_complete=None):
        self.x = Tween(start[0], end[0], duration, easing_name, delay=delay)
        self.y = Tween(start[1], end[1], duration, easing_name, delay=delay)
        self.on_complete = on_complete
        self._notified = False

    @property
    def value(self):
        return (self.x.value, self.y.value)

    @property
    def done(self):
        return self.x.done and self.y.done

    def update(self, dt):
        alive = self.x.update(dt)
        self.y.update(dt)
        if not alive and not self._notified:
            self._notified = True
            if self.on_complete is not None:
                self.on_complete()
        return alive

    def finish(self):
        self.x.finish()
        self.y.finish()

    def retarget(self, end, duration=None, easing_name=None):
        self.x.retarget(end[0], duration, easing_name)
        self.y.retarget(end[1], duration, easing_name)
        self._notified = False
        return self


# ==================================================
# 一组命名补间
# ==================================================


class TweenTrack:
    """按 key 管理一组补间（"每张手牌的上浮量" / "每个按钮的缩放"）。

    两种用法：

    * :meth:`to` —— 平滑逼近一个目标值（同一 key 反复调用只改终点，不新建）；
    * :meth:`set` —— 直接落值（首帧初始化 / 跳过演出）。
    """

    def __init__(self, duration=0.15, easing_name=DEFAULT_EASING):
        self.default_duration = float(duration)
        self.default_easing = easing_name
        self._items = {}

    def __contains__(self, key):
        return key in self._items

    def __len__(self):
        return len(self._items)

    def keys(self):
        return list(self._items)

    def value(self, key, default=0.0):
        tween = self._items.get(key)
        if tween is None:
            return default
        return tween.value

    def raw(self, key):
        return self._items.get(key)

    def to(self, key, end, duration=None, easing_name=None, *, on_complete=None,
           start=None):
        """平滑逼近 ``end``；``start`` 只在第一次出现时用作起点。

        **目标没变时不重新计时**：调用方每帧都会把"这一帧算出来的目标"喂进来，
        无条件 retarget 会把计时反复归零，动画永远走不到终点（手牌会停在
        半路上抖）。判据是"终点有没有变"。
        """

        tween = self._items.get(key)
        if tween is not None:
            if abs(tween.end - float(end)) < 0.01 and not tween.finished:
                return tween
            tween.retarget(end, self.default_duration if duration is None else duration,
                           easing_name)
            return tween
        origin = end if start is None else start
        tween = Tween(origin, end, self.default_duration if duration is None else duration,
                      easing_name or self.default_easing)
        self._items[key] = tween
        if origin == end and tween.duration <= 0:
            tween.finish()
        return tween

    def set(self, key, value):
        tween = self._items.get(key)
        if tween is None:
            tween = Tween(value, value, 0.0)
            self._items[key] = tween
        else:
            tween.start = tween.end = float(value)
            tween.elapsed = tween.delay + tween.duration
            tween.finished = True
        return tween

    def finish(self, key=None):
        targets = [key] if key is not None else list(self._items)
        for item in targets:
            tween = self._items.get(item)
            if tween is not None:
                tween.finish()

    def drop(self, key):
        return self._items.pop(key, None)

    def keep_only(self, keys):
        """丢掉不在 ``keys`` 里的条目（手牌离手 / 按钮消失时用）。"""

        wanted = set(keys)
        for item in list(self._items):
            if item not in wanted:
                del self._items[item]

    def busy(self):
        return any(not tween.done for tween in self._items.values())

    def update(self, dt):
        for item in list(self._items):
            tween = self._items[item]
            if tween.finished and tween.done:
                continue
            tween.update(dt)

    def clear(self):
        self._items.clear()


class PointTrack:
    """位置版本的 :class:`TweenTrack`（点 → 点）。"""

    def __init__(self, duration=0.28, easing_name=DEFAULT_EASING):
        self.default_duration = float(duration)
        self.default_easing = easing_name
        self._items = {}
        self._arrivals = {}

    def __contains__(self, key):
        return key in self._items

    def __len__(self):
        return len(self._items)

    def keys(self):
        return list(self._items)

    def value(self, key, default=None):
        tween = self._items.get(key)
        if tween is None:
            return default
        return tween.value

    def to(self, key, end, duration=None, easing_name=None, *, on_complete=None,
           start=None):
        """平滑移动到 ``end``；同一个 key 反复调用只改终点。

        **首次出现（key 不在轨道里）默认"就位"**：牌第一次进入手牌时它本来
        就该出现在目标位置，从别处滑过来会和摸牌的飞行动画打架。需要首帧
        也补间（例如手牌整体重排的第一帧）就显式给 ``start``。

        目标没变时不重新计时（详见 ``TweenTrack.to`` 的说明）。
        """

        if on_complete is not None:
            self._arrivals[key] = on_complete
        tween = self._items.get(key)
        duration = self.default_duration if duration is None else duration
        easing_name = easing_name or self.default_easing
        if tween is not None:
            if (abs(tween.x.end - float(end[0])) < 0.01
                    and abs(tween.y.end - float(end[1])) < 0.01
                    and not (tween.x.finished and tween.y.finished)):
                return tween
            tween.retarget(end, duration, easing_name)
            return tween
        location = end if start is None else start
        tween = PointTween(location, location, 0.0)
        self._items[key] = tween
        if start is not None and duration > 0:
            tween.retarget(end, duration, easing_name)
        return tween

    def place(self, key, position):
        """不做动画，直接把位置放好（首帧 / 跳过演出用）。"""

        tween = self._items.get(key)
        if tween is None:
            self._items[key] = PointTween(position, position, 0.0)
            return
        tween.x.start = tween.x.end = float(position[0])
        tween.y.start = tween.y.end = float(position[1])
        tween.x.elapsed = tween.x.delay + tween.x.duration
        tween.y.elapsed = tween.y.delay + tween.y.duration
        tween.x.finished = tween.y.finished = True

    def finish(self, key=None):
        targets = [key] if key is not None else list(self._items)
        for item in targets:
            tween = self._items.get(item)
            if tween is not None:
                tween.finish()

    def drop(self, key):
        self._arrivals.pop(key, None)
        return self._items.pop(key, None)

    def keep_only(self, keys):
        wanted = set(keys)
        for item in list(self._items):
            if item not in wanted:
                del self._items[item]
                self._arrivals.pop(item, None)

    def busy(self):
        return any(not tween.done for tween in self._items.values())

    def update(self, dt):
        for item in list(self._items):
            tween = self._items[item]
            if not tween.done:
                tween.update(dt)
            if tween.done and item in self._arrivals:
                callback = self._arrivals.pop(item)
                callback()

    def clear(self):
        self._items.clear()
        self._arrivals.clear()


# ==================================================
# 场景级管理器
# ==================================================


class TweenManager:
    """一局 / 一个场景的全部补间。

    ``update(dt)`` 里的 ``dt`` 是**本机表现秒**（表现层已经乘过速度倍率），
    所以这里不再缩放——速度档只有一处生效点（``ui.anim_config``）。
    """

    def __init__(self):
        self._tracks = {}
        self._singles = []
        self.time_scale = 1.0

    def track(self, name, *, duration=0.15, easing_name=DEFAULT_EASING):
        """取（或建）一个标量轨道。"""

        entry = self._tracks.get(name)
        if entry is None or not isinstance(entry, TweenTrack):
            entry = TweenTrack(duration, easing_name)
            self._tracks[name] = entry
        return entry

    def points(self, name, *, duration=0.28, easing_name=DEFAULT_EASING):
        """取（或建）一个位置轨道。"""

        entry = self._tracks.get(name)
        if entry is None or not isinstance(entry, PointTrack):
            entry = PointTrack(duration, easing_name)
            self._tracks[name] = entry
        return entry

    def add(self, tween):
        """登记一条一次性补间（不按 key 管理）。"""

        self._singles.append(tween)
        return tween

    def update(self, dt):
        step = max(0.0, float(dt)) * float(self.time_scale)
        for track in self._tracks.values():
            track.update(step)
        for tween in list(self._singles):
            if not tween.update(step):
                self._singles.remove(tween)

    def finish_all(self):
        """跳过演出：所有补间立刻到达终点。"""

        for track in self._tracks.values():
            track.finish()
        for tween in list(self._singles):
            tween.finish()
        self._singles.clear()

    def clear(self):
        for track in self._tracks.values():
            track.clear()
        self._tracks.clear()
        self._singles.clear()

    def describe(self):
        busy = sum(1 for track in self._tracks.values() if track.busy())
        return "tracks=%d busy=%d singles=%d" % (
            len(self._tracks), busy, len(self._singles))
