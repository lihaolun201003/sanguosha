# Engine V2 架构审计与渐进迁移计划 v0.1

- 审计日期：2026-09-16
- 项目：Python + Pygame 单机 1v1 三国杀原型
- 审计范围：`main.py`、`src/` 下全部 Python 源文件，重点追踪回合、基本牌、杀/闪、伤害、濒死、装备、AI、选择、响应、动画与渲染
- 本轮实施边界：仅新增 Engine V2 最小骨架及测试；不迁移现有牌、装备、UI、AI 或规则

## 1. 执行摘要

当前版本不是“没有架构”，而是一套以 `Game` 为中心、Mixin 分文件、动画队列充当 continuation scheduler 的可运行原型。它适合继续以 Strangler Pattern 演进，不适合推倒重写。

真正的核心问题不是某个文件太长，而是四件事叠在一起：

1. `Game` 同时保存状态、执行规则、组织流程、生成提示、驱动动画并承接输入。
2. 规则流程依赖动画完成回调继续，规则无法脱离 Pygame 时间轴独立运行。
3. 响应、二选一、通用选牌、丈八选牌是四套并列 pending 机制，缺少统一生命周期。
4. 玩家与 AI 走两套不同规则路径，装备能力以具体名称判断散落在流程中。

推荐方向是让旧 `Game` 暂时充当 V2 `GameContext.state` 的适配对象，先建立同步 Event/Hook、Atom 入口、可暂停 Flow 协议和统一 Skill Hook 接口。第一条真实迁移切片应是最小化的“普通杀 → 请求闪 → 命中伤害”，但在迁移前先补状态快照式回归测试。

## 2. 当前真实架构

### 2.1 运行时主链

```text
main.py / Pygame event loop
├─ 读取 Game 的 scene / busy / phase / pending 状态决定点击含义
├─ 菜单 → StartMenu
├─ choice → ChoiceSystem → 回调
├─ response → ResponseSystem → 回调
├─ pending_selection → CardSelectionMixin → 回调
├─ 丈八专用选择 → BasicCardMixin
├─ 出牌 → Game.player_use_card()
├─ 弃牌 → Game.player_discard()
└─ 每帧 Game.update(dt)
              ↓
        ActionQueue.update(dt)
              ↓
  MoveCardAction / WaitAction / CallbackAction
              ↓
  CallbackAction 继续规则结算并继续向队列追加动作

Renderer.draw(Game)
├─ 直接读取 Game / Player / Deck / ActionQueue
├─ 读取 response / pending_selection / zhangba 状态
├─ 调用 selection 查询方法
└─ 调用 game.get_distance() 显示规则结果
```

### 2.2 `Game` 的真实组成

`src/game/core.py` 中的 `Game` 通过多继承组合：

```text
Game
├─ CardSelectionMixin
├─ TurnMixin
├─ BasicCardMixin
├─ EquipmentMixin
├─ CombatMixin
├─ DyingMixin
└─ AIMixin
```

这种拆分降低了单文件长度，但没有形成对象边界。所有 Mixin 都默认可读写 `Game` 上的所有字段并互相直接调用，所以真实形态仍是一个共享可变状态的 God Object。

### 2.3 当前状态存放位置

当前没有独立 `GameState`。状态分散在：

- `Game`：场景、阶段、消息、游戏结束、玩家/电脑、牌堆、桌面牌、动画队列、响应、选择和回合临时标记。
- `Player`：体力、手牌、装备、性别。
- `Deck`：摸牌堆、弃牌堆。
- `ActionQueue`：当前动画/回调及等待队列，本质上也是流程进度状态。
- `ResponseSystem.current`、`ChoiceSystem.current`、`Game.pending_selection`：三类回调式等待状态。
- `Game.zhangba_selecting` / `zhangba_selected`：第四套专用等待状态。
- `Card` 动态属性：`_virtual`、`_original_nature`、`_cixiong_resolved` 保存单次结算临时状态。

因此“当前 Game State”其实是 `Game + Player + Deck + ActionQueue + pending objects + Card 动态属性` 的联合体。

## 3. Phase 0 问题逐项回答

### 3.1 Action 如何进入游戏

当前 `src/actions.py` 的 Action 指动画动作，不是领域 Action。

玩家意图由 `main.py` 直接根据 UI 状态分支翻译成具体 `Game` 方法调用，例如：

- 出牌：`player_use_card(index, rect)`
- 弃牌：`player_discard(index, rect)`
- 响应：`respond_with_card(index, rect)` / `pass_response()`
- 二选一：`ChoiceSystem.choose_yes()` / `choose_no()`
- 选牌：`select_pending_card(card, rect, key)`

AI 不是生成 Action，而是由 `AIMixin` 直接移牌、改状态、追加动画和调用后续规则。

### 3.2 “杀”如何完整结算

玩家侧真实路径：

```text
main 点击手牌
→ player_use_card
→ try_player_sha
→ 可选朱雀羽扇 ChoiceRequest
→ commit_player_sha
   - 再检查距离
   - 直接从手牌 remove
   - 设置 sha_used / 消耗酒增伤
   - MoveCardAction 到桌面
→ resolve_player_sha
   - 雌雄双股剑分支（通过递归回到自身继续）
   - 仁王盾/藤甲直接无效检查
   - 八卦阵判定
→ resolve_player_sha_after_bagua
   - AI 有闪就自动 remove 并动画弃置
   - 否则进入伤害
→ 闪抵消时 offer_player_missed_sha
   - 贯石斧优先
   - 然后青龙偃月刀
→ 命中时 resolve_player_sha_damage
   - 寒冰剑替代伤害
→ apply_player_sha_damage
   - 古锭刀/藤甲/白银狮子/青釭剑修正
   - 直接 enemy.hp -= damage
   - 麒麟弓
→ finish_player_sha_damage
→ 杀进入弃牌堆
→ after_enemy_took_damage
→ 必要时濒死
```

电脑侧由 `AIMixin.enemy_attack` 开始，随后走 `CombatMixin.request_player_shan`。它与玩家路径共享部分装备工具，却不是同一个 UseCardFlow，具体分支和决策仍重复。

### 3.3 “闪”如何响应

- 电脑响应玩家的杀：`resolve_player_sha_after_bagua` 直接检查并移除第一张 `SHAN`，没有通用响应请求。
- 玩家响应电脑的杀：`request_player_shan_after_bagua` 将 `phase` 改成 `response`，向 `ResponseSystem` 写入允许牌名和两个闭包。
- 点击后 `ResponseSystem.play_card` 先清空 `current`，再调用 `player_respond_shan`。
- 不响应同样先清空请求，再调用 `player_pass_shan`。

`ResponseSystem` 已经是一个有价值的雏形，但请求只支持“允许哪些牌名 + callback”，无法表达来源、目标、响应原因、转化牌、AI 决策、嵌套响应和 continuation identity。

### 3.4 伤害如何结算

没有统一 Damage 对象或流程。杀伤害分别在：

- `apply_player_sha_damage`：直接 `enemy.hp -= damage`
- `resolve_enemy_sha_damage`：直接 `player.hp -= damage`

伤害修正在 `EquipmentMixin.apply_sha_damage_modifiers` 中硬编码武器/防具顺序，且仅服务于“杀”。普通失去体力、锦囊伤害、连环伤害和未来受伤技能没有统一入口。

### 3.5 濒死如何处理

- 对电脑：伤害牌完成弃置后调用 `after_enemy_took_damage`，若 `hp <= 0`，AI 在 `enemy_try_rescue` 中循环使用桃或酒，最终恢复或死亡。
- 对玩家：`after_player_took_damage` 调用 `request_player_rescue`，复用 `ResponseSystem` 请求桃/酒；每次回复后再检查，直到 `hp > 0`、玩家放弃或无牌死亡。

优点是已支持负体力需要多张救援牌；限制是救援者固定为濒死角色自己、后续 continuation 固定回到“电脑杀结束”，无法直接扩展到其他伤害来源和多人轮询救援。

### 3.6 动画和逻辑如何耦合

耦合很深，`ActionQueue` 不只是表现层：

- 牌经常在创建动画前就从逻辑区域移除。
- 进入新区域常在 `MoveCardAction.on_finish` 才发生。
- 后续规则由 `CallbackAction` 排队触发。
- `busy` 同时代表“动画播放中”和“规则暂不可输入”。
- 规则模块必须知道 UI 矩形常量并传递 `source_rect` / `target_rect`。

因此去掉 Pygame 时间推进后，现有规则不会自然完成。第一阶段不能直接替换队列；应先建立规则变化与动画观察之间的边界。

### 3.7 装备技能在哪里介入

- `basic_cards.py`：诸葛连弩、丈八蛇矛、朱雀羽扇、方天画戟。
- `combat.py`：雌雄双股剑、贯石斧、青龙偃月刀、寒冰剑、麒麟弓，以及防具响应时机。
- `equipment.py`：距离、青釭剑、古锭刀、八卦阵、仁王盾、藤甲、白银狮子、装备替换。
- `dying.py`：电脑诸葛连弩继续攻击。
- `ai.py`：丈八蛇矛、方天画戟和诸葛连弩相关决策。
- `equipment_skills/*.py`：目前主要是按显示名判断装备及少量纯规则函数，并不是可注册的 Skill。

核心流程因此知道具体装备名与触发顺序。

### 3.8 主要临时状态

```text
Game.phase
Game.sha_used
Game.jiu_used
Game.player_wine_buff
Game.wine_sha_required
Game.enemy_wine_buff
Game.enemy_jiu_used
Game.zhangba_selecting
Game.zhangba_selected
Game.pending_selection
Game.table_cards
ResponseSystem.current
ChoiceSystem.current
ActionQueue.current / queue
Card._virtual
Card._original_nature
Card._cixiong_resolved
```

### 3.9 最容易造成卡死的状态和路径

1. `ChoiceSystem.request`、`ResponseSystem.request` 和 `start_card_selection` 都会无条件覆盖当前请求；没有防重入断言或请求 ID，旧 continuation 可永久丢失。
2. `select_pending_card` 在执行 callback 前先把 pending 清空。后续若发现所选牌已不在原区域并直接 `return`，流程没有恢复点。雌雄弃牌路径已有这种静默返回。
3. `phase` 既表示回合阶段又表示 `response` / `dying`。嵌套流程依靠手写恢复成 `enemy`，没有可恢复的父流程栈。
4. Generic selection 没有取消/超时/AI 兜底；未来任何候选列表为空或 UI 不支持的新 zone 都会锁住流程。
5. 动画 callback 是唯一 continuation。callback 抛错、未入队、被覆盖或条件分支提前返回都会使状态停留在不可操作阶段。

### 3.10 闪退与状态破坏风险

- 牌区没有统一所有权模型；`Deck.discard` 不检查重复，同一对象理论上可重复进入弃牌堆。
- 多处先保存对象引用，再在动画后或用户选择后按 identity 查找；区域变化时可能出现 stale selection。
- 麒麟弓等路径假设选中装备仍存在；若将来有并发 Hook 移除装备，`None` 可能进入动画/弃牌逻辑。
- 临时状态挂在 `Card` 对象上，只有某些弃牌路径负责清理；改变去向时可能把一次结算状态带回牌堆。
- `ActionQueue` 对 callback 异常没有隔离或诊断上下文，异常将直接退出主循环。
- `table_cards` 与真实区域分离，依赖对象 identity 清理；重复添加或遗漏清理会留下幽灵牌。

### 3.11 重复职责

- 玩家与 AI 分别实现桃、酒、装备、杀和弃牌。
- 玩家杀与电脑杀有两套近似但不同的杀/闪/伤害流程。
- 直接回血存在于基本牌、AI、濒死、装备卸下多个位置。
- 直接移牌散落于 `Player`、`Deck`、基本牌、战斗、AI、装备、濒死和回合模块。
- 消息、动画、规则与状态变更在同一方法中重复编排。

### 3.12 最适合第一批迁移的代码

在进入真实切片前，最适合先建立并验证：

1. 同步 `EventDispatcher`，允许优先级、取消和安全增删监听。
2. `Atom + apply_atom`，作为所有未来状态变化的唯一可观察入口。
3. 可暂停/恢复的同步 `Flow` 协议，贴合 Pygame 主循环，不引入 asyncio。
4. 统一 `Skill` Hook 接口。
5. Legacy `Game` → `GameContext.state` 适配器。

第一条真实业务切片应是普通杀的核心路径，但必须暂时排除酒、八卦、武器分支，先以 feature boundary 与旧流程并存。

### 3.13 可以保留的现有代码

- `Card`、`Player`、`Deck` 作为迁移期数据模型。
- `card_catalog.py` 的牌堆数据及现有 79 张左右开发牌堆。
- `ActionQueue` 与现有动画类，作为旧 UI 动画适配层。
- `Renderer` 的绘制实现和现有蓝/黄选牌反馈。
- `CardSelectionMixin` 的交互成果，先包装成新 Pending adapter，不立即删除。
- `ResponseSystem` / `ChoiceSystem` 的 UI 接口，先由统一 Request 适配。
- `equipment_skills` 中无副作用的装备判断函数，可在迁移期间复用作规则 oracle。
- 菜单、字体、布局、卡牌绘制、牌面数据。

### 3.14 本轮不要动的代码

- 不改任何现有装备规则与优先级。
- 不改牌堆构成、1v1 性别设定和开发阶段特殊规则。
- 不改 `main.py` 输入分支与 Renderer 行为。
- 不把动画队列一次性替换成 Flow scheduler。
- 不引入多人、武将、锦囊、判定区或联网。
- 不拆 `renderer.py`；应等规则查询从 renderer 移出并有 UI view model 后再拆。

## 4. 最大的五个技术问题

### P1. Mixin 只拆文件，没有拆依赖

所有规则共享并任意修改一个 `Game` 对象。方法调用图横跨所有 Mixin，无法只构造“伤害系统”或“杀流程”测试。

### P2. 动画队列承担规则 continuation

规则正确性依赖时间推进和 UI 坐标，导致 headless 测试困难；状态转移发生在动画前后不同位置，区位不变量不清晰。

### P3. Pending 模型碎片化且不可组合

响应、选择、二选一和丈八状态并列，覆盖请求没有保护，也没有 request ID、父流程、有效性重检、取消策略或统一 AI 入口。

### P4. 人类与 AI 不共用领域 Action/Flow

AI 直接操作状态，玩家从 UI 直接调用规则方法。相同牌存在两套实现，未来修规则很容易只修一边。

### P5. 状态变化无统一入口，技能只能硬插分支

扣血、回血、摸牌、弃牌、装备、阶段变化都直接修改字段。没有可观察 Atom 和稳定事件时机，日志、动画、技能、回放和测试只能继续侵入核心流程。

## 5. 推荐的 Engine V2 目标结构

按实际需要逐步长成，当前不一次性创建全部目录：

```text
src/game/
├─ engine/
│  ├─ context.py          # 运行时服务与 Legacy state adapter
│  ├─ events.py           # Event / Dispatcher
│  ├─ atoms.py            # Atom / apply_atom
│  ├─ flows.py            # 可暂停、恢复 Flow 约定
│  └─ skills.py           # Skill / Hook 约定
├─ state/                 # 真正迁移 GameState 时再创建
├─ actions/               # 领域 Action 成形时再创建；避免与现动画 actions.py 混淆
├─ atoms/                 # 出现 3 个以上真实 Atom 后再按职责拆
├─ flows/                 # UseCard/Damage/Dying/Turn 等真实流程
├─ events/                # 事件规模增大后从 engine 提升
├─ cards/                 # CardDefinition / CardEffect / registry
├─ skills/
│  ├─ equipment/
│  └─ characters/
├─ rules/                 # targeting/distance/usage/legality 纯查询
├─ selection/             # PendingRequest + adapters
└─ ai/                    # 只做决策，提交同一种领域 Action
```

重要约束：目录在职责出现时创建，不先生成空壳。

## 6. Engine V2 Migration Plan

### Step 1：最小基础骨架（本轮实施）

- 目的：建立可运行、可测试、完全旁路的 V2 扩展点。
- 修改范围：新增 `src/game/engine/`；在 `Game` 上挂载 `GameContext(state=self)`；新增标准库 `unittest`。
- 新建文件：`engine/__init__.py`、`context.py`、`events.py`、`atoms.py`、`flows.py`、`skills.py`、`tests/test_engine_v2.py`、本报告。
- 修改文件：仅 `src/game/core.py`，增加一个 import 和一个 context 初始化。
- 明确不修改：所有现有卡牌、装备、选择、响应、AI、动画、Renderer 和规则行为。
- 风险：新包 import 循环；初始化影响 Legacy Game；事件分发修改监听列表时不稳定。
- 验收标准：旧 `Game()` 正常创建；Context 指向旧 Game；Atom 可观察/可取消；Flow 可等待/恢复；Skill 可安装/卸载；事件优先级稳定。
- 回归测试：`compileall`；6 个 Engine V2 单元测试；Legacy Game 构造烟雾测试。

### Step 2：建立行为基线与区域不变量

- 目的：在迁移真实规则前固定当前可见行为，暴露重复弃牌和 stale selection。
- 修改范围：只增加 headless fixtures、固定牌堆和现有关键组合测试；必要时加只读 snapshot helper。
- 新建文件：`tests/legacy/fixtures.py`、`tests/legacy/test_sha_regressions.py`、`tests/legacy/test_dying_regressions.py`。
- 修改文件：优先不改；若需要只为可注入 RNG/牌堆增加构造参数。
- 明确不修改：规则结果、动画时长、UI。
- 风险：现流程依赖动画时间，测试可能脆弱。
- 验收标准：可在无窗口环境推进 ActionQueue 到 idle；关键组合结果可重复。
- 回归测试：普通杀命中/被闪；酒杀；火杀+藤甲；青釭+藤甲/仁王；白银狮子；濒死多次自救；青龙/贯石/寒冰。

### Step 3：统一最小 PendingRequest，保留旧 UI adapter

- 目的：让 Flow 能暂停，并由玩家或 AI 用同一种 Response 恢复。
- 修改范围：建立 typed request/response、request ID、合法性重检；适配 Choice/Response/CardSelection。
- 新建文件：`src/game/selection/requests.py`、`responses.py`、`legacy_adapter.py`。
- 修改文件：`core.py`、`main.py` 的输入转换层、现有三个 request system 的薄适配。
- 明确不修改：蓝框/黄框、按钮布局、现有装备行为。
- 风险：输入优先级改变；旧 callback 与新 continuation 双重执行。
- 验收标准：同一时刻只有一个 authoritative pending request；完成/取消后恰好恢复一次；AI 可响应同类型请求。
- 回归测试：闪/桃/酒、八卦是否发动、贯石两牌、寒冰敌方手牌、麒麟坐骑、雌雄弃牌、青龙选杀。

### Step 4：第一条真实垂直切片——普通杀/闪/1 点伤害

- 目的：验证 Action → UseCardFlow → Response → DamageFlow → Atom 的完整链。
- 修改范围：只迁移无酒、无装备介入的普通杀路径；旧复杂路径继续运行。
- 新建文件：`game/actions/use_card.py`、`flows/use_card.py`、`flows/response.py`、`flows/damage.py`、`atoms/move_card.py`、`atoms/damage.py`、`rules/card_usage.py`。
- 修改文件：`basic_cards.py` 增加受控路由；输入/AI 提交同一 UseCardAction；动画通过 Atom 事件适配。
- 明确不修改：元素杀、酒、装备、濒死的旧实现。
- 风险：一张牌在新旧系统被结算两次；动画观察与状态提交时机不一致。
- 验收标准：新路径不含具体装备名；玩家和 AI 可走同一规则入口；无 Pygame 点击即可测试命中和闪避。
- 回归测试：合法性、出牌次数、距离、闪成功、无闪伤害、卡牌最终区域、流程只能完成一次。

### Step 5：统一伤害与濒死

- 目的：所有伤害/回复/失去体力通过 Atom，濒死成为可复用 Flow。
- 修改范围：引入 DamageContext、DamageFlow、HealAtom、LoseHpAtom、DyingFlow、DeathFlow；适配旧动画。
- 新建文件：对应真实 atoms/flows 与事件定义。
- 修改文件：`combat.py`、`dying.py`、`basic_cards.py`、`equipment.py` 中相关入口逐条迁移。
- 明确不修改：装备技能决策语义和 1v1 自救限制。
- 风险：伤害修正顺序、伤害牌来源丢失、死亡后 continuation 继续。
- 验收标准：伤害前后事件 payload 完整；最终伤害只扣一次；濒死可从任意伤害来源进入；死亡终止父流程。
- 回归测试：普通/火/雷伤害；酒；古锭；藤甲；白银；青釭；-1 体力多牌自救；放弃自救。

### Step 6：装备技能逐个注册化

- 目的：核心 Flow 不再按装备名分支。
- 修改范围：按青龙 → 贯石 → 寒冰顺序，每次只迁移一件并保留回归基线；之后迁移其余装备。
- 新建文件：`skills/equipment/weapons/qinglong.py`、`stone_axe.py`、`ice_sword.py` 等，按实际复杂度创建。
- 修改文件：逐步删除 `combat.py` 对应分支；装备 registry 安装/卸载 Skill。
- 明确不修改：规则文本所列开发阶段限制。
- 风险：触发优先级改变；卸下装备后 listener 残留；递归青龙追杀丢失父上下文。
- 验收标准：装备 Skill 生命周期与装备区一致；核心流程无具体装备名；旧组合测试全过。
- 回归测试：杀+闪+青龙；贯石强命；寒冰替代；青釭与各防具；酒/藤甲/白银/古锭组合。

### Step 7：GameState、领域 Action 与回合 Flow

- 目的：把状态从 God Object 中抽出，让 UI/AI 只提交 Action。
- 修改范围：迁移 players 列表、current player、phase、zones、pending、game result；建立 Action validator/dispatcher；实现完整空阶段兼容的 TurnFlow。
- 新建文件：`state/game_state.py`、`state/zones.py`、领域 Action、`flows/turn.py`、phase 事件。
- 修改文件：`core.py` 退化为 façade；`main.py` 和 `ai.py` 改为 Action producer；Renderer 读取稳定 view state。
- 明确不修改：仍为 1v1，不实现身份、多目标或多人座次规则。
- 风险：兼容属性过多形成双真相；阶段与 pending 恢复不一致。
- 验收标准：只有 GameState 是权威状态；Action 合法性由 Engine 判定；玩家/AI 同入口；回合包含准备/判定/摸牌/出牌/弃牌/结束阶段。
- 回归测试：完整多回合、弃牌上限、酒/杀跨回合清理、游戏结束与重开。

### Step 8：卡牌定义/效果与规则查询分层

- 目的：新增牌主要注册 CardDefinition/CardEffect，不修改核心流程。
- 修改范围：把 Card 数据、目标规则、使用次数、效果实现分开；建立 registry；把距离/攻击范围/合法性变成无副作用规则服务。
- 新建文件：`cards/base.py`、`cards/registry.py`、`cards/basic/*`、`rules/*`。
- 修改文件：`card.py`/`card_catalog.py` 保留兼容 façade，逐步迁移；Renderer 改读展示模型而非调用规则方法。
- 明确不修改：此步本身不添加锦囊或武将。
- 风险：Card instance 与 definition identity 混淆；序列化字段变化。
- 验收标准：新增基本牌效果不改 UseCardFlow；Renderer 不判断规则；牌的临时结算数据在 context 而非动态挂在 Card。
- 回归测试：现有全部牌面、花色、点数、牌堆数量和基本牌行为。

### Step 9：锦囊、判定、武将、多角色（后续里程碑）

- 目的：在基础设施稳定后按普通锦囊 → 无懈响应链 → 判定/延时锦囊 → 少量武将 → 多角色顺序扩展。
- 修改范围：每类先做最小垂直切片验证抽象，再扩数量。
- 新建文件：只随真实功能创建 tricks/delayed_tricks/character skills/seat rules。
- 修改文件：核心 Flow 仅增加通用扩展点，不加入具体牌/武将判断。
- 明确不修改：联网最后考虑，不在这些步骤夹带实现。
- 风险：事件时机命名过早冻结；多目标和响应链暴露旧 1v1 假设。
- 验收标准：题述曹操、郭嘉、张飞、赵云、关羽、无懈、乐、闪电可通过 Flow+Atom+Event+Skill+Rule 组合实现，核心不堆特殊名判断。
- 回归测试：每个新机制先有最小规则矩阵，再做跨技能组合。

## 7. Step 1 实际设计

### 7.1 `GameContext`

- `state` 暂时引用旧 `Game`，明确这是迁移适配，不声称已经得到纯 GameState。
- 持有 `EventDispatcher` 和可选 services。
- 提供 `emit(event)` 与 `apply(atom)` 两个入口。

### 7.2 `EventDispatcher`

- 同步执行，符合 Pygame 单线程主循环。
- 支持优先级，相同优先级保持注册顺序。
- dispatch 使用监听快照，Hook 在分发时安装/卸载不会破坏本次遍历。
- 支持按 token 和 owner 卸载，为装备卸下/技能失效准备。
- Event 支持 cancel 与 stop propagation；两者语义分离。

### 7.3 `Atom`

- `apply_atom` 固定发出 before / after；before 可取消。
- Atom 必须返回 `AtomResult`，防止不同 Atom 随意返回不可预测结构。
- 本轮故意不实现 Draw/Damage/Heal 等真实 Atom，避免未经垂直切片验证就冻结字段。

### 7.4 `Flow`

- 状态：ready/running/waiting/completed/cancelled。
- `start()` 同步推进；需要输入时返回 waiting。
- Pygame 或 AI 得到响应后调用 `resume(response)`，不使用 asyncio，不阻塞主循环。
- Flow 生命周期发事件，便于日志、调试和未来父子流程管理。

### 7.5 `Skill`

- 声明多个 `SkillBinding(event_name, priority)`。
- `can_trigger` 与 `resolve` 分离。
- 安装/卸载通过 dispatcher token 完成。
- optional 仅作为元数据；发动询问要等统一 PendingRequest 后实现，不在骨架中伪造。

## 8. Step 1 验证

验证命令：

```powershell
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

测试覆盖：

1. Atom 只改变一次状态，before/after 可观察。
2. before Hook 可取消 Atom，状态不变。
3. Skill 可安装、触发、卸载且卸载后不再触发。
4. Flow 可等待并由普通同步调用恢复，不依赖异步框架。
5. Event 优先级和同级注册顺序稳定。
6. 旧 `Game` 可正常构造，`game.context.state is game`，且没有默认 Hook 改变行为。

## 9. 下一步建议

下一步不要立即迁移青龙或伤害。先做 Step 2：建立可控牌堆、动作队列 drain helper 和现有规则组合回归矩阵。尤其先固定以下组合的当前结果：

```text
普通杀命中 / 被闪
酒 + 杀
火杀 + 藤甲
青釭剑 + 藤甲
青釭剑 + 仁王盾
杀 + 闪 + 青龙偃月刀
杀 + 闪 + 贯石斧
杀 + 寒冰剑
杀 + 古锭刀
杀 + 白银狮子
濒死时从 -1 体力连续自救
```

有了这层保护后，再迁移“无装备普通杀 → 闪 → 1 点伤害”垂直切片，风险最低。
