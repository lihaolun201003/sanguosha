"""客户端只读视图模型（Phase 11.3）。

这里定义的是一份**纯数据**：它描述"某一名玩家此刻有权看见的牌局"，不含
任何规则、任何流程、任何可变更的权威状态。三条硬约束：

1. 客户端永远不构造第二个 ``Game``。它只持有 ``ClientGameView``。
2. 视图里不出现"只有房主该知道"的东西——隐藏手牌、未公开身份、牌堆顺序
   在**生成视图时**就已经不在数据里了，不是靠 UI 不画。
3. 视图模型不认识 TurnFlow / DamageFlow / SkillManager / 牌堆随机 / AI。
   它只有数据 + ``to_payload`` / ``from_payload``。

命名沿用报告里的 ``ClientGameView``。``ViewCard`` / ``PlayerView`` 是它的
组成部分；``PlayerView.hand`` 只对"自己"有内容，其他人永远是空元组（真实
手牌数量在 ``hand_count``）。
"""

from dataclasses import dataclass, field

# ==================================================
# 卡牌视图
# ==================================================

def card_to_payload(card):
    """把一张真实 ``Card`` 转成可上网的牌面数据（只用于可见的牌）。"""

    return {
        "card_id": str(getattr(card, "id", "") or ""),
        "name": str(getattr(card, "name", "") or ""),
        "label": str(getattr(card, "display_name", "") or ""),
        "suit": getattr(card, "suit", None),
        "rank": getattr(card, "rank", None),
        "category": str(getattr(card, "category", "") or ""),
        "subtype": getattr(card, "subtype", None),
        "nature": str(getattr(card, "nature", "normal") or "normal"),
        "attack_range": int(getattr(card, "attack_range", 1) or 1),
        "description": getattr(card, "description", None),
    }


@dataclass(frozen=True)
class ViewCard:
    """一张牌在客户端眼里的样子（稳定的 card_id + 展示所需的牌面）。"""

    card_id: str = ""
    name: str = ""
    label: str = ""
    suit: str = None
    rank: str = None
    category: str = ""
    subtype: str = None
    nature: str = "normal"
    attack_range: int = 1
    description: str = None
    #: 内容未知的牌（例如顺手牵羊看到的对方手牌）：只画牌背，牌面字段为空。
    face_down: bool = False

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            card_id=str(payload.get("card_id") or ""),
            name=str(payload.get("name") or ""),
            label=str(payload.get("label") or ""),
            suit=payload.get("suit"),
            rank=payload.get("rank"),
            category=str(payload.get("category") or ""),
            subtype=payload.get("subtype"),
            nature=str(payload.get("nature") or "normal"),
            attack_range=int(payload.get("attack_range") or 1),
            description=payload.get("description"),
            face_down=bool(payload.get("face_down")),
        )

    def to_payload(self):
        return {
            "card_id": self.card_id,
            "name": self.name,
            "label": self.label,
            "suit": self.suit,
            "rank": self.rank,
            "category": self.category,
            "subtype": self.subtype,
            "nature": self.nature,
            "attack_range": self.attack_range,
            "description": self.description,
            "face_down": self.face_down,
        }

    @property
    def display_name(self):
        return self.label or self.name


def face_down_card(card_id=""):
    """只有 id、没有牌面的牌（内容未知）。"""

    return ViewCard(card_id=str(card_id or ""), face_down=True)


# ==================================================
# 角色视图
# ==================================================

VIEW_SLOTS = ("weapon", "armor", "defensive_horse", "offensive_horse")


@dataclass
class PlayerView:
    """一名角色**公开可见**的部分；``hand`` 只在自己身上有内容。"""

    player_id: str = ""
    seat: int = 0
    nickname: str = ""
    general_id: str = None
    general_name: str = ""
    hp: int = 0
    max_hp: int = 0
    alive: bool = True
    gender: str = ""
    kingdom: str = ""
    chained: bool = False
    controller: str = "ai"
    is_self: bool = False
    is_host: bool = False
    #: 只包含"这名观众有权看见"的身份；``None`` 表示未知。
    identity: str = None
    hand_count: int = 0
    hand: tuple = ()
    equipment: dict = field(default_factory=dict)
    judge_area: tuple = ()
    skills: tuple = ()

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        equipment = {}
        raw_equipment = payload.get("equipment")
        if isinstance(raw_equipment, dict):
            for slot, entry in raw_equipment.items():
                if entry:
                    equipment[slot] = ViewCard.from_payload(entry)
        return cls(
            player_id=str(payload.get("player_id") or ""),
            seat=int(payload.get("seat") or 0),
            nickname=str(payload.get("nickname") or ""),
            general_id=payload.get("general_id"),
            general_name=str(payload.get("general_name") or ""),
            hp=int(payload.get("hp") or 0),
            max_hp=int(payload.get("max_hp") or 0),
            alive=bool(payload.get("alive")),
            gender=str(payload.get("gender") or ""),
            kingdom=str(payload.get("kingdom") or ""),
            chained=bool(payload.get("chained")),
            controller=str(payload.get("controller") or "ai"),
            is_self=bool(payload.get("is_self")),
            is_host=bool(payload.get("is_host")),
            identity=payload.get("identity"),
            hand_count=int(payload.get("hand_count") or 0),
            hand=tuple(ViewCard.from_payload(item) for item in payload.get("hand") or ()),
            equipment=equipment,
            judge_area=tuple(
                ViewCard.from_payload(item) for item in payload.get("judge_area") or ()),
            skills=tuple(str(item) for item in payload.get("skills") or ()),
        )

    def to_payload(self):
        return {
            "player_id": self.player_id,
            "seat": self.seat,
            "nickname": self.nickname,
            "general_id": self.general_id,
            "general_name": self.general_name,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "alive": self.alive,
            "gender": self.gender,
            "kingdom": self.kingdom,
            "chained": self.chained,
            "controller": self.controller,
            "is_self": self.is_self,
            "is_host": self.is_host,
            "identity": self.identity,
            "hand_count": self.hand_count,
            "hand": [card.to_payload() for card in self.hand],
            "equipment": {
                slot: (card.to_payload() if card is not None else None)
                for slot, card in self.equipment.items()
            },
            "judge_area": [card.to_payload() for card in self.judge_area],
            "skills": list(self.skills),
        }

    def equipment_card(self, slot):
        return self.equipment.get(slot)

    def get_equipment(self, slot):
        return self.equipment.get(slot)


# ==================================================
# 当前动作 / 响应 / 判定 / 选择
# ==================================================

@dataclass
class ActionView:
    """桌面上正在被表现的那次出牌（语义牌，不是来源实体牌）。"""

    actor_id: str = ""
    label: str = ""
    card: ViewCard = None
    targets: tuple = ()
    skill_name: str = ""
    #: View-As 的来源实体牌（只在观众有权看见时才有内容）。
    sources: tuple = ()
    virtual: bool = False

    @classmethod
    def from_payload(cls, payload):
        if not payload:
            return None
        return cls(
            actor_id=str(payload.get("actor_id") or ""),
            label=str(payload.get("label") or ""),
            card=(ViewCard.from_payload(payload["card"]) if payload.get("card") else None),
            targets=tuple(str(item) for item in payload.get("targets") or ()),
            skill_name=str(payload.get("skill_name") or ""),
            sources=tuple(
                ViewCard.from_payload(item) for item in payload.get("sources") or ()),
            virtual=bool(payload.get("virtual")),
        )

    def to_payload(self):
        return {
            "actor_id": self.actor_id,
            "label": self.label,
            "card": self.card.to_payload() if self.card is not None else None,
            "targets": list(self.targets),
            "skill_name": self.skill_name,
            "sources": [card.to_payload() for card in self.sources],
            "virtual": self.virtual,
        }


@dataclass
class ResponseView:
    player_id: str = ""
    label: str = ""
    card: ViewCard = None

    @classmethod
    def from_payload(cls, payload):
        if not payload:
            return None
        return cls(
            player_id=str(payload.get("player_id") or ""),
            label=str(payload.get("label") or ""),
            card=(ViewCard.from_payload(payload["card"]) if payload.get("card") else None),
        )

    def to_payload(self):
        return {
            "player_id": self.player_id,
            "label": self.label,
            "card": self.card.to_payload() if self.card is not None else None,
        }


@dataclass
class JudgeView:
    """判定展示所需的全部信息（规则语义来自 ``judge_presentation`` 声明表）。"""

    reason: str = ""
    judged_player_id: str = ""
    stage: str = "revealed"
    card: ViewCard = None
    source_label: str = ""
    source_kind: str = ""
    rule_text: str = ""
    tone: str = "neutral"
    outcome_title: str = ""
    outcome_text: str = ""
    replacements: tuple = ()

    @classmethod
    def from_payload(cls, payload):
        if not payload:
            return None
        return cls(
            reason=str(payload.get("reason") or ""),
            judged_player_id=str(payload.get("judged_player_id") or ""),
            stage=str(payload.get("stage") or "revealed"),
            card=(ViewCard.from_payload(payload["card"]) if payload.get("card") else None),
            source_label=str(payload.get("source_label") or ""),
            source_kind=str(payload.get("source_kind") or ""),
            rule_text=str(payload.get("rule_text") or ""),
            tone=str(payload.get("tone") or "neutral"),
            outcome_title=str(payload.get("outcome_title") or ""),
            outcome_text=str(payload.get("outcome_text") or ""),
            replacements=tuple(
                (str(item.get("player_id") or ""), str(item.get("skill_name") or ""),
                 ViewCard.from_payload(item.get("old") or {}),
                 ViewCard.from_payload(item.get("new") or {}))
                for item in payload.get("replacements") or ()
            ),
        )

    def to_payload(self):
        return {
            "reason": self.reason,
            "judged_player_id": self.judged_player_id,
            "stage": self.stage,
            "card": self.card.to_payload() if self.card is not None else None,
            "source_label": self.source_label,
            "source_kind": self.source_kind,
            "rule_text": self.rule_text,
            "tone": self.tone,
            "outcome_title": self.outcome_title,
            "outcome_text": self.outcome_text,
            "replacements": [
                {"player_id": player_id, "skill_name": skill_name,
                 "old": old.to_payload(), "new": new.to_payload()}
                for player_id, skill_name, old, new in self.replacements
            ],
        }


@dataclass
class PoolEntryView:
    """公共区的一格（五谷 / 顺手看到的手牌）：牌 + 可选的来源槽位 key。"""

    card: ViewCard = None
    key: str = None

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(card=ViewCard.from_payload(payload.get("card") or {}),
                   key=payload.get("key"))

    def to_payload(self):
        return {"card": self.card.to_payload(), "key": self.key}


@dataclass
class SelectionView:
    """当前选牌界面（只发给正在做这个决定的玩家）。"""

    zone: str = "hand"
    prompt: str = ""
    number: int = 1
    candidates: tuple = ()
    selected_ids: tuple = ()
    #: 引擎给出的选择原因（``huogong_reveal`` / ``huogong_discard`` 一类）：
    #: UI 据此决定要不要显示专用界面。规则合法性仍只看 ``candidates``。
    reason: str = ""
    #: 这次选择里已经**公开亮出**的牌（火攻的展示牌）。只发给正在做这个选择
    #: 的人，而且它在规则上本来就是公开信息。
    revealed: object = None
    revealed_player_id: str = ""
    caster_id: str = ""

    @classmethod
    def from_payload(cls, payload):
        if not payload:
            return None
        return cls(
            zone=str(payload.get("zone") or "hand"),
            prompt=str(payload.get("prompt") or ""),
            number=int(payload.get("number") or 0),
            candidates=tuple(
                PoolEntryView.from_payload(item) for item in payload.get("candidates") or ()),
            selected_ids=tuple(str(item) for item in payload.get("selected_ids") or ()),
            reason=str(payload.get("reason") or ""),
            revealed=(ViewCard.from_payload(payload["revealed"])
                      if payload.get("revealed") else None),
            revealed_player_id=str(payload.get("revealed_player_id") or ""),
            caster_id=str(payload.get("caster_id") or ""),
        )

    def to_payload(self):
        return {
            "zone": self.zone,
            "prompt": self.prompt,
            "number": self.number,
            "candidates": [item.to_payload() for item in self.candidates],
            "selected_ids": list(self.selected_ids),
            "reason": self.reason,
            "revealed": self.revealed.to_payload() if self.revealed is not None else None,
            "revealed_player_id": self.revealed_player_id,
            "caster_id": self.caster_id,
        }


@dataclass
class ResultView:
    """对局结果（只由房主判定，客户端只显示）。"""

    outcome: str = ""
    reason: str = ""
    winner_id: str = ""
    headline: str = ""
    rows: tuple = ()

    @classmethod
    def from_payload(cls, payload):
        if not payload:
            return None
        return cls(
            outcome=str(payload.get("outcome") or ""),
            reason=str(payload.get("reason") or ""),
            winner_id=str(payload.get("winner_id") or ""),
            headline=str(payload.get("headline") or ""),
            rows=tuple(dict(item) for item in payload.get("rows") or ()),
        )

    def to_payload(self):
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "winner_id": self.winner_id,
            "headline": self.headline,
            "rows": [dict(item) for item in self.rows],
        }


# ==================================================
# 客户端牌局视图
# ==================================================

@dataclass
class ClientGameView:
    """一名玩家有权看见的牌局（纯数据，可 JSON 往返）。"""

    match_id: str = ""
    revision: int = 0
    game_mode: str = "ffa"
    mode_label: str = ""
    local_player_id: str = ""
    host_player_id: str = ""
    current_player_id: str = ""
    current_phase: str = ""
    responding_player_id: str = ""
    message: str = ""
    game_over: bool = False
    draw_pile_count: int = 0
    discard_count: int = 0
    discard_tail: tuple = ()
    public_pool: tuple = ()
    table_cards: tuple = ()
    hand: tuple = ()
    players: tuple = ()
    action: object = None
    response: object = None
    judge: object = None
    selection: object = None
    decision: dict = None
    #: 正在等待的**共享响应阶段**（无懈）里"我"的状态：
    #: ``""`` 我没有资格 / ``"pending"`` 正在问我 / ``"passed"`` 我已放弃本轮。
    #: 只有自己的状态：谁手里有【无懈可击】、别人的候选材料一律不出现在视图里。
    response_window: dict = None
    result: object = None
    log: tuple = ()
    skills: tuple = ()

    # ---- 序列化 ----

    @classmethod
    def from_payload(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            match_id=str(payload.get("match_id") or ""),
            revision=int(payload.get("revision") or 0),
            game_mode=str(payload.get("game_mode") or "ffa"),
            mode_label=str(payload.get("mode_label") or ""),
            local_player_id=str(payload.get("local_player_id") or ""),
            host_player_id=str(payload.get("host_player_id") or ""),
            current_player_id=str(payload.get("current_player_id") or ""),
            current_phase=str(payload.get("current_phase") or ""),
            responding_player_id=str(payload.get("responding_player_id") or ""),
            message=str(payload.get("message") or ""),
            game_over=bool(payload.get("game_over")),
            draw_pile_count=int(payload.get("draw_pile_count") or 0),
            discard_count=int(payload.get("discard_count") or 0),
            discard_tail=tuple(
                ViewCard.from_payload(item) for item in payload.get("discard_tail") or ()),
            public_pool=tuple(
                ViewCard.from_payload(item) for item in payload.get("public_pool") or ()),
            table_cards=tuple(
                PoolEntryView.from_payload(item) for item in payload.get("table_cards") or ()),
            hand=tuple(ViewCard.from_payload(item) for item in payload.get("hand") or ()),
            players=tuple(PlayerView.from_payload(item) for item in payload.get("players") or ()),
            action=ActionView.from_payload(payload.get("action")),
            response=ResponseView.from_payload(payload.get("response")),
            judge=JudgeView.from_payload(payload.get("judge")),
            selection=SelectionView.from_payload(payload.get("selection")),
            decision=(dict(payload["decision"]) if payload.get("decision") else None),
            response_window=(
                dict(payload["response_window"])
                if isinstance(payload.get("response_window"), dict) else None),
            result=ResultView.from_payload(payload.get("result")),
            log=tuple(str(item) for item in payload.get("log") or ()),
            skills=tuple(str(item) for item in payload.get("skills") or ()),
        )

    def to_payload(self):
        return {
            "match_id": self.match_id,
            "revision": self.revision,
            "game_mode": self.game_mode,
            "mode_label": self.mode_label,
            "local_player_id": self.local_player_id,
            "host_player_id": self.host_player_id,
            "current_player_id": self.current_player_id,
            "current_phase": self.current_phase,
            "responding_player_id": self.responding_player_id,
            "message": self.message,
            "game_over": self.game_over,
            "draw_pile_count": self.draw_pile_count,
            "discard_count": self.discard_count,
            "discard_tail": [card.to_payload() for card in self.discard_tail],
            "public_pool": [card.to_payload() for card in self.public_pool],
            "table_cards": [item.to_payload() for item in self.table_cards],
            "hand": [card.to_payload() for card in self.hand],
            "players": [item.to_payload() for item in self.players],
            "action": self.action.to_payload() if self.action is not None else None,
            "response": self.response.to_payload() if self.response is not None else None,
            "judge": self.judge.to_payload() if self.judge is not None else None,
            "selection": self.selection.to_payload() if self.selection is not None else None,
            "decision": dict(self.decision) if self.decision else None,
            "response_window": (dict(self.response_window)
                                if self.response_window else None),
            "result": self.result.to_payload() if self.result is not None else None,
            "log": list(self.log),
            "skills": list(self.skills),
        }

    # ---- 查询（纯读） ----

    @property
    def empty(self):
        return not self.match_id or self.revision <= 0

    def player(self, player_id):
        for item in self.players:
            if item.player_id == player_id:
                return item
        return None

    @property
    def me(self):
        return self.player(self.local_player_id)

    @property
    def current_player(self):
        return self.player(self.current_player_id)

    def card(self, card_id):
        """在自己看得见的牌里找一张（手牌 → 装备 → 判定区 → 公共区）。"""

        text = str(card_id)
        for card in self.hand:
            if card.card_id == text:
                return card
        for item in self.players:
            for card in item.equipment.values():
                if card is not None and card.card_id == text:
                    return card
            for card in item.judge_area:
                if card.card_id == text:
                    return card
        for card in self.public_pool:
            if card.card_id == text:
                return card
        for item in self.table_cards:
            if item.card is not None and item.card.card_id == text:
                return item.card
        return None

    def hand_ids(self):
        return {card.card_id for card in self.hand}


# ==================================================
# 消息构造（房主发给客户端）
# ==================================================

def snapshot_message_payload(view, *, host_player_id="", base_revision=None):
    """把视图打成一条 ``GAME_VIEW_SNAPSHOT`` 的 payload。"""

    payload = view.to_payload()
    payload["host_player_id"] = host_player_id or payload.get("host_player_id", "")
    payload["snapshot"] = True
    if base_revision is not None:
        payload["base_revision"] = int(base_revision)
    return payload
