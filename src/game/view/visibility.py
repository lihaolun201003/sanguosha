"""逐人可见性规则（Phase 11.3）。

同一帧的权威 ``Game``，对不同的观众生成的视图**必须不同**。全部差异都由
这里集中判定，视图生成器（``view_builder``）与表现事件过滤器
（``presentation``）都只调用这里的函数，不各自写条件：

========================================  ==========================
信息                                      谁能看见
========================================  ==========================
自己的手牌                                本人（完整牌面）
别人的手牌                                任何人都不给内容，只给张数
装备区 / 判定区 / 弃牌堆 / 结算区 / 桌面牌   所有人（公开区域）
身份                                      按 ``visible_identity`` 规则
牌堆顺序                                  任何人都不给（只给张数）
观星一类"允许某人看牌堆顶"                 只在对应 DecisionRequest 里发给那个人
========================================  ==========================

注意：这些规则的作用是**在生成数据时就不放进去**，而不是"发过去让 UI 不画"。
"""

from src.game.identity import Identity, visible_identity

# 公开区域名（写进事件里，供客户端做视觉落位）。
ZONE_DRAW_PILE = "draw_pile"
ZONE_HAND = "hand"
ZONE_EQUIPMENT = "equipment"
ZONE_JUDGE = "judge_area"
ZONE_DISCARD = "discard_pile"
ZONE_PROCESSING = "processing"
ZONE_TABLE = "table"
ZONE_POOL = "public_pool"
ZONE_NONE = "none"

PUBLIC_ZONES = (
    ZONE_EQUIPMENT, ZONE_JUDGE, ZONE_DISCARD, ZONE_PROCESSING, ZONE_TABLE, ZONE_POOL,
)


def player_by_id(game, player_id):
    for player in getattr(game, "players", ()) or ():
        if player.player_id == player_id:
            return player
    return None


def is_self(viewer_id, player):
    return bool(player is not None and str(player.player_id) == str(viewer_id))


def hand_is_visible(viewer_id, player):
    """手牌内容是否对该观众公开（只有本人）。"""

    return is_self(viewer_id, player)


def visible_hand_cards(viewer_id, player):
    """该观众能看到的手牌内容；不是本人则返回空元组（张数另外给）。"""

    if player is None or not hand_is_visible(viewer_id, player):
        return ()
    return tuple(player.hand or ())


def visible_identity_of(game, viewer_id, player):
    """该观众能看到的身份（字符串）或 ``None``。"""

    mode = getattr(game, "mode", None)
    if mode is not None and not getattr(mode, "uses_identities", False):
        return None
    if player is None:
        return None
    viewer = player_by_id(game, viewer_id)
    identity = visible_identity(player, viewer)
    if identity is None:
        return None
    return identity.value if isinstance(identity, Identity) else str(identity)


def equipment_cards(player):
    """公开的装备区：``{slot: card}``（只保留有牌的槽位）。"""

    result = {}
    for slot, card in (getattr(player, "equipment", {}) or {}).items():
        if card is not None:
            result[slot] = card
    return result


def judge_area_cards(player):
    return tuple(getattr(player, "judgement_zone", ()) or ())


def secret_zones(game, viewer_id):
    """对这名观众**内容未知**的牌的对象 id 集合。

    只有一种情况：别人手里的牌。装备 / 判定 / 弃牌 / 结算区 / 桌面都是公开的，
    所以这里不需要枚举它们。
    """

    secret = set()
    for player in getattr(game, "players", ()) or ():
        if hand_is_visible(viewer_id, player):
            continue
        for card in player.hand or ():
            secret.add(id(card))
    return secret


def card_owner_of_hand(game, card):
    """这张牌在谁手上（不在任何人手上返回 ``None``）。"""

    for player in getattr(game, "players", ()) or ():
        for item in player.hand or ():
            if item is card:
                return player
    return None


def card_is_visible_to(game, viewer_id, card, *, allow=()):
    """这张牌的**牌面**是否可以发给该观众。

    ``allow`` 是本次事件额外授权的牌（例如顺手牵羊的获得者、火攻展示的牌）：
    规则允许这些牌在这一次事件里对特定玩家公开。
    """

    if card is None:
        return False
    for item in allow or ():
        if item is card:
            return True
    owner = card_owner_of_hand(game, card)
    if owner is None:
        # 不在任何人手上 = 公开区域里的牌，牌面公开。
        return True
    return hand_is_visible(viewer_id, owner)


def card_or_back(game, viewer_id, card, *, allow=()):
    """可见就给出 ``(card, True)``，否则 ``(card, False)``（调用方按内容未知处理）。"""

    return card, card_is_visible_to(game, viewer_id, card, allow=allow)


def secret_selection_zone(selection, *, viewer_id, game):
    """当前选牌界面里哪些候选是该观众看不到内容的。

    判定与 ``CardSelectionMixin._face_down_candidate_ids`` 同源——"从别人手里
    拿 / 弃"的牌只画牌背，但**本人自己的手牌**与公开区域照常显示卡面。
    """

    if not selection:
        return set()
    owner = selection.get("owner")
    if owner is None or is_self(viewer_id, owner):
        return set()
    hand = list(getattr(owner, "hand", ()) or ())
    if not hand:
        return set()
    return {
        id(card) for card, _key in selection.get("candidates", ())
        if any(card is item for item in hand)
    }
