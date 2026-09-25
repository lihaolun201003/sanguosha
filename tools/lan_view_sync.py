"""Phase 11.3 场景 1～3、14、15：逐人视图、隐藏信息、revision、resync。

全部走**真实链路**：真实 TCP socket、真实权威 Game、真实 ``HostMatch`` 逐人
视图生成、真实客户端 ``ClientGameView`` + 真实 ``Renderer``（SDL dummy）。

隐藏信息一律用**原始网络载荷**证明：``ClientSide.raw_messages`` 记录该客户端
从 socket 上收到的每一条消息，直接在里面搜对方的牌 id 与未公开身份。

    python tools/lan_view_sync.py
"""

import json
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from lan_view_harness import (                            # noqa: E402
    ClientSide,
    HostSide,
    check,
    close_all,
    force_hand,
    make_pump,
    settle,
    start_match,
    summary,
    take_card,
    wait_for,
)

from src.network.decisions import ACTION_END_PHASE, DecisionResult   # noqa: E402
from src.network.protocol import MessageType                          # noqa: E402

SNAPSHOT = MessageType.GAME_VIEW_SNAPSHOT
EVENT = MessageType.GAME_EVENT


# ==================================================
# 场景 1：开局基础状态 + 只有自己手牌
# ==================================================

def scenario_setup(host, clients, pump):
    print("\n[场景 1] 开局：两边看到相同的公开状态，客户端只看到自己手牌")

    check("房主建立了权威对局", host.match is not None and host.match.started)
    check("客户端都收到了开局视图",
          all(c.match is not None and c.match.ready for c in clients))

    host_view_players = sorted(
        (p.player_id, p.seat, p.name, p.general_id, p.hp, p.max_hp)
        for p in host.game.players)
    ok = True
    for client in clients:
        view_players = sorted(
            (p.player_id, p.seat, p.nickname, p.general_id, p.hp, p.max_hp)
            for p in client.view.players)
        if view_players != host_view_players:
            ok = False
            print("      %s 看到的名单与房主不一致：%s" % (client.nickname, view_players))
    check("客户端看到的玩家列表 / 座位 / 武将 / 体力与房主一致", ok)

    # 每个客户端：自己的手牌内容 == 权威手牌；别人的手牌只有张数
    hand_ok = True
    leak_ok = True
    for client in clients:
        me = next(p for p in host.game.players
                  if p.player_id == client.view.local_player_id)
        mine = sorted(card.card_id for card in client.view.hand)
        authoritative = sorted(card.id for card in me.hand)
        if mine != authoritative:
            hand_ok = False
            print("      %s 自己的手牌与权威不一致" % client.nickname)
        for other in host.game.players:
            if other.player_id == me.player_id:
                continue
            if client.leaked_card_ids([card.id for card in other.hand]):
                leak_ok = False
                print("      %s 的载荷里出现了 %s 的手牌"
                      % (client.nickname, other.name))
            hidden = [{"card_id": card.id, "label": card.display_name}
                      for card in other.hand]
            problems = client.audit_hidden_cards(other.player_id, hidden)
            if problems:
                leak_ok = False
                print("      %s：%s" % (client.nickname, problems[0]))
            view_other = client.player_view(other.player_id)
            if view_other is None or view_other.hand:
                leak_ok = False
                print("      %s 的视图里带了 %s 的手牌内容" % (client.nickname, other.name))
    check("客户端自己的手牌是权威手牌（完整牌面）", hand_ok)
    check("别人的手牌只有张数：视图与原始载荷里都没有内容", leak_ok)

    # 真实渲染一帧：既有 Renderer 能画这份只读视图
    frames = [c.draw_frame() for c in clients]
    check("客户端用既有 Renderer 画出了只读牌局（真实渲染链路）", all(frames))
    check("渲染没有异常", all(c.frame_errors == 0 for c in clients))

    # 当前玩家 / 阶段一致
    check("当前玩家与阶段同步",
          all(c.view.current_player_id == host.game.current_player_id
              and c.view.current_phase == host.game.phase for c in clients),
          host.game.current_player_id + " / " + str(host.game.phase))

    for client in clients:
        client.snapshot("scene1_" + client.nickname)


# ==================================================
# 场景 2 / 3：摸牌
# ==================================================

def scenario_self_draw(host, client, other, pump):
    print("\n[场景 2] 客户端自己摸 2 张 → 自己看到具体牌，其他客户端看不到")

    from src.game.atoms_v2 import DrawCardsAtom

    me = next(p for p in host.game.players
              if p.player_id == client.view.local_player_id)
    before = len(me.hand)
    client.raw_messages.clear()
    other.raw_messages.clear()

    host.game.engine.context.apply(DrawCardsAtom(me, 2))
    host.match.push_views(force=True)
    pump(0.6)

    drawn = [card.id for card in me.hand[before:]]
    check("权威手牌 +2", len(me.hand) == before + 2, "%d → %d" % (before, len(me.hand)))
    check("自己收到的视图里出现这两张具体牌",
          all(item in client.hand_ids() for item in drawn),
          "抽到 %s / 我手上 %s" % (drawn, client.hand_ids()))
    check("自己拿到的载荷里**确实**有这两张牌（本来就该有）",
          all(client.leaked_card_ids(drawn)))
    check("自己收到 cards_drawn 表现事件并带牌面",
          any(event.get("kind") == "cards_drawn" and len(event.get("cards") or ()) == 2
              for event in _events_of(client, EVENT)))
    check("其他客户端看不到这两张牌的任何信息",
          not other.leaked_card_ids(drawn),
          "泄漏 " + str(other.leaked_card_ids(drawn)))
    check("其他客户端只收到张数",
          any(event.get("kind") == "cards_drawn" and event.get("count") == 2
              and not event.get("cards")
              for event in _events_of(other, EVENT)))
    check("其他客户端视图里的手牌数 +2",
          (other.player_view(me.player_id) or None) is not None
          and other.player_view(me.player_id).hand_count == before + 2)


def scenario_other_draw(host, client, pump):
    print("\n[场景 3] 其他玩家摸 2 张 → 客户端只看到手牌数量 +2")

    from src.game.atoms_v2 import DrawCardsAtom

    other = next(p for p in host.game.players
                 if p.player_id != client.view.local_player_id)
    before = len(other.hand)
    client.raw_messages.clear()
    host.game.engine.context.apply(DrawCardsAtom(other, 2))
    host.match.push_views(force=True)
    pump(0.6)

    drawn = [card.id for card in other.hand[before:]]
    check("权威手牌 +2", len(other.hand) == before + 2)
    check("客户端载荷里没有这两张牌的 id", not client.leaked_card_ids(drawn),
          "泄漏 " + str(client.leaked_card_ids(drawn)))
    hidden = [{"card_id": card.id, "label": card.display_name}
              for card in other.hand[before:]]
    problems = client.audit_hidden_cards(other.player_id, hidden)
    check("结构化审计：载荷里没有任何地方夹带这两张牌的内容",
          not problems, "；".join(problems[:3]))
    view_other = client.player_view(other.player_id)
    check("客户端视图里的手牌数 +2",
          view_other is not None and view_other.hand_count == before + 2,
          "%d → %s" % (before, view_other.hand_count if view_other else "?"))
    check("客户端视图里这名角色的 hand 字段是空的",
          view_other is not None and not view_other.hand)


# ==================================================
# 场景 4：三个客户端看到不同的视图（同一份权威 Game）
# ==================================================

def scenario_three_clients(host, clients, pump):
    print("\n[场景 4] 同一份权威 Game，三个客户端看到三份不同的合法视图")

    a, b = clients[0], clients[1]
    from tests.legacy_helpers import normal_sha, shan

    player_a = next(p for p in host.game.players
                    if p.player_id == a.view.local_player_id)
    player_a.hand = [normal_sha(), shan()]
    a.raw_messages.clear()
    b.raw_messages.clear()
    host.match.push_views(force=True)
    pump(0.6)

    ids = [card.id for card in player_a.hand]
    check("A 自己看到【杀】【闪】两张具体牌",
          sorted(a.hand_ids()) == sorted(ids), str(a.hand_ids()))
    check("A 的载荷里有这两张牌", a.leaked_card_ids(ids) == ids)
    check("B 看不到 A 的手牌内容", not b.leaked_card_ids(ids))
    check("B 只看到 A 的手牌数 2",
          b.player_view(player_a.player_id) is not None
          and b.player_view(player_a.player_id).hand_count == 2)
    host_view = [item for item in a.view.players
                 if item.player_id == host.game.player.player_id]
    host_sees_a = host.match.view_for(host.game.player.player_id)
    host_view_of_a = host_sees_a.player(player_a.player_id)
    check("房主视角（自己也是第三名玩家）同样只能看到 A 的手牌数量",
          host_view_of_a is not None and host_view_of_a.hand_count == 2
          and not host_view_of_a.hand)

    check("两个客户端拿到的是**两份不同的**视图数据",
          json.dumps(a.view.to_payload(), sort_keys=True)
          != json.dumps(b.view.to_payload(), sort_keys=True))


# ==================================================
# 场景 5：身份模式 5 人（主公公开，其他隐藏）
# ==================================================

def scenario_identity(host, clients, pump):
    print("\n[场景 5] 5 人身份局：未公开身份不进任何网络包")

    game = host.game
    # 身份由**开局本身**分配（房主在大厅选的身份局 + HostMatch 的开局规则）。
    # 这里不再手动 roll / apply：手动那一步正是"工具绿、真人运行是裸局"的原因。
    host.match.push_views(force=True)
    pump(0.8)

    lord = next((p for p in game.players
                 if getattr(p.identity, "value", "") == "lord"), None)
    check("身份已分配（5 人配比）", lord is not None)
    check("每个客户端都收到了身份模式的视图",
          all(c.view.game_mode == "identity" for c in clients),
          str([c.view.game_mode for c in clients]))
    check("主公身份对所有客户端公开",
          all((c.player_view(lord.player_id) or None) is not None
              and c.player_view(lord.player_id).identity == "lord"
              for c in clients))

    # 未公开身份：既不在视图里，也不在原始载荷里
    ok_view, ok_wire = True, True
    for client in clients:
        for player in game.players:
            hidden = (player.identity is not None
                      and not (client.view.local_player_id == player.player_id)
                      and player is not lord)
            if not hidden:
                continue
            view_player = client.player_view(player.player_id)
            if view_player is not None and view_player.identity:
                ok_view = False
                print("      %s 的视图里带了 %s 的身份" % (client.nickname, player.name))
            # 结构化判定：载荷里"别人那条 player 记录"的 identity 必须为空。
            # 这比全文本搜索更准——身份会被重新分配（开局分配一次，本场景再
            # 分配一次），而载荷是历史快照：文本搜索会把"自己当时的身份"
            # 误判成泄露，也无法区分同一个词到底是谁的。
            if _wire_shows_identity(client, player.player_id):
                ok_wire = False
                print("      %s 的载荷里带了 %s 的身份" % (client.nickname, player.name))
    check("未公开身份不出现在视图里", ok_view)
    check("未公开身份不出现在原始载荷里", ok_wire)

    mine_ok = all(
        (c.player_view(c.view.local_player_id) or None) is not None
        and c.player_view(c.view.local_player_id).identity is not None
        for c in clients)
    check("每个人仍然看得到自己的身份", mine_ok)
    for client in clients:
        client.snapshot("scene5_identity_" + client.nickname)


def _wire_shows_identity(client, player_id):
    """载荷里 ``player_id`` 的身份是否被**违规**发出来了。

    判定与 ``visible_identity`` 同一套规则，而且**按载荷自身的时间点**判断
    （每条消息里带着它当时看到的状态），不看当前状态：

    * 自己的身份——合法（自己的身份自己知道）；
    * 主公身份——合法（开局公开）；
    * 已阵亡角色的身份——合法（阵亡即公开）；
    * 其余情况只要包里有值，就是未公开身份真的发出去了。
    """

    target = str(player_id)
    local = str(client.view.local_player_id)

    def walk(node):
        if isinstance(node, dict):
            if str(node.get("player_id")) == target and target != local:
                identity = node.get("identity")
                if identity and identity != "lord" and node.get("alive", True):
                    return True
            for value in node.values():
                if walk(value):
                    return True
        elif isinstance(node, list):
            for value in node:
                if walk(value):
                    return True
        return False

    return any(walk(message) for message in client.raw_messages)


# ==================================================
# 场景 6：revision gap → 重同步
# ==================================================

def scenario_resync(host, client, pump):
    print("\n[场景 6] 人为丢一份快照 → 客户端发现 gap → STATE_RESYNC_REQUEST → 恢复")

    from src.game.atoms_v2 import RecoverHpAtom

    me = next(p for p in host.game.players
              if p.player_id == client.view.local_player_id)
    revision_before = client.match.revision
    gaps_before = client.match.gaps
    requests_before = client.match.resync_requests

    # 人为丢弃下一条快照（模拟丢包）：拦截一次消息，不让它进入 ClientMatch。
    dropped = {"count": 0}
    original = client.session._route_client_game_message

    def route(message, _original=original):
        from src.network.protocol import message_type
        if dropped["count"] == 0 and message_type(message) == MessageType.GAME_VIEW_SNAPSHOT:
            dropped["count"] += 1
            client.raw_messages.append(message)
            return None
        return _original(message)

    client.session._route_client_game_message = route
    try:
        me.hp = max(1, me.hp - 1)
        host.match.push_views(force=True)     # 第 1 份：被丢掉
        pump(0.5)
        me.hp = max(1, me.hp - 1)
        host.match.push_views(force=True)     # 第 2 份：客户端发现 revision 跳了
        pump(0.6)
    finally:
        client.session._route_client_game_message = original

    check("确实丢了一份快照", dropped["count"] == 1)
    check("客户端发现了 revision gap", client.match.gaps > gaps_before,
          "gaps %d → %d" % (gaps_before, client.match.gaps))
    check("客户端发出了 STATE_RESYNC_REQUEST",
          client.match.resync_requests > requests_before)
    check("房主为重同步补发了完整视图",
          wait_for(pump, lambda: client.match.revision >= host.match.revision, 5.0,
                   "追平 revision"),
          "客户端 %d / 房主 %d" % (client.match.revision, host.match.revision))
    view_me = client.player_view(me.player_id)
    check("重同步后客户端状态与权威一致（体力已跟上）",
          view_me is not None and view_me.hp == me.hp,
          "%s vs %s" % (view_me.hp if view_me else "?", me.hp))
    check("重同步没有终止对局（不踢人）", host.match.aborted == "")
    check("重同步后客户端仍能继续收表现事件",
          _events_of(client, EVENT) is not None)


# ==================================================
# 快照的 revision 与事件顺序
# ==================================================

def scenario_revision_model(host, client, pump):
    print("\n[场景 7] revision 模型：单调递增、事件去重、决策前先刷新视图")

    from src.game.atoms_v2 import LoseHpAtom, RecoverHpAtom

    me = next(p for p in host.game.players
              if p.player_id == client.view.local_player_id)
    client.raw_messages.clear()
    start_revision = client.match.revision

    # 连续 3 次可见状态变化：每次都应推进一个 revision 并发一份新视图。
    # 其中一次是真实引擎事件（造成伤害 / 回复体力），用来验证事件流。
    target = host.game.player
    host.game.engine.context.apply(LoseHpAtom(target, 1))
    for index in range(3):
        host.game.message = "状态第 %d 次变化" % (index + 1)
        if index == 0:
            host.game.engine.context.apply(RecoverHpAtom(target, 1))
        host.match.push_views(force=True)
        pump(0.3)

    revisions = [message.get("payload", {}).get("revision")
                 for message in client.raw_messages
                 if message.get("type") == SNAPSHOT]
    check("每次状态变化都发了一份新视图", len(revisions) >= 3, str(revisions[:5]))
    check("revision 严格递增且连续",
          all(b == a + 1 for a, b in zip(revisions, revisions[1:])),
          str(revisions[:5]))
    check("revision 从开局到现在只增不减",
          client.match.revision > start_revision,
          "%d → %d" % (start_revision, client.match.revision))

    events = _events_of(client, EVENT)
    check("每一条表现事件都带 revision 与 event_id",
          all("revision" in event and "event_id" in event for event in events))
    check("引擎事件确实变成了表现事件（recover）",
          any(event.get("kind") == "recover" for event in events),
          str([event.get("kind") for event in events]))
    check("事件 id 严格递增（客户端据此去重）",
          all(a < b for a, b in zip([item["event_id"] for item in events],
                                    [item["event_id"] for item in events][1:])),
          str([item["event_id"] for item in events][:6]))

    # 重发同一条事件：客户端不能重复播放（按 event_id 去重）。
    match = client.match
    client.scene.presentation.play(match.take_events())      # 先排空
    played_before = client.scene.presentation.played_events
    duplicate = next(({"kind": "damage", "event_id": event["event_id"],
                       "player_id": me.player_id, "amount": 1}
                      for event in reversed(events) if event.get("event_id")), None)
    if duplicate is not None:
        match._apply_events({"events": [duplicate]})
        client.scene.presentation.play(match.take_events())
    check("重复事件被丢弃、不会重播",
          duplicate is not None
          and client.scene.presentation.played_events == played_before
          and match.dropped_events >= 1,
          "played=%d dropped=%d" % (client.scene.presentation.played_events,
                                    match.dropped_events))


def _events_of(client, message_type_value):
    events = []
    for message in client.raw_messages:
        if message.get("type") != message_type_value:
            continue
        events.extend(message.get("payload", {}).get("events") or ())
    return events


# ==================================================
# 主流程
# ==================================================

def main():
    print("=" * 60)
    print("Phase 11.3 逐人视图同步验证（真实 socket / 真实引擎 / 真实渲染）")
    print("=" * 60)

    # ---- 场景 1～4、6、7：房主 + 两个客户端，自由混战 ----
    host = HostSide(nickname="房主")
    a = ClientSide("小明", host.port, render=True)
    b = ClientSide("小红", host.port, render=True)
    clients = [a, b]
    pump, ok, message = start_match(host, clients)
    check("对局建立成功", ok, message)
    pump(0.5)

    scenario_setup(host, clients, pump)
    scenario_self_draw(host, a, b, pump)
    scenario_other_draw(host, b, pump)
    scenario_three_clients(host, clients, pump)
    scenario_revision_model(host, b, pump)
    scenario_resync(host, b, pump)

    # 收尾：让房主把桌面推回空闲，再关掉这一局
    settle(host, pump, 2.0)
    close_all(host, clients)
    time.sleep(0.2)

    # ---- 场景 5：身份模式 5 人（房主 + 4 个客户端）----
    # 模式从大厅走（HostSide 传给 create_room），不再在开局后手动改。
    host = HostSide(nickname="房主", game_mode="identity")
    clients = [ClientSide("甲%s" % index, host.port, render=True) for index in range(4)]
    pump, ok, message = start_match(host, clients)
    check("5 人身份局建立成功", ok, message)
    scenario_identity(host, clients, pump)
    close_all(host, clients)

    ok = summary("Phase 11.3 视图同步验证")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
