"""Presentation-only effects driven by engine events.

The rules layer never waits for an animation: this module only subscribes to
read-only events and keeps its own timers.

指向性动作（杀 / 决斗 / 顺手牵羊 / 反间…）在这里登记一条 ``TargetArrow``，
由 Renderer 画成"谁对谁做了什么"的箭头。箭头数据全部来自引擎事件
（``CARD_USED`` 的 targets / ``SKILL_TRIGGERED`` 的 targets），这里**不枚举卡名**。
"""

from src.game.atoms_v2 import RecoverHpAtom
from src.game.engine import EventType

from . import layout as layout_module
from . import theme
from .judge import JudgePanel

# ==================================================
# 动画节奏（集中配置）
# ==================================================
#
# 只改这一处就能整体放慢 / 加快。不要在各模块里散落 0.15 / 0.3 这样的数字。
#
#     very_slow  最慢档，用来"一帧不落地看懂一局"
#     slow       默认档（已经明显慢于 Phase 10）
#     normal     接近传统节奏
#     fast       快速过牌

ANIMATION_SPEED_PRESETS = {
    "very_slow": 1.75,
    "slow": 1.30,
    "normal": 1.0,
    "fast": 0.70,
}

# 表现层默认档位。真人可用 fx.set_speed_preset() 切换（键盘 1/2/3 已接）。
ACTIVE_SPEED_PRESET = "slow"
ANIMATION_SPEED = ANIMATION_SPEED_PRESETS[ACTIVE_SPEED_PRESET]


class FXTiming:
    """关键动作的展示时长（秒），已按当前档位缩放。

    命名按**语义阶段**来，而不是按"某个控件"：
    出牌 → 亮牌 → 箭头 → 响应 → 结算，每一步各自有时长，
    这样调整顺序或单独放慢某一步都不需要在各模块里找数字。
    """

    def __init__(self, scale=ANIMATION_SPEED):
        self.scale = float(scale)

    def _t(self, base):
        return base * self.scale

    # ---- 出牌与指向 ----

    @property
    def card_reveal(self):
        """中央亮出这张牌。"""

        return self._t(0.55)

    @property
    def card_reveal_hold(self):
        """亮牌之后、箭头出现之前的停顿（先看清是什么牌）。"""

        return self._t(0.45)

    @property
    def arrow_enter(self):
        """箭头从起点沿路径"画"到目标的时间（玩家反馈：太快看不清）。"""

        return self._t(0.60)

    @property
    def arrow_hold(self):
        """箭头稳定停留时间。

        需要响应的牌由语义释放（响应结束才消失），这个值只作为兜底上限；
        不需要响应的牌按这个时长自然淡出。
        """

        return self._t(1.15)

    @property
    def arrow_max_hold(self):
        """兜底：无论发生什么，箭头最多留这么久，避免异常时永远挂着。"""

        return self._t(9.0)

    # ---- 响应 ----

    @property
    def response_prepare(self):
        """进入响应前的停顿（"B 即将响应"）。"""

        return self._t(0.50)

    @property
    def response_card_show(self):
        """响应牌（闪 / 杀 / 无懈）的展示。"""

        return self._t(0.85)

    @property
    def response_hold(self):
        """响应结果停留。"""

        return self._t(0.45)

    # ---- 结算反馈 ----

    @property
    def damage_show(self):
        return self._t(0.85)

    @property
    def heal_show(self):
        return self._t(0.85)

    @property
    def judge_reveal(self):
        """判定牌翻开。"""

        return self._t(0.70)

    @property
    def judge_hold(self):
        """判定结果停留（改判窗口用 judge_swap）。"""

        return self._t(0.85)

    @property
    def judge_swap(self):
        """改判前后各自停留。"""

        return self._t(0.50)

    # ---- 判定展示面板（Phase 10.5）----
    #
    # 一个判定走：OPEN → SOURCE_HOLD → DRAW_ANIMATION → REVEALED_HOLD
    # →（可多次 REPLACEMENT）→ FINAL_RESULT → OUTCOME_HOLD → FADE_OUT。
    # 全部走 dt，没有任何 sleep；最慢档必须能一眼看清每一步。

    @property
    def judge_open(self):
        """面板弹出。"""

        return self._t(0.32)

    @property
    def judge_source_hold(self):
        """亮出判定来源（哪张牌 / 哪个技能触发的）。"""

        return self._t(0.80)

    @property
    def judge_draw(self):
        """判定牌从牌堆滑入判定区的表现时长。"""

        return self._t(0.65)

    @property
    def judge_revealed_hold(self):
        """判定牌停留；改判窗口开着时面板会一直保持。"""

        return self._t(0.80)

    @property
    def judge_replacement(self):
        """改判发生后的换牌表现。"""

        return self._t(0.55)

    @property
    def judge_final(self):
        """最终判定牌锁定。"""

        return self._t(0.30)

    @property
    def judge_outcome_hold(self):
        """结果语义（跳过出牌 / 受到伤害 / 反击成功）停留。"""

        return self._t(1.55)

    @property
    def judge_fade_out(self):
        """面板淡出。"""

        return self._t(0.40)

    @property
    def between_actions(self):
        """两次动作之间的基础间隔。"""

        return self._t(0.35)

    # ---- 开局发牌 ----

    @property
    def initial_deal_card(self):
        """开局每张牌飞到手牌的时间。"""

        return self._t(0.28)

    # ---- 摸牌 / 拿到牌 ----

    @property
    def draw_card_flight(self):
        """摸到的一张牌从牌堆飞到手上 / 座位上的时长。"""

        return self._t(0.26)

    @property
    def draw_card_step(self):
        """同时摸多张时，逐张出发的间隔。"""

        return self._t(0.09)

    # ---- 提示与浮字 ----

    @property
    def banner_hold(self):
        """动作横幅（"AI 1 对你使用【杀】"）停留。"""

        return self._t(1.30)

    @property
    def skill_float(self):
        return self._t(1.00)

    @property
    def flash(self):
        return self._t(0.62)

    @property
    def shake(self):
        return self._t(0.36)

    @property
    def float(self):
        """通用浮字（阵亡 / 横置 / 濒死）。"""

        return self._t(1.10)

    @property
    def turn_banner(self):
        return self._t(1.10)

    @property
    def pulse(self):
        return self._t(0.55)

    # ---- 兼容旧命名（其他模块与既有测试仍在用）----

    @property
    def card_play(self):
        return self.card_reveal + self.card_reveal_hold

    @property
    def target_arrow(self):
        return self.arrow_enter + self.arrow_hold

    @property
    def target_step(self):
        return self.between_actions

    @property
    def damage_float(self):
        return self.damage_show

    @property
    def heal_float(self):
        return self.heal_show

    @property
    def judge_display(self):
        return self.judge_reveal + self.judge_hold


_timing = FXTiming()


def timing():
    """当前生效的节奏配置。"""

    return _timing


def set_speed_preset(name):
    """切换节奏档位（slow / normal / fast）。"""

    global ACTIVE_SPEED_PRESET, ANIMATION_SPEED, _timing
    if name not in ANIMATION_SPEED_PRESETS:
        return ANIMATION_SPEED
    ACTIVE_SPEED_PRESET = name
    ANIMATION_SPEED = ANIMATION_SPEED_PRESETS[name]
    _timing = FXTiming(ANIMATION_SPEED)
    return ANIMATION_SPEED


# 兼容旧常量名（其他模块与测试仍会引用）。
FLASH_TIME = _timing.flash
SHAKE_TIME = _timing.shake
FLOAT_TIME = _timing.float
JUDGE_TIME = _timing.judge_display
TURN_BANNER_TIME = _timing.turn_banner


class FloatText:
    def __init__(self, text, position, color, life=None):
        self.text = text
        self.position = position
        self.color = color
        self.life = timing().float if life is None else life
        self.max_life = self.life

    @property
    def alpha(self):
        return int(255 * max(0.0, self.life / self.max_life))

    @property
    def offset(self):
        return int(34 * (1 - self.life / self.max_life))


class TargetArrow:
    """一条"某人 → 某人"的指向箭头（纯表现，不参与任何规则判定）。

    ``delay`` 让多目标牌的箭头依次出现，而不是七根一起铺满屏幕。

    ``hold=True`` 时箭头**不会**到时自动消失，而是等语义释放：
    需要响应的牌会一直留到响应结束（``PENDING_RESOLVED``），
    这样玩家一定能看到"A 指向 B 之后 B 才响应"。
    """

    def __init__(self, source, target, color, life, *, delay=0.0, label="",
                 hold=False, key=None):
        self.source = source
        self.target = target
        self.color = tuple(color)
        self.life = life
        self.max_life = life
        self.delay = delay
        self.label = label
        self.hold = bool(hold)
        self.released = not self.hold
        self.key = key          # 用于匹配释放条件（通常是这张牌）
        self.age = 0.0

    @property
    def waiting(self):
        return self.delay > 0

    def release(self):
        """语义释放：允许它开始淡出。"""

        self.released = True

    def update(self, dt):
        """推进一帧；返回 False 表示可以回收。"""

        if self.delay > 0:
            self.delay -= dt
            if self.delay > 0:
                return True
            # 等待结束的那一帧，多出来的时间直接接到生命周期上，
            # 否则会白掉一帧（表现为箭头"卡"一下才出现）。
            dt = -self.delay
            self.delay = 0.0

        self.age += dt
        if self.hold and not self.released:
            # 等语义释放。兜底上限只在异常时生效，避免箭头永远挂着。
            if self.age < timing().arrow_max_hold:
                return True
        self.life -= dt
        return self.life > 0

    @property
    def alpha(self):
        if self.delay > 0 or self.max_life <= 0:
            return 0
        # 进入阶段按"已经活了多久"算，而不是按 life 消耗量：
        # hold 期间 life 不减少，用 life 判断会导致 alpha 永远是 0。
        enter = max(0.01, timing().arrow_enter)
        if self.age < enter:
            return int(255 * self.age / enter)
        remaining = max(0.0, self.life)
        fade = max(0.01, min(0.4, self.max_life * 0.45))
        if remaining < fade:
            return int(255 * remaining / fade)
        return 255

    @property
    def progress(self):
        """箭头"画"到哪了（0→1）：入场阶段沿路径推进，之后固定为 1。

        它让箭头是从**起点长到终点**的，方向与指向一眼可见——以前只是整体
        淡入，配上偏快的时长，看起来就是"一闪而过"。
        """

        if self.delay > 0:
            return 0.0
        if self.age >= self.grow_time:
            return 1.0
        if self.grow_time <= 0:
            return 1.0
        return max(0.0, min(1.0, self.age / self.grow_time))

    @property
    def grow_time(self):
        return max(0.01, timing().arrow_enter)

    def endpoints(self, source_rect, target_rect):
        """箭头两端落在两个面板的边缘（不遮住面板内的文字）。"""

        sx, sy = source_rect.center
        tx, ty = target_rect.center
        dx, dy = tx - sx, ty - sy
        if dx == 0 and dy == 0:
            return (sx, sy), (tx, ty)
        start = _rect_edge_point(source_rect, dx, dy)
        end = _rect_edge_point(target_rect, -dx, -dy)
        return start, end


def _rect_edge_point(rect, dx, dy):
    """从矩形中心沿 (dx, dy) 射线与边界的交点。"""

    cx, cy = rect.center
    scale_x = (rect.width / 2.0) / abs(dx) if dx else float("inf")
    scale_y = (rect.height / 2.0) / abs(dy) if dy else float("inf")
    scale = min(scale_x, scale_y)
    return (cx + dx * scale, cy + dy * scale)


def _ease_out(t):
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


class DealFlight:
    """一张牌从起点飞向目标位置（开局发牌 / 摸牌 / 拿牌）。

    纯表现——牌早就在手牌里了，这里只是让它"看起来"飞过来，
    不抽牌、不重复、不改变任何实体数据。``owner`` 是这张牌落到的角色，
    飞行期间该角色的手牌区不画它（见 ``dealing_card_ids``）。
    """

    def __init__(self, card, start, end, duration, delay=0.0, owner=None):
        self.card = card
        self.owner = owner
        self.start = tuple(start)
        self.end = tuple(end)
        self.duration = max(0.01, float(duration))
        self.delay = max(0.0, float(delay))
        self.elapsed = 0.0

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
    def position(self):
        eased = _ease_out(self.progress)
        x = self.start[0] + (self.end[0] - self.start[0]) * eased
        y = self.start[1] + (self.end[1] - self.start[1]) * eased
        return int(x), int(y)

    def update(self, dt):
        self.elapsed += dt
        return not self.done


def arrow_color_for_card(card):
    """箭头颜色只按牌的属性来（红 / 黑 / 装备），不认识具体牌名。"""

    if getattr(card, "category", "") == "equipment":
        return (250, 214, 130)
    if getattr(card, "card_color", None) == "red":
        return (255, 152, 118)
    if getattr(card, "card_color", None) == "black":
        return (132, 198, 255)
    return (200, 220, 245)


class Effects:
    """Collects engine events into short visual reactions."""

    def __init__(self):
        self.game = None
        self._tokens = []
        self._seat_flash = {}
        self._seat_shake = {}
        self.floats = []
        self.arrows = []
        # 统一判定展示面板：延时锦囊 / 装备技能 / 武将技能共用。
        self.judge_panel = JudgePanel()
        #: 判定展示期间压住动作队列的开关（绑定方法只取一次，便于身份比较）。
        self._action_gate = self._holds_actions
        self.turn_banner = None
        self.turn_timer = 0.0
        self.last_draw_pulse = 0.0
        self.last_discard_pulse = 0.0
        self.last_play_pulse = 0.0
        self.action_banner = None
        self.action_timer = 0.0
        self.deal_flights = []
        self._deal_owner = None
        # 待表现的"牌到手"事件：引擎已经完成真实移动，这里下一帧再决定
        # 要不要播飞行动画（那时才知道有没有别的系统接管了这张牌）。
        self._pending_arrivals = []

    # ---- 判定 → 动作队列的门控 ----

    def _holds_actions(self):
        return self.judge_panel.holds_actions

    def sync_action_gate(self):
        """把"判定展示期间别开始下一个动作"装到当前这份动作队列上。

        单机/房主是 ``game.actions``，联网客户端是只读视图的
        ``view.actions``（它自己的表现队列）——同一个函数覆盖三种视角。
        没有 UI 的批量演算不会调用到这里，行为完全不变。
        """

        actions = getattr(self.game, "actions", None)
        if actions is None:
            return
        if getattr(actions, "hold", None) is not self._action_gate:
            actions.hold = self._action_gate

    # ---- 指向箭头 ----

    def add_arrow(self, source, targets, *, color=None, label="", stagger=None,
                  hold=False, key=None, base_delay=0.0):
        """登记一次指向性动作的箭头（source → 每个 target）。

        这是公开入口：引擎事件与 UI 都用它，不按卡名分支。
        没有目标（桃 / 无中生有 / 装备自己）时什么也不做——不画假箭头。

        ``hold=True`` 时箭头等语义释放（响应结束）才淡出；
        ``base_delay`` 用来实现"先看清牌、再出现箭头"的顺序。
        """

        if source is None:
            return 0
        targets = [item for item in (targets or ()) if item is not None and item is not source]
        if not targets:
            return 0
        color = theme.TARGET_BLUE if color is None else color
        life = timing().arrow_hold + timing().arrow_enter
        step = timing().between_actions if stagger is None else stagger
        for index, target in enumerate(targets):
            self.arrows.append(TargetArrow(
                source, target, color, life,
                delay=base_delay + index * step, label=label,
                hold=hold, key=key))
        return len(targets)

    def release_arrows(self, *, key=None, source=None, target=None, release_all=False):
        """语义释放：条件匹配的箭头开始淡出。返回释放了几条。"""

        released = 0
        for arrow in self.arrows:
            if not arrow.hold or arrow.released:
                continue
            if not release_all:
                if key is not None and arrow.key is not key:
                    continue
                if source is not None and arrow.source is not source:
                    continue
                if target is not None and arrow.target is not target:
                    continue
            arrow.release()
            released += 1
        return released

    def clear_arrows(self):
        self.arrows = []

    # ---- 开局发牌 ----

    def queue_deal(self, player, cards, *, start, end, per_card=None, duration=None):
        """为**已经发好**的牌登记逐张落位动画（不抽牌、不改变数据）。

        真实发牌顺序是"每名玩家连续 N 张"，这里按传入的 cards 顺序播放即可。
        """

        cards = [card for card in (cards or ()) if card is not None]
        if player is None or not cards:
            return 0
        per = timing().initial_deal_card if per_card is None else per_card
        dur = per * 0.85 if duration is None else duration
        self.deal_flights = self._flights_for(player, cards, start, end, per, dur)
        self._deal_owner = player
        return len(self.deal_flights)

    def _flights_for(self, player, cards, start, end, per_card, duration):
        """一串逐张错开的飞行：同一个落点横向铺开一点，看得出是几张牌。"""

        width = self._hand_card_width()
        spread = max(6, width // 3)
        count = len(cards)
        flights = []
        for index, card in enumerate(cards):
            offset = (index - (count - 1) / 2.0) * spread
            target = (int(end[0] + offset), int(end[1]))
            flights.append(DealFlight(
                card, start, target, duration,
                delay=index * per_card, owner=player))
        return flights

    def queue_draw(self, player, cards, *, start, end, per_card=None, duration=None):
        """摸牌 / 拿牌：把**已经到手**的牌登记成"从来源飞向该角色"的表现。

        与 ``queue_deal`` 的区别是**追加**而不是替换（一局里会摸很多次），
        并且逐张错开落点，让人看清摸了几张。牌早就在手牌里了，这里只是
        表现——不抽牌、不改变任何规则数据。
        """

        cards = [card for card in (cards or ()) if card is not None]
        if player is None or not cards:
            return 0
        step = timing().draw_card_step if per_card is None else per_card
        dur = timing().draw_card_flight if duration is None else duration
        flights = self._flights_for(player, cards, start, end, step, dur)
        self.deal_flights.extend(flights)
        # 同时最多留 12 张在飞，避免连续摸牌堆成一片。
        limit = 12
        if len(self.deal_flights) > limit:
            self.deal_flights = self.deal_flights[-limit:]
        return len(flights)

    def _hand_card_width(self):
        layout = getattr(self, "_layout", None)
        metrics = getattr(layout, "metrics", None)
        if metrics is not None:
            return metrics.hand_card_size()[0]
        return 106

    def dealing_card_ids(self, player):
        """还在飞向该角色的牌：手牌区暂时不画它们，避免"牌同时出现在两处"。"""

        return {id(flight.card) for flight in self.deal_flights
                if not flight.arrived and (flight.owner is None or flight.owner is player)}

    def owned_card_ids(self):
        """这一帧由某个表现系统**独占表现**的牌（稳定身份 ``id(card)``）。

        这是表现层唯一的「视觉所有权」查询，也是"同一张牌不能同时被两个
        系统画"的落实点。出现在这里的牌来自三类 owner：

        * 移动动画（正在飞的手牌 / 桌卡 / 响应牌 / 判定牌）；
        * 开局发牌仍在飞行中的牌；
        * 判定面板正在展示的判定牌与来源牌。

        静态区域（手牌 / 桌面主体卡 / 公共池 / 弃牌堆顶）一律不再画它们——
        动画与静态之间是**交接**，不是两份副本并存。身份只认对象引用，
        不比较坐标。
        """

        ids = set()
        queue = getattr(self.game, "actions", None)
        if queue is not None:
            ids |= queue.animating_card_ids()
        for flight in self.deal_flights:
            if not flight.arrived:
                ids.add(id(flight.card))
        ids |= self.judge_panel.owned_card_ids(self.game)
        return ids

    # 兼容旧名：仍表示"正在被移动动画表现的牌"。
    def animating_card_ids(self):
        return self.owned_card_ids()

    def deal_in_progress(self):
        return any(not flight.arrived for flight in self.deal_flights)

    # ==================================================
    # 订阅
    # ==================================================

    def attach(self, game):
        if self.game is game:
            return
        self.detach()
        # 只读视图（联网客户端）没有引擎事件总线：表现完全由房主下发的
        # 表现事件驱动（见 ui.client_fx），这里不订阅任何东西。
        dispatcher = getattr(getattr(game, "context", None), "events", None)
        self.game = game
        if dispatcher is None:
            self._tokens = []
            return
        subscriptions = (
            (EventType.DAMAGE_APPLIED, self._on_damage),
            (EventType.ATOM_AFTER, self._on_atom),
            (EventType.JUDGE_REVEALED, self._on_judge),
            (EventType.JUDGE_REPLACED, self._on_judge_replaced),
            (EventType.JUDGE_RESULT, self._on_judge_result),
            (EventType.TURN_START, self._on_turn_start),
            (EventType.DEATH, self._on_death),
            (EventType.CHAIN_STATE_CHANGED, self._on_chain),
            (EventType.DYING_ENTERED, self._on_dying),
            (EventType.CARD_USED, self._on_card_used),
            (EventType.SKILL_TRIGGERED, self._on_skill),
            (EventType.CARD_USE_FINISHED, self._on_card_use_finished),
            (EventType.PENDING_CREATED, self._on_pending_created),
            (EventType.PENDING_RESOLVED, self._on_pending_resolved),
        )
        for event_name, handler in subscriptions:
            self._tokens.append(
                dispatcher.subscribe(event_name, handler, owner=self)
            )

    def detach(self):
        if self.game is not None:
            for token in self._tokens:
                self.game.context.events.unsubscribe(token)
        self._tokens = []

    def bind_view(self, view):
        """联网客户端：把只读视图作为"动画落点参照"接上。

        与 ``attach(game)`` 的区别：这里**不订阅任何引擎事件**——客户端没有
        引擎，表现完全由房主下发的表现事件驱动（见 ``ui.client_fx``）。
        """

        self.detach()
        self._tokens = []
        self.game = view
        return self

    def reset(self):
        self._seat_flash.clear()
        self._seat_shake.clear()
        self.floats.clear()
        self.arrows = []
        self.judge_panel.cancel()
        self.turn_banner = None
        self.turn_timer = 0.0
        self.action_banner = None
        self.action_timer = 0.0
        self.deal_flights = []
        self._deal_owner = None
        self._pending_arrivals = []

    # ==================================================
    # 公开表现入口
    #
    # 引擎事件回调与联网客户端**共用**这些方法：房主侧由 event 回调调用，
    # 客户端侧由网络表现事件适配层调用。表现逻辑只有一份实现，客户端不会
    # 长出第二套动画代码。
    # ==================================================

    def show_damage(self, player, amount):
        """受到伤害：闪红 + 抖动 + 飘出 -N。"""

        if player is None or amount <= 0:
            return
        self._seat_flash[player] = (timing().flash, theme.DANGER)
        self._seat_shake[player] = timing().shake
        x, y = self._anchor(player)
        self.floats.append(FloatText("-" + str(int(amount)), (x, y), (238, 122, 108)))

    def show_recover(self, player, amount):
        if player is None or amount <= 0:
            return
        self._seat_flash[player] = (timing().flash, theme.HEAL)
        x, y = self._anchor(player)
        self.floats.append(FloatText("+" + str(int(amount)), (x, y), (146, 226, 160)))

    def show_lose_hp(self, player, amount):
        """失去体力（不是伤害）：用偏黄的飘字区分于伤害。"""

        if player is None or amount <= 0:
            return
        x, y = self._anchor(player)
        self.floats.append(FloatText("-" + str(int(amount)), (x, y), (240, 196, 120)))

    def show_dying(self, player):
        if player is None:
            return
        self._seat_flash[player] = (timing().flash, theme.DANGER)
        x, y = self._anchor(player)
        self.floats.append(FloatText("濒死", (x, y), (240, 170, 120)))

    def show_death(self, player):
        if player is None:
            return
        x, y = self._anchor(player)
        self.floats.append(FloatText("阵亡", (x, y), (222, 142, 132)))

    def show_chain(self, player, chained):
        if player is None:
            return
        x, y = self._anchor(player)
        color = theme.CHAIN if chained else (168, 220, 180)
        self.floats.append(FloatText("横置" if chained else "重置", (x, y), color))

    def show_skill(self, player, skill_name, targets=()):
        """技能发动：技能名飘字（+ 有目标时补箭头）。"""

        if player is None or not skill_name:
            return
        x, y = self._anchor(player)
        self.floats.append(FloatText("【" + str(skill_name) + "】", (x, y),
                                     (250, 220, 150), life=timing().skill_float))
        targets = [item for item in (targets or ()) if item is not None]
        if targets:
            self.add_arrow(player, targets, color=(250, 214, 130),
                           label=str(skill_name))

    def show_turn_start(self, player):
        if player is None:
            return
        self.turn_banner = {"text": getattr(player, "name", "") + " 的回合"}
        self.turn_timer = timing().turn_banner
        self._seat_flash[player] = (timing().flash, theme.GOLD)

    def show_card_used(self, actor, targets, card, *, sequential=False):
        """一次出牌：中央横幅 + 指向箭头（顺序：先亮牌，再出现箭头）。"""

        self.last_play_pulse = timing().pulse
        targets = [item for item in (targets or ()) if item is not None]
        if card is None or not targets:
            # 无目标牌（桃 / 无中生有 / 装备自己）不画箭头。
            return 0
        self.publish_action(actor, targets, card)
        if sequential:
            # 逐目标响应型锦囊（南蛮 / 万箭）：一次只结算一个目标，
            # 箭头由每个目标的响应请求单独点亮（见 focus_arrow）。
            return 0
        return self.add_arrow(
            actor, targets,
            color=arrow_color_for_card(card),
            hold=True, key=card,
            base_delay=timing().card_reveal_hold,
        )

    def focus_arrow(self, source, target, card):
        """逐目标结算：把箭头切到当前正在结算的那一个目标。"""

        if card is None or target is None:
            return 0
        self.release_arrows(key=card)
        return self.add_arrow(source, [target],
                              color=arrow_color_for_card(card), hold=True, key=card)

    def note_draw(self, player, cards, origin=None):
        """有牌进了某人的手牌：下一帧按本地布局播飞行动画。"""

        self.last_draw_pulse = timing().pulse
        self._note_arrivals(player, cards, origin)

    def note_move(self, player, card, origin=None):
        """一张牌移动到某人的手牌（顺手 / 拿牌一类）。"""

        if player is not None and card is not None:
            self._note_arrivals(player, (card,), origin)

    def note_discard(self):
        self.last_discard_pulse = timing().pulse

    # ---- 判定（面板状态机复用同一个 JudgePanel） ----

    def judge_begin(self, result):
        if result is None:
            return self.judge_panel
        return self.judge_panel.begin(result)

    def judge_replace(self, payload, game=None):
        return self.judge_panel.note_replacement(payload, game)

    def judge_finish(self, result):
        if result is None:
            return self.judge_panel
        return self.judge_panel.finish(result)

    def finish_card_use(self, card):
        if card is not None:
            self.release_arrows(key=card)

    def finish_response(self, card):
        if card is not None:
            self.release_arrows(key=card)

    # ==================================================
    # 事件处理（房主侧：直接订阅引擎事件）
    # ==================================================

    def _anchor(self, player):
        rect = self._seat_rect(player)
        if rect is not None:
            return rect.centerx, rect.y + 20
        return 500, 300

    def _seat_rect(self, player):
        if self.game is None or player is None:
            return None
        layout = getattr(self, "_layout", None)
        if layout is None:
            return None
        return layout.seat_rect(player)

    def set_layout(self, layout):
        """Renderer hands over the frame layout so effects can anchor to seats."""

        self._layout = layout

    def _on_damage(self, _context, event):
        damage = event.payload.get("damage")
        amount = event.payload.get("amount", 0)
        target = getattr(damage, "target", None) or event.target
        self.show_damage(target, int(amount or 0))

    def _on_atom(self, _context, event):
        atom = event.payload.get("atom")
        result = event.payload.get("result")
        if isinstance(atom, RecoverHpAtom):
            amount = getattr(result, "data", {}).get("amount", 0)
            self.show_recover(atom.target, int(amount or 0))
        elif atom.__class__.__name__ == "DrawCardsAtom":
            data = getattr(result, "data", None) or {}
            self.note_draw(
                data.get("target", getattr(atom, "target", None)),
                data.get("cards") or (),
                self.game.deck.draw_pile if self.game is not None else None,
            )
        elif atom.__class__.__name__ == "MoveCardAtom":
            destination = getattr(atom, "destination", None)
            if self.game is not None and destination is self.game.deck.discard_pile:
                self.note_discard()
            # 牌进了某个角色的手牌：只要没有别的系统接管，就播一段飞行动画。
            owner = self._hand_owner(destination)
            if owner is not None:
                self.note_move(owner, getattr(atom, "card", None),
                               getattr(atom, "source", None))

    def _hand_owner(self, zone):
        """这个区域是不是某个角色的手牌？是则返回该角色。"""

        if zone is None or self.game is None:
            return None
        for player in self.game.players:
            if player.hand is zone:
                return player
        return None

    # ---- 牌到手的飞行动画 ----

    def _note_arrivals(self, player, cards, source):
        """记下"这些牌刚进了这个人的手牌"，具体要不要播动画留到下一帧判断。"""

        if player is None:
            return
        for card in cards:
            if card is not None:
                self._pending_arrivals.append((card, player, source))
        if len(self._pending_arrivals) > 16:
            del self._pending_arrivals[:-16]

    def _flush_arrivals(self):
        """把待表现的"牌到手"变成真正的飞行动画。

        规则早在事件发生时就完成了，这里只决定视觉：已经被移动动画（顺手
        牵羊 / 仁德一类）或既有飞行接管的牌直接跳过，避免同一张牌两段动画。
        """

        pending, self._pending_arrivals = self._pending_arrivals, []
        if not pending or self.game is None:
            return 0
        if not getattr(self.game, "ui_rects", None):
            # 无 UI（无头测试）：不做任何动画。
            return 0

        busy = set()
        queue = getattr(self.game, "actions", None)
        if queue is not None:
            busy |= queue.animating_card_ids()
        busy |= {id(flight.card) for flight in self.deal_flights if not flight.arrived}

        groups = {}
        for card, player, source in pending:
            if id(card) in busy:
                continue
            groups.setdefault((player, self._origin_key(source)), []).append(card)

        queued = 0
        for (player, origin_key), cards in groups.items():
            queued += self.queue_draw(
                player, cards,
                start=self._origin_point(origin_key),
                end=self.draw_target(player),
            )
        return queued

    @staticmethod
    def _origin_key(source):
        return id(source) if source is not None else None

    def _origin_point(self, key):
        """一张牌飞行的起点：牌堆 / 公共牌池 / 默认中央。"""

        game = self.game
        if game is not None and key is not None:
            if key == id(getattr(game.deck, "draw_pile", None)):
                return self._design_center(layout_module.DRAW_PILE_RECT)
            if key == id(getattr(game, "public_card_pool", None)):
                return self._design_center(layout_module.CENTRAL_RECT)
        return self._design_center(layout_module.TABLE_CARD_RECT)

    def _design_center(self, design_rect):
        layout = getattr(self, "_layout", None)
        metrics = getattr(layout, "metrics", None)
        if metrics is None:
            return (design_rect.centerx, design_rect.centery)
        return metrics.to_screen(design_rect).center

    def draw_target(self, player):
        """摸牌动画的落点：真人飞到手牌区，其他角色飞向自己的座位。"""

        layout = getattr(self, "_layout", None)
        metrics = getattr(layout, "metrics", None)
        if layout is None or metrics is None:
            return self._design_center(layout_module.TABLE_CARD_RECT)
        if self.game is not None and player is self.game.player:
            hand = metrics.hand_area
            return (hand.centerx, hand.y)
        rect = layout.any_seat_rect(player)
        if rect is not None:
            return (rect.centerx, rect.bottom - metrics.px(18))
        return self._design_center(layout_module.TABLE_CARD_RECT)

    def _on_judge(self, _context, event):
        result = event.payload.get("result")
        if result is None:
            return
        # 统一判定展示：所有判定都走同一个面板，来源 / 规则 / 结果语义
        # 全部来自规则层的判定声明（judge_presentation）。
        self.judge_panel.begin(result)

    def _on_judge_replaced(self, _context, event):
        """鬼才一类改判：面板保留，判定牌换成新的。"""

        self.judge_panel.note_replacement(event.payload, self.game)

    def _on_judge_result(self, _context, event):
        """最终判定牌锁定：面板开始展示结果语义。"""

        self.judge_panel.finish(event.payload.get("result"))

    def _on_turn_start(self, _context, event):
        self.show_turn_start(event.source)

    def _on_death(self, _context, event):
        self.show_death(event.target)

    def _on_chain(self, _context, event):
        self.show_chain(event.target, event.payload.get("chained"))

    def _on_dying(self, _context, event):
        self.show_dying(event.target)

    def _on_card_used(self, _context, event):
        self.show_card_used(
            event.source, event.payload.get("targets"),
            event.payload.get("card"),
            sequential=bool(event.payload.get("sequential_targets")),
        )

    def _on_pending_created(self, _context, event):
        """逐目标响应请求：把指向箭头切到当前正在结算的那一个目标。"""

        request = event.payload.get("request")
        context_data = getattr(request, "context", None) or {}
        if not context_data.get("sequential_targets"):
            return
        self.focus_arrow(getattr(request, "source", None),
                         getattr(request, "target", None),
                         context_data.get("card"))

    def _on_card_use_finished(self, _context, event):
        self.finish_card_use(event.payload.get("card"))

    def _on_pending_resolved(self, _context, event):
        """响应结束：这次响应涉及的牌，其箭头可以消失了。"""

        resolution = event.payload.get("resolution")
        request = getattr(resolution, "request", None)
        context_data = getattr(request, "context", None) or {}
        self.finish_response(context_data.get("card"))

    def publish_action(self, source, targets, card):
        """动作横幅：谁对谁使用了什么（用真实玩家名与真实牌名）。"""

        if source is None or card is None:
            return
        names = "、".join(getattr(item, "name", "?") for item in list(targets)[:3])
        if len(targets) > 3:
            names += " 等 %d 人" % len(targets)
        self.action_banner = {
            "text": "%s 对 %s 使用【%s】" % (source.name, names, card.display_name),
        }
        self.action_timer = timing().banner_hold

    def _on_skill(self, _context, event):
        self.show_skill(event.source, event.payload.get("skill_name"),
                        event.payload.get("targets"))

    # ==================================================
    # 每帧推进
    # ==================================================

    def update(self, dt):
        for player, (remaining, _color) in list(self._seat_flash.items()):
            remaining -= dt
            if remaining <= 0:
                del self._seat_flash[player]
            else:
                self._seat_flash[player] = (remaining, _color)
        for player, remaining in list(self._seat_shake.items()):
            remaining -= dt
            if remaining <= 0:
                del self._seat_shake[player]
            else:
                self._seat_shake[player] = remaining

        for item in list(self.floats):
            item.life -= dt
            if item.life <= 0:
                self.floats.remove(item)

        for arrow in list(self.arrows):
            if not arrow.update(dt):
                self.arrows.remove(arrow)

        for flight in list(self.deal_flights):
            if not flight.update(dt):
                self.deal_flights.remove(flight)

        # 上一帧记录的"牌到手"在这里变成飞行动画：此时别的系统（顺手牵羊 /
        # 仁德的亮牌动画）已经把该接管的牌登记好了，能准确判断谁该播。
        self._flush_arrivals()

        self.judge_panel.update(dt, self.game)
        # 判定面板还在演 → 后面的行动先别开始（本轮玩家反馈：判定之后的行动
        # 必须等判定结束）。没有动作队列（无头演算）时这一步什么都不做。
        self.sync_action_gate()
        if self.turn_timer > 0:
            self.turn_timer -= dt
            if self.turn_timer <= 0:
                self.turn_banner = None
        if self.action_timer > 0:
            self.action_timer -= dt
            if self.action_timer <= 0:
                self.action_banner = None

        for name in ("last_draw_pulse", "last_discard_pulse", "last_play_pulse"):
            value = getattr(self, name, 0.0)
            if value > 0:
                setattr(self, name, max(0.0, value - dt))

    # ==================================================
    # 查询
    # ==================================================

    def seat_flash(self, player):
        entry = self._seat_flash.get(player)
        if entry is None:
            return 0.0, theme.DANGER
        remaining, color = entry
        return max(0.0, remaining / timing().flash), color

    def seat_shake(self, player):
        remaining = self._seat_shake.get(player, 0.0)
        if remaining <= 0:
            return 0
        ratio = remaining / timing().shake
        import math

        return int(math.sin(remaining * 46) * 5 * ratio)

    def judge_display(self):
        """兼容接口：把判定展示状态整理成旧的横幅结构。

        数据来自统一的 ``judge_panel``，这里只是换个形状给老调用点，
        判定文本不再有第二份来源。
        """

        panel = self.judge_panel
        if not panel.active:
            return None
        card = panel.shown_card
        return {
            "card": card,
            "owner_name": getattr(panel.owner, "name", ""),
            "reason_text": getattr(panel.spec, "display_name", "") or "",
            "result_text": "" if card is None else card.suit_name + str(card.rank),
            "alpha": panel.alpha,
        }

    def action_display(self):
        """动作横幅：谁对谁使用了什么（与卡牌动画同步停留）。"""

        if self.action_banner is None or self.action_timer <= 0:
            return None
        info = dict(self.action_banner)
        ratio = self.action_timer / max(0.001, timing().banner_hold)
        info["alpha"] = int(255 * min(1.0, ratio * 2.2))
        return info

    def turn_display(self):
        if self.turn_banner is None or self.turn_timer <= 0:
            return None
        info = dict(self.turn_banner)
        ratio = self.turn_timer / timing().turn_banner
        info["alpha"] = int(255 * min(1.0, ratio * 2.2))
        return info
