# Phase 11.1：内嵌局域网房主 + 多人房间大厅（LAN Host + Lobby）

- 报告日期：2026-09-20
- 基线：Phase 11（950 tests / compileall 全通过）
- 本轮结果：988 tests 全通过；双实例冒烟 42/42；跨进程验证 11/11；`tools/ui_smoke.py` 单机冒烟仍通过
- 工作区：`C:\Users\lihao\Desktop\sanguosha\sanguosha -zcode`

本阶段只做四件事：**TCP 传输 + 线程安全事件队列 + 权威大厅 + Pygame 联机界面**。
没有同步任何对局内容：手牌、出牌、技能、回合、判定、锦囊、伤害、濒死、卡牌移动
一律未联网，`Game` 的规则代码没有为联机改过一行。

---

## 1. 实际修改文件

**新增（网络层，不依赖 pygame）**

| 文件 | 职责 |
| --- | --- |
| `src/network/protocol.py` | 协议版本、消息类型、错误文案、长度前缀编解码、超时/心跳参数集中配置 |
| `src/network/events.py` | `NetworkEvent` / `EventKind`：网络线程 → 主线程的唯一通信形式 |
| `src/network/lobby.py` | `LobbyPlayer` / `LobbyState`：权威大厅数据模型与开局规则 |
| `src/network/transport.py` | `PeerConnection`（读线程 + 写线程 + 心跳应答）、本机 IP 探测 |
| `src/network/host.py` | `HostServer`：accept 线程、权威状态机、广播、心跳与掉线清理 |
| `src/network/client.py` | `LanClient`：异步连接、镜像大厅、致命错误中文翻译 |
| `src/network/session.py` | `LanSession`：主线程门面（房主 / 客户端同形） |
| `src/network/__init__.py` | 对外出口 |

**新增（界面层）**

| 文件 | 职责 |
| --- | --- |
| `src/ui/text_input.py` | `TextField`：TEXTINPUT + KEYDOWN 双通道的一行输入框 |
| `src/ui/multiplayer_menu.py` | 「多人对战」入口屏：昵称 / 最大人数 / 房主 IP / 端口 / 创建 / 加入 |
| `src/ui/lobby.py` | 房间大厅屏：房间信息 + 玩家列表 + 准备 / 开始 / 离开 |
| `src/ui/lan_scene.py` | `LanScene`：两个屏幕 + 会话 + 每帧网络轮询 + 场景切换 |

**新增（工具与测试）**

- `tools/lan_smoke.py`：同进程双实例，真实 TCP + 真实界面代码路径，覆盖 10 个场景
- `tools/lan_two_process.py`：跨进程验证（`main.py --host` / `main.py --join`）
- `tests/test_engine_v2_phase11_lan.py`：38 条确定性用例（framing / 大厅权威 / 加入离开 / 房间满 / 界面外壳）

**修改**

- `main.py`：联机场景的事件路由、每帧轮询、绘制；退出时关闭会话；`--host` / `--join` 启动参数
- `src/start_menu.py`：新增「多人对战（局域网）」一级入口（面板高度 700 → 800，三段式按钮）
- `src/game/core.py`：`show_multiplayer_notice()` → `open_multiplayer_menu()`（真入口，进联机前清零本局）
- `src/player.py`：预留 `ControllerType.REMOTE_HUMAN` 与 `Player.connection_id`
- `src/ui/theme.py`：`LOBBY_STATUS_COLORS` / `lobby_status_color()`（大厅状态色唯一来源）
- `src/ui/__init__.py`：`__all__` 补上四个新模块

单机路径（单人 / 本地多人 / 身份模式 / AI / 技能 / 素材）没有任何改动，
联机只是菜单上多了一个入口。

---

## 2. 网络架构

```
                    房主进程（同时也是玩家）
  ┌──────────────────────────────────────────────────────────┐
  │ Pygame 主线程                                             │
  │   LanScene.update(dt)  ──每帧──▶  LanSession.poll()       │
  │        ▲                                │                │
  │        │ 改界面 / 切场景                 │ 改 LobbyState   │
  │   ┌────┴──────────────┐                 ▼                │
  │   │ MultiplayerMenu   │        ┌──────────────────┐      │
  │   │ LobbyScreen       │        │ HostServer       │      │
  │   └───────────────────┘        │  权威 LobbyState  │      │
  │                                └────────┬─────────┘      │
  │                                         │ send(广播)      │
  │   ┌──────────────── queue.Queue ────────┴──────────┐     │
  │   │ NetworkEvent(ACCEPTED/MESSAGE/DISCONNECTED)     │     │
  │   └──────▲──────────────────────────▲───────────────┘     │
  │          │                          │                    │
  │   accept 线程            每条连接：读线程 + 写线程         │
  └──────────┼──────────────────────────┼────────────────────┘
             │ TCP 0.0.0.0:9527         │
             ▼                          ▼
        客户端 A                    客户端 B（各自进程）
        LanClient ── 镜像 LobbyState（只读） ──▶ LobbyScreen
```

- 传输：TCP；房主监听 `0.0.0.0`（默认 9527，端口可改，填 0 则系统分配）。
- Windows 上用 `SO_EXCLUSIVEADDRUSE`：端口被占用时第二次创建会**明确报错**，
  而不是两个监听套接字抢同一个端口。
- 不用 asyncio、不用第三方框架、不需要单独启动服务器：房主就是游戏本身。

## 3. 房主 / 客户端生命周期

**房主**

1. `HostServer.start()`：绑定 + listen，建立权威 `LobbyState`（自己 = seat 0、`is_host`），
   启动 accept 线程；绑定失败返回中文原因（端口占用 / 权限不足）。
2. accept 线程只做三件事：`accept` → 建 `PeerConnection` → 投递 `ACCEPTED` 事件。
3. 主线程 `poll()`：注册新连接 → 处理消息 → 心跳 → 返回提示文本。
4. `HELLO` 通过校验（版本 / 未开局 / 未满员）后分配 `player_id` 与座位，
   回 `WELCOME`（含权威大厅），广播 `PLAYER_JOINED` + `LOBBY_STATE`。
5. `close()`：给所有客户端发 `ERROR(host_closing)` + `DISCONNECT`，关闭 socket，
   回收 accept / 读写线程。可重复调用。

**客户端**

1. `LanClient.start()`：后台线程 `create_connection`（超时 6 秒，UI 不冻结）。
2. 连上后把 socket 交给 `PeerConnection` 的读写线程，连接线程立即结束。
3. 收到 `WELCOME` → `handshaked = True`，`LanScene` 把场景切到大厅。
4. `set_ready()` 只发请求；本地镜像永远等房主广播。
5. 掉线：对端关闭 / 心跳超时 → `error` 置中文文案 → `LanScene` 退回多人菜单并显示。

## 4. 协议 framing

```
+----------------+--------------------------------------+
| 4 字节大端长度  | UTF-8 JSON（v / type / id / payload） |
+----------------+--------------------------------------+
```

- `FrameDecoder` 负责缓冲与切分：**粘包**（一次 recv 多条）与**拆包**（一条被拆成多次）
  都在这里消化，上层只会拿到完整消息字典。
- 单帧上限 64 KB；长度头非法直接判协议错误并断开**那一条**连接。
- 单帧 JSON 坏掉只丢那一帧，不影响后续帧（`dropped_frames` 计数可观测）。
- 消息类型：`HELLO / WELCOME / LOBBY_STATE / PLAYER_JOINED / PLAYER_LEFT /
  SET_READY / START_GAME / ERROR / PING / PONG / DISCONNECT`。
- 版本 `PROTOCOL_VERSION = 1`；版本不符时房主回 `ERROR(version_mismatch)`
  并断开，客户端显示「协议版本不兼容」，而不是静默错乱。
- 心跳 `PING/PONG` 在连接层内部消化，不进入上层事件流；参数集中在
  `protocol.py`（3 秒一次，10 秒无字节判掉线）。
- 全程不 pickle 任何对象，也不传输 `Game`。

## 5. Lobby State 权威规则

`LobbyState` 只有房主进程会修改；客户端收到的是 `to_dict()` 快照反序列化出来的镜像。

| 事项 | 规则 |
| --- | --- |
| 身份 | 每个连接分配 UUID 片段作为 `player_id`，**不用 socket 当身份** |
| 座位 | 房主固定 seat 0，后来者取最小空位；离开后座位会被复用 |
| 加入 | 版本不符 / 已开局 / 满员 → `ERROR` 拒绝，不进入玩家列表 |
| 准备 | 客户端只发 `SET_READY`，房主改完再广播 `LOBBY_STATE` |
| 房主 | 房主不需要准备，`status_label` 恒为「房主」 |
| 开局条件 | ≥ 2 人且所有非房主玩家都已准备；`start_blocker()` 给出中文原因 |
| 开始 | 只有房主能触发；房主广播 `START_GAME` → 双方进入「已开始」横幅 |
| 人数上限 | 房主可调 2～8，只限制后来者，不踢已在房里的玩家 |
| 掉线 | 读线程结束 → 主线程从大厅移除该玩家 → 广播 `PLAYER_LEFT` + `LOBBY_STATE` |

客户端界面里的「准备 / 取消准备」按钮按本地点击立刻翻面（避免连点看起来没反应），
但最终显示的状态始终来自房主广播；连点 5 次这类场景下双方仍收敛到同一个结果。

## 6. Pygame 主线程与网络线程的隔离

- 网络线程只做：`accept / recv / send / 解码 / queue.put`。
  它们**不碰** pygame、`Game`、`players[]`、`Renderer`、任何 Surface。
- 主线程只做：每帧 `LanSession.poll()` 排空队列 → 改 `LobbyState`（房主）或镜像
  （客户端）→ 更新界面 / 切场景。所有绘制都发生在主线程。
- 所有跨线程传递都是 `NetworkEvent` 纯数据；socket 只在连接对象里被读写线程使用。
- 发送也走队列：主线程 `send()` 只是 `outbox.put()`，因此不会因为对端不读而卡住。
- 线程全部 `daemon=True`，`close()` 会 shutdown + join（有上限），
  5 轮「创建 → 加入 → 准备 → 双方离开」后线程数回到 1（无泄漏）。

## 7. 实际验证结果

**确定性测试**：`tests/test_engine_v2_phase11_lan.py`，38 条，覆盖帧切分（粘包 / 拆包 /
超长帧 / 坏帧不影响后续）、端口与地址解析、大厅座位与满员、开局条件、序列化往返、
真实 socket 的加入 / 准备 / 连点 / 房间满 / 已开局 / 主动离开 / 硬断线 / 房主关房 /
端口冲突 / 版本不符 / 垃圾字节只断一个连接，以及界面外壳（入口按钮、分辨率布局、
场景切换、失败可重试、输入框过滤与限长）。

**同进程双实例**（`python tools/lan_smoke.py`）：42 项检查全部通过，含
场景 1 创建/加入、场景 2 准备同步、场景 3 START_GAME 到达、场景 4 客户端离开、
场景 5 房主关闭、场景 6 错误 IP/端口（不冻结、中文提示）、场景 7 房间满、
场景 8 连点准备、场景 9 多分辨率切换、场景 10 真实局域网 IP 连接、
场景 11 创建→返回→再创建。

**跨进程**（`python tools/lan_two_process.py`）：11 项检查全部通过，
双向都验过——真实 `python main.py --host` 当房主、外部客户端加入/准备/被拒/房主进程被杀掉后立刻感知；
反向也验了 `python main.py --join` 作为客户端加入脚本开的房间。

**回归**：`python -m unittest discover -s tests -t .` → 988 tests OK（基线 950 + 新增 38）；
`python -m compileall main.py src tools tests` 通过；`python main.py`（dummy 驱动）可正常启动主菜单；
`python -m tools.ui_smoke`（既有单机 UI 冒烟）仍然全通过。

**给两台真机的操作步骤**（本轮唯一没能在物理双机上跑的项：本机只有一台电脑）

1. 两台电脑连同一个 WiFi / 同一个宿舍路由，都启动游戏：`python main.py`
2. 房主：`多人对战（局域网）` → 填昵称 → 选人数 → `创建房间`；
   大厅顶部会显示 `192.168.x.x:9527`，把它念给同学。
3. 其他人：`多人对战（局域网）` → 填昵称 + 房主 IP + 端口 9527 → `加入房间`。
4. 每个人点`准备`，房主点`开始游戏`，双方同时看到「房主已开始游戏」。
5. 连不上时先看 Windows 防火墙是否拦了 Python（首次监听会弹窗），
   再确认两台机器 `ping` 得通。

## 8. 已知限制

- 只有大厅：`START_GAME` 之后停在大厅横幅，**不进入对局**（Phase 11.2 的正题）。
- 没有自动发现房间，需要手输房主 IP；没有公网联机 / NAT 穿透 / 加密 / 重连恢复。
- 没有观战、踢人、换座、房间密码、聊天；身份模式在联机入口暂不可选（沿用当前模式的允许人数）。
- 客户端镜像始终是房主广播的快照，不做断线重连：掉线即退回多人菜单。
- 心跳参数（3 秒 / 10 秒）未做自适应；极端弱网下可能误判掉线。
- 房主与客户端共用 `Game` 实例：进联机前会重置本局角色（单机数据不跨模式保留）。

## 9. Phase 11.2 推荐入口

1. **RemoteHumanController**：`ControllerType.REMOTE_HUMAN` 与 `Player.connection_id`
   已在 `src/player.py` 预留。控制器按 `PlayerController` 接口实现
   （与 `HumanController` / `AIController` 并列），流程只向控制器要动作，
   不写 `if remote_player` 之类特判。
2. **意图协议**：在 `src/network/protocol.py` 增加
   `PLAY_CARD / SELECT_TARGET / RESPOND_CARD / ACTIVATE_SKILL / END_PHASE`
   与对应的「权威结果」消息（`STATE_SNAPSHOT` / `PENDING_REQUEST`），
   继续走同一套 `FrameDecoder` 与 `NetworkEvent`。
3. **权威桥**：房主的 `Game` 是唯一权威。客户端提交意图 → 房主在主线程
   （`LanScene.update` 的同一处）喂给 `RemoteHumanController` → 引擎照常推进 →
   把需要客户端回答的 `PendingRequest` 广播出去。
4. **视图同步**：只同步「玩家能看到的信息」（自己的手牌、公开区、当前动作），
   不要整包同步 `Game`；`Renderer` 继续走 `visible_identity` 等既有可见性查询。
5. **接管点**：`LanScene.update` 在收到 `START_GAME` 后不再停留在大厅横幅，
   而是按大厅座位创建 `Player` 列表（真人 = REMOTE_HUMAN）并调用现有开局流程。
