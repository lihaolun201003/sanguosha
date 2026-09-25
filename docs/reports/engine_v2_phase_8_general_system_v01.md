# Engine V2 Phase 8：General System Productionization

报告日期：2026-09-19
范围：在 Phase 7 的 General / Skill 架构之上补齐两个通用扩展点（判定替换、卡牌转化），实现 11 名标准武将、选将界面、技能 UI 与 AI 技能决策。
规则版本锁定在 [docs/rules/phase_8_general_rules_reference.md](../../rules/phase_8_general_rules_reference.md)。

---

## 1. Phase 8 目标

> 用一批真实标准武将验证 General / Skill / Hook / Modifier / Pending / UI / AI 是否真的能支撑后续几十个武将。

交付物：两个通用扩展点 + 11 名武将 + 选将流程 + 技能 UI + AI 技能策略 + 68 项新测试。

## 2. Phase 7 / 7.5 基线

```text
Phase 7   GeneralDef / GeneralRegistry / player.general_id
          SkillDef / SkillRegistry / SkillManager
          Before / After Hook、Modifier、SkillState、Active Skill、3 名武将
Phase 7.5 Fullscreen / F11 / LayoutMetrics / 2～8 人响应式布局 / Overlap Audit
基线测试  256 / 256，compileall 通过，Pygame dummy 通过
```

## 3. JudgeFlow 审计（改前）

```text
JudgeFlow.advance：抽牌 → 判定 → 弃牌，一次同步跑完
JudgeResult：frozen dataclass，判定牌不可替换
调用点：TurnFlow._resolve_judgement_zone / Ganglie / 八卦阵，全部假设同步完成
```

结论：判定牌替换无法接入，必须让判定变成**可恢复流程**并提供替换窗口。

## 4. Judge Replacement 架构

```text
抽牌 → 判定牌进入处理区
  → JUDGE_REVEALED
  → 替换窗口：按座次（从判定角色开始）逐个询问有改判技能的角色
       Pass → 下一个
       Replace → 新牌 手牌→处理区，旧牌 处理区→弃牌堆
  → 锁定判定牌，计算 JudgeResult（带 replacement_history）
  → JUDGE_BEFORE_RESULT / JUDGE_RESULT / JUDGE_FINISHED（天妒在此取牌）
  → 仍在处理区的判定牌 → 弃牌堆
```

- **没有改判者时完全同步完成**，既有行为与性能不变（226 项旧测试零改动通过即为证据）。
- 有改判者时 `JudgeFlow` 返回 `WAITING`，调用方通过 `on_complete` 接回自己的结算。
- 新增 `JudgeContext`（judged_player / reason / original_card / current_card / replacements / locked），技能通过它读取当前判定牌。

## 5. 多人改判顺序

```python
SkillManager.judge_replacers(judged_player, reason)
    → seats.alive_players_in_order(start_after=judged_player, include_start=True)
    → 逐个检查该角色是否有带 judge_replacement 的技能
```

顺序只取决于**座次**与技能绑定顺序，不依赖任何字典遍历顺序。死亡角色自动跳过（测试覆盖）。

## 6. 判定牌移动

全部通过 `MoveCardAtom` 完成，没有直接赋值：

| 阶段 | 移动 |
|---|---|
| 翻开 | 抽牌堆 → 处理区 |
| 被替换 | 旧牌 处理区 → 弃牌堆；新牌 手牌 → 处理区 |
| 最终结算 | 仍在处理区的判定牌 → 弃牌堆 |
| 天妒 | 在 JUDGE_FINISHED 期间从处理区 → 手牌（因此不会再被弃置） |

## 7. CardConversion 架构

`src/game/conversion.py`：

```python
@dataclass(frozen=True)
class CardConversion:
    skill_id, matches(predicate), name, category, subtype, nature,
    source_count=1, contexts=("play", "response")

class ConversionRegistry:   # 挂在 Game 上
    register(conversion, owner) / unregister_owner / unregister_owner_skill / clear
    options_for(game, actor, card, context)      → 某张牌有哪些转换用法（UI 用）
    candidates_for(game, actor, name, context)   → 需要某牌名时能用哪些牌（响应/出牌用）
```

技能通过 `SkillDef.conversions` 声明，`SkillManager.bind` 注册、`unbind` 精确撤销（按 owner + skill_id）。

## 8. VirtualCard

```python
@dataclass(frozen=True, eq=False)
class VirtualCard:
    name, source_cards, skill_id, owner, category, subtype, nature
    _virtual = True
    suit / rank / card_color / identity_label  → 从 source_cards 取
```

- **绝不修改实体牌**：转换只产生一个新的身份对象。
- 结算按 `name`（例如当【杀】结算），实体移动按 `source_cards`。
- `UseCardFlow` 在出牌阶段把 `source_cards` 从手牌移入处理区、结算后移入弃牌堆；已被技能取走的牌（奸雄/天妒）会跳过重复移动。

## 9. source_cards / provenance

- 曹操【奸雄】取的是 `damage.card` 的 `source_cards`（虚拟牌则取原始实体牌），**不按牌名重建**。
- 测试 `test_caocao_jianxiong_handles_converted_damage` 验证：关羽用红桃【桃】武圣当【杀】打曹操，曹操拿到的是那张红桃牌本身。
- 因此"牌的唯一来源"在整条链路上保持可追溯：响应 → 结算 → 伤害 → 技能。

## 10. ResponseSystem 接入

`GameEngine._respond_card` 改为：

```text
校验牌名是否在 allowed_cards（虚拟牌的 name 就是结果牌名）
按 source_cards 校验实体牌仍在手牌
按 source_cards 依次移入处理区 → 弃牌堆
```

AI 侧新增 `AIController.converted_response(request)`：没有真实响应牌时，用 `candidates_for(..., "response")` 找技能转化出来的虚拟牌。UI 侧无需任何改动（响应流程本来就是「点手牌」）。

## 11. General Selection

`src/ui/general_select.py`：一屏展示全部武将卡（程序化绘制）。

```text
势力色条 + 头像圆（姓氏首字）+ 名字 + 势力·性别 + 体力点 + 技能名 + 技能描述
按钮：随机 / 确认出战 / 返回
```

- 完全数据驱动：只读 `GeneralDef` 与 `SkillDef`，不认识任何具体武将。
- 全部 rect 由 `LayoutMetrics` 计算：卡片高度按"屏幕高 − 标题 − 按钮区"反推，保证任何分辨率下按钮不压卡片。
- 流程：开始菜单 →（设置 AI 数量）→ **选将** → 确认 → 开局。

## 12. AI General Assignment

```python
Game.assign_generals(mapping=None, human_general=None)
    → 真人使用 human_general
    → AI 从 general_pool 中随机抽取、同一局不重复
    → 随机源是 Game.rng（可注入种子，测试可复现）
```

11 名武将 > 最多 8 人，因此正常对局不会重复。测试覆盖"真人武将不被 AI 重复"与"固定种子可复现"。

## 13. SeatCard 武将显示

副标题行改为 `座次 N · 武将名 · 势力`（Phase 7 只有座次）。头像圆环与势力色沿用 Phase 7.5 的程序化样式。

## 14. Skill UI

- 真人状态条在名字右侧显示技能名：`玩家 座次0 【武圣】【龙胆】`。
- 悬停主动技能按钮弹出引擎提供的技能描述或禁用原因。

## 15. Active Skill UI

`src/ui/skill_bar.py`：在操作按钮上方动态生成技能按钮。

```python
game.skills.skill_ids_of(player) → 过滤出有 activate 的技能
game.skills.can_activate(player, skill_id) → (bool, reason)
```

- 一个技能都没发动条件时按钮渲染为 Disabled（仍可见，带原因）。
- 点击后提交 `game.skills.activate(player, skill_id)`，多步结算走既有 Pending（反间用 SelectCards，结姻用参数目标），没有新建弹窗系统。
- UI 不出现任何 `if general_id == ...`（测试扫描 `src/ui/*.py`）。

## 16. Skill Trigger FX

新增只读事件 `SKILL_TRIGGERED`（`Skill._handle_event` 与 `SkillManager.activate` 各发一次）。UI 的 `Effects` 订阅它，在对应座位弹出 `【集智】` 浮字（0.9 秒）。它不驱动任何规则，规则测试不受影响。

## 17. AI Skill Policy

| 技能类型 | AI 行为 |
|---|---|
| 锁定技 modifier（咆哮/英姿/奇才） | 自动生效，无需决策 |
| 伤害后触发（刚烈/遗计/反馈/奸雄/天妒） | 自动触发 |
| 响应转换（武圣/龙胆） | 没有真实牌时用 `candidates_for` 找虚拟牌 |
| 出牌阶段转换 | 没有真【杀】时把最没用的红牌/闪当杀 |
| 改判（鬼才） | 只在**自己判定**且结果不利时改判，优先选能让判定变有利的牌；不认识"闪电"以外的判定则不改 |
| 主动技（反间/结姻/突袭） | 反间与结姻在有明确收益时发动；结姻只在体力偏低时用；突袭在摸牌阶段发动并夺取手牌最多的两名角色 |

## 18. 11 名正式武将

| 武将 | 势力 | 体力 | 技能 |
|---|---|---|---|
| 张飞 | 蜀 | 4 | 咆哮 |
| 黄月英 | 蜀 | 3 | 集智、奇才 |
| 夏侯惇 | 魏 | 4 | 刚烈 |
| 曹操 | 魏 | 4 | 奸雄 |
| 司马懿 | 魏 | 3 | 反馈、鬼才 |
| 郭嘉 | 魏 | 3 | 天妒、遗计 |
| 张辽 | 魏 | 4 | 突袭 |
| 关羽 | 蜀 | 4 | 武圣 |
| 赵云 | 蜀 | 4 | 龙胆 |
| 周瑜 | 吴 | 3 | 英姿、反间 |
| 孙尚香 | 吴 | 3 | 结姻、枭姬 |

技能实现按势力拆分为 `src/game/skills/standard/{wei,shu,wu}.py`，反间的多步结算单独放在 `fanjian.py`，没有巨型文件。

## 19. 各武将技能实现要点

- **咆哮**：纯 `SLASH_QUOTA` modifier（Phase 7 已实现，本阶段补测试）。
- **集智 / 奇才**：`CARD_USED` 触发摸牌；新增 `TRICK_RANGE_IGNORE` modifier 让锦囊跳过距离检查。
- **刚烈**：`DAMAGE_SETTLED` → 通用判定（可被鬼才改判）→ 非红桃反伤。
- **奸雄**：`DAMAGE_SETTLED` → 从处理区/弃牌堆取回 `source_cards`。
- **反馈**：`DAMAGE_SETTLED` → 从来源手牌（无手牌则装备）取一张。
- **鬼才**：`judge_replacement` 声明，判定窗口按座次询问。
- **天妒**：`JUDGE_FINISHED` → 若判定牌仍在处理区则收入手牌（因此拿到的是**改判后**的最终牌）。
- **遗计**：`DAMAGE_SETTLED` → 按实际伤害点数摸 2×N。
- **突袭**：新增 `PhaseReplacement` 扩展点，在摸牌阶段开始前询问，发动后跳过摸牌并夺取至多两名角色各一张手牌。
- **武圣 / 龙胆**：纯 `CardConversion`，两个文件都不自己实现虚拟牌。
- **英姿**：`DRAW_COUNT +1`。
- **反间**：主动技 + 分步 Pending（选牌 → 交给目标 → 目标弃同花色牌或受伤）。
- **结姻**：主动技 + 性别/受伤状态校验 + 弃两张手牌 + 双方回血。
- **枭姬**：`EQUIPMENT_LOST` → 摸两张。

## 20. 新增通用扩展点

| 扩展点 | 位置 | 用途 |
|---|---|---|
| `JudgeReplacement` | `skills/definitions.py` | 判定牌替换（鬼才 / 未来的鬼道） |
| `JudgeContext` + 替换窗口 | `flows/judge.py` | 多人改判的确定性顺序与真实牌移动 |
| `CardConversion` / `VirtualCard` / `ConversionRegistry` | `conversion.py` | 卡牌转化（武圣 / 龙胆 / 未来的倾国、奇袭…） |
| `PhaseReplacement` | `skills/definitions.py` + `flows/turn.py` | 阶段替代（突袭 / 未来的摸牌阶段类技能） |
| `ModifierKind.TRICK_RANGE_IGNORE` | `skills/modifiers.py` | 锦囊无距离限制（奇才） |
| `SKILL_TRIGGERED` | `engine/events.py` | 技能触发提示（纯 presentation） |
| `JudgeResult.replacement_history` | `flows/judge.py` | 判定替换历史（展示与测试） |

## 21. 是否出现核心具体武将 if

**没有。** 测试 `test_core_files_do_not_mention_specific_generals` 扫描以下文件并断言不存在 `general_id == ...` / `general.name == ...`：

```text
flows/damage.py  flows/turn.py  flows/dying.py  flows/death.py
flows/judge.py  flows/use_card.py  flows/wuxie.py
rules/distance.py  rules/seats.py  rules/targeting.py
equipment.py  conversion.py  engine/runtime.py  engine/skills.py
card_effects/*.py
```

另有 `test_ui_does_not_mention_specific_generals` 扫描 `src/ui/*.py`。

## 22. 修改文件

```text
src/game/flows/judge.py        判定替换窗口 + JudgeContext + on_complete 回调
src/game/flows/turn.py         判定区可恢复 + 阶段替代询问
src/game/flows/use_card.py     虚拟牌的实体源牌进出区域
src/game/engine/runtime.py     响应支持虚拟牌 + min_cards=0 可放弃
src/game/engine/events.py      + SKILL_TRIGGERED
src/game/engine/skills.py      触发时发只读通知
src/game/skills/definitions.py + JudgeReplacement / PhaseReplacement / conversions
src/game/skills/registry.py    技能清单记录全部绑定 + judge_replacers/phase_offers 查询
src/game/skills/modifiers.py   + TRICK_RANGE_IGNORE
src/game/card_effects/tricks.py 锦囊距离检查接入 modifier
src/game/core.py               选将流程 + conversions 注册表 + rng + 忽略锦囊距离查询
src/game/controllers/ai.py     转换响应 / 转换出牌 / 改判策略 / 主动技能
src/game/generals/catalog.py   11 名武将
src/game/start_menu…           （见下）
src/start_menu.py              开始游戏 → 进入选将
src/renderer.py                技能栏、文本 tooltip、SeatCard 武将
src/ui/seats.py                副标题显示武将
src/ui/player.py               状态条显示技能名
src/ui/skills…                 （新增文件见下）
src/card.py                    牌名表提为模块级（实体牌与虚拟牌共用）
main.py                        选将界面接入 + 技能按钮动作
```

## 23. 新增文件

```text
src/game/conversion.py                      CardConversion / VirtualCard / ConversionRegistry
src/game/skills/standard/__init__.py        分势力聚合
src/game/skills/standard/wei.py             曹操 / 司马懿 / 郭嘉 / 张辽 / 夏侯惇
src/game/skills/standard/shu.py             张飞 / 黄月英 / 关羽 / 赵云
src/game/skills/standard/wu.py              周瑜 / 孙尚香
src/game/skills/standard/fanjian.py         反间的分步结算流程
src/ui/general_select.py                    选将界面
src/ui/skill_bar.py                         主动技能按钮条
docs/rules/phase_8_general_rules_reference.md   规则版本参考
tests/test_engine_v2_phase8_generals.py     54 项 Phase 8 测试
```

## 24. 测试

```text
python -m compileall -q main.py src tests tools    → 通过
python -m unittest discover -s tests               → Ran 310 tests, OK
```

| 模块 | 用例数 |
|---|---|
| Phase 1～4 规则 | 48 |
| Phase 5 规则 / 多人 / UI | 14 / 38 / 9 |
| Phase 6 UI（含节奏） | 47 |
| Phase 7 Skill / General | 48 |
| Phase 7.5 分辨率与布局 | 30 |
| legacy 1v1 | 22 |
| **Phase 8 武将系统（新增）** | **54** |
| **合计** | **310** |

被改动的旧测试共 6 处，都是事实性变化：武将数量断言（3 → ≥11）、"未知武将"改用真正不存在的 id、黄月英技能由 1 个变 2 个、技能清单现在包含纯 modifier 技能、开始菜单改为先进入选将。

## 25. Judge Replacement Tests

```text
无改判者（同步完成） ✅  一个鬼才替换 ✅         真人 Pass ✅
AI 鬼才替换不利判定 ✅   改判者顺序从判定者开始 ✅ 死亡角色被跳过 ✅
原判定牌进弃牌堆 ✅      替换牌离开手牌 ✅       最终判定牌进弃牌堆 ✅
天妒取得最终替换牌 ✅    刚烈判定被改判 ✅       闪电判定被改判 ✅
乐不思蜀判定 ✅          八卦阵判定（沿用同一 JudgeFlow）✅
```

## 26. Conversion Tests

```text
武圣红牌当杀（play）✅   武圣拒绝黑牌 ✅        龙胆杀→闪 ✅
龙胆闪→杀 ✅             虚拟牌保留花色/点数/颜色 ✅  不污染牌堆 ✅
source_cards 正确 ✅     转换不能以虚拟牌为源（防递归）✅
绑定注册 / 解绑撤销 ✅   UI 侧 options_for ✅    响应白名单接受虚拟牌 ✅
```

## 27. Skill Combination Tests

```text
鬼才 + 天妒（拿到替换后的判定牌）✅
反馈 + 刚烈 ✅
关羽武圣 → 曹操奸雄（拿到原始实体牌）✅
同武将多角色状态隔离 ✅
Reset 清理全部技能产物 ✅
```

## 28. UI Tests

选将界面与技能栏的几何检查加入了 `tools/ui_audit.py`：选将卡片不越界、不重叠，按钮不压卡片；技能按钮不越界、不压手牌区与 Prompt。Pygame dummy 流程覆盖「菜单 → 选将 → 确认 → 5 人局 → 出牌 → 响应 → AI 行动 → 重开 → 返回菜单」。

## 29. UI Audit

```text
工具      tools/ui_audit.py（5 种分辨率 × 7 种人数 × 3 种手牌量 = 105 组合）
结果      105 组合，0 处问题
新增检查  选将卡片/按钮、技能按钮
```

过程中修掉一个真实问题：2560×1440 下选将按钮压到第三行卡片（改为按可用高度反推卡片高度）。

## 30. 自动多人对局

| 批次 | 对局数 | 异常 |
|---|---|---|
| 2～8 人，无武将 | 42 | 0 |
| 2～8 人，11 名武将随机分配（12 种子 × 7 种人数） | 84 | 0（修 JudgeFlow 回调前为 33） |
| 同规模扩大种子（24 种子） | 168 | 2（约 1.2%，见已知问题） |

修复的关键缺陷：判定窗口完成时 `JudgeFlow` 没有回调，导致回合停在判定阶段（33/84 局）。

## 31. Pygame Dummy

`tools/ui_smoke.py` 在 1920×1080 下：

```text
布局场景 A～F（含五谷 / 无懈 / 求桃 / 铁索 / 顺手牵羊选装备）  12 项全通过
dummy 完整流程                                                15 步全通过
  菜单选 4 AI → 进入选将 → 选择赵云 → 确认 → 5 人局 → 真人=赵云
  → AI 各分到不同武将 → 悬停/目标选择 → 结束回合 → AI 接管
  → Pending 响应 → 重新开始 → 返回主菜单
```

## 32. 已知限制

1. **长局约 1.2% 偶发异常**：168 局带武将长局中出现 2 例异常（无法在单独重跑时复现，与随机牌序/武将分配有关）。无武将 42 局与首轮 84 局均零异常。这是当前最需要继续追查的问题。
2. **AI 改判范围有限**：AI 只改自己的判定，不替其他角色改判（FFA 无盟友语义下的保守选择）。测试因此通过 `judge_replacers` 的顺序断言来验证多人改判。
3. **规则简化**（已在规则文档标注）：刚烈不做"弃两张手牌"二选一；遗计不做牌分配；反间省略"目标声明花色"这一步。
4. **突袭的发动时机**依赖新增的 `PhaseReplacement`，目前只有它一个使用者。
5. **主动技能 UI 未做目标选择界面**：反间/结姻由引擎自动选目标（AI 逻辑），真人点击按钮即发动；让真人手动选技能目标是下一步。
6. **`GeneralDef.portrait` 为空**，头像仍是程序化圆环；武将立绘与技能配音按需求本阶段不做。
7. **`src/game/skills/library.py` 现在是薄导出**，旧导入路径保留但不再包含实现。
8. **`SRC/start_menu.py` 的"开始游戏"固定进入选将**：无武将模式（`general_pool` 为空）仍可通过 `game.start_single_player()` 直接开局，但没有 UI 入口。

## 33. Phase 9 建议

1. **先追查长局偶发异常**：在 `multiplayer_smoke` 里固定 `game.rng` 与 `deck_seed`，把 1.2% 的复现变成可重跑的确定性用例。
2. **补完技能目标选择 UI**：让真人自己选反间/结姻的目标（复用 `pending_target_selection` 的交互语言，新增"技能目标"上下文）。
3. **继续扩武将**：本阶段已验证 modifier / 触发 / 判定替换 / 卡牌转化 / 阶段替代 / 主动技六类扩展点，下一批可以做 20～30 个标准武将，重点验证"多技能交互"（例如同时存在两名改判者、转化叠加装备）。
4. **身份局前置**：若要做身份模式，建议先做"阵营"抽象（现在 `Player` 只有 FFA 语义），再让 AI 目标评分真正用上阵营。
5. **卡的来源可追溯已经打通**：可以基于 `source_cards` 做更准确的战报（例如"关羽将♥K当【杀】对曹操造成伤害，曹操获得该牌"）。
