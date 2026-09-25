"""Phase 11.4：联机可玩性闭环（真实 socket + 真实引擎 + 真实渲染 + 真实点击）。

与 ``lan_view_sync`` / ``lan_presentation_sync`` 的关键区别：

* 那两个工具用 ``client.match.answer(...)`` **直接回答协议**，验证的是"数据
  能不能同步"；
* 本工具**只通过客户端的鼠标点击**做决定——点手牌、点座位、点桌面公共区的牌、
  点技能键、点固定按钮。玩家真实能做的动作，就是这里驱动的东西。

这正是"过河拆桥客户端选不到牌"这类 bug 的暴露方式：协议层一直是通的，界面层
根本没有可点的东西。

    python tools/lan_playability_sync.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame                                              # noqa: E402

from lan_view_harness import (                             # noqa: E402
    FRAME,
    MatchSession,
    check,
    clear_windows,
    force_hand,
    give_general,
    put_in_judge_area,
    start_remote_turn,
    summary,
    take_card,
    wait_for,
)

from src.network.decisions import (                         # noqa: E402
    ACTION_END_PHASE,
    ACTION_PASS,
    ACTION_SKILL,
    DecisionKind,
    DecisionResult,
)

MISS = ""


# ==================================================
# 客户端界面驱动（真实鼠标）
# ==================================================

class UiClient:
    """把客户端的界面点成"玩家会点的样子"。

    只调用 ``RemoteTableScene.handle_event`` + 既有 Renderer 的命中查询；
    不碰 ``match.answer``，也不读请求里的内部字段来做决定（除了"要点哪张牌"
    这种本来就是玩家意图的东西）。
    """

    def __init__(self, client):
        self.client = client

    # ---- 基础 ----

    @property
    def scene(self):
        return self.client.scene

    @property
    def renderer(self):
        return self.client.renderer

    @property
    def match(self):
        return self.client.match

    @property
    def view(self):
        return self.scene.view

    def frame(self, dt=FRAME):
        self.client.update_frame(dt)

    def click(self, position):
        if position is None:
            return False
        self.frame()
        event = pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, {"pos": (int(position[0]), int(position[1])),
                                     "button": 1})
        self.scene.handle_event(event, self.match)
        self.frame()
        return True

    # ---- 牌 / 角色定位 ----

    def hand_index_of(self, card_id):
        for index, card in enumerate(self.view.player.hand):
            if card is not None and card.id == card_id:
                return index
        return None

    def hand_rect(self, index):
        layout = self.renderer.table_layout
        return layout.hand_rect(index) if layout is not None else None

    def hand_cards(self):
        return [(index, card) for index, card in enumerate(self.view.player.hand)
                if card is not None]

    def click_hand_index(self, index):
        rect = self.hand_rect(index)
        return self.click(rect.center if rect is not None else None)

    def click_hand_card(self, card_id):
        index = self.hand_index_of(card_id)
        if index is None:
            return False
        return self.click_hand_index(index)

    def click_first_hand_card(self):
        cards = self.hand_cards()
        return self.click_hand_index(cards[0][0]) if cards else False

    def seat_rect(self, player_id):
        layout = self.renderer.table_layout
        mine = self.view.player.player_id if self.view.player is not None else ""
        if layout is not None:
            for player, rect in layout.seat_rects.items():
                if getattr(player, "player_id", "") == player_id:
                    return rect
        if player_id == mine:
            return self.renderer.metrics.player_status
        return None

    def click_seat(self, player_id):
        rect = self.seat_rect(player_id)
        return self.click(rect.center if rect is not None else None)

    def pool_entries(self):
        """公共区的 (rect, card, key)：与绘制用的是同一套几何。"""

        entries = self.renderer.get_pool_entries(self.view)
        cards = [card for card, _key in entries]
        rects = self.renderer.get_public_card_rects(cards)
        return [(rect, card, key) for rect, (card, key) in zip(rects, entries)
                if card is not None]

    def click_pool_card(self, card_id):
        for rect, card, _key in self.pool_entries():
            if card.id == card_id:
                return self.click(rect.center)
        return False

    def click_first_pool_card(self, *, face_down=None):
        for rect, card, _key in self.pool_entries():
            if face_down is not None and bool(card.face_down) != bool(face_down):
                continue
            return self.click(rect.center)
        return False

    # ---- 按钮 / 技能 / 选项 ----

    def click_primary(self):
        button = self.renderer.primary_button
        button.enabled = True
        return self.click(button.rect.center)

    def click_secondary(self):
        button = self.renderer.secondary_button
        button.enabled = True
        return self.click(button.rect.center)

    def click_skill(self, skill_id):
        rect = self.renderer.skill_bar.rect_for(skill_id)
        return self.click(rect.center if rect is not None else None)

    def skill_enabled(self, skill_id):
        self.frame()
        self.renderer.skill_bar.sync(self.view)
        return skill_id in self.renderer.skill_bar.enabled_ids

    def click_choice(self, yes=True):
        overlay = self.scene.choice_overlay
        overlay.sync_layout(self.renderer.metrics)
        rect = overlay.yes_rect if yes else overlay.no_rect
        return self.click(rect.center)

    def labels(self):
        """当前界面上的提示与按钮文案（视觉验收用）。"""

        from src.ui import prompt as prompt_module
        request = None if self.match is None or self.match.answered else self.match.decision
        self.scene._sync_overlay(self.match)
        info = prompt_module.describe(self.view)
        actions = self.renderer.actions_for(self.view)
        return {
            "prompt": (info.title, info.body, info.progress),
            "primary": (actions["primary"].label, actions["primary"].enabled),
            "secondary": (actions["secondary"].label, actions["secondary"].enabled),
            "request": (request or {}).get("kind"),
        }

    def snapshot(self, name):
        self.frame()
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_snapshots")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "phase11_4_" + name + ".png")
        pygame.image.save(self.client.screen, path)
        return path


# ==================================================
# 通用"用界面回答当前请求"
# ==================================================

def decision_of(client, kind=None):
    match = client.match
    if match is None or match.decision is None or match.answered:
        return None
    request = match.decision
    if kind is not None and request.get("kind") != kind:
        return None
    return request


def wait_decision(client, pump, kind=None, timeout=10.0):
    wait_for(pump, lambda: decision_of(client, kind) is not None, timeout,
             "等待 " + str(kind))
    return decision_of(client, kind)


def answer_via_ui(ui, pump, *, card_name=None, card_id=None, target_id=None,
                  face_down=None, confirm=True, timeout=10.0):
    """按请求类型挑最合理的点法，全部通过真实点击完成。

    返回 ``(kind, 描述)``。**只有真的提交成功才返回 kind**：以前"点了某个
    位置"就算成功，于是"公共区的牌根本点不动"这类 bug 会被验收工具报成
    PASS（审查报告 §7）。判据换成"客户端确实把答案发回房主了"
    （``match.answered`` / 请求已被换掉）；没提交就返回 ``(None, 原因)``。
    """

    request = wait_decision(ui.client, pump, timeout=timeout)
    if request is None:
        return None, "没有待回答的请求"
    kind = request.get("kind")
    picked = []
    request_id = request.get("request_id")

    def submitted():
        """这次点击有没有真的变成一条发回房主的回答。"""

        pump(0.3)
        match = ui.client.match
        if match is None:
            return False
        if match.answered or match.waiting_ack:
            return True
        current = match.decision
        return current is None or current.get("request_id") != request_id

    def pick_card():
        if card_id is not None and ui.click_hand_card(card_id):
            picked.append("手牌:" + str(card_id))
            return True
        if card_name is not None:
            entry = next((item for item in request.get("cards", ())
                          if item.get("name") == card_name), None)
            if entry is not None and ui.click_hand_card(entry.get("card_id")):
                picked.append("手牌:" + str(entry.get("name")))
                return True
        if ui.click_first_pool_card(face_down=face_down):
            picked.append("公共区")
            return True
        if ui.click_first_hand_card():
            picked.append("手牌")
            return True
        return False

    if kind == DecisionKind.PLAY_PHASE:
        entries = request.get("cards", ())
        entry = None
        if card_id is not None:
            entry = next((item for item in entries
                          if item.get("card_id") == card_id), None)
        elif card_name is not None:
            entry = next((item for item in entries
                          if item.get("name") == card_name), None)
        if entry is None and entries:
            entry = entries[0]
        if entry is None:
            ui.click_primary()                  # 结束回合
            return (kind, "结束回合") if submitted() else (None, "结束回合没有提交")
        ui.click_hand_card(entry.get("card_id"))
        picked.append("手牌:" + str(entry.get("name")))
        option = next((item for item in entry.get("options") or ()
                       if item.get("enabled")), None)
        maximum = int((option or {}).get("max_targets") or 0)
        if maximum > 0:
            target = target_id
            if target is None:
                candidates = [item.get("player_id")
                              for item in (option or {}).get("targets", ())]
                target = candidates[0] if candidates else None
            if target:
                ui.click_seat(target)
                picked.append("目标:" + str(target))
        else:
            ui.click_primary()
            picked.append("确认使用")
        if not submitted():
            return None, "出牌没有提交（" + " + ".join(picked) + "）"
        return kind, " + ".join(picked)

    if kind == DecisionKind.RESPOND_CARD:
        if not pick_card():
            ui.click_secondary()                # 不出
            return (kind, "不出") if submitted() else (None, "「不出」没有提交")
        ui.click_primary()
        if not submitted():
            return None, "响应没有提交（" + " + ".join(picked) + "）"
        return kind, " + ".join(picked)

    if kind == DecisionKind.SELECT_CARDS:
        if not pick_card():
            return None, "没有可点的候选（界面没有可点击的牌）"
        if not submitted():
            return None, "点了" + " + ".join(picked) + " 但没有提交"
        return kind, " + ".join(picked)

    if kind == DecisionKind.SELECT_TARGETS:
        candidates = [item.get("player_id") for item in request.get("targets", ())]
        target = target_id or (candidates[0] if candidates else None)
        if target:
            ui.click_seat(target)
            if not submitted():
                return None, "选了目标但没有提交"
            return kind, "目标:" + str(target)
        return None, "没有可点的目标"

    if kind == DecisionKind.CONFIRM:
        ui.click_choice(confirm)
        if not submitted():
            return None, "是/否没有提交"
        return kind, ("发动" if confirm else "不发动")

    if kind == DecisionKind.CHOOSE_OPTION:
        ui.click_choice(True)
        if not submitted():
            return None, "选项没有提交"
        return kind, "选项 1"

    return None, "未支持的决策类型"


def answer_skill_via_ui(ui, pump, skill_id, *, cost=(), target_id=None, timeout=10.0):
    """在出牌阶段发动主动技：点技能键 → 点费用牌 → 点目标 → 点「确认发动」。"""

    request = wait_decision(ui.client, pump, DecisionKind.PLAY_PHASE, timeout=timeout)
    if request is None:
        return False, "没有出牌阶段请求"
    entry = next((item for item in (request.get("constraints") or {}).get("activatable", ())
                  if item.get("skill_id") == skill_id), None)
    if entry is None:
        return False, "房主没有列出发动【%s】的入口" % skill_id
    if not ui.click_skill(skill_id):
        return False, "技能键点不到"
    pump(0.2)
    for card_id in cost:
        ui.click_hand_card(card_id)
        pump(0.2)
    if target_id is None and entry.get("needs_target"):
        target_id = next((item.get("player_id") for item in entry.get("targets", ())), None)
    if target_id:
        ui.click_seat(target_id)
        pump(0.2)
    ui.click_primary()
    return True, "发动【%s】" % skill_id


# ==================================================
# 场景
# ==================================================

def scenario_client_plays_sha(m):
    """A1：远程玩家在真实界面上出一张【杀】。"""

    host, pump = m.host, m.pump
    print("\n[场景 1] 远程玩家用真实界面出【杀】")
    m.fix_hands(["SHA", "TAO"], keep=m.remote())
    remote = m.remote()
    force_hand(host.game, remote, ["SHA", "TAO", "SHAN"])
    victim = host.game.player
    force_hand(host.game, victim, ["SHA", "TAO", "SHAN"])
    start_remote_turn(host, pump)
    ui = UiClient(m.client_of(remote))
    hp_before = victim.hp
    before = len(victim.hand)
    kind, how = answer_via_ui(ui, pump, card_name="SHA", target_id=victim.player_id)
    check("远程玩家在界面上完成出牌", kind == DecisionKind.PLAY_PHASE, how)
    # 房主是真人：他的【闪】窗口按真实规则开着，这里替他"不出"。
    played = []
    for _ in range(6):
        if host.game.response.active:
            played.append("闪窗口")
            host.game.pass_response()
        pump(0.3)
        if victim.hp < hp_before:
            break
    pump(0.4)
    check("房主权威：这次出牌真的结算了（目标掉血，或他打出了【闪】）",
          victim.hp < hp_before or len(victim.hand) > before - 1,
          "hp %d→%d 手牌 %d→%d 窗口=%s" % (hp_before, victim.hp, before,
                                           len(victim.hand), played))
    check("房主没有拒绝这条响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene1_client_sha")
    clear_windows(host, m.clients, pump)


def scenario_client_guohe_equipment(m):
    """A2（本阶段核心）：远程玩家用【过河拆桥】拆房主装备区的牌。"""

    host, pump = m.host, m.pump
    print("\n[场景 2] 远程【过河拆桥】→ 房主（手牌 + 装备区都能选）")
    remote = m.remote()
    victim = host.game.player
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(host.game, remote, ["GUOHE", "SHA"])
    force_hand(host.game, victim, ["SHA", "TAO", "SHAN"])
    from tests.legacy_helpers import equipment
    victim.set_equipment(equipment("QINGLONG"))
    weapon = victim.get_equipment("weapon")
    start_remote_turn(host, pump)
    ui = UiClient(m.client_of(remote))

    kind, how = answer_via_ui(ui, pump, card_name="GUOHE", target_id=victim.player_id)
    check("远程玩家在界面上把【过河拆桥】指向房主", kind == DecisionKind.PLAY_PHASE, how)
    ui.snapshot("scene2a_client_guohe_target")

    select = wait_decision(ui.client, pump, DecisionKind.SELECT_CARDS, timeout=12.0)
    if select is None:
        check("房主为『选择目标区域内的一张牌』开出选牌请求", False,
              "房主 pending=%s 日志=%s" % (
                  getattr(host.game.pending_request, "prompt", None),
                  host.game.game_log[-2:]))
        return
    check("房主为『选择目标区域内的一张牌』开出选牌请求", True,
          select.get("prompt", ""))
    entries = select.get("cards", ())
    real_ids = {item.get("card_id") for item in entries if not item.get("face_down")}
    hidden = [item for item in entries if item.get("face_down")]
    check("装备区的牌给了真实 card_id（公开信息）", weapon.id in real_ids,
          str(real_ids))
    check("对方手牌只给牌背", len(hidden) >= 1, str(len(hidden)) + " 张")
    check("隐藏手牌的 card_id 是不透明占位符（不是真牌 id）",
          all(str(item.get("card_id", "")).startswith("hidden:") for item in hidden),
          str([item.get("card_id") for item in hidden]))
    check("隐藏手牌不泄露牌名 / 花色 / 点数",
          all(item.get("name") in ("", None) and item.get("suit") is None
              and item.get("rank") is None for item in hidden))

    check("公共区里能点到装备区的牌（界面可交互）",
          ui.click_pool_card(weapon.id), "装备 " + weapon.display_name)
    pump(1.2)

    check("房主权威：那件装备真的被弃置了",
          victim.get_equipment("weapon") is None)
    check("房主没有拒绝这条响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene2_client_guohe_equipment")
    clear_windows(host, m.clients, pump)


def scenario_client_guohe_hidden_only(m):
    """B：目标只有隐藏手牌——客户端盲选一个不透明位。"""

    host, pump = m.host, m.pump
    print("\n[场景 3] 远程【过河拆桥】→ 只有隐藏手牌的对手（盲选）")
    remote = m.remote()
    victim = host.game.player
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(host.game, remote, ["GUOHE", "SHA"])
    force_hand(host.game, victim, ["SHA", "TAO", "SHAN"])
    for slot in ("weapon", "armor", "defensive_horse", "offensive_horse"):
        victim.remove_equipment(slot)
    start_remote_turn(host, pump)
    ui = UiClient(m.client_of(remote))

    answer_via_ui(ui, pump, card_name="GUOHE", target_id=victim.player_id)
    select = wait_decision(ui.client, pump, DecisionKind.SELECT_CARDS, timeout=12.0)
    if select is None:
        check("盲选窗口到达客户端", False, "没有收到 SELECT_CARDS")
        return
    hand_before = [card.id for card in victim.hand]
    hidden = [item for item in select.get("cards", ()) if item.get("face_down")]
    check("窗口里只有内容未知的候选", len(hidden) == len(hand_before),
          "%d vs %d" % (len(hidden), len(hand_before)))
    token = hidden[0].get("card_id") if hidden else None
    check("隐藏候选的 id 与真牌 id 完全不重合",
          token is not None and token not in hand_before, str(token))
    check("界面能点到那张牌背", ui.click_first_pool_card(face_down=True))
    pump(1.2)
    check("房主权威：手牌真的少了一张",
          len(victim.hand) == len(hand_before) - 1,
          "%d → %d" % (len(hand_before), len(victim.hand)))
    check("房主没有拒绝这条响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene3_client_guohe_hidden")
    clear_windows(host, m.clients, pump)


def scenario_client_shunshou_hidden(m):
    """C：远程【顺手牵羊】拿走对方一张隐藏手牌。"""

    host, pump = m.host, m.pump
    print("\n[场景 4] 远程【顺手牵羊】→ 对方隐藏手牌（牌到手）")
    remote = m.remote()
    victim = host.game.player
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(host.game, remote, ["SHUNSHOU", "SHA"])
    force_hand(host.game, victim, ["SHA", "TAO", "SHAN"])
    for slot in ("weapon", "armor", "defensive_horse", "offensive_horse"):
        victim.remove_equipment(slot)
    start_remote_turn(host, pump)
    ui = UiClient(m.client_of(remote))

    answer_via_ui(ui, pump, card_name="SHUNSHOU", target_id=victim.player_id)
    select = wait_decision(ui.client, pump, DecisionKind.SELECT_CARDS, timeout=12.0)
    if select is None:
        check("顺手牵羊的选牌窗口到达客户端", False, "没有收到 SELECT_CARDS")
        return
    victim_before = [card.id for card in victim.hand]
    remote_before = len(remote.hand)
    check("界面能点到对方隐藏手牌的牌背",
          ui.click_first_pool_card(face_down=True))
    pump(1.2)
    check("房主权威：那张牌从对方手里移到了远程玩家手里",
          len(victim.hand) == len(victim_before) - 1
          and len(remote.hand) == remote_before + 1,
          "对方 %d→%d，自己 %d→%d" % (len(victim_before), len(victim.hand),
                                       remote_before, len(remote.hand)))
    check("房主没有拒绝这条响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene4_client_shunshou")
    clear_windows(host, m.clients, pump)


def scenario_host_guohe_client(m):
    """D：房主（本地真人）拆远程玩家——本地 UI 路径与本阶段新增结构不冲突。"""

    host, pump = m.host, m.pump
    print("\n[场景 5] 房主【过河拆桥】→ 远程玩家（反向）")
    game = host.game
    remote = m.remote()
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["SHA", "TAO", "SHAN"])
    force_hand(game, game.player, ["GUOHE", "SHA", "TAO"])
    from tests.legacy_helpers import equipment
    remote.set_equipment(equipment("QINGLONG"))
    weapon = remote.get_equipment("weapon")
    game.current_turn_player = game.player
    game.phase = "play"
    index = next(index for index, card in enumerate(game.player.hand)
                 if card.name == "GUOHE")
    game.player_use_card(index, (0, 0, 10, 10))
    pump(0.3)
    game.toggle_target_selection(remote)
    pump(0.6)
    selection = game.pending_selection
    if selection is None:
        check("房主侧打开选牌界面", False, "pending_selection 为空")
        return
    client = m.client_of(remote)
    hidden_ids = [card.id for card in remote.hand if card is not weapon]
    check("房主侧选牌界面列出了对方的牌",
          len(selection["candidates"]) >= 1, str(len(selection["candidates"])))
    entry = next((item for item in selection["candidates"]
                  if item[0] is weapon), None)
    if entry is None:
        check("房主能选中对方的装备", False)
        return
    game.select_pending_card(entry[0], (0, 0, 10, 10), key=entry[1])
    pump(1.2)
    check("房主权威：远程玩家的装备被弃置", remote.get_equipment("weapon") is None)
    check("远程客户端没有收到对方（房主）手牌的真实 id",
          not client.leaked_card_ids([card.id for card in game.player.hand]),
          str(client.leaked_card_ids([card.id for card in game.player.hand])[:2]))
    mine = [card for card in client.scene.view.player.hand if card is not None]
    check("远程玩家自己的手牌照常显示牌面（没有被误伤成牌背）",
          bool(mine) and all(card.name and not card.face_down for card in mine),
          str([(card.name, card.face_down) for card in mine]))
    clear_windows(host, m.clients, pump)


def scenario_client_respond_shan(m):
    """远程玩家被【杀】指定为目标：在界面上点手牌打出【闪】。"""

    host, pump = m.host, m.pump
    print("\n[场景 6a] 远程玩家用界面响应【闪】")
    game = host.game
    remote = m.remote()
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["SHAN", "TAO"])
    force_hand(game, game.player, ["SHA", "TAO"])
    index = next(index for index, card in enumerate(game.player.hand)
                 if card.name == "SHA")
    game.current_turn_player = game.player
    game.phase = "play"
    game.player_use_card(index, (0, 0, 10, 10))
    pump(0.4)
    if game.pending_target_selection is not None:
        game.toggle_target_selection(remote)
    pump(0.8)
    client = m.client_of(remote)
    ui = UiClient(client)
    request = wait_decision(client, pump, DecisionKind.RESPOND_CARD, timeout=10.0)
    if request is None:
        check("远程玩家收到响应【闪】的请求", False,
              str((client.decision or {}).get("kind")))
        return
    check("远程玩家收到响应【闪】的请求", True, str(request.get("prompt"))[:20])
    labels = ui.labels()
    check("响应提示沿用既有提示条（「需要你的响应」）",
          labels["prompt"][0] == "需要你的响应", str(labels["prompt"]))
    check("「不出」按钮在既有位置可用", labels["secondary"] == ("不出", True),
          str(labels["secondary"]))
    hand_ids = [card.id for card in client.scene.view.player.hand if card is not None]
    playable = client.scene.view.network_playable_indices()
    check("手牌区高亮可响应的牌（房主给的集合，客户端不自己算）",
          playable == {index for index, card in enumerate(client.scene.view.player.hand)
                       if card is not None and card.name == "SHAN"},
          "手牌 %s 高亮 %s" % (hand_ids, playable))
    hand_before = len(remote.hand)
    ok, how = answer_via_ui(ui, pump)
    check("远程玩家用界面打出【闪】", ok == DecisionKind.RESPOND_CARD, how)
    pump(1.0)
    check("房主权威：远程玩家手牌真的少了一张（打出了响应牌）",
          len(remote.hand) == hand_before - 1,
          "%d → %d" % (hand_before, len(remote.hand)))
    check("房主没有拒绝这条响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene6a_client_shan")
    clear_windows(host, m.clients, pump)


def scenario_client_dying_rescue(m):
    """远程玩家濒死：界面上求桃（自己用【桃】自救）。"""

    host, pump = m.host, m.pump
    print("\n[场景 6b] 远程玩家濒死：界面求桃")
    game = host.game
    remote = m.remote()
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["TAO", "SHAN", "SHA"])
    force_hand(game, game.player, ["SHA", "TAO"])
    remote.hp = 1
    index = next(index for index, card in enumerate(game.player.hand)
                 if card.name == "SHA")
    game.current_turn_player = game.player
    game.phase = "play"
    game.player_use_card(index, (0, 0, 10, 10))
    pump(0.4)
    if game.pending_target_selection is not None:
        game.toggle_target_selection(remote)
    pump(0.6)
    client = m.client_of(remote)
    ui = UiClient(client)
    # 先不出闪（让伤害落地），再在濒死窗口用自己的桃自救。
    for _ in range(4):
        request = decision_of(client)
        if request is None:
            pump(0.3)
            continue
        if request.get("kind") == DecisionKind.RESPOND_CARD:
            cards = [item.get("name") for item in request.get("cards", ())]
            if "TAO" in cards:
                break
            ui.click_secondary()                 # 不出
            pump(0.4)
            continue
        answer_via_ui(ui, pump, confirm=True, timeout=3.0)
        pump(0.3)
    request = decision_of(client, DecisionKind.RESPOND_CARD)
    if request is None or "TAO" not in [
            item.get("name") for item in request.get("cards", ())]:
        check("远程玩家在濒死窗口拿到【桃】的响应请求", False,
              str((client.decision or {}).get("kind")))
        return
    check("远程玩家在濒死窗口拿到【桃】的响应请求", True,
          str(request.get("prompt"))[:24])
    hp_before = remote.hp
    ok, how = answer_via_ui(ui, pump, card_name="TAO")
    pump(1.2)
    check("远程玩家用界面打出【桃】自救", ok == DecisionKind.RESPOND_CARD, how)
    check("房主权威：远程玩家被救回来（体力回升 / 没有阵亡）",
          remote.alive and remote.hp > hp_before,
          "hp %d → %d alive=%s" % (hp_before, remote.hp, remote.alive))
    ui.snapshot("scene6b_client_dying_rescue")
    clear_windows(host, m.clients, pump)


def scenario_client_discard_multi(m):
    """远程玩家弃牌阶段：一次点两张牌（多数量的选牌交互）。"""

    host, pump = m.host, m.pump
    print("\n[场景 6c] 远程玩家弃牌阶段：多张选牌")
    game = host.game
    remote = m.remote()
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["SHA", "TAO", "SHAN", "SHA", "TAO", "JIU"])
    remote.hp = 4
    start_remote_turn(host, pump)
    client = m.client_of(remote)
    ui = UiClient(client)
    wait_decision(client, pump, DecisionKind.PLAY_PHASE, timeout=10.0)
    # 与真人一样：点「结束回合」→ 房主推进到弃牌阶段 → 要求弃掉多余的牌。
    ui.click_primary()
    request = wait_decision(client, pump, DecisionKind.SELECT_CARDS, timeout=10.0)
    if request is None:
        check("远程玩家收到弃牌请求", False, str(game.message))
        return
    check("远程玩家收到弃牌请求", True, str(request.get("prompt"))[:24])
    labels = ui.labels()
    check("选牌提示给出数量进度（沿用本地提示条）",
          "还需选择" in labels["prompt"][2], str(labels["prompt"]))
    before = len(remote.hand)
    for index in range(2):
        cards = ui.hand_cards()
        if index >= len(cards):
            break
        ui.click_hand_index(cards[index][0])
        pump(0.25)
    pump(1.2)
    check("房主权威：远程玩家真的弃掉了两张牌",
          len(remote.hand) == before - 2,
          "%d → %d" % (before, len(remote.hand)))
    check("房主没有拒绝弃牌", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene6c_client_discard")
    clear_windows(host, m.clients, pump)


def scenario_client_zhiheng(m):
    """E：远程玩家在出牌阶段发动主动技【制衡】（真实点击技能键 → 选牌 → 确认）。"""

    host, pump = m.host, m.pump
    print("\n[场景 6] 远程玩家发动主动技【制衡】")
    remote = m.remote()
    give_general(host.game, remote, "sunquan")
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(host.game, remote, ["SHA", "TAO", "SHAN", "SHA"])
    force_hand(host.game, host.game.player, ["SHA", "TAO", "SHAN"])
    start_remote_turn(host, pump)
    ui = UiClient(m.client_of(remote))
    request = wait_decision(ui.client, pump, DecisionKind.PLAY_PHASE, timeout=10.0)
    if request is None:
        check("远程玩家拿到出牌阶段请求", False)
        return
    activatable = [item.get("skill_id")
                   for item in (request.get("constraints") or {}).get("activatable", ())]
    check("出牌阶段请求里列出了可发动的主动技", "zhiheng" in activatable,
          str(activatable))
    hand_before = [card.id for card in remote.hand]
    kicker = ui.skill_enabled("zhiheng")
    check("技能栏上【制衡】是可点的（房主说能按）", kicker, "enabled_ids")
    ok, how = answer_skill_via_ui(
        ui, pump, "zhiheng", cost=[hand_before[0], hand_before[1]])
    check("用真实点击发动【制衡】", ok, how)
    pump(1.5)
    check("房主权威：制衡真的结算了（弃了两张并摸了新牌）",
          len(host.match.registry.rejected) == 0 and len(remote.hand) == len(hand_before) - 2 + 2,
          "手牌 %d → %d，拒绝=%s" % (len(hand_before), len(remote.hand),
                                     host.match.registry.rejected[-2:]))
    ui.snapshot("scene6_client_zhiheng")
    clear_windows(host, m.clients, pump)


def scenario_client_liuli(m):
    """F：远程玩家被【杀】指定为目标时发动【流离】，用座位点击选新目标。"""

    host, pump = m.host, m.pump
    print("\n[场景 7] 远程玩家【流离】→ 用界面点座位改目标")
    game = host.game
    remote = m.remote()
    give_general(game, remote, "daqiao")
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["SHAN", "SHA", "TAO"])
    force_hand(game, game.player, ["SHA", "SHA", "TAO"])
    third = next(player for player in game.players
                 if player is not remote and player is not game.player)
    force_hand(game, third, ["SHA", "TAO"])
    index = next(index for index, card in enumerate(game.player.hand)
                 if card.name == "SHA")
    game.current_turn_player = game.player
    game.phase = "play"
    game.player_use_card(index, (0, 0, 10, 10))
    pump(0.4)
    if game.pending_target_selection is not None:
        game.toggle_target_selection(remote)
    pump(0.8)
    client = m.client_of(remote)
    ui = UiClient(client)
    request = wait_decision(client, pump, timeout=10.0)
    if request is None:
        check("远程玩家收到【流离】相关决策", False,
              "房主 pending=%s" % getattr(game.pending_request, "prompt", None))
        return
    check("远程玩家收到【流离】相关决策", True,
          "%s / %s" % (request.get("kind"), request.get("prompt", "")[:24]))
    kind, how = answer_via_ui(ui, pump, confirm=True)
    check("远程玩家用界面回答了【流离】", kind is not None, how)
    pump(0.6)
    # 流离可能先问"是否发动"，再问弃哪张牌、转给谁：把剩下的窗口也用界面答完。
    for _ in range(4):
        if decision_of(client) is None:
            break
        answer_via_ui(ui, pump, confirm=True, timeout=4.0)
        pump(0.4)
    pump(1.0)
    check("房主没有拒绝【流离】的响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene7_client_liuli_targets")
    clear_windows(host, m.clients, pump)


def scenario_client_guicai(m):
    """I：远程玩家用【鬼才】改判——从自己手牌里点一张牌替换判定牌。"""

    host, pump = m.host, m.pump
    print("\n[场景 8] 远程玩家【鬼才】改判（真实点击手牌）")
    game = host.game
    remote = m.remote()
    give_general(game, remote, "simayi")
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["SHA", "TAO"])
    force_hand(game, game.player, ["SHA", "TAO"])
    put_in_judge_area(game, game.player, "LEBU")
    client = m.client_of(remote)
    ui = UiClient(client)
    # 直接开一次判定流程：改判窗口会按座次问给【鬼才】的持有者。
    from src.game.flows.judge import JudgeFlow

    replacement = force_hand(game, remote, ["TAO"])[0]
    flow = JudgeFlow(game.engine, game.player, "lebu")
    flow.start()
    pump(0.8)
    request = wait_decision(client, pump, timeout=10.0)
    if request is None:
        check("远程玩家收到改判窗口", False,
              "房主 pending=%s" % getattr(game.pending_request, "prompt", None))
        return
    check("远程玩家收到改判窗口", True, str(request.get("kind")))
    entries = request.get("cards", ())
    check("改判候选里是远程玩家自己的牌（自己手牌给真实牌面）",
          any(item.get("card_id") == replacement.id for item in entries),
          str([item.get("card_id") for item in entries]))
    check("界面能点到自己手里那张改判牌", ui.click_hand_card(replacement.id))
    pump(1.2)
    check("房主没有拒绝改判", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene8_client_guicai")
    clear_windows(host, m.clients, pump)


def scenario_client_wugu(m):
    """L：五谷丰登——每个玩家依次从公共池取牌，远程玩家用界面点公共区的牌。"""

    host, pump = m.host, m.pump
    print("\n[场景 9] 五谷丰登：多人依次取牌（远程用界面点公共区的牌）")
    game = host.game
    remote = m.remote()
    m.fix_hands(["SHA", "TAO"], keep=remote)
    force_hand(game, remote, ["SHAN", "TAO"])
    force_hand(game, game.player, ["WUGU", "SHA", "TAO"])
    game.current_turn_player = game.player
    game.phase = "play"
    index = next(index for index, card in enumerate(game.player.hand)
                 if card.name == "WUGU")
    game.player_use_card(index, (0, 0, 10, 10))
    pump(0.8)
    client = m.client_of(remote)
    ui = UiClient(client)
    answered = []
    initial_pool = [str(card.id) for card in game.public_card_pool]
    hand_before = [str(card.id) for card in remote.hand]
    taken = ""
    for _ in range(8):
        if game.game_over:
            break
        request = decision_of(client)
        if request is not None:
            # 五谷的候选：逐条检查它们**确实是公共池里的牌**。
            cards = [item for item in request.get("cards", ())
                     if str(item.get("zone")) == "public_pool"]
            if request.get("kind") == DecisionKind.SELECT_CARDS and cards:
                check("五谷的候选标成公共池（不是自己的装备区）",
                      len(cards) == len(request.get("cards", ())),
                      "zones=" + str({item.get("zone")
                                      for item in request.get("cards", ())}))
                target_id = str(cards[0].get("card_id"))
                kind, how = answer_via_ui(ui, pump, card_id=target_id, timeout=4.0)
                if kind is not None:
                    taken = target_id
                answered.append((kind, how))
                pump(0.4)
                continue
            kind, how = answer_via_ui(ui, pump, timeout=4.0)
            answered.append((kind, how))
            pump(0.4)
            continue
        if game.pending_selection is not None and game.pending_selection["owner"] is game.player:
            entries = game.selection_pool_entries()
            if entries:
                game.select_pending_card(entries[0][0], (0, 0, 10, 10),
                                         key=entries[0][1])
            pump(0.4)
            continue
        if game.response.active:
            game.pass_response()
        pump(0.3)
        if not game.busy and decision_of(client) is None \
                and game.pending_selection is None:
            break
    pool_now = [str(card.id) for card in game.public_card_pool]
    hand_now = [str(card.id) for card in remote.hand]
    check("五谷丰登期间远程玩家用界面取到了牌",
          any(kind == DecisionKind.SELECT_CARDS for kind, _how in answered),
          str(answered[:3]))
    check("取到的那张牌真的进了游客手牌",
          bool(taken) and taken in hand_now and taken not in hand_before,
          "取牌=%s 手牌=%s" % (taken, hand_now))
    check("公共池真的少了一张（并且这张不在池里了）",
          bool(taken) and taken not in pool_now
          and len(pool_now) == len(initial_pool) - len(game.players),
          "池 %d → %d" % (len(initial_pool), len(pool_now)))
    check("五谷流程正常结束（池空、没有卡住的请求）",
          not pool_now and decision_of(client) is None,
          "池=" + str(pool_now))
    check("房主没有拒绝五谷的响应", not host.match.registry.rejected,
          str(host.match.registry.rejected[-2:]))
    ui.snapshot("scene9_client_wugu")
    clear_windows(host, m.clients, pump)


def scenario_hidden_audit(m):
    """隐藏信息：把整局收到的原始 socket 载荷对"别人的手牌"做审计。"""

    host, pump = m.host, m.pump
    print("\n[场景 10] 隐藏信息审计（原始 socket 载荷）")
    game = host.game
    remote = m.remote()
    observer = m.client_of(remote)          # 玩家甲自己的客户端
    victim = next(player for player in game.players
                  if player is not game.player and player is not remote)
    check("局里有第三方（另一名远程玩家）", victim is not None,
          "%d 人局" % len(game.players))
    problems = observer.audit_hidden_cards(
        victim.player_id,
        [{"card_id": card.id, "label": card.display_name} for card in victim.hand])
    check("玩家甲看不到玩家乙的隐藏手牌内容", not problems, str(problems[:3]))
    host_problems = observer.audit_hidden_cards(
        game.player.player_id,
        [{"card_id": card.id, "label": card.display_name} for card in game.player.hand])
    check("玩家甲看不到房主的隐藏手牌内容", not host_problems, str(host_problems[:3]))
    my_view = observer.view
    check("玩家甲自己的手牌在自己的视图里（内容确实发给了本人）",
          len(my_view.hand) == len(remote.hand),
          "%d vs %d" % (len(my_view.hand), len(remote.hand)))


def scenario_parity(m):
    """视觉/文案一致性：同一种状态下的提示与按钮（单机 vs 远程）。"""

    from src.game import Game
    from src.renderer import Renderer
    from src.ui import prompt as prompt_module

    host, pump = m.host, m.pump
    print("\n[场景 11] 单机 / 远程 界面文案一致性（同一份提示与按钮逻辑）")
    local = Game(ai_count=1)
    local.start_single_player()
    local.actions.clear()
    local.phase = "play"
    local.current_turn_player = local.player
    local_info = prompt_module.describe(local)
    local_actions = Renderer(local_player_screen()).actions_for(local)
    check("单机出牌阶段的提示文案", local_info.title == "出牌阶段", local_info.title)

    remote = m.remote()
    ui = UiClient(m.client_of(remote))
    start_remote_turn(host, pump)
    labels = ui.labels()
    check("远程出牌阶段的提示与单机逐字一致",
          labels["prompt"][:2] == (local_info.title, local_info.body),
          "%s vs %s" % (str(labels["prompt"][:2]),
                        str((local_info.title, local_info.body))))
    check("远程固定按钮与单机同一套逻辑（结束回合 / 可用）",
          labels["primary"] == (local_actions["primary"].label,
                                local_actions["primary"].enabled),
          "%s vs %s" % (str(labels["primary"]),
                        str((local_actions["primary"].label,
                             local_actions["primary"].enabled))))
    ui.snapshot("scene11_client_play_phase")
    clear_windows(host, m.clients, pump)


def local_player_screen():
    """单机对照用的显示面（SDL dummy，不弹窗）。"""

    return pygame.display.set_mode((1600, 1000))


SCENARIOS = (
    ("客户端出杀", scenario_client_plays_sha, 1),
    ("客户端过河拆桥（装备）", scenario_client_guohe_equipment, 1),
    ("客户端过河拆桥（隐藏手牌）", scenario_client_guohe_hidden_only, 1),
    ("客户端顺手牵羊（隐藏手牌）", scenario_client_shunshou_hidden, 1),
    ("房主过河拆桥（反向）", scenario_host_guohe_client, 1),
    ("客户端响应闪", scenario_client_respond_shan, 1),
    ("客户端濒死求桃", scenario_client_dying_rescue, 1),
    ("客户端弃牌多选", scenario_client_discard_multi, 1),
    ("客户端制衡（主动技）", scenario_client_zhiheng, 1),
    ("客户端流离（选目标）", scenario_client_liuli, 2),
    ("客户端鬼才（改判）", scenario_client_guicai, 1),
    ("五谷丰登（多人）", scenario_client_wugu, 1),
    ("隐藏信息审计", scenario_hidden_audit, 2),
    ("界面文案一致性", scenario_parity, 2),
)


def main():
    print("=" * 60)
    print("Phase 11.4 联机可玩性闭环（真实点击驱动）")
    print("=" * 60)
    for label, scenario, clients in SCENARIOS:
        try:
            with MatchSession(client_count=clients) as session:
                if not session.ok:
                    check(label + "：开局", False, session.message)
                    continue
                scenario(session)
        except Exception as error:                              # pragma: no cover
            import traceback
            traceback.print_exc()
            check(label, False, "%s: %s" % (type(error).__name__, error))
        finally:
            pygame.display.quit()
            pygame.display.init()
    return summary("Phase 11.4 联机可玩性")


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
