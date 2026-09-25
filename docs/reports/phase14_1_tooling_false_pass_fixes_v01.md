# Phase 14.1：修复 Phase 14 检查工具的四处"假通过"

Phase 14 交付的检查工具本身有四个问题，症状一致：**该报错的时候不报错**。
本文件记录每一处的复现方法、修复前后的行为、验证级别与剩余限制。

* 先加能在旧实现上失败的针对性测试，再最小修复；
* 只动检查工具、开关入口与测试，没有碰规则 / 技能 / 协议 / 回放；
* 负向测试构造的是**违规输入**（一份把对手手牌发过来的视图载荷、一个面板缺失
  的客户端状态、一个落后版本），**没有** mock 掉规则结算或网络链路；
* "全量回归"按顺序单独跑完，没有与其他测试并发。

---

## 0. 结论速览

| # | 问题 | 修复前 | 修复后 | 新增用例 |
| --- | --- | --- | --- | --- |
| 1 | 隐藏信息漏报 | 工具自己把收到的对手手牌归一化成"没有内容" | 忠实读出实际收到的牌身份与**牌面**，泄露当场被拒 | 9（6 + 3） |
| 2 | 可操作性假通过 | 面板缺失 / 请求编号过期也算"可操作" | 状态机区分 9 种状态，只有真正可提交才算 | 6 |
| 3 | 模块调试开关失效 | `enable_debug()` 对**已创建**对局无效 | 统一走一个判定入口，不冻结默认值 | 8 |
| 4 | 同版本检查缺失 | 字段相等就通过，落后版本一样过 | 检查点绑定 `revision + 状态指纹`，落后一律拒绝 | 6 |

改动文件：

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `tools/view_consistency.py` | 改（主要） | 三档牌面分类、可操作性状态机、同步检查点、按玩家视角的原始读法 |
| `src/game/invariants.py` | 改 | `armed_for` 成为唯一开关判定入口；优先级改为"对局显式 > 模块强制 > 环境变量"；新增 `effective()` |
| `src/game/core.py` | 改（2 行） | 默认值不再冻结（`None` = 跟随全局）；帧边界改走 `armed_for` |
| `src/game/engine/atoms.py` | 改（4 行） | 原子边界改走 `armed_for` |
| `src/network/match.py` | 改（+11 行） | 新增只读属性 `published_fingerprint`（把 revision 与当时的权威状态绑起来） |
| `tests/test_view_consistency_tooling.py` | 新增 | 29 个负向 / 语义测试 |
| `tests/test_view_consistency_lan.py` | 改 | 脚手架接上检查点式比对（9 个场景用例本身一条没删） |
| `tests/test_card_ownership_invariants.py` | 改 | 开关语义的 1 个用例按新语义重写（22 个用例数量不变） |
| `docs/reports/phase14_card_ownership_and_view_consistency_v01.md` | 改 | 撤回"改动前全过"的错误表述（见 §5） |

---

## 1. 问题 1：隐藏信息漏报

### 复现方法（旧实现）

```python
# 走真实客户端入口，喂一份"把对手手牌真发过来"的视图载荷
client.match.handle_client_message({
    "type": MessageType.GAME_VIEW_SNAPSHOT,
    "payload": {"revision": 10**6, "local_player_id": "<游客>",
                "players": [{"player_id": "<受害者>", ..., "hand_count": 1,
                             "hand": [{"card_id": "card-LEAK-1", "name": "SHA",
                                       "suit": "spade", "rank": "7"}]}]}})
```

### 修复前失败

```
client_state 里 P-VICTIM.hand_ids = None
[问题在] 工具把收到的对手手牌自行隐藏了
[问题在] assert_hidden 放过这份泄露 泄露没被发现（假通过）
```

根因：`_hand_ids(entries, *, mine)` 开头写死 `if not mine: return None`——**在
归一化之前就把内容抹掉了**，于是"客户端真收到了对手手牌"这件事在工具里根本
不可见；`assert_hidden` 又是从同一份结果里读的，自然一路通过。

### 修复后通过

```
P-VICTIM = {...
  'hand_ids':   ('card-LEAK-1',),
  'hand_faces': (('card-LEAK-1', 'SHA', 'spade', '7'),), ...}
[问题不在] 工具把收到的对手手牌自行隐藏了
[问题不在] assert_hidden 放过这份泄露
   客户端 P-OTHER 看到了它不该看的东西（该座位手牌三档={'real': 1, ...}）
```

做法：

1. **期望侧**仍按可见性规则算（`expected_for`：别人一律 `None`，本人给真实
   内容）；**实际侧**忠实读客户端收到的数据，不再代为隐藏。
2. 新增 `hand_faces` 字段：**未经授权却拿到牌面**（牌 id + 牌名 + 花色 +
   点数）。期望侧对所有玩家恒为 `()`，实际侧非空即泄露。它与 `hand_ids`
   互补：只泄露 `card_id` 由前者报，带上牌面由后者报。
3. 三档分类 `card_face_kind`（`real` / `token` / `blank`），**先看牌面、再看
   占位前缀与牌背标记**：
   * `token` = 选择用的不透明占位（复用生产常量
     `src/game/controllers/remote.OPAQUE_PREFIX`）或只有 id 的牌背；
   * `blank` = 什么都没拿到（`None` 占位 / 空牌背）；
   * 自相矛盾的数据（占位 token 带牌名、`face_down=True` 却带牌名）判为
     `real` —— 不被占位前缀或牌背标记掩盖。
   另有 `hand_face_kinds()` / `unauthorized_hand_faces()` 两个公开入口，按玩家
   视角直接看"这一座位究竟拿到了什么"。
4. **网络解析视图与适配层分别覆盖**：适配层不会掩盖上游泄露
   （`test_adapter_layer_does_not_hide_the_upstream_leak` 断言
   `client_state(source="adapter")` 同样报出泄露）。

### 实际验证级别

**进程内真实网络**：真实 socket、真实引擎、真实 `ClientMatch.handle_client_message`
解析链路、真实 `RemoteTableScene` → `RemoteGameView` 适配层。构造的只有
**输入载荷**，链路一步没少。真实场景用例（拆装备 / 顺手牵羊 / 丈八 / 悲歌）
仍在 `tests/test_view_consistency_lan.py` 里跑，一条没删。

### 剩余限制

* 只覆盖"视图里 **角色条目** 的手牌"。其它字段（`action.sources`、
  `selection.candidates`、`judge`）的泄露不在 `assert_hidden` 里判——那部分
  由既有的载荷级审计 `ClientSide.audit_hidden_cards` 负责，两者互补。
* 装备区 / 判定区 / 弃牌堆 / 公共池按规则就是公开的，所以"这些区域多发了内容"
  只会在整片 `diff` 里体现，不会被当成"泄露"。这是刻意的：它们不是隐藏信息。
* 三档分类只对"客户端牌对象"有效；服务端的权威 `Card` 没有 `face_down` 概念，
  不进这条路径。

---

## 2. 问题 2：可操作性假通过

### 复现方法（旧实现）

```python
# 引擎已经在等这名玩家，但客户端一帧都没推（面板根本没送到）
client.match.decision = None
ok, why = decision_is_operable(host, client)
```

### 修复前失败

```
栈顶请求 id = 2  等的是 <玩家申>  他的客户端 decision = None
[问题在] 面板缺失时判定为可操作（当前返回 True：等待玩家申回答(beige)）
```

根因：旧实现只确认"回答者名单里有他 + match_id 一致"，**没有确认请求是否真的
送到、编号是否对应、版本是否够、状态是否还在等他答**。

### 修复后通过

```
[问题不在] 面板缺失时判定为可操作（state=waiting：房主已经把请求 #2 发出来了，
           但客户端手上还没有面板）
```

`decision_is_operable` 改成返回 `DecisionOperability`，依次确认：

1. **对局标识一致**（不是停在上一局）；
2. **房主在等谁**：引擎 `PendingRequest`（含共享响应阶段的成员）与线上决策
   登记表 `DecisionRegistry.current_for` **两边都看**——出牌阶段这类不是引擎
   请求的回合决策走后者，只看引擎会漏判；
3. **请求编号对应**：客户端手上的 `request_id` 必须等于登记表里那条；
   同时把"引擎请求编号"也带出来（`("pending", PendingRequest)` /
   `("group", request, round_id)` 两种 `local` 都解）；
4. **协议要求的版本条件**：请求带的 `base_revision` 必须已经应用，否则面板
   还被挂在"等视图"上（与 `ClientMatch._offer_decision` 同一判据）；
5. **状态**：客户端已提交等确认（`answered` / `waiting_ack`）不算可操作。

状态区分（`STATE_*`）：`idle` / `operable` / `waiting`（还没送到）/
`waiting_view`（等视图）/ `covered`（被更高优先级请求盖住）/
`answered`（已提交待确认）/ `closed`（编号过期或对不上）/
`wrong_player` / `unsynced`。

另外把两个语义分开，避免混用：
* `operable` = **此刻能提交**；
* `mine` = **这条请求归他**（可能还没送到 / 被盖住 / 已提交）。

`covered` 是这一轮新增的关键状态：Phase 14 已经实测过"被覆盖的请求答了也会
被房主拒"，所以它此刻不算"可继续操作"，等内层结束、外层被重新驱动才恢复。

### 实际验证级别

**进程内真实网络**：真实 socket + 真引擎 + 真客户端提交入口。负向输入是
"清空客户端面板 / 篡改请求编号 / 标记已提交"，以及真实的悲歌嵌套栈。
`test_nested_recovery_is_operable_and_the_owner_finishes_it` 用
`answer_via_ui`（真实点击链路）把内层、外层依次答完并断言结算收尾。

### 剩余限制

* 判据依赖登记表条目的 `local` 形状（`("pending", …)` / `("group", …)` /
  `("turn", …)`）。将来若新增第 4 种 `local` 种类，`covered` 判定会退化
  （不误报，但会漏判"被覆盖"）。
* 房主侧没有 `match`（纯引擎场景）时退化为只看引擎请求，`(operable=True,
  state=OPERABLE)` 仍会给出，但 `request_id` 为 0。
* `base_revision` 为 0 的请求跳过版本检查（旧协议字段缺省）。
* 不做"客户端面板内容是否与请求一致"的校验（例如候选牌集合），那属于
  `test_engine_v2_phase11_4_lan_playability` 的载荷契约范围。

---

## 3. 问题 3：模块调试开关失效

### 复现方法（旧实现）

```python
game = make_test_game(...)          # 先建局
invariants.enable_debug()           # 再开全局开关
game.deck.discard_pile.append(game.player.hand[0])   # 人为重复归属
game.update(1 / 60)
```

### 修复前失败

```
开关前：assert_card_ownership = False  armed_for = False
开关后：assert_card_ownership = False  armed_for = True     ← 属性与统一判定分叉
[问题在] enable_debug 对已创建对局无效（帧边界）
[问题在] enable_debug 对已创建对局无效（原子边界）
[问题不在] enable_debug 对新建对局也无效     ← 新建的能生效
[问题不在] disable_debug 之后仍在扫描        ← 关闭方向本来就对
```

根因：`Game.__init__` 在构造时把"默认值"**冻结**进 `assert_card_ownership`，
而 `Atom` 与 `Game.update` 两个入口只读这个属性、**从不问模块开关**
（`armed_for` 里写的是"模块强制 > 属性"的判定，却没人调用）。

### 修复后通过

* `Game` 的默认值改成 `None`（= "跟随全局"），不再冻结；
* 开 `enable_debug()` → 已创建对局与新建对局都立即生效，**原子边界与帧边界
  都验证**（`test_enable_debug_arms_the_atom_boundary_too`）；
* `disable_debug()` → 不做任何牌区扫描；
* `disable_debug()` 之后**按局显式** `True` 仍然生效；
* 按局显式 `False` 也说了算（能只让大部分对局受检）；
* `restore_debug()` 正确还原。

**统一后的优先级：对局显式设置 > 模块临时强制 > 环境变量。**

为什么是这个方向（而不是 Phase 14 初版写的"模块强制优先"）：
`disable_debug()` 若压掉"测试里明确按局打开"的检查，用例会在**该报错的时候
静默通过**——那正是本轮要消灭的这类缺陷。所以"按局显式设置"永远优先，模块
开关只对"没表态"的对局生效。

**"关闭时不得进行牌区扫描"是实测的**：用 spy 替换 `invariants.zone_entries`
（真正的扫描动作；不能换 `assert_card_ownership`——它是 from-import 绑进
`core` 命名空间的，patch 模块属性拦不到），关闭状态下跑 5 帧 + 1 个原子，
`zone_entries` **调用 0 次**；打开后立即被调用。

开销（`timeit` 实测）：关闭时每个边界 `armed_for` **0.107 微秒/次**，一次完整
牌区扫描 **3.9 微秒/次**（36×）。按 500 次原子/帧估算，关闭时一帧的开关判定
约 54 微秒，占 16.7 毫秒帧预算的 0.3%。

### 实际验证级别

**纯引擎**（`make_test_game` + 真实原子/帧边界），无需网络与 UI。
阶段 14 的整局审计（`tools/card_ownership_audit.py`）在本轮改动后重跑：
20 局全部打完、2944 次原子边界检查、0 处重复归属。

### 剩余限制

* 环境变量 `SANGUOSHA_ASSERT_CARD_OWNERSHIP` 在模块导入时求值一次，进程起来
  之后再设它无效（本来就是这个设计，文档已写明；测试请用按局属性或
  `enable_debug()`）。
* `armed_for` 每次边界都重新判定（这是"已创建对局也能被开关影响"的代价）。
  0.107 微秒/次是实测值；若将来要压到零成本，需要引入"开关代次 + 缓存"，本轮
  不做。

---

## 4. 问题 4：同版本检查缺失

### 复现方法（旧实现）

```python
# 只推房主、不推客户端：客户端停在旧版本
for index in range(4):
    game.message = "推进 %d" % index
    host.match.push_views(force=True)
    host_only_pump(...)
wait_for_agreement(host_only_pump, expected_of, actual_of, timeout=0.6)
```

### 修复前失败

```
房主 revision = 7  客户端 applied = 3
落后状态下的 wait_for_agreement 差异 = []          ← 落后 4 个版本却"通过"
[问题在] 客户端落后却提前通过
```

根因：`wait_for_agreement` 只反复比对**语义字段**。只要被比的字段恰好还没变
（本例改的只是 `message`），旧版本与新版本的字段完全一致，于是"字段相等"
被当成了"客户端已同步"。

### 修复后通过

```
落后状态下 wait_for_sync → None
  没能把两端定在同一个版本上：客户端落后：<match>@r7，客户端 <match>@r3（差 4 个版本）
放行客户端后 wait_for_sync → <match>@r7 已同步到 <match>@r7
```

`SyncCheckpoint = (match_id, revision, fingerprint)`，比对分三步：

1. **先让房主把当前状态发布出去**（`publish_current_state`：脏了才发，避免空转
   刷版本号）。生产代码 `send_decision` 也是先 `push_views(force=True)`
   再下发请求，这里沿用同一条口径。
2. **等客户端 `revision` 追上**，然后**复核房主的实时状态指纹仍与发布的一致**；
   房主又动了就重取检查点重来，绝不把旧版本的期望快照拿去比新版本的客户端。
3. `compare_at_checkpoint` 读数前再复核三件事：客户端已应用该版本、房主已发布
   的那版就是检查点、房主此刻指纹一致。任何一条不成立直接失败，不比字段。

**为什么必须带指纹**：`push_views` 是**节流**的（`SYNC_INTERVAL = 0.08`），两次
发布之间房主状态可以继续变而 `revision` 不动。只认版本号就会出现"版本相同、
状态早已不同"。这条修正在做测试时被真实触发过：`test_discarded_equipment_...`
在旧的"只等 revision"实现下报出了 8 处差异（弃牌堆 16 vs 0、牌堆 106 vs 115…）
——那正是"检查点描述的状态"与"读到的状态"不是同一份。为此在生产侧加了一个
只读属性 `HostMatch.published_fingerprint`。

**没有用固定 sleep 或延长超时替代同步条件**：所有等待都是"版本 + 指纹"的
可判定条件，`timeout` 只作为失败上界，超时后返回 `None` 并写明卡在哪一步。

### 实际验证级别

**进程内真实网络**（真实 socket + 真 `HostMatch.push_views` / `ClientMatch._apply_view`）。
5 个负向用例覆盖：

| 用例 | 断言的事 |
| --- | --- |
| `test_stale_client_with_equal_fields_is_rejected` | 落后 4 个版本、**字段完全相同**时：`compare_at_checkpoint` 拒绝，且失败信息点明"还没应用检查点版本" |
| `test_delayed_delivery_eventually_syncs` | 被挡住时 `wait_for_sync` 返回 `None`（理由含"落后"）；放行后能建立检查点并通过比对 |
| `test_never_delivered_is_reported_not_silently_passed` | 客户端永不推进时不静默通过 |
| `test_host_keeps_advancing_never_yields_a_checkpoint` | 房主每轮都推进时不给检查点（宁可失败也不混比版本） |
| `test_host_state_moved_after_publishing_is_rejected` | 发布之后房主又改状态（`revision` 不变、字段不变、只有指纹变）：检查点失效并拒绝；重新对齐后恢复 |
| `test_checkpoint_revision_matches_both_sides` | 正常对齐时检查点的版本与两侧**当下**版本一致 |

### 剩余限制

* "房主到位后客户端才追上、紧接着房主又推进"与"客户端一直落后"在外层看是同一
  现象，因此前一个分支只能断言"**不返回检查点 + 理由说清楚**"，不能断言
  理由一定是哪一句。确定性的那一条（指纹守卫）由单独用例覆盖。
* 检查点依赖 `HostMatch.published_fingerprint`；手工造的 host 替身若没有
  `match`，`host_checkpoint` 会直接抛错（而不是静默给个错检查点）。
* 指纹是房主本地数据，不上网，因此这套检查点只服务测试/审计，不影响协议。
* `revision` 相等即视为同步；若将来客户端出现"应用了未发布的版本"，这条判据
  需要改成严格相等。

---

## 5. 修正 Phase 14 报告里的基线矛盾

Phase 14 报告同时写了"改动之前退出码 0（1290 全过）"与"改动之前之后这个文件
都在失败名单里"。**前者是误读，已撤回**：

```bash
... python -m unittest discover ... 2>&1 | tail -25     # 没有 pipefail
```

管道退出码来自 `tail`（恒为 0），对测试结果没有证明力；输出又正好被 libpng
警告刷满，汇总行没留下。所以 **"改动前 1290 全过"不成立**。

Phase 14 能拿出来的基线证据只有：加了（当时是空操作的）钩子之后的一次全量跑
= 1290 个 / 1 失败。归因口径统一为：

* 那份不稳定文件是真实 socket + 随机武将的联机用例，多次观测里**失败用例每次
  都不一样**（`test_scenario_h_humans_and_ai_rotate_for_two_rounds` ↔
  `test_scenario_h_remote_player_receives_its_own_turn_again` ↔
  `test_scenario_h_remote_player_really_plays_through_the_bridge` ↔
  `test_local_human_must_discard_before_ending_an_over_limit_turn`），失败断言
  也在换（『没有走完两轮』→『没有收到出牌阶段请求』）；
* 该文件单独连跑两次都是 41/41 全过；
* 本轮全量回归（顺序执行、无并发）**1350 个全过**，这个文件也过了。

**归因状态**：既有不稳定（有上述重复观测支撑）。但"改动前它一次都没失败过"
这条没有可靠基线可证，因此**不断言"与本次改动绝对无关"**——只陈述"本次改动在
关闭时是实测的空操作（0 次调用），且该文件表现为随机失败"。

---

## 6. 实际测试结果

### 修复前后对照（同一台机器、同一套命令）

| 命令 | 修复前 | 修复后 |
| --- | --- | --- |
| 探针脚本（四个问题逐一取证） | 4/4 复现 | 4/4 不再复现 |
| `python -m unittest tests.test_view_consistency_tooling` | 文件不存在 | **29 个全过** |
| `python -m unittest tests.test_view_consistency_lan` | 9 个全过（旧接口） | **9 个全过**（检查点式比对） |
| `python -m unittest tests.test_card_ownership_invariants` | 22 个全过 | **22 个全过** |
| `python tools/card_ownership_audit.py` | 20 局 / 3621 次检查 / 0 重复 | 20 局 / **2944** 次检查 / **0 重复** |
| `python -m unittest discover -s tests -t .`（顺序、无并发） | 1321 个 / 2 失败（不稳定文件） | 见下：**1350 个**，两次运行分别 0 失败 / 1 失败 |

> 审计那一行的两个数字都只对**自己那一次运行**成立：AI 对局并不完全可复现
> （每个 `AIController` 自带无种子的随机源，`Game` 的种子只覆盖洗牌与选将），
> 所以两次运行的状态变更次数本来就会不同。这里要说明的是"20 局都真的打完了、
> 0 处重复归属"，而不是"次数没变过"。

#### 最终全量回归：两次运行，同一份代码，结果不同

| 运行 | 结果 |
| --- | --- |
| 第一次（顺序、无并发） | **1350 个全过**（`EXIT=0`） |
| 第二次（同一份代码，顺序、无并发） | 1350 个，**1 失败**：`test_scenario_h_humans_and_ai_rotate_for_two_rounds`（`tests/test_engine_v2_phase11_4_3_lan_identity_runtime.py:513`） |
| 失败用例单独复跑 | **3/3 全过** |

第二次的失败断言原文：

```
AssertionError: 1 not greater than or equal to 2 : 房主 没有完成两轮行动
```

这就是那条不稳定用例：同一份代码两次跑结果不同，单独跑又全过。**归因：既有
不稳定**（详见 §5）；但按同一口径，**不再断言"与本次改动绝对无关"**——只能说
"本次改动在关闭时是实测的空操作，且该用例在多次观测里表现为随机失败"。

1350 = 基线 1290 + Phase 14 的 31（22 + 9） + Phase 14.1 的 29。

用例分布（`tests/test_view_consistency_tooling.py`）：

| 类 | 数量 | 覆盖 |
| --- | --- | --- |
| `CardFaceKindTests` | 6 | 三档分类（含两种自相矛盾数据） |
| `HiddenInfoNegativeTests` | 3 | 注入泄露（网络解析视图 / 适配层）、合法匿名不误报 |
| `DecisionOperabilityTests` | 6 | 缺失面板 / 不归他答 / 被覆盖 / 旧编号 / 待确认 / 恢复后真实答完 |
| `SwitchSemanticsTests` | 8 | 已建局与新建局 / 两个边界 / 关闭不扫描 / 按局启用 / 恢复 |
| `SyncCheckpointTests` | 6 | 旧版本同字段 / 延迟送达 / 从未送达 / 房主推进 / 指纹守卫 / 版本一致 |

### 测试级别（如实分级）

| 级别 | 本轮的用例 | 是否实际执行 |
| --- | --- | --- |
| 纯引擎 | `SwitchSemanticsTests`（8）、`tools/card_ownership_audit.py` | **是** |
| 进程内消息（不联网） | 无新增 | — |
| 真实本机 socket（同进程） | `HiddenInfoNegativeTests`（3）、`DecisionOperabilityTests`（6）、`SyncCheckpointTests`（6）、原有 LAN 9 个 | **是** |
| 独立多进程（多个 `main.py`） | 无新增（既有 `tools/runtime_parity_run.py` 属这一级，本轮未涉及） | 否 |
| 真实双机 | 本机只有一台电脑 | **未验证** |

### 接口变更（调用方需要知道）

* `decision_is_operable(host, client)` 返回值由 `(bool, str)` 改成
  `DecisionOperability`（`.operable` / `.state` / `.note` / `.mine` /
  `.request_id` / `.engine_request_id`）。
* `wait_for_agreement(pump, expected_of, actual_of)` **已删除**（它就是问题 4
  的载体）。替代：`wait_for_sync(pump, host, client)` +
  `compare_at_checkpoint(...)`；封装好的入口是
  `agree_or_fail(case, pump, host, client, player_id, ...)`。
* `expected_for` / 两份客户端快照新增 `hand_faces` 字段（期望侧恒为 `()`）。
* 新增公开入口：`card_face_kind`、`hand_face_kinds`、
  `unauthorized_hand_faces`、`host_checkpoint`、`client_checkpoint`、
  `host_state_matches`、`publish_current_state`、以及
  `FACE_*` / `STATE_*` 常量。
* 生产侧：`HostMatch.published_fingerprint`（只读）、
  `Game.assert_card_ownership` 默认值由布尔改为 `None`（含义"跟随全局"）。

---

## 7. 怎么用

```bash
# 1. 本轮的负向 / 语义测试（纯引擎 + 进程内真实 socket）
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest tests.test_view_consistency_tooling

# 2. 真实场景（检查点式比对）
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest tests.test_view_consistency_lan

# 3. 开关语义 + 归属检查（含多种子整局审计）
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest tests.test_card_ownership_invariants

# 4. 全量回归（单独跑，别与其他测试并发）
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest discover -s tests -t . -p "test_*.py"
```

写新场景时：

```python
from tools.view_consistency import agree_or_fail, assert_hidden, decision_is_operable

# 对齐到同一版本再比语义字段
agree_or_fail(self, session.pump, session.host, client, player.player_id,
              note="拆掉装备之后")

# 隐藏信息：按玩家视角，卡牌三档会自动写进失败信息
assert_hidden(self, other_client, victim.player_id, [card.id for card in victim.hand])

# 现在能不能继续操作（区分"还没送到 / 被覆盖 / 已提交 / 已过期"）
state = decision_is_operable(session.host, client)
self.assertTrue(state.operable, state.describe())
```
