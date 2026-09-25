"""Phase 11.4.2 真实验收 runner：单机 / 局域网的**真实 main.py** 截图与几何。

三个角色都跑**玩家真正会用的那个 ``main.py``**：

* ``local``：单机身份局（1 真人 + N-1 AI）；
* ``host``：``main.py`` 创建房间并作为房主开局；
* ``client``：``main.py`` 加入房间，作为远程玩家进入牌桌。

脚本自己只做编排（起进程、传环境变量、收结果）；所有操作与截图都发生在
被启动的进程内部（``tools/runtime_capture.py``）。

测试级别（必须按级别描述结果，不许混用）：

* 本脚本产出的默认结果 = **LOCALHOST MULTI-PROCESS**（同一台机器上的多个
  真实进程，走 127.0.0.1 loopback）。
* 它不是 PHYSICAL TWO-PC（两台物理电脑）的结果。只有真的在两台机器上运行
  过，才能写 ``PHYSICAL TWO-PC PASS``。

    python tools/runtime_parity_run.py --players 5
    python tools/runtime_parity_run.py --role local
"""

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAPSHOTS = os.path.join(ROOT, "tools", "ui_snapshots")
LOGS = os.path.join(ROOT, "tools", "ui_snapshots", "runtime_logs")

DEFAULT_PORT = 19640
RESULT_KEYS = ("runtime_parity_local_identity", "runtime_parity_lan_host",
               "runtime_parity_lan_client")


SIZE = "1920x1080"


def _set_size(value):
    """验收分辨率：三个进程必须完全一致，否则几何对比没有意义。"""

    global SIZE
    SIZE = str(value)
    return SIZE


def child_env(role, **extra):
    env = dict(os.environ)
    env["SDL_VIDEODRIVER"] = "dummy"
    env["SDL_AUDIODRIVER"] = "dummy"
    env["SGS_RUNTIME_SCRIPT"] = "runtime_capture"
    env["SGS_CAPTURE_ROLE"] = role
    env["SGS_CAPTURE_SIZE"] = extra.pop("SGS_CAPTURE_SIZE", SIZE)
    for key, value in extra.items():
        if value is None:
            continue
        env[key] = str(value)
    return env


def spawn(role, name, env, log_name):
    os.makedirs(LOGS, exist_ok=True)
    handle = open(os.path.join(LOGS, log_name), "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "main.py"], cwd=ROOT, env=env,
        stdout=handle, stderr=subprocess.STDOUT)
    process._log_handle = handle                       # noqa: SLF001 - 保留句柄
    process._label = name                              # noqa: SLF001
    process._log_name = log_name                       # noqa: SLF001
    return process


def wait_all(processes, timeout):
    deadline = time.monotonic() + timeout
    for process in processes:
        remaining = max(1.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    for process in processes:
        handle = getattr(process, "_log_handle", None)
        if handle is not None:
            handle.close()


def tail(path, lines=6):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read().strip().splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def run_local(players):
    out = os.path.join(SNAPSHOTS, "runtime_parity_local_identity.png")
    meta = os.path.join(SNAPSHOTS, "runtime_parity_local_identity.json")
    env = child_env("local",
                    SGS_CAPTURE_OUT=out, SGS_CAPTURE_JSON=meta,
                    SGS_CAPTURE_PLAYERS=players, SGS_CAPTURE_LINGER=1,
                    SGS_CAPTURE_TIMEOUT=120)
    process = spawn("local", "单机", env, "local.log")
    wait_all([process], 150)
    return {"label": "LOCAL（单进程，真实 main.py）", "png": out, "json": meta}


def run_lan(players, port):
    os.makedirs(SNAPSHOTS, exist_ok=True)
    host_png = os.path.join(SNAPSHOTS, "runtime_parity_lan_host.png")
    host_json = os.path.join(SNAPSHOTS, "runtime_parity_lan_host.json")
    client_png = os.path.join(SNAPSHOTS, "runtime_parity_lan_client.png")
    client_json = os.path.join(SNAPSHOTS, "runtime_parity_lan_client.json")

    processes = [spawn("host", "房主", child_env(
        "host",
        SGS_CAPTURE_PORT=port, SGS_CAPTURE_PLAYERS=players,
        SGS_CAPTURE_OUT=host_png, SGS_CAPTURE_JSON=host_json,
        SGS_CAPTURE_HANDOVER="all",
        SGS_CAPTURE_LINGER=45, SGS_CAPTURE_TIMEOUT=260,
        SGS_CAPTURE_JOIN_WAIT=180,
    ), "host.log")]

    # 其余座位：每个都是一台"另一台电脑"，这里用同机的真实进程代替。
    for index in range(1, players):
        first = index == 1
        processes.append(spawn(
            "client", "客户端" + str(index), child_env(
                "client",
                SGS_CAPTURE_PORT=port,
                SGS_CAPTURE_NAME="远程玩家%d" % index,
                SGS_CAPTURE_OUT=(client_png if first else
                                 os.path.join(LOGS, "client%d.png" % index)),
                # 每个客户端都写一份诊断 JSON（不截图的只写 JSON），
                # 否则"某个座位为什么没进对局"在事后完全看不出来。
                SGS_CAPTURE_JSON=(client_json if first else
                                  os.path.join(LOGS, "client%d.json" % index)),
                SGS_CAPTURE_LINGER=45 if first else 6,
                # 不截图的客户端只是"占位真人"：必须活到别人截完图。
                SGS_CAPTURE_TIMEOUT=300,
            ), "client%d.log" % index))
        time.sleep(1.0)

    wait_all(processes, 260)
    return {"label": "LOCALHOST MULTI-PROCESS（同一台机器 %d 个真实进程 / loopback）"
                     % len(processes),
            "png": host_png, "json": host_json,
            "client_png": client_png, "client_json": client_json,
            "logs": [item._log_name for item in processes]}       # noqa: SLF001


def compare(local, host, client):
    """三份几何里"应当完全一致"的部分：把它们摆在一起看差异。"""

    rows = []
    if not all(isinstance(item, dict) for item in (local, host, client)):
        return rows, {}
    keys = ("scale", "screen")
    for key in keys:
        values = {name: data.get(key) for name, data in
                  (("local", local), ("host", host), ("client", client))}
        rows.append((key, values, len({json.dumps(value, sort_keys=True)
                                       for value in values.values()}) == 1))

    regions = ("central", "prompt", "player_status", "hand_area", "log",
               "action_card")
    for key in regions:
        values = {name: data["regions"].get(key) for name, data in
                  (("local", local), ("host", host), ("client", client))
                  if "regions" in data}
        rows.append(("regions." + key, values,
                     len({json.dumps(value) for value in values.values()}) == 1))

    for key in ("primary", "secondary"):
        values = {name: data["buttons"].get(key) for name, data in
                  (("local", local), ("host", host), ("client", client))
                  if "buttons" in data}
        rows.append(("buttons." + key, values,
                     len({json.dumps(value, sort_keys=True)
                          for value in values.values()}) == 1))

    seat_rows = {}
    for name, data in (("local", local), ("host", host), ("client", client)):
        seats = data.get("seats") if "seats" in data else None
        if seats:
            seat_rows[name] = {item["offset"]: (item["side"], tuple(item["rect"]))
                               for item in seats}
    # 座位几何：三个模式在"相同 offset"上必须落到同一个 rect。
    seats_equal = bool(seat_rows) and len({
        json.dumps({str(key): value for key, value in sorted(items.items())},
                   sort_keys=True) for items in seat_rows.values()
    }) == 1
    rows.append(("seats(offset→rect)", seat_rows, seats_equal))
    return rows, seat_rows


def main():
    parser = argparse.ArgumentParser(description="Phase 11.4.2 UI 真实验收")
    parser.add_argument("--players", type=int, default=5)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--role", choices=("all", "local", "lan"), default="all")
    parser.add_argument("--size", default="")
    args = parser.parse_args()
    if args.size:
        _set_size(args.size)

    print("=" * 68)
    print("Phase 11.4.2 真实验收（真实 main.py 路径）")
    print("测试级别：LOCALHOST MULTI-PROCESS（同一台机器）。"
          "PHYSICAL TWO-PC 未在本机验证。")
    print("=" * 68)

    if args.role in ("all", "local"):
        info = run_local(args.players)
        print("\n[LOCAL] %s" % info["label"])
        print("  截图：%s" % os.path.relpath(info["png"], ROOT))
        data = load_json(info["json"])
        print("  几何：%s" % ("正常" if data and "regions" in data
                              else "失败 → " + str((data or {}).get("notes"))))
    if args.role in ("all", "lan"):
        info = run_lan(args.players, args.port)
        print("\n[LAN] %s" % info["label"])
        print("  房主截图：%s" % os.path.relpath(info["png"], ROOT))
        print("  客户端截图：%s" % os.path.relpath(info["client_png"], ROOT))
        for name in info["logs"]:
            tail_text = tail(os.path.join(LOGS, name), 4)
            if tail_text:
                print("  --- %s ---\n%s" % (name, tail_text))

    local = load_json(os.path.join(SNAPSHOTS, "runtime_parity_local_identity.json"))
    host = load_json(os.path.join(SNAPSHOTS, "runtime_parity_lan_host.json"))
    client = load_json(os.path.join(SNAPSHOTS, "runtime_parity_lan_client.json"))
    rows, _seats = compare(local or {}, host or {}, client or {})
    if rows:
        print("\n几何对照（local / host / client 是否相同）")
        for key, values, same in rows:
            print("  [%s] %s" % ("一致" if same else "不同", key))
            if not same:
                for name, value in values.items():
                    print("      %-7s %s" % (name, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
