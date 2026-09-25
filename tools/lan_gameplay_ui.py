"""Phase 11.2 的真实界面验证：两个 Pygame 实例跑一场联机对局。

与 ``tools/lan_gameplay_bridge.py`` 互补：那个工具验证"桥"的规则正确性，
这个工具验证**真实界面**——房主进的是普通牌桌，客户端进的是远程决策面板，
两边都用真实的鼠标点击事件驱动（SDL dummy 驱动，无窗口）。

    python tools/lan_gameplay_ui.py
"""

import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pygame

from src.game import Game
from src.network.decisions import DecisionKind
from src.renderer import Renderer
from src.ui.lan_scene import LAN_SCENES, LanScene

FRAME = 1.0 / 60.0
RECT = (0, 0, 10, 10)
RESOLUTIONS = ((1280, 720), (1366, 768), (1920, 1080), (2560, 1440))

_results = []


def check(name, condition, detail=""):
    _results.append((name, bool(condition), detail))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                           ("  — " + detail) if detail else ""))
    return bool(condition)


class UiInstance:
    """一台跑着真实 Pygame 界面的电脑。"""

    def __init__(self, label):
        self.label = label
        self.screen = pygame.display.set_mode(RESOLUTIONS[0])
        self.game = Game(ai_count=1)
        self.game.ai_pacing = True
        self.renderer = Renderer(self.screen)
        # 客户端牌桌复用同一个 Renderer（Phase 11.3：客户端用既有渲染层画只读视图）。
        self.lan = LanScene(self.screen, self.game, self.renderer)
        self.lan.set_screen(self.screen)
        self.lan.sync_layout(self.renderer.metrics)
        self.frames = 0

    # ---- 与 main.py 一致的每帧推进 ----

    def tick(self):
        self.lan.update(FRAME, self.game)
        self.game.update(FRAME)
        self.renderer.update(FRAME)
        if self.game.scene in LAN_SCENES:
            self.lan.draw(self.game, self.renderer.metrics)
        else:
            self.renderer.draw(self.game)
        pygame.display.flip()
        self.frames += 1

    def resize(self, size):
        self.screen = pygame.display.set_mode(size)
        self.renderer.set_screen(self.screen)
        self.lan.set_screen(self.screen)
        self.lan.sync_layout(self.renderer.metrics)
        self.tick()

    def click(self, position):
        self.lan.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=tuple(position)),
            self.game)
        self.tick()

    def remote_player(self, index=0):
        """房主侧：权威对局里的远程角色。"""

        remote = [player for player in self.game.players
                  if player.controller_type.value == "remote_human"]
        return remote[index] if index < len(remote) else None

    def close(self):
        self.lan.close_session()


def client_hand_rects(client):
    """客户端牌桌上手牌的屏幕 rect（与「点击命中」用的是同一套几何）。"""

    layout = client.lan.remote.renderer.table_layout
    return list(layout.hand_rects) if layout is not None else []


def client_seat_rect(client, player_id):
    """客户端牌桌上某个角色的屏幕 rect（点它=选它当目标）。"""

    layout = client.lan.remote.renderer.table_layout
    if layout is None:
        return None
    for player, rect in layout.seat_rects.items():
        if getattr(player, "player_id", None) == player_id:
            return pygame.Rect(rect)
    if client.lan.remote.view.player.player_id == player_id:
        return pygame.Rect(layout.metrics.player_status)
    return None


def pump(instances, seconds=1.0, predicate=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for instance in instances:
            instance.tick()
        if predicate is not None and predicate():
            return True
        time.sleep(FRAME / 2)
    return predicate() if predicate is not None else True


def wait(instances, predicate, timeout=8.0, what=""):
    ok = pump(instances, timeout, predicate)
    if not ok:
        print("      （等待超时：%s）" % what)
    return ok


def drain(host, client, pump, seconds=10.0):
    """把桌面上的残留结算跑完，回到"没有任何等待"的状态。

    房主侧要回应的窗口替他放弃；远程侧的决策按策略回答（走的是与界面同一个
    answer 通道）。场景之间先 drain，后面的断言就不会被上一段的残局影响。
    """

    from src.game.engine import PassPendingAction
    from src.game.engine.pending import PendingRequestType
    from tools.lan_gameplay_bridge import auto_policy

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        game = host.game
        current = game.engine.pending.current
        if game.response.active:
            game.pass_response()
        elif (current is not None and current.target is game.player
              and current.request_type is PendingRequestType.RESPOND_CARD):
            game.submit_action(PassPendingAction(
                game.player, current.request_id))
        elif game.choice.active:
            # 房主界面上还挂着一个选择框：替他选"否"，否则远程玩家的出牌
            # 阶段会一直被这个模态挡住。
            game.choice.choose_no()
        elif not game.busy and current is None and not game.choice.active:
            return True
        live = client.lan.session.match
        if live is not None and live.waiting_decision:
            result = auto_policy(live.decision, live)
            if result is not None:
                live.answer(result)
        pump([host, client], 0.1)
    return not host.game.busy


def main():
    pygame.init()
    print("=" * 70)
    print("Phase 11.2 真实界面验证：房主牌桌 + 客户端决策面板")
    print("=" * 70)

    host = UiInstance("房主")
    client = UiInstance("客户端")
    try:
        host.lan.enter(host.game)
        client.lan.enter(client.game)
        host.lan.menu.nickname_field.set_text("房主")
        host.lan.menu.port_field.set_text("0")
        # 本工具验证的是 Phase 11.2 的"2 人自由混战 + 真实界面"：自由混战不
        # 分配身份、不补位 AI，于是"双方各一张牌桌"的每一步期望都是确定的。
        # 默认的身份局（补位 AI + 先看身份再选将）由 Phase 11.4 的测试覆盖。
        host.lan.menu.set_match_mode("ffa", host.lan.session, host.game)
        host.lan.menu.create_room(host.lan.session, host.game)
        host.tick()

        client.lan.menu.nickname_field.set_text("玩家A")
        client.lan.menu.ip_field.set_text("127.0.0.1")
        client.lan.menu.port_field.set_text(str(host.lan.session.host.bound_port))
        client.lan.menu.join_room(client.lan.session, client.game)

        print("\n[1] 开局")
        check("客户端用真实界面加入房间",
              wait([host, client], lambda: client.game.scene == "lobby", 8.0, "进大厅"))
        client.lan.session.set_ready(True)
        wait([host, client], lambda: host.lan.session.lobby.can_start(), 6.0, "全员准备")
        ok, message = host.lan.session.start_match()
        check("房主从大厅点开始游戏", ok, message)
        wait([host, client], lambda: client.game.scene == "remote_game", 8.0, "客户端进面板")
        check("房主进入普通牌桌（scene=game）", host.game.scene == "game", host.game.scene)
        check("客户端进入远程决策面板（scene=remote_game）",
              client.game.scene == "remote_game", client.game.scene)
        check("客户端面板拿到自己的手牌",
              len(client.lan.session.match.hand) == len(host.remote_player().hand),
              "%d 张" % len(client.lan.session.match.hand))
        check("客户端面板看到双方公开信息",
              len(client.lan.session.match.players) == 2,
              str([item["nickname"] for item in client.lan.session.match.players]))

        print("\n[2] 等待期间界面不冻结 + 分辨率切换")
        frames_before = (host.frames, client.frames)
        pump([host, client], 1.2)
        check("双方在等待期间持续跑帧",
              host.frames > frames_before[0] + 20 and client.frames > frames_before[1] + 20,
              "房主 +%d / 客户端 +%d 帧" % (
                  host.frames - frames_before[0], client.frames - frames_before[1]))
        for size in RESOLUTIONS:
            for instance in (host, client):
                instance.resize(size)
        check("四种分辨率来回切换后界面仍然正常",
              client.lan.remote.view.revision > 0
              and host.lan.session.match.aborted == "")
        check("分辨率切换后客户端牌桌仍能画出手牌",
              all(rect.width > 0 for rect in client_hand_rects(client))
              or not client.lan.session.match.hand)

        print("\n[3] 远程玩家用真实界面出牌")
        from tests.legacy_helpers import normal_sha

        # 只做"合法的测试准备"：给远程玩家一套确定的手牌，以及房主一张【杀】。
        # 回合本身仍然由引擎自己流转（房主结束自己的出牌阶段→轮到远程玩家），
        # 手工改 current_turn_player 会破坏 TurnFlow 的内部账本。
        remote = host.remote_player()
        remote.hand = [normal_sha(), normal_sha(), normal_sha()]
        host.game.player.hand = [normal_sha()]
        host.game.player.sha_used = False
        host.game.actions.clear()
        host.game.end_player_turn(host.game.player)
        guard = 0
        while (host.game.phase == "discard"
               and host.game.current_turn_player is host.game.player and guard < 15):
            host.game.player_discard(0, RECT)
            pump([host, client], 0.3)
            guard += 1

        got = wait([host, client],
                   lambda: (client.lan.session.match.decision or {}).get("kind")
                   == DecisionKind.PLAY_PHASE, 12.0, "出牌阶段请求")
        decision = client.lan.session.match.decision or {}
        check("客户端面板收到出牌阶段请求",
              got and decision.get("kind") == DecisionKind.PLAY_PHASE,
              str(decision.get("kind")))
        check("请求里每张可用牌都带了合法目标",
              bool(decision.get("cards")) and all(
                  "targets" in card for card in decision["cards"]),
              str([(card["name"], len(card.get("targets", ()))) for card in
                   decision.get("cards", ())[:4]]))

        before_hand = len(remote.hand)
        client.tick()                      # 先画一帧，保证手牌 rect 已算好

        # 用真实点击完成一次出牌。决策面板可能在点击途中被房主换掉（更高优先级
        # 的请求会顶掉出牌面板），所以允许重试，最多三轮。
        played_ids = []
        for _attempt in range(3):
            live = client.lan.session.match.decision
            if live is None or not live.get("cards"):
                break
            client.tick()
            hand_rects = client_hand_rects(client)
            if not hand_rects:
                break
            live_ids = [item.get("card_id") for item in live.get("cards", ())]
            index = next(
                (i for i, entry in enumerate(client.lan.session.match.hand)
                 if entry.get("card_id") in live_ids), 0)
            client.click(hand_rects[index].center)
            selected = (client.lan.remote.decision.cards or [None])[0]
            chosen = next((item for item in live.get("cards", ())
                           if item.get("card_id") == selected), None)
            if chosen is not None and int(chosen.get("min_targets") or 0) > 0:
                target_ids = [item["player_id"] for item in live.get("targets", ())]
                for target in chosen.get("targets", ())[:1]:
                    rect = client_seat_rect(client, target["player_id"])
                    if rect is not None:
                        client.click(rect.center)
            client.click(client.lan.remote.renderer.primary_button.rect.center)
            answer = client.lan.session.match.last_answer or {}
            played_ids = list((answer.get("result") or {}).get("card_ids") or ())
            if played_ids:
                break
            pump([host, client], 0.5)

        check("客户端用真实点击完成选择并提交", bool(played_ids),
              str(client.lan.session.match.last_answer and
                  client.lan.session.match.last_answer.get("result")))
        moved = wait(
            [host, client],
            lambda: bool(played_ids) and not any(
                card.id in played_ids for card in remote.hand),
            10.0, "房主执行远程出牌")
        check("房主执行了这次远程出牌（被点的牌离开权威手牌）", moved,
              "点了 %s，手牌 %d → %d 张" % (played_ids, before_hand, len(remote.hand)))
        check("房主侧没有拒绝这条响应",
              not host.lan.session.match.registry.rejected,
              str([item["code"] for item in
                   host.lan.session.match.registry.rejected]))

        drain(host, client, pump)

        print("\n[4] 远程玩家用真实界面结束回合")
        # 完全走真实路径：房主结束自己的出牌阶段（含交互式弃牌），把回合交给
        # 远程玩家；远程玩家用真实界面点「结束回合」，中间可能还有弃牌决策，
        # 由策略通过同一条 answer 通道回答。
        from tools.lan_gameplay_bridge import auto_policy

        host.game.actions.clear()
        host.game.end_player_turn(host.game.player)
        guard = 0
        while (host.game.phase == "discard"
               and host.game.current_turn_player is host.game.player and guard < 15):
            host.game.player_discard(0, RECT)
            pump([host, client], 0.3)
            guard += 1

        live = wait([host, client],
                    lambda: host.game.current_turn_player is remote, 10.0, "回合交给远程玩家")
        check("回合交到远程玩家手上", live, host.game.current_turn_player.name)

        def until_play_phase():
            """等出牌阶段请求；途中冒出来的别的决策（残留弃牌等）先答掉。"""

            m = client.lan.session.match
            if m is None or not m.waiting_decision:
                return False
            if (m.decision or {}).get("kind") == DecisionKind.PLAY_PHASE:
                return True
            result = auto_policy(m.decision, m)
            if result is not None:
                m.answer(result)
            return False

        live = wait([host, client], until_play_phase, 15.0, "出牌阶段请求")
        check("远程玩家在真实界面上拿到出牌阶段", live, str(
            (client.lan.session.match.decision or {}).get("kind")))

        client.tick()
        client.click(client.lan.remote.renderer.secondary_button.rect.center)
        check("客户端用真实界面点「结束回合」把决定发回房主",
              client.lan.session.match.last_answer is not None,
              str(client.lan.session.match.last_answer and
                  client.lan.session.match.last_answer.get("result")))

        # 房主接受了这次"结束阶段"的两种正解：回合推进了，或进入该玩家的
        # 弃牌阶段（后续弃牌链由 tools/lan_gameplay_bridge 的确定性场景覆盖）。
        def end_phase_applied():
            if host.game.current_turn_player is not remote:
                return True
            if host.game.phase == "discard":
                return True
            m = client.lan.session.match
            if m is not None and m.waiting_decision                     and (m.decision or {}).get("kind") == DecisionKind.SELECT_CARDS:
                return True
            return False

        answered = 0
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not end_phase_applied():
            m = client.lan.session.match
            if m is not None and m.waiting_decision:
                result = auto_policy(m.decision, m)
                if result is not None and m.answer(result):
                    answered += 1
            pump([host, client], 0.08)
        check("房主的权威回合流程接受了远程的结束阶段", end_phase_applied(),
              "补答 %d 次；回合=%s 阶段=%s busy=%s pending=%s" % (
                  answered, host.game.current_turn_player.name, host.game.phase,
                  host.game.busy, host.game.engine.pending.active))
        check("整轮没有出现回合守卫兜底",
              "（回合守卫）" not in " ".join(host.game.game_log))
        drain(host, client, pump)

        print("\n[5] 客户端掉线：房主不卡在等待里")
        match = host.lan.session.match
        # 确定地制造一次"房主等远程玩家响应"：房主对远程玩家出一张【杀】
        from tests.legacy_helpers import normal_sha, shan

        remote.hand = [shan()]
        host.game.actions.clear()
        host.game.current_turn_player = host.game.player
        host.game.phase = "play"
        host.game.player.hand = [normal_sha()]
        host.game.player.sha_used = False
        host.game.player_use_card(0, RECT)
        wait([host, client], lambda: host.game.waiting_for_remote, 10.0, "房主等待远程响应")
        check("房主正在等远程玩家操作", host.game.waiting_for_remote is True)
        client.close()
        aborted = wait([host, client], lambda: match.aborted != "", 10.0, "对局终止")
        check("客户端退出后房主终止对局并回大厅", aborted, match.abort_reason)
        check("房主界面切回大厅场景", host.game.scene == "lobby", host.game.scene)
        check("等待中的决策已释放", not match.registry.waiting)
    finally:
        host.close()
        client.close()
        pygame.quit()

    failed = [item for item in _results if not item[1]]
    print("\n" + "=" * 70)
    print("共 %d 项检查，通过 %d 项，失败 %d 项"
          % (len(_results), len(_results) - len(failed), len(failed)))
    for name, _ok, detail in failed:
        print("  FAIL: %s %s" % (name, detail))
    print("=" * 70)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
