# Phase 10.4.3：全 PARTIAL 技能闭环

- 报告日期：2026-09-20
- 基线：Phase 10.4.2（25 武将 / 40 技能，COMPLETE 30 + PARTIAL 10）
- 工作区：`C:\Users\lihao\Desktop\sanguosha\sanguosha -zcode`
- 目标：**PARTIAL = 0**

---

## 0. 基数核准与一份更正

重新从 `GeneralRegistry` / `SkillRegistry` 读取（不依赖上一轮摘要）：

* 武将 **25**
* 技能绑定 **40**，唯一 skill id **40**

**更正上一轮报告的一处统计笔误**：10.4.2 报告正文的矩阵里
COMPLETE 是 30 行、PARTIAL 是 **10 行**，而统计行写成了 "COMPLETE 29 / PARTIAL 11"。
逐行核对后确认：**PARTIAL 实际就是 10 项，不存在第 11 项**。

因此本轮的 10 个目标技能是：

`rende` `guanxing` `luoyi` `luoyi_boost` `zhiheng` `keji` `liuli` `kurou` `jijiu` `qingnang`

其中 `luoyi_boost`（裸衣强化）是**内部 helper skill**（locked + 纯 modifier，
`activate is None`，没有也不应该有独立真人入口），它的闭环方式见第 2 节。

## 1. 逐技能的审计结论与修复

### 1.1 急救（jijiu）—— 真 bug，已修

**症状**：濒死求桃时急救完全不可用。

**根因**：`CardConversion.contexts` 只声明了 `(PLAY_CONTEXT, RESPONSE_CONTEXT)`，
而濒死救援场合的 context 是 `RESCUE_CONTEXT`（"rescue"）。Discovery 在
`context.context not in conversion.contexts` 处直接跳过 —— **急救在最核心的
用途（救命）上被过滤掉了**。

**修复**：`contexts=(PLAY, RESPONSE, RESCUE)`。

**验证**：濒死时 `view_as_options` 返回 1 个候选；点技能进入 View-As；红牌
真实消耗；濒死者被救起（hp 0 → 1）。自己回合内仍然不可用。

### 1.2 苦肉（kurou）—— 真 bug，已修

**两个问题**：

1. **失去体力不会进入濒死**。`LoseHpAtom.apply()` 只做 `hp -= amount`，
   全项目**没有任何濒死检查**。黄盖把自己打到 0 血会变成 `hp=0, alive=True`
   的活死人。
2. `can_activate` 里有一条 `player.hp <= 1 → 不能发动` 的限制，而
   `SkillDef.description` 写的是"出牌阶段，你可以失去 1 点体力，然后摸两张牌"，
   **没有这条限制**；正是它让"降入濒死"永远不可达。

**修复**：

* 新增通用入口 `Game.lose_hp(target, amount, source, cause)` —— 只减体力、
  **不产生伤害事件**（不触发受伤类技能、不走防具与伤害加成），但体力降到 0
  **同样进入真实的 `DyingFlow`**，并把它返回给调用方。
* 苦肉改用它，并去掉 `hp <= 1` 限制；摸牌挂在濒死结算之后（死了不摸，
  被救回来才摸），与"失去体力，然后摸牌"的顺序一致。

**验证**：失去 1 点体力且**不触发任何 DAMAGE 事件**；摸两张；1 血可发动；
真实进入濒死流程（`DYING_ENTERED` 事件触发）。

### 1.3 观星（guanxing）—— 功能缺失，已补

**症状**：只"查看"并原序放回，真人**无法调整顺序**；`_activate_guanxing`
的注释也自认"顺序调整留给后续阶段"。

**判断说明（规则版本）**：本项目 `description` 原文只写了"查看牌堆顶的若干张牌
（数量为存活角色数，至多五张）"，没有写排序。按任务要求"以 description 为
当前版本依据"，我**没有**引入任何标准版以外的机制，而是把 description 补全为
"…并将它们以任意顺序放回牌堆顶"，使文本与实现一致；数量规则（存活角色数、
至多五张）本来就是标准观星的规则，未改。

**修复**：

* 打开真实的排序通道：复用 `start_card_selection(zone="public_pool")`，
  玩家按"放回顺序"依次点选，`ordered[0]` 落在牌堆顶。
* 重排只动 `draw_pile[-n:]` 的元素顺序 —— **牌从未离开牌堆**，不 draw、
  不复制、不丢弃。
* 给选牌通道加了 `cancellable`：选满才结束的请求也要有"放弃并保持原序"的出口，
  否则玩家会被卡住。

**验证**：3 张候选（= 存活角色数）；玩家点选顺序 `[闪, 杀, 桃]` 后，
**实际摸牌顺序正是 `[闪, 杀, 桃]`**；牌堆总数不变。

### 1.4 流离（liuli）—— 缺真人入口，已补

**先纠正一个误判**：转移机制本身**是好的**。实测日志为
`AI 1 发动【流离】：弃置 桃，将【杀】转移给 AI 3` + `玩家 对 AI 3 造成 1 点普通伤害`，
即"改 `flow.targets` 而不取消重建原杀"的路线是通的，属性/酒/武器修正也不会丢。

**真正的问题**：整条链路是**全自动**的 —— 自动决定发动、自动弃第一张手牌、
自动选第一个合法目标。真人没有任何选择机会。

**修复（新增通用扩展点）**：

* `UseCardFlow` 增加"**目标重定向窗口**"：目标事件（`TARGET_SELECTED` /
  `BECOME_TARGET`）发完之后，如果技能登记了 `flow.target_redirect`，
  本次用牌就挂起，等窗口给出结论再进入效果阶段；`flow.redirect_resolver`
  负责把结论落地。核心流程里**不认识任何具体技能**。
* 流离改为向引擎申请一次 `SELECT_TARGETS` 请求（`min_cards=0`，
  候选 = 攻击范围内的其他角色）。**真人与 AI 走同一条通道**：
  真人通过选目标 UI 回答（可以直接确认空选择 = 不发动），
  AI 由 `AIController._respond_select_targets` 回答。没有真人专用分支。

**验证**（真人是大乔、AI 用杀打她）：

| 选择 | 结果 |
| --- | --- |
| 选择发动 | 大乔支付一张牌、杀转移给玩家选定的目标并造成伤害、大乔未受伤 |
| 放弃发动 | 大乔不支付、杀照常打在大乔身上 |

### 1.5 裸衣 / 裸衣强化（luoyi / luoyi_boost）—— modifier 正确，暴露更严重的通用问题

**裸衣本身是正确的**：`draw_count` 由 2 变 1、`damage_dealt_bonus` 对
【杀】/【决斗】为 1、对【桃】为 0、对其他角色为 0；真实出杀时目标掉 2 点。

**但验证"下回合恢复"时发现了本阶段最严重的通用缺陷 ——
`ResetScope` 从未被消费**：

`SkillState` 允许技能用 `ResetScope.TURN / PHASE / ROUND` 声明状态活多久，
但全项目**没有任何一处调用 `clear_scope`**（`clear_all` 只在解绑/死亡/重置时用）。
后果：

* `裸衣.active` 永久生效 → 之后**每个回合都少摸一张、伤害永远 +1**；
* `反间 / 结姻 / 制衡 / 苦肉 / 闭月 / 洛神 …` 的 `used` 标记**永不清除** →
  这些"限一次"技能**一辈子只能发动一次**。

这已经不是 PARTIAL 问题，而是**所有标记了 scope 的技能都在跨回合后失效**。

**修复**：在 `TurnFlow` 的真实边界上消费 scope（清理发生在事件**之前**，
这样技能在 `TURN_START` / `PHASE_START` 里写下的新状态不会被误清）：

* `begin_interactive()` / `advance()` 回合开始 → `clear_scope(TURN)`
* 三个回合收尾点（含阵亡收尾）→ `clear_scope(TURN)`
* 每个 `PHASE_START` 之前 → `clear_scope(PHASE)`

**验证**：回合结束后 TURN scope 被清空；真实反间发动后 `used=1`，走完一个
`TurnFlow` 变成 `used=0`（**下一回合可以再次发动**）；裸衣在下回合
`draw_count` 回到 2、伤害加成归零。

**`luoyi_boost` 的闭环**：它是 locked + 纯 modifier 的 helper（无 `activate`、
无独立按钮），由 `luoyi` 的 `phase_replacement.apply` 打开
`skill_state["luoyi"]["active"]`，两个 modifier（`DAMAGE_DEALT` / `DRAW_COUNT`）
据此生效。验证：未发动时 `draw_count=2` 且加成 0；发动后 `draw_count=1`
且加成 1；回合结束后两者一起复位。

### 1.6 克己（keji）—— 结论：本来就是正确的

**先纠正一个误判**：`skills.phase_offers()` 只按阶段过滤、**不**调用
`can_offer`，容易让人以为"用过杀之后仍会提供克己"。实际上真正决定是否询问的
是 `TurnFlow._offer_phase_replacement`，它在 offer 前会调用
`replacement.can_offer(game, player)` ✓ —— 分层是对的：`phase_offers` 是候选
清单，`can_offer` 是**当时的**合法性判断。

**状态来源**：克己读 `player.sha_used`，这是引擎在 `ShaEffect` 里维护的
"本回合是否使用/打出过【杀】"标记（`use_card.py` 在使用【杀】时置位），
不是"看当前手牌有没有杀"，也不是"看最后一张牌"。多目标杀只置位一次。

**验证**：未使用杀 → 提供跳过弃牌；`sha_used=True` → 不再提供；
真实出杀后 `sha_used` 确实为 `True`，且此时不能发动克己。
`TurnFlow` 里**没有**吕蒙特判。

### 1.7 仁德（rende）—— 正确

`transfer_cards=True` 由 `skills/activation.py` 统一实现：费用牌走
`MoveCardAtom(card, source=player.hand, destination=target.hand)` —— **真实转移**，
不是复制也不是 list 操作。

**验证**：牌真实离开刘备手牌并进入目标手牌；双方手牌总数不变（无凭空生成）；
可以连续发动；手牌交完后 `can_activate` 拒绝。

按本项目 description（"将任意数量的手牌交给一名其他角色"），**没有**"给满两张
回复体力"的阈值条款；因此不涉及"累计数量"的规则效果（累计计数只用于日志）。

### 1.8 制衡（zhiheng）—— 正确

`variable_cost=True`，费用牌由引擎统一弃置（`MoveCardAtom` → 弃牌堆），
再按弃置数量 `DrawCardsAtom(player, count)` 摸牌 —— 顺序是"先弃后摸"，
不是"先摸再删旧牌"。

**验证**：弃两张摸两张（手牌数守恒）；被弃的牌确实在弃牌堆；再次发动被拒
（`used` 标记 + PHASE scope，配合第 1.5 节的 scope 修复，下一回合恢复）。

### 1.9 青囊（qingnang）—— 正确，但记录一处版本差异

实现：`ActiveSkillSpec(cost_cards=1, needs_target=True)`，费用牌走引擎统一弃置，
`RecoverHpAtom(target, 1)` 真实回血（`RecoverHpAtom` 自身封顶 `max_hp`），
目标候选 = 已受伤角色。

**验证**：目标回复 1 点；费用牌真实进弃牌堆；目标满血时 `can_activate` 因
"没有已受伤的角色"而拒绝。

**版本差异说明（未改）**：本项目 `description` 没有"限一次"字样，实现也
不限次；标准版青囊是"出牌阶段限一次"。按任务要求"以本项目 description 与
既有实现为规则版本依据"，我**保持不限次**，仅在此说明差异，未擅自改版。

## 2. 本轮技能矩阵

| 武将 | 技能 | 类型 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| 刘备 | 仁德 | active | COMPLETE | 真实转移、可重复、无牌拒绝 |
| 诸葛亮 | 观星 | active | COMPLETE | 真实排序 + 摸牌顺序生效（本轮补） |
| 许褚 | 裸衣 | passive | COMPLETE | 少摸 + 伤害 +1，跨回合复位 |
| 许褚 | 裸衣强化 | locked(helper) | COMPLETE | 由裸衣驱动，两条 modifier 均生效 |
| 孙权 | 制衡 | active | COMPLETE | 弃 N 摸 N、限一次、跨回合恢复 |
| 吕蒙 | 克己 | passive | COMPLETE | 读真实 sha_used；offer 路径正确 |
| 大乔 | 流离 | passive | COMPLETE | 真人可选发动/目标（本轮补） |
| 黄盖 | 苦肉 | active | COMPLETE | lose_hp 语义 + 真实濒死（本轮修） |
| 华佗 | 急救 | view_as | COMPLETE | rescue 场合可用（本轮修） |
| 华佗 | 青囊 | active | COMPLETE | 弃牌回血（不限次，按本项目版本） |

其余 30 个技能沿用 10.4.2 的结论（其中 `wushuang` / `tieji` 已在 10.4.2 修复），
本轮**回归验证未发现回退**。

**统计**：COMPLETE 40 / PARTIAL 0 / BROKEN 0 / REGISTERED_ONLY 0 /
NOT_REACHABLE 0 / UNCERTAIN 0。

## 3. 新发现的真实 Gameplay bug

1. **`ResetScope` 从未被消费**（影响所有"限一次 / 本回合生效"技能）——
   最严重，见 1.5。
2. **急救在 `rescue` 场合不可用** —— 技能的最核心用途被 context 过滤掉，见 1.1。
3. **`LoseHpAtom` 不进入濒死** —— 失去体力到 0 血不会死，见 1.2。
4. **苦肉多了 `hp <= 1` 限制** —— 与 description 冲突且让濒死不可达，见 1.2。
5. **观星没有顺序调整** —— 只有 Engine 的"查看"，没有可用入口，见 1.3。
6. **流离全自动** —— 真人无法选择是否发动 / 转移给谁，见 1.4。

## 4. 新增的通用 Engine 能力

| 能力 | 位置 | 用途 |
| --- | --- | --- |
| `Game.lose_hp()` | `src/game/core.py` | "失去体力" ≠ 伤害，但同样进入濒死 |
| 目标重定向窗口 | `src/game/flows/use_card.py` | 技能可在目标确定后插入自己的结算（流离） |
| `SELECT_TARGETS` + `min_cards=0` 约定 | 复用既有 pending | "可放弃的技能选择"统一通道 |
| 选牌通道 `cancellable` | `src/game/card_selection.py` | 选满才结束的请求也能放弃（观星） |
| TurnFlow scope 消费 | `src/game/flows/turn.py` | `ResetScope.TURN / PHASE` 真正生效 |

**没有新增任何武将特判**：`TurnFlow` / `DamageFlow` / `CardEffect` / `ShaEffect`
里都没有 `if general_id == ...`。

## 5. Card Movement 核查

本轮涉及的 7 个技能**全部**走统一原子，没有新的 `list.remove/append` 绕过：

| 技能 | 牌移动 |
| --- | --- |
| 仁德 | `MoveCardAtom(owner.hand → target.hand)`（引擎统一支付） |
| 观星 | **不移动**（只重排 `draw_pile[-n:]`，牌从未离开牌堆） |
| 制衡 | `MoveCardAtom(→ discard_pile)` + `DrawCardsAtom` |
| 流离（代价） | `MoveCardAtom(owner.hand → discard_pile)` |
| 苦肉（摸牌） | `DrawCardsAtom` |
| 急救 | `RespondCardAction` → 引擎移动 source 实体牌 |
| 青囊 | `MoveCardAtom(→ discard_pile)`（引擎统一支付）+ `RecoverHpAtom` |

## 6. 状态 Reset 核查

除青囊（按本项目版本不限次、因此不写 `used`）外，本轮技能的状态清理如下：

| 状态 | scope | 清理点 | 验证 |
| --- | --- | --- | --- |
| 裸衣 `active` | TURN | TurnFlow 回合收尾 / 下次开始 | 下回合 `draw_count` 回 2、加成归零 |
| 制衡 `used` | PHASE | PHASE_START | 阶段切换即清，下一回合可再发动 |
| 反间 / 结姻 `used` | PHASE / TURN | 同上 | 真实反间跨回合 `used` 1 → 0 |
| 苦肉 `used` | TURN | 同上 | 与新回合一致 |
| 观星 `used` | TURN | 同上 | 每回合可再观星一次 |
| 克己 | 无自身状态 | 由 `sha_used` 决定 | 回合重置时 `sha_used` 复位 |
| 仁德 | 无状态 | — | 可反复发动 |

## 7. 实际场景验证

`python` 脚本驱动真实引擎流程（`UseCardAction` / 真实 pending / 真实 TurnFlow），
**46/46 通过**，关键结果：

* **裸衣**：不发动正常摸 2；发动后只摸 1、杀/决斗 +1、桃 +0、他人 +0；
  真实出杀目标掉 2；下回合 `draw_count` 回 2 且加成归零。
* **流离**：真人选发动 → 支付一张且杀改结算新目标；选放弃 → 自己挨打且不付牌。
* **急救**：濒死时可用、点技能进入 View-As、红牌真实消耗、濒死者被救起；
  普通点击永不自动转换；自己回合内不可用。
* **克己**：未用杀提供跳过弃牌；真实出杀后 `sha_used=True` 且不再提供。
* **观星**：玩家选择顺序 = 后续真实摸牌顺序；放弃不丢牌。
* 仁德 / 观星 / 制衡 / 青囊 / 苦肉 / 裸衣强化 的逐项断言全部通过。

**静态审计**：新增 `tools/skill_static_audit.py`（三条轻量检查），当前输出
**"静态审计：通过"** —— 无死事件、规则层无只写不读的标记、每个 `ResetScope`
都有消费点。工具本身在本轮就抓到过真实问题（第 3 条检查正是为 `ResetScope`
缺失而加）。

## 8. 回归

* `python -m compileall main.py src tests tools`：**通过**
* 全量 unittest：**934 通过**（无失败）
* 本轮**没有新增测试文件**；修复由确定性场景脚本验证（符合"不要为凑数量写测试"）

## 9. 修改文件

```
src/game/core.py                      新增 Game.lose_hp()（含濒死检查）
src/game/flows/use_card.py            目标重定向窗口（通用扩展点）
src/game/flows/turn.py                消费 ResetScope.TURN / PHASE
src/game/card_selection.py            选牌通道 cancellable
src/game/skills/standard/qun.py       急救补 RESCUE_CONTEXT
src/game/skills/standard/shu.py       观星真实排序 + description 补全
src/game/skills/standard/wu2.py       苦肉（lose_hp + 去 hp 限制）、流离真人入口
tools/skill_static_audit.py           新增：三条静态审计检查
docs/reports/phase10_4_3_partial_skill_closure_v01.md
```

## 10. 剩余非阻断问题

1. **青囊不限次**与本项目 description 一致，但与标准版（"限一次"）不同。
   若后续要做"标准版对齐"，改 description + 加 `used` 标记即可，本轮未改。
2. **流离的 AI 策略**是"总是发动、取第一个候选"（`AIController` 的通用
   `select_targets` 行为）。规则链正确，但 AI 不会挑更划算的目标 ——
   属于策略层，按"不扩 AI"未做。
3. 观星的排序交互复用了公共牌区。若将来五谷丰登与观星在同一时刻都需要
   公共区，需要给公共区加"用途"区分；目前两者不会同时发生。
4. `ResetScope.ROUND` 仍未定义消费点（当前没有技能使用它）；静态审计会对
   它报警，属于预留能力。

## 11. 结论

* **PARTIAL = 0，BROKEN = 0**，40 个技能全部 COMPLETE。
* 本轮真正的收获不在 10 个技能本身，而是揪出了 **`ResetScope` 从未被消费**
  这个影响所有"限一次 / 本回合生效"技能的通用缺陷。
* Gameplay 技能层已达到"规则确实会发生"的标准，可以恢复 Phase 10.5。
