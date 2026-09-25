# Phase 8 Hotfix 3：View-As 必须先点技能，再选牌

报告日期：2026-09-19
范围：只修真人 View-As（视为技）交互入口。未改 AI、未新增武将卡牌、未跑随机长局。

---

## 1. 根因

Hotfix 2 建立的 `Game.begin_card_action` 把"点击实体牌"当成了统一入口：

```text
真人点击【闪】 → Card Action Discovery → 发现【龙胆】→ 自动当【杀】使用
```

引擎侧（CardConversion / VirtualCard / source_cards / CardActionContext）没有问题，
错的是**真人从哪里进入 Conversion**：点牌即转换，玩家无法表达"我现在只是想用【闪】本身"，
也无法在两个用途之间做选择。

## 2. 修改方案

```text
旧：点击实体牌 → 自动发现 Conversion
新：点击「发动技能」→ 选择视为技 → 进入选牌模式 → 选择实体牌 → 目标 / 响应
```

- `SkillKind` 新增 `VIEW_AS`；龙胆、武圣（以及 test-only 的 4 个 probe）标注为 `VIEW_AS`。
  它们**不是** PASSIVE，也**没有**被改成 ACTIVE。
- `Game.card_action_options` 现在只返回**正常使用**的动作：
  点击实体牌永远不会触发技能转化。
- 点击一张"只能靠技能使用"的牌时给出引导：
  `【龙胆】需要在「发动技能」里选择后才能使用。`
- 入口统一：`begin_skill_activation` → Skill Picker → `start_skill_activation(skill_id)`
  内部按 `SkillDef.is_view_as` 分流，UI 里没有任何 `if skill_id == "longdan"`。

## 3. ViewAsSession

`src/game/view_as.py`（轻量 dataclass，无 DSL）：

```text
skill_id / skill_name / context
selected_source_cards / required_source_count
effective_card / selected_targets
stage ∈ {select_source, select_target, ready}
candidates（当前可作为来源的实体牌）
```

Game 侧入口（`card_action_session.py`）：

```text
view_as_options(context)        当前场合可进入的视为技（供 Skill Picker）
begin_view_as(skill_id)         进入选牌模式（此时才高亮合法素材牌）
toggle_view_as_source(card)     选择 / 取消一张来源牌
cancel_view_as()                取消（无副作用）
view_as_ready() / view_as_candidate_ids() / view_as_source_ids()
```

## 4. Play

```text
点击「发动技能」→【龙胆】
Prompt：请选择 1 张牌，将其当【杀】使用
只有合法 source（杀 / 闪）可点，其余灰化
选择【闪】→ 构造 VirtualCard【杀】→ 标准目标选择 → 确认
→ UseCardAction（带 metadata.card_action / conversion）→ 按【杀】规则结算
```

未点技能时点击【闪】：不转换、不弃牌、进入目标选择，只给出引导提示。
`source_zones` 保持现有正式规则（武圣 / 龙胆只声明手牌），未擅自改规则版本。

## 5. Response

```text
Pending 请求：【杀】：请打出一张【闪】
未点技能点【杀】→ 不响应，提示先点技能
点「发动技能」→【龙胆】→ Prompt：请选择 1 张牌，将其当【闪】打出
选择【杀】→ 提交【闪】（Engine Pending 提交 VirtualCard；legacy 响应按下标移除实体牌）
```

## 6. 龙胆 / 武圣验证

| 场景 | 结果 |
|---|---|
| 赵云未点龙胆点【闪】 | 不转换，提示去点技能 |
| 赵云点龙胆 → 选【闪】 | 高亮合法，形成【杀】并进入目标选择，结算后实体牌进弃牌堆 |
| 响应需要【闪】未点龙胆点【杀】 | 不响应 |
| 响应点龙胆 → 选【杀】 | 当【闪】打出成功 |
| 关羽点武圣前点满血【桃】 | 既不正常用也不自动转换 |
| 关羽点武圣 → 选红桃【桃】 | 当【杀】使用成功 |
| 取消（选牌中 / 目标选择中） | 不弃牌、不写 used、不写战报、不播 FX |
| 只有提交后 | 才写"发动【龙胆】，将♦2【闪】当【杀】使用"并播 SKILL_TRIGGERED |

## 7. 测试

```text
tests/test_engine_v2_phase8_hotfix3_view_as.py   15 项（新增，全部真实鼠标点击）
tests/test_engine_v2_phase8_hotfix2_*.py        按新入口更新（机制未回退）
原测试全部通过
```

新增覆盖：未点技能不转换（龙胆 / 武圣）、点技能后 source 合法、闪→杀完整结算、
响应未点技能不能响应、响应点技能后杀→闪、取消无副作用、可以取消后重新进入、
多 source 必须先点技能再选两张、反间 / 结姻仍走 ACTIVE 流程、
ACTIVE + VIEW_AS 共用同一入口、只有提交才写战报与 FX、
视为技不通过 `ActivateSkillAction`。

## 8. 回归结果

```text
compileall                                        PASS
python -m unittest discover -s tests              Ran 437 tests, OK
tools/view_as_smoke.py                            7 步全部通过（两个短场景）
tools/ui_smoke.py                                 27 步全部通过（连跑 3 次稳定）
tools/ui_audit.py                                 105 组合 0 问题（未改全局布局）
```

## 9. 已知限制

1. 视为技的 `source_zones` 仍按现有正式规则（手牌）；装备区通路保留在 Probe D。
2. 多 source 的 UI 流程只在 PLAY 场合完整；响应 / 救援的多 source 取决于技能声明。
3. `CardActionPicker`（一张牌多个普通用途的选择面板）在当前交互下不会被触发，
   组件与渲染路径保留，供未来"同一张牌两种正常用法"的场景使用。
4. 未改 AI：AI 仍通过 `converted_response` / `converted_play_card`（同一套 Discovery）决策。
