"""客户端渲染适配层：让**既有 Renderer** 读一份只读视图（Phase 11.3 §17）。

Renderer / TableLayout / seats / player / table / prompt 原本读的是权威 ``Game``。
这里给它们一个薄薄的替身 ``RemoteGameView``：

* 展示字段（玩家、座位、手牌、装备、判定区、牌堆张数、弃牌堆顶、当前玩家、
  阶段、战役日志、结算）全部来自 ``ClientGameView``；
* 交互字段（Pending / 选择 / 技能 / 响应 / 出牌动作）全部是"惰性"的，
  客户端本来就该由 DecisionRequest 决定能做什么；
* **没有任何规则**：距离、合法性、牌堆、流程都不在这里算。客户端不构造
  第二个 Game，也不推导任何权威状态。

一个关键设计：``update(view)`` 是**原地更新**。同一个 player / 同一张牌在
两次快照之间保持同一个 Python 对象，否则 FX 的闪烁、手牌矩形、选中状态都
会因为"对象换了"而整体重置。
"""

import pygame

from src.card import Card as CardData
from src.game.view.view_model import ClientGameView, PlayerView, ViewCard
from src.network.decisions import DecisionKind

from . import decision_presentation as presentation


class RemoteInteractionState:
    """客户端"我现在处在哪一步"（Phase 11.5 §17）。

    以前这些语义挤在几个 bool 里（``way_chosen`` / ``configuring_skill`` …），
    一个 bool 被拿来表达好几件事，于是"选完方式没进下一步""选了一张就提交"
    这类 bug 很难看。这里把它们收敛成**一个**明确的阶段值，UI 与提交逻辑都
    只读它。
    """

    IDLE = "idle"                        # 没有待回答的决策
    SELECTING_WAY = "selecting_way"      # 这张牌有多种用法，正在挑一种
    SELECTING_CARDS = "selecting_cards"  # 正在选来源牌 / 候选牌
    SELECTING_TARGETS = "selecting_targets"
    CHOOSING_OPTION = "choosing_option"  # 是 / 否 或 多选一
    READY_TO_SUBMIT = "ready_to_submit"
    SUBMITTED = "submitted"              # 已提交，等房主确认
    REJECTED = "rejected"                # 房主拒绝了，可以改选重来


class RemoteDecisionState:
    """客户端本地的"我正在选什么"（纯 UI 状态，权威判断在房主）。

    ``cards`` / ``targets`` 是"打出一张牌"或"选牌"的选择；``skill_*`` 是
    "发动主动技"的选择。两者互斥：正在配置技能时，点手牌 / 点角色都算技能的
    费用与目标。

    它记录的全是"玩家点了什么"，**不含任何权威结论**：这些 id 只会在提交时
    作为回答发给房主，由房主重新校验。

    ``action_id`` 是"这次用哪种方式"——房主下发的候选方式 id（普通使用 /
    【龙胆】当【杀】/ 两张手牌当【杀】…）。它取代了"按下标猜"的做法：下标
    会随候选顺序变化，id 不会；而且房主收到之后可以精确复核。
    """

    __slots__ = ("request_id", "kind", "cards", "targets", "option_index",
                 "notice", "skill_id", "skill_cards", "skill_target",
                 "way_chosen", "action_id", "result_name", "stage_override")

    def __init__(self):
        self.reset(None)

    def reset(self, request):
        self.request_id = None if request is None else request.get("request_id")
        self.kind = "" if request is None else str(request.get("kind") or "")
        self.cards = []
        self.targets = []
        self.option_index = 0
        #: 一张牌有多种用法时，玩家是否已经在"选择操作"面板里挑过一种。
        self.way_chosen = False
        #: 挑中的那种用法（房主的 action_id + 它想打出的逻辑牌名）。
        self.action_id = ""
        self.result_name = ""
        self.notice = ""
        self.skill_id = ""
        self.skill_cards = []
        self.skill_target = ""
        #: 由场景写入的"这一步被房主拒绝过"（面板保留，玩家可以重来）。
        self.stage_override = ""

    def clear_selection(self):
        self.cards = []
        self.targets = []
        self.option_index = 0
        self.way_chosen = False
        self.action_id = ""
        self.result_name = ""
        self.notice = ""

    def choose_way(self, option, index=None):
        """选定一种用法（房主下发的条目）。"""

        self.action_id = str((option or {}).get("action_id") or "")
        self.result_name = str((option or {}).get("result_name") or "")
        self.skill_id = str((option or {}).get("skill_id") or "")
        if index is not None:
            self.option_index = int(index)
        self.way_chosen = True
        return self.action_id

    @property
    def empty(self):
        return self.request_id is None

    @property
    def configuring_skill(self):
        return bool(self.skill_id) and self.way_chosen is False

    def stage(self, request=None, *, answered=False, rejected=False):
        """当前所处的阶段（见 :class:`RemoteInteractionState`）。"""

        if rejected:
            return RemoteInteractionState.REJECTED
        if answered:
            return RemoteInteractionState.SUBMITTED
        if request is None:
            return RemoteInteractionState.IDLE
        kind = str((request or {}).get("kind") or self.kind or "")
        if self.configuring_skill:
            return RemoteInteractionState.SELECTING_TARGETS \
                if self.skill_target or self.skill_cards \
                else RemoteInteractionState.SELECTING_CARDS
        if kind in (DecisionKind.CONFIRM, DecisionKind.CHOOSE_OPTION):
            return RemoteInteractionState.CHOOSING_OPTION
        if kind == DecisionKind.SELECT_TARGETS:
            return RemoteInteractionState.SELECTING_TARGETS
        if kind == DecisionKind.PLAY_PHASE:
            if not self.cards:
                return RemoteInteractionState.SELECTING_CARDS
            if not self.way_chosen:
                return RemoteInteractionState.SELECTING_WAY
        return RemoteInteractionState.SELECTING_CARDS

#: 客户端本地的节奏档位（纯显示设置，与房主的节奏无关）。
SPEED_STEPS = (0.4, 0.55, 0.75, 1.0, 1.5, 2.0)
SPEED_LABELS = {0.4: "很慢", 0.55: "慢", 0.75: "稍慢", 1.0: "正常", 1.5: "快", 2.0: "很快"}

SLOT_ORDER = ("weapon", "armor", "defensive_horse", "offensive_horse")


# ==================================================
# 牌：把视图里的牌面还原成展示用 Card（稳定 id + 稳定对象）
# ==================================================

class CardCache:
    """``card_id → Card`` 的稳定映射。

    ``Card.id`` 会被改写成房主的稳定 id，所以客户端提交决策时给出的 card_id
    与服务端的牌是同一个身份。同一个 card_id 永远返回同一个对象：快照重发
    不会让手牌矩形、选中状态、动画所有权全部重置。
    """

    def __init__(self):
        self._cards = {}
        self._placeholders = 0

    def get(self, entry):
        if entry is None:
            return None
        if isinstance(entry, ViewCard):
            card_id, payload = entry.card_id, entry
        else:
            card_id = str(entry.get("card_id") or "")
            payload = entry
        if not card_id:
            return None
        card = self._cards.get(card_id)
        if card is None:
            card = self._build(card_id, payload)
            self._cards[card_id] = card
        return card

    @staticmethod
    def _build(card_id, payload):
        get = (payload.get if isinstance(payload, dict) else
               (lambda key, default=None: getattr(payload, key, default)))
        face_down = bool(get("face_down"))
        card = CardData(
            name="" if face_down else str(get("name") or ""),
            category="" if face_down else str(get("category") or ""),
            color=(0, 0, 0),
            nature=str(get("nature") or "normal"),
            subtype=get("subtype"),
            attack_range=int(get("attack_range") or 1),
            suit=get("suit"),
            rank=get("rank"),
            description=get("description"),
        )
        card.id = card_id
        card.face_down = face_down
        return card

    def placeholder(self):
        """一张"内容未知"的占位牌：只用于表现（别人的摸牌动画画牌背）。

        它没有真实 card_id，也不会进入任何交互集合——纯粹为了让"某人摸了两张
        牌"这件事有东西可画。
        """

        self._placeholders += 1
        card = CardData(name="", category="", color=(0, 0, 0))
        card.id = "hidden-%d" % self._placeholders
        card.face_down = True
        return card

    def clear(self):
        self._cards.clear()


# ==================================================
# 玩家
# ==================================================

class ViewPlayer:
    """占位角色对象：只提供既有绘制路径需要的展示字段。"""

    __slots__ = ("player_id", "seat", "name", "alive", "hp", "max_hp", "hand",
                 "equipment", "judgement_zone", "chained", "general_id", "identity",
                 "identity_revealed", "controller_type", "hand_count", "gender",
                 "kingdom", "skills")

    def __init__(self, player_id="", seat=0, name=""):
        self.player_id = player_id
        self.seat = seat
        self.name = name
        self.alive = True
        self.hp = 0
        self.max_hp = 0
        self.hand = []
        self.equipment = {}
        self.judgement_zone = ()
        self.chained = False
        self.general_id = None
        self.identity = None
        self.identity_revealed = False
        self.controller_type = "remote_human"
        self.hand_count = 0
        self.gender = ""
        self.kingdom = ""
        self.skills = ()

    # ---- 既有绘制路径需要的接口 ----

    def get_equipment(self, slot):
        return self.equipment.get(slot)

    @property
    def is_human(self):
        return self.controller_type == "human"

    @property
    def is_ai(self):
        return self.controller_type == "ai"

    # ---- 原地更新（保持对象身份） ----

    def apply(self, view: PlayerView, cards: CardCache):
        self.player_id = view.player_id
        self.seat = view.seat
        self.name = view.nickname
        self.alive = view.alive
        self.hp = view.hp
        self.max_hp = view.max_hp
        self.hand_count = view.hand_count
        self.chained = view.chained
        self.general_id = view.general_id
        self.identity = view.identity
        # 身份公开与否只由房主决定：房主给了字符串就是公开的。
        self.identity_revealed = view.identity is not None
        self.controller_type = view.controller
        self.gender = view.gender
        self.kingdom = view.kingdom
        self.skills = view.skills
        if view.hand:
            self.hand = [cards.get(card) for card in view.hand]
        elif view.is_self:
            self.hand = []
        else:
            # 别人的手牌只有张数：本地用等量占位牌表示"有这么多张"，
            # 内容永远是空的（房主根本没发）。
            self.hand = [None] * view.hand_count
        self.equipment = {
            slot: cards.get(card) for slot, card in view.equipment.items()
        }
        self.judgement_zone = tuple(cards.get(card) for card in view.judge_area)
        return self

    def __repr__(self):                             # pragma: no cover - 调试用
        return "ViewPlayer(%s, seat=%d, hp=%d)" % (self.name, self.seat, self.hp)


# ==================================================
# 牌堆 / 模式 / 技能 / 交互占位
# ==================================================

class ViewDeck:
    """牌堆与弃牌堆的只读替身（只给张数与弃牌堆顶，没有顺序信息）。

    ``draw_pile`` / ``discard_pile`` 是**原地更新**的列表：表现层用"区域对象
    的身份"来解析摸牌动画的起点（``fx.Effects._origin_key``），每次快照换一个
    新列表会让起点匹配失败、动画退回屏幕中央。
    """

    def __init__(self):
        self.draw_pile = []
        self.discard_pile = []
        self.draw_count = 0
        self.discard_count = 0
        self._cards = CardCache()

    def apply(self, view: ClientGameView, cards: CardCache):
        self._cards = cards
        self.draw_count = int(view.draw_pile_count)
        self.discard_count = int(view.discard_count)
        self.draw_pile[:] = [None] * self.draw_count
        self.discard_pile[:] = [cards.get(card) for card in view.discard_tail]
        return self


class ViewMode:
    """身份显示：房主已经把"你能不能看到"过滤好了，这里只做文案。"""

    def __init__(self):
        self.uses_identities = False
        self.name = ""
        self._rows = ()
        self._headline = ""
        self._self_id = ""
        self._player_id = ""

    def apply(self, view: ClientGameView):
        from src.game.identity import identity_name

        self.uses_identities = view.game_mode != "ffa"
        self.name = view.mode_label
        self._player_id = view.local_player_id
        self._rows = tuple(view.result.rows) if view.result is not None else ()
        self._headline = view.result.headline if view.result is not None else ""
        self._identity_names = {
            item.player_id: identity_name(item.identity)
            for item in view.players
        }
        return self

    def identity_label(self, player):
        """只返回房主允许这项客户端看见的身份（没发就是空串）。"""

        return self._identity_names.get(getattr(player, "player_id", ""), "")

    def result_lines(self):
        return self._rows

    def result_headline(self):
        return self._headline


class ViewSkills:
    """技能只读查询：技能名是公开信息，能不能发动由房主决定。

    "能不能按"完全来自房主下发的那条决策请求（``activatable`` 列表），
    所以技能栏的高亮与单机一样是数据驱动的——只是数据来源换成了房主。
    """

    def __init__(self, registry, view=None):
        self._registry = registry
        self._view = view
        self._by_player = {}
        self._self_id = ""

    def apply(self, view: ClientGameView):
        self._self_id = view.local_player_id
        self._by_player = {
            item.player_id: tuple(item.skills) for item in view.players
        }
        return self

    def skill_ids_of(self, player):
        return self._by_player.get(getattr(player, "player_id", ""), ())

    def has(self, player, skill_id):
        return skill_id in self.skill_ids_of(player)

    def can_activate(self, player, skill_id):
        view = self._view
        if view is None:
            return False, "由房主判定"
        return view.skill_activation_state(skill_id)

    def view_as_skill_ids(self, player):
        # 视为技在本项目里是"牌的一种用法"，不是技能栏里的一个按钮，
        # 因此客户端不向技能栏暴露它们（与本地 UI 的牌方式面板一致）。
        return ()


class PendingSlot:
    """``game.response`` / ``game.choice`` 的替身。

    默认"没有在等"（客户端本来就不该有本地响应状态）；房主的决策请求到了
    之后由 ``RemoteGameView.apply_decision`` 填上 ``current``，于是
    ``prompt.describe`` / ``Renderer.actions_for`` 这些既有代码照常工作，
    客户端不需要第二套提示逻辑。
    """

    def __init__(self, current=None):
        self.current = current

    @property
    def active(self):
        return self.current is not None

    def clear(self):
        self.current = None


class InertRequest:
    """``game.pending_request`` 的替身：只用于"谁在响应"的高亮。"""

    def __init__(self, target=None, prompt=""):
        self.target = target
        self.prompt = prompt
        self.source = None
        self.allowed_cards = ()

    def __bool__(self):
        return self.target is not None


class ResponseRequest:
    """轮到"我"打出响应牌时的提示数据（提示条 / 手牌高亮共用）。"""

    def __init__(self, prompt="", allowed_cards=(), reason=""):
        self.title = "需要你的响应"
        self.prompt = prompt
        self.allowed_cards = tuple(allowed_cards)
        #: 语义标签（"wuxie_chain" / "sha"…）：提示层据此换标题，
        #: 不参与任何规则判断。
        self.reason = str(reason or "")


class ChoiceRequestView:
    """是 / 否 与多选一：``src.choice.ChoiceOverlay`` 直接认识的同构数据。"""

    def __init__(self, title, prompt, yes_label, no_label, cancel_label=""):
        self.title = title
        self.prompt = prompt
        self.yes_label = yes_label
        self.no_label = no_label
        self.cancel_label = cancel_label


class CardWayOption:
    """一个"这次的用法"（普通使用 / 技能转化）——``CardActionPicker`` 的同构数据。

    字段与 ``CardActionOption`` 对齐：``CardActionPicker`` 只读 label / detail /
    enabled / is_conversion / describe_sources()，所以客户端的"选择操作"面板
    与单机长得完全一样。

    ``action_id`` 用的是**房主下发的字符串 id**（不是面板里的下标）：玩家点
    哪一行，客户端就回传哪一行的 id，房主据此重新解析这次操作。多来源转化
    （两张手牌当【杀】）还要带上 ``min_sources`` / ``max_sources``。
    """

    def __init__(self, index, entry, payload):
        self.index = int(index)
        #: 面板顺序（''CardActionPicker.hit`` 返回下标，但对外用 action_id）。
        self.action_id = str(payload.get("action_id") or index)
        self.card = entry
        self.label = str(payload.get("label") or "使用")
        self.detail = str(payload.get("detail") or "")
        self.enabled = bool(payload.get("enabled", True))
        self.is_conversion = bool(payload.get("is_conversion"))
        self.skill_id = str(payload.get("skill_id") or "")
        self.skill_name = str(payload.get("skill_name") or "")
        self.result_name = str(payload.get("result_name") or "")
        self.result_display = str(payload.get("result_display") or "")
        self.min_sources = int(payload.get("min_sources") or 1)
        self.max_sources = int(payload.get("max_sources") or 1)
        self.min_targets = int(payload.get("min_targets") or 0)
        self.max_targets = int(payload.get("max_targets") or 0)
        self.target_entries = list(payload.get("targets") or ())
        self.disabled_reason = str(payload.get("disabled_reason") or "")
        self.complete = False

    def describe_sources(self):
        """来源描述（面板副标题用）：只报牌名，不涉及任何规则。"""

        names = []
        for item in self.card.get("sources") or ():
            label = str(item.get("label") or item.get("name") or "")
            if label and label not in names:
                names.append(label)
        name = str(self.card.get("label") or self.card.get("name") or "")
        if name and name not in names:
            names.insert(0, name)
        return " ＋ ".join(names)


class ViewSpeed:
    """客户端本地的节奏档位（纯显示，不影响房主）。"""

    def __init__(self):
        self.index = 3

    def slower(self):
        self.index = max(0, self.index - 1)
        return self.value

    def faster(self):
        self.index = min(len(SPEED_STEPS) - 1, self.index + 1)
        return self.value

    @property
    def value(self):
        return SPEED_STEPS[self.index]

    def label(self):
        return SPEED_LABELS.get(self.value, "")


# ==================================================
# 视图替身
# ==================================================

class RemoteGameView:
    """把 ``ClientGameView`` 适配成既有渲染层认识的"牌局"。

    它**不是** Game：没有牌堆随机、没有流程、没有技能、没有 AI，也不能改
    任何权威状态。所有"能不能做"的问题都由房主的 DecisionRequest 回答。
    """

    #: 既有渲染层据此判断"这是一份只读视图，没有本地交互"（不算距离、
    #: 不查 Card Action Discovery、不提交任何动作）。
    local_interaction = False
    #: 但**界面层**要画：提示条 / 固定按钮 / 选择面板由房主的决策请求驱动，
    #: 槽位形状与本地引擎同构，所以客户端与单机长得一样。
    interaction_layers = True

    def __init__(self, view=None, *, host_player_id="", skill_registry=None,
                 general_registry=None):
        from src.game.generals import create_default_general_registry
        from src.game.skills import create_default_skill_registry

        self.cards = CardCache()
        self.skill_registry = skill_registry or create_default_skill_registry()
        self.generals = general_registry or create_default_general_registry()
        self.skills = ViewSkills(self.skill_registry, self)
        self.deck = ViewDeck()
        self.mode = ViewMode()
        self.speed_state = ViewSpeed()
        self.SPEED_STEPS = SPEED_STEPS
        self.response = PendingSlot()
        self.choice = PendingSlot()
        self.actions = None                 # 由客户端场景注入本地动画队列
        self.match_id = ""
        self.network = True
        self.is_remote_view = True
        self.host_player_id = str(host_player_id or "")
        self._players = {}
        self._player_order = []
        self.players = []
        self.player = ViewPlayer()
        self._selection_face_down = set()
        self.revision = 0
        self.view = ClientGameView()
        #: 当前决策允许点选的手牌 id 集合（由房主下发，客户端不自己算）。
        self.allowed_card_ids = None
        self.pending_selection = None
        self.pending_target_selection = None
        self.pending_skill_input = None
        self.pending_view_as = None
        self.pending_card_action = None
        self.pending_skill_picker = None
        #: 房主发来的当前决策（纯数据）+ 本地"我选了什么"（由客户端场景注入）。
        self.decision = None
        self._way_options = ()
        self._way_index = 0
        self._way_picker_open = False
        self.deal_presentation = None
        self.ui_rects = None
        self.ui_metrics = None
        self.table_cards = ()
        self.public_card_pool = []
        self.game_log = []
        self.game_over = False
        self.message = ""
        self.result = None
        self.winner = None
        self.phase = ""
        self.current_player_id = ""
        self.current_turn_player = None
        self.pending_request = None
        self.busy = False
        self.network_notice = ""
        if view is not None:
            self.update(view)
        else:
            self.player = ViewPlayer()
            self.players = []

    # ==================================================
    # 更新（原地，保持对象身份）
    # ==================================================

    def update(self, view: ClientGameView, *, new_revision=True):
        self.view = view
        self.match_id = view.match_id
        self.revision = int(view.revision)
        self.phase = view.current_phase
        self.current_player_id = view.current_player_id
        self.message = view.message
        self.game_over = bool(view.game_over)
        self.game_log = list(view.log)
        self._selection_face_down = set()
        self.pending_selection = None
        self._build_players(view)
        self.deck.apply(view, self.cards)
        self.mode.apply(view)
        self.skills.apply(view)
        self.table_cards = [
            (self.cards.get(item.card), item.key or "table_card")
            for item in view.table_cards if item.card is not None
        ]
        # 公共池同样原地更新：它是"公共池取牌"动画起点的区域标识。
        self.public_card_pool[:] = [
            self.cards.get(card) for card in view.public_pool]
        self.current_turn_player = self._players.get(view.current_player_id)
        self.pending_request = (
            InertRequest(self._players.get(view.responding_player_id))
            if view.responding_player_id else None)
        self.result = ViewResult.for_view(view, self._players)
        self.winner = (self._players.get(view.result.winner_id)
                       if view.result is not None else None)
        self._apply_selection(view)
        return self

    def _build_players(self, view):
        order = []
        for item in view.players:
            player = self._players.get(item.player_id)
            if player is None:
                player = ViewPlayer(item.player_id, item.seat, item.nickname)
                self._players[item.player_id] = player
            player.apply(item, self.cards)
            order.append(player)
        self._player_order = order
        self.players = order
        local = self._players.get(view.local_player_id)
        if local is None:
            local = order[0] if order else ViewPlayer()
            self._players[view.local_player_id] = local
        self.player = local

    def _apply_selection(self, view):
        """房主的选牌界面 → 既有绘制路径认识的 ``pending_selection`` 结构。

        只有正在做这个选择的玩家会收到 ``view.selection``；其他人的视图里
        根本没有这个字段（房主没发）。
        """

        selection = view.selection
        if selection is None:
            return
        candidates = []
        face_down = set()
        for item in selection.candidates:
            card = self.cards.get(item.card)
            candidates.append((card, item.key))
            if item.card is not None and item.card.face_down:
                face_down.add(id(card))
        selected = []
        for card_id in selection.selected_ids:
            for card, key in candidates:
                if card is not None and card.id == card_id:
                    selected.append((card, pygame.Rect(0, 0, 10, 10), key))
                    break
        self._selection_face_down = face_down
        self.pending_selection = {
            "zone": selection.zone,
            "owner": self.player,
            "candidates": candidates,
            "number": selection.number,
            "prompt": selection.prompt,
            "selected": selected,
            "on_complete": None,
            "request_id": None,
            "cancellable": True,
            "face_down_ids": face_down,
        }

    # ==================================================
    # 既有渲染路径会调用的只读查询
    # ==================================================

    def selection_pool_entries(self):
        """公共区里要画的候选牌。

        判定标准是"'这张牌现在不在我手上'——顺手牵羊看到的对方手牌、装备区
        的候选、五谷的公共牌都走这里；它们要么是公开的，要么只画牌背
        （由 ``selection_face_down_ids`` 决定）。
        """

        selection = self.pending_selection
        if selection is None:
            return []
        mine = {id(card) for card in self.player.hand if card is not None}
        return [(card, key) for card, key in selection["candidates"]
                if id(card) not in mine]

    def is_selection_candidate(self, card, key=None):
        selection = self.pending_selection
        if selection is None:
            return False
        return any(item is card and item_key == key
                   for item, item_key in selection["candidates"])

    def is_selection_selected(self, card, key=None):
        selection = self.pending_selection
        if selection is None:
            return False
        return any(item is card and item_key == key
                   for item, _rect, item_key in selection["selected"])

    def can_cancel_pending_selection(self):
        return True

    def network_playable_indices(self):
        """由**房主给的合法牌集合**决定手牌高亮，客户端不自己算规则。

        ``None`` 表示"不灰化"（现在不是我决定的时候）。
        """

        allowed = getattr(self, "allowed_card_ids", None)
        if allowed is None:
            return None
        return {
            index for index, card in enumerate(self.player.hand)
            if card is not None and card.id in allowed
        }

    # ---- 操作权限（与单机同一接口，见 ui.prompt.local_can_play）----

    def local_can_play(self):
        """客户端现在能不能主动出牌 / 结束回合。

        判据是"**手里有一条尚未回答的出牌阶段决策**"，而不是"我的回合 + 出牌
        阶段 + 没在播动画"：``phase`` / ``current_turn_player`` 是房主快照里的
        值，房主正在等别人响应、正在结算锦囊、或者还没把面板发过来时，它们
        照样成立——那时客户端什么都提交不了，界面也不该开放操作。
        """

        decision = self.decision
        if not isinstance(decision, dict):
            return False
        return str(decision.get("kind") or "") == DecisionKind.PLAY_PHASE

    @property
    def response_window(self):
        """房主下发的共享响应阶段（无懈）里"我"的状态（只读，可能为 None）。"""

        return getattr(self.view, "response_window", None)

    def card_action_picker(self):
        """"选择操作"面板：当前选中的牌有多个可用方式时列出它们。

        ``CardActionPicker`` 直接认识这些同构对象，因此客户端的多方式选择
        与单机是同一个面板（普通使用 / 【龙胆】当【杀】使用…）。
        """

        if not self._way_picker_open:
            return []
        return [item for item in self._way_options if item.enabled]

    def card_action_ready(self):
        """本地选择是否已经凑齐（来源牌够、目标数够）。"""

        state = self.pending_card_action
        if state is None or state["picker"]:
            return False
        option = state["option"]
        if option is None:
            return False
        if len(state["selected"]) < option.min_sources:
            return False
        targets = self.pending_target_selection
        if targets is not None:
            return len(targets["selected"]) >= targets["minimum"]
        return option.max_targets <= 0

    def card_action_source_ids(self):
        """被选作来源的手牌 id（本地真人的手牌上浮也用这一套）。"""

        if self.decision is None:
            return set()
        return set(getattr(self, "selected_source_ids", ()) or ())

    def view_as_options(self):
        # 视为技由"牌的方式"表达（每张牌的 options 里有转换条目），
        # 客户端不再单独维护一套"先点技能再选牌"的模式。
        return ()

    # ==================================================
    # 决策 → 桌面交互槽位（Phase 11.4 §7）
    # ==================================================

    def player_by_id(self, player_id):
        return self._players.get(str(player_id or ""))

    def display_card(self, entry):
        """一条请求候选 → 展示用 Card（同一 id 永远同一个对象）。

        候选可能是"只有 id、没有牌面"的隐藏牌（顺手牵羊看到的对方手牌）：
        那种情况下 CardCache 造出的牌 ``face_down`` 为真，渲染层只画牌背。
        """

        if entry is None:
            return None
        if isinstance(entry, str):
            entry = {"card_id": entry}
        return self.cards.get(entry)

    def selection_face_down_ids(self):
        """本次选牌里只许画牌背的牌（内容未知 = 别人的手牌）。"""

        selection = self.pending_selection
        if not selection:
            return set()
        return set(selection.get("face_down_ids") or ())

    def apply_decision(self, request, selection=None):
        """把房主的决策请求翻译成渲染层已经认识的交互状态。

        ``request`` 是 ``DecisionRequest`` 的 payload（dict，可能为 None）；
        ``selection`` 是客户端本地的"我选了什么"（``RemoteDecisionState``：
        ``cards`` / ``targets`` / ``option_index``）。两者合起来决定
        这一帧的高亮、候选与按钮。

        这一步**不产生任何权威信息**：候选与目标都只来自请求本身。
        """

        self.decision = dict(request) if request else None
        self.pending_selection = None
        self.pending_target_selection = None
        self.pending_card_action = None
        self.pending_skill_input = None
        self._way_options = ()
        self._way_picker_open = False
        self.response.clear()
        self.choice.clear()
        self.selected_source_ids = set()
        self.allowed_card_ids = None

        request = self.decision
        if not request:
            return self

        my_id = str(getattr(self.player, "player_id", "") or self.view.local_player_id)
        chosen_cards = [str(item) for item in getattr(selection, "cards", ()) or ()]
        chosen_targets = [str(item) for item in getattr(selection, "targets", ()) or ()]
        hand_ids = {card.id for card in self.player.hand if card is not None}
        self.allowed_card_ids = presentation.hand_allowed_ids(request, hand_ids)

        kind = request.get("kind")
        self.pending_selection = presentation.selection_presentation(
            request, resolve_card=self.display_card, my_player_id=my_id,
            owner=self.player, selected_ids=chosen_cards)

        # 正在配置主动技：费用牌与目标都归技能，牌桌不再显示"出牌"那一套。
        # （"选了方式"也会带 skill_id，所以判定要看"是不是在配置技能"，
        # 不看 skill_id 是否为空——鸭子类型的状态对象用同样的规则兜底。）
        entry = None
        configuring = getattr(selection, "configuring_skill", None)
        if configuring is None:
            configuring = bool(getattr(selection, "skill_id", "")) \
                and not bool(getattr(selection, "way_chosen", False))
        if configuring:
            entry = presentation.skill_entry(request, getattr(selection, "skill_id", ""))
        if entry is not None:
            self._apply_skill(request, entry, selection)
            return self

        if kind == DecisionKind.SELECT_TARGETS:
            self.pending_target_selection = presentation.targets_presentation(
                request, resolve_player=self.player_by_id, selected_ids=chosen_targets)
        elif kind == DecisionKind.RESPOND_CARD:
            payload = presentation.response_presentation(request)
            if payload is not None:
                self.response.current = ResponseRequest(
                    payload["prompt"], payload["allowed_cards"],
                    reason=payload.get("reason", ""))
            self._apply_card_way(request, selection, chosen_cards, chosen_targets)
        elif kind == DecisionKind.CHOOSE_OPTION:
            self._apply_choice(request)
        elif kind == DecisionKind.CONFIRM:
            self.choice.current = ChoiceRequestView("请确认", request.get("prompt", ""),
                                                    "确定", "取消")
        elif kind == DecisionKind.PLAY_PHASE:
            self._apply_card_way(request, selection, chosen_cards, chosen_targets)

        return self

    def _apply_skill(self, request, entry, selection):
        """主动技 → ``pending_skill_input``（本地同一套"发动技能"界面）。

        高亮的座位是房主给的合法目标，可点的手牌是房主给的费用候选；进度
        文案（"已选 1/2 张"）由 ``prompt.describe`` 从这份状态里算，和单机
        逐字一致。
        """

        chosen_cards = {str(item) for item in getattr(selection, "skill_cards", ()) or ()}
        target_id = str(getattr(selection, "skill_target", "") or "")
        targets = []
        for item in entry.get("targets") or ():
            player = self.player_by_id(item.get("player_id"))
            if player is not None and player not in targets:
                targets.append(player)
        cards = [card for card in self.player.hand
                 if card is not None and card.id in chosen_cards]
        self.allowed_card_ids = {
            str(item.get("card_id") or "") for item in entry.get("cost_candidates") or ()
        } or None
        self.pending_skill_input = {
            "skill_id": entry.get("skill_id") or "",
            "name": str(entry.get("name") or ""),
            "needs_target": bool(entry.get("needs_target")),
            "target_prompt": str(entry.get("target_prompt") or ""),
            "cost_cards": int(entry.get("cost_cards") or 0),
            "cost_prompt": str(entry.get("cost_prompt") or ""),
            "variable_cost": bool(entry.get("variable_cost")),
            "transfer_cards": bool(entry.get("transfer_cards")),
            "targets": targets,
            "target": self.player_by_id(target_id) if target_id else None,
            "cards": cards,
        }

    def skill_input_ready(self):
        """技能所需的输入是否已经凑齐（固定"确认发动"按钮的可用性）。"""

        state = self.pending_skill_input
        if state is None:
            return False
        if state["needs_target"] and state["target"] is None:
            return False
        if state["variable_cost"]:
            return bool(state["cards"])
        return len(state["cards"]) >= state["cost_cards"]

    def _apply_choice(self, request):
        options = [item for item in (request.get("options") or ()) if isinstance(item, dict)]
        labels = [str(item.get("label") or "") for item in options]
        if len(labels) == 2:
            self.choice.current = ChoiceRequestView(
                "请选择", request.get("prompt", ""), labels[0], labels[1])
        else:
            self.choice.current = ChoiceRequestView(
                "请选择", request.get("prompt", ""), labels[0] if labels else "确定", "")

    def _apply_card_way(self, request, selection, chosen_cards, chosen_targets):
        """"选中的牌 + 用哪种方式" → 方式面板 / 来源收集 / 目标选择（出牌与响应共用）。

        与单机同序：一张牌有多种用法时先弹"选择操作"面板；选完之后按这种方式
        需要几张实体牌决定下一步——不够就继续收集来源（面板显示"已选择 x/N"），
        够了有目标就进目标选择，没目标就可以直接提交。

        **响应窗口**同样走这一套：两张手牌当【杀】打出是个多来源方式，选第一
        张**不能**直接提交（那正是联机响应失败的根因）。
        """

        entry = presentation.play_card_entry(request, chosen_cards)
        if entry is None:
            return
        options = presentation.play_options(entry)
        if not options:
            return
        self._way_options = [CardWayOption(index, entry, payload)
                             for index, payload in enumerate(options)]
        action_id = str(getattr(selection, "action_id", "") or "")
        chosen_way = bool(getattr(selection, "way_chosen", False))
        option = presentation.option_by_action(entry, action_id) \
            if (chosen_way and action_id) else None
        if option is None and chosen_way:
            # 老状态只记了下标（或方式 id 对不上）：退回到下标，
            # 让玩家不至于卡住不动。
            option = presentation.play_option(
                entry, getattr(selection, "option_index", 0))
        if option is None or not option.get("enabled", True):
            # 还没挑方式：多个方式才弹面板（与单机一致，单个方式直接往下走）。
            self._way_picker_open = len(self._way_options) > 1
            if self._way_picker_open:
                return
            if option is None:
                option = options[0]
        else:
            self._way_picker_open = False

        self.selected_source_ids = {card.id for card in self.player.hand
                                    if card is not None and card.id in set(chosen_cards)}
        ready = presentation.play_sources_ready(entry, option, chosen_cards)
        targets = presentation.play_targets(option)
        maximum = int(option.get("max_targets") or 0)
        if ready and targets and not self._way_picker_open:
            self.pending_target_selection = presentation.targets_presentation(
                request, resolve_player=self.player_by_id, target_entries=targets,
                selected_ids=chosen_targets,
                prompt=str(option.get("label") or request.get("prompt") or "请选择目标"),
                minimum=int(option.get("min_targets") or 0), maximum=maximum)
            return
        # 来源还没凑齐 / 这种用法没有目标：走"确认使用"面板（与单机同构），
        # 客户端据此显示"已选择 x/N 张"，点牌继续加来源。
        index = min(max(0, int(getattr(selection, "option_index", 0))),
                    len(self._way_options) - 1)
        way = next((item for item in self._way_options
                    if item.action_id == str(option.get("action_id") or "")),
                   self._way_options[index] if self._way_options else None)
        self.pending_card_action = {
            "option": way,
            "selected": [card for card in self.player.hand
                         if card is not None and card.id in set(chosen_cards)],
            "picker": None,
            "source_rect": None,
            "context": None,
        }

    def skill_activation_state(self, skill_id):
        """主动技此刻能不能按：只认房主在本次请求里列出的技能。"""

        allowed, reason = presentation.skill_activation_state(self.decision, skill_id)
        if allowed:
            return True, ""
        definition = self.skill_registry.get(skill_id)
        if definition is not None and getattr(definition, "is_view_as", False):
            # 视为技在客户端是"牌的一种用法"：不需要先按技能键。
            return False, "请直接选择要转化的牌"
        return False, reason

    # ---- 空实现：本地交互专用，客户端永远不走这些路径 ----

    def end_player_turn(self, *args, **kwargs):
        return False

    def player_use_card(self, *args, **kwargs):
        return False

    def player_discard(self, *args, **kwargs):
        return False

    def respond_with_card(self, *args, **kwargs):
        return False

    def pass_response(self, *args, **kwargs):
        return False

    def __getattr__(self, name):
        # 只读替身不该被写：任何没实现的属性都明确报错，便于发现"某个绘制
        # 路径偷偷依赖了权威状态"，而不是静默返回 None 画出一片空白。
        if name.startswith("_"):
            raise AttributeError(name)
        raise AttributeError(
            "只读视图没有 " + name + "：客户端不构造第二个 Game")

    # ---- 节奏（本地显示设置） ----

    def speed_index(self):
        return self.speed_state.index

    @property
    def speed_label(self):
        return self.speed_state.label()

    def slower(self):
        return self.speed_state.slower()

    def faster(self):
        return self.speed_state.faster()


class ViewResult:
    """结算信息替身（``game.result``）。"""

    def __init__(self, outcome="", reason="", winner_id="", headline="", winner=None):
        self.outcome = outcome
        self.reason = reason
        self.winner_id = winner_id
        self.headline = headline
        self.winner = winner
        self.loser = None

    @property
    def winner_player_id(self):
        return self.winner_id

    @classmethod
    def for_view(cls, view, players):
        if not view.game_over or view.result is None:
            return None
        winner = players.get(view.result.winner_id)
        return cls(view.result.outcome, view.result.reason,
                   view.result.winner_id, view.result.headline, winner)
