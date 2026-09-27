"""实体牌移动的统一表现（Presentation System 2.0）。

**任何"一张实体牌从 A 到 B"的画面都走这里。** 它的存在是因为同一个动作在
不同模块里有四份实现：开局发牌 ``Effects.queue_deal``、摸牌 ``queue_draw``、
引擎的 ``MoveCardAction``、客户端表现事件 ``client_fx.MOVE_DURATION`` 各自
算坐标、各自缓动、各自决定画正面还是背面。于是同一张牌在三个视角下速度不
一样、落点不一样，"牌瞬间消失、手牌数 +1"这类看不懂的画面就是从这些缝隙里
漏出来的（获得判定牌【天妒】、五谷选牌、装备替换）。

# 区域词汇（``zone``）

    deck          牌堆
    hand          某人的手牌
    table         桌面上正在展示的牌（动作主体 / 响应牌）
    judge         判定区 / 判定展示位
    equipment     装备槽
    discard       弃牌堆
    public_pool   公共牌池（五谷 / 从别处选牌）
    seat          座位面板（对手的"手牌区"）

# 只画，不算

本模块**不抽牌、不移动数据、不改任何规则状态**。牌早在事件发生时就到了它
该在的地方；这里只是让它"看起来"飞过去。``owned_card_ids`` 是"这一帧由我
独占表现"的查询，静态区域据此不再画第二份——动画与静态之间是**交接**。
"""

import math

# ==================================================
# 区域
# ==================================================

ZONE_DECK = "deck"
ZONE_HAND = "hand"
ZONE_TABLE = "table"
ZONE_JUDGE = "judge"
ZONE_EQUIPMENT = "equipment"
ZONE_DISCARD = "discard"
ZONE_PUBLIC_POOL = "public_pool"
ZONE_SEAT = "seat"

#: 协议 / 报告用的全集（新增区域时同步补这里，报告与自检都读它）。
ZONES = (ZONE_DECK, ZONE_HAND, ZONE_TABLE, ZONE_JUDGE, ZONE_EQUIPMENT,
         ZONE_DISCARD, ZONE_PUBLIC_POOL, ZONE_SEAT)

ZONE_LABELS = {
    ZONE_DECK: "牌堆",
    ZONE_HAND: "手牌",
    ZONE_TABLE: "桌面",
    ZONE_JUDGE: "判定区",
    ZONE_EQUIPMENT: "装备区",
    ZONE_DISCARD: "弃牌堆",
    ZONE_PUBLIC_POOL: "公共牌池",
    ZONE_SEAT: "座位",
}

#: 可见性：这一趟该不该让人看见牌面。
VISIBLE = "face_up"
HIDDEN = "face_down"


def hidden_zones():
    """默认"内容未知"的落点：飞到这些地方的一律画牌背。"""

    return frozenset({ZONE_DECK})


class CardTransfer:
    """一次实体牌移动的**表现**描述（不是规则动作）。

    ``source_zone`` / ``destination_zone`` 决定起飞点与落点；
    ``visibility`` 决定飞行途中画不画牌面；``owner`` 是落点所属角色
    （手牌 / 装备 / 座位需要它来确定具体位置）。
    """

    __slots__ = ("card", "source_zone", "destination_zone", "visibility",
                 "owner", "slot", "label")

    def __init__(self, card, source_zone, destination_zone, *, visibility=VISIBLE,
                 owner=None, slot="", label=""):
        self.card = card
        self.source_zone = str(source_zone or ZONE_TABLE)
        self.destination_zone = str(destination_zone or ZONE_TABLE)
        self.visibility = str(visibility or VISIBLE)
        self.owner = owner
        self.slot = str(slot or "")
        self.label = str(label or "")

    @property
    def face_down(self):
        return self.visibility != VISIBLE

    def describe(self):
        return "%s→%s" % (ZONE_LABELS.get(self.source_zone, self.source_zone),
                          ZONE_LABELS.get(self.destination_zone, self.destination_zone))


# ==================================================
# 一次飞行
# ==================================================


class CardFlight:
    """一张牌从 ``start`` 飞到 ``end``（纯表现）。

    ``owner`` 是这张牌落到的角色：飞行期间该角色的手牌区不画它
    （见 ``CardTransferPresentation.dealing_card_ids``）。
    ``arc`` 是路径高度：牌走一条轻微上抛的曲线，而不是贴地平移——
    这是"丝滑"的主要来源之一，且不依赖任何缓动库。
    """

    def __init__(self, card, start, end, duration, delay=0.0, owner=None, *,
                 face_down=False, arc=None, transfer=None, on_arrive=None,
                 card_size=None):
        self.card = card
        self.owner = owner
        self.start = (float(start[0]), float(start[1]))
        self.end = (float(end[0]), float(end[1]))
        self.duration = max(0.01, float(duration))
        self.delay = max(0.0, float(delay))
        self.elapsed = 0.0
        self.face_down = bool(face_down)
        self.transfer = transfer
        self.on_arrive = on_arrive
        self.card_size = card_size
        self._arrived = False
        span = math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])
        self.arc = (-span * 0.055) if arc is None else float(arc)

    # ---- 生命周期 ----

    @property
    def done(self):
        return self.elapsed >= self.delay + self.duration

    @property
    def arrived(self):
        return self.done

    @property
    def progress(self):
        if self.elapsed <= self.delay:
            return 0.0
        return min(1.0, (self.elapsed - self.delay) / self.duration)

    @property
    def alpha(self):
        """起飞后很快到 255；落位前的最后 15% 淡出交给静态区域接手。

        "落位即淡出"比"突然消失"更像一次交接：手牌区在同一时刻开始画它，
        两者叠起来看不到缝。
        """

        if self.elapsed < self.delay:
            return 0
        ratio = self.progress
        if ratio < 0.12:
            return int(255 * (ratio / 0.12))
        if ratio > 0.86:
            return int(255 * (1.0 - ratio) / 0.14)
        return 255

    @property
    def position(self):
        """当前位置（整数像素）。"""

        ratio = self.progress
        eased = 1.0 - (1.0 - ratio) ** 3
        x = self.start[0] + (self.end[0] - self.start[0]) * eased
        y = self.start[1] + (self.end[1] - self.start[1]) * eased
        if self.arc:
            y += self.arc * math.sin(math.pi * min(1.0, max(0.0, ratio)))
        return int(round(x)), int(round(y))

    @property
    def rect(self):
        if not self.card_size:
            return None
        from pygame import Rect

        x, y = self.position
        return Rect(int(x), int(y), int(self.card_size[0]), int(self.card_size[1])).move(
            -int(self.card_size[0]) // 2, -int(self.card_size[1]) // 2)

    def update(self, dt):
        self.elapsed += max(0.0, float(dt))
        if self.done and not self._arrived:
            self._arrived = True
            if self.on_arrive is not None:
                self.on_arrive()
        return not self.done

    def finish(self):
        """跳过演出：直接落位。"""

        self.elapsed = self.delay + self.duration
        if not self._arrived:
            self._arrived = True
            if self.on_arrive is not None:
                self.on_arrive()

    # ---- 兼容旧 ``DealFlight`` 的名字 ----

    def place(self, position):
        """把起点终点都改成某个位置（落位后的静态位置，给"就位"用）。"""

        self.start = (float(position[0]), float(position[1]))
        self.end = (float(position[0]), float(position[1]))


# ==================================================
# 表现管理器
# ==================================================

#: 同时在飞的牌上限：连续摸牌 / 发牌时不能让整屏都是牌。
MAX_FLIGHTS = 24


class CardTransferPresentation:
    """统一管理"牌在飞"的表现。

    落点坐标由表现层每帧通过 :meth:`sync` 注入（分辨率 / 全屏切换后自动
    跟着变，不会停在旧像素上）；本模块只负责排队、推进、回答"谁在飞"。
    """

    def __init__(self, effects=None):
        self.effects = effects
        self.flights = []
        self._anchors = {}
        self._card_size = None
        self.counter = 0

    # ---- 布局同步 ----

    def sync(self, *, anchors=None, card_size=None):
        """注入这一帧的落点表（``zone → (x, y)``）与卡牌尺寸。"""

        if anchors:
            self._anchors = dict(anchors)
        if card_size:
            self._card_size = tuple(card_size)
        return self

    def anchor(self, zone, default=(0, 0)):
        return self._anchors.get(str(zone), default)

    def has_anchor(self, zone):
        return str(zone) in self._anchors

    # ---- 生产者 ----

    def submit(self, transfer, *, start=None, end=None, duration, delay=0.0,
               on_arrive=None, arc=None):
        """登记一次移动（``start`` / ``end`` 不给时按区域自动取落点）。"""

        if transfer is None or transfer.card is None:
            return None
        begin = self._resolve(start, transfer.source_zone, transfer.owner, transfer.slot)
        finish = self._resolve(end, transfer.destination_zone, transfer.owner,
                               transfer.slot)
        if begin is None or finish is None:
            return None
        flight = CardFlight(
            transfer.card, begin, finish, duration, delay=delay,
            owner=transfer.owner, face_down=transfer.face_down,
            arc=arc, transfer=transfer, on_arrive=on_arrive,
            card_size=self._card_size,
        )
        self.counter += 1
        self.flights.append(flight)
        if len(self.flights) > MAX_FLIGHTS:
            # 丢最老的：牌早就在正确的地方了，飞行只是表现。
            del self.flights[:len(self.flights) - MAX_FLIGHTS]
        return flight

    def queue(self, player, cards, *, source_zone, destination_zone,
              duration, per_card=0.0, visibility=VISIBLE, spread=None,
              start=None, end=None):
        """一模一样的"一串牌"移动（发牌 / 摸牌 / 获得多张）。

        落点横向铺开一点，让人看得出是几张牌；``per_card`` 是逐张出发的间隔。
        """

        cards = [card for card in (cards or ()) if card is not None]
        if player is None or not cards:
            return []
        base = self._resolve(end, destination_zone, player, "")
        if base is None:
            return []
        step = max(1, int(spread if spread is not None else self._default_spread()))
        count = len(cards)
        flights = []
        for index, card in enumerate(cards):
            offset = (index - (count - 1) / 2.0) * step
            transfer = CardTransfer(
                card, source_zone, destination_zone, visibility=visibility,
                owner=player)
            flight = self.submit(
                transfer, start=start, end=(base[0] + offset, base[1]),
                duration=duration, delay=index * per_card)
            if flight is not None:
                flights.append(flight)
        return flights

    def _default_spread(self):
        if self._card_size:
            return max(6, int(self._card_size[0] // 3))
        return 32

    def _resolve(self, explicit, zone, owner, slot):
        """落点解析：``(区域, 角色, 槽位)`` → ``(区域, 角色)`` → ``区域``。

        这三档的 key 形式必须与 :meth:`sync` 写入端**完全一致**——不一致的
        后果不是报错而是**静默不动画**（"装备 / 得到判定牌"这类移动突然没有
        表现，而别处一切正常，很难查）。
        """

        if explicit is not None:
            return tuple(explicit)
        table = self._anchors
        zone = str(zone)
        if owner is not None:
            for key in ((zone, id(owner), str(slot)), (zone, id(owner))):
                entry = table.get(key)
                if entry is not None:
                    return tuple(entry)
        entry = table.get(zone)
        if entry is None:
            return None
        return tuple(entry)

    # ---- 查询 ----

    def dealing_card_ids(self, player=None):
        """还在飞、且没有别人接管表现的那些牌（手牌区暂时不画它们）。"""

        return {id(flight.card) for flight in self.flights
                if not flight.arrived
                and (flight.owner is None if player is None else flight.owner is player)}

    def owned_card_ids(self):
        return {id(flight.card) for flight in self.flights if not flight.arrived}

    def busy(self):
        return any(not flight.arrived for flight in self.flights)

    def describe(self):
        active = [flight for flight in self.flights if not flight.arrived]
        if not active:
            return ""
        head = active[0].transfer
        text = head.describe() if head is not None else "移动"
        if len(active) > 1:
            text += " +%d" % (len(active) - 1)
        return text

    # ---- 推进 ----

    def update(self, dt):
        for flight in list(self.flights):
            if not flight.update(dt):
                self.flights.remove(flight)

    def finish_all(self):
        """跳过演出：所有在飞的牌立刻落位。"""

        for flight in list(self.flights):
            flight.finish()
        self.flights.clear()

    def clear(self):
        self.flights.clear()

    def keep(self, limit):
        if len(self.flights) > limit:
            del self.flights[:len(self.flights) - limit]
