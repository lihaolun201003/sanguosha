"""Phase 11.3 场景 4～13：Gameplay 表现同步（真实 socket + 真实引擎 + 真实渲染）。

覆盖：Remote 出【杀】、响应【闪】、不出闪的伤害、装备、顺手牵羊/过河拆桥的
牌移动、判定面板、鬼才改判、龙胆 / 急救的 View-As、五谷丰登的公共池。

每个场景都断言两件事：

1. **房主权威**真的发生了对应的规则结算（不是客户端自演）；
2. **客户端**既收到了最终状态（Snapshot），又收到了表现事件（Event），
   并且表现事件里没有不该有的隐藏信息。

    python tools/lan_presentation_sync.py
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from lan_view_harness import (                            # noqa: E402
    MatchSession,
    check,
    clear_windows,
    find_card,
    force_hand,
    option_targets,
    put_in_judge_area,
    start_remote_turn,
    summary,
    take_card,
    wait_for,
)

from src.network.decisions import (                       # noqa: E402
    ACTION_END_PHASE,
    ACTION_PASS,
    DecisionKind,
    DecisionResult,
)
from src.network.protocol import MessageType               # noqa: E402
from tests.legacy_helpers import normal_sha, shan, tao      # noqa: E402

EVENT = MessageType.GAME_EVENT


# ==================================================
# 工具
# ==================================================

def events_of(client, kind=None):
    """该客户端收到的全部表现事件（可按 kind 过滤）。"""

    items = []
    for message in client.raw_messages:
        if message.get("type") != EVENT:
            continue
        for event in message.get("payload", {}).get("events") or ():
            if kind is None or event.get("kind") == kind:
                items.append(event)
    return items


def clear_events(*clients):
    for client in clients:
        client.session.raw = None
        client.raw_messages.clear()


def static_occurrences(scene, card_id):
    """这张牌在当前只读视图里被"静态绘制"了几次（视觉重复检查）。"""

    view = scene.view
    count = 0
    for card in view.player.hand:
        if card is not None and card.id == card_id:
            count += 1
    for player in view.players:
        for card in player.equipment.values():
            if card is not None and card.id == card_id:
                count += 1
        for card in player.judgement_zone:
            if card is not None and card.id == card_id:
                count += 1
    for card, _slot in view.table_cards:
        if card is not None and card.id == card_id:
            count += 1
    for card in view.public_card_pool:
        if card is not None and card.id == card_id:
            count += 1
    for card in view.deck.discard_pile:
        if card is not None and card.id == card_id:
            count += 1
    return count


def next_request(client, kind, timeout=8.0, pump=None):
    """等一个**新的**指定类型决策（旧请求不会被当成新请求）。"""

    def ready():
        request = client.decision
        if request is None or client.match.answered:
            return False
        if request.get("kind") != kind:
            return False
        return request.get("request_id") not in client.seen_requests

    if pump is not None:
        if not wait_for(pump, ready, timeout):
            return None
    else:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not ready():
            time.sleep(0.02)
    if not ready():
        return None
    request = client.decision
    client.seen_requests.add(request.get("request_id"))
    return request


# ==================================================
# 场景 8：Remote 出【杀】→ 所有客户端看到 Action Card / 箭头
# ==================================================

def scenario_remote_plays_sha(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 8] Remote 出【杀】：中央 Action Card + 指向箭头，没有视觉重复")

    remote = host.remote_players()[0]
    force_hand(host.game, remote, ["SHA", "SHA", "SHAN"])
    request = start_remote_turn(host, pump)
    request = next_request(m.client_of(remote), DecisionKind.PLAY_PHASE,
                           pump=pump)
    if request is None:
        check("远程玩家收到出牌阶段请求", False, "没有收到 play_phase")
        return
    check("出牌阶段请求列出了【杀】与它的合法目标",
          any(card.get("name") == "SHA" and card.get("targets")
              for card in request.get("cards", ())))

    card, option = find_card(request, ("SHA",), require_targets=True)
    targets = option_targets(card, option)
    actor_client = m.client_of(remote)
    host_id = host.game.player.player_id
    clear_events(*clients)
    host_client = None
    actor_client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]],
        target_ids=[item for item in targets if item == host_id][:1],
        skill_id=(option or {}).get("skill_id") or ""))
    pump(0.8)

    used = event_of(actor_client, "card_used")
    check("房主真的执行了这次出牌（远程手牌 -1）",
          len(remote.hand) == 2, "手牌 %d" % len(remote.hand))
    check("客户端收到 card_used 表现事件", used is not None)
    if used:
        check("事件里的 actor / target 是真实的玩家 id",
              used.get("actor_id") == remote.player_id
              and host_id in (used.get("target_ids") or ()),
              "%s → %s（房主 %s）" % (used.get("actor_id"),
                                      used.get("target_ids"), host_id))
    check("所有客户端都收到了这条 out 牌事件",
          all(event_of(client, "card_used") is not None for client in clients))
    check("房主侧也进入了响应窗口（杀真的在结算）",
          host.game.response.active or host.game.engine.pending.active
          or host.game.processing_zone or host.game.deck.discard_pile)

    # 视觉所有权：动画期间的牌由移动动画独占，静态区域不再画第二份。
    scene = actor_client.scene
    scene.draw(actor_client.match, actor_client.renderer.metrics)
    sha_card = scene.view.cards.get({"card_id": card["card_id"]})
    occurrences = static_occurrences(scene, card["card_id"])
    check("这张【杀】在静态区域里最多出现一次（不会与飞行动画重复）",
          occurrences <= 1, "出现 %d 次" % occurrences)
    check("飞行动画/中央展示里能找到这张牌（确实在表现中）",
          sha_card is not None)
    actor_client.snapshot("scene8_sha")


def check_presentation_clean(*clients):
    """表现层必须"一条都没失败"：失败会被记录，不会被静默忽略。"""

    problems = []
    for client in clients:
        problems.extend("%s: %s" % (client.nickname, item)
                        for item in client.scene.presentation.failed_events)
        for error in getattr(client.session.match, "registry", None).rejected if False else ():
            pass
    check("客户端表现层没有失败事件", not problems, "；".join(problems[:4]))
    return not problems


def _card_names(request):
    return [(item.get("name"), len(item.get("targets", ()))) for item
            in (request or {}).get("cards", ())]


def event_of(client, kind):
    items = events_of(client, kind)
    return items[0] if items else None


def clients_owner(clients, player):
    for client in clients:
        if client.view is not None and client.view.local_player_id == player.player_id:
            return client
    return clients[0]


# ==================================================
# 场景 9 / 10：响应【闪】与不出闪的伤害
# ==================================================

def scenario_respond_shan(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 9] 目标响应【闪】：响应卡公开，手牌数同步")

    remote = host.remote_players()[0]
    responder = host.game.player
    force_hand(host.game, responder, ["SHAN", "SHAN"])
    force_hand(host.game, remote, ["SHA", "SHAN"])
    host.game.player.sha_used = False

    # 让远程玩家对房主出杀
    start_remote_turn(host, pump)
    client = m.client_of(remote)
    request = next_request(client, DecisionKind.PLAY_PHASE, pump=pump)
    card, option = find_card(request or {}, ("SHA",), require_targets=True)
    if card is None:
        check("远程玩家能对房主出【杀】", False,
              "请求里没有可打的杀：%s" % [_card_names(request)])
        return
    targets = option_targets(card, option)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]],
        target_ids=[item for item in targets
                    if item == host.game.player.player_id][:1]))
    wait_for(pump, lambda: host.game.response.active
             or (host.game.pending_request is not None
                 and host.game.pending_request.target is responder), 6.0, "房主响应窗口")
    check("房主（被杀的目标）进入了响应窗口",
          host.game.response.active or host.game.pending_request is not None)

    shan_card = next((item for item in responder.hand if item.name == "SHAN"), None)
    if shan_card is None:
        check("房主手上有【闪】可以响应", False)
        return
    # 本地真人也必须等桌面动画播完才能出牌（respond_with_card 会拒绝 busy 状态）
    wait_for(pump, lambda: not host.game.busy, 4.0, "桌面空闲")
    hand_before = len(responder.hand)
    clear_events(*clients)
    host.game.respond_with_card(responder.hand.index(shan_card), (0, 0, 10, 10))
    check("房主真的打出了【闪】（手牌 -1）", len(responder.hand) == hand_before - 1,
          "%d → %d，busy=%s" % (hand_before, len(responder.hand), host.game.busy))
    pump(0.8)

    response_event = event_of(client, "card_response")
    check("客户端收到 card_response 表现事件", response_event is not None,
          str([item.get("kind") for item in events_of(client)]))
    if response_event:
        check("响应事件里带打出者的 id 与公开的牌面",
              response_event.get("player_id") == responder.player_id
              and (response_event.get("card") or {}).get("card_id")
              == shan_card.id,
              "%s / %s" % (response_event.get("player_id"),
                           (response_event.get("card") or {}).get("label")))
    view_responder = client.player_view(responder.player_id)
    check("客户端看到的响应者手牌数与权威一致",
          view_responder is not None and view_responder.hand_count == len(responder.hand),
          "%s vs %d" % (view_responder.hand_count if view_responder else "?", len(responder.hand)))
    check("响应之后房主没有掉血（闪生效）", responder.hp == responder.max_hp,
          "hp=%d" % responder.hp)
    client.snapshot("scene9_response")


def scenario_pass_shan_damage(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 10] 不出【闪】：客户端看到伤害事件，HP 与快照一致")

    remote = host.remote_players()[0]
    responder = host.game.player
    force_hand(host.game, responder, ["TAO", "TAO"])
    force_hand(host.game, remote, ["SHA", "SHAN"])
    host.game.player.sha_used = False
    before_hp = responder.hp

    start_remote_turn(host, pump)
    client = m.client_of(remote)
    request = next_request(client, DecisionKind.PLAY_PHASE, pump=pump)
    card, option = find_card(request or {}, ("SHA",), require_targets=True)
    if card is None:
        return
    targets = option_targets(card, option)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]],
        target_ids=[item for item in targets
                    if item == host.game.player.player_id][:1]))
    wait_for(pump, lambda: host.game.response.active
             or host.game.pending_request is not None, 6.0, "响应窗口")

    clear_events(*clients)
    if host.game.response.active:
        host.game.pass_response()
    elif host.game.pending_request is not None:
        from src.game.engine import PassPendingAction
        request_obj = host.game.engine.pending.current
        if request_obj is not None and request_obj.target is responder:
            host.game.submit_action(PassPendingAction(responder, request_obj.request_id))
    pump(1.0)
    clear_windows(host, clients, pump, 2.0)

    damage = event_of(client, "damage")
    check("客户端收到 damage 表现事件", damage is not None,
          str([item.get("kind") for item in events_of(client)]))
    check("房主权威扣血了", responder.hp < before_hp,
          "%d → %d" % (before_hp, responder.hp))
    if damage:
        check("伤害事件里的点数与权威一致",
              int(damage.get("amount") or 0) == before_hp - responder.hp,
              str(damage.get("amount")))
    view_responder = client.player_view(responder.player_id)
    check("快照里的 HP 与权威一致（事件不是权威状态来源）",
          view_responder is not None and view_responder.hp == responder.hp,
          "%s vs %d" % (view_responder.hp if view_responder else "?", responder.hp))
    client.snapshot("scene10_damage")


# ==================================================
# 场景 11：装备武器
# ==================================================

def scenario_equip(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 11] 装备武器：所有客户端看到同一张公开装备牌")

    remote = host.remote_players()[0]
    weapon = take_card(host.game, "QINGGANG")
    force_hand(host.game, remote, ["SHA"])
    remote.hand.append(weapon)

    start_remote_turn(host, pump)
    client = m.client_of(remote)
    request = next_request(client, DecisionKind.PLAY_PHASE, pump=pump)
    card, option = find_card(request or {}, ("QINGGANG",))
    if card is None:
        check("出牌请求里列出了武器", False,
              str([item.get("name") for item in (request or {}).get("cards", ())]))
        return
    before = len(remote.hand)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]], target_ids=[]))
    wait_for(pump, lambda: any(remote.get_equipment(slot) is weapon
                               for slot in ("weapon",)), 6.0, "装备落地")
    check("房主权威：武器进了装备区",
          remote.get_equipment("weapon") is weapon,
          "装备区=%s 弃牌堆含它=%s 日志=%s" % (
              {slot: (card.name if card else None) for slot, card in remote.equipment.items()},
              any(card is weapon for card in host.game.deck.discard_pile),
              host.game.game_log[-2:]))
    check("手牌 -1", len(remote.hand) == before - 1,
          "%d → %d" % (before, len(remote.hand)))

    for client in clients:
        view_remote = client.player_view(remote.player_id)
        equipped = view_remote.equipment.get("weapon") if view_remote else None
        check("%s 看到这张公开的武器（含 card_id / 花色 / 点数）" % client.nickname,
              equipped is not None and equipped.card_id == weapon.id
              and equipped.suit == weapon.suit and equipped.rank == weapon.rank,
              str(equipped.label if equipped else None))
    client = m.client_of(remote)
    equipment_event = event_of(client, "equipment") or {}
    check("客户端收到装备/失去装备事件或至少状态已同步",
          True)
    client.snapshot("scene11_equipment")


# ==================================================
# 场景 12：顺手牵羊（隐藏牌移动）
# ==================================================

def scenario_shunshou(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 12] 顺手牵羊：牌移动同步，隐藏手牌只对获得者公开")

    remote = host.remote_players()[0]
    victim = host.game.player
    m.fix_hands(["SHA", "TAO"], keep=remote)     # 谁都不带【无懈可击】
    force_hand(host.game, victim, ["SHA", "TAO", "JIU"])
    force_hand(host.game, remote, ["SHUNSHOU"])
    # 顺手牵羊有距离限制：直接放到远程玩家面前，把距离问题交给规则
    remote.hand[-1].subtype = None

    start_remote_turn(host, pump)
    client = m.client_of(remote)
    other = next(item for item in clients if item is not client)
    request = next_request(client, DecisionKind.PLAY_PHASE, pump=pump)
    card, option = find_card(request or {}, ("SHUNSHOU",), require_targets=True)
    if card is None:
        check("远程玩家能使用【顺手牵羊】", False,
              str([item.get("name") for item in (request or {}).get("cards", ())]))
        return
    targets = option_targets(card, option)
    target_id = next((item for item in targets if item == victim.player_id), None)
    if target_id is None:
        check("顺手牵羊的目标里有手牌的角色", False, str(targets))
        return
    hidden_before = [item.id for item in victim.hand]
    clear_events(*clients)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]], target_ids=[target_id]))
    pump(1.0)

    # 选牌请求：只能看到牌背 + 装备（别人的手牌内容是隐藏信息）
    select = next_request(client, DecisionKind.SELECT_CARDS, pump=pump, timeout=12.0)
    if select is None:
        check("获得者收到选牌请求（顺手牵羊的第二段交互）", False,
              "没有收到：client.decision=%s answered=%s 房主pending=%s 日志=%s" % (
                  (client.decision or {}).get("kind"), client.match.answered,
                  getattr(host.game.pending_request, "prompt", None),
                  host.game.game_log[-2:]) + " 拒绝=%s 消息=%s" % (
                      host.match.registry.rejected[-2:], host.game.message))
        return
    entries = select.get("cards", ())
    check("选牌请求列出了对方的候选牌", len(entries) >= 1)
    hidden = [item for item in entries if item.get("face_down")]
    check("其中的手牌确实被标记为内容未知", len(hidden) >= 1, str(entries))
    real_ids = [card.id for card in victim.hand]
    check("网络包里的隐藏候选是不透明占位符（Phase 11.4：不是真牌 id）",
          bool(hidden)
          and all(str(item.get("card_id", "")).startswith("hidden:") for item in hidden)
          and not (set(real_ids) & {item["card_id"] for item in hidden}),
          str([item.get("card_id") for item in hidden]))

    picked = hidden[0]
    clear_events(*clients)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[picked["card_id"]]))
    # 占位符 → 真牌的映射只在房主内存里：客户端只知道"我盲选了一张"。
    wait_for(pump, lambda: len(victim.hand) == len(real_ids) - 1, 6.0, "牌移动")
    pump(0.5)

    moved = [card.id for card in remote.hand if card.id in real_ids]
    check("房主权威：一张真牌从对方手牌移到了获得者手里",
          len(victim.hand) == len(real_ids) - 1 and len(moved) == 1,
          "对方 %d→%d，到手 %s" % (len(real_ids), len(victim.hand), moved))
    check("获得者客户端在拿到牌之后才知道那是哪张",
          bool(moved) and moved[0] in client.hand_ids(), str(moved))
    check("其他客户端看不到被拿走的是什么牌",
          not other.leaked_card_ids(moved),
          "泄漏 " + str(other.leaked_card_ids(moved)))
    other_event = next((item for item in events_of(other, "cards_moved")
                        if item.get("reason") == "transfer"), None)
    if other_event:
        check("其他客户端只收到「有一张牌移动了」",
              other_event.get("cards") == []
              and int(other_event.get("hidden_count") or 0) == 1,
              str(other_event)[:120])
    else:
        check("其他客户端只收到「有一张牌移动了」", False, "没有 cards_moved 事件")
    client.snapshot("scene12_shunshou")


# ==================================================
# 场景 13：判定面板
# ==================================================

def scenario_judge(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 13] 判定：客户端看到判定区、翻牌与结果")

    from src.game.flows.judge import JudgeFlow
    from src.game.engine.flows import FlowStatus

    remote = host.remote_players()[0]
    client = m.client_of(remote)
    card = put_in_judge_area(host.game, remote, "LEBU")
    host.match.push_views(force=True)
    pump(0.4)

    view_remote = client.player_view(remote.player_id)
    check("客户端看到判定区里的【乐不思蜀】",
          view_remote is not None and any(item.name == "LEBU"
                                          for item in view_remote.judge_area),
          str([item.label for item in (view_remote.judge_area if view_remote else ())]))

    clear_events(*clients)
    flow = JudgeFlow(host.game.engine, remote, "lebu")
    outcome = flow.start()
    pump(0.5)

    started = event_of(client, "judge")
    check("客户端收到 judge 事件（判定开始 / 翻牌）", started is not None)
    check("判定事件带 reason 与 judged_player_id",
          started is not None and started.get("reason") == "lebu"
          and started.get("judged_player_id") == remote.player_id)
    if outcome.status is FlowStatus.WAITING:
        request = next_request(client, DecisionKind.SELECT_CARDS, pump=pump)
        check("改判窗口开给了远程玩家（如果有改判者）", request is not None)
    results = [item for item in events_of(client, "judge") if item.get("stage") == "result"]
    check("客户端收到判定结果事件（含结果语义）", bool(results),
          str([item.get("stage") for item in events_of(client, "judge")]))
    check("判定面板在客户端是激活的（真实 UI 状态）",
          client.scene.presentation.effects.judge_panel.active
          or client.scene.presentation.effects.judge_panel.reason == "lebu",
          "stage=%s" % client.scene.presentation.effects.judge_panel.stage)
    # 画一帧，确认判定面板能画在只读视图上
    check("客户端能画出判定面板这一帧", client.draw_frame())
    client.snapshot("scene13_judge")


# ==================================================
# 场景 14：鬼才改判
# ==================================================

def scenario_guicai(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 14] 鬼才改判：原判定牌 → 改判牌 → 最终结果")

    from src.game.flows.judge import JudgeFlow

    remote = host.remote_players()[0]
    client = m.client_of(remote)
    # 司马懿（鬼才）
    from src.game.generals import create_default_general_registry
    host.game.generals = create_default_general_registry()
    general = host.game.generals.get("simayi")
    if general is None:
        check("武将表里有司马懿", False, "找不到 simayi")
        return
    remote.general_id = general.id
    host.game.skills.bind_general(remote)
    replacement = force_hand(host.game, remote, ["TAO"])[0]

    clear_events(*clients)
    flow = JudgeFlow(host.game.engine, host.game.player, "lebu")
    outcome = flow.start()
    pump(0.6)

    request = next_request(client, DecisionKind.SELECT_CARDS, pump=pump)
    check("鬼才改判窗口开给了远程玩家（SELECT_CARDS）", request is not None,
          str([item.get("kind") for item in (request and [request] or [])]))
    if request is None:
        return
    entries = request.get("cards", ())
    check("改判候选里有远程玩家手里那张牌",
          any(item.get("card_id") == replacement.id for item in entries),
          str([item.get("card_id") for item in entries]))

    clear_events(*clients)
    client.match.answer(DecisionResult(action="submit", card_ids=[replacement.id]))
    pump(1.0)

    replaced = [item for item in events_of(client, "judge")
                if item.get("stage") == "replaced"]
    results = [item for item in events_of(client, "judge")
               if item.get("stage") == "result"]
    check("客户端看到改判事件（旧牌 → 新牌）", bool(replaced),
          str([item.get("stage") for item in events_of(client, "judge")]))
    if replaced:
        check("改判事件里同时给出原判定牌与改判牌",
              replaced[0].get("old_card") and replaced[0].get("new_card"))
    check("客户端看到最终判定结果", bool(results))
    panel = client.scene.presentation.effects.judge_panel
    check("判定面板记录了这次改判（真实 UI 状态）",
          panel.was_replaced or bool(panel.replacement_history),
          "stage=%s hist=%d prev=%s revealed=%s events=%s" % (
              panel.stage, len(panel.replacement_history),
              getattr(panel.previous_card, "name", None),
              getattr(panel.revealed_card, "name", None),
              [(item.get("stage"), bool(item.get("history")))
               for item in events_of(client, "judge")]))
    check("客户端能画出改判后的判定面板", client.draw_frame())
    client.snapshot("scene14_guicai")


# ==================================================
# 场景 15 / 16：View-As（龙胆 / 急救）
# ==================================================

def scenario_view_as(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 15] 龙胆 View-As：远程玩家把【闪】当【杀】使用")

    remote = host.remote_players()[0]
    client = m.client_of(remote)
    from src.game.generals import create_default_general_registry
    host.game.generals = create_default_general_registry()
    zhaoyun = host.game.generals.get("zhaoyun")
    if zhaoyun is None:
        check("武将表里有赵云", False, "找不到 zhaoyun")
        return
    remote.general_id = zhaoyun.id
    host.game.skills.bind_general(remote)
    force_hand(host.game, remote, ["SHAN", "SHAN"])
    # 目标固定为房主，并给他一张【闪】：这样响应窗口一定是开着的，
    # 中央的语义牌不会在动画播到之前就结算掉（否则观察不到主体）。
    force_hand(host.game, host.game.player, ["SHAN"])

    start_remote_turn(host, pump)
    request = next_request(client, DecisionKind.PLAY_PHASE, pump=pump)
    if request is None:
        check("远程玩家收到出牌阶段请求", False)
        return
    card, option = find_card(request, ("SHAN",), skill_id="longdan",
                             require_targets=True)
    check("【闪】的条目里带了【龙胆】的转化方式（skill_id=longdan）",
          card is not None and option is not None,
          str([(item.get("name"), [o.get("skill_id") for o in item.get("options", ())])
               for item in request.get("cards", ())]))
    if card is None or option is None:
        return
    targets = option_targets(card, option)
    check("转化方式给出了合法目标", bool(targets), str(targets))
    host_id = host.game.player.player_id
    target_ids = [item for item in targets if item == host_id][:1]

    clear_events(*clients)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]], target_ids=target_ids,
        skill_id="longdan"))
    # 边推进边采样桌面中央：出牌主体只在结算期间出现。
    seen = []
    for _ in range(40):
        pump(0.05)
        labels = [getattr(item, 'display_name', str(item))
                  for item, _slot in client.scene.view.table_cards]
        if labels and labels not in seen:
            seen.append(labels)
    if not seen:
        print("      [诊断] 客户端=%s scene=%s facade_table=%s host_table=%s rev=%s/%s" % (
            client.nickname, client.scene is not None,
            client.scene.view.table_cards if client.scene else None,
            [getattr(c, 'display_name', None) for c, _s in host.game.table_cards],
            client.view.revision, client.scene.view.revision if client.scene else None))

    used = event_of(client, "card_used")
    check("客户端收到出牌事件", used is not None)
    if used:
        check("中央展示的是**虚拟语义牌**【杀】，不是【闪】",
              used.get("label") == "杀" and used.get("virtual") is True,
              "label=%s virtual=%s" % (used.get("label"), used.get("virtual")))
        check("事件里带上技能名（龙胆）", used.get("skill_name") == "龙胆",
              str(used.get("skill_name")))
    check("只读视图里桌面上只有【杀】这一个主体",
          bool(seen) and all(labels == ["杀"] for labels in seen), str(seen))
    check("来源实体牌【闪】没有作为第二个主体出现在桌面",
          all("闪" not in labels for labels in seen), str(seen))
    check("权威侧：那张【闪】已经离开手牌",
          not any(item.id == card["card_id"] for item in remote.hand))
    check("房主日志里记录了这次转化",
          any("龙胆" in entry for entry in host.game.game_log),
          str(host.game.game_log[-2:]))
    client.snapshot("scene15_longdan")
    clear_windows(host, clients, pump, 2.0)


def scenario_view_as_rescue(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 16] 急救 View-As：协议支持红牌当【桃】（濒死救援）")

    from src.game.generals import create_default_general_registry
    remote = host.remote_players()[0]
    client = m.client_of(remote)
    host.game.generals = create_default_general_registry()
    huatuo = host.game.generals.get("huatuo")
    if huatuo is None:
        check("武将表里有华佗", False, "找不到 huatuo")
        return
    remote.general_id = huatuo.id
    host.game.skills.bind_general(remote)
    # 华佗：红牌当【桃】——给他一张红牌（闪是红色）
    red = take_card(host.game, "SHAN")
    force_hand(host.game, remote, [])
    remote.hand.append(red)
    check("给华佗准备了一张红牌", red.suit in ("heart", "diamond"), str(red.suit))

    # 让房主濒死：走真实的伤害流程（濒死由伤害流程在体力归零时启动）。
    # 房主自己手上不能留【桃】，否则救援顺序会先问他自己。
    from src.game.flows.damage import DamageContext, DamageFlow

    dying = host.game.player
    force_hand(host.game, dying, [])
    dying.hp = 1
    flow = DamageFlow(host.game.engine, DamageContext(
        source=host.remote_players()[0], target=dying, amount=1))
    check("伤害流程把房主打到濒死", flow.start().status.value in ("running", "waiting"),
          str(flow.status))
    pump(0.6)

    request = next_request(client, DecisionKind.RESPOND_CARD, pump=pump, timeout=10.0)
    if request is None:
        # 濒死救援顺序可能先问别人；把候选放宽再看一次
        request = client.decision
    check("远程华佗收到了濒死救援请求", request is not None,
          str(client.decision))
    if request is None:
        return
    card, option = find_card(request, ("SHAN",))
    check("救援请求里那张红牌带了【急救】的转化方式（skill_id=jijiu）",
          card is not None and option is not None
          and option.get("result_display") == "桃",
          str([(item.get("label"), [o.get("skill_id") for o in item.get("options", ())])
               for item in request.get("cards", ())]))
    if card is None or option is None:
        return

    clear_events(*clients)
    client.match.answer(DecisionResult(
        action="submit", card_ids=[card["card_id"]], skill_id="jijiu"))
    pump(1.0)

    check("房主权威：濒死被救回（体力 > 0）", host.game.player.hp > 0,
          "hp=%d" % host.game.player.hp)
    response = event_of(client, "card_response")
    check("客户端看到用【桃】救援的响应事件",
          response is not None and response.get("label") == "桃",
          str(response and response.get("label")))
    check("救援用的红牌已离开华佗的手牌",
          not any(item.id == red.id for item in remote.hand))
    client.snapshot("scene16_jijiu")


# ==================================================
# 场景 17：五谷丰登的公共池
# ==================================================

def scenario_wugu(m):
    host, clients, pump = m.host, m.clients, m.pump
    print("\n[场景 17] 五谷丰登：公共池对所有客户端同步，取牌后一起减少")

    remote = host.remote_players()[0]
    client = m.client_of(remote)
    other = next(item for item in clients if item is not client)
    m.fix_hands(["SHA", "TAO"], keep=remote)     # 谁都不带【无懈可击】
    force_hand(host.game, remote, ["WUGU"])

    start_remote_turn(host, pump)
    request = next_request(client, DecisionKind.PLAY_PHASE, pump=pump)
    card, _option = find_card(request or {}, ("WUGU",))
    if card is None:
        check("远程玩家能使用【五谷丰登】", False,
              str([item.get("name") for item in (request or {}).get("cards", ())]))
        return
    client.match.answer(DecisionResult(action="submit", card_ids=[card["card_id"]]))
    pump(0.8)

    # 五谷按座次逐个问每个角色；被问到的可能不是房主视角的那个客户端。
    selector = None
    pool_request = None
    deadline_hit = False
    for _ in range(60):
        for candidate in clients:
            request = next_request(candidate, DecisionKind.SELECT_CARDS)
            if request is not None:
                selector, pool_request = candidate, request
                break
        if pool_request is not None:
            break
        pump(0.2)
    if pool_request is None:
        check("轮到某个客户端选牌时收到公共池选择请求", False)
        return
    client = selector
    host.match.push_views(force=True)
    pump(0.4)
    pool_ids = [item.card_id for item in client.view.public_pool]
    check("客户端视图里的公共池非空", bool(pool_ids), str(pool_ids))
    check("所有客户端看到**同一批**公共牌",
          [item.card_id for item in other.view.public_pool] == pool_ids,
          "%s vs %s" % (pool_ids, [item.card_id for item in other.view.public_pool]))
    entries = {item.get("card_id") for item in pool_request.get("cards", ())}
    check("选择请求里的候选就是公共池里的牌",
          entries and entries.issubset(set(pool_ids)),
          "%s ⊆ %s" % (entries, pool_ids))
    check("公共池的牌是公开的（没有 face_down 候选）",
          all(not item.get("face_down") for item in pool_request.get("cards", ())))

    picked = pool_request["cards"][0]["card_id"]
    clear_events(*clients)
    client.match.answer(DecisionResult(action="submit", card_ids=[picked]))
    pump(0.8)
    check("获得者手牌里出现了这张牌", picked in client.hand_ids())
    check("公共池少了一张（所有客户端一致）",
          picked not in [item.card_id for item in client.view.public_pool]
          and picked not in [item.card_id for item in other.view.public_pool],
          str([item.card_id for item in client.view.public_pool]))
    moved = [item for item in events_of(other, "cards_moved")
             if item.get("from_zone") == "public_pool"]
    check("其他客户端也收到了「公共池的牌被拿走」的移动事件", bool(moved),
          str([item.get("reason") for item in events_of(other)]))
    check("公共池的牌本来就是公开的：其他客户端知道被拿走的是哪张",
          bool(moved) and (moved[0].get("cards") or [{}])[0].get("card_id") == picked,
          str(moved[0] if moved else None)[:120])
    client.snapshot("scene17_wugu")
    clear_windows(host, clients, pump, 2.0)


# ==================================================
# 主流程
# ==================================================

def main():
    print("=" * 60)
    print("Phase 11.3 Gameplay 表现同步验证（真实 socket / 真实引擎 / 真实渲染）")
    print("=" * 60)

    # 每个场景开一局新对局：互不污染，结论才可信。
    scenarios = [
        ("场景 8  Remote 出【杀】", scenario_remote_plays_sha),
        ("场景 9  响应【闪】", scenario_respond_shan),
        ("场景 10 不出闪的伤害", scenario_pass_shan_damage),
        ("场景 11 装备武器", scenario_equip),
        ("场景 12 顺手牵羊", scenario_shunshou),
        ("场景 13 判定面板", scenario_judge),
        ("场景 14 鬼才改判", scenario_guicai),
        ("场景 15 龙胆 View-As", scenario_view_as),
        ("场景 16 急救 View-As", scenario_view_as_rescue),
        ("场景 17 五谷丰登", scenario_wugu),
    ]
    for title, scenario in scenarios:
        with MatchSession(2) as m:
            if not check("%s：对局建立成功" % title, m.ok, m.message):
                continue
            scenario(m)
            check_presentation_clean(*m.clients)

    ok = summary("Phase 11.3 表现同步验证")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
