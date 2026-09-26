"""决策 → 桌面交互 的映射（Phase 11.4 §7 / §18）。

房主把"要这名远程玩家做的决定"翻译成 ``DecisionRequest``；本模块把它翻译成
**既有渲染层已经认识的那几个交互槽位**——就是本地真人在引擎里用的同一批
状态：

============================  ==================================================
交互槽位                       本地真人的来源（引擎）
============================  ==================================================
``pending_selection``          ``game.start_card_selection(...)``
``pending_target_selection``   ``game.start_target_selection(...)``
``response``                   ``game.response.request(...)``
``choice``                     ``game.choice.request(...)``
手牌高亮                        ``CardActionDiscovery`` 的 ``is_operable``
============================  ==================================================

于是客户端不需要一整套"联机专用面板"：``Renderer`` / ``prompt`` / 按钮 / 高亮
全部走本地同一份代码，玩家看到的桌面与单机一致（差异只在数据来源）。

**这里不做三件事**：不算距离、不判合法性、不构造虚拟牌。请求里没出现的牌
与角色，客户端就当作不能选——房主是唯一的规则权威（§18 硬要求）。
"""

from src.network.decisions import DecisionKind


# ==================================================
# 请求字段的读取（一律容忍缺字段：老客户端不会因为新字段崩掉）
# ==================================================

def constraints(request):
    value = (request or {}).get("constraints")
    return value if isinstance(value, dict) else {}


def entries(request, key="cards"):
    values = (request or {}).get(key) or ()
    return [item for item in values if isinstance(item, dict)]


def int_field(request, name, default=0):
    try:
        return int(constraints(request).get(name) or 0)
    except (TypeError, ValueError):
        return int(default)


def activatable(request):
    """本条请求允许发动的主动技（出牌阶段才有）。"""

    if (request or {}).get("kind") != DecisionKind.PLAY_PHASE:
        return ()
    return tuple(constraints(request).get("activatable") or ())


def skill_entry(request, skill_id):
    for item in activatable(request):
        if str(item.get("skill_id") or "") == str(skill_id or ""):
            return item
    return None


# ==================================================
# 手牌高亮
# ==================================================

#: 这些决策里，手牌由房主给出的候选集合决定可点 / 可灰。
HAND_KINDS = (
    DecisionKind.PLAY_PHASE,
    DecisionKind.RESPOND_CARD,
    DecisionKind.SELECT_CARDS,
)


def hand_allowed_ids(request, my_hand_ids=()):
    """允许点的手牌 id 集合；``None`` 表示"不灰化"。

    ``SELECT_CARDS`` 只在候选就是自己的手牌时才灰化：从别人区域选牌时，
    灰掉自己的手牌会让玩家以为自己选错了地方（候选在公共区高亮）。
    """

    if not request:
        return None
    kind = request.get("kind")
    if kind not in HAND_KINDS:
        return None
    mine = {str(item) for item in my_hand_ids or ()}
    allowed = set()
    for item in entries(request):
        card_id = str(item.get("card_id") or "")
        if not card_id:
            continue
        if kind == DecisionKind.SELECT_CARDS:
            if not mine:
                return None
            if card_id not in mine:
                continue            # 候选不在我手上：这次不灰化手牌
        allowed.add(card_id)
    if kind == DecisionKind.SELECT_CARDS and not allowed:
        return None
    return allowed


def skill_activation_state(request, skill_id):
    """某个技能此刻能不能按（房主下发为准）。"""

    entry = skill_entry(request, skill_id)
    if entry is None:
        return False, "由房主判定"
    if not entry.get("enabled", True):
        return False, str(entry.get("disabled_reason") or "现在不能发动")
    return True, ""


# ==================================================
# 选牌 → pending_selection
# ==================================================

def _selection_zone(request, mine_count, total, zones):
    if total and mine_count == total and zones <= {"hand"}:
        return "hand"
    if total and mine_count == total and zones <= {"equipment"}:
        return "player_equipment"
    return "public_pool"


def selection_presentation(request, *, resolve_card, my_player_id="", owner=None,
                           selected_ids=()):
    """``SELECT_CARDS`` → ``pending_selection`` 同构字典（渲染层直接认识）。

    ``resolve_card`` 吃的是请求里的一条候选（dict），返回一个稳定的展示用
    ``Card``；候选可能只有 id（别人的手牌），所以展示卡由调用方按需构造。
    """

    if (request or {}).get("kind") != DecisionKind.SELECT_CARDS:
        return None
    items = entries(request)
    if not items:
        return None

    mine = [item for item in items
            if str(item.get("owner_id") or "") == str(my_player_id or "")]
    zones = {str(item.get("zone") or "hand") for item in items}
    zone = _selection_zone(request, len(mine), len(items), zones)

    selected_text = {str(item) for item in selected_ids or ()}
    candidates = []
    selected = []
    face_down = set()
    for item in items:
        card_id = str(item.get("card_id") or "")
        card = resolve_card(item)
        if card is None:
            continue
        key = None if zone == "hand" else (item.get("slot") or None)
        candidates.append((card, key))
        if item.get("face_down"):
            face_down.add(id(card))
        if card_id in selected_text:
            # 选中项的 rect 由渲染层当前帧自己算（本地真人也传空 rect），
            # 这里只需要"哪几张被选中"。
            selected.append((card, (0, 0, 10, 10), key))

    minimum = int_field(request, "min_cards", 1)
    maximum = max(minimum, int_field(request, "max_cards", minimum))
    # 有上下文的选择（火攻）：请求里带着原因与"已经公开亮出的那张牌"，
    # 客户端据此画专用界面；规则合法性仍然只由候选集决定。
    context = request.get("context") or {}
    revealed = context.get("revealed_card")
    return {
        "zone": zone,
        "owner": owner,
        "candidates": candidates,
        "number": max(1, minimum),
        "minimum": maximum if minimum <= 0 else minimum,
        "maximum": maximum,
        "prompt": str(request.get("prompt") or "请选择卡牌"),
        "selected": selected,
        "on_complete": None,
        "request_id": request.get("request_id"),
        "cancellable": bool(constraints(request).get("allow_cancel")),
        "face_down_ids": face_down,
        "reason": str(context.get("reason") or ""),
        "revealed": (resolve_card(revealed) if isinstance(revealed, dict) else None),
        "revealed_player": str(context.get("revealed_by") or ""),
        "caster": str(context.get("caster") or ""),
    }


# ==================================================
# 选角色 → pending_target_selection
# ==================================================

def targets_presentation(request=None, *, resolve_player=None, target_entries=None,
                         selected_ids=(), prompt="", minimum=None, maximum=None):
    """选角色 → ``pending_target_selection`` 同构字典。

    目标候选可能来自请求的 ``targets``（``SELECT_TARGETS``），也可能来自
    出牌阶段里"当前选中的那张牌允许打谁"（``PLAY_PHASE`` 的某个方式）。
    """

    if target_entries is None:
        target_entries = entries(request, "targets")
    candidates = []
    seen = set()
    for item in target_entries:
        player = resolve_player(item.get("player_id")) if resolve_player else None
        if player is None:
            continue
        identity = id(player)
        if identity in seen:
            continue
        seen.add(identity)
        candidates.append(player)
    if not candidates:
        return None

    if minimum is None:
        minimum = int_field(request, "min_targets", 1)
    if maximum is None:
        maximum = int_field(request, "max_targets", minimum)
    minimum = max(0, int(minimum))
    maximum = max(minimum, int(maximum))

    chosen = {str(item) for item in selected_ids or ()}
    selected = [player for player in candidates
                if str(getattr(player, "player_id", "")) in chosen]
    return {
        "card": None,
        "source_rect": None,
        "candidates": candidates,
        "selected": selected,
        "minimum": minimum,
        "maximum": maximum,
        "metadata": {},
        "ignore_usage_limit": False,
        "prompt": str(prompt or (request or {}).get("prompt") or "请选择目标"),
        "on_complete": None,
        "on_cancel": None,
        "request_id": (request or {}).get("request_id"),
    }


# ==================================================
# 响应 / 是是否 → response / choice
# ==================================================

def response_presentation(request):
    """``RESPOND_CARD`` → ``response`` 同构数据（提示条用）。"""

    if (request or {}).get("kind") != DecisionKind.RESPOND_CARD:
        return None
    names = []
    for item in entries(request):
        name = str(item.get("name") or "")
        if name and name not in names:
            names.append(name)
    return {
        "prompt": str(request.get("prompt") or "请打出一张响应牌"),
        "allowed_cards": tuple(sorted(names)),
        # 语义标签：提示层据此把共享无懈阶段显示成"使用无懈／不出"。
        "reason": str((request.get("context") or {}).get("reason") or ""),
    }


def choice_presentation(request):
    """``CONFIRM`` / ``CHOOSE_OPTION`` → ``choice`` 同构数据（标题 + 提示）。"""

    kind = (request or {}).get("kind")
    if kind == DecisionKind.CONFIRM:
        return {"title": "请确认", "prompt": str(request.get("prompt") or "")}
    if kind == DecisionKind.CHOOSE_OPTION:
        return {"title": "请选择", "prompt": str(request.get("prompt") or "")}
    return None


# ==================================================
# 出牌阶段：可用的牌 / 可用方式 / 该方式的合法目标
# ==================================================

def play_card_entry(request, card_ids):
    """当前选中的实体牌在请求里的条目（多 source 时取第一张就够）。"""

    wanted = [str(item) for item in card_ids or ()]
    for card_id in wanted:
        for item in entries(request):
            if str(item.get("card_id") or "") == card_id:
                return item
    return None


def play_options(entry):
    """一张实体牌在当前局面下的全部使用方式（普通使用在前，转化在后）。"""

    if not entry:
        return []
    return [item for item in (entry.get("options") or ())
            if item.get("enabled", True)]


def play_option(entry, index=0):
    options = play_options(entry)
    if not options:
        return None
    return options[min(max(0, int(index)), len(options) - 1)]


def play_targets(option):
    """一个方式的合法目标条目（``player_entry`` 列表）。"""

    if not option:
        return []
    return [item for item in (option.get("targets") or ()) if isinstance(item, dict)]


def play_sources_ready(entry, option, chosen):
    """选中的实体牌张数是否已经够这次转化（多 source 转化才可能不足）。"""

    need = int((option or {}).get("min_sources") or 1)
    return len(chosen or ()) >= max(1, need)


def play_source_limit(entry, option):
    """这次最多能选几张实体牌。"""

    return max(1, int((option or {}).get("max_sources")
                      or (entry or {}).get("max_sources") or 1))


# ==================================================
# "方式"的查找（出牌阶段与响应窗口共用）
# ==================================================

def option_by_action(entry, action_id):
    """按房主下发的 ``action_id`` 找这张牌的一种用法（找不到返回 None）。

    客户端只回传房主给过的 id，自己从不构造方式：这是"客户端不做规则判断"
    的直接体现。
    """

    wanted = str(action_id or "")
    if not wanted:
        return None
    for item in (entry or {}).get("options") or ():
        if isinstance(item, dict) and str(item.get("action_id") or "") == wanted:
            return item
    return None


def option_index(entry, action_id):
    """方式 id → 它在候选里的下标（渲染层按顺序显示时要对齐）。"""

    options = (entry or {}).get("options") or []
    wanted = str(action_id or "")
    for index, item in enumerate(options):
        if isinstance(item, dict) and str(item.get("action_id") or "") == wanted:
            return index
    return None


def option_min_sources(entry, option):
    return max(1, int((option or {}).get("min_sources") or 1))


def option_multi_source(entry, option):
    """这次用法是不是"多张实体牌凑一张逻辑牌"。"""

    return option_min_sources(entry, option) > 1


def option_label(option):
    """一行中文：这张牌这样用会打出什么（提示条用）。"""

    return str((option or {}).get("label") or "")


def sources_needed_text(option, chosen):
    need = option_min_sources(None, option)
    if len(chosen or ()) >= need:
        return ""
    return "还需要选择 %d 张牌" % (need - len(chosen or ()))
