"""Phase 11.4.2：Local / LAN Host / LAN Client 必须走同一套身份局 UI。

上一次 parity 报告之所以是**假阳性**，是因为它只验证了"存在某个元素"与
"提示文案一致"，从来没有验证三件事：

1. 三个角色用的是不是**同一个 scene / layout / 点击路由**；
2. 同一状态下关键 rect 是不是**同一套 LayoutMetrics** 算出来的；
3. 点击、选牌、技能按钮、确认 / 取消是不是**同一份交互实现**。

这里把它们分别钉住。

测试级别（必须按级别描述结果）：
* 本文件是 **IN-PROCESS**：真实 socket、真实引擎、真实 Renderer，但房主与
  客户端在同一个进程里。
* 真实启动路径（多个独立 ``main.py`` 进程）在 ``tools/runtime_parity_run.py``，
  级别是 **LOCALHOST MULTI-PROCESS**。
* 本机只有一台电脑，**PHYSICAL TWO-PC: NOT TESTED**。
"""

import os
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.renderer import Renderer
from src.ui import interaction, layout
from src.ui.human_control import HumanController, LocalHumanController
from src.ui.remote_control import RemoteHumanController
from src.ui.remote_table import RemoteTableScene
from src.ui.view_adapter import RemoteGameView

SIZE = (1600, 900)
#: 客户端牌桌在工具里用的分辨率（与 harness 保持一致）。
CLIENT_SIZE = (1600, 1000)


def _rect(value):
    return (int(value.x), int(value.y), int(value.width), int(value.height))


class ControllerInterfaceTests(unittest.TestCase):
    """结构：三个角色共用同一个交互接口，不是"各写一套"。"""

    def test_both_humans_implement_the_same_interface(self):
        interface = {name for name in dir(HumanController)
                     if not name.startswith("_") and callable(getattr(HumanController, name))}
        self.assertIn("run_action", interface)
        for name in sorted(interface):
            self.assertTrue(hasattr(LocalHumanController, name),
                            "LocalHumanController 缺 " + name)
            self.assertTrue(hasattr(RemoteHumanController, name),
                            "RemoteHumanController 缺 " + name)

    def test_click_router_is_shared(self):
        """点击路由只有一份：单机与客户端都走 ui.interaction.handle_game_click。"""

        import inspect

        signature = inspect.signature(interaction.handle_game_click)
        self.assertIn("human", signature.parameters,
                      "点击路由必须接受 HumanController（否则客户端只能另写一套）")
        source = inspect.getsource(RemoteTableScene.handle_event)
        self.assertIn("handle_game_click", source,
                      "客户端牌桌必须复用单机的点击路由")

    def test_remote_table_owns_no_second_game_ui(self):
        """客户端牌桌不许再长出"第二套牌桌"：布局 / 选择 / 提交都不在它身上。"""

        for name in ("_toggle_card", "_toggle_candidate", "_toggle_target",
                     "_handle_skill", "_submit", "_submit_play", "_submit_skill",
                     "_run_action", "_seat_is_candidate", "_draw_buttons",
                     "_draw_hand", "_draw_prompt"):
            self.assertFalse(hasattr(RemoteTableScene, name),
                             "RemoteTableScene 还在自己实现 " + name)

    def test_human_decision_state_is_shared_not_copied(self):
        """客户端的选择状态与场景共用同一个对象（原地重置，不换壳）。"""

        pygame.display.init()
        pygame.font.init()
        screen = pygame.display.set_mode(SIZE)
        scene = RemoteTableScene(screen)
        before = scene.decision
        scene._sync_overlay(None)
        self.assertIs(scene.human.state, scene.decision)
        self.assertIs(scene.decision, before, "选择状态被换成了新对象")
        scene.screen = None


class GeometryParityTests(unittest.TestCase):
    """几何：同一分辨率下，三个角色拿到的命名区域是同一套。"""

    def setUp(self):
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode(SIZE)
        self.metrics = layout.LayoutMetrics(*SIZE)

    def test_named_regions_come_from_one_layout_metrics(self):
        """同一分辨率 → 同一批命名区域；没有任何一方自带第二套坐标。"""

        renderer = Renderer(self.screen)
        self.assertEqual(renderer.metrics.screen_w, SIZE[0])
        for name in ("central", "prompt", "player_status", "hand_area",
                     "primary_button", "secondary_button", "log_rect",
                     "action_card_rect", "response_card_rect"):
            self.assertEqual(getattr(renderer.metrics, name),
                             getattr(self.metrics, name),
                             "Renderer 与 LayoutMetrics 对 " + name + " 不一致")

    def test_seat_geometry_is_viewer_relative(self):
        """座次环上的位移决定位置：与自己同一个 offset 的对手，任何角色都落在同一个 rect。

        这就是"自己永远在下方、别人按相对座位排布"的几何表达。
        """

        class FakePlayer:
            def __init__(self, player_id, seat):
                self.player_id = player_id
                self.seat = seat
                self.hand = []
                self.equipment = {}
                self.judgement_zone = ()
                self.alive = True
                self.hp = 4
                self.max_hp = 4
                self.name = player_id
                self.general_id = None

        class FakeGame:
            def __init__(self, me_seat, total=5):
                self.players = [FakePlayer("P%d" % index, index)
                                for index in range(total)]
                self.player = self.players[me_seat]

        shapes = []
        for me_seat in range(5):
            game = FakeGame(me_seat)
            table = layout.TableLayout(game, self.metrics)
            ordered = sorted(game.players, key=lambda item: item.seat)
            my_index = ordered.index(game.player)
            row = {}
            for player in ordered:
                if player is game.player:
                    continue
                offset = (ordered.index(player) - my_index) % len(ordered)
                row[offset] = _rect(table.seat_rect(player))
            shapes.append(row)
        first = shapes[0]
        for row in shapes[1:]:
            self.assertEqual(row, first,
                             "换了座位之后，同一 offset 的对手没有落在同一位置")


class LanClientUsesIdentityGameTableTests(unittest.TestCase):
    """端到端（IN-PROCESS）：身份局的客户端拿到的就是同一张身份局牌桌。"""

    @classmethod
    def setUpClass(cls):
        from tools.lan_view_harness import ClientSide, HostSide, close_all, start_match

        cls.host = HostSide(nickname="房主", game_mode="identity")
        cls.clients = [ClientSide("甲%d" % index, cls.host.port, render=True)
                       for index in range(4)]
        cls.pump, ok, message = start_match(cls.host, cls.clients)
        if not ok:
            raise AssertionError("身份局没有建立：" + str(message))
        cls.pump(1.0)

    @classmethod
    def tearDownClass(cls):
        from tools.lan_view_harness import close_all

        close_all(cls.host, cls.clients)

    def test_client_plays_on_the_identity_table(self):
        view = self.clients[0].view
        self.assertEqual(view.game_mode, "identity")
        for player in view.players:
            self.assertIsNotNone(player.general_id,
                                 "身份局座位没有武将：界面会失去技能栏与武将信息")
        self.assertIsNotNone(self.host.game.mode.lord(),
                             "权威对局没有主公")

    def test_client_view_drives_the_same_renderer_and_layout(self):
        client = self.clients[0]
        scene = client.scene
        scene.sync_layout(client.renderer.metrics)
        client.draw_frame()
        table = client.renderer.table_layout
        self.assertIsNotNone(table, "客户端没有走既有 TableLayout")
        self.assertIs(table.game, scene.view)
        self.assertEqual(
            _rect(client.renderer.metrics.prompt),
            _rect(layout.LayoutMetrics(*CLIENT_SIZE).prompt),
            "客户端画出来的提示条不是 LayoutMetrics 算出来的同一个 rect")

    def test_client_uses_the_shared_human_controller(self):
        client = self.clients[0]
        self.assertIsInstance(client.scene.human, RemoteHumanController)
        self.assertFalse(client.scene.human.local_interaction)
        # 客户端的动作仍然只走"发回房主"这一条路。
        self.assertTrue(hasattr(client.scene.human, "run_action"))


if __name__ == "__main__":
    unittest.main()
