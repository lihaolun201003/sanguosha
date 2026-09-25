# Phase 11.2：RemoteHumanController + 权威决策桥

- 报告日期：2026-09-20
- 基线：Phase 11.1（988 tests / 联机大厅 42 项 / 跨进程 11 项全通过）
- 本轮结果：1012 tests 全通过；规则链 41/41；真实界面 23/23；11.1 大厅回归 44/44；跨进程 11/11；单机 UI 冒烟通过
- 工作区：`C:\Users\lihao\Desktop\sanguosha\sanguosha -zcode`

本阶段解决的是**一件事**：房主上的权威 `Game` 需要一个决策时，能把它交给远程真人，
拿到答案后继续走原来那条 Flow。规则仍然只有一套，`TurnFlow / ResponseSystem /
技能流程` 里没有出现任何 `if remote:` 分支。

---

## 1. 原有真人输入机制审计结果

审计结论：项目**已经有了**统一的决策抽象，缺的只是"异步来源"这一档。

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 引擎决策请求 | `src/game/engine/pending.py` | `PendingRequest`（`RESPOND_CARD / CONFIRM / CHOOSE_OPTION / SELECT_CARDS / SELECT_TARGETS`）+ `PendingManager`（带**栈**，支持嵌套）+ `PendingResolution` |
| 唯一咽喉点 | `GameEngine.present_or_auto_resolve(request)` | 全部 15 处流程（杀 / 决斗 / 无懈 / 濒死 / 判定 / 装备技能 / 武将技能 / 回合）都从这里"要一个决策" |
| 本地真人交互状态 | `Human` 分支写入 `game.response` / `game.choice` / `game.pending_selection` / `game.pending_target_selection` | UI（`src/ui/interaction.py` 的点击路由）只把点击翻译成 `game.*` 公开入口 |
| 出牌阶段 | `TurnMixin._enter_play_phase` | 真人：只给提示等鼠标；AI：`controller.take_turn(...)` 用动作队列自动出牌 |
| AI | `AIController.respond(request)` | 与真人共用同一个 `PendingRequest`，只是答案由 AI 给 |

所以本阶段的做法是**扩展已有结构**，不是另造一套：把 `present_or_auto_resolve` 从
"AI 分支 / 人类分支"改成"问这名角色的控制器"，三个控制器各自回答。

## 2. Controller 抽象实际落在哪里

```
GameEngine.present_or_auto_resolve(request)
        └── game.get_controller(request.target).present(request)
            ├── HumanController.present        (src/game/controllers/human.py)
            ├── AIController.present           (src/game/controllers/ai.py)
            └── RemoteHumanController.present  (src/game/controllers/remote.py)  ← 本轮新增
```

- `PlayerController`（`base.py`）新增两个入口：`present(request)` 与
  `take_turn(on_complete, can_play)`，另加 `notify()`、`legal_targets()`、
  `target_limits()`、`can_use()`、`discard_cards()` 这些**三种来源共用**的规则查询。
- `HumanController` 里放的是**原封不动搬过来**的本地 UI 装配代码（`game.response.request(...)`
  那一整段），所以本地真人的鼠标行为一字未变。
- `AIController.present` 接管了原引擎里的"节奏模式"分支（`WaitAction` +
  `engine.defer_respond`），`respond()` 仍是同步答案，测试与批量演算不受影响。
- `RemoteHumanController` 是唯一 `asynchronous = True` 的控制器：
  `present()` 只登记 + 发送，绝不阻塞；答案由网络回来时再 `submit()`。
- `Game.create_controller(player)` 按 `controller_type` 选实现；远程座位由网络桥
  注入的 `remote_controller_factory` 生成，**没有桥时退回 AI**（无头环境不会卡死）。
- `TurnMixin.end_player_turn(actor=None)` 加了可选的 `actor`，远程真人的"结束阶段"
  走的是和本地真人完全相同的规则路径。
- `Game.start_networked_battle(seats)` 按大厅名单建立权威角色（房主=本地真人、
  其他电脑=`REMOTE_HUMAN` + `connection_id`）。

## 3. DecisionRequest / DecisionResponse

`src/network/decisions.py`（纯数据，不认识任何武将 / 牌）：

```python
DecisionKind:  respond_card / confirm / choose_option / select_cards /
               select_targets / play_phase
```

`DecisionKind` 就是 `PendingRequestType` 的一一对应 + `PLAY_PHASE`（出牌阶段不是
PendingRequest，它是回合流程），**没有** `GUANXING_REQUEST` 这种按技能命名的类型。

`DecisionRequest`（Host → Client）：
`match_id / request_id / player_id / kind / prompt / context(cards / options / constraints)`

- `cards`: 只发**该玩家有权看到**的牌（自己的手牌发卡面；别人手里的候选只发牌背 + id）
- `targets`: 合法目标（公开信息：昵称 / 座位 / 体力 / 手牌数）
- `options`: `CONFIRM` 是「发动 / 不发动」；`CHOOSE_OPTION` 用下标映射回真实选项对象
- `constraints`: `min_cards / max_cards / min_targets / max_targets / allow_cancel / allow_pass`
- `play_phase` 的每张可用牌额外带 `targets / min_targets / max_targets`：
  **合法目标由房主算好**（距离、出杀次数、装备限制都在 `CardEffect` 里），客户端只负责挑。

`DecisionResult`（Client → Host）：`action(submit/pass/cancel/end_phase) + card_ids +
target_ids + option + confirm`。**没有任何 Python 对象、Rect、socket 或 `object id`**，
牌用 `Card.id`、人用 `Player.player_id`（都是全局稳定的字符串）。

## 4. Pending Decision 生命周期

```
      (引擎要一个决策)
present() ──▶ OPEN ──▶ DECISION_REQUEST 已发出
                 │
   DECISION_RESPONSE ──▶ RESOLVED ──▶ 映射回 GameAction ──▶ engine.submit()
                 │
                 ├─ 引擎改问了别的事 ──▶ CANCELLED (+ DECISION_CANCELLED 通知客户端)
                 ├─ 对局终止 / 掉线   ──▶ INVALIDATED（registry.cancel_all / cancel_player）
                 └─ 客户端掉线        ──▶ 对局终止（见第 9 节）
```

`DecisionRegistry` 保证：**同一名玩家同时只有一条前台决策**。引擎内部允许嵌套
（无懈套无懈）；嵌套的内层解决后，外层会被引擎的 `_drive_pending_front` 重新驱动，
网络侧再发一次详情。另有兜底：`HostMatch.recover_stalled_decisions()` 每秒检查一次
"引擎在等某个远程玩家，而客户端手里没有对应请求"，有就重新驱动一次——宁可多发一次
请求，也不能让整局停在那里等一个不会出现的面板（思路与引擎自己的 `_drive_pending_front` 一致）。

## 5. Host 验证规则

`DecisionRegistry.validate()` 是**纯数据校验**（不改任何状态），拒绝时抛
`DecisionError(code)`；`HostMatch` 只记一条简短记录并回一个提示，**对局继续**：

| 拒绝场景 | code |
| --- | --- |
| 不存在的 request_id | `unknown_request` |
| 别人的 request_id | `not_yours` |
| 上一局迟到的响应 | `stale_match` |
| 已结束的请求重复响应 | `already_resolved` |
| 消息里自称别人 | `impersonation`（以**连接身份**为准） |
| 选了没发过去的牌 / 目标 | `unknown_card` / `unknown_target` |
| 数量不符（含重复 id 凑数，会先去重） | `bad_card_count` / `bad_target_count` |
| 不允许取消却 cancel / 必须选却 pass | `cancel_not_allowed` / `pass_not_allowed` |
| 结构坏掉（request_id 不是数字等） | `bad_payload` |

只有全部通过，才会把答案映射回真实 Game 对象并 `engine.submit(...)`；映射时**再算一次**
目标合法性（`_build_play_action`：桃 / 酒 / 群体牌的目标由规则决定，需要挑的牌复核客户端给的
id 是否仍合法），引擎自己还会第三次校验。客户端永远无法直接改 HP / 手牌 / 牌堆 / 阶段。

## 6. 网络消息扩展

仍用 Phase 11.1 的 framing（4 字节长度 + JSON），**没有第二套协议**：

```
GAME_SETUP         Host → Client  开局：名单（公开信息）+ 我的手牌 + 我的座位
GAME_VIEW          Host → Client  只读视图：公开信息 + 自己的手牌 + 当前回合/阶段（指纹变化才推）
DECISION_REQUEST   Host → Client  该你做决定了（含可用牌与合法目标）
DECISION_RESPONSE  Client → Host  我选好了
DECISION_CANCELLED Host → Client  这条请求作废（引擎改问了别的事）
GAME_ABORTED       Host → Client  联网对局终止（掉线 / 出错）
```

`HostServer` / `LanClient` 只负责搬运：大厅之外的消息进 `game_messages`，由
`LanSession.poll()` 交给网络桥（`src/network/match.py`）——网络层依旧不认识规则。

## 7. Remote Player 如何关联 connection

- 大厅玩家 id 就是连接身份：`Player.connection_id = 大厅的 player_id`
  （`connection_id` 是 Phase 11.1 就预留好的字段）。
- 房主侧 `HostMatch.controllers[player_id] → RemoteHumanController`，一人一个控制器，
  这也保证了"同一玩家同时只有一条前台决策"。
- 客户端侧只有一份**只读** `ClientMatch`（公开信息 + 自己手牌 + 待回答的决策）；
  客户端进程里的 `Game` 对象**不参与联网对局**（不演算、不推进），只作为单机模式的载体存在。

## 8. 实际接通了哪些 Gameplay 场景

| 决策类型 | 说明 | 验证 |
| --- | --- | --- |
| 出牌阶段（`play_phase`） | 出牌（含目标）或结束阶段 | 远程出【杀】→ 房主真实扣牌 + 进入杀结算 |
| 结束阶段 → 弃牌阶段 | 走 `end_player_turn(actor)` → 手牌超限则发 `select_cards` 弃牌决策 | 远程结束阶段 → 回合真实交回房主 |
| 响应牌（`respond_card`） | 出【闪】/【桃】/【无懈】/【杀】或不出 | 远程出【闪】抵消杀；不出则真实掉血 |
| 确认（`confirm`） | 是 / 否（装备技能、以及将来任何"是否发动"） | 远程【八卦阵】是否发动 |
| 选牌（`select_cards`） | 弃牌 / 选一张牌（含隐藏信息只发牌背） | 远程弃牌链 |
| 选目标 / 选选项 | 通用实现已就位（突袭类、二选一） | 单测覆盖校验与映射 |

桥本身是通用的：以后接鬼才 / 观星 / 流离 / 急救 / 仁德 / 制衡，只需复用这几种
`DecisionKind`（观星排序、制衡多选都能落在 `select_cards` 上），不需要重新设计。

## 9. 双实例 / 双进程验证

四个工具，全部真实运行（不是 mock）：

| 工具 | 内容 | 结果 |
| --- | --- | --- |
| `tools/lan_gameplay_bridge.py` | 真实 socket + 真实引擎：4 条决策链（结束阶段 / 出杀 / 响应闪 / 技能确认）+ 非法 / 冒名 / 重复 / 过期 / 掉线 | **41/41** |
| `tools/lan_gameplay_ui.py` | 两个真实 Pygame 实例：大厅 → 开局 → 房主牌桌 + 客户端决策面板 → 真实鼠标点击出牌 / 结束回合 → 掉线终止；含等待期间不冻结与四种分辨率切换 | **23/23**（连跑 6 轮稳定） |
| `tools/lan_smoke.py` | Phase 11.1 大厅回归（含"开始游戏后双方进入各自对局场景"） | **44/44** |
| `tools/lan_two_process.py` | 真进程：`main.py --host` / `main.py --join` 双向 | **11/11** |

确定性用例 `tests/test_engine_v2_phase11_2_bridge.py`：24 条（校验规则 16 条 +
联网开局 3 条 + 端到端远程决策 5 条）。全量 `1012 tests OK`；`python main.py`（dummy 驱动）
可正常启动；`tools/ui_smoke.py` 单机冒烟通过。

## 10. 当前仍未联网的交互类别

- **View-As / 技能转化**（把【闪】当【杀】打、丈八蛇矛两张牌）：出牌阶段只下发"正常使用"的牌；
  这类交互需要"先选技能再选实体牌"的第二段 UI，留到后续。
- **选目标的特殊规则**：群体锦囊与自指牌由房主代选目标（规则决定），玩家不参与挑；
  需要精确挑两张的（铁索连环）已支持，但只按"合法目标"过滤，没有做"已横置"这种细节排序。
- **改判 / 五谷 / 观星**：协议与控制器都支持（`select_cards`），但没有专门的可视化
  （客户端只看到"请选择一张牌"）。
- **客户端视图**：只有自己的手牌 + 公开信息（体力 / 手牌数 / 装备槽位名 / 是否为当前回合）；
  没有桌面动作、卡牌飞行动画、判定展示、技能栏、身份隐藏展示——完整视图是 Phase 11.3 的正题。
- **掉线策略**：只做"终止联网对局并回大厅"，没有重连、AI 托管、断线续玩。

## 11. Phase 11.3 应该做什么

1. **Per-Player Game View**：把"公开信息 + 自己手牌 + 当前 Pending"扩展成完整的逐人视图
   （装备具体牌、判定区、当前阶段与桌面动作卡、Card Movement 事件），客户端只读、不演算。
2. **事件同步**：把引擎的 `EventType` 里**对玩家可见**的那部分（出牌 / 结算 / 判定 / 伤害 /
   濒死 / 卡牌移动）形成 `GAME_EVENT` 流，客户端据此驱动既有的 `src/ui/fx.py` 表现层，
   而不是每帧同步整包状态。
3. **服务端权威的时间轴**：客户端动画只播"已经发生的事"，Pending 面板出现前先播前一段结算。
4. **隐藏信息边界**：身份模式的身份可见性继续走 `visible_identity`，只在房主侧展开成该玩家
   有权看到的字符串；武将 / 装备 / 判定区的公开性按现有规则判定。
5. **把 View-As 与技能转化接上**：给 `play_phase` 的每张牌补 `skill_id / source_cards`，
   客户端先选技能再选实体牌，房主用现有 `CardActionDiscovery` 复核。
6. **掉线体验**：在"终止对局"之上加一层"等待重连 / AI 托管"的可选项。
