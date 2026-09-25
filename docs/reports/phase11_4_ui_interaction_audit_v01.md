# Phase 11.4 §3：LAN UI / 交互架构审计

审计时间：2026-09-20。对象是**当时的真实代码**（Phase 11.3 结束时的状态），
不是设计稿。以下每条结论都给出了文件与行号级的事实依据；本阶段的修复见
`phase11_4_lan_playability_closure_v01.md`。

本次审计的触发事件：第一次真实双电脑联机时出现两个问题——

1. **UI 退化**：Host 与 Client 两边都出现"底部巨大黄色决策框 / 右侧巨大确认取消 /
   像 debug 面板"的观感；
2. **过河拆桥无法拆房主**：客户端提示"请选择 wuhuo 区域内的一张牌"后，点击目标
   角色却得到"房主没有把这个角色列为合法目标"，无法继续操作。

---

## 1. 单机模式 Renderer 读取 Game 的哪些属性？

`Renderer` 与它调用的 UI 模块把**权威 Game 当成一份纯数据源**，属性面很大但全部
是只读查询。按用途分四类：

**A. 表现数据**（画什么）
- `game.players` / `game.player` / `game.current_turn_player` / `game.current_player_id`
- `game.player.hand`、`player.get_equipment(slot)`、`player.judgement_zone`、
  `player.chained` / `.hp` / `.max_hp` / `.alive` / `.seat` / `.name` / `.general_id`
- `game.generals.get(id)`、`game.skills.skill_ids_of(player)`、`game.skill_registry`
- `game.deck.draw_pile` / `.discard_pile`、`game.public_card_pool`、`game.table_cards`
- `game.game_log`、`game.message`、`game.phase`、`game.result`、`game.game_over`、
  `game.mode`、`game.match_id`
- `game.deal_presentation`、`game.actions`（动画队列）、`game.ui_rects`、
  `game.ui_metrics`（后两个是 Renderer **写入**的）

**B. 交互状态**（能点什么、按钮什么文案）
- `game.pending_selection` / `pending_target_selection` / `pending_skill_input` /
  `pending_skill_picker` / `pending_card_action` / `pending_view_as`
- `game.response.active` + `game.response.current.{prompt,allowed_cards}`
- `game.choice.active` + `game.choice.current.{title,prompt}`
- `game.pending_request`（谁在响应 → 座位高亮）
- `game.zhangba_selecting` / `zhangba_selected` / `zhangba_card`
- `game.card_action_picker()` / `card_action_ready()` / `card_action_source_ids()`
- `game.view_as_options()` / `view_as_source_ids()` / `view_as_candidate_ids()`
- `game.can_cancel_pending_selection()` / `is_selection_candidate()` /
  `is_selection_selected()` / `selection_face_down_ids()` / `selection_pool_entries()`

**C. 规则查询**（灰化与提示）
- `game.card_actions.play_context/is_operable`（手牌灰化，`src/ui/player.py:44`）
- `game.busy`、`game.hand_limit(player)`

**D. 客户端没有的能力**
- 距离：`Renderer.distance_to` 走 `src.game.rules.DistanceRule`（`src/renderer.py:599`）
- 出牌方式的“可操作性”：`game.card_actions.source_zones_in_use` / `is_operable`
  （`src/renderer.py:811`）

**结论**：Renderer 从未 `isinstance(game, Game)`，它是**鸭子类型**的——只要一个对象
提供上述属性，它就能画。这既是可复用的基础，也是"悄悄依赖某个字段"的风险来源。

## 2. LAN Host 为什么没有直接得到原来单机 UI 的表现？

**Host 其实一直走的是单机 UI**：`LanScene.update` 在房主开局后把场景切成 `"game"`
（`src/ui/lan_scene.py:168-171`），`main.py` 于是调用 `renderer.draw(game)`
（`main.py:433-435`）—— 与单机完全同一条路径。

所以退化**不在 Host 的牌桌**，而在两处：

1. **客户端**：整个"决策层"是 Phase 11.2 单独造的（见 §4、§5）；
2. **Host 的观感差异**来自"决策文案由远程控制器提供"这一层：出牌阶段的提示来自
   `RemoteHumanController` 的请求文案，与本地 `prompt.describe` 的措辞略有不同。

## 3. LAN Host 是否也被强制转换为 ClientGameView / RemoteGameView？

**没有**。只有远程座位经过视图层：`HostMatch.view_for()` 为每名**远程**玩家生成
`ClientGameView`（`src/network/match.py:126-137`），房主本人用权威 `Game`。
`RemoteGameView` 只在客户端的 `RemoteTableScene` 里构造
（`src/ui/remote_table.py:101`）。

但有一个**隐蔽的断点**：`build_selection_view(game, viewer_id)`
（`src/game/view/view_builder.py:209-236`）只读**房主本地的**
`game.pending_selection`，而它只在本地真人走 `HumanController.present` 的
SELECT_CARDS 分支时才会被设置（`src/game/controllers/human.py:104-113`）。
远程玩家的同类决策在房主侧**只**是一条 `PendingRequest`，永远不会写进
`game.pending_selection`。于是客户端的 `view.selection` 恒为 `None`。

这就是过河拆桥 bug 的第一层根因（第二层见 §7）。

## 4. RemoteGameView 当前缺少哪些 Renderer 所需的 presentation fields？

| Renderer 需要 | 客户端当时的情况 |
|---|---|
| `pending_selection` | **只能来自 `view.selection`，远程决策从不写它** → 公共区没有候选可点 |
| `pending_target_selection` | 恒为 `None` → 座位不进入候选 / 选中态（无高亮） |
| `pending_skill_input` / `pending_skill_picker` / `pending_view_as` | 恒为 `None` → 没有"发动技能"这一层 |
| `card_action_picker()` / `card_action_ready()` / `card_action_source_ids()` | 空实现（`view_adapter.py` 里写死 `None/False/()`) |
| `response.active` / `choice.active` | `InertPending`：永远"没有在等" |
| `pending_card_action` | 恒为 `None` |
| `view_as_options()` | 返回 `()` |
| `allowed_card_ids`（`network_playable_indices`） | 有，且只由房主给的集合驱动（唯一做对的一项） |

一句话：**表现数据（A 类）齐了，交互状态（B 类）几乎全缺**。客户端因此"能看不能点"。

## 5. 当前巨大黄色 Decision Panel 在哪里绘制？

三个来源，用户看到的是它们的叠加：

1. **提示条被顶成全亮金色**：`RemoteTableScene._draw_prompt`
   （原 `src/ui/remote_table.py:518-533`）构造 `PromptInfo("network", text)`，
   **不传 accent**，于是 `prompt.draw` 用默认的 `theme.GOLD_BRIGHT` 画 2px 金边
   （`src/ui/prompt.py:229-236`）——而本地真实逻辑是按状态换色
   （出牌 GOLD_BRIGHT / 选牌 TARGET_BLUE / 响应 DANGER / 等待 TEXT_DIM，
   `src/ui/prompt.py:56-200`）。客户端于是**永远**是一条又宽又空的亮金框。
2. **固定按钮由客户端自配文案**：`RemoteTableScene._configure_buttons`
   （原 `:485-516`）把 `BUTTON_LABELS` 写死成"确认 / 取消 / 打出 / 不出"，
   与引擎 `Renderer._button_state`（`src/renderer.py:236-295`）不是同一份逻辑。
3. **Phase 11.2 的联机专用面板仍在仓库里**：`src/ui/remote_game.py` 的
   `RemoteGameScreen`——一整块 1180×780 的 PANEL_DEEP 面板 + 3px 金边 + 底部
   "确认 / 取消"大按钮。`LanScene` 已不引用它，但文件仍在（本阶段已删除）。

另外 `Renderer.draw` 里所有"本地层"（提示条 / 按钮 / 技能选择 / 出牌方式面板）
都被 `if self.local_interaction(game)` 关掉（`src/renderer.py:729-753`），
客户端只能自己再画一套 —— **这就是"两套 UI 各自演化"的机制性原因**。

## 6. "确认 / 取消"为什么不是原本 UI interaction 的一部分？

因为它不是从引擎状态里推出来的，而是客户端**另写**的一份 `BUTTON_LABELS`
（`DecisionKind → 文案`）。原本的唯一来源是 `Renderer._button_state`：
它按 `pending_card_action → pending_view_as → pending_skill_input →
pending_selection → pending_target_selection → response → choice → phase`
的顺序推出"(文案, 可用, 动作名)"。客户端因为 §4 那些槽位都是空的，这份逻辑对它
完全失效，只能绕过。

## 7. 当前本地 UI 与 Remote UI 的分叉点在哪里？

分叉只在**一处**，但它是致命的：`Renderer.local_interaction(game)`
（`src/renderer.py:588-597`）把"只读视图"与"本地交互"绑成了同一个开关。
客户端既要 `local_interaction=False`（不能算规则），又需要那一层界面，
于是只能在外面重画。

**结论**：应该拆成两个正交概念——
- `local_interaction`：能不能算规则 / 提交动作（规则权威）；
- `interaction_layers`：要不要画提示条与固定按钮（界面层）。

本阶段就是这样拆的（`src/renderer.py:599-612`）。

## 8. 哪些代码是为了 Phase 11.3 临时建立的 debug-style UI？

| 位置 | 性质 | 处置 |
|---|---|---|
| `src/ui/remote_game.py`（整块） | Phase 11.2 的联机专用决策面板 | **删除**（无引用，历史使命结束） |
| `RemoteTableScene._draw_decision` / `_configure_buttons` / `_draw_prompt` / `_draw_options` | 自己重画提示条与按钮 | **删除**，改用 `prompt.describe` + `Renderer.actions_for` |
| `RemoteTableScene._draw_action_banners`（屏幕中央一行"正在等待 X 操作"） | 与提示条信息重复 | **删除**（提示条已经说了） |
| 公共区候选的 rect 缓存 `_refresh_pool_rects` | 正经用途（命中区） | 保留 |

## 9. 哪些旧 UI interaction 可以直接复用？

**全部**。它们本来就是数据驱动的：

| 交互 | 复用的组件 | 需要的输入（本地真人的来源 / 客户端的来源） |
|---|---|---|
| 提示条 | `prompt.describe` + `prompt.draw` | `pending_selection` 等槽位 / 由决策请求填 |
| 固定按钮 | `Renderer._button_state` + `actions_for` | 同上 |
| 出牌方式选择 | `CardActionPicker` | `card_action_picker()` / 由牌的 `options` 造同构对象 |
| 是 / 否 与二选一 | `ChoiceOverlay` + `ChoiceSystem` | `choice.current` / 由请求的 `options` 填 |
| 目标选择（座位高亮 + 点击 + 确认） | `Renderer.draw` 的座位段 + `TableLayout.player_at` | `pending_target_selection` |
| 选牌（手牌 / 装备 / 桌面公共区） | `table.draw_pool` + `player.draw_hand` | `pending_selection` + `selection_pool_entries()` + `selection_face_down_ids()` |
| 发动技能（选费用 + 选目标 + 确认） | `prompt.describe` 的 skill 分支 + `_button_state` 的 skill 分支 | `pending_skill_input` + `skill_input_ready()` |
| 技能栏 | `SkillBar` | `skills.skill_ids_of` + `can_activate` |
| 手牌灰化 | `player.playable_hand_indices` | `network_playable_indices()`（已存在） |

## 10. 是否存在 Renderer 对具体 Game 类型的硬依赖？

没有 `isinstance(Game)`，但有三类鸭子类型的**隐式契约**：

1. **`getattr` 默认值掩盖缺失**：`getattr(game, "local_interaction", True)`、
   `getattr(game, "deal_presentation", None)`——字段名写错会静默走默认分支。
2. **`RemoteGameView.__getattr__` 主动抛错**（`src/ui/view_adapter.py:556-562`）：
   这是**设计正确**的一项——缺字段立刻报错，而不是画出空白。
3. **写入权威状态**：`begin_frame` 会写 `game.ui_rects` / `game.ui_metrics`
   （`src/renderer.py:128-130`）。客户端视图接受这两个写入（纯本地表现缓存），
   不构成越权，但它说明"只读视图"与"权威对象"在接口上没有区分。

## 11. 是否应该抽 Presentation Interface？

**不需要新接口**，需要的是**补齐缺口并把契约写成测试**。

理由：客户端适配层（`RemoteGameView`）已经是"Presentation Interface"了——
它的属性面就是契约本身，且已被 `Renderer` / `prompt` / `layout` / `seats` 一致地
消费。再抽一层 `IGamePresentation` 只会多一层转发，不会让渲染代码少一行。

真正缺的是：

1. **决策 → 交互槽位 的映射**（本阶段新增 `src/ui/decision_presentation.py`）；
2. **界面层与规则层的正交开关**（`interaction_layers`）；
3. **契约的回归测试**（`tests/test_engine_v2_phase11_4_lan_playability.py`：请求 →
   槽位 → 渲染层真的读得到）。

---

## 附：过河拆桥 bug 的根因链（审计结论）

```
客户端点【过河拆桥】→ 选目标 → 确认
        │  （协议正确：Host 校验通过并执行）
        ▼
Host: 过河拆桥效果创建 PendingRequest(SELECT_CARDS, target=远程玩家,
      zone_owner=受害者, candidates=受害者的手牌+装备)
        │  （正确：RemoteHumanController._build_pending_request 转成 DecisionRequest）
        ▼
Client: 收到 kind=select_cards 的请求，cards 字段里确实有候选
        │  ❌ 断在这里
        ▼
Client 的 RemoteGameView.pending_selection 仍为 None
      （它只读 view.selection，而那是房主自己本地选择的状态）
        │
        ▼
公共区没有候选可画 → 玩家看到"无处可点"
→ 只能去点座位 → _toggle_target 发现 targets 为空
→ 提示"房主没有把这个角色列为合法目标"（误导性文案）
```

判定：**属于 §11 的 C + G**（ClientGameView 的公开信息 / Host 生成的合法候选 /
Remote 面板的本地合法性判断三者没有对齐；且跨区域第二阶段没有对应的表现层）。
不是 player_id 映射错误，也不是 revision 竞态——`DecisionRegistry` 的校验一直是对的。

Phase 11.4 的修复方式：把**决策请求**当作唯一的表现来源，映射到本地引擎同构的
交互槽位（`pending_selection` / `pending_target_selection`），并让隐藏手牌退化成
**不透明占位 token**。规则权威、隐藏信息、界面三者因此重新对齐。
