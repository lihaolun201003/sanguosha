"""逐人视图生成器（Phase 11.3 §7 / §16）。

``build_view(game, viewer_id)`` 在一份权威 ``Game`` 上为**某一名观众**生成
``ClientGameView``。同一帧调用两次、传不同的 ``viewer_id``，得到的是两份不同
的合法视图——这正是本阶段要证明的东西。

全部分支只看"这名观众是谁"，不看"UI 要不要画"：隐藏信息在生成阶段就不在
数据里。规则层完全不知道这个模块存在，视图生成也只读不改。
"""

from src.game.identity import identity_name

from .view_model import (
    ActionView,
    ClientGameView,
    JudgeView,
    PlayerView,
    PoolEntryView,
    ResponseView,
    ResultView,
    SelectionView,
    ViewCard,
    card_to_payload,
    face_down_card,
)
from .visibility import (
    equipment_cards,
    hand_is_visible,
    is_self,
    judge_area_cards,
    secret_selection_zone,
    visible_hand_cards,
    visible_identity_of,
)

#: 弃牌堆只同步末尾这么多张：客户端只需要"顶牌 + 张数"，不需要整堆历史。
DISCARD_TAIL = 12


# ==================================================
# 牌
# ==================================================

def view_card(card):
    return ViewCard.from_payload(card_to_payload(card))


# ==================================================
# 角色
# ==================================================

def build_player_view(game, viewer_id, player):
    """一名角色的公开视图；手牌内容只对本人出现。"""

    general_id = getattr(player, "general_id", None)
    general = game.generals.get(general_id) if general_id else None
    equipment = {
        slot: view_card(card) for slot, card in equipment_cards(player).items()
    }
    hand = tuple(view_card(card) for card in visible_hand_cards(viewer_id, player))
    return PlayerView(
        player_id=str(player.player_id),
        seat=int(player.seat),
        nickname=str(player.name),
        general_id=general_id,
        general_name=(general.name if general is not None else ""),
        hp=int(player.hp),
        max_hp=int(player.max_hp),
        alive=bool(player.alive and player.hp > 0),
        gender=str(getattr(player, "gender", "") or ""),
        kingdom=(general.kingdom if general is not None else ""),
        chained=bool(getattr(player, "chained", False)),
        controller=_controller_name(player),
        is_self=is_self(viewer_id, player),
        is_host=bool(player is getattr(game, "player", None)),
        identity=visible_identity_of(game, viewer_id, player),
        hand_count=len(player.hand or ()),
        hand=hand,
        equipment=equipment,
        judge_area=tuple(view_card(card) for card in judge_area_cards(player)),
        skills=_skill_ids(game, player),
    )


def _controller_name(player):
    value = getattr(player, "controller_type", None)
    return getattr(value, "value", None) or str(value or "ai")


def _skill_ids(game, player):
    """这名角色**公开**的武将技能 id（武将技能本来就是公开信息）。"""

    manager = getattr(game, "skills", None)
    if manager is not None and hasattr(manager, "skill_ids_of"):
        try:
            return tuple(str(item) for item in manager.skill_ids_of(player))
        except Exception:                            # pragma: no cover - 兜底
            pass
    general = game.generals.get(getattr(player, "general_id", None))
    return tuple(str(item) for item in getattr(general, "skill_ids", ()) or ())


# ==================================================
# 当前动作 / 响应 / 判定
# ==================================================

def build_action_view(action, viewer_id, game):
    """``action`` 是房主侧记录的一次出牌（见 ``presentation.PresentationBridge``）。

    View-As 的语义牌在这里是**主角**：中央显示的是虚拟结果牌（例如【杀】），
    来源实体牌只作为 provenance 附在后面；且只有观众有权看见的实体牌才带上
    牌面，否则退化成"内容未知"。
    """

    if not action:
        return None
    card = action.get("card")
    sources = []
    for item in action.get("sources") or ():
        if item is None:
            continue
        if _card_visible(game, viewer_id, item):
            sources.append(view_card(item))
        else:
            sources.append(face_down_card(getattr(item, "id", "")))
    return ActionView(
        actor_id=str(getattr(action.get("actor"), "player_id", "") or ""),
        label=str(action.get("label") or ""),
        card=(view_card(card) if card is not None else None),
        targets=tuple(
            str(getattr(player, "player_id", "") or "")
            for player in action.get("targets") or ()
        ),
        skill_name=str(action.get("skill_name") or ""),
        sources=tuple(sources),
        virtual=bool(action.get("virtual")),
    )


def build_response_view(response, viewer_id, game):
    if not response:
        return None
    card = response.get("card")
    return ResponseView(
        player_id=str(getattr(response.get("player"), "player_id", "") or ""),
        label=str(response.get("label") or ""),
        card=(view_card(card) if card is not None and _card_visible(game, viewer_id, card)
              else None),
    )


def build_judge_view(judge, viewer_id, game):
    """判定展示数据：牌面 + 规则声明 + 结果语义（都来自公开的判定声明表）。"""

    if not judge:
        return None
    from src.game.judge_presentation import judge_source

    reason = str(judge.get("reason") or "")
    spec = judge_source(reason)
    card = judge.get("card")
    if card is None:
        card = getattr(judge.get("result"), "card", None)
    judged = judge.get("judged_player")
    outcome_title = ""
    outcome_text = ""
    tone = "neutral"
    if judge.get("final") and spec is not None:
        # 与引擎同一套调用约定：结果语义函数吃的是**判定牌本身**（Card），
        # 不是 JudgeResult（八卦阵一类读的是 card_color）。
        outcome = spec.outcome(card, judged)
        tone = getattr(outcome.tone, "value", str(outcome.tone))
        outcome_title = outcome.title
        outcome_text = outcome.text
    replacements = []
    for item in judge.get("replacements") or ():
        replacements.append((
            str(getattr(item.get("player"), "player_id", "") or ""),
            str(item.get("skill_name") or ""),
            view_card(item["old"]) if item.get("old") is not None else face_down_card(),
            view_card(item["new"]) if item.get("new") is not None else face_down_card(),
        ))
    return JudgeView(
        reason=reason,
        judged_player_id=str(
            getattr(judge.get("judged_player"), "player_id", "") or ""),
        stage=str(judge.get("stage") or "revealed"),
        card=(view_card(card) if card is not None else None),
        source_label=(spec.display_name if spec is not None else reason),
        source_kind=(getattr(spec.kind, "value", "") if spec is not None else ""),
        rule_text=(spec.rule_text if spec is not None else ""),
        tone=tone,
        outcome_title=outcome_title,
        outcome_text=outcome_text,
        replacements=tuple(replacements),
    )


def _card_visible(game, viewer_id, card):
    from .visibility import card_is_visible_to

    return card_is_visible_to(game, viewer_id, card)


# ==================================================
# 选牌界面
# ==================================================

def build_response_window(game, viewer_id):
    """共享响应阶段（无懈）里**这名观众自己的**状态。

    只回答"我现在该做什么"：``""`` 我没有资格、``"pending"`` 正在问我、
    ``"passed"`` 我已放弃本轮。谁手里有【无懈可击】、别人已经放弃了没有，
    一律不在这里出现——那会泄露手牌信息，界面也不需要知道。
    """

    request = getattr(game, "pending_request", None)
    if request is None or not getattr(request, "is_group", False):
        return None
    viewer = _find(game, viewer_id)
    status = request.member_status(viewer) if viewer is not None else ""
    return {
        "window_id": int(request.context.get("window_id") or 0),
        "round_id": int(request.context.get("round_id") or 0),
        "reason": str(request.context.get("reason") or ""),
        "status": status,
    }


def build_selection_view(game, viewer_id):
    """只发给"正在做这个选择"的玩家；其他人根本不该看到这个界面。"""

    selection = getattr(game, "pending_selection", None)
    if not selection:
        return None
    owner = selection.get("owner")
    if owner is not None and not is_self(viewer_id, owner):
        return None
    face_down = secret_selection_zone(selection, viewer_id=viewer_id, game=game)
    candidates = []
    for card, key in selection.get("candidates", ()):
        entry = (
            face_down_card(getattr(card, "id", "")) if id(card) in face_down
            else view_card(card)
        )
        candidates.append(PoolEntryView(card=entry, key=key))
    selected = tuple(
        str(getattr(card, "id", "") or "")
        for card, _rect, _key in selection.get("selected", ())
    )
    return SelectionView(
        zone=str(selection.get("zone") or "hand"),
        prompt=str(selection.get("prompt") or ""),
        number=int(selection.get("number") or 0),
        candidates=tuple(candidates),
        selected_ids=selected,
    )


# ==================================================
# 结果
# ==================================================

def build_result_view(game):
    if not getattr(game, "game_over", False):
        return None
    result = getattr(game, "result", None)
    mode = getattr(game, "mode", None)
    rows = ()
    if mode is not None and getattr(mode, "uses_identities", False):
        lines = getattr(mode, "result_lines", None)
        if callable(lines):
            rows = tuple(dict(item) for item in lines())
    return ResultView(
        outcome=getattr(getattr(result, "outcome", None), "value", "") or "",
        reason=str(getattr(result, "reason", "") or ""),
        winner_id=str(getattr(result, "winner_player_id", "") or ""),
        headline=(_result_headline(mode) if mode is not None else ""),
        rows=rows,
    )


def _result_headline(mode):
    headline = getattr(mode, "result_headline", None)
    if callable(headline):
        try:
            return str(headline() or "")
        except Exception:                            # pragma: no cover - 兜底
            return ""
    return ""


# ==================================================
# 主入口
# ==================================================

def build_view(game, viewer_id, revision, *, host_player_id="", action=None,
               response=None, judge=None, decision=None):
    """为 ``viewer_id`` 生成一份只读视图（纯读，不改任何状态）。"""

    players = tuple(
        build_player_view(game, viewer_id, player)
        for player in game.seats.all_players()
    )
    viewer = _find(game, viewer_id)
    discard_pile = list(getattr(game.deck, "discard_pile", ()) or ())
    return ClientGameView(
        match_id=str(getattr(game, "match_id", "") or ""),
        revision=int(revision),
        game_mode=str(getattr(game, "mode_id", "ffa") or "ffa"),
        mode_label=str(getattr(getattr(game, "mode", None), "name", "") or ""),
        local_player_id=str(viewer_id),
        host_player_id=str(host_player_id or ""),
        current_player_id=str(getattr(game, "current_player_id", "") or ""),
        current_phase=str(getattr(game, "phase", "") or ""),
        responding_player_id=_responding_id(game),
        message=str(getattr(game, "message", "") or ""),
        game_over=bool(getattr(game, "game_over", False)),
        draw_pile_count=len(getattr(game.deck, "draw_pile", ()) or ()),
        discard_count=len(discard_pile),
        discard_tail=tuple(view_card(card) for card in discard_pile[-DISCARD_TAIL:]),
        public_pool=tuple(
            view_card(card) for card in (getattr(game, "public_card_pool", ()) or ())),
        table_cards=tuple(_table_card_entries(game)),
        hand=tuple(
            view_card(card)
            for card in visible_hand_cards(viewer_id, viewer)),
        players=players,
        action=build_action_view(action, viewer_id, game),
        response=build_response_view(response, viewer_id, game),
        judge=build_judge_view(judge, viewer_id, game),
        selection=build_selection_view(game, viewer_id),
        decision=(dict(decision) if decision else None),
        response_window=build_response_window(game, viewer_id),
        result=build_result_view(game),
        log=tuple(str(item) for item in (getattr(game, "game_log", ()) or ())),
        skills=_skill_ids(game, viewer) if viewer is not None else (),
    )


def _table_card_entries(game):
    for card, slot in getattr(game, "table_cards", ()) or ():
        if card is None:
            continue
        yield PoolEntryView(card=view_card(card), key=str(slot))


def _responding_id(game):
    request = getattr(game, "pending_request", None)
    if request is None:
        return ""
    if getattr(request, "is_group", False):
        # 共享响应阶段没有"唯一正在响应的人"，而且"谁是第一个有资格的人"
        # 本身就是手牌信息（他多半手里有【无懈可击】）：不能发出去。
        return ""
    target = getattr(request, "target", None)
    if target is None:
        return ""
    return str(getattr(target, "player_id", "") or "")


def _find(game, player_id):
    for player in game.players:
        if str(player.player_id) == str(player_id):
            return player
    return None


def identity_label_for(game, viewer_id, player):
    """给 UI 用的身份文案（隐藏时为空串）。"""

    return identity_name(getattr(player, "identity", None)) \
        if visible_identity_of(game, viewer_id, player) else ""
