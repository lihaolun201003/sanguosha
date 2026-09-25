# Phase 11.4.2：真机运行路径上的身份局 UI 一致性

**目标**：局域网身份局必须直接用原来的单机身份局牌桌——不是"再画一套相似的"，
而是**同一张牌桌**。

**结论**：Local / LAN Host / LAN Client 现在使用同一套牌桌、同一套布局、同一份
交互实现；三者在 1920×1080、5 人身份局、同一种"我的出牌阶段"状态下，关键几何
完全一致。上一次 parity 报告的假阳性根因已定位并修掉。

---

## 1. 为什么真人看到的 LAN UI 仍然很差

不是"没有复用 Renderer"，也不是"外层还是另一套 scene"。真实原因是
**LAN 局缺了身份局的开局数据**：

| 缺失 | 界面后果 |
| --- | --- |
| 没有分配身份 | 看不到主公标识、看不到自己的身份 |
| 没有分配武将 | 状态条只有"座次 N"、没有武将头像与名字、**技能栏整条消失**、回合横幅里没有武将牌 |

于是 LAN 画面虽然几何结构正确，却像一局"没有武将、没有身份"的裸局——与单机
身份局观感差距极大。玩家看到的是"这是一个明显不同的 LAN debug UI"。

**证据**：`src/game/core.py` 的 `start_networked_battle()`（LAN 唯一开局入口）
只发牌、只 `assign_generals()` 一个空池，从不发身份；而单机走
`begin_general_select() → confirm_general() → start_local_battle()`，身份与武将在
这条路上都有。

## 2. 三条真实运行链（从 `main.py` 出发）

### 2.1 Local（单机身份局）

```
main.py
 ├ StartMenu.handle_click("开始")      → game.begin_general_select()
 ├ 场景 identity_reveal                → game.confirm_identity()
 ├ 场景 general_select                 → game.confirm_general()
 └ 场景 game
     ├ 事件   handle_game_click(pos, game, renderer, human)
     ├ 更新   game.update(dt) / renderer.update(dt)
     └ 绘制   renderer.draw(game)          ← src/renderer.py
```

### 2.2 LAN Host

```
main.py
 ├ LanScene.enter()  → MultiplayerMenu → LobbyScreen
 ├ session.create_room() / session.start_match()
 │      └ HostMatch.start() → game.start_networked_battle(seats, general_pool)
 ├ 场景 game（与单机**完全同一个 scene 字符串**）
     ├ 事件   handle_game_click(pos, game, renderer, human)
     ├ 更新   lan_scene.update(dt, game)（只排空网络）+ game.update(dt)
     └ 绘制   renderer.draw(game)          ← 与单机同一行
```

### 2.3 LAN Client

```
main.py
 ├ LanScene.enter() → MultiplayerMenu → Lobby（session.join_room + set_ready）
 ├ 场景 remote_game（LAN_SCENES 之一）
     ├ 更新   lan_scene.update → RemoteTableScene.update
     │            ├ 取走房主的只读视图 ClientGameView
     │            ├ 把 DecisionRequest 翻译成本地同构交互槽位
     │            └ 播放表现事件（ClientPresentation）
     ├ 事件   RemoteTableScene.handle_event
     │            └ ui.interaction.handle_game_click(pos, view, renderer, remote_human)
     └ 绘制   renderer.draw(view)          ← 与单机同一行
```

## 3. 真正的 UI 分叉点

| 路径 | Phase 11.4.2 之前 | 现在 |
| --- | --- | --- |
| 绘制 | 已共用 `Renderer.draw` | 不变 |
| 布局 | 已共用 `LayoutMetrics` / `TableLayout` | 不变 |
| **开局数据** | **LAN 缺身份与武将** | **补上（第 1 节）** |
| **鼠标点击** | **Local 走 `interaction.py`；Client 走 `RemoteTableScene` 自己的一套** | **两者共用 `handle_game_click`** |
| **末端动作** | Local 直接调 `Game.xxx()`；Client 自己在 `RemoteTableScene` 里拼 `DecisionResult` | **共用 `HumanController` 接口，Local/Remote 只是两个实现** |

所以分叉**不在** scene，而在"点击路由 + 末端动作"这一层，以及**LAN 局缺失的
开局数据**。

## 4. 上一次 parity test 为什么是假阳性

`tools/lan_ui_parity_snapshots.py`（Phase 11.4）的问题：

1. **自己搭渲染循环**：自己 `set_mode`、自己 `new Renderer`、自己调 `draw`，
   绕开了真实 `main.py` 的初始化与场景路由；它验证的是"我这段代码能画出什么"，
   不是"玩家会看到什么"。
2. **只比较文本**：断言的是 `prompt.describe()` 的标题/正文与按钮文案，
   **完全没有比较任何 rect**——而"难看"恰恰是几何与元素缺失的问题。
3. **不检查隐藏的缺失**：没有断言"身份 / 武将 / 技能栏"必须存在，于是
   "没有武将的裸局"也能拿到 6/6 PASS。
4. **分辨率与真实运行不同**（1600×1000 窗口 vs 全屏桌面），进一步掩盖差异。

新的验收换成三层，且**跑真实 `main.py`**（见第 12 节）。

## 5. `remote_table.py` 最终职责

它**不再是第二套游戏界面**。现在只做四件事：

1. 把房主的 `DecisionRequest` 翻译成本地同构交互槽位
   （`pending_selection` / `pending_target_selection` / `pending_skill_input` /
   `response` / `choice`）；
2. 取走表现事件、推进本地动画；
3. 是 / 否与多选一复用单机的 `ChoiceOverlay`；
4. 本地提示与离开信号。

它已经**没有** `_toggle_card` / `_toggle_candidate` / `_toggle_target` /
`_handle_skill` / `_submit*` / `_run_action` / 任何布局与绘制代码——这些全部
交回统一实现。测试 `test_remote_table_owns_no_second_game_ui` 会盯住这一点，
防止它再长出第二套 UI。

## 6. 是否实现单一 GameTable

是。三个角色最终进入的绘制与交互入口是**同一组对象**：

```
                 Renderer.draw(state)          ← 一个类
                 layout.LayoutMetrics          ← 一套几何
                 layout.TableLayout            ← 一套座位/手牌/装备排布
                 prompt.describe(state)        ← 一份提示文案
                 Renderer.actions_for(state)   ← 一份按钮逻辑
   ┌──────────────────────┴──────────────────────┐
   │        ui.interaction.handle_game_click      │  ← 一个点击路由
   └──────────────────────┬──────────────────────┘
                  ui.human_control.HumanController
                     ┌────┴────┐
        LocalHumanController  RemoteHumanController
                 │                    │
               Game          DecisionResponse → 房主
```

`state` 在单机/房主侧是权威 `Game`，在客户端侧是 `RemoteGameView`（只读视图，
提供同构的展示字段与惰性交互槽位）。

## 7. Presentation Adapter 架构

```
权威侧                             客户端侧
Game                               ClientGameView（纯数据，逐人过滤）
 │                                     │
 ├ Renderer / LayoutMetrics  ──────────┤ 同一套
 │                                     │
 └ LocalHumanController       RemoteGameView（Adapter）
        │                            ├ 展示字段：玩家/手牌/装备/判定/牌堆/日志
      Game API                       └ 交互槽位：由 DecisionRequest 填充
        │                                     │
        └────────────── 同一个 handle_game_click ──────────────┘
                                              │
                                  RemoteHumanController
                                              │
                                     DecisionResult → 房主
```

**客户端不构造 Game**（禁止），不跑规则引擎，不接收别人的手牌、未公开身份、
牌堆顺序——这些在视图生成阶段就**不放进去**（`src/game/view/visibility.py`）。

## 8. viewer-relative 座位

`layout.TableLayout._build_seat_rects()` 以**自己**为原点按座次环排布：正对面在
上、两侧按距离远近分列。协议里的 `player_id` 仍是全局稳定身份，但**位置永远
相对当前观众**。

因此 Host（seat 0）与 Client（seat 1）在同一局里看到的座位分布不同，但
**"与我同一个 offset 的对手"落在完全相同的 rect**——这正是第 12 节的几何断言。

## 9. LayoutMetrics 统一结果

1920×1080、5 人身份局下，Local / Host / Client 三份运行时几何：

| 键 | 结果 |
| --- | --- |
| `screen` / `scale` | 一致（1920×1080 / 1.2） |
| `regions.central` / `prompt` / `player_status` / `hand_area` / `log` / `action_card` | 一致 |
| `buttons.primary` / `buttons.secondary`（含 rect 与文案） | 一致 |
| `seats(offset→rect)` | 一致 |

没有三套 magic number：所有 rect 都由 `LayoutMetrics` 从同一份设计坐标换算，
绘制与命中测试用的是同一批对象。

## 10. interaction UI 统一结果

| 路径 | 是否共用 | 依据 |
| --- | --- | --- |
| draw path | ✅ | `Renderer.draw(state)` |
| layout path | ✅ | `LayoutMetrics` + `TableLayout` |
| mouse/input path | ✅ | `ui.interaction.handle_game_click` |
| selection/highlight path | ✅ | `pending_selection` / `card_action_source_ids()` 等同构槽位 |
| skill button path | ✅ | `Renderer.hit_action` → `skill_bar.hit` → `human.run_action` |
| confirm/cancel path | ✅ | `Renderer.actions_for` → `human.run_action` |

Local 与 Remote 的差异只剩 `HumanController` 的两个实现与网络传输。

## 11. 真实 UI 截图

全部由 **真实 `main.py` 进程**在 1920×1080、5 人身份局、同一种"我的出牌阶段"
状态下产出（不是 `Renderer(fake_view)`）：

* 单机：`tools/ui_snapshots/runtime_parity_local_identity.png`
* 房主：`tools/ui_snapshots/runtime_parity_lan_host.png`
* 客户端：`tools/ui_snapshots/runtime_parity_lan_client.png`

对应几何：同名 `.json`（`screen` / `regions` / `buttons` / `seats` / `hand` /
`equipment` / `skill_bar` / `prompt_text`）。

**测试级别标注（不得混用）**

* 上述三张图与几何对比：**LOCALHOST MULTI-PROCESS**
  （同一台机器上的多个真实 `main.py` 进程，走 127.0.0.1 loopback）。
* 新增单元测试：**IN-PROCESS**（真实 socket / 引擎 / Renderer，房主与客户端同进程）。
* **PHYSICAL TWO-PC: NOT TESTED**（本机只有一台电脑，没有用 localhost 或
  双进程替代真实双机结论）。

## 12. 实际查看截图后的结论

三张图已逐张打开检查（不是"生成后不看"）：

* 版式一致：座位带、中央战场、提示条、真人状态条、手牌区、技能栏、战报、
  固定按钮的位置与尺寸相同；
* 客户端同样显示**武将**（名字 + 头像）、**技能栏**（如【结姻】【枭姬】）、
  **身份**（主公在所有人视图里公开：客户端看到"房主 座次 0 主公 赵云 蜀"）；
* 不存在"LAN 专用面板"、没有第二套提示条样式、没有调试文字；
* 唯一的差异是内容本身（昵称、手牌、血量、身份归属），不是版式。

## 13. Phase 11.4 功能回归结果

| 项 | 结果 |
| --- | --- |
| 过河拆桥（双向） | ✅ `lan_playability_sync` 场景 5 通过 |
| 顺手牵羊（双向） | ✅ 同上 |
| 隐藏手牌不透明选择 | ✅ `lan_view_sync` 场景 1/2 通过 |
| Remote Decision（决策请求→回答→房主校验） | ✅ `lan_gameplay_bridge` 41/41 |
| 主动技入口 | ✅ `lan_playability_sync` 场景 6 通过 |
| 隐藏信息保护 | ✅ `lan_view_sync` 48/48（检查已升级为结构化判定，见下） |
| 观星远程化 | 未在本次范围内（按任务要求不动） |

**顺带修掉的问题**

1. `HostMatch.abort()` 遍历 `controllers` 时字典被修改 → `RuntimeError`
   （房主关房必崩）。已改为遍历快照。
2. `lan_view_sync` 的"未公开身份不进网络包"检查原先是**全文本搜索**，会把
   "自己当时（重分配前）的身份"误判成泄露。已升级为**结构化判定**：按每条
   载荷自身的时间点，检查"别人那条 player 记录的 identity 是否非空"，并且
   仍然尊重"主公公开 / 阵亡公开 / 自己可见"三条规则——比原来更严格。

## 14. 全量测试数字

```
python -m unittest discover -s tests   →   1053 tests, OK
                                            （Phase 11.4 基线 1044 + 本阶段新增 9）
python -m compileall main.py src tools →   通过
```

基线 1044 一项都没有减少，也没有任何测试被 skip / xfail / 删除。

LAN 工具套件（全部本次实跑）：

| 工具 | 结果 | 级别 |
| --- | --- | --- |
| `lan_smoke` | 44/44 | IN-PROCESS |
| `lan_gameplay_bridge` | 41/41 | IN-PROCESS |
| `lan_gameplay_ui` | 23/23 | IN-PROCESS |
| `lan_view_sync` | 48/48 | IN-PROCESS |
| `lan_presentation_sync` | 87/87 | IN-PROCESS |
| `lan_playability_sync` | 58/58 | IN-PROCESS |
| `lan_two_process` | 11/11 | LOCALHOST TWO-PROCESS |
| `runtime_parity_run`（本阶段新增） | 三张图 + 几何全一致 | LOCALHOST MULTI-PROCESS |

新增测试：`tests/test_engine_v2_phase11_4_2_runtime_parity.py`（9 项，IN-PROCESS），
覆盖 §10 的六条路径与座位几何的 viewer-relative 性质。

## 15. 修改的主要文件

| 文件 | 改动 |
| --- | --- |
| `src/game/core.py` | `start_networked_battle()` 补上身份分配；武将池由调用方决定 |
| `src/network/match.py` | `general_pool()`：身份局必须有武将；`abort()` 遍历快照修崩溃 |
| `src/ui/human_control.py`（新） | `HumanController` 接口 + `LocalHumanController` |
| `src/ui/remote_control.py`（新） | `RemoteHumanController`（点击 → DecisionResponse） |
| `src/ui/interaction.py` | 点击路由改为"命中测试 + 语义分支 + HumanController"，Local/Remote 共用 |
| `src/ui/remote_table.py` | 降级为适配层：不再有自己的选择状态机、布局与提交逻辑 |
| `src/ui/view_adapter.py` | `RemoteDecisionState` 迁入（客户端选择状态） |
| `src/ui/lan_scene.py` | `human_for(game)`：按场景选 HumanController |
| `src/ui/runtime_hook.py`（新） | 真实主循环上的验证挂钩（默认关闭，零影响） |
| `main.py` | 点击路由传 `HumanController`；挂载运行期验证脚本 |
| `tools/runtime_capture.py`（新） | 在真实进程里驱动操作、截图、导出几何 |
| `tools/runtime_parity_run.py`（新） | 编排 Local / LAN 的验收运行并对比几何 |
| `tools/lan_view_sync.py` | 隐藏身份检查升级为结构化判定 |

## 16. 剩余问题

1. **PHYSICAL TWO-PC 未验证**：本机只有一台电脑，所有联机结论都是
   LOCALHOST 级别。真机双电脑需要在两台机器上重跑 `tools/runtime_parity_run.py`
   的 LAN 部分（`--role lan`），并人工确认客户端分辨率与画面。
2. **LAN 没有"选将"界面**：身份局开局直接按座次分配武将（与单机"三选一"不同）。
   本次只保证"有武将、有技能栏"，选将流程属于下一阶段。
3. **观星远程化**未做（按任务要求排除）。
4. `lan_playability_sync` 在极少数运行下会出现 2 项场景失配（重跑即过），
   与本次改动无关，属于既有场景搭建对随机开局的敏感，后续可加固定种子。
