"""客户端表现层：把房主发来的表现事件喂给**既有** FX / 动画（Phase 11.3 §22）。

这一层是纯表现：它只决定"播什么动画、飘什么字、面板翻什么牌"，绝不改变
任何状态，也不推导任何规则。所有素材都来自 ``GAME_EVENT``，坐标由**本机**
的 ``LayoutMetrics`` 现算（房主从不发送屏幕坐标）。

复用的既有资产（一行重写都没有）：

* ``Effects`` 的箭头 / 横幅 / 飘字 / 席位闪烁 / 判定面板
* ``ActionQueue`` + ``MoveCardAction``：卡牌移动动画（落位是命名区域，
  和房主侧同一套语义）
* ``JudgePanel``：判定展示状态机
* ``judge_presentation``：判定来源与结果语义的静态声明表

Phase 11.5 修掉的三件事（都是"区域 ≠ 单张牌"引出的）：

1. **飞牌尺寸**：区域只提供锚点，飞出去的牌永远是**一张牌**那么大；
   以前把整个手牌区（1024 宽）当成牌矩形交给动画，于是画出一条浅色长条。
2. **落点语义**：响应牌飞向响应牌位、摸牌从牌堆起飞、装备飞向具体槽位，
   不再全部塞进中央出牌位。
3. **重复登记**：同一张牌往同一个落位在极短时间内只飞一次；"手牌 → 处置区"
   这一段的动画所有权归出牌语义事件（``card_used`` / ``card_response``），
   普通移动事件不再重复注册。
"""

import time

from src.actions import ActionQueue, MoveCardAction, WaitAction
from src.game.judge_presentation import JudgeOutcome, JudgeOutcomeTone, judge_source
from src.game.view.presentation import (
    EV_CARD_RESPONSE,
    EV_CARD_REVEALED,
    EV_CARDS_DRAWN,
    EV_CARDS_MOVED,
    EV_CHAIN,
    EV_DAMAGE,
    EV_DEATH,
    EV_DYING,
    EV_EQUIPMENT,
    EV_HP_LOST,
    EV_JUDGE,
    EV_LOG,
    EV_PHASE,
    EV_PHASE_SKIPPED,
    EV_PUBLIC_POOL,
    EV_RECOVER,
    EV_RESPONSE_REQUEST,
    EV_SKILL,
    EV_TURN_START,
    EV_CARD_USED,
)
from src.ui.storyboard import STEP_PHASE_SKIP

#: 移动动画时长（表现用；与房主侧的 0.3s 同一观感）。
MOVE_DURATION = 0.3

#: 屏幕上没有布局信息时，飞行牌使用的默认尺寸（设计像素）。
FALLBACK_CARD_SIZE = (96, 134)

#: 同一张牌飞向同一个落位的最小间隔：挡掉"同一段移动被两个事件各登记一次"。
#: 这是**按牌按落位**的窄规则（不是"同类动画一律忽略"），所以连摸两张牌、
#: 同一张牌先后飞向不同区域都不受影响。
FLIGHT_DEDUP_WINDOW = 1.2

#: "手牌 → 处置区"这一段由出牌 / 响应语义事件负责，普通移动不重复登记。
SEMANTIC_TARGET_ZONES = ("processing", "table")


def _rect_tuple(value):
    """把矩形规范成 ``(x, y, w, h)``：Rect / 四元组 / 二元组都能吃。"""

    if value is None:
        return None
    if hasattr(value, "x") and hasattr(value, "width"):
        return (int(value.x), int(value.y), int(value.width), int(value.height))
    values = tuple(value)
    if len(values) >= 4:
        return tuple(int(item) for item in values[:4])
    if len(values) == 2:
        return (int(values[0]), int(values[1]),
                FALLBACK_CARD_SIZE[0], FALLBACK_CARD_SIZE[1])
    return None


def _point_tuple(value):
    """把一个矩形 / 点规范成 ``(x, y)`` 中心点。"""

    rect = _rect_tuple(value)
    if rect is None:
        return None
    return (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)


def _card_sized(value, size):
    """区域矩形 → **一张牌**大小、居中在区域里的矩形。

    这是"白影"的正面修法：区域只给位置，尺寸永远来自真实卡牌尺寸。
    """

    if value is None:
        return None
    rect = _rect_tuple(value)
    if rect is None:
        return None
    width, height = int(size[0]), int(size[1])
    center_x = rect[0] + rect[2] // 2
    center_y = rect[1] + rect[3] // 2
    return (int(center_x - width // 2), int(center_y - height // 2), width, height)


def _anchor_card(value, size, *, top=False):
    """把区域矩形当成"锚点"：牌宽居中、高度贴边（手牌区用 top，座位用 bottom）。"""

    if value is None:
        return None
    rect = _rect_tuple(value)
    if rect is None:
        return None
    width, height = int(size[0]), int(size[1])
    center_x = rect[0] + rect[2] // 2
    y = rect[1] if top else rect[1] + rect[3] - height
    return (int(center_x - width // 2), int(y), width, height)


class RemoteJudgeResult:
    """判定面板要的展示对象（由网络事件现造，不是规则引擎的 JudgeResult）。

    字段与 ``ui.judge.JudgePanel`` 的读取口一致；``source_spec`` 走本机静态
    声明表（``judge_presentation``），所以判定语义不需要过网。
    """

    def __init__(self, reason="", card=None, target=None, source_spec=None,
                 outcome=None, replacement_history=(), final=False):
        self.reason = reason
        self.card = card
        self.target = target
        self.source = target
        self.source_spec = source_spec
        self.outcome = outcome
        self.replacement_history = tuple(replacement_history or ())
        self.final = bool(final)


class ClientPresentation:
    """客户端表现驱动器（一台客户端一个）。"""

    def __init__(self, effects):
        self.effects = effects
        self.queue = ActionQueue()
        self.view = None
        self.played_events = 0
        self.failed_events = []
        #: 本次判定累计的改判记录（由事件累积，供最终结果一起交给面板）。
        self.judge_history = ()
        #: (card_id, 落位) → 最近一次登记飞行的时刻（同段移动不重复登记）。
        self._flights = {}
        #: 被"重复登记"规则挡掉的飞行次数（测试与报告取证用）。
        self.duplicate_flights = 0

    # ==================================================
    # 绑定只读视图
    # ==================================================

    def bind_view(self, view):
        """把只读视图交给 FX（动画落点 / 席位定位都要它）。"""

        self.view = view
        self.effects.bind_view(view)
        return self

    def set_layout(self, layout):
        self.effects.set_layout(layout)

    def update(self, dt):
        self.queue.update(dt)
        self.effects.update(dt)

    def reset(self):
        self.queue.clear()
        self.effects.reset()
        self._flights.clear()

    def owned_card_ids(self):
        return self.effects.owned_card_ids()

    # ==================================================
    # 事件入口
    # ==================================================

    def play(self, events):
        """播放一批表现事件（客户端场景每帧调用一次）。

        单条事件出问题**绝不能**影响牌局显示：记进 ``failed_events`` 后跳过，
        继续播后面的。测试与工具会断言 ``failed_events`` 为空。
        """

        for event in events or ():
            kind = event.get("kind", "?")
            try:
                self._play_one(event)
            except Exception as error:                # pragma: no cover - 兜底
                self.failed_events.append("%s: %s: %s" % (kind, type(error).__name__, error))
                del self.failed_events[:-10]
                continue
            self.played_events += 1
        return self.played_events

    def _play_one(self, event):
        kind = event.get("kind")
        handler = _HANDLERS.get(kind)
        if handler is None:
            self.failed_events.append("未知事件类型：%s" % (kind or "?"))
            del self.failed_events[:-10]
            return
        handler(self, event)

    # ==================================================
    # 工具
    # ==================================================

    def player(self, player_id):
        if self.view is None or not player_id:
            return None
        return self.view._players.get(str(player_id))

    def card(self, payload):
        if payload is None or self.view is None:
            return None
        return self.view.cards.get(payload)

    def hidden_cards(self, count):
        """若干张"内容未知"的牌（别人的摸牌 / 拿牌动画只画牌背）。"""

        if self.view is None or count <= 0:
            return []
        return [self.view.cards.placeholder() for _ in range(int(count))]

    def anchor(self, player):
        return self.effects._anchor(player)

    # ---- 区域落位（本机坐标系）----

    def _metrics(self):
        metrics = getattr(getattr(self.effects, "_layout", None), "metrics", None)
        if metrics is None:
            metrics = getattr(self.view, "ui_metrics", None)
        return metrics

    def _layout_state(self):
        return getattr(self.effects, "_layout", None)

    def card_size(self):
        """一张牌的屏幕尺寸（本机布局）；拿不到布局时退回设计尺寸。"""

        from src.ui import layout as layout_module

        metrics = self._metrics()
        if metrics is None:
            return FALLBACK_CARD_SIZE
        return tuple(metrics.hand_card_size()) or layout_module.HAND_CARD_SIZE

    def zone_rect(self, zone, player, slot=None):
        """区域 → **一张牌**大小的屏幕矩形（命名落位，不依赖房主坐标）。

        区域只提供锚点 / 中心，尺寸永远来自本机卡牌尺寸：把整块手牌区
        （1024×173）当成一张牌交给动画，正是"白影"的成因。具体卡位优先：
        手牌取对应卡位、装备取槽位、响应牌取响应位、公共池取第一张位。
        """

        from src.ui import layout as layout_module

        metrics = self._metrics()
        if metrics is None:
            return None
        size = self.card_size()
        if zone in ("draw_pile", "card_count_changed"):
            return _card_sized(metrics.to_screen(layout_module.DRAW_PILE_RECT), size)
        if zone == "discard_pile":
            return _card_sized(metrics.to_screen(layout_module.DISCARD_PILE_RECT), size)
        if zone in ("processing", "table", "response_card"):
            design = (layout_module.RESPONSE_CARD_RECT
                      if zone == "response_card" else layout_module.ACTION_CARD_RECT)
            return _rect_tuple(metrics.to_screen(design))
        if zone == "public_pool":
            return self._pool_rect(metrics)
        if player is None:
            return _card_sized(metrics.to_screen(layout_module.CENTRAL_RECT), size)
        if zone == "equipment":
            return self._equipment_rect(player, slot) or _card_sized(
                metrics.player_status, size)
        if player is getattr(self.view, "player", None):
            if zone == "hand":
                # 自己的手牌：优先用具体卡位（发牌动画的起点才对得上），
                # 拿不到就用手牌区的锚点（牌宽居中、贴着手牌顶边）。
                return (self._hand_card_rect(player, zone) if player is not None else None) \
                    or _anchor_card(metrics.hand_area, size, top=True)
            return _card_sized(metrics.player_status, size)
        seat = None
        layout_state = self._layout_state()
        if layout_state is not None:
            seat = layout_state.any_seat_rect(player)
        return _card_sized(seat or metrics.to_screen(layout_module.CENTRAL_RECT), size)

    def card_rect(self, zone, player, slot=None):
        """``zone_rect`` 的别名（语义更清楚：这里返回的是**一张牌**的矩形）。"""

        return self.zone_rect(zone, player, slot)

    def _pool_rect(self, metrics):
        """公共牌池里第一张牌的位置（没有牌就用池子中心）。"""

        rects = self.pool_rects()
        if rects:
            return rects[0]
        from src.ui import layout as layout_module

        return _card_sized(metrics.to_screen(layout_module.CENTRAL_RECT), self.card_size())

    def pool_rects(self):
        """公共牌池里每一张牌的屏幕矩形（有几张牌就有几个）。"""

        from src.ui import layout as layout_module

        metrics = self._metrics()
        if metrics is None:
            return []
        cards = list(getattr(self.view, "public_card_pool", ()) or ())
        if not cards:
            return []
        layout_state = self._layout_state()
        if layout_state is not None:
            rects = layout_state.public_rects(cards)
            if rects:
                return [_rect_tuple(rect) for rect in rects]
        return [_rect_tuple(rect)
                for rect in layout_module.public_rect_list(cards, metrics)]

    def _hand_card_rect(self, player, zone):
        """这个人手牌里"第一张牌"的屏幕矩形（发牌 / 补牌动画的起点）。"""

        layout_state = self._layout_state()
        if layout_state is None:
            return None
        reference = getattr(layout_state, "game", None)
        if reference is not None and reference.player is not player:
            return None                       # 别人的手牌没有本机卡位
        rects = getattr(layout_state, "hand_rects", None)
        if not rects:
            return None
        return _rect_tuple(rects[0])

    def _equipment_rect(self, player, slot):
        """装备槽的屏幕矩形（优先具体槽位）。"""

        layout_state = self._layout_state()
        if layout_state is None:
            return None
        reference = getattr(layout_state, "game", None)
        if reference is None or reference.player is not player:
            return None
        rects = layout_state.player_equipment_rects()
        rect = rects.get(slot) if slot else None
        if rect is None:
            return None
        return _card_sized(rect, self.card_size())

    def fly(self, card, start, end, *, duration=MOVE_DURATION, key=None):
        """登记一段卡牌飞行动画（纯表现，不改变任何状态）。

        ``key`` 是"这段移动的唯一身份"（默认用 ``(card_id, 落位)``）：同一张牌
        在短时间窗口内飞向同一个落位只登记一次。这不是"同类动画一律忽略"的
        时间 hack，而是"同一段移动不会被两个事件各登记一次"的窄规则。
        """

        start, end = _rect_tuple(start), _rect_tuple(end)
        if card is None or start is None or end is None:
            return None
        card_id = str(getattr(card, "id", "") or "")
        flight_key = key or (card_id, end[:2])
        now = time.monotonic()
        last = self._flights.get(flight_key)
        if last is not None and now - last < FLIGHT_DEDUP_WINDOW:
            self.duplicate_flights += 1
            return None
        self._flights[flight_key] = now
        if len(self._flights) > 64:
            for item in sorted(self._flights, key=self._flights.get)[:-32]:
                self._flights.pop(item, None)
        move = MoveCardAction(card, start, end, duration=duration)
        self.queue.add(move)
        return move


# ==================================================
# 事件处理器
# ==================================================

def _p_card_response(feed, event):
    actor = feed.player(event.get("player_id"))
    card = feed.card(event.get("card"))
    if card is None:
        return
    # 响应牌是公开信息：中央亮出 + 横幅（复用既有 Response 表现路径）。
    # 响应卡停在**响应牌位**一会儿，再飞进弃牌堆——与本地真人出闪的观感一致，
    # 而且不会和主动出牌落到同一个位置。
    feed.fly(card, feed.zone_rect("hand", actor),
             feed.zone_rect("response_card", actor), duration=MOVE_DURATION)
    feed.queue.add(WaitAction(MOVE_DURATION + 0.35))
    feed.fly(card, feed.zone_rect("response_card", actor),
             feed.zone_rect("discard_pile", actor), duration=MOVE_DURATION,
             key=(str(card.id), "response->discard"))
    feed.effects.publish_action(actor, [actor], card)


def _p_card_used(feed, event):
    actor = feed.player(event.get("actor_id"))
    card = feed.card(event.get("card"))
    targets = [feed.player(item) for item in event.get("target_ids") or ()]
    targets = [item for item in targets if item is not None]
    if event.get("finished"):
        if card is not None:
            feed.effects.finish_card_use(card)
        return
    if card is None or actor is None:
        return
    # 中央显示的是**语义牌**（View-As 时就是虚拟牌）：来源实体牌只作为
    # provenance 存在，绝不出现"闪 + 杀"两个主体。这一段飞行由本事件独占
    # （``_p_cards_moved`` 会跳过飞向处置区的那条），所以不会飞两次。
    feed.fly(card, feed.zone_rect("hand", actor),
             feed.zone_rect("table", actor), duration=MOVE_DURATION,
             key=(str(card.id), "use->table"))
    # 横幅与箭头排进**统一演出队列**：房主那边怎么排队，客户端就怎么排队。
    feed.effects.present_card_used(
        actor, targets, card, sequential=bool(event.get("sequential")))


def _p_response_request(feed, event):
    """逐目标响应型锦囊：把箭头切到当前正在结算的目标。

    只有"按目标逐个结算"的请求才配箭头，判据与本地 FX
    （``ui.fx._on_pending_created``）完全一致：

    * 【无懈可击】链（``reason == "wuxie_chain"``）问的是"要不要打断这张锦囊"，
      提问对象**不是**正在结算的目标，牵箭头会画出一次并不存在的结算；
    * 普通【杀】→【闪】这类响应同理（那条箭头已经在 CARD_USED 时牵好了）。
    """

    if not event.get("sequential") or str(event.get("reason") or "") == "wuxie_chain":
        return
    card = feed.card(event.get("card"))
    source = feed.player(event.get("source_id"))
    target = feed.player(event.get("target_id"))
    if card is None:
        return
    feed.effects.focus_arrow(source, target, card)


def _p_cards_drawn(feed, event):
    player = feed.player(event.get("player_id"))
    if player is None:
        return
    cards = [feed.card(item) for item in event.get("cards") or ()]
    cards = [item for item in cards if item is not None]
    count = int(event.get("count") or len(cards) or 0)
    if cards:
        # 本人：牌面已知，逐张飞到手牌。
        feed.effects.note_draw(player, cards,
                               _origin_key(feed, event.get("from_zone"), player))
        return
    if count > 0:
        # 其他人：只知道"摸了几张"，用同样张数的牌背飞过去。
        feed.effects.note_draw(player, feed.hidden_cards(count),
                               _origin_key(feed, event.get("from_zone"), player))


def _p_cards_moved(feed, event):
    source_player = feed.player(event.get("from_player_id"))
    target_player = feed.player(event.get("to_player_id"))
    reason = event.get("reason")
    to_zone = str(event.get("to_zone") or "")
    if reason == "discard" and to_zone == "discard_pile":
        feed.effects.note_discard()
    cards = [feed.card(item) for item in event.get("cards") or ()]
    cards = [item for item in cards if item is not None]
    hidden = int(event.get("hidden_count") or 0)
    if not cards and hidden:
        # 内容未知的牌（例如顺手牵羊拿走的手牌）：只表现"有一张牌过去了"。
        cards = feed.hidden_cards(hidden)
    if to_zone in SEMANTIC_TARGET_ZONES:
        # 手牌 → 处置区 / 桌面这一段由 card_used / card_response 负责
        # （它们还要同时亮牌、拉箭头）。这里再飞一次就是"同一张牌飞两遍"。
        return
    start = feed.zone_rect(event.get("from_zone"), source_player, event.get("from_slot"))
    end = feed.zone_rect(to_zone, target_player, event.get("to_slot"))
    if start is None or end is None:
        return
    for card in cards:
        feed.fly(card, start, end)


def _p_damage(feed, event):
    # 排进演出队列（不是立刻播）：判定 / 技能提示还在演的时候，这一次伤害
    # 不会抢在它前面出现。房主侧与客户端是同一条队列实现。
    feed.effects.present_damage(feed.player(event.get("player_id")),
                                int(event.get("amount") or 0))


def _p_recover(feed, event):
    feed.effects.present_recover(feed.player(event.get("player_id")),
                                 int(event.get("amount") or 0))


def _p_hp_lost(feed, event):
    feed.effects.present_lose_hp(feed.player(event.get("player_id")),
                                 int(event.get("amount") or 0))


def _p_dying(feed, event):
    feed.effects.present_dying(feed.player(event.get("player_id")))


def _p_death(feed, event):
    feed.effects.present_death(feed.player(event.get("player_id")))


def _p_chain(feed, event):
    feed.effects.present_chain(feed.player(event.get("player_id")),
                               bool(event.get("chained")))


def _p_skill(feed, event):
    feed.effects.present_skill(
        feed.player(event.get("player_id")),
        event.get("skill_name"),
        [feed.player(item) for item in event.get("target_ids") or ()],
        skill_id=str(event.get("skill_id") or ""),
        kind_label=str(event.get("kind_label") or ""),
        text=str(event.get("text") or ""),
    )


def _p_turn_start(feed, event):
    feed.effects.present_turn_start(feed.player(event.get("player_id")))


def _p_phase_skipped(feed, event):
    """阶段被跳过（乐不思蜀 / 兵粮寸断）：结论提示条，同样排进演出队列。"""

    text = str(event.get("text") or "")
    if not text:
        return
    feed.effects.present_result(
        text, detail=str(event.get("detail") or ""),
        tone=str(event.get("tone") or "phase"), kind=STEP_PHASE_SKIP,
    )


def _p_card_revealed(feed, event):
    """公开亮出的牌（火攻展示）：所有客户端都要看得到。"""

    card = feed.card(event.get("card"))
    player = feed.player(event.get("player_id"))
    if card is None or player is None:
        return
    mark = (getattr(card, "suit_name", "") or "") + str(getattr(card, "rank", "") or "")
    feed.effects.present_result(
        "%s 展示了一张手牌" % getattr(player, "name", ""),
        detail=("【%s】%s" % (card.display_name, mark)) if mark
               else ("【%s】" % card.display_name),
        tone="",
    )


def _p_phase(feed, event):
    player = feed.player(event.get("player_id"))
    if player is None or event.get("skipped"):
        return
    # 阶段变化只更新横幅下的提示（回合横幅由 turn_start 播）。
    return


def _p_equipment(feed, event):
    player = feed.player(event.get("player_id"))
    card = feed.card(event.get("card"))
    if player is None or card is None:
        return
    if event.get("lost"):
        feed.fly(card, feed.zone_rect("equipment", player, event.get("slot")),
                 feed.zone_rect(event.get("to_zone") or "discard_pile",
                                feed.player(event.get("to_player_id"))),
                 duration=MOVE_DURATION)
    else:
        feed.fly(card, feed.zone_rect("hand", player),
                 feed.zone_rect("equipment", player, event.get("slot")),
                 duration=MOVE_DURATION)


def _p_public_pool(feed, event):
    cards = [feed.card(item) for item in event.get("cards") or ()]
    cards = [item for item in cards if item is not None]
    if not cards or feed.view is None:
        return
    start = feed.zone_rect("draw_pile", None)
    if start is None:
        return
    # 逐张落到池子里各自的位置：全部塞进同一个点会看成"只有一张牌在动"。
    slots = feed.pool_rects()
    for index, card in enumerate(cards):
        slot = slots[index] if index < len(slots) else feed._pool_rect(feed._metrics())
        feed.fly(card, start, slot, duration=MOVE_DURATION,
                 key=(str(card.id), "pool"))


def _p_log(feed, event):
    return


def _p_judge(feed, event):
    """判定：开始 / 翻牌 / 改判 / 结果，全部喂给既有 JudgePanel。"""

    stage = event.get("stage")
    target = feed.player(event.get("judged_player_id"))
    reason = str(event.get("reason") or "")
    spec = judge_source(reason)
    card = feed.card(event.get("card"))

    if stage in ("started", "revealed"):
        feed.judge_history = ()
        # 判定排进演出队列：它前面的演出（技能提示 / 上一次结算）先演完，
        # 它后面的（伤害 / 濒死 / 阵亡 / 下一个回合）一定排在它后面。
        feed.effects.present_judge(RemoteJudgeResult(
            reason=reason, card=card, target=target, source_spec=spec))
        return
    if stage == "replaced":
        feed.judge_history = tuple(event.get("history") or ())
        feed.effects.judge_note("replaced", {
            # 改判事件用的是 new_card / old_card（不是 card）。
            "new_card": feed.card(event.get("new_card")),
            "old_card": feed.card(event.get("old_card")),
            "player": feed.player(event.get("player_id")),
            "skill_id": str(event.get("skill_id") or ""),
            "history": tuple(event.get("history") or ()),
        })
        return
    if stage == "result":
        outcome = JudgeOutcome(
            _tone(event.get("tone")), str(event.get("title") or ""),
            str(event.get("text") or ""))
        feed.effects.judge_note("result", RemoteJudgeResult(
            reason=reason, card=card, target=target, source_spec=spec,
            outcome=outcome, final=True, replacement_history=feed.judge_history))
        return
    if stage == "finished":
        return


def _tone(value):
    try:
        return JudgeOutcomeTone(value)
    except ValueError:
        return JudgeOutcomeTone.NEUTRAL


def _origin_key(feed, zone, player):
    """摸牌动画的起点（交给 FX 的 origin 语义：牌堆 / 公共池）。

    注意类型约定：``Effects`` 那边要的是**区域对象**（它自己取 ``id()`` 做
    匹配，见 ``fx.Effects._origin_key``）。以前这里回传的是已经取过 ``id``
    的整数，于是"牌堆起点"永远匹配不上，摸牌动画从中央起飞。
    """

    if feed.view is None:
        return None
    if zone == "draw_pile":
        return getattr(getattr(feed.view, "deck", None), "draw_pile", None)
    if zone == "public_pool":
        return getattr(feed.view, "public_card_pool", None)
    return None


_HANDLERS = {
    EV_CARD_USED: _p_card_used,
    EV_CARD_RESPONSE: _p_card_response,
    EV_RESPONSE_REQUEST: _p_response_request,
    EV_CARDS_DRAWN: _p_cards_drawn,
    EV_CARDS_MOVED: _p_cards_moved,
    EV_DAMAGE: _p_damage,
    EV_RECOVER: _p_recover,
    EV_HP_LOST: _p_hp_lost,
    EV_DYING: _p_dying,
    EV_DEATH: _p_death,
    EV_CHAIN: _p_chain,
    EV_SKILL: _p_skill,
    EV_TURN_START: _p_turn_start,
    EV_PHASE: _p_phase,
    EV_PHASE_SKIPPED: _p_phase_skipped,
    EV_CARD_REVEALED: _p_card_revealed,
    EV_EQUIPMENT: _p_equipment,
    EV_PUBLIC_POOL: _p_public_pool,
    EV_LOG: _p_log,
    EV_JUDGE: _p_judge,
}
