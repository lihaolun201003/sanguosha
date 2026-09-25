"""跨进程验证：真实的 ``main.py`` 当房主，另一个进程接入。

与 ``tools/lan_smoke.py``（同进程双实例）互补：这里跑的是**真的独立进程**，
走的是 ``python main.py --host`` 的正式启动路径，因此能验证：

* main.py 的事件循环 / 每帧 poll 真的在消费网络事件；
* 房主进程里的权威大厅会正确广播给外部客户端；
* 房主进程被杀掉时，客户端能立刻感知（不会卡死）。

    python tools/lan_two_process.py
"""

import os
import subprocess
import sys
import time

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.network.session import LanSession  # noqa: E402

PORT = 19627
STARTUP_TIMEOUT = 40.0


def launch_host():
    """按玩家真实会用的方式启动游戏：python main.py --host …"""

    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    return subprocess.Popen(
        [sys.executable, "main.py", "--host",
         "--port", str(PORT), "--name", "房主进程", "--max", "2"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def pump(sessions, predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for session in sessions:
            session.poll()
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def connect(nickname, timeout=STARTUP_TIMEOUT):
    """反复重试连接（游戏进程启动要几秒），成功后返回会话。"""

    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        session = LanSession()
        ok, message = session.join_room(nickname, "127.0.0.1", PORT)
        if not ok:
            last = message
        elif pump([session], lambda: session.connected or bool(session.error), 6.0):
            if session.connected:
                return session
            last = session.error
        session.leave()
        time.sleep(0.5)
    raise AssertionError("无法连上游戏进程：" + (last or "超时"))


_results = []


def check(name, condition, detail=""):
    _results.append((name, bool(condition), detail))
    print("  [%s] %s%s" % ("PASS" if condition else "FAIL", name,
                           ("  — " + detail) if detail else ""))
    return bool(condition)


def launch_join(port):
    """按玩家真实会用的方式加入：python main.py --join 192.168.x.x"""

    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    return subprocess.Popen(
        [sys.executable, "main.py", "--join", "127.0.0.1:" + str(port),
         "--name", "客户端进程"],
        cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def part_b_game_joins_us():
    """反向：本脚本当房主，让真实的 main.py 以客户端身份连进来。"""

    print("\n[反向] 本脚本开房 ← main.py --join")
    host = LanSession()
    ok, message = host.create_room("脚本房主", 4, 0)
    if not ok:
        check("脚本房主创建房间", False, message)
        return
    port = host.host.bound_port
    process = launch_join(port)
    try:
        ok = pump([host], lambda: len(host.lobby.players) == 2, 40.0)
        check("main.py --join 的进程成功加入房间（客户端路径可用）", ok,
              str([(p.nickname, p.seat) for p in host.lobby.ordered()]))
        joiner = next((p for p in host.lobby.players if not p.is_host), None)
        check("房主侧看到客户端昵称", joiner is not None and joiner.nickname == "客户端进程",
              joiner.nickname if joiner else "无")
        process.kill()
        process.wait(timeout=5)
        ok = pump([host], lambda: len(host.lobby.players) == 1, 8.0)
        check("客户端进程退出后房主及时把座位空出来", ok,
              str([p.nickname for p in host.lobby.ordered()]))
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        host.leave()


def main():
    print("=" * 68)
    print("Phase 11.1 跨进程验证：main.py --host  ←  外部客户端")
    print("=" * 68)

    process = launch_host()
    try:
        guest = connect("外部客户端")
        check("外部客户端连上了真实的 main.py 房主进程", guest.connected,
              guest.address_text)
        check("房主进程创建的房间人数上限生效（--max 2）",
              guest.lobby.max_players == 2, "max=%d" % guest.lobby.max_players)
        check("客户端拿到房主昵称", guest.lobby.ordered()[0].nickname == "房主进程",
              str([p.nickname for p in guest.lobby.ordered()]))

        guest.set_ready(True)
        ok = pump([guest], lambda: guest.lobby.player(guest.local_player_id).ready)
        check("房主进程接受了 SET_READY 并广播了权威状态", ok,
              str([(p.nickname, p.ready) for p in guest.lobby.ordered()]))

        extra = LanSession()
        extra.join_room("挤不进的人", "127.0.0.1", PORT)
        ok = pump([extra], lambda: bool(extra.error), 8.0)
        check("第三个人被真实房主进程以「房间已满」拒绝",
              ok and extra.error == "房间已满", extra.error)
        extra.leave()

        guest.leave()                       # 主动离开：房主应当移除他
        time.sleep(0.3)
        check("客户端主动离开后回到干净状态", not guest.active)

        second = connect("离开后再来")
        check("同一个人可以重新加入（复位后重连）", second.connected)

        process.kill()
        process.wait(timeout=5)
        start = time.monotonic()
        ok = pump([second], lambda: bool(second.error), 8.0)
        elapsed = time.monotonic() - start
        check("房主进程被杀掉后客户端立刻感知（不卡死）", ok,
              "%.2fs 后得到「%s」" % (elapsed, second.error))
        second.leave()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    part_b_game_joins_us()

    failed = [item for item in _results if not item[1]]
    print("\n共 %d 项检查，通过 %d 项，失败 %d 项"
          % (len(_results), len(_results) - len(failed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
