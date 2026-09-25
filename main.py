import sys

import pygame

from src.choice import ChoiceOverlay

from src.constants import (
    FPS,
    WINDOWED_SIZE,
)

from src.game import Game
from src.renderer import Renderer
from src.game.skills.standard.fanjian import FanjianFlow  # noqa: F401  (技能流程随包加载)
from src.start_menu import StartMenu
from src.ui.duel_hud import DuelHud
from src.ui.duel_setup import DuelSetupScreen
from src.ui.general_select import GeneralSelectScreen
from src.ui.identity_reveal import IdentityRevealScreen
from src.ui.interaction import handle_game_click
from src.ui.lan_scene import LAN_SCENES, LanScene
from src.ui.runtime_hook import RuntimeContext, load_runtime_hook


pygame.init()


def _desktop_size():
    """当前桌面分辨率（拿不到时退回窗口默认尺寸）。"""

    try:
        info = pygame.display.Info()
        if info.current_w and info.current_h:
            return (info.current_w, info.current_h)
    except pygame.error:
        pass
    return WINDOWED_SIZE


def _set_mode(fullscreen):
    """按需求切换显示模式，任何一步失败都安全退回窗口模式。"""

    if fullscreen:
        try:
            surface = pygame.display.set_mode(_desktop_size(), pygame.FULLSCREEN)
            return surface, True
        except pygame.error:
            pass
    try:
        return pygame.display.set_mode(WINDOWED_SIZE, pygame.RESIZABLE), False
    except pygame.error:
        return pygame.display.set_mode(WINDOWED_SIZE), False


screen, fullscreen_active = _set_mode(True)

pygame.display.set_caption("三国杀")


clock = pygame.time.Clock()


game = Game()


renderer = Renderer(screen)


choice_overlay = ChoiceOverlay(screen)


start_menu = StartMenu(screen)


general_select = GeneralSelectScreen(screen)


identity_reveal = IdentityRevealScreen(screen)


# 1v1 测试：设置页（开战前分别指定双方武将）+ 对局内控制条。
# 两者都只在「1v1 测试」这个模式下参与，其它模式的绘制与点击一行都不变。
duel_setup = DuelSetupScreen(screen)


duel_hud = DuelHud(screen)


# 局域网联机：多人菜单 + 房间大厅 + 客户端只读牌桌 + 每帧网络轮询都在它内部。
# 房主开局后用的就是本进程的 game —— 房主侧的对局本身即权威状态；
# 客户端侧复用同一个 Renderer 画房主下发的只读视图（Phase 11.3）。
lan_scene = LanScene(screen, game, renderer)


def apply_display_mode(fullscreen):
    """切换全屏 / 窗口，并让所有组件重新计算布局。"""

    global screen, fullscreen_active
    screen, fullscreen_active = _set_mode(fullscreen)
    resync_screens(screen)
    return screen


def resync_screens(surface):
    """把新的显示表面分发给所有界面组件，并立即重建布局。

    启动时（全屏）与窗口缩放都会走这里：漏掉任何一个组件，它就会继续
    按旧尺寸绘制，表现为"挤在一起"，直到某次点击才恢复。
    """

    renderer.set_screen(surface)
    choice_overlay.screen = surface
    choice_overlay.sync_layout(renderer.metrics)
    start_menu.screen = surface
    start_menu.sync_layout(renderer.metrics)
    start_menu.sync_modes(game.modes.list_modes())
    start_menu.sync_layout(renderer.metrics)
    general_select.screen = surface
    general_select.sync_layout(renderer.metrics, game.selectable_generals())
    identity_reveal.screen = surface
    identity_reveal.sync_layout(renderer.metrics)
    duel_setup.screen = surface
    duel_setup.sync_layout(renderer.metrics, game)
    duel_hud.screen = surface
    duel_hud.sync_layout(renderer.metrics)
    lan_scene.set_screen(surface)
    lan_scene.sync_layout(renderer.metrics)
    return surface


# 启动即按真实分辨率布局：全屏尺寸通常与设计尺寸不同，第一帧就要正确。
resync_screens(screen)


# ==================================================
# 运行期验证挂钩（只在本机做 UI 验收时启用）
#
# 设置了 SGS_RUNTIME_SCRIPT 时，主循环每帧把**真实用到的那一组对象**交给
# tools/ 下的脚本，用来驱动操作与截图；没有设置时这里是 None，主循环行为
# 与以前逐字节相同。见 src/ui/runtime_hook.py。
# ==================================================

runtime_hook = load_runtime_hook()

if runtime_hook is not None:
    runtime_hook.bind(RuntimeContext(
        screen=screen,
        game=game,
        renderer=renderer,
        start_menu=start_menu,
        general_select=general_select,
        identity_reveal=identity_reveal,
        choice_overlay=choice_overlay,
        lan_scene=lan_scene,
        settings=duel_setup,
        hud=duel_hud,
        resync=resync_screens,
    ))


# ==================================================
# 启动参数（可选）
#
# 直接进房间，省掉每次手点，也方便开两台机器做联机验证：
#
#   python main.py --host                        创建房间（默认端口 9527）
#   python main.py --host --port 9600 --max 4 --name 房主
#   python main.py --join 192.168.1.23            加入房间
#   python main.py --join 192.168.1.23:9600 --name 小明
# ==================================================

VALUE_OPTIONS = ("--join", "--name", "--port", "--max")


def parse_launch_options(argv):
    """极简参数解析：只认联机相关的几个开关，不引第三方库。"""

    options = {"mode": "", "name": "", "address": "", "port": "", "max": ""}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--host":
            options["mode"] = "host"
        elif token in VALUE_OPTIONS:
            value = argv[index + 1] if index + 1 < len(argv) else ""
            index += 1
            if token == "--join":
                options["mode"], options["address"] = "join", value
            elif token == "--name":
                options["name"] = value
            elif token == "--port":
                options["port"] = value
            elif token == "--max":
                options["max"] = value
        index += 1
    return options


def apply_launch_options():
    """按启动参数直接开房 / 进房；参数不合法时只在界面上提示，不影响启动。"""

    options = parse_launch_options(sys.argv[1:])
    if not options["mode"]:
        return

    lan_scene.enter(game)
    menu = lan_scene.menu
    if options["name"]:
        menu.nickname_field.set_text(options["name"])
    if options["address"]:
        menu.ip_field.set_text(options["address"])
    if options["port"]:
        menu.port_field.set_text(options["port"])
    if options["max"]:
        try:
            lan_scene.session.max_players = int(options["max"])
        except ValueError:
            pass

    if options["mode"] == "host":
        menu.create_room(lan_scene.session, game)
    else:
        menu.join_room(lan_scene.session, game)


apply_launch_options()


# 真实对局开启节奏模式：AI 响应排队出现，玩家能逐个看清。
game.ai_pacing = True


running = True


while running:

    dt = (
        clock.tick(FPS)
        / 1000.0
    )


    # ==================================================
    # 事件
    # ==================================================

    for event in pygame.event.get():

        if event.type == pygame.QUIT:

            running = False

            continue


        # ==================================================
        # ESC 在全屏下先退出全屏
        #
        # 放在联机路由之前：联机界面里 ESC 也有「返回」的含义，
        # 但"先退出全屏"的优先级在全程序保持一致。
        # ==================================================

        if (
            event.type == pygame.KEYDOWN
            and event.key == pygame.K_ESCAPE
            and fullscreen_active
        ):

            apply_display_mode(False)

            continue


        # ==================================================
        # 多人对战（局域网）
        #
        # 联机的两个场景自己接管事件：文字输入需要 TEXTINPUT / KEYDOWN，
        # 点击也不能落到游戏桌面的路由里。返回 "back" 表示要回主菜单，
        # 返回 None 表示事件与联机无关（F11 / 缩放等继续走下面的通用处理）。
        # ==================================================

        if game.scene in LAN_SCENES:

            lan_action = lan_scene.handle_event(event, game)

            if lan_action == "back":

                notice = lan_scene.take_exit_notice()
                game.return_to_menu()
                if notice:
                    game.menu_message = notice

            if lan_action:

                continue


        # ==================================================
        # 1v1 测试的设置页
        #
        # 放在"全屏切换与节奏快捷键"之前：搜索框正在输入时，按键必须归输入框，
        # 不能顺手把「1」当成调快节奏的快捷键（见 DuelSetupScreen.handle_key_event）。
        # 返回 None 表示这个事件与设置页无关（窗口缩放 / F11 继续走下面的通用处理）。
        # ==================================================

        if game.scene == "duel_setup":

            duel_action = duel_setup.handle_event(event, game)

            if duel_action == "start":

                if duel_setup.start_battle(game)[0]:

                    renderer.reset_effects()

            elif duel_action == "back":

                game.return_to_menu()

            if duel_action:

                continue


        # ==================================================
        # 全屏切换与节奏快捷键
        # ==================================================

        if event.type == pygame.KEYDOWN:

            if event.key == pygame.K_F11:
                apply_display_mode(not fullscreen_active)

            elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS, pygame.K_1):
                game.slower()

            elif event.key in (pygame.K_EQUALS, pygame.K_KP_PLUS, pygame.K_3):
                game.faster()

            continue


        # ==================================================
        # 窗口被拖动缩放：下一帧按新尺寸重建布局
        # ==================================================

        if event.type == pygame.VIDEORESIZE and not fullscreen_active:

            screen = pygame.display.set_mode(
                (max(640, event.w), max(480, event.h)), pygame.RESIZABLE
            )
            resync_screens(screen)

            continue


        if (
            event.type
            != pygame.MOUSEBUTTONDOWN
        ):

            continue


        if event.button != 1:

            continue


        # ==================================================
        # 开始界面
        # ==================================================

        if game.scene == "menu":

            start_menu.sync_layout(renderer.metrics)
            start_menu.sync_modes(game.modes.list_modes())
            menu_action = start_menu.handle_click(
                event.pos,
                game
            )

            if menu_action == "exit":

                running = False

            elif menu_action == "multiplayer":
                # 进入联机流程：先清掉可能残留的旧会话再进多人菜单。
                lan_scene.enter(game)

            elif menu_action == "select_general":
                # 开局流程已经由菜单推进（身份模式先看身份，1v1 测试先设置武将）。
                if game.scene == "identity_reveal":
                    identity_reveal.sync_layout(renderer.metrics)
                elif game.scene == "duel_setup":
                    duel_setup.on_enter(game)
                    duel_setup.sync_layout(renderer.metrics, game)
                else:
                    general_select.sync_layout(
                        renderer.metrics, game.selectable_generals())

            continue


        # ==================================================
        # 身份展示
        # ==================================================

        if game.scene == "identity_reveal":

            identity_reveal.sync_layout(renderer.metrics)
            if identity_reveal.handle_click(event.pos, game) == "continue":
                if lan_scene.is_host_setup():
                    # 联机开局：候选武将由网络桥分配（各真人互斥），所以不能走
                    # 单机的 confirm_identity()（它会用本机 rng 再抽一份候选）。
                    # 这一步还负责把"我确认过身份了"告诉网络桥——只切屏不通知，
                    # 流程会一直停在等身份确认，对方永远收不到候选武将。
                    lan_scene.host_confirm_identity(game)
                else:
                    game.confirm_identity()
                general_select.sync_layout(
                    renderer.metrics, game.selectable_generals())

            continue


        # ==================================================
        # 选将界面
        # ==================================================

        if game.scene == "general_select":

            general_select.sync_layout(renderer.metrics, game.selectable_generals())
            if lan_scene.is_host_setup():
                # 联机开局：不在这里开单机局，把选择回给网络桥；所有人选完
                # 之后由房主统一发牌开局（那时场景会被切成 "game"）。
                general_select.notice = lan_scene.setup_notice()
            select_action = general_select.handle_click(event.pos, game)

            if select_action == "confirm":
                if lan_scene.is_host_setup():
                    if not lan_scene.host_pick_general(game):
                        general_select.notice = lan_scene.setup_notice()
                else:
                    game.confirm_general()
            elif select_action == "back":
                game.return_to_menu()

            continue


        # ==================================================
        # 二选一弹窗优先，出现时拦截其它点击
        # ==================================================

        if game.choice.active:

            choice_overlay.handle_click(
                event.pos,
                game.choice
            )

            continue


        # ==================================================
        # 1v1 测试的控制条（当前操作提示 + 重开 / 换将 / 主菜单）
        #
        # 它只在自己那三个按钮上接管点击，其余位置返回 None，继续走下面的
        # 常规牌桌路由，所以牌桌上的操作一点都没变。
        # ==================================================

        hud_action = duel_hud.handle_click(event.pos, game)

        if hud_action:

            duel_hud.run_action(hud_action, game)

            continue


        # ==================================================
        # 游戏内点击：路由集中在 src/ui/interaction.py
        #
        # 真实事件循环与 dummy 测试共用同一份逻辑；末端动作交给当前这位
        # 玩家对应的 HumanController（单机是"本机权威"，联网客户端是
        # "把动作发回房主"）。客户端的点击在 lan_scene 里已经处理，走的是
        # 同一个 handle_game_click，只是 human 不同。
        # ==================================================

        handle_game_click(event.pos, game, renderer, lan_scene.human_for(game))


    # ==================================================
    # 更新
    #
    # 联机会话每帧都要排空网络事件：连接成功 / 掉线 / 房主开始游戏 /
    # 远程玩家的决策响应，全部在这里转成界面变化。**必须在所有场景下都调用**
    # ——房主打对局时用的是普通牌桌（scene == "game"），但网络照样要收。
    # 网络 I/O 全在后台线程，主线程只消费事件。
    # ==================================================

    if lan_scene.update(dt, game) == "back":

        notice = lan_scene.take_exit_notice()
        game.return_to_menu()
        if notice:
            game.menu_message = notice


    game.update(dt)

    renderer.update(dt)


    # ==================================================
    # 绘制
    # ==================================================

    if game.scene == "menu":

        start_menu.draw(game, renderer.metrics)

    elif game.scene in LAN_SCENES:

        lan_scene.draw(game, renderer.metrics)

    elif game.scene == "identity_reveal":

        # 联机开局：这一行提示每帧更新（"还在等谁"会随对方操作变化）。
        identity_reveal.notice = (
            lan_scene.setup_notice(game.scene) if lan_scene.is_host_setup() else "")
        start_menu.draw(game, renderer.metrics)
        identity_reveal.draw(game, renderer.metrics)

    elif game.scene == "general_select":

        general_select.notice = (
            lan_scene.setup_notice(game.scene) if lan_scene.is_host_setup() else "")
        general_select.draw(game, renderer.metrics)

    elif game.scene == "duel_setup":

        duel_setup.draw(game, renderer.metrics)

    else:

        renderer.draw(game)

        # 1v1 测试：牌桌之上再画一条控制条（其它模式里它自己不画）。
        duel_hud.draw(game, renderer.metrics)


    # ==================================================
    # 二选一界面最后绘制
    #
    # 这样会覆盖在正常游戏界面最上层。
    # ==================================================

    choice_overlay.draw(
        game.choice,
        renderer.metrics
    )


    pygame.display.flip()


    # 运行期脚本看到的是**刚刚画完并翻转的这一帧**，与玩家屏幕上的一致。
    if runtime_hook is not None:

        if runtime_hook.step(dt):

            running = False


# 退出前收尾：房主关房会通知所有客户端，避免对方停在"等房主"的状态。
lan_scene.close_session()


if runtime_hook is not None:

    runtime_hook.finish()


pygame.quit()
