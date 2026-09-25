"""规则状态 / 客户端视图一致性工具（任务 B 的公共工具层）。

同一条场景要能同时看到四件事，并且**分别独立取得**：

1. **房主权威状态**：房主进程里的真实 ``Game``（``host_state``）。
2. **某个玩家应看到的合法视图**：从权威 ``Game`` 直接读出、按
   ``src.game.view.visibility`` 的规则过滤之后的内容（``expected_for``）。
   这里刻意**不调用** ``build_view``：期望值必须来自"规则的另一种表述"，
   否则就成了把 ``build_view`` 的结果抄给客户端、再断言两者相等——那什么
   都证明不了。只有客户端那一侧真的同步错位（漏字段、把别人的手牌发出去、
   装备区对不上），两边才会分叉。
3. **客户端实际得到的数据**：客户端走完真实链路之后的语义字段
   （``client_state``）。默认取视图**适配层**（``RemoteGameView``）的结果，
   也就是既有渲染路径真正读的那份数据；``source="view"`` 则取
   ``ClientMatch`` 手上那份 ``ClientGameView``（刚从 socket 载荷解析出来）。
4. **当前请求是否还能由正确玩家继续操作**（``pending_responsible`` /
   ``decision_is_operable``）。

比对只覆盖**同一稳定版本的语义字段**：血量、手牌数量与内容（仅本人）、装备、
判定区、公开标记、身份、响应对象、区域张数。不比较动画中间帧、``revision``
号、时间戳这些与语义无关的东西。

隐藏信息按**玩家视角**校验：``expected_for`` 对别人的手牌给出 ``None``，
``client_state`` 也必须给 ``None``（拿到任何真实牌 id 都会当场对不上），
因此"所有客户端必须看到同样的手牌"这种错误断言不会出现在这里。
"""

import os
import sys
from dataclasses import dataclass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.game.controllers.remote import OPAQUE_PREFIX
from src.game.view import visibility

#: 与 ``view_builder.DISCARD_TAIL`` 保持一致：客户端只拿弃牌堆末尾这几张。
DISCARD_TAIL = 12

# ==================================================
# 客户端一张牌的三种身份
#
# 检查隐藏信息时，"没看到内容"和"看到了内容"必须分得清，而且**不能替客户端
# 把内容藏起来**：Phase 14.1 问题 1 就是工具自己把收到的对手手牌归一化成
# ``None``，于是真泄露也测不出来。
# ==================================================

#: 真实牌面（或至少是真实 card_id）：这名观众不该拿到的东西。
FACE_REAL = "real"
#: 合法匿名：选择用的不透明占位（``hidden:…``），或只有 id 的牌背。
FACE_TOKEN = "token"
#: 什么都没拿到：``None`` 占位 / 空的牌背。
FACE_BLANK = "blank"


# ==================================================
# 小工具
# ==================================================

def _text(value):
    return "" if value is None else str(value)


def _card_id(card):
    """一张卡的稳定标识。

    两套对象都要认：权威侧的 ``Card`` / ``CardData`` 用 ``.id``，
    网络与适配层的 ``ViewCard`` 用 ``.card_id``。只认一个会让比对结果
    整片变成空串，看起来像"客户端什么都没收到"。
    """

    value = getattr(card, "id", None)
    if value in (None, ""):
        value = getattr(card, "card_id", None)
    return _text(value)


def _card_face(card):
    """这张牌上可读的牌面：``(牌名, 花色, 点数)``。"""

    return (_text(getattr(card, "name", "")),
            getattr(card, "suit", None),
            getattr(card, "rank", None))


def _has_readable_face(card):
    name, suit, rank = _card_face(card)
    return bool(name) or suit is not None or rank is not None


def card_face_kind(card):
    """把客户端拿到的一张牌分成三档（**在归一化之前**判）。

    * ``FACE_REAL``：带着真实牌面，或者带着真实 ``card_id`` 却没标牌背。
      ``card_id`` 本身就能把牌对上号，所以它同样算"拿到了内容"。
    * ``FACE_TOKEN``：合法匿名——选择用的不透明占位（``hidden:…``），或者
      明确标了牌背且没有任何牌面的牌。
    * ``FACE_BLANK``：什么都没有（``None`` 占位、空牌背）。

    关键顺序：**先看牌面，再看 id 与牌背标记**。``face_down=True`` 却带着牌名，
    或者占位 token 上却带着花色，都是自相矛盾的数据——按"泄露"处理，而不是被
    ``face_down`` / 占位前缀掩盖过去。
    """

    if card is None:
        return FACE_BLANK
    if _has_readable_face(card):
        return FACE_REAL
    card_id = _card_id(card)
    if card_id.startswith(OPAQUE_PREFIX):
        return FACE_TOKEN
    if card_id:
        return FACE_TOKEN if getattr(card, "face_down", False) else FACE_REAL
    return FACE_BLANK


def _equipment_map(player):
    """公开装备区：``{slot: card_id}``，只保留有牌的槽位。"""

    result = {}
    for slot, card in sorted((getattr(player, "equipment", None) or {}).items()):
        if card is not None:
            result[str(slot)] = _card_id(card)
    return result


def _identity_text(game, viewer_id, player):
    value = visibility.visible_identity_of(game, viewer_id, player)
    return None if value is None else _text(getattr(value, "value", value))


def _viewer_of(card):
    """客户端侧的一张牌：占位牌（``None`` / ``face_down``）不算"看得到内容"。"""

    if card is None:
        return None
    if getattr(card, "face_down", False):
        return None
    card_id = _card_id(card)
    return card_id or None


# ==================================================
# 1. 房主权威状态
# ==================================================

def host_state(game):
    """权威状态的摘要（不含任何可见性过滤，供报告与调试打印用）。"""

    return {
        "current_player_id": _text(game.current_player_id),
        "phase": _text(getattr(game, "phase", "")),
        "game_over": bool(getattr(game, "game_over", False)),
        "draw_pile_count": len(game.deck.draw_pile),
        "discard_count": len(game.deck.discard_pile),
        "players": {
            _text(player.player_id): {
                "hp": int(player.hp),
                "max_hp": int(player.max_hp),
                "alive": bool(player.alive),
                "hand": tuple(_card_id(card) for card in player.hand),
                "equipment": _equipment_map(player),
                "judge_area": tuple(_card_id(card)
                                    for card in player.judgement_zone),
            }
            for player in game.seats.all_players()
        },
    }


def pending_responsible(game):
    """当前在等谁回答：返回 ``(player_id 列表, 说明)``。

    两种等待都覆盖：引擎的 ``PendingRequest``（含共享无懈阶段的**群体**成员）
    与遗留 ``ResponseSystem`` 的响应窗口。
    """

    request = getattr(game, "pending_request", None)
    if request is not None:
        reason = _text(request.context.get("reason", "")) or request.request_type.value
        if getattr(request, "is_group", False):
            members = [_text(getattr(member, "player_id", ""))
                       for member in request.pending_members]
            return members, "共享响应阶段(%s)" % reason
        target = getattr(request, "target", None)
        if target is None:
            return [], "请求没有回答者(%s)" % reason
        note = "等待%s回答(%s)" % (_text(target.name), reason)
        return [_text(target.player_id)], note

    response = getattr(getattr(game, "response", None), "current", None)
    if response is not None:
        responder = getattr(response, "responder", None)
        if responder is None:
            responder = getattr(game, "player", None)
        if responder is None:
            return [], "响应窗口没有归属"
        return ([_text(responder.player_id)],
                "响应窗口(%s)" % (_text(getattr(response, "reason", "")) or "?"))

    return [], "没有待回答的请求"


# ==================================================
# 2. 某玩家应看到的合法视图（从权威状态直接算）
# ==================================================

def expected_for(game, viewer_id):
    """``viewer_id`` 这名玩家**合法应见**的语义快照。

    手牌内容只有本人有（别人是 ``None``，只给张数）；身份按
    ``visible_identity`` 规则；装备 / 判定区 / 弃牌堆 / 公共池是公开信息。
    """

    viewer_id = _text(viewer_id)
    players = {}
    for player in game.seats.all_players():
        player_id = _text(player.player_id)
        mine = visibility.hand_is_visible(viewer_id, player)
        players[player_id] = {
            "hp": int(player.hp),
            "max_hp": int(player.max_hp),
            "alive": bool(player.alive and player.hp > 0),
            "hand_count": len(player.hand or ()),
            # None = 这名玩家不该有手牌内容（本人给真实内容，别人一律 None）。
            # 保留手牌顺序（两边用同一个顺序表达；排序反而会掩盖顺序错乱）。
            "hand_ids": (tuple(_card_id(card) for card in player.hand)
                         if mine else None),
            # 牌面泄露（别人手上出现了可读的牌名/花色/点数）。期望侧恒为空：
            # 谁都不该拿到别人的牌面。
            "hand_faces": (),
            "equipment": _equipment_map(player),
            "judge_area": tuple(_card_id(card) for card in player.judgement_zone),
            "identity": _identity_text(game, viewer_id, player),
            "general_id": getattr(player, "general_id", None),
            "chained": bool(getattr(player, "chained", False)),
        }

    discard_pile = list(game.deck.discard_pile)
    return {
        "local_player_id": viewer_id,
        "current_player_id": _text(game.current_player_id),
        "phase": _text(getattr(game, "phase", "")),
        "game_over": bool(getattr(game, "game_over", False)),
        "draw_pile_count": len(game.deck.draw_pile),
        "discard_count": len(discard_pile),
        "discard_tail": tuple(_card_id(card)
                              for card in discard_pile[-DISCARD_TAIL:]),
        "public_pool": tuple(_card_id(card) for card in game.public_card_pool),
        "players": players,
    }


# ==================================================
# 3. 客户端同步 + 视图适配之后的数据
# ==================================================

def _hand_ids(entries, *, mine):
    """手牌的**忠实**读法：实际收到的真实牌身份照原样交出来，绝不代为隐藏。

    * 本人的手牌：按顺序给出真实牌面的 id。混进了牌背 / 占位就少给几个，
      与期望长度对不上，比对自然报错。
    * 别人的手牌：规则上"不该有内容"，所以**期望侧**是 ``None``。这一侧只
      回答"实际收到了什么"——收到真实牌面或真实 ``card_id`` 就原样交出来，
      比对与 ``assert_hidden`` 才抓得到。

    Phase 14.1 问题 1 的根因正是这里曾经写死 ``if not mine: return None``：
    泄露被工具自己抹掉了，于是怎么测都是通过。
    """

    real = tuple(
        _card_id(card) for card in (entries or ())
        if card_face_kind(card) == FACE_REAL
    )
    if mine:
        return real
    return real or None


def _hand_faces(entries, *, mine):
    """未经授权就拿到的**牌面**（本人有权看自己的牌，所以本人永远是空）。

    期望侧对所有玩家都该是空元组；实际侧一旦非空，就是上游把不该发的牌面发
    过来了——哪怕它的 ``card_id`` 是合法的不透明占位，牌名 / 花色 / 点数也
    不该出现。
    """

    if mine:
        return ()
    leaked = []
    for card in (entries or ()):
        if card is None or not _has_readable_face(card):
            continue
        if card_face_kind(card) != FACE_REAL:
            continue
        name, suit, rank = _card_face(card)
        leaked.append((_card_id(card), name, suit, rank))
    return tuple(leaked)


def _state_from_adapter(view):
    """``RemoteGameView``（既有点击 / 绘制路径真正读的那份数据）。"""

    inner = getattr(view, "view", None)      # 底下的 ClientGameView（若有）
    local = _text(getattr(view, "local_player_id", "")
                  or getattr(inner, "local_player_id", "")
                  or getattr(getattr(view, "player", None), "player_id", ""))

    players = {}
    for player in view.players:
        player_id = _text(getattr(player, "player_id", ""))
        judge_area = [_viewer_of(card) for card in (player.judgement_zone or ())]
        players[player_id] = {
            "hp": int(getattr(player, "hp", 0)),
            "max_hp": int(getattr(player, "max_hp", 0)),
            "alive": bool(getattr(player, "alive", False)),
            "hand_count": int(getattr(player, "hand_count", 0)),
            "hand_ids": _hand_ids(player.hand, mine=player_id == local),
            "hand_faces": _hand_faces(player.hand, mine=player_id == local),
            "equipment": {
                str(slot): _card_id(card)
                for slot, card in sorted((player.equipment or {}).items())
                if card is not None
            },
            "judge_area": tuple(item for item in judge_area if item is not None),
            "identity": getattr(player, "identity", None),
            "general_id": getattr(player, "general_id", None),
            "chained": bool(getattr(player, "chained", False)),
        }

    deck = view.deck
    discard_ids = [_viewer_of(card) for card in (deck.discard_pile or ())]
    return {
        "local_player_id": local,
        "current_player_id": _text(getattr(view, "current_player_id", "")),
        "phase": _text(getattr(view, "phase", "")),
        "game_over": bool(getattr(view, "game_over", False)),
        "draw_pile_count": int(getattr(deck, "draw_count", 0)),
        "discard_count": int(getattr(deck, "discard_count", 0)),
        "discard_tail": tuple(item for item in discard_ids if item is not None),
        "public_pool": tuple(_card_id(card)
                             for card in (view.public_card_pool or ()) if card),
        "players": players,
    }


def _state_from_view(view):
    """``ClientGameView``（刚从 socket 载荷解析出来、还没过期的那份）。"""

    players = {}
    for player in view.players:
        player_id = _text(player.player_id)
        players[player_id] = {
            "hp": int(player.hp),
            "max_hp": int(player.max_hp),
            "alive": bool(player.alive),
            "hand_count": int(player.hand_count),
            "hand_ids": _hand_ids(player.hand, mine=bool(player.is_self)),
            "hand_faces": _hand_faces(player.hand, mine=bool(player.is_self)),
            "equipment": {
                str(slot): _card_id(card)
                for slot, card in sorted(player.equipment.items())
                if card is not None
            },
            "judge_area": tuple(_card_id(card) for card in player.judge_area),
            "identity": player.identity,
            "general_id": player.general_id,
            "chained": bool(player.chained),
        }
    return {
        "local_player_id": _text(view.local_player_id),
        "current_player_id": _text(view.current_player_id),
        "phase": _text(view.current_phase),
        "game_over": bool(view.game_over),
        "draw_pile_count": int(view.draw_pile_count),
        "discard_count": int(view.discard_count),
        "discard_tail": tuple(_card_id(card) for card in view.discard_tail),
        "public_pool": tuple(_card_id(card) for card in view.public_pool),
        "players": players,
    }


def client_state(client, *, source="adapter"):
    """客户端侧的语义快照。``client`` 是 ``tools.lan_view_harness.ClientSide``。

    ``source="adapter"``（默认）读视图适配层 ``RemoteGameView``；
    ``source="view"`` 读 ``ClientMatch.view``（网络载荷解析结果）。
    """

    if source == "adapter":
        scene = getattr(client, "scene", None)
        view = getattr(scene, "view", None)
        if view is None:
            raise AssertionError(
                "客户端没有视图适配层数据（ClientSide(render=True) 才有）")
        return _state_from_adapter(view)
    if source == "view":
        view = getattr(client, "view", None)
        if view is None:
            raise AssertionError("客户端还没有收到过任何视图")
        return _state_from_view(view)
    raise ValueError("unknown client_state source: " + str(source))


def host_projection(host, player_id):
    """房主为某名玩家生成的权威投影（``HostMatch.view_for`` → ``build_view``）。

    它服务的是**可见性规则的双向核验**：``expected_for`` 与它各自独立地把
    同一套规则表达了一遍，两边对不上就说明其中一边漏了或多了东西。它**不是**
    客户端数据的来源——客户端那一侧永远走真实网络同步，绝不用这份投影去喂
    （那才会变成"把 build_view 抄给客户端再断言相等"）。
    """

    match = getattr(host, "match", None)
    if match is None:
        raise AssertionError("房主还没有建立对局")
    return _state_from_view(match.view_for(_text(player_id)))


# ==================================================
# 3b. 按玩家视角看"究竟拿到了什么"
#
# 这两个入口刻意读**原始**条目（不经过 hand_ids / hand_faces 的归一化），
# 用来在负向测试里证明"工具确实分得清三档"，并且在真的泄露时给出证据。
# ==================================================

def _raw_player_entries(client, player_id, *, source="adapter"):
    """这名玩家在客户端手上的**原始**手牌条目（未做任何过滤 / 归一化）。"""

    if source == "adapter":
        view = getattr(getattr(client, "scene", None), "view", None)
        if view is None:
            raise AssertionError("客户端没有视图适配层数据")
        entries = [player for player in view.players
                   if _text(getattr(player, "player_id", "")) == _text(player_id)]
        return list(entries[0].hand or ()) if entries else []
    if source == "view":
        view = getattr(client, "view", None)
        if view is None:
            raise AssertionError("客户端还没有收到过任何视图")
        entries = [player for player in view.players
                   if _text(player.player_id) == _text(player_id)]
        return list(entries[0].hand or ()) if entries else []
    raise ValueError("unknown source: " + str(source))


def hand_face_kinds(client, player_id, *, source="adapter"):
    """这名玩家的手牌在客户端手上**分别是哪一档**（``real`` / ``token`` / ``blank``）。

    顺序与客户端收到的条目一致。它不进快照比对（不同源对"合法匿名"的表达
    本来就不一样），只用来把"合法匿名"与"真泄露"分开说清楚。
    """

    return tuple(card_face_kind(card)
                 for card in _raw_player_entries(client, player_id, source=source))


def unauthorized_hand_faces(client, player_id, *, source="adapter"):
    """客户端手上属于这名玩家、但**不该被看到**的牌面。

    返回 ``[(card_id, 牌名, 花色, 点数), …]``；只含真实牌面，合法的匿名占位
    与牌背不在里面（它们由 ``hand_face_kinds`` 表达为 ``token`` / ``blank``）。
    """

    leaked = []
    for card in _raw_player_entries(client, player_id, source=source):
        if card is None or card_face_kind(card) != FACE_REAL:
            continue
        if not _has_readable_face(card):
            continue
        name, suit, rank = _card_face(card)
        leaked.append((_card_id(card), name, suit, rank))
    return leaked


# ==================================================
# 4. 比对
# ==================================================

def diff(expected, actual, *, path=""):
    """逐字段比较两份快照；返回差异说明列表（空列表 = 一致）。"""

    problems = []
    for key in sorted(set(expected) | set(actual)):
        where = (path + "." + key) if path else key
        if key not in expected:
            problems.append("%s 客户端多出字段 %r" % (where, actual[key]))
            continue
        if key not in actual:
            problems.append("%s 客户端缺少字段（权威=%r）" % (where, expected[key]))
            continue
        left, right = expected[key], actual[key]
        if isinstance(left, dict) and isinstance(right, dict):
            problems.extend(diff(left, right, path=where))
        elif left != right:
            problems.append("%s 期望 %r，客户端 %r" % (where, left, right))
    return problems


def assert_view_matches(case, expected, actual, *, note="", ignore=()):
    """断言客户端数据与"这名玩家应见的视图"一致。

    ``ignore`` 里可以列出场景确实拿不到的字段（必须写明理由，不能用它掩盖
    整块区域的差异）。
    """

    problems = [item for item in diff(expected, actual)
                if not any(item == key or item.endswith("." + key)
                           for key in ignore)]
    if problems:
        case.fail(
            ("视图不一致" + ("（" + note + "）" if note else "")) + "：\n  "
            + "\n  ".join(problems))
    return True


# ==================================================
# 5. 同步检查点（同一版本的比对）
# ==================================================

@dataclass(frozen=True)
class SyncCheckpoint:
    """一次比对绑定的"读数点"：对局标识 + 版本号 + 那一版的状态指纹。

    ``revision`` 是房主发布快照时递增的单调计数；``fingerprint`` 是发布**当时**
    的权威状态指纹（``HostMatch.state_fingerprint()``，房主本地用、不上网）。

    为什么必须带上指纹：``push_views`` 是**节流**的（``SYNC_INTERVAL``），两次
    发布之间房主状态可以继续变而 ``revision`` 不动。只认 revision 就会出现
    "版本号相同、状态早已不同"，比出来的结论没有意义。
    """

    match_id: str
    revision: int
    fingerprint: tuple = ()

    def __str__(self):
        return "%s@r%d" % (self.match_id or "?", self.revision)


def host_checkpoint(host):
    """房主此刻**已发布**的读数点（版本 + 那一版的状态指纹）。"""

    match = getattr(host, "match", None)
    if match is None:
        raise AssertionError("房主还没有建立对局")
    fingerprint = getattr(match, "published_fingerprint", None)
    return SyncCheckpoint(
        _text(getattr(match, "match_id", "")),
        int(getattr(match, "revision", 0) or 0),
        tuple(fingerprint) if fingerprint else ())


def client_checkpoint(client):
    """客户端**已应用**的读数点。"""

    match = getattr(client, "match", None)
    if match is None:
        raise AssertionError("客户端还没有建立对局")
    return SyncCheckpoint(_text(getattr(match, "match_id", "")),
                          int(getattr(match, "revision", 0) or 0))


def host_state_matches(host, checkpoint):
    """房主**此刻**的权威状态是否就是检查点发布出去的那一份。"""

    match = getattr(host, "match", None)
    if match is None:
        return False
    if not checkpoint.fingerprint:
        return True                       # 拿不到指纹（旧调用方）时不做这层校验
    try:
        return tuple(match.state_fingerprint()) == checkpoint.fingerprint
    except Exception:                                        # pragma: no cover
        return False


def publish_current_state(host):
    """把房主**当前**状态发布成一个版本（脏了才发，不会空转刷版本号）。

    生产代码里 ``send_decision`` 也是先 ``push_views(force=True)`` 再下发请求；
    这里同样"先发布、再钉读数点"，这样 revision + 指纹才真的描述当前状态。
    """

    match = getattr(host, "match", None)
    if match is None:
        raise AssertionError("房主还没有建立对局")
    if not host_state_matches(host, host_checkpoint(host)):
        match.push_views(force=True)
    return host_checkpoint(host)


def wait_for_sync(pump, host, client, *, timeout=8.0, interval=0.05):
    """等客户端应用房主**当前**状态，并把读数点钉在这个状态上。

    返回 ``(检查点, 说明)``；追不上时检查点是 ``None``，说明里写明卡在哪一步。

    步骤（每一步都是可判定的条件，不靠 sleep 猜）：

    1. **先让房主把当前状态发布出去**（脏了才发），于是 ``revision + 指纹``
       唯一描述"客户端应该看到的那一份"；
    2. 等客户端的 ``revision`` 追上；
    3. 复核房主的**实时**指纹仍与发布的一致——否则说明房主在这期间又推进了，
       回到第 1 步重来，**绝不**把旧版本的期望快照拿去比新版本的客户端。

    为什么不能"字段相等就收工"：客户端落后时，只要被比较的字段恰好还没变，
    字段就会相等——旧版本一样"通过"（Phase 14.1 问题 4）。所以先确认版本与
    指纹，再比字段。
    """

    import time

    deadline = time.monotonic() + timeout
    while True:
        checkpoint = publish_current_state(host)
        applied = client_checkpoint(client)
        if checkpoint.match_id and applied.match_id \
                and checkpoint.match_id != applied.match_id:
            return None, ("对局标识不一致：房主 %s，客户端 %s"
                          % (checkpoint, applied))
        if applied.revision >= checkpoint.revision:
            # 客户端到位了：确认房主没有在这期间又往前推。
            if host_checkpoint(host) == checkpoint \
                    and host_state_matches(host, checkpoint):
                return checkpoint, "已同步到 %s" % checkpoint
            why = "客户端到位后房主又推进了"
        else:
            why = ("客户端落后：房主 %s，客户端 %s（差 %d 个版本）"
                   % (checkpoint, applied, checkpoint.revision - applied.revision))
        if time.monotonic() >= deadline:
            return None, ("没能把两端定在同一个版本上：" + why)
        pump(interval)


def compare_at_checkpoint(case, host, client, player_id, checkpoint, *,
                          note="", ignore=()):
    """在检查点上比对语义字段；版本对不上就拒绝。

    这里不接受"字段恰好相等"当证据。读数前复核三件事：

    1. 客户端**已应用**检查点的版本（落后就直接失败，不比字段）；
    2. 房主已发布的那一版就是检查点（没有被新的发布顶掉）；
    3. 房主**此刻**的状态指纹与检查点一致（节流窗口里房主可能又变了）。

    任何一条不成立都说明我们手里的期望快照与客户端手上的不是同一份状态，
    此时比对毫无意义——宁可失败也不混比不同版本。
    """

    here_client = client_checkpoint(client)
    if here_client.revision < checkpoint.revision:
        case.fail("客户端还没应用检查点版本（%s）：检查点 %s，客户端 %s"
                  % (note or "", checkpoint, here_client))
    here_host = host_checkpoint(host)
    if here_host != checkpoint:
        case.fail("读数的瞬间房主已经不在检查点上（%s）：期望 %s，实际 %s"
                  % (note or "", checkpoint, here_host))
    if not host_state_matches(host, checkpoint):
        case.fail("房主的状态在检查点之后又变了（%s）：检查点 %s，指纹已不同"
                  % (note or "", checkpoint))

    problems = [item for item in diff(expected_for(host.game, player_id),
                                      client_state(client))
                if not any(item == key or item.endswith("." + key)
                           for key in ignore)]
    if problems:
        case.fail("视图不一致（%s，读数点 %s）：\n  "
                  % (note or "", checkpoint) + "\n  ".join(problems))
    return checkpoint


def agree_or_fail(case, pump, host, client, player_id, *, timeout=8.0,
                  note="", ignore=()):
    """等两端到同一版本，再断言语义字段一致。

    返回读数点（``SyncCheckpoint``），方便失败信息与报告引用"这次比的是哪一版"。
    """

    checkpoint, why = wait_for_sync(pump, host, client, timeout=timeout)
    if checkpoint is None:
        case.fail("没能建立同步检查点（%s）：%s" % (note or "", why))
    return compare_at_checkpoint(case, host, client, player_id, checkpoint,
                                 note=note, ignore=ignore)


def assert_hidden(case, client, owner_id, card_ids, *, source="adapter"):
    """按玩家视角校验隐藏信息：这些牌**不该**出现在这名客户端的视图里。

    校验的是客户端手上的数据（同步 + 适配之后），而不是网络载荷——载荷审计
    由 ``ClientSide.audit_hidden_cards`` 负责，两者互补。

    读的是**忠实**结果：``hand_ids`` 给出实际收到的牌身份，``hand_faces``
    给出实际收到的牌面。合法的匿名占位（``token``）与空牌背（``blank``）都
    不算泄露，但会被写进失败信息里，方便区分"没看到"与"看到了"。
    """

    state = client_state(client, source=source)
    owner_id = _text(owner_id)
    mine = _text(state.get("local_player_id", ""))
    if mine == owner_id:
        case.fail("隐藏信息校验用错了视角：%s 就是本人" % owner_id)

    entry = state["players"].get(owner_id)
    if entry is None:
        case.fail("客户端 %s 的视图里没有 %s 这个座位" % (mine, owner_id))

    kinds = hand_face_kinds(client, owner_id, source=source)
    problems = []
    leaked = list(entry.get("hand_ids") or ())
    if leaked:
        problems.append("拿到了 %s 的手牌身份：%s" % (owner_id, leaked))
    faces = list(entry.get("hand_faces") or ())
    if faces:
        problems.append("拿到了 %s 的手牌牌面：（牌 id, 牌名, 花色, 点数）= %s"
                        % (owner_id, faces))

    wanted = {_text(item) for item in card_ids}
    found = [item for item in leaked if item in wanted]
    if found:
        problems.append("其中这些牌是明确不该可见的：%s" % (found,))

    if problems:
        case.fail("客户端 %s 看到了它不该看的东西（该座位手牌三档=%s）：%s"
                  % (mine, dict(zip(("real", "token", "blank"),
                                    (kinds.count(FACE_REAL), kinds.count(FACE_TOKEN),
                                     kinds.count(FACE_BLANK)))),
                     "；".join(problems)))
    return True


# ==================================================
# 6. 决策可操作性
#
# Phase 14.1 问题 2：原来的实现只确认"回答者 + match_id"，客户端根本没有
# 面板（decision=None）时也会返回可操作。判断"能不能继续操作"必须问到：
# 请求送到没有、编号对不对、版本够不够、状态是不是还在等他答。
# ==================================================

STATE_IDLE = "idle"                  # 没有待回答的请求，谁都不用操作
STATE_OPERABLE = "operable"          # 面板在手、编号对应、版本到位、可以提交
STATE_WAITING = "waiting"            # 房主在等他，但面板还没送到客户端手上
STATE_WAITING_VIEW = "waiting_view"  # 面板被挂在"等视图"：依据的版本还没应用
STATE_COVERED = "covered"            # 被更高优先级的请求盖住了（等它结束会恢复）
STATE_ANSWERED = "answered"          # 已提交，等房主确认（不能再点）
STATE_CLOSED = "closed"              # 手上那条请求已经不是房主在等的那条
STATE_WRONG_PLAYER = "wrong_player"  # 此刻要答的不是这名玩家
STATE_UNSYNCED = "unsynced"          # 对局标识对不上 / 客户端还没建立对局


@dataclass(frozen=True)
class DecisionOperability:
    """一次可操作性判定的结果。两个语义分开表达，别混用：

    * ``operable``：**此刻就能提交**答案（只有 ``STATE_OPERABLE`` 为真）；
      "没有待回答的请求"是健康的，但不是"可以操作"。
    * ``mine``：这条请求**归这名玩家**（只是可能还没送到 / 被盖住 / 已提交）。
      校验"责任归属"时用它，校验"现在能不能点"时用 ``operable``。
    """

    operable: bool
    state: str
    note: str
    request_id: int = 0            # 房主的线上请求编号（0 = 没有）
    engine_request_id: int = 0     # 对应的引擎 PendingRequest 编号（0 = 非引擎请求）
    player_id: str = ""

    #: 这些状态说明"事情就是这名玩家的"，只是未必现在可提交。
    MINE_STATES = frozenset({STATE_OPERABLE, STATE_WAITING, STATE_WAITING_VIEW,
                            STATE_ANSWERED, STATE_COVERED})

    @property
    def mine(self):
        return self.state in DecisionOperability.MINE_STATES

    def __bool__(self):
        return self.operable

    def describe(self):
        return "state=%s mine=%s %s" % (self.state, self.mine, self.note)


def _open_decision_for(host, player_id):
    """房主侧登记表里这名玩家当前**打开**的网络决策（没有则 None）。"""

    match = getattr(host, "match", None)
    registry = getattr(match, "registry", None)
    if registry is None:
        return None
    try:
        return registry.current_for(_text(player_id))
    except Exception:                                   # pragma: no cover - 兜底
        return None


def _local_engine_request(pending):
    """登记表条目 ``local`` 字段里的引擎请求（"pending" / "group" / "turn"）。"""

    local = tuple(getattr(pending, "local", None) or ())
    if not local:
        return None, ""
    kind = _text(local[0])
    if kind in ("pending", "group") and len(local) >= 2:
        return local[1], kind
    return None, kind


def decision_is_operable(host, client, *, expect_player_id=None):
    """当前请求还能不能由**正确的那名玩家**继续操作。

    返回 ``DecisionOperability``（``.operable`` / ``.state`` / ``.note``）。
    判定依次确认：

    1. 对局标识一致（客户端不是停在上一局）；
    2. 房主此刻到底在等谁——引擎 ``PendingRequest``（含共享响应阶段的成员）
       与"线上决策登记表"两边都要看，出牌阶段这类**不是**引擎请求的决策走后者；
    3. 这份客户端持有的请求编号**就是**房主在等的那一条；
    4. 协议要求的视图版本条件（请求带的 ``base_revision`` 客户端已经应用），
       否则面板还被挂在"等视图"上；
    5. 客户端此刻的状态：可提交 / 已提交等确认 / 已经过期。

    关键：**面板不在手上（``decision=None``）一律不可操作**——房主还在等，
    但请求根本没送到这名玩家，那是链路问题，不是"可以继续操作"。
    """

    game = host.game
    match = getattr(client, "match", None)
    view = getattr(client, "view", None)
    mine = _text(getattr(view, "local_player_id", "") if view is not None else "")

    if expect_player_id is not None and mine != _text(expect_player_id):
        return DecisionOperability(
            False, STATE_UNSYNCED,
            "这份客户端是 %s，预期是 %s" % (mine, expect_player_id), player_id=mine)

    if match is None:
        return DecisionOperability(False, STATE_UNSYNCED,
                                   "客户端还没有建立对局", player_id=mine)

    host_match_id = _text(getattr(game, "match_id", ""))
    client_match_id = _text(getattr(match, "match_id", ""))
    if host_match_id and client_match_id and host_match_id != client_match_id:
        return DecisionOperability(
            False, STATE_UNSYNCED,
            "客户端还在上一局：房主 %s，客户端 %s" % (host_match_id, client_match_id),
            player_id=mine)

    pending = _open_decision_for(host, mine)
    responsible, note = pending_responsible(game)
    engine_request_id = 0
    if pending is not None:
        wire_id = int(getattr(pending, "request_id", 0) or 0)
        engine_request, _kind = _local_engine_request(pending)
        engine_request_id = int(getattr(engine_request, "request_id", 0) or 0)
    else:
        wire_id = 0

    held = getattr(match, "decision", None)
    held_id = int(held.get("request_id") or 0) if held else 0

    # 房主没有在等任何人，也没有开着的线上决策：谁都不用操作。
    if pending is None and not responsible:
        if held_id:
            return DecisionOperability(
                False, STATE_CLOSED,
                "房主已经不等任何人，客户端手上却还有请求 #%d" % held_id,
                request_id=held_id, player_id=mine)
        return DecisionOperability(False, STATE_IDLE, "没有待回答的请求",
                                   player_id=mine)

    # 引擎在等别人，且线上决策也不是这名玩家的：不归他答。
    if pending is None and mine not in responsible:
        if held_id:
            return DecisionOperability(
                False, STATE_CLOSED,
                "手上那条请求 #%d 已经不是房主在等的那条（等的是 %s）"
                % (held_id, responsible or "别人"),
                request_id=held_id, player_id=mine)
        return DecisionOperability(
            False, STATE_WRONG_PLAYER,
            "请求等的是 %s，这份客户端是 %s" % (responsible or ["?"], mine),
            player_id=mine)

    if pending is None and mine in responsible:
        # 引擎在等这名玩家，但房主还没把线上决策开出来（正在翻译 / 本地已自动处理）。
        return DecisionOperability(
            False, STATE_WAITING,
            "引擎在等 %s，但房主还没有把请求下发出去（%s）" % (mine, note),
            engine_request_id=int(getattr(game.pending_request, "request_id", 0) or 0),
            player_id=mine)

    # 到这里：房主侧有一条打开的网络决策属于这名玩家。
    if not held_id:
        return DecisionOperability(
            False, STATE_WAITING,
            "房主已经把请求 #%d 发出来了，但客户端手上还没有面板" % wire_id,
            request_id=wire_id, engine_request_id=engine_request_id, player_id=mine)
    if held_id != wire_id:
        return DecisionOperability(
            False, STATE_CLOSED,
            "客户端的请求编号 #%d 与房主在等的 #%d 不是同一条"
            % (held_id, wire_id),
            request_id=held_id, engine_request_id=engine_request_id, player_id=mine)

    # 协议要求的视图版本条件：请求依据的版本客户端必须已经应用。
    base = int((held or {}).get("base_revision") or 0)
    applied = int(getattr(match, "revision", 0) or 0)
    if base and applied < base:
        return DecisionOperability(
            False, STATE_WAITING_VIEW,
            "客户端视图还没到位：请求依据 r%d，客户端已应用 r%d" % (base, applied),
            request_id=wire_id, engine_request_id=engine_request_id, player_id=mine)

    if getattr(match, "_held_decision", None) is not None:
        return DecisionOperability(
            False, STATE_WAITING_VIEW, "请求被挂起在'等视图'，面板还没开",
            request_id=wire_id, engine_request_id=engine_request_id, player_id=mine)

    if getattr(match, "answered", False) or getattr(match, "waiting_ack", False):
        return DecisionOperability(
            False, STATE_ANSWERED,
            "已经提交 #%d，正在等房主确认（不能再点）" % wire_id,
            request_id=wire_id, engine_request_id=engine_request_id, player_id=mine)

    # 嵌套：引擎在等的是**栈顶**那条请求。手上这条若被它盖住，现在答了也会被
    # 房主拒绝（Phase 14 场景 C 实测过），所以它此刻不算"可以继续操作"——
    # 等内层结束、外层被重新驱动之后才会恢复。
    engine_request, kind = _local_engine_request(pending)
    if kind in ("pending", "group") and engine_request is not None:
        top = getattr(game, "pending_request", None)
        top_id = int(getattr(top, "request_id", 0) or 0)
        if top_id and engine_request_id and engine_request_id != top_id:
            return DecisionOperability(
                False, STATE_COVERED,
                "手上的请求 #%d 被引擎当前在等的 #%d 盖住了，等它结束会恢复"
                % (engine_request_id, top_id),
                request_id=wire_id, engine_request_id=engine_request_id,
                player_id=mine)

    member_note = ""
    if kind == "group" and engine_request is not None:
        # 共享响应阶段：本轮自己还没答过才算能操作。
        player = None
        for candidate in game.seats.all_players():
            if _text(candidate.player_id) == mine:
                player = candidate
                break
        status = engine_request.member_status(player) if player is not None else ""
        if status != "pending":
            return DecisionOperability(
                False, STATE_ANSWERED,
                "共享响应阶段里你这轮的状态是 %r" % (status,),
                request_id=wire_id, engine_request_id=engine_request_id,
                player_id=mine)
        member_note = "（本轮状态 pending）"

    return DecisionOperability(
        True, STATE_OPERABLE, (note or "可以继续操作") + member_note,
        request_id=wire_id, engine_request_id=engine_request_id, player_id=mine)
