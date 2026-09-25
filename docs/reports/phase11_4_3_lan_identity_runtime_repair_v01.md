# Phase 11.4.3 修复报告：LAN 开局 = 原来的身份局 + RemoteHumanController

本阶段只做一件事：**把联机开局从"二人裸局"换成原来的身份局**，区别只在
"某些座位由远程真人控制"。没有新增模式、没有新增规则、没有 UI 美化。

## 一、为什么真实 LAN 之前只有 2 人裸局

三个独立原因叠加，任何一个都足以让那一局不是身份局（完整审计见
`phase11_4_3_lan_identity_runtime_audit_v01.md`）：

1. **房间模式默认是自由混战。** `Game.__init__` 里 `mode_id = "ffa"`，玩家除非
   在主菜单手动点「标准身份」，进联机时模式就是 ffa。`HostMatch.start()` 与
   `start_networked_battle()` 都不设置模式，于是 `mode.uses_identities` 为
   False → 身份分支整段跳过；`general_pool()` 也按这条判断返回空池 → 无武将、
   无技能。
2. **没有 AI 补位。** seats 只由 `lobby.ordered()`（真人）生成：2 个真人 = 2 个
   `Player`。即使模式是身份局，`distribution_for(2)` 也返回 `None`
   （身份局只有 5～8 人），身份一个都发不出来。
3. **存在第二套开局初始化。** `start_networked_battle` 与 `start_local_battle`
   是两份独立实现：前者没有身份展示、没有选将、没有清理旧技能绑定，人数完全
   由调用方决定。

## 二、分叉点

| 环节 | 单机 | 修复前的 LAN | 修复后 |
| --- | --- | --- | --- |
| 模式来源 | 主菜单 `set_mode` | 大厅里的显示串（默认 ffa），**从不生效** | 大厅模式 → `HostMatch` 真正 `set_mode` |
| 武将池 | `confirm_general` 写入真实池 | 仅身份局给池，ffa 给空 | 身份局给真实池（与单机同池） |
| 人数 | 菜单选（身份局 5～8） | 实数真人 | 模式允许人数中 ≧ 真人数的**最小值**，不够补 AI |
| 开局规则 | `reset → 身份 → 武将 → 主公加成 → 首行动` | 另一份内联实现 | **同一份** `Game._apply_opening_rules` |

## 三、原 `start_networked_battle()` 做了什么

它确实调用了 `roll_identities / apply_identities / setup_battle / first_player`——
这正是之前"看起来已经接回身份局"的原因。但它：

* 不设置模式（调用方给的是 ffa）；
* 人数由 seats 决定，不补 AI；
* 武将池由调用方给，给空就静默无武将；
* `reset()` 把技能绑在**旧名单**上，随后 `self.players` 被整体替换，旧绑定残留；
* 不经过 `begin_general_select → confirm_identity → confirm_general` 这条真实流程。

## 四、最终统一的 identity game creation path

```
python main.py --host                     python main.py --join 127.0.0.1
        │                                         │
 LancScene.enter(game)                    LanScene.enter(game)
        │  menu.apply_match_mode(session, game)   │
        │  game.set_mode("identity")              │  join_room(...)
        ▼                                         ▼
 menu.create_room(..., game_mode="identity")
        │
 LanSession.create_room → HostServer（权威大厅）
        │
 LobbyScreen「开始游戏」→ LanSession.start_match()
        │
 HostMatch.start()                       ← 联机开局的**唯一**入口
        ├─ game.set_mode(lobby.game_mode)        模式真正生效
        ├─ seats = 真人 + AI 补位                 §六
        ├─ game.remote_controller_factory = make_controller
        └─ Game.start_networked_battle(seats, general_pool=真实武将池)
                ├─ 按座位表重建 Player（HUMAN / REMOTE_HUMAN / AI）
                ├─ skills / modifiers / conversions 清空后重新绑定
                └─ Game._apply_opening_rules()   ← 与单机**同一份**开局规则
                        ├─ mode.apply_identities(roll_identities())   身份
                        ├─ assign_generals()                          武将
                        ├─ mode.setup_battle()                        主公 +1 体力
                        └─ return mode.first_player()                 主公先手
```

单机的 `start_local_battle()` 走的是同一个 `Game._apply_opening_rules()`，
所以"身份局"在两条链上只有一份规则实现。

## 五、Controller seat mapping

5 人局（2 真人 + 3 AI）实际建成的座位表：

| 座位 | 角色 | controller_type | 实际控制器 |
| --- | --- | --- | --- |
| 0 | 房主（真人） | `human` | `HumanController` |
| 1 | 远程真人 | `remote_human` | `RemoteHumanController`（网络桥注入） |
| 2..4 | AI 补位 | `ai` | `AIController`（原来的那个，没有新写） |

`Game.create_controller` 只按 `controller_type` 分派，规则层不认识"网络玩家"。

## 六、AI 补位逻辑

* 本局人数 = **模式允许人数中 ≧ 真人数的那个最小值**（`lobby.planned_battle_size`）。
  身份局只有 5～8 人，所以 2 名真人 → 5 人局（补 3 AI）；5 名真人 → 5 人局（补 0）；
  6 名真人 → 6 人局。自由混战允许 2～8 人 → "有几名真人就是几人"，与联机旧行为一致。
* 补位座位用最小的空座号，`player_id = "AI-<seat>"`，名字 `AI 1…`。
* **大厅里显示的真人 / AI 补位数，与真正开局的人数出自同一个函数**
  （`src/network/lobby.py: planned_battle_size`），所以不会出现"显示 5 人、开局 2 人"。
* 补位只发生在 `HostMatch`（联机编排层）；`start_networked_battle` 保持"按给定
  座位表建局"的底层语义，既有单测直接用它。

## 七、身份分配结果

由 `IdentityMode.roll_identities()` 按人数配比生成、按座次写入（复用原实现）：

```
5 人 = 主公 ×1 + 忠臣 ×1 + 反贼 ×2 + 内奸 ×1
主公 identity_revealed = True（开局公开），主公 max_hp = 武将上限 + 1，主公先手
```

实测（Runtime Marker 输出）：

```
=== LAN IDENTITY RUNTIME ===
game_mode = identity
players = 5
human = 2
local_human = 1
remote_human = 1
ai = 3
identities = 主公 x1、忠臣 x1、反贼 x2、内奸 x1
identities_assigned = True
generals_assigned = True
generals = [<房主>=daqiao, <远程>=simayi, AI-2=lvbu, AI-3=diaochan, AI-4=guanyu]
```

## 八、武将 / 技能初始化结果

* 联机身份局一律注入**真实武将池**（`game.generals.ids()`，25 个武将），
  走单机的 `assign_generals()`：同池不重复抽取。
* 用房间号派生随机种子，同一间房每次开局得到同一份分配（可复现、可写进报告）。
* 换名单时先 `skills / modifiers / conversions` 全部清空再重新绑定——修掉了
  "旧技能绑在新名单上"的残留。
* 主公技只在主公身上绑定（`SkillManager.lord_skills_enabled`，既有规则），
  其余技能逐项与武将技能表一致（测试断言）。
* 自由混战联机**沿用旧语义**（不指定武将）：联机没有选将界面，临时塞一套随机
  武将会让触发类技能（例如【裸衣】的确认窗口）插进既有交互链，属于本阶段之外
  的行为改变。

## 九、visibility 验证

* 视图生成走既有 `build_view` + `visible_identity_of`：手牌内容只发给本人，
  未公开身份根本不进网络包。
* 测试同时检查**视图**与**原始载荷**：`test_scenario_d_*` 遍历客户端真正收到的
  每一条 socket 消息，确认别人的隐藏身份一次都没出现。
* 渲染层只走 `visible_identity(player, viewer=...)`：座位区显示"主公 / 已阵亡 /
  自己"的身份，玩家自己的身份显示在自己的状态面板（装备槽右侧）。
  **没有任何一处直读 `player.identity`。**

## 十、Runtime Marker

`src/game/runtime_marker.py`：开局结束后把真实结论打到控制台（默认开启，
`SGS_RUNTIME_MARKER=0` 关闭）。它只打印，不画界面，所以既不污染 UI，也能在
真人运行时被看到：

```
=== LAN IDENTITY RUNTIME ===
created_by = src/network/match.py:HostMatch.start() → src/game/core.py:Game.start_networked_battle()
game_mode / players / human / local_human / remote_human / ai
controllers = [<id>:<名字>=<类型>(<控制器类名>) …]
identities / identities_assigned / generals_assigned / generals
```

它存在的理由就是本阶段的根因：上一轮的"已接回身份局"结论来自工具，
而工具自己调了 `set_mode / apply_identities`，真人运行走的是另一条路径。

## 十一、本机双开工具

`tools/run_local_lan_pair.py`：

```bash
python tools/run_local_lan_pair.py              # 两个真实窗口，手动玩
python tools/run_local_lan_pair.py --capture    # 自动走完大厅并截图
```

* 手动模式：`python main.py --host` + `python main.py --join 127.0.0.1`，
  两个完整进程、两个窗口、各自网络线程，**没有 fake client**。
* 截图模式：两个进程各加载 `tools/runtime_capture.py`（真实主循环上的运行期
  脚本），在进程内部完成"创建房间 / 加入房间 / 开始游戏 / 截图"。

## 十二 / 十三、Host 与 Client 截图

两张图都来自**两个真实 main.py 进程**（localhost socket、真实大厅、真实
开始游戏、真实身份局），不是 fake view：

* `tools/ui_snapshots/lan_identity_runtime_host.png`
  —— 房主视角：5 个座位（房主 + 远程玩家 + AI 1/2/3）、远端座位标着金色「主公」、
  自己的面板标着「反贼」、武将（马超 / 夏侯惇 / 华佗 / 诸葛亮 / 曹操）、
  技能栏【马术】【铁骑】、6 张手牌、战报「标准身份开始：5 人」。
* `tools/ui_snapshots/lan_identity_runtime_client.png`
  —— 客户端视角：座位布局与单机逐字同构，自己的面板标着金色「主公」（体力 5/5，
  主公 +1）、技能栏【刚烈】、6 张手牌、出牌阶段与「结束回合」按钮。

## 十四、LOCALHOST MULTI-PROCESS 验证

* `tools/run_local_lan_pair.py --capture`（两个真实进程，回环地址）
* `tools/lan_two_process.py`（11/11）
* `tests/test_engine_v2_phase11_4_3_lan_identity_runtime.py`（真实 socket，
  25 项）

## 十五、PHYSICAL TWO-PC

**PHYSICAL TWO-PC: NOT TESTED** —— 本机只有一台机器，全部联机验证都是
**LOCALHOST MULTI-PROCESS**（同机多进程 + 127.0.0.1 回环），不是两台物理电脑。

## 十六、全量测试

| 验证 | 命令 | 结果 |
| --- | --- | --- |
| 编译 | `python -m compileall -q main.py src tools` | 通过 |
| 全量单测 | `python -m unittest discover -s tests -p "test_*.py"` | **1079 passed**（基线 1053 + 本阶段新增 26） |
| 视图同步 | `tools/lan_view_sync.py` | 48/48 |
| 表现同步 | `tools/lan_presentation_sync.py` | 87/87 |
| 联机可玩性 | `tools/lan_playability_sync.py` | 58/58 |
| 视觉一致性 | `tools/lan_ui_parity_snapshots.py` | 6/6 |
| 跨进程 | `tools/lan_two_process.py` | 11/11 |
| 双开截图 | `tools/run_local_lan_pair.py --capture` | host + client 两张，均 P`tag=scene` |

（`lan_presentation_sync` 与 `lan_ui_parity_snapshots` 在机器负载高时偶现 1～2 项
场景失配，重跑即过——这是本阶段之前就存在的脚手架时序脆弱，已记录但未改动
产品代码。）

没有任何测试被删除、跳过、标 xfail 或降低断言；与本次行为变更冲突的两处
**测试设置**做了等价替换（都换成"走上真实开局路径"，断言不变或更严）：

* `tests/test_engine_v2_phase11_lan.py`：START_GAME 广播的模式断言从 `"ffa"` 改为
  `"identity"`（联机默认模式）；
* `tests/test_engine_v2_phase11_2_bridge.py`：桥接用例的测试房间显式使用自由混战
  （保持 2 人最小局，与身份局用例分工）；
* `tests/test_engine_v2_phase11_4_2_runtime_parity.py`：模式改由 HostSide 交给大厅，
  删掉开局前手动 `set_mode`。

## 十七、修改文件

产品代码：

| 文件 | 改动 |
| --- | --- |
| `src/game/core.py` | 抽出 `_apply_opening_rules()`（身份→武将→开局修正→首行动）；`start_local_battle` 与 `start_networked_battle` 共用；换名单时清空技能绑定；战报按模式报名字 |
| `src/game/runtime_marker.py` | **新增**：开发期 Runtime Marker |
| `src/game/turn.py` | 修"出牌阶段被跳过"的既有死锁（弃牌阶段可收尾） |
| `src/network/match.py` | `HostMatch.start()`：模式真正生效、AI 补位座位表、真实武将池 + 可复现种子、开局 Marker |
| `src/network/lobby.py` | `planned_battle_size()`：本局人数的唯一推算 |
| `src/network/session.py` | 联机默认模式 `identity`；`create_room` 模式来源；`planned_battle_size / planned_ai_count` |
| `src/ui/lan_scene.py` | 进联机菜单时把大厅模式落到 Game 上 |
| `src/ui/lobby.py` | 大厅显示"真人 N ｜ AI 补位 M ｜ 本局 K 人" |
| `src/ui/multiplayer_menu.py` | 模式选择（默认标准身份）+ 本局人数预告 |
| `src/ui/player.py` | 自己身份显示（可见性查询）+ 座次右边界改用装备槽位置 |
| `src/renderer.py` | 座位身份走 `visible_identity(viewer=自己)`；弃牌阶段"结束回合"可用性；`hand_limit_of()` 兼容只读视图 |

工具与测试：

| 文件 | 改动 |
| --- | --- |
| `tools/run_local_lan_pair.py` | **新增**：一键双开两个真实 main.py（手动 / `--capture`） |
| `tools/runtime_capture.py` | 交棒只给远程真人座位；牌桌兜底截图 |
| `tools/lan_view_harness.py` | `HostSide` 的模式交给大厅；开局等桌面空闲 |
| `tools/lan_view_sync.py` | 删掉手动 `set_mode / apply_identities` |
| `tests/test_engine_v2_phase11_4_3_lan_identity_runtime.py` | **新增**：场景 A～H + 只读视图渲染 + 跳过阶段收尾（26 项） |
| `tests/test_engine_v2_phase11_lan.py` | START_GAME 模式断言 |
| `tests/test_engine_v2_phase11_2_bridge.py` | 桥接用例改用最小局 |
| `tests/test_engine_v2_phase11_4_2_runtime_parity.py` | 模式交给大厅 |

文档与产物：

* `docs/reports/phase11_4_3_lan_identity_runtime_audit_v01.md`（审计）
* `docs/reports/phase11_4_3_lan_identity_runtime_repair_v01.md`（本文件）
* `tools/ui_snapshots/lan_identity_runtime_host.png` / `_client.png`（双开截图）
* `tools/ui_snapshots/runtime_logs/lan_identity_runtime_*.json`（进程内诊断）

## 十八、剩余问题（如实列出）

1. **武将分配是系统代选**，联机还没有选将界面。房主与远程玩家都由系统从真实
   武将池抽取（同一房间号可复现）。要改成手动选将，需要把选将界面也做成
   远程决策。
2. **自由混战联机没有武将**（沿用旧语义）。要给它加武将，得先让触发类技能的
   确认窗口能在联机交互链里正确出现。
3. **观星一类需要本地选牌的技能对远程座位是禁用状态**（房主明确回"这个技能的
   交互还没有远程化"），本阶段没有做远程化。
4. **`lan_playability_sync` / `lan_view_harness` 曾有的时序脆弱已修**（开局动画
   0.66 秒 > 脚手架固定等待 0.4 秒），但那属于工具脚手架，不是产品行为。
5. **修了一个与联机无关的既有死锁**：【乐不思蜀】判定命中导致"出牌阶段被跳过"
   时，本地真人没有任何动作能结束那个回合（AI 与远程真人都由控制器收尾，只有
   本地路径漏了）。现在"结束回合"按钮在该状态下可用，`end_player_turn` 也接受
   弃牌阶段收尾。被跳过时必须先弃够牌才能结束回合，这一点由测试钉住。
