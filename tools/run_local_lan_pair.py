"""本机一键双开：两个**真实 main.py 进程**通过 127.0.0.1 连成同一局。

联机的验收一直卡在"还得再找一台电脑"：单进程里开两个会话（``tools/lan_smoke.py``）
证明不了主循环与 socket 线程真的能一起跑，而真机联调又太贵。这里把两件事
合到一起——两个完整的 ``main.py``，各自的窗口、各自的网络线程、各自的
socket，全部走 127.0.0.1。

    python tools/run_local_lan_pair.py                 # 两个真实窗口，手动玩
    python tools/run_local_lan_pair.py --capture       # 自动走完大厅并截图
    python tools/run_local_lan_pair.py --port 9600 --host-name 房主 --client-name 小明

``--capture`` 会加载 ``tools/runtime_capture.py``（真实主循环上的运行期脚本），
在**两个进程内部**完成"创建房间 / 加入房间 / 开始游戏 / 截屏"，
产物是 ``tools/ui_snapshots/lan_identity_runtime_host.png`` 与
``..._client.png`` —— 那是真实的身份局牌桌，不是 fake view。

测试级别：本工具产出的是 **LOCALHOST MULTI-PROCESS**（同机多进程 + 回环地址）。
它不是 PHYSICAL TWO-PC，报告里不许混用这两个说法。
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS = os.path.join(ROOT, "tools", "ui_snapshots")
DEFAULT_PORT = 9527
#: 客户端启动前等房主真正监听的时限（游戏进程启动要几秒）。
READY_TIMEOUT = 60.0


def child_env(**extra):
    env = dict(os.environ)
    for key, value in extra.items():
        if value is not None:
            env[key] = str(value)
    return env


def spawn(name, args, env):
    print("[双开] 启动 %s：python %s" % (name, " ".join(args)))
    return subprocess.Popen(
        [sys.executable] + args, cwd=ROOT, env=env,
        stdout=None if env.get("SGS_LIVE_OUTPUT") else subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def wait_for_port(port, timeout=READY_TIMEOUT):
    """等房主进程真的在监听（而不是盲等固定秒数）。"""

    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", int(port))) == 0:
                return True
        time.sleep(0.3)
    return False


def dump(process, name):
    """把子进程输出打出来（capture 模式下它们不直接连终端）。"""

    if process.stdout is None:
        return
    output = process.stdout.read()
    if not output:
        return
    print("\n---- %s 进程输出 ----" % name)
    print(output.decode("utf-8", "replace").rstrip())


def run_manual(args):
    """手动模式：两个真实窗口，玩家自己点。"""

    print("[双开] 手动模式：两个真实 Pygame 窗口，房主先建好房间，客户端自动加入")
    print("[双开] 提示：本机 IP 可以给别的电脑用；本机验证走 127.0.0.1")
    host_env = child_env(SGS_LIVE_OUTPUT="1")
    host = spawn("房主", ["main.py", "--host", "--port", str(args.port),
                          "--name", args.host_name], host_env)
    if not wait_for_port(args.port):
        print("[双开] 房主端口 %d 未就绪，放弃启动客户端" % args.port)
        host.terminate()
        return 1

    client_env = child_env(SGS_LIVE_OUTPUT="1")
    client = spawn("客户端", ["main.py", "--join", "127.0.0.1",
                              "--port", str(args.port),
                              "--name", args.client_name], client_env)
    print("[双开] 两个进程都已启动：关掉任一窗口即可退出")
    try:
        while host.poll() is None and client.poll() is None:
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n[双开] 收到 Ctrl+C，正在收尾")
    for process in (host, client):
        if process.poll() is None:
            process.terminate()
    for process in (host, client):
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:                  # pragma: no cover
            process.kill()
    return 0


def run_capture(args):
    """截图模式：两个进程各自跑运行期脚本，自动走完大厅与开局。"""

    os.makedirs(SNAPSHOTS, exist_ok=True)
    logs = os.path.join(SNAPSHOTS, "runtime_logs")
    os.makedirs(logs, exist_ok=True)
    host_png = os.path.join(SNAPSHOTS, "lan_identity_runtime_host.png")
    client_png = os.path.join(SNAPSHOTS, "lan_identity_runtime_client.png")
    host_json = os.path.join(logs, "lan_identity_runtime_host.json")
    client_json = os.path.join(logs, "lan_identity_runtime_client.json")
    for path in (host_png, client_png):
        if os.path.exists(path):
            os.remove(path)

    common = {
        "SDL_VIDEODRIVER": "dummy",
        "SDL_AUDIODRIVER": "dummy",
        "SGS_RUNTIME_SCRIPT": "runtime_capture",
        "SGS_CAPTURE_SIZE": args.size,
        # 这里的"人数"是**真人**数：身份局总人数由模式决定（5 人），不足的
        # 座位由房主补 AI。所以 2 个真人进房就能开局，不必等满 5 个人。
        "SGS_CAPTURE_PLAYERS": args.humans,
        "SGS_CAPTURE_PORT": args.port,
    }
    host = spawn("房主", ["main.py"], child_env(
        **common, SGS_CAPTURE_ROLE="host", SGS_CAPTURE_OUT=host_png,
        SGS_CAPTURE_JSON=host_json,
        # 房主截完自己那一帧后，把回合轮流交给每个远程座位——客户端才有机
        # 会截到"轮到我出牌"的画面的。linger 要长于 交接次数 × 交接间隔
        # （9 × 6 秒），否则房主先退出会把还没截到图的客户端踢回大厅。
        SGS_CAPTURE_HANDOVER="all", SGS_CAPTURE_LINGER=90,
        SGS_CAPTURE_TIMEOUT=280, SGS_CAPTURE_JOIN_WAIT=120))
    if not wait_for_port(args.port):
        dump(host, "房主")
        print("[双开] 房主端口 %d 未就绪" % args.port)
        host.terminate()
        return 1
    client = spawn("客户端", ["main.py"], child_env(
        **common, SGS_CAPTURE_ROLE="client", SGS_CAPTURE_NAME=args.client_name,
        SGS_CAPTURE_OUT=client_png, SGS_CAPTURE_JSON=client_json,
        SGS_CAPTURE_LINGER=45, SGS_CAPTURE_TIMEOUT=300))

    codes = {}
    for name, process in (("房主", host), ("客户端", client)):
        try:
            codes[name] = process.wait(timeout=260)
        except subprocess.TimeoutExpired:                  # pragma: no cover
            process.kill()
            codes[name] = -1
        dump(process, name)

    print("\n---- 双开截图 ----")
    ok = True
    for path in (host_png, client_png):
        exists = os.path.exists(path)
        ok = ok and exists
        print("  [%s] %s" % ("PASS" if exists else "FAIL", path))
    for path in (host_json, client_json):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):                      # pragma: no cover
            continue
        notes = data.get("notes") or []
        print("  [%s] %s tag=%s" % (
            "PASS" if notes else "INFO", os.path.basename(path), data.get("tag", "")))
        for line in notes[-6:]:
            print("        " + str(line))
    print("[双开] 退出码：%s" % codes)
    print("[双开] 级别：LOCALHOST MULTI-PROCESS（不是 PHYSICAL TWO-PC）")
    return 0 if ok else 1


def pick_free_port(preferred):
    """优先用指定端口；被占用（例如上次没退干净的进程）就换一个空闲的。"""

    import socket

    def usable(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("0.0.0.0", int(port)))
            except OSError:
                return False
        return True

    if usable(preferred):
        return int(preferred)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("0.0.0.0", 0))
        port = int(probe.getsockname()[1])
    print("[双开] 端口 %d 被占用，改用空闲端口 %d" % (preferred, port))
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description="本机双开两个真实 main.py")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host-name", default="房主")
    parser.add_argument("--client-name", default="远程玩家")
    parser.add_argument("--players", type=int, default=5,
                        help="身份局总人数（房间模式决定；2 名真人即开局，其余补 AI）")
    parser.add_argument("--humans", type=int, default=2,
                        help="截图模式里有几个真人（房主 + 客户端）")
    parser.add_argument("--capture", action="store_true",
                        help="自动走完大厅与开局并截图（开发验收）")
    parser.add_argument("--size", default="1600x1000")
    args = parser.parse_args(argv)
    args.port = pick_free_port(args.port)
    return run_capture(args) if args.capture else run_manual(args)


if __name__ == "__main__":
    sys.exit(main())
