# Phase 8 Hotfix：Active Skill ActionBar Pipeline

报告日期：2026-09-19
问题：**主动技能无法由真人正常发动**（阻断性缺陷）
范围：只修主动技能的真人发动链路，不新增武将、不新增卡牌、不引入美术资源、不改 UI 主题、不重构 AI。

---

## 1. 问题描述

真人拥有主动技能（例：周瑜【反间】、孙尚香【结姻】、张辽【突袭】）时：

- 界面上的技能标签点了没有反应，或即使进入结算也无法提供目标；
- 需要选择目标的技能（反间）永远拿不到目标；
- 需要支付费用牌的技能（结姻弃两张、回收弃一张）没有选牌入口；
- 玩家无法取消一次误触的发动。

AI 侧却一切正常 —— 因为 AI 直接调用内部函数并自行挑选参数，绕过了 UI。

## 2. 审计方法

> 先审计，不猜。

审计路径（读真实代码，不依赖任何假设）：

```text
UI 点击 → main.py 的 MOUSEBUTTONDOWN 分支
        → renderer.hit_action → ("activate_skill", skill_id)
        → game.skills.activate(game.player, skill_id)
        → SkillManager.activate → SkillDef.activate(game, player, target=None, cards=[])
```

结论：整条链路上**没有任何环节负责收集玩家输入**。

## 3. 修复前的链路缺陷（审计结果）

| # | 位置 | 缺陷 |
|---|---|---|
| 1 | `main.py` | `("activate_skill", skill_id)` 直接调用 `skills.activate`，没有目标、没有费用牌 |
| 2 | `ui/skill_bar.py` | 每个技能各画一个标签，点击标签即被视为"发动"；技能标签同时承担展示与发动，没有统一入口 |
| 3 | 全局 | 没有技能选择面板，多个主动技只能并列展示 |
| 4 | 全局 | 没有输入收集阶段：目标与费用牌无处提供 |
| 5 | `skills/standard/wu.py` | `_activate_fanjian` 自己挑目标（对真人来说等于替玩家做决定），`_activate_jieyin` 自己在函数内部弃两张牌，`probes._activate_recycle` 自己弃一张牌 —— 费用支付混在技能实现里，校验失败也会先付成本 |
| 6 | `ui/` | 技能候选目标不显示蓝框，费用牌不高亮，Prompt 不说明"现在要做什么"，没有"确认 / 取消"按钮 |

## 4. 统一目标链路（修复后）

```text
真人点击「发动技能」
  → Game.begin_skill_activation()
      可发动技能 = 0 → 什么都不做（按钮本来就不可点）
                   = 1 → 直接进入该技能
                  ≥ 2 → 打开 Skill Picker
  → Game.start_skill_activation(skill_id)
      can_activate 通过 → 收集参数（目标候选 / 费用牌数量）
      不需要参数 → 直接提交
  → Game.confirm_skill_input()
  → ActivateSkillAction(actor, skill_id, target, cards)
  → engine.submit → resolve_activation
      1) 再次 can_activate（状态可能已变化）
      2) 目标齐备且属于候选
      3) 费用牌齐备且仍在手牌
      4) 支付费用（手牌 → 弃牌堆）
      5) emit SKILL_TRIGGERED
      6) definition.activate(game, player, target, cards)
```

`SkillManager.activate` 现在也走同一条 `resolve_activation`，所以 **UI / AI / 测试三者的规则行为是同一份实现**。

## 5. 技能参数声明（数据驱动）

`SkillDef` 新增 `active_spec: ActiveSkillSpec`：

```python
ActiveSkillSpec(
    needs_target=True,
    target_candidates=_jieyin_targets,          # callable(game, player) -> list
    target_prompt="【结姻】：请选择一名已受伤的男性角色",
    cost_cards=2,
    cost_prompt="【结姻】：请选择两张手牌弃置",
)
```

| 技能 | needs_target | cost_cards |
|---|---|---|
| 反间（周瑜） | 是（其余存活角色） | 0 |
| 结姻（孙尚香） | 是（已受伤男性） | 2 |
| 回收（架构探针） | 否 | 1 |
| 突袭（张辽） | 否 | 0 |

技能实现里不再出现"自己挑目标 / 自己弃牌"的代码：`_activate_fanjian`、`_activate_jieyin`、`_activate_recycle` 都只负责结算本身。

## 6. ActionBar

真人操作区固定三键，自上而下：

```text
[ 发动技能 ]   仅当：真人回合 + 出牌阶段 + 无阻塞 Pending + 至少一个可发动的主动技
[ 结束回合 ]   主按钮（沿用既有语义）
[ 取消     ]   次按钮（沿用既有语义）
```

- **Disabled 与"不存在"分开**：有主动技但当回合不可发动时，按钮显示为禁用态，并把原因放进 tooltip；完全没有技能时才彻底不显示内容。
- 技能输入模式下，技能按钮自动禁用（避免重复进入），主按钮变「确认发动」，次按钮变「取消发动」。
- 三个按钮视觉上分层：发技能为蓝（次要）、结束回合为红（主）、取消为幽灵键。

## 7. Skill Picker

- 只有 1 个主动技：点击「发动技能」直接进入它，不弹面板。
- 2 个及以上：弹出选择面板。
- 面板完全数据驱动：标题、每行 `【SkillDef.name】`、每行描述 `SkillDef.description`（超出宽度按真实字宽省略），不可发动的行显示禁用原因。
- 面板是模态的：打开期间其它点击一律被吞掉（返回 `"swallow"`），底下的手牌不会被误点。

## 8. 技能输入与提示

- 候选目标：角色面板画蓝框；已选目标画黄框（与出牌选目标共用同一套视觉语义）。
- 费用牌：点击手牌选中（上浮高亮），再点一次取消该张选择。
- Prompt 面板分三层显示：
  - 标题 `发动【结姻】`
  - 说明 `点击角色面板选择目标，点击手牌选择要弃置的牌` / `点击「确认发动」结算`
  - 进度 `目标：AI 1　已选 2/2 张`
- 底部的 `game.message` 同步给出同样信息（供日志与状态栏使用）。

## 9. 取消语义与 cost 支付时机

- 取消（`cancel_skill_input`）只清空 UI 收集状态：目标、已选费用牌、面板与输入态全部复位，回到普通出牌阶段，**不写 used 标记、不弃任何牌、没有任何不可逆副作用**。
- 费用牌只在 `resolve_activation` 里**全部校验通过之后**才移动到手牌外。
- used 标记写在技能自己的 `activate` 内部（即真正结算时），所以"取消"和"校验失败"都不会污染 mark。

## 10. Pending 接回技能流程

技能建立的 Pending 与既有流程完全一致：

```text
反间 → FanjianFlow 建立 SELECT_CARDS（真人选一张手牌交出）
     → 真人点击手牌回答 → 流程继续 → 伤害 / 弃牌抵消
     → used 标记保持，回合继续
```

技能的 Pending 不会被技能输入态拦截：`confirm_skill_input` 提交后立即清空输入态，之后的 Pending 走既有的选牌 UI。

## 11. 响应式与 draw/hit 一致性

- 技能按钮与选择面板的所有 rect 都由 `LayoutMetrics` 计算，**绘制与命中用同一个 Rect 对象**。
- F11 / 窗口 Resize 后由 `Renderer.refresh_layout` 重建；测试覆盖 1280×720、1366×768、1920×1080、2560×1440 四种分辨率下按钮仍然可点。
- 几何审计扩展到技能按钮与选择面板：按钮不越界、不与手牌区 / Prompt / 主次按钮重叠；面板不越界、行不重叠、取消按钮在面板内。

## 12. AI 不回归

- `AIController._try_active_skill` 改为提交 `ActivateSkillAction`，与真人走同一 `resolve_activation`。
- AI 会按 `ActiveSkillSpec.cost_cards` 自动挑出价值最低的牌作为费用（`_active_skill_cards`）。
- AI 不接触任何 UI 状态：测试断言 AI 整个回合内 `pending_skill_picker` / `pending_skill_input` 始终为 `None`。

## 13. 点击路由只有一份

新增 `src/ui/interaction.py`，把游戏内的点击分发从 `main.py` 抽出：

```python
handle_game_click(position, game, renderer)   # 真实事件循环与 dummy 测试共用
run_action(action, game, renderer)           # UI 动作 → Game 公开入口
```

这样"测试里点得到"与"真人点得到"是同一段代码，不会出现两套路由各自演化。

## 14. 新增测试（29 项）

文件：`tests/test_engine_v2_phase8_hotfix.py`。全部通过 `pygame.event.post` 投递真实 `MOUSEBUTTONDOWN`，再交给 `handle_game_click`。

| 分组（测试类） | 覆盖点 | 数量 |
|---|---|---|
| ActionBarAvailabilityTests | 无技能不可点、有技能可点、draw rect == hit rect、4 种分辨率下仍可点、非真人回合禁用 | 5 |
| SkillPickerTests | 单技能直达、双技能数据驱动面板、模态吞点击、取消不写 mark、面板打开时按钮禁用、布局取自 metrics、无可发动技能时给出原因 | 7 |
| SkillInputTests | 反间完整点击链（最低 smoke）、结姻两张费用牌、重复点击取消选择、自己不是合法目标、取消干净、缺目标拒绝确认、技能输入期间不能出牌、本阶段二次发动被拒、提交前二次校验、Prompt 文案随输入推进 | 10 |
| SkillPendingTests | 反间的 Pending 选牌接回技能流程并造成伤害 | 1 |
| SkillResetTests | 重开 / 返回菜单清空输入态、重开清空面板、无残留按钮状态 | 4 |
| SkillAiParityTests | AI 提交 ActivateSkillAction 并成功发动、AI 不触碰技能 UI 状态 | 2 |

回归命令：

```text
python -m compileall -q main.py src tests tools   → 通过
python -m unittest discover -s tests              → Ran 339 tests, OK
```

| 模块 | 用例数 |
|---|---|
| Phase 1～7.5 既有 | 310 |
| **Phase 8 Hotfix（新增）** | **29** |
| **合计** | **339** |

## 15. Pygame dummy 真实鼠标点击

`tools/ui_smoke.py` 的完整流程新增一段真实点击（不是直接调 API）：

```text
点击「发动技能」 → 进入技能输入
点击角色面板   → 选中目标（该角色面板出现黄框）
主按钮识别为「确认发动」
点击「确认发动」→ 提交技能，写入 used 标记
点击手牌（真实鼠标事件）→ 回答反间的选牌 Pending
结算完成后技能按钮回到不可点
```

流程共 21 步全部通过，包含既有的布局场景 A～F、五谷、铁索、顺手牵羊装备等。

## 16. 最低 smoke（真人周瑜反间）

```text
打出手牌为空 + 目标无手牌 → 反间造成 1 点伤害
发动后 can_activate 返回 False（出牌阶段限一次）
再次点击「发动技能」不可点
```

## 17. 长局压测

```text
无武将：2～8 人 × 3 个随机种子 = 21 局 → 0 卡死、0 异常
带武将（含拥有主动技的周瑜 / 孙尚香 / 张辽）：2～8 人 × 3 个种子 = 21 局 → 0 卡死、0 异常
```

## 18. 几何审计

```text
python tools/ui_audit.py → 审计完成：105 组合，0 处问题
```

审计项新增：技能按钮越界 / 与手牌区 / Prompt / 主次按钮重叠、技能面板越界、面板行重叠、行压到取消按钮、取消按钮越界。

## 19. 视觉验收

在 1920×1080 下逐张确认（对照 Phase 6 的深色古风风格，未改动主题）：

- 普通出牌阶段：技能按钮可用（蓝）；不可发动时为灰 + tooltip 说明原因。
- 选择面板：深色遮罩 + 金边面板，两行技能、描述省略号、取消按钮；悬停行高亮。
- 技能输入：候选角色蓝框、已选目标黄框、费用牌上浮、Prompt 三行分层、主按钮金色「确认发动」。

## 20. 修改文件

```text
src/game/core.py                      技能输入状态机、reset 清理、提交结果归一化
src/ui/skill_bar.py                   SkillBar 重写 + SkillPicker（新增文件）
src/renderer.py                       Picker 绘制与命中、确认按钮、候选高亮、tooltip
src/ui/prompt.py                      技能输入提示分支
src/ui/player.py                      费用牌上浮高亮
src/ui/interaction.py                 唯一点击路由（新增文件）
main.py                               点击分发改用 interaction
tools/ui_smoke.py                     真实鼠标点击的技能全链路
tools/ui_audit.py                     技能按钮与面板几何检查
```

引擎侧（同一 hotfix 的组成部分，随本报告一起交付）：

```text
src/game/skills/definitions.py        ActiveSkillSpec
src/game/skills/activation.py         resolve_activation（新增文件）
src/game/engine/domain_actions.py     ActivateSkillAction
src/game/engine/runtime.py            submit 分支
src/game/skills/registry.py           activate 统一走 resolve_activation
src/game/skills/standard/wu.py        反间 / 结姻声明 spec，移除自选目标与自行弃牌
src/game/skills/probes.py             回收声明 spec
src/game/controllers/ai.py            AI 提交 ActivateSkillAction + 选费用牌
```

## 21. 已知问题

1. 长局压测中偶发异常仍是 Phase 8 报告里记录的那一条（约 1.2%，依赖随机牌序与随机武将分配），本次 42 局长局未复现，未定位到根因。
2. 技能选择面板的悬停高亮只改底色（不改边框），在深色面板上对比度有限；属于观感问题，不影响命中。
3. 技能的键盘快捷键未做（当前全部通过鼠标完成）。

## 22. 未做的事（按需求约束）

未新增武将、未新增卡牌、未寻找原画、未改动 UI 主题、未实现身份模式、未重构 AI。
