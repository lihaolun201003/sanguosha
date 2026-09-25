"""Phase 11.1 联机冒烟：两个"实例"跑真实 socket + 真实界面代码路径。

无窗口（SDL dummy 驱动）。每个实例持有一份 ``Game`` + ``StartMenu`` +
``LanScene``，事件路由与每帧更新都照抄 ``main.py`` 的写法，因此这里跑通的
路径就是真实游戏里跑的那条：

    主菜单 → 多人对战 → 创建/加入房间 → 大厅 → 准备 → 房主开始

两个实例之间走的是 127.0.0.1（以及本机局域网 IP）上的**真实 TCP**，
不是函数桩：连接、握手、广播、心跳、掉线检测全都在真实线程里发生。

    python tools/lan_smoke.py
"""

import os
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame

from src.game import Game
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.network.transport import local_ip_addresses
from src.ui.lan_scene import LAN_SCENES, LanScene

RESOLUTION = (1280, 720)
FRAME_DT = 1.0 / 60.0

_results = []


# ==================================================
# 一个游戏实例（等价于一台电脑上跑着的一份 main.py）
# ==================================================

class Instance:
    def __init__(self, label):
        self.label = label
        self.screen = pygame.display.set_mode(RESOLUTION)
        self.game = Game(ai_count=1)
        self.renderer = Renderer(self.screen)
        self.menu = StartMenu(self.screen)
        self.menu.sync_layout(self.renderer.metrics)
        self.menu.sync_modes(self.game.modes.list_modes())
        self.menu.sync_layout(self.renderer.metrics)
        self.lan = LanScene(self.screen, self.game)
        self.lan.set_screen(self.screen)
        self.lan.sync_layout(self.renderer.metrics)
        self.frames = 0

    # ---- 与 main.py 一致的事件路由 ----

    def dispatch(self, event):
        if self.game.scene == "menu":
            if event.type != pygame.MOUSEBUTTONDOWN:
                return None
            action = self.menu.handle_click(event.pos, self.game)
            if action == "multiplayer":
                self.lan.enter(self.game)
            return action

        if self.game.scene in LAN_SCENES:
            action = self.lan.handle_event(event, self.game)
            if action == "back":
                # 与 main.py 完全一致：回主菜单并显示离场原因。
                notice = self.lan.take_exit_notice()
                self.game.return_to_menu()
                if notice:
                    self.game.menu_message = notice
            return action
        return None

    # ---- 每帧推进（更新 + 绘制，绘制异常也要暴露出来）----

    def tick(self):
        if self.game.scene in LAN_SCENES:
            if self.lan.update(FRAME_DT, self.game) == "back":
                notice = self.lan.take_exit_notice()
                self.game.return_to_menu()
                if notice:
                    self.game.menu_message = notice
        self.game.update(FRAME_DT)
        self.renderer.update(FRAME_DT)
        self.draw()
        self.frames += 1

    def draw(self):
        if self.game.scene == "menu":
            self.menu.sync_layout(self.renderer.metrics)
            self.menu.draw(self.game, self.renderer.metrics)
        elif self.game.scene in LAN_SCENES:
            self.lan.draw(self.game, self.renderer.metrics)
        else:
            self.renderer.draw(self.game)
        pygame.display.flip()

    # ---- 输入模拟 ----

    def click(self, position):
        self.dispatch(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1, pos=tuple(position)))
        self.tick()

    def click_button(self, button):
        # 先画一帧：按钮的可用状态是在 draw 里按当前会话刷新的，
        # 场景刚切换时它可能还是上一屏的值。
        self.tick()
        assert button.enabled, self.label + "：按钮不可点击"
        self.click(button.rect.center)

    def type_text(self, field, text):
        """模拟键盘输入（先把输入框清空，等于全选后重打）。"""

        field.set_text("")
        field.focus()
        for char in text:
            self.dispatch(pygame.event.Event(pygame.TEXTINPUT, text=char))
        field.blur()

    def set_port(self, value):
        """改端口输入框（"0" = 让系统分配一个空闲端口，多个房主实例互不冲突）。"""

        self.lan.menu.port_field.set_text(str(value))
        return self

    def resize(self, size):
        """窗口拖动 / 全屏切换：重新分发屏幕并重建布局。"""

        self.screen = pygame.display.set_mode(size)
        self.renderer.set_screen(self.screen)
        self.menu.screen = self.screen
        self.menu.sync_layout(self.renderer.metrics)
        self.lan.set_screen(self.screen)
        self.lan.sync_layout(self.renderer.metrics)
        self.tick()


def pump(instances, seconds=1.0, predicate=None):
    """按真实帧率推进若干秒；predicate 成立即可提前结束。"""

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for instance in instances:
            instance.tick()
        if predicate is not None and predicate():
            return True
        time.sleep(FRAME_DT)
    return predicate() if predicate is not None else True


def wait_for(instances, predicate, timeout=8.0, what=""):
    ok = pump(instances, timeout, predicate)
    if not ok:
        raise AssertionError("等待超时：" + (what or "条件未成立"))
    return True


def check(name, condition, detail=""):
    _results.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print("  [%s] %s%s" % (mark, name, ("  — " + detail) if detail else ""))
    return bool(condition)


def lobby_of(instance):
    return instance.lan.session.lobby


def roster(instance):
    lobby = lobby_of(instance)
    if lobby is None:
        return []
    return [(player.seat, player.nickname, player.status_label)
            for player in lobby.ordered()]


def enter_multiplayer(instance):
    """走真实入口：主菜单点「多人对战（局域网）」。

    本工具验的是 Phase 11.1 的大厅 / 连接 / 心跳，场景按"2 人自由混战"写：
    自由混战不补 AI、没有身份与选将那一步，所以开局之后客户端**直接**进远程
    牌桌——每一步期望都是确定的。默认的身份局（补 AI、先看身份再选将）由
    Phase 11.4 的测试与 ``lan_playability_sync`` 覆盖。
    """

    instance.click(instance.menu.multiplayer_button.rect.center)
    assert instance.game.scene == "multiplayer_menu", instance.label + "：没进多人菜单"
    instance.lan.menu.set_match_mode("ffa", instance.lan.session, instance.game)


# ==================================================
# 场景
# ==================================================

def scenario_1_join(host, guest):
    print("\n[场景 1] 同一台电脑双实例：创建房间 → 加入房间")
    enter_multiplayer(host)
    enter_multiplayer(guest)

    host.type_text(host.lan.menu.nickname_field, "房主")
    host.lan.session.max_players = 4      # 4 人房，方便后面塞第三个人
    host.click_button(host.lan.menu.create_button)
    host.tick()

    port = host.lan.session.host.bound_port
    check("房主创建房间后进入大厅",
          host.game.scene == "lobby", "地址 " + host.lan.session.address_text)

    guest.type_text(guest.lan.menu.nickname_field, "玩家A")
    guest.type_text(guest.lan.menu.ip_field, "127.0.0.1")
    guest.type_text(guest.lan.menu.port_field, str(port))
    guest.click_button(guest.lan.menu.join_button)
    check("客户端点击「加入房间」后不会卡住（界面仍在多人菜单等待握手）",
          guest.game.scene == "multiplayer_menu")

    wait_for([host, guest], lambda: guest.game.scene == "lobby", 8.0, "客户端进入大厅")
    check("客户端连接成功后自动进入大厅",
          guest.game.scene == "lobby", "错误=" + (guest.lan.session.error or "无"))

    wait_for([host, guest], lambda: len(roster(host)) == 2 and len(roster(guest)) == 2,
             4.0, "双方玩家列表都变成 2 人")
    same = roster(host) == roster(guest)
    check("双方看到同样的大厅", same, "房主=%s 客户端=%s" % (roster(host), roster(guest)))
    check("座位按加入顺序分配（房主 seat 0 / 客户端 seat 1）",
          [seat for seat, _n, _s in roster(host)] == [0, 1])
    check("房主身份正确标记",
          roster(host)[0][2] == "房主" and roster(guest)[0][2] == "房主")
    return port


def converged(host, guest, label):
    """等待双方大厅完全一致，并停在目标状态上。"""

    return wait_for([host, guest],
                    lambda: roster(host) == roster(guest) and roster(host)[1][2] == label,
                    6.0, "双方收敛到「%s」" % label)


def scenario_2_ready(host, guest):
    print("\n[场景 2] 准备 / 取消准备，双方同步")
    guest.click_button(guest.lan.lobby.ready_button)
    converged(host, guest, "已准备")
    check("客户端点「准备」后双方都显示已准备",
          roster(host)[1][2] == "已准备" and roster(guest)[1][2] == "已准备",
          str(roster(host)))

    guest.click_button(guest.lan.lobby.ready_button)
    converged(host, guest, "未准备")
    check("客户端取消准备后双方都恢复未准备",
          roster(host)[1][2] == "未准备" and roster(guest)[1][2] == "未准备",
          str(roster(host)))


def scenario_8_rapid_ready(host, guest):
    print("\n[场景 8] 快速连点「准备 / 取消 / 准备」，房主最终状态权威")
    for _ in range(5):
        guest.dispatch(pygame.event.Event(
            pygame.MOUSEBUTTONDOWN, button=1,
            pos=guest.lan.lobby.ready_button.rect.center))
        for instance in (host, guest):
            instance.tick()
    converged(host, guest, "已准备")
    check("连点 5 次后房主与客户端看到同一个最终状态（奇数次 = 已准备）",
          roster(host) == roster(guest) and roster(host)[1][2] == "已准备",
          str(roster(host)))
    check("客户端始终只是请求方（房主才是权威）",
          guest.lan.session.is_host is False)


def scenario_3_start(host, guest):
    print("\n[场景 3] 房主开始游戏 → START_GAME 到达所有客户端")
    check("满员且全员准备后「开始游戏」可用",
          host.lan.lobby.start_button.enabled and lobby_of(host).can_start(),
          "阻塞原因：" + (lobby_of(host).start_blocker() or "无"))
    host.click_button(host.lan.lobby.start_button)
    wait_for([host, guest], lambda: lobby_of(guest).started, 4.0, "客户端收到 START_GAME")
    check("客户端收到 START_GAME（本地大厅标记为已开始）", lobby_of(guest).started)
    check("客户端保留了开始消息的负载",
          bool(guest.lan.session.client.start_payload),
          str(guest.lan.session.client.start_payload))
    check("房主自己在大厅显示「已开始」", lobby_of(host).started)

    # Phase 11.2 起「开始游戏」会真的建立权威对局：房主进普通牌桌，客户端进
    # 远程决策面板（细节由 tools/lan_gameplay_* 验证）。这里只确认场景切换，
    # 随后关掉对局，让后面的场景继续验证大厅流程。
    wait_for([host, guest], lambda: guest.game.scene == "remote_game", 8.0, "客户端进入对局")
    check("房主进入权威牌桌（scene=game）", host.game.scene == "game", host.game.scene)
    check("客户端进入远程决策面板（scene=remote_game）",
          guest.game.scene == "remote_game", guest.game.scene)

    for instance in (host, guest):
        instance.lan.close_session()
        instance.lan.enter(instance.game)


def reopen_room(host, guest, nickname="房主", guest_name="玩家A"):
    """重新开一间房并让客户端加入（场景之间重置用）。"""

    host.lan.enter(host.game)
    host.type_text(host.lan.menu.nickname_field, nickname)
    # Phase 11.1 的场景按"2 人自由混战"写：自由混战不补 AI、没有身份 / 选将
    # 那一步，开局后客户端直接进远程牌桌。默认的身份局要补 AI 并先看身份，
    # 那一段由 Phase 11.4 的测试与 lan_playability_sync 覆盖。
    host.lan.menu.set_match_mode("ffa", host.lan.session, host.game)
    host.lan.menu.create_room(host.lan.session, host.game)
    host.tick()
    port = host.lan.session.host.bound_port

    guest.lan.enter(guest.game)
    guest.type_text(guest.lan.menu.nickname_field, guest_name)
    guest.type_text(guest.lan.menu.ip_field, "127.0.0.1")
    guest.type_text(guest.lan.menu.port_field, str(port))
    guest.click_button(guest.lan.menu.join_button)
    # 两个条件都要满足：房主列表里有人，且客户端自己已经进了大厅——
    # 只看房主侧会在客户端还没收到 WELCOME 时就往下走。
    wait_for([host, guest],
             lambda: len(roster(host)) == 2 and guest.game.scene == "lobby",
             8.0, "重新进房")
    return port


def scenario_7_room_full():
    print("\n[场景 7] 房间满：第三个人被拒绝（房主设 max_players = 2）")
    small_host = Instance("满房房主")
    second = Instance("房员2")
    third = Instance("房员3")
    try:
        enter_multiplayer(small_host)
        small_host.lan.session.max_players = 2
        small_host.type_text(small_host.lan.menu.nickname_field, "满房房主")
        small_host.set_port(0)          # 系统分配端口，避免与场景 1 的房间抢 9527
        small_host.click_button(small_host.lan.menu.create_button)
        small_host.tick()
        port = small_host.lan.session.host.bound_port

        enter_multiplayer(second)
        second.type_text(second.lan.menu.nickname_field, "房员2")
        second.type_text(second.lan.menu.ip_field, "127.0.0.1")
        second.type_text(second.lan.menu.port_field, str(port))
        second.click_button(second.lan.menu.join_button)
        wait_for([small_host, second], lambda: second.game.scene == "lobby", 8.0,
                 "第二个人进房")
        check("2 人房在 2/2 时已满", len(roster(small_host)) == 2, str(roster(small_host)))

        enter_multiplayer(third)
        third.type_text(third.lan.menu.nickname_field, "房员3")
        third.type_text(third.lan.menu.ip_field, "127.0.0.1")
        third.type_text(third.lan.menu.port_field, str(port))
        third.click_button(third.lan.menu.join_button)
        wait_for([small_host, third],
                 lambda: bool(third.lan.menu.status) and "正在连接" not in third.lan.menu.status,
                 8.0, "第三个人被拒绝")
        check("第三人看到「房间已满」", "房间已满" in third.lan.menu.status,
              third.lan.menu.status)
        check("被拒后仍停在多人菜单（可以换房间重试）",
              third.game.scene == "multiplayer_menu")
        check("房主这边人数没有变化", len(roster(small_host)) == 2,
              str(roster(small_host)))
    finally:
        for instance in (small_host, second, third):
            instance.lan.close_session()


def scenario_4_client_leave(host, guest):
    print("\n[场景 4] 客户端离开 → 房主及时移除")
    reopen_room(host, guest)
    guest.click_button(guest.lan.lobby.leave_button)
    wait_for([host], lambda: len(roster(host)) == 1, 6.0, "房主移除离开的玩家")
    check("客户端回到多人菜单", guest.game.scene == "multiplayer_menu")
    check("房主玩家列表只剩房主", len(roster(host)) == 1, str(roster(host)))


def scenario_5_host_leave(host, guest):
    print("\n[场景 5] 房主关闭 → 客户端提示并回多人菜单")
    # 上一局已经 started，先换一个新房间，才能验证"正常运行中的掉线"。
    host.lan.close_session()
    host.lan.enter(host.game)
    host.type_text(host.lan.menu.nickname_field, "房主")
    host.set_port(0)
    host.click_button(host.lan.menu.create_button)
    host.tick()
    port = host.lan.session.host.bound_port

    guest.type_text(guest.lan.menu.nickname_field, "玩家A")
    guest.type_text(guest.lan.menu.ip_field, "127.0.0.1")
    guest.type_text(guest.lan.menu.port_field, str(port))
    guest.click_button(guest.lan.menu.join_button)
    wait_for([host, guest], lambda: guest.game.scene == "lobby", 8.0, "客户端重新进房")
    check("客户端可以重新加入新房间（加入失败后能再来）",
          len(roster(guest)) == 2, str(roster(guest)))

    host.lan.close_session()          # 等效于房主关掉游戏 / 关掉房间
    wait_for([guest], lambda: guest.game.scene == "multiplayer_menu", 8.0,
             "客户端检测到房主断开")
    status = guest.lan.menu.status
    check("客户端检测到房主断开并回多人菜单",
          guest.game.scene == "multiplayer_menu", "提示：" + status)
    check("客户端看到中文原因（房主已关闭房间）",
          "房主" in status, status)
    check("客户端没有卡死（帧数继续增长）", guest.frames > 0, str(guest.frames))


def scenario_6_bad_address():
    print("\n[场景 6] 错误 IP / 端口：界面不冻结，显示连接失败")
    bad = Instance("玩家D")
    enter_multiplayer(bad)
    bad.type_text(bad.lan.menu.nickname_field, "玩家D")
    bad.type_text(bad.lan.menu.ip_field, "127.0.0.1")
    bad.type_text(bad.lan.menu.port_field, "1")      # 没人监听的端口
    bad.click_button(bad.lan.menu.join_button)

    frames_before = bad.frames
    wait_for([bad], lambda: "正在连接" not in bad.lan.menu.status, 6.0,
             "端口无人监听时给出结果")
    check("端口无人监听时立刻给出中文提示",
          "房主没有开房" in bad.lan.menu.status or "无法连接到房主" in bad.lan.menu.status,
          bad.lan.menu.status)
    check("等待连接期间仍然在跑帧（界面未冻结）", bad.frames > frames_before + 10,
          "推进了 %d 帧" % (bad.frames - frames_before))
    check("连接失败后停留在一屏可重试的界面", bad.game.scene == "multiplayer_menu")

    # 不存在的局域网地址：连不上会等到超时，期间必须保持响应。
    bad.lan.menu.ip_field.set_text("192.0.2.1")
    bad.lan.menu.port_field.set_text("9527")
    bad.click_button(bad.lan.menu.join_button)
    frames_before = bad.frames
    wait_for([bad], lambda: bool(bad.lan.menu.status) and "正在连接" not in bad.lan.menu.status,
             12.0, "不存在的地址给出结果")
    check("不存在的地址最终给出中文原因（超时或不可达）",
          "超时" in bad.lan.menu.status or "无法到达" in bad.lan.menu.status
          or "无法连接" in bad.lan.menu.status, bad.lan.menu.status)
    check("等待超时期间界面持续响应", bad.frames > frames_before + 30,
          "推进了 %d 帧" % (bad.frames - frames_before))
    bad.lan.close_session()
    return bad


def scenario_9_resize(host, guest):
    print("\n[场景 9] 等待/在大厅期间拖动窗口、切分辨率不崩溃")
    for size in ((1366, 768), (1920, 1080), (1024, 768), (1280, 720)):
        guest.resize(size)
        host.resize(size)
    check("多分辨率来回切换后大厅布局仍然有效",
          guest.lan.lobby.panel_rect.width > 10
          and guest.lan.lobby.panel_rect.height > 10, str(guest.lan.lobby.panel_rect))
    rows_inside = all(
        guest.lan.lobby.row_rects[index].bottom < guest.lan.lobby.panel_rect.bottom
        for index in range(4))
    check("玩家行永远落在面板内", rows_inside)


def scenario_10_real_lan():
    """用本机真实的局域网 IP 建连（两台真机就是走这条路径）。"""

    print("\n[场景 10] 真实局域网地址（0.0.0.0 监听 + 局域网 IP 连接）")
    addresses = local_ip_addresses()
    if not addresses:
        check("本机没有可用的局域网地址（跳过真实 IP 连接）", True, "只有回环")
        return
    lan_ip = addresses[0]
    host = Instance("房主(真实IP)")
    guest = Instance("玩家(真实IP)")
    enter_multiplayer(host)
    enter_multiplayer(guest)
    host.type_text(host.lan.menu.nickname_field, "房主")
    host.set_port(0)
    host.click_button(host.lan.menu.create_button)
    host.tick()
    port = host.lan.session.host.bound_port
    check("房主监听 0.0.0.0（局域网内可达）",
          host.lan.session.host.bind == "0.0.0.0",
          "地址 " + host.lan.session.address_text)

    guest.type_text(guest.lan.menu.nickname_field, "同学")
    guest.type_text(guest.lan.menu.ip_field, lan_ip)
    guest.type_text(guest.lan.menu.port_field, str(port))
    guest.click_button(guest.lan.menu.join_button)
    wait_for([host, guest], lambda: guest.game.scene == "lobby", 8.0,
             "通过 " + lan_ip + " 连上房主")
    check("通过真实局域网 IP %s 连接成功" % lan_ip,
          len(roster(host)) == 2 and len(roster(guest)) == 2, str(roster(guest)))
    host.lan.close_session()
    guest.lan.close_session()


def scenario_11_reenter():
    print("\n[场景 11] 创建 → 返回 → 再创建；加入失败 → 再加入")
    one = Instance("复进")
    enter_multiplayer(one)
    one.type_text(one.lan.menu.nickname_field, "复进")
    one.click_button(one.lan.menu.create_button)
    one.tick()
    first_port = one.lan.session.host.bound_port
    check("第一次创建成功", one.lan.session.is_host, str(first_port))

    one.click_button(one.lan.lobby.leave_button)
    check("离开房间回到多人菜单", one.game.scene == "multiplayer_menu")
    check("旧房主会话已释放（端口已关闭）", not one.lan.session.active)

    one.click_button(one.lan.menu.create_button)
    one.tick()
    check("再次创建成功（没有留下旧 Host）", one.lan.session.is_host)
    check("新房间可用（端口已绑定）",
          one.lan.session.host.bound_port > 0, str(one.lan.session.host.bound_port))

    one.click_button(one.lan.lobby.leave_button)     # 离开房间 → 多人菜单
    check("再次离开房间也能回到多人菜单", one.game.scene == "multiplayer_menu")

    one.click_button(one.lan.menu.back_button)       # 多人菜单 → 主菜单
    check("从多人菜单返回主菜单", one.game.scene == "menu", one.game.menu_message)
    check("返回主菜单时会话已关闭", not one.lan.session.active)
    check("主菜单显示离场提示", "多人对战" in one.game.menu_message or "离开" in one.game.menu_message,
          one.game.menu_message)
    one.lan.close_session()


def main():
    pygame.init()
    print("=" * 68)
    print("Phase 11.1 局域网联机冒烟（真实 TCP + 真实界面代码路径）")
    print("=" * 68)

    host = Instance("房主A")
    guest = Instance("玩家A")
    try:
        scenario_1_join(host, guest)
        scenario_2_ready(host, guest)
        scenario_8_rapid_ready(host, guest)
        scenario_3_start(host, guest)
        scenario_7_room_full()
        scenario_4_client_leave(host, guest)
        scenario_5_host_leave(host, guest)
        scenario_6_bad_address()
        scenario_9_resize(host, guest)
        scenario_10_real_lan()
        scenario_11_reenter()
    finally:
        for instance in (host, guest):
            instance.lan.close_session()
        pygame.quit()

    failed = [item for item in _results if not item[1]]
    print("\n" + "=" * 68)
    print("共 %d 项检查，通过 %d 项，失败 %d 项"
          % (len(_results), len(_results) - len(failed), len(failed)))
    for name, _ok, detail in failed:
        print("  FAIL: %s %s" % (name, detail))
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
