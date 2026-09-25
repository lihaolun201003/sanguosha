# Engine V2 Phase 7：General / Skill / Event Hook Foundation

报告日期：2026-09-19
目标：建立能长期承载几十乃至上百个武将技能的通用扩展架构，而不是先堆武将内容。
约束：不改写 DamageFlow / TurnFlow / TargetRule / CardEffect 等核心规则；不新建第二套事件总线；不引入任何外部依赖。

---

## 1. Phase 7 目标

一句话：

> 以后新增绝大多数普通武将技能时，只需要新增 General / Skill 模块和注册信息，不再修改核心规则文件。

Phase 7 交付的是**骨架 + 证明**：
- 骨架：GeneralDef / GeneralRegistry、SkillDef / SkillRegistry、按 owner 的绑定与解绑、Hook 管线、Modifier 查询、技能状态、主动技能入口。
- 证明：6 类架构探针技能 + 3 个简单正式武将（咆哮 / 集智 / 刚烈），全部只用新扩展点实现，核心规则文件里没有任何武将判断。

---

## 2. Phase 6 之后的 Engine 状态

```text
游戏规则    Phase 5 完成（2～8 人自由混战、群体锦囊、无懈、求桃、连环、闪电…）
界面        Phase 6 完成（src/ui/ 十个模块，Renderer 为协调者）
节奏        Phase 6 补丁（Game.speed 可调速 + AI 响应排队）
测试        178 项全绿，compileall 通过，Pygame dummy 通过，多人长局零异常
```

Phase 7 前的关键事实（决定了架构方向）：

- `EventDispatcher` 已经是**带 priority / owner / 可退订**的同步事件总线，UI 的特效层就在用它。
- `Atom` + `apply_atom` 已经是唯一的状态修改入口，并且每个 Atom 前后都会发事件。
- `engine/skills.py` 里**已经有** `Skill` / `SkillBinding` 抽象（装备技能在用），但只被装备系统使用，没有注册表、没有 owner 级绑定管理、没有生命周期。

因此 Phase 7 是**扩展**既有基础设施，不是新建体系。

---

## 3. 现有 EventDispatcher / Atom 审计

### 3.1 事件覆盖度（对照需求第二十条的 24 个扩展点）

| 需求扩展点 | 现有事件 | 状态 |
|---|---|---|
| 使用牌前 / 后 | `CARD_USE_BEFORE` / `CARD_USED` / `CARD_USE_FINISHED` | 已有 |
| 指定目标前 / 后 | `TARGET_SELECTED` | 已有 |
| 成为目标前 / 后 | `BECOME_TARGET` | 已有 |
| 造成伤害前 / 后 | `DAMAGE_SOURCE_BEFORE` / `DAMAGE_SOURCE_AFTER` | 已有 |
| 受到伤害前 / 后 | `DAMAGE_TARGET_BEFORE` / `DAMAGE_TARGET_AFTER` | 已有 |
| 摸牌前 / 后、回复前 / 后、卡牌移动 | `ATOM_BEFORE` / `ATOM_AFTER`（带 `atom` 与 `result`） | 用 Atom 覆盖 |
| 判定前 / 后 | `JUDGE_STARTED` / `JUDGE_BEFORE_RESULT` / `JUDGE_RESULT` / `JUDGE_FINISHED` | 已有 |
| 阶段开始 / 结束、回合开始 / 结束 | `PHASE_START` / `PHASE_END` / `TURN_START` / `TURN_END` | 已有 |
| 进入 / 脱离濒死、死亡 | `DYING_ENTERED` / `DYING_EXITED` / `DEATH` | 已有 |

### 3.2 审计结论与两处补强

1. **不新建 EventBus**：技能直接订阅现有 `EventDispatcher`（`Skill.install` 已经这么做）。
2. **补一个事件**：`DAMAGE_SETTLED`（伤害完全结算之后，含濒死与死亡处理）。原先只有 `DAMAGE_TARGET_AFTER`，它发生在濒死检查之前，无法正确表达"受到伤害后"类技能（刚烈、遗计）。这是一个**只读通知**，不改变任何结算顺序。
3. **补一个 Atom 级保护**：`Atom.cancelled` 已存在（技能可以取消一次 Atom），无需新机制。

---

## 4. 最终 Skill 架构

```text
                    Game
      ┌──────────────┼───────────────┐
      │              │               │
  generals       skill_registry   modifiers
（武将定义表）    （技能定义表）    （持续修正表）
      │              │               │
      └──────► SkillManager ◄────────┘
                    │
        ┌───────────┴────────────┐
   Skill 实例（每玩家一份）    Modifier（带 owner）
        │
   EventDispatcher + Atom ──► 规则层（DamageFlow / TurnFlow / …）
```

四条铁律：

1. **技能不写进规则**：规则文件里没有 `general_id` 判断（测试会扫描核心文件）。
2. **状态修改只走 Atom / Flow / 既有 Game API**：技能不直接 `target.hp -= 1`。
3. **确定性**：事件分发按 `priority` → 注册顺序；注册顺序 = 座次顺序。
4. **可解绑**：每个技能实例与 modifier 都能被精确卸载，重复绑定会被拒绝。

---

## 5. GeneralDef / GeneralRegistry

`src/game/generals/`：

```python
@dataclass(frozen=True)
class GeneralDef:
    id: str            # 稳定 ID："zhangfei"，中文名只用于显示
    name: str
    kingdom: str = "qun"      # wei / shu / wu / qun
    gender: str = "male"
    max_hp: int = 4
    skill_ids: Tuple[str, ...] = ()
    title: str = ""
    description: str = ""
    portrait: Any = None      # 本阶段不下载任何素材，保持 None
```

```python
registry = GeneralRegistry()
registry.register(general)
registry.get("zhangfei") / require(...) / list_generals() / ids() / by_kingdom("shu")
```

已内置 3 个武将（`catalog.py`）：

| id | 名称 | 势力 | 体力 | 技能 |
|---|---|---|---|---|
| zhangfei | 张飞 | 蜀 | 4 | 咆哮 |
| huangyueying | 黄月英 | 蜀 | 3 | 集智 |
| xiahoudun | 夏侯惇 | 魏 | 4 | 刚烈 |

GeneralDef 里**没有一行技能逻辑**，只有技能 id 引用。

---

## 6. SkillDef / SkillRegistry

`src/game/skills/definitions.py`：

```python
@dataclass(frozen=True)
class SkillDef:
    id, name, description
    kind: SkillKind          # PASSIVE / LOCKED / ACTIVE
    factory: Callable[[player], Skill] | None      # 触发式技能的运行时对象
    can_activate / activate                        # 主动技能
    modifiers: Tuple[ModifierSpec, ...]            # 持续修正
    general_id, tags
```

一个技能可以只用其中任意组合：

| 组合 | 例子 |
|---|---|
| 只有 `modifiers` | 咆哮（纯 modifier，零事件监听） |
| 只有 `factory` | 集智、刚烈 |
| 只有 `activate` | 探针 F 回收 |
| 混合 | 未来的"英姿 + 反间"型武将 |

`SkillRegistry`（定义表）与 `SkillManager`（运行时）严格分离：

```python
skill_registry.register(defn)     # 定义：全局唯一
skill_manager.bind(player, id)    # 实例：每玩家一份
```

`SkillManager` 额外提供 `registry` 注入点，测试可以换成"正式技能 + 探针"的注册表。

---

## 7. Skill 生命周期

```text
开局 / reset
  → SkillManager.clear()            卸载全部实例 + 清空 modifier + 清空技能状态
  → 重建角色（Player.skill_state 重新注入）
  → assign_generals()               按座次顺序绑定武将技能

角色死亡
  → DEATH 事件（"死亡时"技能仍有响应机会）
  → SkillManager.on_player_death()  卸载该玩家普通技能 + 其 modifier + 技能状态

返回主菜单 / 重新开局
  → 与 reset 相同路径
```

关键设计：

- **绑定即拒绝重复**：`bind()` 发现同一玩家已有同名技能会直接抛错，从源头杜绝 listener 叠加。
- **卸载按 owner**：`unregister_owner_skill(player, skill_id)` 同时匹配拥有者与技能 id，因此两名玩家持有同一技能时互不影响。
- **状态随技能消失**：解绑时调用 `skill_state.drop_skill(id)`，不留残渣。
- **递归深度保护**：`Skill.MAX_DEPTH = 12`，超过则放弃当次触发（不是禁止嵌套）。

---

## 8. Hook pipeline

技能的触发路径完全复用现有事件系统：

```text
规则执行 → context.emit(Event(...)) → EventDispatcher.dispatch
        → 按 (-priority, 注册顺序) 依次调用 Skill._handle_event
        → can_trigger() 过滤 → resolve() 处理 → 可再触发 Atom / Flow
```

- **before 类**：`DAMAGE_MODIFY`、`ATOM_BEFORE`、`CARD_EFFECT_BEFORE`、`JUDGE_BEFORE_RESULT` 等，技能可以改数值、改目标、取消事件。
- **after 类**：`DAMAGE_SETTLED`、`ATOM_AFTER`、`JUDGE_FINISHED`、`DEATH` 等，技能只观察并触发新动作。
- 装备技能（藤甲、白银狮子、仁王盾、八卦阵…）仍在同一条管线上，未做任何迁移；它们与新技能共用 `Skill` 基类与 `EventDispatcher`。

---

## 9. Before Hook

支持的干预方式（全部已在探针中验证）：

| 干预 | 机制 | 探针 |
|---|---|---|
| 修改数值 | 改 `event.payload["damage"].amount` | A 铁骨 `-1` |
| 累加修改 | 多个技能按稳定顺序依次改 | A + 加压 `+1` → 净 0 |
| 取消效果 | 把 amount 归零并 `cancelled = True` | 免疫探针 |
| 取消一次 Atom | `event.cancel()`（`ATOM_BEFORE`） | 既有能力 |

顺序由 `SkillBinding(priority=...)` 决定，测试 `test_hook_modification_order_is_stable` 与 `test_hook_priority_beats_registration_order` 覆盖。

---

## 10. After Hook

- 只读观察，不改写已经发生的事实。
- 可以触发新的 Atom / Flow（嵌套）。
- 新增的 `DAMAGE_SETTLED` 让"受到伤害后"技能在濒死与死亡处理之后触发，语义正确。

探针 B（血偿：受伤后摸一张）与刚烈（受伤后判定 + 反伤）都在这一层工作。

---

## 11. Context 设计

两种情况分别处理：

1. **事件型**：直接用现有 `Event`（`source` / `target` / `payload`），payload 里带 `damage`、`card`、`flow`、`phase`、`result` 等。技能从 `event.payload` 取所需，不传整个 Game 的任意可写引用。
2. **查询型**（距离 / 手牌上限 / 摸牌数 / 攻击范围 / 出杀额度）：通过 `ModifierRegistry.total(kind, **query)`，query 是具名键（`source` / `target` / `player`），modifier 声明自己关心哪个角色（`roles`）。

技能的 `resolve(context, event)` 拿到的是 `GameContext`（`context.state` 即 Game，`context.apply(atom)` 是唯一的状态修改入口）。

---

## 12. Modifier / Query 体系

`src/game/skills/modifiers.py`：

```python
class ModifierKind(str, Enum):
    DISTANCE_OUTGOING   # 自己计算到别人的距离修正（马术 = -1）
    DISTANCE_INCOMING   # 别人计算到自己的距离修正（飞影 = +1）
    ATTACK_RANGE        # 攻击范围修正
    HAND_LIMIT          # 手牌上限修正
    DRAW_COUNT          # 摸牌阶段摸牌数修正
    SLASH_QUOTA         # 出杀额度（咆哮 = 视为无限）
```

```python
@dataclass
class Modifier:
    kind, value（int 或 callable）, owner, skill_id, priority, order, roles, condition
```

规则层统一入口（`Game`）：

```python
game.distance_modifier(source, target)   # 距离
game.attack_range_bonus(player)          # 攻击范围
game.hand_limit(player)                  # 手牌上限，默认 hp
game.draw_count(player, base=2)          # 摸牌阶段摸牌数，默认 2
game.slash_quota(player)                 # 出杀额度
```

**接入点（全部是"改一处、全局生效"）**：

| 查询 | 接入位置 |
|---|---|
| 距离 | `rules/distance.py` 的 `DistanceRule.distance` |
| 攻击范围 | `DistanceRule.attack_range`（`in_attack_range` 使用它） |
| 手牌上限 | `flows/turn.py` 弃牌阶段、`controllers/ai.py` 弃牌、`turn.py` 真人弃牌提示与判定 |
| 摸牌数 | `flows/turn.py` 摸牌阶段 |
| 出杀额度 | `equipment.py` 的 `can_use_unlimited_sha`（诸葛连弩与新技能共用这一入口） |

顺序稳定：`(-priority, 注册顺序)`，测试 `test_modifier_order_is_stable` 覆盖。

---

## 13. Skill State / Marks

`src/game/skills/state.py`：`player.skill_state`（`SkillState`）按 `(skill_id, key)` 存储，并带清理作用域。

```python
state.set("jizhi", "drawn", 1, ResetScope.TURN)
state.add("ganglie", "judged", 1, ResetScope.TURN)
state.get("probe_recycle", "used", 0)
state.clear_scope(ResetScope.TURN, skill_ids=("jizhi",))
```

四种作用域：`TURN` / `PHASE` / `ROUND` / `PERSISTENT`。需求要求的第一版三种（每阶段一次、每回合一次、永久）都在其中。

**没有往 Player 上加任何技能专属字段** —— `Player` 上只多了 `general_id` 与 `skill_state` 两个通用槽位。

---

## 14. 主动技能入口

```python
definition = SkillDef(
    id="probe_recycle", kind=SkillKind.ACTIVE,
    can_activate=_can_recycle,     # (game, player) -> bool 或 (bool, reason)
    activate=_activate_recycle,    # (game, player, **params) -> Any
)

game.skills.can_activate(player, "probe_recycle")   # (True, "") / (False, 原因)
game.skills.activatable_skills(player)              # 当前可发动的主动技能
game.skills.activate(player, "probe_recycle", card=card)
```

- 可发动性判断集中在技能自己的 `can_activate`，UI 只读结论。
- 主动技能的执行通过 `context.apply(Atom)` 与既有 Pending / Selection API 完成，**不在 UI 里硬写**。
- 探针 F 实现了"出牌阶段限一次：弃一张手牌摸一张"，限制用 `ResetScope.TURN` 状态表达。
- 本阶段不做完整的"技能按钮 UI"，Phase 8 的选将/技能界面直接调用这两个入口即可。

---

## 15. Damage 扩展验证

| 探针 / 武将 | 路径 | 结果 |
|---|---|---|
| 铁骨（`probe_iron_body`） | `DAMAGE_MODIFY` 改 amount | 2 点伤害变成 1 点 |
| 加压（测试技能，+1） | 同事件、不同 priority | 与铁骨叠加成净 0，顺序稳定 |
| 免疫（测试技能） | amount 归零 + `cancelled` | 完全抵消 |
| 血偿（`probe_blood_draw`） | `DAMAGE_SETTLED` → 摸牌 | 受伤后摸一张 |
| 刚烈（夏侯惇） | `DAMAGE_SETTLED` → 判定 → 反伤 | 黑桃反伤 1 点、红桃不触发 |
| 藤甲 / 白银狮子 | 既有装备 hook | 未回归：火焰 +1、大于 1 的伤害压到 1 |

## 16. Draw 扩展验证

- `probe_extra_draw`（博闻）声明 `DRAW_COUNT = +1`，`game.draw_count(player)` 从 2 变 3，回合摸牌阶段实际摸 3 张。
- 集智（黄月英）在使用锦囊时通过 `ATOM_AFTER` 路径摸 1 张：无中生有从"净 +1"变成"净 +2"（实测通过）。
- `DrawCardsAtom` 本身仍可被 `ATOM_BEFORE` 取消或改写，不需要为摸牌单独造事件。

## 17. Phase 扩展验证

- `probe_play_phase`（锐意）订阅 `PHASE_START`，在 `TurnFlow.begin_interactive()` 进入出牌阶段时把计数 +1（实测为 1）。
- 阶段跳过（`PhaseControl`）与阶段事件共存，技能可以观察到"被跳过的阶段"（payload 带 `skipped`）。

## 18. Distance 扩展验证

- `probe_nimble`（轻身）声明 `DISTANCE_OUTGOING = -1`：4 人局中 P1 到 P3 的距离从 2 变 1，解绑后恢复为 2。
- `ATTACK_RANGE` 修正同样生效：加了 +3 之后原本打不到的目标变成合法目标。
- `DistanceRule.distance` 的查询顺序是"环形基础距离 → 坐骑 → 技能修正 → 下限 1"，马术/飞影一类技能不需要改 `SeatManager`。

## 19. Active Skill 验证

`probe_recycle`（回收）：
- 出牌阶段限一次（第二次 `can_activate` 返回 False，原因是"本回合已经发动过"）。
- 不在自己回合 / 没有手牌时被正确拒绝并给出原因。
- 执行后：弃掉的牌进入弃牌堆、手牌净数不变（弃 1 摸 1）。

## 20. 多玩家同技能验证

`test_the_same_skill_on_two_players_is_independent`：
- P1、P2 同时绑定血偿，事件触发时**只有受伤者自己**摸牌；
- 解绑 P1 之后 P2 的监听仍然存在（`unregister_owner_skill` 同时匹配 owner 与 skill id）；
- 技能状态互不干扰（各自 `skill_state` 命名空间）。

## 21. Death / Reset 生命周期

| 场景 | 结果 |
|---|---|
| 阵亡 | `on_player_death` 在 DEATH 事件之后执行：卸载普通技能 + modifier + 技能状态 |
| 死亡瞬间的技能 | 受伤致死时 `DAMAGE_SETTLED` 仍能触发（技能此刻尚未卸载） |
| 连续 reset 4 次 | 每次重新绑定，`SLASH_QUOTA` 修正恰好 2 个（两名张飞），无叠加 |
| restart 3 次后受伤 | 技能只触发一次（不是三次） |
| 返回主菜单 | 监听器 0、modifier 0、技能状态 0 |
| 旧对局泄漏 | 同一个 Game 对象 reset 后，旧技能在新对局里完全不生效 |

---

## 22. 新增 / 修改文件

**新增**

```text
src/game/generals/__init__.py        导出与默认注册表
src/game/generals/definitions.py     GeneralDef
src/game/generals/registry.py        GeneralRegistry
src/game/generals/catalog.py         3 个标准武将数据

src/game/skills/__init__.py          统一导出 + 默认/探针注册表工厂
src/game/skills/definitions.py       SkillDef / ModifierSpec / SkillKind
src/game/skills/registry.py          SkillRegistry + SkillManager
src/game/skills/modifiers.py         ModifierKind / Modifier / ModifierRegistry
src/game/skills/state.py             SkillState / ResetScope
src/game/skills/library.py           正式武将技能：咆哮 / 集智 / 刚烈
src/game/skills/probes.py            6 类架构探针（仅测试使用）

tests/test_engine_v2_phase7_skills.py   48 项测试
```

**修改（都是"接入点"，不是规则改写）**

```text
src/game/engine/events.py      + DAMAGE_SETTLED 事件
src/game/engine/skills.py      + MAX_DEPTH 嵌套保护、installed 属性、reset_depth()
src/game/flows/damage.py       伤害完全结算后发 DAMAGE_SETTLED（只读通知）
src/game/flows/death.py        死亡结算后调用 skills.on_player_death()
src/game/flows/turn.py         摸牌数、弃牌上限改走 game.draw_count / game.hand_limit
src/game/rules/distance.py     距离与攻击范围接入 modifier 查询
src/game/equipment.py          can_use_unlimited_sha 接入 SLASH_QUOTA
src/game/controllers/ai.py     弃牌上限改走 game.hand_limit
src/game/turn.py               真人弃牌上限改走 game.hand_limit
src/game/core.py               Game 持有 generals / skill_registry / skills / modifiers；
                               新增 set_general / assign_generals / 五个规则查询方法
src/player.py                  仅新增 general_id 与 skill_state 两个通用字段
tools/multiplayer_smoke.py     支持 general_pool 参数（带武将长局压测）
```

---

## 23. 是否修改核心 Flow

**没有改规则语义**。核心文件里只有以下三类改动：

1. **取值改为统一查询**：`flows/turn.py` 的摸牌数与弃牌上限、`rules/distance.py` 的距离与攻击范围、`equipment.py` 的出杀额度。行为在无技能时与之前完全一致（178 项旧测试全绿即为证据）。
2. **新增只读通知**：`DAMAGE_SETTLED` 事件、死亡后的技能卸载调用。
3. **基础设施增强**：`Skill` 基类的嵌套深度保护。

**核心规则文件里没有任何具体武将判断**，测试 `test_generals_do_not_leak_into_core_flows` 会扫描以下文件并断言不存在 `general_id == …` / `general.name == …` 这类代码：

```text
flows/damage.py  flows/turn.py  flows/dying.py  flows/death.py
rules/distance.py  rules/seats.py  rules/targeting.py
equipment.py  cards.py  card_effects/*.py
```

---

## 24. 架构探针技能

`src/game/skills/probes.py`（模块 docstring 明确标注 DEVELOPMENT ONLY，且**不进入默认注册表**）：

| 探针 | 验证的能力 | 结果 |
|---|---|---|
| A 铁骨 `probe_iron_body` | before hook 改数值 | 2 → 1 |
| B 血偿 `probe_blood_draw` | after hook 触发新 Atom | 受伤后摸 1 张 |
| C 博闻 `probe_extra_draw` | 持续 modifier（摸牌数） | 2 → 3 |
| D 锐意 `probe_play_phase` | 阶段 hook | 出牌阶段计数 1 |
| E 轻身 `probe_nimble` | 距离 modifier | 距离 -1 |
| F 回收 `probe_recycle` | 主动技能 + 每回合限一次 | 弃 1 摸 1，第二次被拒 |

测试还会临时注册 3 个额外探针（加压 +1、免疫取消、连环嵌套）验证顺序、取消与嵌套。

---

## 25. 测试

```text
python -m compileall -q main.py src tests tools    → 通过
python -m unittest discover -s tests               → Ran 226 tests, OK
```

| 测试模块 | 用例数 |
|---|---|
| Phase 1～4 规则测试 | 48 |
| test_engine_v2_phase5 / phase5_multiplayer / phase5_ui | 14 / 38 / 9 |
| test_engine_v2_phase6_ui（含节奏控制） | 47 |
| legacy 1v1 兼容 | 22 |
| **test_engine_v2_phase7_skills（本阶段新增）** | **48** |
| **合计** | **226** |

Phase 7 新增 48 项，覆盖需求第四十一条的全部要点：GeneralRegistry（4）、SkillRegistry（3）、绑定解绑与状态隔离（6）、Hook（before/after/顺序/取消/嵌套/优先级，8）、Modifier（6）、主动技能（5）、生命周期（6）、三个正式武将（5）、装备不回归与无武将继续如常（5）。

重点用例：

```text
test_repeated_restart_keeps_exactly_one_listener   连续 reset 4 次后修正数仍然是 2
test_restart_does_not_double_fire_a_hook           重开 3 次后技能只触发一次
test_old_game_listeners_do_not_leak_into_new_game  旧对局的技能在新对局里不生效
test_the_same_skill_on_two_players_is_independent  同技能多玩家互不干扰
test_hook_modification_order_is_stable             -1 与 +1 的叠加结果确定
test_generals_do_not_leak_into_core_flows          核心规则无武将判断
test_equipment_damage_rules_still_work             藤甲 / 白银狮子未回归
```

---

## 26. 自动多人对局

复用 `tools/multiplayer_smoke.py`（脚本化真人 + 引擎 AI，真实节奏模式）：

| 批次 | 对局数 | 异常 |
|---|---|---|
| 2～8 人（无武将） | 42 | 0 |
| 2～8 人（3 个标准武将按座次轮转） | 42 | 0 |

带武将的一局样例：

```text
武将分配：玩家=张飞(4)、AI 1=黄月英(3)、AI 2=夏侯惇(4)、AI 3=张飞(4)、AI 4=黄月英(3)
技能绑定：AI 2=(刚烈)、AI 3=(咆哮)、AI 4=(集智)；阵亡角色的技能已被正确卸载
结果：正常结束（无卡死、无残留 Pending）
```

被动 / 锁定技对 AI 完全生效（咆哮、刚烈、集智都不需要 AI 主动决策）。

---

## 27. Pygame dummy

- `tools/ui_smoke.py`：布局场景 A～F 共 11 项 + dummy 完整交互流程 12 步，全部通过。
- Phase 7 未改动任何 UI 代码；SeatCard 头像仍是占位圆环，符合需求第二十九条（Phase 8 再做立绘与选将界面）。
- `Player.general_id` / `general_of(player)` 已可作为只读接口供未来 UI 使用。

---

## 28. 已知问题

1. **判定牌替换尚未支持**：司马懿【鬼才】、张角【鬼道】需要"在判定牌生效前替换它"。现有 `JUDGE_BEFORE_RESULT` 事件已经存在，但 `JudgeResult` 是 frozen dataclass，技能无法改牌。需要给 `JudgeFlow` 增加一个通用的"判定牌可被替换"能力（预计 30 行以内，不涉及规则分支）。
2. **卡牌转化尚未支持**：赵云【龙胆】、关羽【武圣】这类"把这张牌当另一张牌使用/打出"需要"虚拟牌 + 技能提供替代牌名"的能力。当前 `RespondCardAction` 只接受牌名白名单，技能无法提供替代。建议在 Phase 8 加一个通用的 `CardConversion` 扩展点（技能声明"这张牌可视为 X"），而不是在响应流程里写 if。
3. **主动技能没有 UI 入口**：`can_activate` / `activate` 已经可用，但界面上还没有技能按钮。需求明确要求 Phase 7 不做选将与技能 UI，因此留到 Phase 8。
4. **AI 不主动发动技能**：需求允许；被动与锁定技已完全生效。
5. **只有 3 个武将**：本阶段刻意控制内容量，目的是验证架构。
6. **`ModifierKind.ATTACK_RANGE` 尚未有正式技能使用**：只被测试覆盖。
7. **技能状态作用域的 ROUND 尚未有轮次边界调用**：`ResetScope.ROUND` 已定义但没有"一回合结束"的统一钩子去清它（`TURN` 与 `PHASE` 已被回合/阶段流程使用）。需要时在 `TurnFlow` 里补一处 `clear_scope(TURN)` 调用即可。

---

## 29. Phase 8 建议

1. **先补两个扩展点**（各 30 行以内，仍是"扩展点"而非规则分支）：判定牌替换、卡牌转化。有了它们，标准包的大部分技能都能实现。
2. **再做选将界面**：开始菜单加"武将"入口，用既有 `theme` / `widgets`，展示 `GeneralDef.name / kingdom / max_hp / skill_ids`；`assign_generals({seat: general_id})` 已经是现成的绑定入口。
3. **SeatCard 显示武将**：头像圆环可以显示 `general.name` 首字，或在面板上加一行技能名；`general_of(player)` 是只读接口。
4. **主动技能的 UI**：在真人操作条旁加"技能"按钮，列出 `activatable_skills(player)`，点击调用 `game.skills.activate(...)`。
5. **技能提示**：技能触发时用现有 `Effects` 订阅层弹一个"【集智】"浮字，不需要改规则层。
6. **内容扩容节奏建议**：先做 8～12 个标准包技能（覆盖 modifier / trigger / active / 转化 / 判定五类各 2 个），确认没有新的扩展点需求之后，再批量补武将。

---

## 附：验收问题自答

> 如果明天要新增郭嘉、司马懿、夏侯惇、张辽、周瑜、孙尚香、赵云、张飞等武将，是否主要只需要新增 General / Skill 模块？

**大部分是，少数需要先补两个通用扩展点。** 逐个对照：

| 武将技能 | 需要的机制 | 现状 |
|---|---|---|
| 张飞 咆哮 | SLASH_QUOTA modifier | 已实现 |
| 夏侯惇 刚烈 | DAMAGE_SETTLED + 判定 + 嵌套伤害 | 已实现 |
| 黄月英 集智 | CARD_USED 触发 | 已实现 |
| 郭嘉 遗计 | DAMAGE_SETTLED → 摸两张 + 分配 | 触发可用；"分配"用现有 Pending 与 SelectCards 表达 |
| 郭嘉 天妒 | JUDGE_FINISHED → 获得判定牌 | 已实现 |
| 司马懿 反馈 | DAMAGE_SETTLED → 获得来源一张牌 | 已实现 |
| 周瑜 英姿 | DRAW_COUNT modifier | 已实现 |
| 周瑜 反间 | 主动技能 | 入口已实现 |
| 孙尚香 枭姬 | EQUIPMENT_LOST 触发 | 已实现 |
| 张辽 突袭 | 摸牌阶段改为获得他人手牌 | 摸牌可被 `ATOM_BEFORE` 取消 + 主动 Pending，路径通畅 |
| 司马懿 鬼才 | 判定牌替换 | **需补扩展点 1** |
| 赵云 龙胆 / 关羽 武圣 | 卡牌转化 | **需补扩展点 2** |

也就是说：12 个常见技能中 10 个今天就能只加 Skill 模块实现；剩下 2 类（判定替换、卡牌转化）需要在技能侧新增通用接口，而**都不需要往 DamageFlow / TurnFlow / TargetRule 里写武将判断**。

这正是 Phase 7 要达成的状态。
