# Engine V2 Phase 5：本地多人自由混战系统

报告日期：2026-09-19
范围：标准版 + 军争篇；1 名真人 + 自定义 1～7 名 AI，总人数 2～8 人，本地自由混战（Free For All）。
不含：武将、身份、国战、任何网络代码。

---

## 1. Phase 5 完成情况

已完成。核心原则「1v1 不再是一套独立规则，而只是 `players[]` 长度为 2 的特殊情况」已经落实到规则层：

- 引擎侧所有旧 `game.enemy` 依赖清零；业务核心只剩 `game.player`（真人视角）与 3 处 AI 出牌动画坐标常量。
- 真人出牌、AI 出牌、AI 响应全部走同一条 `GameAction → GameEngine → Flow` 路径。
- 2～8 人局都能从选人界面开始、完整打到分出胜负，并支持重新开始与返回主菜单。

本阶段在既有 Phase 5 半成品基础上补完了 AI 多人决策、控制器边界、玩家回合状态归属、目标契约、死亡语义、UI 多人交互，并修掉 4 个真实的卡死/误判缺陷（见第 31 节）。

### 完成度对照（需求九十五条 43 项）

| # | 标准 | 结果 |
|---|---|---|
| 1 | 正式 `players[]` | 通过 |
| 2 | 支持总人数 2～8 | 通过 |
| 3 | 固定 1 名真人，其余 AI | 通过 |
| 4 | AI 数量可选择 1～7 | 通过 |
| 5 | SeatManager 完成 | 通过 |
| 6 | TurnFlow 任意存活角色轮转 | 通过 |
| 7 | 死亡角色自动跳过 | 通过 |
| 8 | 多人环形距离正确 | 通过 |
| 9 | 死亡角色不计有效距离 | 通过 |
| 10 | 杀使用多人距离和攻击范围 | 通过 |
| 11 | 顺手牵羊使用多人距离 | 通过 |
| 12 | 兵粮寸断使用多人距离 | 通过 |
| 13 | TargetSelection 支持多个候选角色 | 通过 |
| 14 | 铁索支持选择多个角色 | 通过 |
| 15 | 南蛮真正多人逐个结算 | 通过 |
| 16 | 万箭真正多人逐个结算 | 通过 |
| 17 | 桃园真正多人结算 | 通过 |
| 18 | 五谷真正多人选择 | 通过 |
| 19 | 无懈支持多人 responder | 通过 |
| 20 | DyingFlow 支持多人求桃 | 通过 |
| 21 | 真人可选择是否救其他角色 | 通过 |
| 22 | AI 支持多个合法目标决策 | 通过 |
| 23 | 借刀杀人支持多人第三目标 | 通过 |
| 24 | 闪电按多人座次转移 | 通过 |
| 25 | 连环伤害支持多个横置角色 | 通过 |
| 26 | 连环中途濒死可恢复外层 Flow | 通过 |
| 27 | 当前行动角色死亡不会卡死 | 通过 |
| 28 | GameResult 支持任意 last survivor | 通过 |
| 29 | 真人死亡能正常结束游戏 | 通过 |
| 30 | Renderer 支持 2～8 人 | 通过 |
| 31 | AI 手牌只显示数量 | 通过 |
| 32 | AI 的装备/HP/判定区可见 | 通过 |
| 33 | 当前行动者有明显高亮 | 通过 |
| 34 | 1v1 仍正常 | 通过 |
| 35 | 没有武将系统 | 通过 |
| 36 | 没有身份系统 | 通过 |
| 37 | 没有联网代码 | 通过 |
| 38 | 不含国战逻辑 | 通过 |
| 39 | 原有 67 项测试全部通过 | 通过（原 84 项全绿） |
| 40 | 新增多人测试全部通过 | 通过（新增 41 项） |
| 41 | compileall 通过 | 通过 |
| 42 | Pygame dummy 多人启动通过 | 通过 |
| 43 | 真实多人自由混战无明显 Pending / Flow 卡死 | 通过（336 局自动对局零异常） |

---

## 2. players[] 架构

`Game` 现在只有一个角色容器：

```python
self.players = []            # 自然顺序 P0..Pn，每名角色带 player_id / seat
self.seats = SeatManager(self)
```

配套查询 API（需求第九十条）：

```python
game.get_player(player_id)
game.get_alive_players()
game.get_alive_players_in_seat_order(start_after=None)
game.get_next_alive_player(player_or_id)
game.current_player_id / game.current_seat
game.get_controller(player)
```

距离与目标不再由业务模块各自实现：

```python
DistanceRule.distance(game, source, target)
DistanceRule.in_range / in_attack_range
target_candidates(game, actor, rule)
validate_targets(game, actor, targets, rule, min, max)
```

`game.player` / `game.enemy` 仅作为 façade 保留（见第 25 节）。

---

## 3. Player identity

`src/player.py`：

```text
player_id        # "P0".."P7"，唯一身份，不使用名字
seat             # 0..7，死亡不重排
name             # "玩家" / "AI 1".."AI 7"
controller_type  # ControllerType.HUMAN / AI（预留 REMOTE_HUMAN）
alive            # 存活标志（濒死时仍为 True）
hp / max_hp      # 默认 4 体力、无武将技能
hand / equipment / judgement_zone / chained
gender           # 真人 male；AI 1 女、AI 2 男、AI 3 女……交替
sha_used / jiu_used / wine_buff / wine_sha_required   # 本回合状态，按角色独立
```

新增派生属性 `is_human` / `is_ai` / `is_alive` 与 `clear_turn_state()`。
回合开始由 `TurnFlow` 驱动方调用 `player.clear_turn_state()`，因此多 AI 各自的出杀次数、酒效果互不干扰（Phase 4 的 `game.enemy_wine_buff` 单例问题已消除）。

---

## 4. Controller 架构

```text
PlayerController            src/game/controllers/base.py
├── HumanController         src/game/controllers/human.py   （Pygame 输入适配标记）
└── AIController            src/game/controllers/ai.py      （自由混战 AI）
        （Phase 6 预留 RemoteController 挂同一位置）
```

- `Game.get_controller(player)` 按 `player_id` 缓存控制器；`reset()` 清空缓存。
- `TurnFlow` 只依据 `current_player.controller_type` 决定 Action 来源，不区分「人回合 / 电脑回合」。
- 引擎的 `present_or_auto_resolve()` 遇到 AI owner 时委托给 `AIController.respond()`，AI 与真人提交同一种 `GameAction`；引擎代码里不再有 AI 专用启发式。
- AI 串行响应：始终是「当前 Pending → 唯一 owner Controller 回应 → Flow 恢复 → 下一个 Pending」。

---

## 5. SeatManager

`src/game/rules/seats.py`：

```python
all_players()                                  # 按 seat 排序
alive_players_in_order(start_after, include_start)
players_from_seat(seat)
next_alive_player(player) / previous_alive_player(player)
distance(source, target)                       # 环形基础距离
```

- `next_alive_player` 跳过死亡角色，不重新编号 seat。
- `distance` 只在存活角色环上取顺时针 / 逆时针较小值：8 人局 0→7 距离为 1。
- TurnFlow、闪电转移、无懈 responder、濒死求桃、群体锦囊、连环传播都通过它取座次。

---

## 6. TurnFlow 多人化

```text
current_player → 准备 → 判定 → 摸牌 → 出牌 → 弃牌 → 结束 → TURN_END
              → SeatManager.next_alive_player() → 下一角色
```

- `game.phase` 值域统一为 `TurnPhase.value`（prepare/judge/draw/play/discard/finish），不再出现 `"enemy"`。
- `phase` 不再由「是不是真人」决定，真人与 AI 阶段完全一致。
- 新增**可恢复暂停**：判定阶段的闪电把当前角色打进濒死时，TurnFlow 停在判定阶段等待，求桃结束后自动继续摸牌 / 出牌阶段，而不是把回合留在半途（这是本次修掉的真实卡死，见第 31 节）。
- 当前行动角色在回合中阵亡时，TurnFlow 立即结束该回合并交给 `next_alive_player`。

`core.py` 的 `game.sha_used` / `jiu_used` / `player_wine_buff` / `enemy_wine_buff` 等改为指向角色字段的 façade 属性，旧 1v1 测试与 UI 读取不变。

---

## 7. DistanceRule 多人化

`DistanceRule.distance` 委托 `game.seats.distance()`，再叠加坐骑：

```text
基础环形距离（忽略死亡角色）
  + 目标有 +1 防御马 → +1
  - 自己有 -1 进攻马 → -1
  ≥ 1
```

【杀】合法性 = 多人基础距离 + 坐骑修正 + 攻击范围，统一由 `ShaEffect.can_use` → `DistanceRule.in_attack_range` 判定；Renderer 与 AI 都不复制规则。

---

## 8. 死亡后的距离

死亡角色 `alive = False`，`seat` 保持不变。因为 `distance` 只遍历存活角色，所以 A–B–C–D 中 B 阵亡后 A→C 的基础距离自动变为 1。测试 `test_circular_distance_and_dead_player_compression` 覆盖。
死亡角色同时：不获得回合、不作为合法目标、不参与群体锦囊 / 五谷 / 无懈响应 / 求桃，也不作为闪电转移目标。

---

## 9. TargetSelection 多人化

- 玩家使用【杀】时，所有攻击范围内的存活角色进入候选，AI 面板显示蓝框；已选择显示黄框（`draw_opponents`）。
- 点击候选角色即选中；单目标牌点选后自动确认，无需二次点击。
- 多目标牌（铁索连环）可点选 1～2 人后点「确认目标」结算。
- 提示文案按需求第五十三条：`请选择目标：已选择 0 / 1`、`请选择 1～2 个目标：已选择 1 / 2，点击「确认目标」结算`。
- 只有 1 个合法目标时（含 1v1）保持一步出牌，不要求额外确认。

目标契约（`rules/targeting.py`）在本次修正为**座次顺序**：`target_candidates` 从使用者开始按座次返回，`ALL_PLAYERS` / `ALL_OTHERS` 的校验只比较集合是否完整、不再要求调用方猜中同一个列表顺序。这修掉了「AI 发动的群体锦囊被判为非法目标」的真实缺陷。

---

## 10. 南蛮入侵多人结算

`NanmanEffect`（`ALL_OTHERS`，`per_target_wuxie`）：按 action 的 `targets[]`（= 使用者之后的座次顺序）逐个：

```text
目标无懈窗口 → 请求【杀】 → 有杀则抵消 → 无杀进入 DamageFlow
```

每个目标独立结算，中途进入濒死会完整走完 DyingFlow/DeathFlow 再回到外层，继续处理下一个目标。

## 11. 万箭齐发多人结算

`WanjianEffect` 与南蛮同一实现，响应牌为【闪】。实测：AI 发起的万箭，真人收到请求【闪】的 Pending，其余 AI 依次处理，Flow 正常结束。

## 12. 桃园结义多人结算

`TaoyuanEffect`（`ALL_PLAYERS`）：按座次遍历 targets，对每名存活角色执行 `RecoverHpAtom`。死亡角色被跳过，回复不超过 `max_hp`。

## 13. 五谷丰登多人选择

`WuguEffect`：

```text
翻开牌数 = 场上存活角色数
选择顺序 = 使用者 → 下一名存活角色 → …（座次顺序）
```

真人走 Pending + 公共牌区点击（蓝框候选、黄框已选），AI 走 `SelectCardsAction` 并按价值取最高的一张。结算结束时公共区必定清空，未被拿走的牌进入弃牌堆，每张牌都有唯一归属。

## 14. 无懈多人 responder 顺序

`WuxieResponseChain.responders` = 从使用者开始的全部存活角色（`include_start=True`）。
`pass_count` 达到 responder 总数才结束；任何人使用无懈后 `nullified` 反转、`pass_count` 归零、`responders` 重新完整轮询，因此支持任意层无懈与反无懈。群体响应锦囊为每个目标单独建立无懈窗口。

AI 无懈策略（自由混战）：只在自己是受影响目标、且效果尚未被抵消、且该牌不是对全场有利牌（无中生有 / 桃园 / 五谷）时使用；其余一律 Pass，不替别人消耗。

## 15. 多人 DyingFlow / 求桃

```text
HP ≤ 0
→ 先问濒死者自己（【桃】或【酒】，酒只能自救）
→ 再按座次依次问其他存活角色（只能用【桃】）
→ 有人出桃则回到自救阶段重新询问
→ 无人救援则进入 DeathFlow
```

救援顺序在流程开始时固定为 `[濒死者] + 其后的座次顺序`，死亡角色自动跳过。真人被询问时显示 `是否使用【桃】救援 AI 3？`，可用桃或 Pass。AI 默认不使用桃救别人，但仍提交 `PassPendingAction` 走完规则层流程（将来身份局只需替换 AI 的决策函数）。

## 16. 闪电多人转移

`SHANDIAN` 判定未命中时调用 `TurnFlow._next_alive_player`，沿 `next_alive_player` 寻找判定区没有闪电的存活角色；找不到合法目标则进入弃牌堆。不再是 player/enemy 互传。

## 17. 铁索多人选择

`TiesuoEffect`：`MULTIPLE`，1～2 个目标，可选择自己或任意角色；在横置 / 重置之间切换并发出 `CHAIN_STATE_CHANGED`。所有可选目标都已横置时，AI 改为重铸（`recast` → 摸一张牌）。真人可选择「连环」或「重铸」。

## 18. 连环伤害多人传播

首次横置角色受到火 / 雷属性伤害并结算完毕后，`ChainDamageFlow` 按座次顺序传播同属性同点数伤害：

```text
AI1（横置）受火伤 → 解除横置 → AI3 → AI4 → …
```

- 共享 `visited` 与 `chain_id` 防止 A→B→A 循环；每个传播目标仍创建独立 `DamageFlow`，因此藤甲、白银狮子、`LoseHpAtom`、DyingFlow / DeathFlow 全部照常介入。
- 传播中某角色阵亡不会中断后续合法目标。
- 传播中进入濒死时 ChainDamageFlow 保持等待，子流程结束后继续。

## 19. 借刀杀人多人化

```text
A 对 B 使用借刀 → 选 C（合法第三目标） → B 对 C 出杀，或 B 交出武器
```

- 真人使用：先选持有武器的角色，再选被迫出杀的第三目标（`pending_target_selection` 的第二阶段），候选由 `can_attack` 动态计算，不写死。
- AI 使用：`AIController` 从合法第三目标中自动选择。
- B 无杀或 C 不在攻击范围时，武器通过 `TransferEquipmentAtom` 交给使用者。

## 20. AI 多目标选择

`AIController`（`src/game/controllers/ai.py`）不再假设唯一敌人：

- `legal_targets(card)`：按 `effect.target_rule` 取候选，逐个用 `effect.can_use` 探测，再按评分排序。
- 评分：`(max_hp - hp) * 20 - hp * 3`，并按牌名追加（过河 / 顺手看手牌与装备数量、火攻看是否有手牌、杀看手牌数量与血量），最后加入小随机扰动打破平局——避免「所有 AI 永远围殴座次靠前的同一个人」。实测 4 人局中同一 AI 对两个合法目标的 300 次决策分布为 142 : 158。
- 出牌循环：一个回合内持续选择最优动作（上限 10 张），依次考虑「回复桃 → 装备（空槽必装、武器只换更长）→ 酒 + 杀 → 其他锦囊（按价值降序）→ 铁索重铸」。
- 装备牌由 `EquipEffect`（类别级效果）统一处理，AI 因此能自己装上武器获得攻击范围，这是 8 人局能打起来的前提。
- 弃牌阶段按估值弃掉最没用的牌。

**信息边界**：AI 只读其他角色的 HP、手牌**数量**、装备、判定区与公开状态。需要对手牌区选牌时（过河、顺手、火攻），装备作为公开信息优先，手牌部分只做随机选择，绝不读取内容。测试 `test_ai_ignores_hidden_hand_contents` 用「同样长度、不同内容」的手牌验证评分完全一致。

## 21. GameResult 多人化

```python
GameResult(outcome, winner, loser, reason)   # winner_player_id 属性
GameOutcome.PLAYER_WIN / AI_WIN / LAST_SURVIVOR / HUMAN_ELIMINATED / NO_SURVIVOR
```

- 只剩 1 名存活角色 → `winner` 为该角色，`reason = "LAST_SURVIVOR"`。
- 真人阵亡 → 立即结束并显示「你已阵亡 / 游戏失败」，若有多个 AI 存活则不宣布任何 AI 获胜（`reason = "HUMAN_ELIMINATED"`）。
- 结束标记只增不减：真人先阵亡、同一传播里后续角色再阵亡时，不会把对局重新变成进行中，也不会改写胜负。
- 对局结束时清空 Pending，结束后的伤害不再结算。

## 22. Renderer 动态布局

- 真人固定在屏幕底部中央 `(330, 570, 340, 75)`。
- 1～3 名 AI：上方横排（面板 220×122）。
- 4～7 名 AI：上方最多 4 个（190×122），其余分左右两列竖排。
- 实测 2～8 人所有面板互不重叠、全部落在窗口内（测试 `test_panels_do_not_overlap_for_up_to_eight_players`、`test_human_panels_and_hand_stay_inside_the_window`）。
- AI 面板显示：名称、座次、存活 / 横置 / 阵亡、HP / MaxHP、手牌数量、武器、防具、+1 马 / -1 马、判定区；AI 手牌只画背面。
- 当前行动者：面板金边加粗 + 桌面中央「当前回合：AI 3」。
- 目标选择：候选面板蓝框、已选黄框。
- 公共牌区同时承担五谷与「从其他角色区域选牌」的候选展示，保证 8 人桌面也能点到目标。
- 删除了 120 余行从未被调用的 `draw_enemy()`，以及只服务旧 1v1 的 enemy 手牌 / 装备点击函数。

## 23. 初始手牌策略

统一为**每名角色 4 张**（需求第五十九条推荐方案），集中在 `Game.reset()` 一处：

```python
for player in self.players:
    player.draw_cards(self.deck, 4)
```

不再存在「真人 6 张 / AI 4 张」的开发模式分支，config 中没有分散的初始手牌常量。牌堆抽空时仍按「弃牌堆洗回抽牌堆」循环（`Deck.draw` → `reshuffle_discard_pile`），24 人局长局不会因牌堆耗尽卡死。

## 24. ViewState / PublicState 基础

`src/game/view/player_view.py`：

```python
PlayerPublicState(player_id, seat, name, alive, hp, max_hp,
                  hand_count, equipment, judgement_zone, chained)
    .from_player(player)
```

自己的手牌仍由本地 `player.hand` 直接渲染；其他角色只通过该公开投影取值。`PendingRequest` 也补齐了远端视角字段：

```text
request_id, owner_id, source_id, target_ids, allowed_card_ids, allowed_cards, context
```

Renderer 依 `owner_id`（即 `request.target`）判断是否需要真人输入，AI 请求交给对应 AIController——Phase 6 只需把「真人输入」换成远端消息。

## 25. 还剩哪些 player/enemy façade

统计口径：`game.player` / `game.enemy` / `self.game.player` / `enemy_` 前缀 / `ENEMY_` 常量。

| 分类 | 剩余 | 说明 |
|---|---|---|
| 业务核心（flows / rules / engine / card_effects / controllers） | 11 | `game.enemy` **0 处**；8 处 `game.player` + 3 处 `ENEMY_HAND_RECT` |
| UI（main / renderer / start_menu / choice / response / card_selection） | 27 | 全部是 `game.player`（真人自己的手牌与装备区），`game.enemy` **0 处** |
| Legacy Mixin（ai / combat / dying / basic_cards / equipment / core） | 63 | 41 处 `enemy_` 命名方法、22 处 `ENEMY_HAND_RECT` |
| 测试 | 331 | 1v1 兼容测试 |
| 常量 | 2 | `ENEMY_HAND_RECT` / `ENEMY_EQUIPMENT_RECTS` 定义 |

**业务核心剩余的 `game.player` 性质**（均属「本地真人视角」，不是二元模型）：

- `engine/runtime.py`：人类 Pending 的 UI 分支以真人为 actor；动画起点选择（真人手牌坐标 / AI 手牌坐标）。
- `flows/death.py`：`human_eliminated`、胜负文案「你获胜了 / 你阵亡了」。
- `flows/dying.py`：濒死提示文案判断。

保留的 façade：

```python
game.player  → 真人玩家（本地唯一真人）
game.enemy   → 仅 2 人局返回唯一 AI；多人局返回座次最小的 AI
```

`game.enemy` 现在只被 Legacy Mixin 与旧测试读取，新规则一律使用 `players[]` / `seats` / `targets`。

## 26. 删除 / 收敛的旧 1v1 代码

- 删除 `renderer.draw_enemy()` 整段（约 120 行死代码）与 `get_enemy_hand_rects` / `enemy_card_at_position`（删除 main.py 的 enemy zone 分支后失去调用者）。
- `main.py` 的选牌入口从 4 个 1v1 zone（`player_hand` / `enemy_hand` / `enemy_cards` / `enemy_equipment`）收敛为 `hand` / `public_pool` / `player_equipment`，`game.enemy` 引用清零。
- 删除 `Game` 上的全局回合状态字段（`sha_used` / `jiu_used` / `player_wine_buff` / `wine_sha_required` / `enemy_wine_buff` / `enemy_jiu_used`），改为指向角色字段的 façade 属性。
- 删除 `start_enemy_turn`（改名为 `start_next_turn` 并统一由座次驱动）、删除 `try_player_sha` 的 UI 分支、删除 `start_menu` 未使用的 `MULTIPLAYER_RECT` 导入与 `show_multiplayer_notice`。
- `flows/turn.py` 删除 `"enemy"` phase 分支；`card_effect/sha.py` 删除「只有真人受出杀次数限制」的分支；`rules/card_use.py` 删除 phase 字符串判断。
- 真人出牌（杀 / 桃 / 酒 / 装备 / 锦囊）与丈八蛇矛虚拟杀统一走 `_begin_v2_card_targeting`，不再有「2 人走旧路径、3 人以上走新路径」的分叉。
- `ai.py` / `combat.py` / `dying.py` 加注说明为 Legacy 兼容层（AI 回合的实际来源是 `AIController`）。

## 27. 测试结果

```text
python -m compileall -q main.py src tests tools     → 通过
python -m unittest discover -s tests                → Ran 125 tests, OK
```

| 测试模块 | 用例数 |
|---|---|
| test_engine_v2 | 6 |
| test_engine_v2_core_combat | 10 |
| test_engine_v2_phase3 | 13 |
| test_engine_v2_phase4 | 19 |
| test_engine_v2_phase5 | 14 |
| **test_engine_v2_phase5_multiplayer**（本阶段新增） | 32 |
| **test_engine_v2_phase5_ui**（本阶段新增） | 9 |
| test_legacy_basic_combat | 3 |
| test_legacy_dying | 3 |
| test_legacy_equipment_combat | 12 |
| test_legacy_test_tools | 4 |
| **合计** | **125** |

Phase 5 新增 41 项，覆盖需求第七十六条全部 20 条，并额外覆盖桃园多人结算、铁索 1～2 目标与超限拒绝、借刀第三目标与交出武器、闪电同名跳过、AI 装备武器、AI 不读隐藏手牌、真人输入锁定、全灭语义、闪电濒死恢复回合、酒后无杀不再锁死等场景。

唯一被修改的旧测试是 `test_engine_v2_phase3.py` 中 CardEffectRegistry 的数量断言：16 → 18（新增【桃】【酒】两个命名效果，装备为类别级共享效果不计入），属于事实性更新。

## 28. Pygame dummy 结果

`SDL_VIDEODRIVER=dummy` 下：

- `test_dummy_driver_renders_a_five_player_table`：选择 4 AI → 创建 5 人局 → 连续 400 帧事件循环 + 渲染 → 真人结束回合 → AI 自动接管 → 正常退出。通过。
- `test_engine_v2_phase5_ui` 另外用 dummy 驱动模拟真实点击：开始界面 `+` 按钮把 AI 数量调到 4、点击「开始本地自由混战」进入 5 人桌面、点击手牌出杀、点击 AI 面板选定目标、铁索点选两个面板后确认、结束回合、重开、返回主菜单。全部通过。
- 8 人局面板与手牌边界检查通过，无越界、无重叠。

## 29. 实际烟雾测试

`tools/phase5_scenarios.py`（1 真人 + N AI，全部真实引擎结算）：

| 场景 | 关键实测数据 | 结果 |
|---|---|---|
| A 南蛮入侵（1 真人 + 3 AI） | 目标顺序 AI 1 → AI 2 → AI 3，各自体力 3/3/3 | 通过 |
| B 五谷丰登（1 真人 + 4 AI） | 翻牌 5 张 → 结算后公共区 0 张 → 每名角色 1 张 | 通过 |
| C 万箭齐发（AI2 发起） | 真人收到「请打出响应牌」Pending，Pass 后掉 1 点；其余 AI 依次处理 | 通过 |
| D AI3 濒死求桃 | 真人收到「是否使用【桃】救援 AI 3？」→ Pass → AI3 阵亡 → 游戏继续 | 通过 |
| E 连环火焰伤害传播 | 三段横置全部解除；中间角色阵亡后仍继续传播到下一人 | 通过 |
| F 当前行动角色阵亡 | AI 1 在自己回合阵亡 → 当前回合自动切到 AI 2，游戏继续 | 通过 |

六个场景连续多次运行稳定通过，结算结束后 Pending 均为空。

## 30. 多人长局测试

`tools/multiplayer_smoke.py` 提供了脚本化真人（会吃桃、装备、出杀、打锦囊、结束回合）+ 引擎 AI 的自动对局驱动：

| 批次 | 对局数 | 异常 |
|---|---|---|
| 2～8 人 × 12 种子（随机牌序） | 84 | 0 |
| 同一批重复 3 轮 | 252 | 0 |
| 固定牌序（deck_seed 1～30 × 2～8 人） | 210 | 0 |
| 合计 | **336** | **0** |

检查项：回合能正常轮转、Pending 不残留、Flow 不丢失、牌堆能洗回、AI 持续行动、死亡后继续、每局都能走到 `game_over`。平均一局约 23 个回合，最长 66 个回合。

## 31. 已知问题

本阶段修掉的 4 个真实缺陷（均由多人长局测试暴露）：

1. **判定阶段濒死卡死**：真人在自己回合被闪电劈至濒死并自救成功后，`phase` 停在 `judge`、回合永远无法继续。修法是让 TurnFlow 支持阶段暂停 / 恢复，求桃结束后自动继续剩余阶段。
2. **连环传播把濒死当成已死**：`DeathFlow` 原先用「HP > 0」判断存活，导致有角色正处于濒死求桃时，另一名角色阵亡就提前宣布某个 AI 获胜。改为以存活标志判定胜负，并让结束后的传播不再结算、不再创建请求。
3. **群体锦囊目标顺序**：`target_candidates` 从座次 0 开始、结算从使用者开始，`validate_targets` 又要求列表严格相等，导致 AI 发动的南蛮 / 万箭 / 五谷被判为非法目标。修法是让候选从使用者开始按座次返回，覆盖性校验改为集合比较。
4. **酒后死锁**：使用【酒】后若没有可用的【杀】或没有能攻击到的目标，玩家既出不了别的牌也结束不了回合。现在酒效果作废并允许正常结束。

仍存在的问题：

- `ai.py` / `combat.py` / `dying.py` / `basic_cards.py` 里仍保留 63 处 Legacy 1v1 命名（`enemy_*` 方法、`ENEMY_HAND_RECT` 动画坐标）。它们已不在 UI 主路径上（AI 回合走 `AIController`），但仍是 Legacy 回归测试的依赖，未在本阶段删除。
- AI 不会主动使用需要复杂选择链的装备技能（丈八蛇矛把两张牌当杀、贯石斧弃两张牌等）之外的判断仍很朴素；方天画戟的多目标杀（一张杀掉多名角色）仍未开放，`ShaEffect.max_targets` 固定为 1。
- AI 不会为「留着桃保命」而克制使用桃，也没有装备更换以外的长线策略；自由混战下 AI 之间不会结盟或针对领先者。
- 五谷丰登、过河拆桥等需要从其他角色区域选牌时，候选牌统一摊在公共区展示，因此会对真人暴露对方手牌内容。这是本阶段为「8 人桌面也能点到」做的取舍，Phase 6 若引入观战 / 联机需要重新设计。
- `Deck()` 洗牌使用全局 `random`，自动对局驱动只有在传入 `deck_seed` 时才能复现同一局；这不影响游戏，但让偶发问题复现成本偏高。

## 32. Phase 6 局域网联机建议

本阶段已经具备联机所需的两个边界，建议 Phase 6 直接在其上扩展：

1. **Action 边界**：所有真人操作都能描述为 `GameAction`（`UseCardAction` / `RespondCardAction` / `PassPendingAction` / `SelectCardsAction` / `ConfirmPendingAction` / `ChooseOptionAction`），且都带 `actor.player_id`。联机时把本机 `HumanController.submit` 换成「校验 actor 权限 → 序列化 → 发往 Host」即可。
2. **Controller 边界**：`ControllerType` 预留了 `REMOTE_HUMAN`，`Game.get_controller` 是唯一的 Action 来源注入点；新增 `RemoteController` 不需要改动 TurnFlow / flows / card_effects。

建议步骤：

1. 先做 `ViewState` 完整化：把 `PlayerPublicState` 扩展成包含 Pending 描述、公共牌区、日志的整桌快照，由 Host 每帧 / 每次状态变更后下发；Renderer 改为只读快照（本阶段 Renderer 已只读 Engine 状态，改动集中在取值函数）。
2. 再做 Pending 归属：`PendingRequest.owner_id` 已经指明等待谁。Host 只把属于该玩家的 Pending 下发给对应客户端，其余客户端只看到「等待 AI 3 响应」。
3. 网络层放在 Host 侧独立线程或 asyncio 循环里，引擎保持单线程串行提交（沿用本阶段的「AI 串行响应」约定），避免多人同时改状态。
4. 隐藏信息处理：`PlayerPublicState` 只带 `hand_count`，五谷 / 过河拆桥的候选牌需要改成「只在 owner 客户端可见，Host 只回传选择结果」，这与本阶段第 31 节最后一条的取舍一致。
5. 重连与观战可以后续再做；本阶段的 `game.result` / `winner_player_id` / `reason` 已足够作为对局结束的统一信号。

---

## 附：本阶段新增文件

```text
src/game/card_effects/basic.py                    桃 / 酒 CardEffect
src/game/card_effects/equipment.py                装备类别级 CardEffect
tests/test_engine_v2_phase5_multiplayer.py        32 项多人规则测试
tests/test_engine_v2_phase5_ui.py                 9 项 Pygame dummy 交互测试
tools/multiplayer_smoke.py                        多人自动对局驱动与长局测试
tools/phase5_scenarios.py                         需求 A～F 六个烟雾场景
```
