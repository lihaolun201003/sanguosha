"""Phase 11.1 tests: LAN protocol framing, lobby authority, and the LAN UI shell.

这些用例只覆盖"确定性的、值得回归的部分"：帧切分、大厅权威规则、加入 /
离开 / 准备 / 房间满 / 版本不符，以及联机界面的场景切换与布局不变量。
真实双实例的运行验证在 ``tools/lan_smoke.py``。

所有连接都走 127.0.0.1 上的真实 socket，端口让系统分配（0），因此不会
和真实游戏或其它用例抢端口。
"""

import os
import socket
import time
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame

from src.game import Game
from src.network.protocol import (
    PROTOCOL_VERSION,
    FrameDecoder,
    MessageType,
    ProtocolError,
    decode_message,
    encode_message,
    make_message,
    message_type,
)
from src.network.lobby import LobbyState, clean_nickname
from src.network.session import LanSession, parse_host_address, parse_port
from src.renderer import Renderer
from src.start_menu import StartMenu
from src.ui.lan_scene import LanScene
from src.ui.lobby import LobbyScreen
from src.ui.multiplayer_menu import MultiplayerMenuScreen
from src.ui.text_input import TextField, accepts_digits_only


def pump(sessions, predicate, timeout=6.0):
    """推进若干会话直到条件成立（真实游戏里由主循环每帧 poll）。"""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for session in sessions:
            session.poll()
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


# ==================================================
# 协议 framing
# ==================================================

class ProtocolTests(unittest.TestCase):
    def test_round_trip(self):
        message = make_message(MessageType.SET_READY, message_id=7, ready=True)
        decoded = decode_message(encode_message(message)[4:])
        self.assertEqual(message_type(decoded), MessageType.SET_READY)
        self.assertEqual(decoded["payload"]["ready"], True)
        self.assertEqual(decoded["v"], PROTOCOL_VERSION)

    def test_frame_is_length_prefixed(self):
        frame = encode_message(make_message(MessageType.PING))
        length = int.from_bytes(frame[:4], "big")
        self.assertEqual(length, len(frame) - 4)

    def test_sticky_packets_are_split(self):
        """一次 recv 拿到三条消息也要能切成三条。"""

        decoder = FrameDecoder()
        chunk = b"".join(
            encode_message(make_message(MessageType.PING)) for _ in range(3))
        decoder.feed(chunk)
        self.assertEqual(len(decoder), 3)

    def test_split_packets_are_buffered(self):
        """一个消息被拆成多次 recv：没凑齐之前不应产出半条。"""

        decoder = FrameDecoder()
        frame = encode_message(make_message(MessageType.LOBBY_STATE, lobby={}))
        for byte in frame[:-1]:
            decoder.feed(bytes([byte]))
            self.assertEqual(len(decoder), 0)
        decoder.feed(frame[-1:])
        self.assertEqual(len(decoder), 1)

    def test_oversized_frame_is_a_protocol_error(self):
        decoder = FrameDecoder(max_frame_bytes=64)
        with self.assertRaises(ProtocolError):
            decoder.feed((999999).to_bytes(4, "big") + b"{}")

    def test_broken_frame_is_dropped_without_killing_the_stream(self):
        """单帧 JSON 坏掉只丢那一帧，后面的消息照常解析。"""

        decoder = FrameDecoder()
        decoder.feed(b"\x00\x00\x00\x03abc")          # 非 JSON
        decoder.feed(encode_message(make_message(MessageType.PING)))
        self.assertEqual(decoder.dropped_frames, 1)
        self.assertEqual(message_type(decoder.pop()), MessageType.PING)


class ParseInputTests(unittest.TestCase):
    def test_port_rules(self):
        self.assertEqual(parse_port("9527"), (9527, ""))
        self.assertEqual(parse_port("")[0], 9527)
        self.assertTrue(parse_port("abc")[1])
        self.assertTrue(parse_port("99999")[1])

    def test_host_address_accepts_ip_and_ip_port(self):
        self.assertEqual(parse_host_address("192.168.1.23"), ("192.168.1.23", 9527, ""))
        self.assertEqual(parse_host_address("192.168.1.23:9600"),
                         ("192.168.1.23", 9600, ""))
        self.assertTrue(parse_host_address("")[2])

    def test_nickname_is_cleaned_and_limited(self):
        self.assertEqual(clean_nickname("  小明  "), "小明")
        self.assertEqual(clean_nickname(""), "玩家")
        self.assertEqual(len(clean_nickname("很长很长很长很长很长很长")), 12)


# ==================================================
# 大厅权威规则（纯数据）
# ==================================================

class LobbyStateTests(unittest.TestCase):
    def make_lobby(self, max_players=8):
        lobby = LobbyState(host_player_id="host", max_players=max_players)
        lobby.add_player("房主", player_id="host", is_host=True)
        return lobby

    def test_host_sits_at_seat_zero_and_later_players_follow(self):
        lobby = self.make_lobby()
        first = lobby.add_player("甲")
        second = lobby.add_player("乙")
        self.assertEqual(lobby.player("host").seat, 0)
        self.assertEqual([first.seat, second.seat], [1, 2])

    def test_removed_seat_is_reused(self):
        lobby = self.make_lobby()
        first = lobby.add_player("甲")
        lobby.add_player("乙")
        lobby.remove_player(first.player_id)
        again = lobby.add_player("丙")
        self.assertEqual(again.seat, first.seat)

    def test_room_full_refuses_new_players(self):
        lobby = self.make_lobby(max_players=2)
        self.assertIsNotNone(lobby.add_player("甲"))
        self.assertIsNone(lobby.add_player("乙"))
        self.assertTrue(lobby.full)

    def test_start_requires_two_players_and_ready_guests(self):
        lobby = self.make_lobby(max_players=4)
        self.assertIn("至少需要 2 名玩家", lobby.start_blocker())

        guest = lobby.add_player("甲")
        self.assertIn("甲", lobby.start_blocker())
        self.assertFalse(lobby.can_start())

        lobby.set_ready(guest.player_id, True)
        self.assertEqual(lobby.start_blocker(), "")
        self.assertTrue(lobby.can_start())
        self.assertEqual(guest.status_label, "已准备")

    def test_host_does_not_need_to_be_ready(self):
        lobby = self.make_lobby(max_players=2)
        lobby.add_player("甲")
        lobby.set_ready("host", True)
        self.assertEqual(lobby.player("host").status_label, "房主")
        self.assertIn("甲", lobby.start_blocker())

    def test_started_lobby_blocks_further_starts(self):
        lobby = self.make_lobby(max_players=2)
        lobby.add_player("甲")
        lobby.players[1].ready = True
        lobby.started = True
        self.assertIn("已经开始", lobby.start_blocker())

    def test_serialisation_round_trip(self):
        lobby = self.make_lobby(max_players=5)
        guest = lobby.add_player("甲", address="10.0.0.9:1234")
        lobby.set_ready(guest.player_id, True)
        restored = LobbyState.from_dict(lobby.to_dict())
        self.assertEqual(restored.max_players, 5)
        self.assertEqual(restored.room_id, lobby.room_id)
        self.assertEqual([player.player_id for player in restored.ordered()],
                         [player.player_id for player in lobby.ordered()])
        self.assertTrue(restored.player(guest.player_id).ready)


# ==================================================
# 真实 socket：房主 ↔ 客户端
# ==================================================

class LanSessionTestCase(unittest.TestCase):
    def setUp(self):
        self.sessions = []
        self.host = self.start_host()
        self.port = self.host.host.bound_port

    def tearDown(self):
        for session in self.sessions:
            session.leave()
        for session in self.sessions:
            session.poll()
        pygame.display.quit()

    def start_host(self, nickname="房主", max_players=4):
        session = LanSession()
        ok, message = session.create_room(nickname, max_players, 0)
        self.assertTrue(ok, message)
        # Phase 11.2 起「开始游戏」会真的建立权威对局，房主必须提供本地 Game。
        session.set_game(Game(ai_count=1))
        self.sessions.append(session)
        return session

    def join(self, nickname="玩家", nickname_alias=None, port=None):
        session = LanSession()
        ok, message = session.join_room(
            nickname_alias or nickname, "127.0.0.1", port or self.port)
        self.assertTrue(ok, message)
        self.sessions.append(session)
        return session

    def wait(self, predicate, timeout=6.0):
        return pump(self.sessions, predicate, timeout)

    def roster(self, session):
        lobby = session.lobby
        return [] if lobby is None else [
            (player.seat, player.nickname, player.ready) for player in lobby.ordered()]

    # ---- 加入 ----

    def test_join_handshakes_and_both_sides_agree(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        self.assertEqual(self.roster(self.host), [(0, "房主", False), (1, "小明", False)])
        self.assertEqual(self.roster(guest), self.roster(self.host))
        self.assertEqual(guest.local_player_id,
                         self.host.lobby.ordered()[1].player_id)

    def test_guest_ready_request_is_applied_by_the_host_and_broadcast(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))

        guest.set_ready(True)
        self.assertTrue(self.wait(lambda: self.roster(guest)[1][2] is True))
        self.assertTrue(self.host.lobby.player(guest.local_player_id).ready)

        guest.set_ready(False)
        self.assertTrue(self.wait(lambda: self.roster(self.host)[1][2] is False))

    def test_rapid_ready_toggles_end_in_the_last_requested_state(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        for value in (True, False, True, False, True):
            guest.set_ready(value)
        self.assertTrue(self.wait(lambda: self.roster(self.host)[1][2] is True))
        self.assertTrue(self.wait(lambda: self.roster(guest)[1][2] is True))

    def test_room_full_is_rejected_with_a_chinese_reason(self):
        self.host.set_max_players(2)
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))

        extra = self.join("小刚")
        self.assertTrue(self.wait(lambda: bool(extra.error)))
        self.assertEqual(extra.error, "房间已满")
        self.assertEqual(len(self.host.lobby.players), 2)
        self.assertFalse(extra.connected)

    def test_already_started_is_rejected(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        guest.set_ready(True)
        self.assertTrue(self.wait(lambda: self.host.lobby.can_start()))
        ok, message = self.host.start_match()
        self.assertTrue(ok, message)
        self.assertTrue(self.wait(lambda: guest.started))

        late = self.join("小刚")
        self.assertTrue(self.wait(lambda: bool(late.error)))
        self.assertIn("已经开始", late.error)

    def test_start_requires_ready_guests(self):
        self.join("小明")
        self.assertTrue(self.wait(lambda: len(self.host.lobby.players) == 2))
        ok, reason = self.host.start_match()
        self.assertFalse(ok)
        self.assertIn("准备", reason)

    # ---- 通知 ----

    def test_start_game_reaches_every_client(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        guest.set_ready(True)
        self.assertTrue(self.wait(lambda: self.host.lobby.can_start()))
        self.host.start_match()
        self.assertTrue(self.wait(lambda: guest.lobby.started))
        # 联机的默认模式是标准身份局：联机不是一种新模式，它只是"身份局里
        # 某些座位由远程真人控制"。START_GAME 广播的就是大厅里那个模式。
        self.assertEqual(guest.client.start_payload.get("mode"), "identity")
        self.assertEqual(guest.client.start_payload.get("player_count"), 2)

    # ---- 离开 / 掉线 ----

    def test_client_leave_removes_the_player_from_the_host_lobby(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        guest.leave()
        self.assertTrue(self.wait(lambda: len(self.host.lobby.players) == 1))
        self.assertEqual(self.host.lobby.ordered()[0].nickname, "房主")

    def test_socket_drop_is_detected_as_a_disconnect(self):
        """直接掐断 socket（模拟拔网线 / 杀进程）也要被房主清理掉。"""

        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        guest.client.conn.sock.close()          # 硬断，不发 DISCONNECT
        self.assertTrue(self.wait(lambda: len(self.host.lobby.players) == 1))

    def test_host_close_tells_every_client(self):
        guest = self.join("小明")
        self.assertTrue(self.wait(lambda: guest.connected))
        self.host.leave()
        self.assertTrue(self.wait(lambda: bool(guest.error)))
        self.assertIn("房主", guest.error)

    def test_host_can_be_created_again_after_leaving(self):
        self.host.leave()
        again = LanSession()
        self.sessions.append(again)
        ok, message = again.create_room("新房间", 4, 0)
        self.assertTrue(ok, message)
        self.assertGreater(again.host.bound_port, 0)

    def test_port_conflict_is_reported_in_chinese(self):
        second = LanSession()
        self.sessions.append(second)
        ok, message = second.create_room("抢端口", 4, self.port)
        self.assertFalse(ok)
        self.assertIn("占用", message)

    def test_version_mismatch_is_rejected(self):
        """老版本客户端握手时房主要明确拒绝，而不是收下再错乱。"""

        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        try:
            raw = {"v": PROTOCOL_VERSION + 99, "type": MessageType.HELLO,
                   "id": 0, "payload": {"nickname": "旧版本"}}
            sock.sendall(encode_message(raw))
            sock.settimeout(0.2)
            decoder = FrameDecoder()
            deadline = time.monotonic() + 4
            reply = None
            while time.monotonic() < deadline and reply is None:
                self.host.poll()
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                decoder.feed(chunk)
                reply = decoder.pop()
            self.assertIsNotNone(reply, "房主没有回应版本不符的握手")
            self.assertEqual(message_type(reply), MessageType.ERROR)
            self.assertEqual(reply["payload"]["code"], "version_mismatch")
        finally:
            sock.close()
        self.assertEqual(len(self.host.lobby.players), 1)

    def test_garbage_bytes_do_not_kill_the_room(self):
        """单个客户端发垃圾数据：只断它一个，房间照常。"""

        good = self.join("小明")
        self.assertTrue(self.wait(lambda: good.connected))

        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        sock.sendall((10 ** 6).to_bytes(4, "big") + b"junk")
        self.assertTrue(self.wait(lambda: len(self.host.host.connections) == 1, 4.0))
        sock.close()

        self.assertEqual(len(self.host.lobby.players), 2)
        self.assertTrue(self.host.poll() is not None)
        self.assertTrue(good.connected)


# ==================================================
# 界面外壳
# ==================================================

class LanUiTests(unittest.TestCase):
    def setUp(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1280, 720))
        self.renderer = Renderer(self.screen)
        self.game = Game(ai_count=2)
        self.menu = StartMenu(self.screen)
        self.menu.sync_layout(self.renderer.metrics)
        self.menu.sync_modes(self.game.modes.list_modes())
        self.menu.sync_layout(self.renderer.metrics)
        self.lan = LanScene(self.screen)
        self.lan.sync_layout(self.renderer.metrics)

    def tearDown(self):
        self.lan.close_session()
        pygame.display.quit()

    def test_start_menu_has_a_multiplayer_entry(self):
        action = self.menu.handle_click(self.menu.multiplayer_button.rect.center, self.game)
        self.assertEqual(action, "multiplayer")
        self.assertIn(self.menu.multiplayer_button, self.menu.buttons())

    def test_multiplayer_menu_is_inside_the_panel_at_every_resolution(self):
        from src.ui import layout

        for size in ((1280, 720), (1366, 768), (1600, 900), (1920, 1080), (2560, 1440)):
            metrics = layout.LayoutMetrics(*size)
            screen = pygame.display.set_mode(size)
            view = MultiplayerMenuScreen(screen)
            view.sync_layout(metrics)
            with self.subTest(size=size):
                self.assertTrue(
                    pygame.Rect(0, 0, *size).contains(view.panel_rect), str(size))
                for rect in (view.nickname_field.rect, view.ip_field.rect,
                             view.port_field.rect, view.create_button.rect,
                             view.join_button.rect, view.back_button.rect):
                    self.assertTrue(view.panel_rect.contains(rect), str(size))
                view.draw(LanSession(), metrics, self.game)

    def test_lobby_rows_stay_inside_the_panel(self):
        from src.ui import layout

        for size in ((1280, 720), (1600, 900), (1920, 1080)):
            metrics = layout.LayoutMetrics(*size)
            screen = pygame.display.set_mode(size)
            view = LobbyScreen(screen)
            view.sync_layout(metrics)
            with self.subTest(size=size):
                self.assertTrue(
                    pygame.Rect(0, 0, *size).contains(view.panel_rect), str(size))
                for rect in view.row_rects:
                    self.assertTrue(view.panel_rect.contains(rect), str(size))
                    self.assertLess(rect.bottom, view.ready_button.rect.top)
                self.assertTrue(view.panel_rect.contains(view.leave_button.rect))

    def test_creating_a_room_switches_to_the_lobby_scene(self):
        self.lan.enter(self.game)
        self.assertEqual(self.game.scene, "multiplayer_menu")

        self.lan.menu.nickname_field.set_text("房主")
        self.lan.menu.port_field.set_text("0")
        self.lan.menu.create_room(self.lan.session, self.game)
        self.lan.update(1 / 60.0, self.game)
        self.assertEqual(self.game.scene, "lobby")
        self.assertEqual(self.lan.session.lobby.count, 1)

        self.lan.draw(self.game, self.renderer.metrics)
        self.lan.close_session()

    def test_back_button_returns_to_the_main_menu_and_closes_the_session(self):
        self.lan.enter(self.game)
        action = self.lan.handle_event(
            pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1,
                               pos=self.lan.menu.back_button.rect.center),
            self.game)
        self.assertEqual(action, "back")
        self.assertFalse(self.lan.session.active)
        self.assertTrue(self.lan.take_exit_notice())

    def test_join_failure_keeps_the_player_on_the_retryable_menu(self):
        self.lan.enter(self.game)
        self.lan.menu.nickname_field.set_text("玩家")
        self.lan.menu.ip_field.set_text("127.0.0.1")
        self.lan.menu.port_field.set_text("1")          # 无人监听
        self.lan.menu.join_room(self.lan.session, self.game)

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and "正在连接" in self.lan.menu.status:
            self.lan.update(1 / 60.0, self.game)
            time.sleep(0.005)
        self.assertEqual(self.game.scene, "multiplayer_menu")
        self.assertTrue(self.lan.menu.status)
        self.assertNotIn("正在连接", self.lan.menu.status)
        self.assertFalse(self.lan.session.active)

    def test_typing_in_the_menu_does_not_leak_to_global_shortcuts(self):
        """在昵称里打 "1"：必须被输入框吃掉，不能顺带改对局速度。"""

        self.lan.enter(self.game)
        menu = self.lan.menu
        menu.nickname_field.focus()
        consumed = menu.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1, mod=0), self.lan.session, self.game)
        self.assertEqual(consumed, "handled")

        menu.nickname_field.blur()
        menu.ip_field.focus()
        menu.ip_field.set_text("")
        menu.handle_event(
            pygame.event.Event(pygame.TEXTINPUT, text="1"), self.lan.session, self.game)
        menu.handle_event(
            pygame.event.Event(pygame.TEXTINPUT, text="9"), self.lan.session, self.game)
        menu.handle_event(
            pygame.event.Event(pygame.TEXTINPUT, text="2"), self.lan.session, self.game)
        self.assertEqual(menu.ip_field.value(), "192")

        # F11 例外：任何界面都要能切全屏。
        self.assertIsNone(menu.handle_event(
            pygame.event.Event(pygame.KEYDOWN, key=pygame.K_F11, mod=0),
            self.lan.session, self.game))

    def test_text_field_types_limits_and_filters(self):
        field = TextField(label="端口", max_length=5, accepts=accepts_digits_only)
        field.focus()
        for char in "95a27x":
            field.handle_event(pygame.event.Event(pygame.TEXTINPUT, text=char))
        self.assertEqual(field.text, "9527")

        for _ in range(10):
            field.handle_event(pygame.event.Event(pygame.TEXTINPUT, text="8"))
        self.assertEqual(len(field.text), 5)

        field.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_BACKSPACE))
        self.assertEqual(len(field.text), 4)
        field.blur()
        self.assertFalse(field.focused)


if __name__ == "__main__":
    unittest.main()
