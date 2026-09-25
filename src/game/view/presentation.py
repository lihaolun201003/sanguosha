"""表现事件桥（Phase 11.3 §20 / §22 / §24 / §25 / §26）。

房主侧订阅引擎的 ``EventDispatcher``，把**真实发生过的** Gameplay 事件翻译成
一份与画面无关的事实表；再由 ``drain_for(viewer)`` 为每名观众生成合法的
``GAME_EVENT`` 载荷。

分工（与 §12 一致）：

* **Snapshot**（``view_builder``）负责"最终事实"：谁在哪、几张牌、什么状态。
* **Event**（本模块）只负责"动画 / 表现"：卡牌从哪飞到哪、谁对谁做了什么、
  判定翻出了什么。**事件永远不是权威状态来源**。

三条硬约束：

1. 一场对局只订阅一次引擎事件（``attach`` / ``detach`` 配对）。
2. 隐藏信息在这里就被剔除：摸牌 / 拿手牌的牌面只发给有权的玩家，其余人
   只收到张数。客户端拿不到，也就不可能画出来。
3. 事件是纯数据（dict），不含屏幕坐标：每台客户端用自己的 ``LayoutMetrics``
   算动画落点。
"""

from ..engine.events import EventType

from .view_model import card_to_payload
from .visibility import (
    ZONE_DISCARD,
    ZONE_DRAW_PILE,
    ZONE_EQUIPMENT,
    ZONE_HAND,
    ZONE_JUDGE,
    ZONE_NONE,
    ZONE_POOL,
    ZONE_PROCESSING,
    ZONE_TABLE,
    card_is_visible_to,
)

# ==================================================
# 事件类型（客户端认识的全部表现事件）
# ==================================================

EV_CARD_USED = "card_used"
EV_CARD_RESPONSE = "card_response"
EV_RESPONSE_REQUEST = "response_request"
EV_CARDS_MOVED = "cards_moved"
EV_CARDS_DRAWN = "cards_drawn"
EV_CARD_COUNT = "card_count_changed"
EV_DAMAGE = "damage"
EV_RECOVER = "recover"
EV_HP_LOST = "hp_lost"
EV_JUDGE = "judge"
EV_SKILL = "skill"
EV_DEATH = "death"
EV_DYING = "dying"
EV_CHAIN = "chain"
EV_TURN_START = "turn_start"
EV_PHASE = "phase"
EV_EQUIPMENT = "equipment"
EV_PUBLIC_POOL = "public_pool"
EV_TABLE_CARDS = "table_cards"
EV_LOG = "log"
EV_GAME_OVER = "game_over"


def zone_descriptor(zone, game):
    """区域对象 → 与客户端约定的区域描述（不含任何坐标）。"""
    if zone is None:
        return {"zone": ZONE_NONE, "player_id": "", "slot": ""}
    deck = getattr(game, "deck", None)
    if zone is getattr(deck, "draw_pile", None):
        return {"zone": ZONE_DRAW_PILE, "player_id": "", "slot": ""}
    if zone is getattr(deck, "discard_pile", None):
        return {"zone": ZONE_DISCARD, "player_id": "", "slot": ""}
    if zone is getattr(game, "processing_zone", None):
        return {"zone": ZONE_PROCESSING, "player_id": "", "slot": ""}
    if zone is getattr(game, "public_card_pool", None):
        return {"zone": ZONE_POOL, "player_id": "", "slot": ""}
    for player in getattr(game, "players", ()) or ():
        if zone is player.hand:
            return {"zone": ZONE_HAND, "player_id": str(player.player_id), "slot": ""}
        if zone is player.judgement_zone:
            return {"zone": ZONE_JUDGE, "player_id": str(player.player_id), "slot": ""}
    return {"zone": ZONE_NONE, "player_id": "", "slot": ""}


class Fact:
    """一条待下发的表现事实。

    ``base`` 是所有人共享的字段；``private`` 是"只有某些观众才有的字段"
    （例如摸牌的两张**具体**手牌）。``viewers`` 给定时，这条事实只发给这些
    观众。三个集合共同保证：隐藏信息在生成载荷时就已剔除。
    """

    __slots__ = ("kind", "base", "private", "viewers", "event_id")

    def __init__(self, kind, base=None, private=None, viewers=None):
        self.kind = kind
        self.base = dict(base or {})
        self.private = dict(private or {})
        self.viewers = frozenset(viewers) if viewers else None
        #: 由 ``HostMatch`` 在广播时统一编号：客户端据此去重，不会重播。
        self.event_id = 0

    def for_viewer(self, viewer_id):
        if self.viewers is not None and viewer_id not in self.viewers:
            return None
        payload = dict(self.base)
        extra = self.private.get(viewer_id)
        if extra:
            payload.update(extra)
        return payload


class PresentationBridge:
    """引擎事件 → 逐人表现事实。房主侧每局一个实例。"""

    #: 事件表上限：正常的客户端会立刻排空；积压这么多说明对端已经不可用。
    MAX_PENDING = 512

    def __init__(self, game, match_id=""):
        self.game = game
        self.match_id = str(match_id or "")
        self.facts = []
        self.next_event_id = 0
        self._tokens = []
        #: 快照要用的"当前动作 / 响应 / 判定"（由真实事件维护，权威在房主）。
        self.action = None
        self.response = None
        self.judge = None
        self.dropped = 0
        #: 表现层自身出错时的记录（**绝不能**让表现影响规则，见 ``_guard``）。
        self.errors = []

    # ==================================================
    # 订阅
    # ==================================================

    def attach(self):
        if self._tokens:
            return self
        dispatcher = self.game.context.events
        subscriptions = (
            (EventType.CARD_USED, self._on_card_used),
            (EventType.CARD_USE_FINISHED, self._on_card_use_finished),
            (EventType.ATOM_AFTER, self._on_atom),
            (EventType.DAMAGE_APPLIED, self._on_damage),
            (EventType.JUDGE_STARTED, self._on_judge_started),
            (EventType.JUDGE_REVEALED, self._on_judge_revealed),
            (EventType.JUDGE_REPLACED, self._on_judge_replaced),
            (EventType.JUDGE_RESULT, self._on_judge_result),
            (EventType.JUDGE_FINISHED, self._on_judge_finished),
            (EventType.SKILL_TRIGGERED, self._on_skill),
            (EventType.TURN_START, self._on_turn_start),
            (EventType.PHASE_START, self._on_phase_start),
            (EventType.DEATH, self._on_death),
            (EventType.DYING_ENTERED, self._on_dying),
            (EventType.CHAIN_STATE_CHANGED, self._on_chain),
            (EventType.EQUIPMENT_LOST, self._on_equipment_lost),
            (EventType.PENDING_CREATED, self._on_pending_created),
            (EventType.PENDING_RESOLVED, self._on_pending_resolved),
        )
        for event_name, handler in subscriptions:
            self._tokens.append(
                dispatcher.subscribe(event_name, self._guard(handler), owner=self))
        return self

    def _guard(self, handler):
        """包一层保护：表现事件出错**绝不能**中断引擎的状态变更。

        事件分发是同步的：如果这里抛异常，会把调用它的规则流程一起打断
        （例如装备流程只走到一半，"牌从处理区拿走"做了、"进装备槽"没做）。
        所以表现层自己吞掉异常、记进 ``errors`` 供测试断言，规则继续跑。
        """

        def guarded(context, event):
            try:
                return handler(context, event)
            except Exception as error:                   # pragma: no cover - 兜底
                self.errors.append("%s: %s" % (type(error).__name__, error))
                del self.errors[:-10]
                return None

        return guarded

    def detach(self):
        if self.game is not None:
            for token in self._tokens:
                self.game.context.events.unsubscribe(token)
        self._tokens = []
        return self

    # ==================================================
    # 取出
    # ==================================================

    def drain_for(self, viewer_id, revision):
        """给一名观众的待发事件；同时清空（事件只发一次）。"""

        payloads = []
        for fact in self.facts:
            data = fact.for_viewer(str(viewer_id))
            if data is None:
                continue
            data["kind"] = fact.kind
            data["revision"] = int(revision)
            payloads.append(data)
        return payloads

    def take_events(self):
        """一次性取出全部事实（再按观众分发）。"""

        items = self.facts
        self.facts = []
        return items

    def empty(self):
        return not self.facts

    def clear(self):
        self.facts = []
        self.action = None
        self.response = None
        self.judge = None

    # ==================================================
    # 记录
    # ==================================================

    def _fact(self, kind, base=None, private=None, viewers=None):
        if len(self.facts) >= self.MAX_PENDING:
            # 客户端明显跟不上了：丢掉最老的一条，绝不无限增长。
            del self.facts[0]
            self.dropped += 1
        self.facts.append(Fact(kind, base, private, viewers))

    def _card_payload(self, card):
        return card_to_payload(card) if card is not None else None

    @staticmethod
    def _player_id(player):
        return str(getattr(player, "player_id", "") or "")

    # ==================================================
    # 用牌 / 响应
    # ==================================================

    def _on_card_used(self, _context, event):
        card = event.payload.get("card")
        actor = event.source
        targets = tuple(event.payload.get("targets") or ())
        if card is None or actor is None:
            return
        sources = tuple(getattr(card, "source_cards", ()) or ())
        virtual = bool(getattr(card, "_virtual", False))
        skill_name = ""
        if virtual:
            skill_id = str(getattr(card, "skill_id", "") or "")
            definition = self.game.skill_registry.get(skill_id)
            skill_name = getattr(definition, "name", "") or skill_id
        self.action = {
            "actor": actor,
            "targets": targets,
            "card": card,
            "label": str(getattr(card, "display_name", "") or ""),
            "skill_name": skill_name,
            "sources": sources,
            "virtual": virtual,
        }
        self._fact(EV_CARD_USED, {
            "actor_id": self._player_id(actor),
            "label": str(getattr(card, "display_name", "") or ""),
            "target_ids": [self._player_id(item) for item in targets],
            "sequential": bool(event.payload.get("sequential_targets")),
            "skill_name": skill_name,
            "virtual": virtual,
            "card": card_to_payload(card),
        })

    def _on_card_use_finished(self, _context, event):
        card = event.payload.get("card")
        self.action = None
        self.response = None
        self._fact(EV_CARD_USED, {
            "actor_id": self._player_id(event.source),
            "label": str(getattr(card, "display_name", "") or ""),
            "finished": True,
        })

    def _on_pending_created(self, _context, event):
        """响应型请求：谁被要求打出什么（响应牌是公开信息）。"""

        request = event.payload.get("request")
        if request is None:
            return
        request_type = getattr(getattr(request, "request_type", None), "value", "")
        if request_type != "respond_card":
            return
        context_data = dict(getattr(request, "context", None) or {})
        allowed = tuple(context_data.get("allowed_cards") or ())
        prompt = str(getattr(request, "prompt", "") or "")
        target = getattr(request, "target", None)
        source = getattr(request, "source", None)
        self._fact(EV_RESPONSE_REQUEST, {
            # 共享响应阶段（无懈）里没有"唯一被问的人"：谁有资格响应本身就是
            # 手牌信息（他多半握着【无懈可击】），所以这里按人取字段的位置留空。
            "target_id": ("" if getattr(request, "is_group", False)
                          else self._player_id(target)),
            "source_id": self._player_id(source),
            "allowed": [str(item) for item in allowed],
            "prompt": prompt,
            "group": bool(getattr(request, "is_group", False)),
            "card": (card_to_payload(context_data["card"])
                     if context_data.get("card") is not None else None),
            # 请求的**意图**要一起过去：无懈链 / 救援 / 普通【闪】响应都不是
            # "某个目标正在逐目标结算"，客户端据此决定要不要牵指向箭头
            # （判据与本地 FX 完全一致，见 ui.client_fx._p_response_request）。
            "reason": str(context_data.get("reason") or ""),
            "sequential": bool(context_data.get("sequential_targets")),
        })

    def _on_pending_resolved(self, _context, event):
        """远程真人打出的响应牌：把实体牌同步给所有人（响应牌是公开的）。

        引擎的 ``PendingResolution`` 对**响应**只填 ``card``，对**选牌**才填
        ``cards``；选牌类请求的牌移动已经由 ATOM 事件覆盖，所以这里只处理
        响应类请求，避免同一张牌被表现两次。
        """

        resolution = event.payload.get("resolution")
        if resolution is None:
            return
        request = getattr(resolution, "request", None)
        request_type = getattr(getattr(request, "request_type", None), "value", "")
        context_data = dict(getattr(request, "context", None) or {})
        if request_type != "respond_card":
            return
        cards = list(getattr(resolution, "cards", ()) or ())
        card = getattr(resolution, "card", None)
        if not cards and card is not None:
            cards = [card]
        actor = getattr(resolution, "actor", None)
        if not cards or getattr(resolution, "passed", False):
            self.response = None
            return
        card = cards[0]
        self.response = {
            "player": actor,
            "card": card,
            "label": str(getattr(card, "display_name", "") or ""),
        }
        self._fact(EV_CARD_RESPONSE, {
            "player_id": self._player_id(actor),
            "label": str(getattr(card, "display_name", "") or ""),
            "card": card_to_payload(card),
            "reason": str(context_data.get("reason") or ""),
        })

    # ==================================================
    # 原子（牌移动 / 摸牌 / 回血 / 失去体力）
    # ==================================================

    def _on_atom(self, _context, event):
        atom = event.payload.get("atom")
        result = event.payload.get("result")
        data = getattr(result, "data", None) or {}
        name = atom.__class__.__name__

        if name == "MoveCardAtom":
            self._note_move(atom, data)
        elif name == "DrawCardsAtom":
            self._note_draw(atom, data)
        elif name == "RecoverHpAtom":
            target = getattr(atom, "target", None)
            amount = int(data.get("amount") or 0)
            if target is not None and amount > 0:
                self._fact(EV_RECOVER, {
                    "player_id": self._player_id(target),
                    "amount": amount,
                })
        elif name == "LoseHpAtom":
            target = getattr(atom, "target", None)
            amount = int(data.get("amount") or 0)
            if target is not None and amount > 0:
                self._fact(EV_HP_LOST, {
                    "player_id": self._player_id(target),
                    "amount": amount,
                })

    def _note_move(self, atom, data):
        card = getattr(atom, "card", None)
        if card is None:
            return
        source = zone_descriptor(getattr(atom, "source", None), self.game)
        destination = zone_descriptor(getattr(atom, "destination", None), self.game)
        self._emit_move(card, source, destination, reason=_reason_for(source, destination))

    def _note_draw(self, atom, data):
        target = data.get("target", getattr(atom, "target", None))
        cards = tuple(data.get("cards") or ())
        if target is None:
            return
        player_id = self._player_id(target)
        private = {}
        if cards:
            # 只有摸牌的人自己拿到具体牌面；其他人只拿到"摸了几张"。
            private[player_id] = {"cards": [card_to_payload(card) for card in cards]}
        self._fact(EV_CARDS_DRAWN, {
            "player_id": player_id,
            "count": len(cards),
            "from_zone": ZONE_DRAW_PILE,
            "to_zone": ZONE_HAND,
        }, private=private)

    def _emit_move(self, card, source, destination, *, reason):
        """一条牌移动：牌面是否公开完全由"从哪个区到哪个区"决定。

        隐藏只发生在**离开一个隐藏区域、又进入另一个隐藏区域**时（手牌 →
        手牌，牌堆 → 手牌）。这时牌面只发给拿到牌的人；其他观众只收到
        "有一张牌动了"（``hidden_count``），**网络包里没有牌面**。

        其余组合都是公开的：手牌 → 弃牌堆（弃置后公开）、手牌 → 结算区
        （出牌）、装备区 ↔ 任何地方、公共池 → 手牌（五谷的牌本来就是明牌）。
        """

        from_hidden = source["zone"] in (ZONE_HAND, ZONE_DRAW_PILE)
        to_hand = destination["zone"] == ZONE_HAND
        base = {
            "from_zone": source["zone"],
            "from_player_id": source["player_id"],
            "from_slot": source["slot"],
            "to_zone": destination["zone"],
            "to_player_id": destination["player_id"],
            "to_slot": destination["slot"],
            "reason": reason,
        }
        private = {}
        if from_hidden and to_hand:
            base["cards"] = []
            base["hidden_count"] = 1
            base["count"] = 1
            gainer = destination["player_id"]
            if gainer:
                private[str(gainer)] = {"cards": [card_to_payload(card)]}
        else:
            base["cards"] = [card_to_payload(card)]
            base["hidden_count"] = 0
            base["count"] = 1
        self._fact(EV_CARDS_MOVED, base, private=private)

    def note_hidden_transfer(self, card, source, destination, gainer_id):
        """规则明确授权的隐藏转移：牌面对"获得者"公开，对其他人仍是牌背。

        与 ``_emit_move`` 的默认规则同源，额外把获得者写清楚，供"从别人手里
        拿牌"这类规则在事件里补一条明确的获得记录。
        """

        source_desc = zone_descriptor(source, self.game)
        destination_desc = zone_descriptor(destination, self.game)
        private = {}
        if gainer_id:
            private[str(gainer_id)] = {"cards": [card_to_payload(card)]}
        base = {
            "from_zone": source_desc["zone"],
            "from_player_id": source_desc["player_id"],
            "from_slot": source_desc["slot"],
            "to_zone": destination_desc["zone"],
            "to_player_id": destination_desc["player_id"],
            "to_slot": destination_desc["slot"],
            "reason": "transfer",
            "cards": [],
            "hidden_count": 1,
            "count": 1,
        }
        self._fact(EV_CARDS_MOVED, base, private=private)

    # ==================================================
    # 伤害 / 濒死 / 死亡 / 连环
    # ==================================================

    def _on_damage(self, _context, event):
        damage = event.payload.get("damage")
        amount = int(event.payload.get("amount") or 0)
        if damage is None or amount <= 0:
            return
        target = getattr(damage, "target", None) or event.target
        source = getattr(damage, "source", None)
        card = getattr(damage, "card", None)
        # 伤害是"失去体力"的一种：把它记成伤害，同一次失去体力事件不再重复播。
        self._absorb_hp_lost(target, amount)
        self._fact(EV_DAMAGE, {
            "player_id": self._player_id(target),
            "amount": amount,
            "source_id": self._player_id(source),
            "nature": str(getattr(damage, "nature", "normal") or "normal"),
            "card": (card_to_payload(card) if card is not None else None),
        })

    def _absorb_hp_lost(self, target, amount):
        """同一次结算里 LoseHpAtom 已经记过账，避免客户端播两次飘字。"""

        for fact in reversed(self.facts):
            if fact.kind != EV_HP_LOST:
                continue
            if fact.base.get("player_id") == self._player_id(target) \
                    and int(fact.base.get("amount") or 0) == int(amount):
                self.facts.remove(fact)
                return

    def _on_dying(self, _context, event):
        player = event.target
        if player is not None:
            self._fact(EV_DYING, {"player_id": self._player_id(player)})

    def _on_death(self, _context, event):
        player = event.target
        if player is not None:
            self._fact(EV_DEATH, {"player_id": self._player_id(player)})

    def _on_chain(self, _context, event):
        player = event.target
        if player is None:
            return
        self._fact(EV_CHAIN, {
            "player_id": self._player_id(player),
            "chained": bool(event.payload.get("chained")),
        })

    # ==================================================
    # 技能 / 回合 / 阶段 / 装备
    # ==================================================

    def _on_skill(self, _context, event):
        player = event.source
        self._fact(EV_SKILL, {
            "player_id": self._player_id(player),
            "skill_id": str(event.payload.get("skill_id") or ""),
            "skill_name": str(event.payload.get("skill_name") or ""),
            "target_ids": [
                self._player_id(item) for item in event.payload.get("targets") or ()
            ],
        })

    def _on_turn_start(self, _context, event):
        player = event.source
        if player is not None:
            self._fact(EV_TURN_START, {"player_id": self._player_id(player)})

    def _on_phase_start(self, _context, event):
        player = event.source
        phase = getattr(event.payload.get("phase"), "value", None) or event.payload.get("phase")
        self._fact(EV_PHASE, {
            "player_id": self._player_id(player),
            "phase": str(phase or ""),
            "skipped": bool(event.payload.get("skipped")),
        })

    def _on_equipment_lost(self, _context, event):
        player = event.target
        card = event.payload.get("card")
        slot = str(event.payload.get("slot") or "")
        destination = zone_descriptor(event.payload.get("destination"), self.game)
        self._fact(EV_EQUIPMENT, {
            "player_id": self._player_id(player),
            "slot": slot,
            "lost": True,
            "card": (card_to_payload(card) if card is not None else None),
            "to_zone": destination["zone"],
            "to_player_id": destination["player_id"],
        })

    # ==================================================
    # 判定
    # ==================================================

    def _on_judge_started(self, _context, event):
        reason = str(event.payload.get("reason") or "")
        self.judge = {
            "reason": reason,
            "judged_player": event.source,
            "card": None,
            "result": None,
            "stage": "started",
            "final": False,
            "replacements": [],
        }
        self._fact(EV_JUDGE, {"stage": "started", "reason": reason,
                              "judged_player_id": self._player_id(event.source)})

    def _on_judge_revealed(self, _context, event):
        result = event.payload.get("result")
        card = getattr(result, "card", None)
        reason = str(getattr(result, "reason", "") or "")
        judged = getattr(result, "target", None) or event.source
        self.judge = {
            "reason": reason,
            "judged_player": judged,
            "card": card,
            "result": result,
            "stage": "revealed",
            "final": False,
            "replacements": [],
        }
        self._fact(EV_JUDGE, {
            "stage": "revealed",
            "reason": reason,
            "judged_player_id": self._player_id(judged),
            "card": (card_to_payload(card) if card is not None else None),
        })

    def _on_judge_replaced(self, _context, event):
        if self.judge is None:
            return
        skill_id = str(event.payload.get("skill_id") or "")
        definition = self.game.skill_registry.get(skill_id)
        skill_name = getattr(definition, "name", "") or skill_id
        old_card = event.payload.get("old_card")
        new_card = event.payload.get("new_card")
        player = event.payload.get("player")
        self.judge["card"] = new_card
        self.judge["stage"] = "replaced"
        self.judge["replacements"].append({
            "player": player, "skill_name": skill_name,
            "old": old_card, "new": new_card,
        })
        self._fact(EV_JUDGE, {
            "stage": "replaced",
            "reason": str(self.judge.get("reason") or ""),
            "judged_player_id": self._player_id(self.judge.get("judged_player")),
            "player_id": self._player_id(player),
            "skill_id": skill_id,
            "skill_name": skill_name,
            "old_card": (card_to_payload(old_card) if old_card is not None else None),
            "new_card": (card_to_payload(new_card) if new_card is not None else None),
            # 面板要凭这条历史显示"被改过"（长度即改判次数）。
            "history": [
                {"player_id": self._player_id(item.get("player")),
                 "skill_name": str(item.get("skill_name") or "")}
                for item in self.judge["replacements"]
            ],
        })

    def _on_judge_result(self, _context, event):
        result = event.payload.get("result")
        if self.judge is None or result is None:
            return
        self.judge["result"] = result
        self.judge["card"] = getattr(result, "card", None)
        self.judge["stage"] = "result"
        self.judge["final"] = True
        outcome = getattr(result, "outcome", None)
        self._fact(EV_JUDGE, {
            "stage": "result",
            "reason": str(getattr(result, "reason", "") or ""),
            "judged_player_id": self._player_id(self.judge.get("judged_player")),
            "card": (card_to_payload(getattr(result, "card", None))
                     if getattr(result, "card", None) is not None else None),
            "tone": str(getattr(getattr(outcome, "tone", None), "value", "") or ""),
            "title": str(getattr(outcome, "title", "") or ""),
            "text": str(getattr(outcome, "text", "") or ""),
        })

    def _on_judge_finished(self, _context, event):
        if self.judge is None:
            return
        self.judge["stage"] = "finished"
        self._fact(EV_JUDGE, {
            "stage": "finished",
            "reason": str(self.judge.get("reason") or ""),
            "judged_player_id": self._player_id(self.judge.get("judged_player")),
        })

    # ==================================================
    # 供房主主动补充的事实（公共池 / 桌面牌 / 战报 / 结算）
    # ==================================================

    def note_public_pool(self, cards):
        self._fact(EV_PUBLIC_POOL, {
            "cards": [card_to_payload(card) for card in cards],
            "count": len(cards),
        })

    def note_table_cards(self, entries):
        self._fact(EV_TABLE_CARDS, {
            "stage": "table",
            "cards": [card_to_payload(card) for card, _slot in entries],
        })

    def note_log(self, text):
        if text:
            self._fact(EV_LOG, {"text": str(text)})

    def note_game_over(self, payload):
        self._fact(EV_GAME_OVER, dict(payload or {}))


def _reason_for(source, destination):
    """区域关系 → 客户端认识的动作语义（客户端只用来挑动画，不参与规则）。"""

    if source["zone"] == ZONE_DRAW_PILE and destination["zone"] == ZONE_HAND:
        return "draw"
    if destination["zone"] == ZONE_DISCARD:
        return "discard"
    if destination["zone"] == ZONE_HAND:
        return "transfer"
    if destination["zone"] == ZONE_PROCESSING:
        return "play"
    if destination["zone"] == ZONE_POOL:
        return "pool_fill"
    if destination["zone"] == ZONE_JUDGE:
        return "judge_place"
    if source["zone"] == ZONE_POOL:
        return "pool_take"
    if source["zone"] == ZONE_EQUIPMENT:
        return "unequip"
    if destination["zone"] == ZONE_EQUIPMENT:
        return "equip"
    return "move"
