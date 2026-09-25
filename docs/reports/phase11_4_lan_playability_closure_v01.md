# Phase 11.4：LAN Playability Closure（联机可玩性闭环）

日期：2026-09-20
结论：**完成**（A～L 十二项完成标准全部达成；已知限制见 §20）

配套文档：

* 架构审计：`docs/reports/phase11_4_ui_interaction_audit_v01.md`
* 交互矩阵：`docs/reports/phase11_4_remote_interaction_matrix_v01.md`

---

## 1. Phase 11.4 是否完成

**完成。** 逐条对照 §28 的完成标准：

| | 标准 | 结果 |
|---|---|---|
| A | LAN Host 与 Client 的 UI 恢复到原成熟 UI 风格 | ✅ 同一套 Renderer + 同一套提示/按钮逻辑；对照图见 §18 |
| B | 常规决策不再依赖巨大 Remote Decision Panel | ✅ 该面板已删除；只剩一个"选牌数量不足"级别的小字提示 |
| C | 过河拆桥 Remote→Host / Host→Remote 都能工作 | ✅ 两条方向都有真实点击用例（场景 2 / 3 / 5） |
| D | 顺手牵羊 Remote→Host / Host→Remote 都能工作 | ✅ 场景 4 + 房主侧本地 UI 路径 |
| E | 隐藏手牌不泄露真实 card_id / 牌面 | ✅ 不透明占位 token + 原始载荷审计（场景 10） |
| F | 涉及真人决策的技能 Remote Human 全部可操作 | ⚠️ 19 / 20 完全可操作 +【观星】1 项显式降级（见 §10、§20）；另外 20 个是自动结算或纯被动 |
| G | 卡牌复杂交互 Remote Human 全部可操作 | ✅ 全部落在 6 种决策类型上；8 条有端到端点击用例 |
| H | Host 与 Client 使用统一的 presentation / interaction 架构 | ✅ 客户端把决策请求翻译成本地引擎同构的交互槽位 |
| I | Client 仍然没有完整 Game | ✅ 客户端只有 `ClientGameView` + `RemoteGameView`（只读） |
| J | 隐藏信息 socket payload 审计继续通过 | ✅ 场景 10 + Phase 11.3 的 48 / 87 项全部通过 |
| K | 旧测试继续通过 | ✅ 1044 项全通过（基线 1012 + 新增 32） |
| L | 新增 LAN playability 测试全部通过 | ✅ `lan_playability_sync` 58/58、新单测 32 项 |

## 2. UI 为什么之前会退化

一句话：**"能算规则"和"要画界面"被绑成了同一个开关**。

`Renderer.local_interaction(game)`（`src/renderer.py`）本来的含义是"这是一份只读
视图，不许算距离、不许提交动作"。但它在 `Renderer.draw` 里同时被用来关掉
**提示条 / 固定按钮 / 技能选择 / 出牌方式面板**这一整层界面
（原 `src/renderer.py:729-753`）。于是联网客户端为了能显示“现在要你做什么”，
只能在 `RemoteTableScene` 里**另画一套**：自配按钮文案（"确认/取消/打出/不出"）、
自造提示条（`PromptInfo("network", …)`，不传 accent → 永远亮金边框）。

三条具体的退化机制：

1. **提示条永远是全亮金**：本地 `prompt.describe` 会按状态换色
   （出牌 GOLD_BRIGHT / 选牌 TARGET_BLUE / 响应 DANGER / 等待 TEXT_DIM）；
   客户端那条永远是默认金色 → 又宽又空的"大黄色框"。
2. **按钮文案是第二份名单**：客户端 `BUTTON_LABELS` 与引擎 `_button_state`
   各自演化，于是出现"巨大确认/取消"这种与桌面无关的按钮。
3. **Phase 11.2 的联机专用面板还在仓库里**：`src/ui/remote_game.py` 的
   `RemoteGameScreen`（1180×780 整块金边面板 + 底部确认/取消）。虽然
   `LanScene` 已改用它，但它作为"联机 UI"的形态留在代码里，成为后来者复制
   的目标。本阶段**已删除该文件**。

## 3. 最终 Presentation 架构

```
                    房主（唯一权威）
        ┌───────────────────────────────────────────┐
        │ Game + Engine + TurnFlow + 技能 + AI        │
        └───────────────────────────────────────────┘
              │                          │
   本地真人：引擎原生交互状态      远程座位：RemoteHumanController
   pending_selection /                 │  DecisionRequest（纯数据）
   pending_target_selection /          │  · cards：候选（隐藏手牌＝opaque token）
   pending_skill_input /               │  · targets：合法角色
   response / choice / prompt          │  · constraints：数量 / 可取消 / 可发动技能
              │                                  │
              ▼                                  ▼
        ┌──────────────────────────────────────────────────┐
        │ Renderer + prompt.describe + actions_for          │
        │  （**只有一份**：提示条、固定按钮、座位高亮、      │
        │   公共区选牌、出牌方式面板、技能栏、ChoiceOverlay）│
        └──────────────────────────────────────────────────┘
                                 ▲
                          同一个接口面
                                 │
                     src/ui/decision_presentation.py
                （决策请求 → 本地同构交互槽位，纯数据映射）
                                 ▲
                                 │
                     src/ui/view_adapter.RemoteGameView
                 （只读视图 + 交互槽位，local_interaction=False,
                   interaction_layers=True）
```

关键代码：

* `src/ui/decision_presentation.py`（新增）：`SELECT_CARDS → pending_selection`、
  `SELECT_TARGETS / PLAY_PHASE → pending_target_selection`、
  `RESPOND_CARD → response`、`CONFIRM/CHOOSE_OPTION → choice`、
  `PLAY_PHASE → card_action_picker / pending_card_action / pending_skill_input`。
  它**不算距离、不判合法性、不造虚拟牌**。
* `RemoteGameView.apply_decision(request, selection)`：把上一步的结果写进视图的
  同名字段，于是 `prompt.describe` / `Renderer.actions_for` / `TableLayout` /
  `seats` / `player` 全部照常工作。
* `Renderer.interaction_layers(game)`（新增）：与 `local_interaction` **正交**的
  界面层开关。单机 = 两者都真；客户端 = 界面真、规则假。

## 4. Host / Client UI 是否统一

**统一。** 三种状态的界面证据：

* `tools/ui_snapshots/phase11_4_parity1_local_play_phase.png`（单机出牌阶段）
* `tools/ui_snapshots/phase11_4_parity2_host_play_phase.png`（LAN 房主）
* `tools/ui_snapshots/phase11_4_parity3_client_play_phase.png`（LAN 客户端）

三张图的提示条文案逐字相同（"出牌阶段 / 点击手牌使用，或点击「结束回合」"）、
固定按钮相同（"结束回合" 可用 + "取消" 不可用）、投降按钮、牌堆/弃牌/阶段带/
战报/手牌区灰化规则、座位面板位置与样式全部一致。

自动化断言（`tools/lan_ui_parity_snapshots.py`，6/6）：

* 客户端提示与单机**逐字一致**（同一份 `prompt.describe`）；
* 客户端固定按钮与单机同构（同一份 `Renderer.actions_for`）；
* 客户端 `local_interaction=False` 且 `interaction_layers=True`。

## 5. Remote Decision Panel 如何处理

| 组件 | 处置 |
|---|---|
| `src/ui/remote_game.py`（`RemoteGameScreen`） | **删除**（无任何引用） |
| `RemoteTableScene._draw_prompt` / `_configure_buttons` / `_draw_decision` | **删除**，改用 `prompt.describe` + `Renderer.actions_for`（由 `Renderer.draw` 直接画） |
| `RemoteTableScene._draw_action_banners`（屏幕中央"正在等待 X 操作"） | **删除**（提示条已经说明，本地也没有这一层） |
| 选项行 / 出牌方式行 | 删除；改用本地的 «CardActionPicker»（"选择操作"面板）与本地的 «ChoiceOverlay»（是/否模态） |
| 结果遗留 | 只剩一个 **一行小字**（`RemoteTableScene._draw_notice`）：用于"已提交，等待房主结算""这张牌房主没有列为现在可用"这类必须说的本地提示，贴在提示条上沿，不占主画面 |

## 6. 过河拆桥 bug 根因

链路（详见审计 §附）：

1. 房主侧**完全正确**：`过河拆桥` 创建 `PendingRequest(SELECT_CARDS, target=远程玩家,
   zone_owner=受害者, candidates=受害者的手牌+装备)`，`RemoteHumanController` 把它
   转成带候选的 `DecisionRequest` 发出去；
2. 客户端的 `RemoteGameView.pending_selection` **只能**来自 `view.selection`，而
   `build_selection_view` 只为**房主本地**的 `pending_selection` 生成
   （`src/game/view/view_builder.py:209`）——远程玩家永远收不到；
3. 于是客户端的桌面公共区**没有任何候选可画**，投影到玩家眼里就是"无处可点"；
4. 玩家只能去点目标角色的座位，而那条请求没有 targets →
   `_toggle_target` 打印了误导性的"房主没有把这个角色列为合法目标"。

判定：属于审计 §11 的 **C + G**（三方信息没对齐 + 跨区域第二阶段没有表现层）。
**不是** player_id 映射错误、不是 revision 竞态（`DecisionRegistry` 一直是对的）。

修复：把**决策请求**当成唯一的表现来源，映射成 `pending_selection`
（zone = `public_pool`，候选 = 请求里的 `cards`），并让 `selection_pool_entries()`
把它交给既有的公共区绘制/命中代码；同时补上目标高亮（`pending_target_selection`）。

## 7. 顺手牵羊结果

与过河拆桥同源，已一并修复并验证：

* 场景 4（远程 → 房主隐藏手牌）：界面点牌背 → 房主执行 → 牌真的到了远程玩家手里；
* 房主侧本地 UI 路径（场景 5 反向）不变；
* Phase 11.3 的 `lan_presentation_sync` 场景 12 已按新契约更新（见 §15）。

## 8. hidden hand opaque choice 方案

**格式**：`hidden:<request_id>:<index>`（`src/game/controllers/remote.py`）。

* 只对"从别人**手牌**里选"的候选生效（过河拆桥 / 顺手牵羊 / 火攻展示 / 寒冰剑 /
  麒麟弓 / 雌雄弃牌 / 观星之外的所有跨手牌选择）；
* **装备区、公共牌池、判定区、自己的手牌照常发真实 `card_id`**（本来就是公开信息）；
* token 里不含牌名、花色、点数，也不含真实卡片 id 的任何片段，
  且**只在一条请求内有效**（`hidden:<req>:<i>` 换一条请求就换一批 token），
  因此客户端无法跨快照对同一张隐藏牌做关联；
* 房主把 `token → 真实 Card` 的映射留在自己的内存里
  （`RemoteHumanController._opaque`，只保留最近 8 条请求）；
* 回答回来时 `_resolve_card_token(request_id, token)` 先查映射表再按 id 兜底，
  查不到就拒答；`DecisionRegistry` 仍然按请求的允许集合做纯数据校验（越权/重复/
  数量不符一律拒绝）。

客户端侧：候选带 `face_down=True` 与 `owner_id`，`RemoteGameView` 把
`face_down` 的牌构造成"只有 id、没有牌面"的展示卡并登记进
`selection_face_down_ids()`，`table.draw_pool` 于是只画牌背。

## 9. player_id / seat / name 如何统一

审计结论：**协议里一直只用稳定 `player_id`**（`player_entry()` / `card_entry()` /
`DecisionRequest.target_ids`），名字与座次只作为展示字段下发。本阶段做了三件事：

1. 候选牌**新增 `owner_id`**（此前只有 `owner_name`），客户端据此判断"这些候选
   是不是我自己的"（决定画在手牌区还是桌面公共区），不再靠名字猜；
2. 目标解析统一走 `RemoteGameView.player_by_id()`，查不到的角色**直接忽略**
   （测试 `test_unknown_player_id_is_ignored_instead_of_crashing`）；
3. 补了确定性测试：`DecisionRevisionRaceTests`（4 项，名字/座次无关的 id 语义）与
   `test_select_targets_lists_only_host_given_candidates`。

房主非 0 座位、客户端非 1 座位的情形由 `MatchSession` 的真实对局覆盖
（房主 = 座次 0，客户端 = 座次 1，但所有请求/回答都用 `player_id`；
`lan_view_sync` 场景 4 覆盖 3 客户端）。

## 10. 40 技能 Remote Interaction Matrix 最终统计

完整表见 `phase11_4_remote_interaction_matrix_v01.md`。

* **40 个技能**：主动技 9 / 视为技 6 / 阶段替代 3 / 改判 1 / 触发式选目标 1 /
  自动结算或纯被动 20。
* **涉及真人决策的 20 个：19 个 COMPLETE，1 个 PARTIAL。**
  * COMPLETE（19）：反间、鬼才、国色、结姻、激将、急救、克己、苦肉、离间、
    流离、龙胆、裸衣、倾国、青囊、奇袭、仁德、突袭、武圣、制衡；
  * **PARTIAL（1）：【观星】**。它的排序交互写在
    `game.start_card_selection`（`src/game/skills/standard/shu.py:318`）——
    那是**只有本地真人界面才有**的通道，不会被翻译成 `DecisionRequest`。
    远程真人点它只会得到一个永远等不到答案的窗口，所以房主在下发列表里
    **列出但禁用**并写明原因（`SkillDef.needs_local_ui`）。
    客户端的技能栏会显示这个原因，玩家不会"点了没反应"。
    修法明确：把它的选牌改成 `PendingRequest`（照【突袭】开一个小 Flow），
    然后去掉标记；留给 Phase 11.5。
* **本阶段新增的能力**：出牌阶段"发动主动技"（9 个主动技此前在客户端**根本没有
  入口**）。实现方式是把 `activatable` 列表放进同一条出牌阶段请求
  （`constraints.activatable`，含 `needs_target` / `cost_cards` /
  `variable_cost` / `targets` / `cost_candidates`），并新增回答动作
  `use_skill`；房主用 `skills.activate(...)` 执行，与 AI、本地真人**同一条**
  校验/支付/结算路径。
* 一点重要事实：本项目的【天妒】【遗计】【刚烈】【反馈】都是**自动结算**
  （`src/game/skills/standard/wei.py`），不产生真人决策，矩阵中标 `N/A`。

## 11. 卡牌 Remote Interaction 覆盖情况

19 张基本/锦囊 + 装备相关交互全部落在同一批决策类型上（详见矩阵 §二）：

* 出牌阶段（选目标 / 无目标 / 多目标）：`PLAY_PHASE` → 手牌点击 + 座位高亮；
* 响应窗口（闪 / 桃 / 无懈 / 决斗连续杀 / 南蛮万箭逐人）：`RESPOND_CARD`
  → 手牌高亮（本阶段新增：响应窗口也走"房主给的高亮集合"）；
* 跨区域选牌（拆 / 顺 / 火攻 / 五谷）：`SELECT_CARDS` → 桌面公共区点击；
* 多段流程（借刀杀人的两段、铁索的多选）：`SELECT_TARGETS` → 座位多选。

**端到端真实点击验证**：杀、闪、桃（濒死）、过河拆桥（装备区/隐藏手牌）、
顺手牵羊、五谷丰登、鬼才改判、流离选目标、制衡发动、弃牌多选 —— 共 10 条。

## 12. Host / Client 对称验证

| 流程 | Host 为发起者 | Remote Client 为发起者 |
|---|---|---|
| 过河拆桥 | 场景 5（房主本地 UI 拆远程） | 场景 2 / 3（远程拆房主） |
| 顺手牵羊 | Phase 11.3 场景 12（房主发起） | 场景 4（远程发起） |
| 杀 + 响应 | 场景 1 / 6a / 6b（远程出杀、房主出杀给远程响应） | 同上（双向都点到） |
| 五谷丰登（多人依次取牌） | 场景 9（房主发起，远程取牌） | — |
| 结束回合 / 弃牌 | 场景 6c（远程结束并弃牌） | — |

没有出现"Host 可以、Client 不可以"或反过来的情况。

## 13. Hidden-info 审计结果

* **结构化审计**（`tools/lan_view_harness.ClientSide.audit_hidden_cards`，逐条走原始
  socket 载荷）：玩家甲看不到玩家乙与房主的隐藏手牌内容；第三方也看不到甲的；
  本人照常看得到自己的手牌（场景 10，4/4）。
* **token 契约**：拆/顺的选牌请求中，隐藏手牌的 `card_id` 全部是
  `hidden:<req>:<i>`，与真牌 id 集合**零交集**；`name` / `suit` / `rank` 全空。
* **表现事件**：别人摸牌 / 被拿牌只收到 `hidden_count`，`cards` 为空数组
  （Phase 11.3 场景 12 的既有断言继续通过）。
* 结论：**原始 socket payload 里不出现其他玩家隐藏手牌的 card_id / 牌名 /
  花色 / 点数**；opaque token 按设计允许出现。

## 14. Revision / Decision race 结果

* 房主：`send_decision()` 先 `push_views(force=True)` 再带 `base_revision` 发请求
  （Phase 11.3 已有，本阶段未改）。
* 客户端：`ClientMatch._offer_decision` 在 `view.revision < base_revision` 时
  **把请求挂起**，视图到位后 `_flush_held_decision` 才开放面板；`DECISION_CANCELLED`
  会清掉挂起与当前请求（不卡死）。
* 本阶段新增 4 条确定性测试（`DecisionRevisionRaceTests`）：请求必须等自己的快照、
  快照已到位时立刻开放、答案只能发一次、被撤回的请求不会留下死锁。
* 旧请求的迟到回答：`DecisionRegistry` 以 `request_id` 判定，
  `already_resolved` / `unknown_request` 一律拒绝且不影响新请求
  （`test_chained_requests_invalidate_the_old_one`）。
* `tools/lan_view_sync.py` 场景 6/7（revision gap → 重同步、决策前先刷新视图）
  48/48 通过。

## 15. 新增测试

**单元测试**：`tests/test_engine_v2_phase11_4_lan_playability.py`（32 项）

* `SelectionPresentationTests`：跨区域选牌 → 公共区 / 自己的手牌 / 装备槽；
  隐藏候选的牌背标记；选中标记。
* `TargetPresentationTests`：只认房主给的目标；未知 id 直接忽略。
* `RemoteViewOverlayTests`：视图上**真的出现**可点的候选（过河拆桥回归）、
  座位高亮、响应层、出牌阶段的按钮语义、多方式面板、确认模态、主动技输入层。
* `OpaqueTokenTests`：token 不含身份信息、隐藏候选全部是 token、
  房主能换回真牌、不属于本请求的 token 解析失败、装备区仍发真实 id。
* `SkillActivationProtocolTests`：`use_skill` 的合法/越权/数量/目标/阶段校验。
* `RemotePlayPhaseActivationTests`：出牌阶段请求里真的列出了主动技与它的
  目标候选、费用候选；以及**需要本地通道的技能必须"列出但禁用并写明原因"**
  （观星，见 §10）。
* `StaleResponseTests` / `DecisionRevisionRaceTests`：陈旧回答与 revision 竞态。

**真实双实例工具**：`tools/lan_playability_sync.py`（58 项检查，全部通过）
—— 见 §23 的场景表。它**只通过鼠标点击**做决定（不调用 `match.answer`），
这是本阶段与 Phase 11.3 工具最重要的区别。

**视觉对照工具**：`tools/lan_ui_parity_snapshots.py`（6 项检查）。

**旧工具更新**：`tools/lan_presentation_sync.py` 场景 12 的断言按新的
"不透明 token"契约重写（协议层不再传真实 id，改为验证"房主换回真牌、获得者拿到
之后才知道是哪张"）。这是契约升级，不是删除失败测试。

## 16. 全量测试结果

```
python -m unittest discover -s tests -t .     →  Ran 1044 tests  OK
python -m compileall main.py src              →  通过
```

* 历史基线 **1012**，本阶段新增 **32** 项（全部在新文件中），总数 **1044**。
  没有任何旧测试被删除或跳过。

## 17. 双进程测试结果

| 工具 | 结果 |
|---|---|
| `tools/lan_playability_sync.py`（本阶段新增） | **58 / 58** |
| `tools/lan_ui_parity_snapshots.py`（本阶段新增） | **6 / 6** |
| `tools/lan_view_sync.py` | 48 / 48 |
| `tools/lan_presentation_sync.py` | 87 / 87 |
| `tools/lan_gameplay_ui.py` | 23 / 23 |
| `tools/lan_gameplay_bridge.py` | 41 / 41 |
| `tools/lan_smoke.py` | 44 / 44 |
| `tools/lan_two_process.py`（真实两个进程 + 真实 main.py） | 11 / 11 |

`lan_playability_sync` 覆盖的场景（每个都是一局新对局、真实 socket、真实
Renderer、真实鼠标点击）：

| 场景 | 覆盖 |
|---|---|
| 1 | 远程出【杀】（手牌点击 + 座位点击，自动提交） |
| 2 | 远程【过河拆桥】→ 房主（装备区真实 id / 手牌牌背，界面点到装备并弃置） |
| 3 | 远程【过河拆桥】→ 只有隐藏手牌（盲选，真牌减少一张） |
| 4 | 远程【顺手牵羊】→ 隐藏手牌（牌到手） |
| 5 | 房主【过河拆桥】→ 远程（反向；本地 UI 路径未被破坏） |
| 6a | 远程响应【闪】（提示条 = "需要你的响应"，手牌高亮，打出成功） |
| 6b | 远程濒死求桃（自救成功） |
| 6c | 远程弃牌阶段（多张选牌，数量提示"还需选择 N 张"） |
| 6 | 远程发动【制衡】（技能栏可点 → 选费用 → 确认发动） |
| 7 | 远程【流离】→ 座位点击改目标 |
| 8 | 远程【鬼才】改判（点自己的手牌替换判定牌） |
| 9 | 五谷丰登（多人在公共区依次取牌） |
| 10 | 隐藏信息审计（原始 socket 载荷） |
| 11 | 单机 / 远程 界面文案逐字一致性 |

## 18. UI screenshot 路径

`tools/ui_snapshots/`（全部为本阶段自动生成的真实截图，1600×1000）：

| 文件 | 内容 |
|---|---|
| `phase11_4_parity1_local_play_phase.png` | 单机·出牌阶段（对照） |
| `phase11_4_parity2_host_play_phase.png` | LAN 房主·出牌阶段（对照） |
| `phase11_4_parity3_client_play_phase.png` | LAN 客户端·出牌阶段（对照） |
| `phase11_4_scene1_client_sha.png` | 客户端出【杀】后的结算过程 |
| `phase11_4_scene2a_client_guohe_target.png` | 过河拆桥：目标选择（座位箭头） |
| `phase11_4_scene2_client_guohe_equipment.png` | 过河拆桥：选牌阶段（装备 + 牌背） |
| `phase11_4_scene3_client_guohe_hidden.png` | 过河拆桥：只有隐藏手牌时的盲选 |
| `phase11_4_scene4_client_shunshou.png` | 顺手牵羊之后（牌已到手） |
| `phase11_4_scene6a_client_shan.png` | 响应【闪】（"需要你的响应"） |
| `phase11_4_scene6b_client_dying_rescue.png` | 濒死求桃 |
| `phase11_4_scene6c_client_discard.png` | 弃牌阶段多选 |
| `phase11_4_scene6_client_zhiheng.png` | 发动【制衡】 |
| `phase11_4_scene7_client_liuli_targets.png` | 【流离】改目标 + 技能栏 |
| `phase11_4_scene8_client_guicai.png` | 【鬼才】改判 |
| `phase11_4_scene9_client_wugu.png` | 五谷丰登 |
| `phase11_4_scene11_client_play_phase.png` | 出牌阶段（文案一致性用例） |

判定面板 / 鬼才改判面板的可视化在 Phase 11.3 已有截图
（`phase11_3_scene13_judge.png` / `phase11_3_scene14_guicai.png`），本阶段未改动
那条链路。

## 19. 修改的主要文件

**新增**

* `src/ui/decision_presentation.py` — 决策 → 桌面交互槽位 的纯数据映射
* `tests/test_engine_v2_phase11_4_lan_playability.py` — 32 项确定性测试
* `tools/lan_playability_sync.py` — 真实点击驱动的联机可玩性 harness
* `tools/lan_ui_parity_snapshots.py` — 单机/房主/客户端 视觉对照
* `docs/reports/phase11_4_ui_interaction_audit_v01.md`
* `docs/reports/phase11_4_remote_interaction_matrix_v01.md`
* `docs/reports/phase11_4_lan_playability_closure_v01.md`（本文）

**修改**

* `src/ui/remote_table.py` — 交互层重写：删除自造面板，改为"决策 → 槽位 → 点击
  → DecisionResult"；坐标命中也统一走既有 Renderer 查询
* `src/ui/view_adapter.py` — `apply_decision` 及配套同构对象
  （`PendingSlot` / `ResponseRequest` / `ChoiceRequestView` / `CardWayOption`）、
  `skill_activation_state`、`display_card`、`selection_face_down_ids`、
  `interaction_layers`
* `src/renderer.py` — 新增 `interaction_layers()`，提示条/按钮/选择面板改由它把关
* `src/ui/player.py` — 客户端的手牌高亮改由房主集合决定（响应窗口也生效）
* `src/ui/skill_bar.py` — 只读视图下"能不能按"改为问 `skill_activation_state`
* `src/game/controllers/remote.py` — 不透明 token、`owner_id`、
  出牌阶段 `activatable` 列表、`use_skill` 落地、弃牌候选带 owner/zone
* `src/network/decisions.py` — 新增 `ACTION_SKILL` 与主动技校验
* `src/game/skills/definitions.py` — 新增 `SkillDef.needs_local_ui` 与
  `active(..., needs_local_ui=)`：声明"这个技能的交互还挂在本地通道上"
* `src/game/skills/standard/shu.py` — 【观星】声明 `needs_local_ui=True`
* `tools/lan_view_harness.py` — `snapshot(name, prefix=...)`
* `tools/lan_presentation_sync.py` — 场景 12 按新契约更新

**删除**

* `src/ui/remote_game.py`（Phase 11.2 的联机专用面板，已被统一交互取代）

## 20. 当前仍存在的限制

1. **丈八蛇矛的多来源转化**：客户端已支持（`min_sources` / 多选），但没有端到端
   点击用例；"点武器牌发动"这条本地专用入口在客户端**没有**等价物
   （客户端只能通过手牌的多来源方式使用）。
2. **【观星】的排序尚未远程化**（详见 §10）：房主显式降级为"不可远程发动"，
   不会卡死，但远程诸葛亮暂时用不了这个技能。
3. **逐卡端到端覆盖不均**：决斗、南蛮、万箭、桃园、无中生有、借刀、火攻、铁索、
   乐/兵粮/闪电的判定链、装备技确认——它们的**决策类型**都有测试与真实点击用例
   覆盖，但每张牌各写一条端到端用例还没有做（Phase 11.3 的桥接工具 + 本阶段
   的类型覆盖是现有证据）。
4. **确认模态标题**：房主本地对 `CONFIRM` 一律显示"装备技能"（历史写法，对
   【突袭】这类阶段替代不够准确），客户端显示"请确认"。这是**有意保留**的差异
   （改本地文案会影响既有单机 UI 断言），提示正文完全相同。
5. **仁德 / 结姻 / 青囊 / 反间 / 离间 / 激将 / 苦肉**这 7 个主动技：走的是与
   【制衡】完全相同的 `activatable → pending_skill_input → use_skill` 通道
   （代码路径共用），但只有【制衡】有端到端点击用例。
6. **投降 = 退出对局**：客户端"投降"与本地同文案、同二次确认，但语义仍是
   "离开房间"（Phase 11.4 不做托管/重连）。
7. **判定面板的客户端截图**沿用 Phase 11.3 的产物（链路未改）。

## 21. 下一阶段建议

1. **观星远程化**：把【观星】的排序改成 `PendingRequest(SELECT_CARDS)`
   + 一个小 Flow（照 `TuxiFlow` 的写法），去掉 `SkillDef.needs_local_ui`；
   客户端侧不需要新代码——公共区选牌那条路已经通了。
2. **逐卡端到端用例补齐**：给 `tools/lan_playability_sync.py` 增加"卡牌矩阵驱动"
   的用例（每张牌一条：真实点击 → 断言房主权威结算），把 §20-2 的缺口清零。
3. **丈八蛇矛 / 武器技入口**：在客户端技能栏或装备槽上提供"用武器发动"的入口，
   或明确在出牌阶段请求里给出"牌 + 武器"的组合选项。
4. **掉线 AI 托管 + 重连**（Phase 11.5）：`RemoteHumanController.waiting` 与
   `HostMatch.recover_stalled_decisions` 已经为"判定谁在等"打了地基。
5. **确认模态标题收敛**：把 `HumanController` 的 CONFIRM 标题改为按
   `request.context["reason"]` 取值（"发动【八卦阵】"/"发动【突袭】"），
   同时更新旧断言——一次把单机与联机的文案对齐且更准确。
6. **牌堆顶排序的桌面表达**：观星远程化之后，可以做成"牌堆顶排序"的专门表现
   （本地与远程同时受益）。
