# Engine V2 Phase 2：核心战斗链迁移报告 v0.1

- 日期：2026-09-16
- 范围：统一 PendingRequest；迁移杀、闪、伤害、濒死、自救与死亡核心链
- 保持范围：Python + Pygame、单机 1v1、现有 UI 与装备行为

## 1. 结论

Phase 2 已完成。Engine V2 现在真实承担以下链路：

```text
Human / AI
  → GameAction
  → GameEngine
  → UseCardFlow
  → ShaEffect
  → PendingRequest（闪）
  → RespondCardAction / PassPendingAction
  → DamageFlow
  → LoseHpAtom
  → DyingFlow
  → PendingRequest（桃/酒）
  → RecoverHpAtom
  → DeathFlow / 脱离濒死
```

普通、火、雷属性的实体杀在没有复杂 Legacy 装备交互时均走这条 V2 链。青龙、贯石斧、寒冰剑、八卦阵等复杂装备仍通过集中 compatibility boundary 回退原实现，避免本阶段顺带重写全部装备。

## 2. 新增核心模块

```text
src/game/engine/
├─ domain_actions.py  # 领域 Action
├─ pending.py         # PendingRequest / Manager / Resolution
├─ runtime.py         # GameEngine、Action dispatch、UI/动画适配
└─ state.py           # GameOutcome / GameResult

src/game/
├─ atoms_v2.py        # MoveCardAtom / LoseHpAtom / RecoverHpAtom
├─ flows/
│  ├─ use_card.py
│  ├─ damage.py
│  ├─ dying.py
│  └─ death.py
├─ card_effects/
│  └─ sha.py
├─ rules/
│  └─ card_use.py
└─ compat/
   └─ legacy_equipment.py
```

同时扩展了 Engine V2 事件类型，增加卡牌使用、目标、Pending、伤害、濒死和死亡时机。

## 3. 领域 Action

`src/actions.py` 继续只表示动画 Action，没有改变语义。

新的领域 Action 位于 `src/game/engine/domain_actions.py`：

- `UseCardAction`
- `RespondCardAction`
- `PassPendingAction`
- `ConfirmPendingAction`
- `SelectCardsAction`
- `ChooseOptionAction`

玩家旧 API 与 AI 都通过 `Game.submit_action()` 进入同一个 `GameEngine.submit()`。

### 玩家入口

```text
main.py 点击手牌
→ Game.player_use_card（Legacy input façade）
→ try_player_sha / commit_player_sha（合法性兼容包装）
→ UseCardAction
→ GameEngine
```

### AI 入口

```text
AIMixin.enemy_attack（仅做简单决策）
→ UseCardAction
→ 同一个 GameEngine / UseCardFlow
```

AI 决定出闪后不再进入 AI 专用结算函数，而是提交与玩家相同的 `RespondCardAction`。

## 4. PendingRequest

`PendingRequest` 字段包括：

- `request_id`
- `request_type`
- `source` / `target`
- `prompt`
- `allowed_cards`
- `min_cards` / `max_cards`
- `options`
- `context`
- `owner_flow`

支持的类型：

- `RESPOND_CARD`
- `CONFIRM`
- `SELECT_CARDS`
- `CHOOSE_OPTION`

`PendingManager` 同一时间只允许一个权威请求；不能覆盖未完成请求。非法 request ID、错误响应者、错误牌、失效手牌、非法选项或数量不会清除原请求。

### 与现有 UI 的关系

新核心流程以 `Game.engine.pending.current` 为权威状态。

现有 `ResponseSystem` 暂时作为 UI adapter：

```text
PendingRequest
→ ResponseSystem 显示/接收当前按钮和手牌点击
→ callback 只把点击转换成 RespondCardAction / PassPendingAction
→ PendingManager 恢复 owner Flow
```

因此 Renderer 和 `main.py` 不需要大规模修改。复杂装备的 Choice/CardSelection 暂时仍是 Legacy adapter；PendingRequest 已具备对应 request/action 类型，后续装备 Skill 化时迁入。

## 5. UseCardFlow 与 ShaEffect

`UseCardFlow` 是通用卡牌使用流程，不是专用 `UseShaFlow`。当前 registry 只注册 `SHA → ShaEffect`。

流程：

1. `validate_sha_use` 检查游戏结束、牌仍在手牌、目标数、阶段、杀次数和距离。
2. 发出 `CARD_USE_BEFORE`。
3. `MoveCardAtom` 将杀移出手牌，进入处理中状态。
4. 发出 `CARD_USED`、`TARGET_SELECTED`、`BECOME_TARGET`。
5. `ShaEffect` 发出 `CARD_EFFECT_BEFORE`。
6. 防具未直接抵消时创建要求闪的 `PendingRequest`。
7. 闪或不响应恢复同一个 UseCardFlow。
8. 未闪进入 `DamageFlow`。
9. 结束后杀通过 Atom 进入弃牌堆，发出 `CARD_EFFECT_AFTER` 和 `CARD_USE_FINISHED`。

Card 数据仍使用原 `Card`；规则效果已经开始从 Card 数据分离到 `ShaEffect`。

## 6. 闪响应

玩家仍必须手动选择闪或不响应。

```text
PendingRequest(RESPOND_CARD, allowed_cards={SHAN})
→ UI 点击
→ RespondCardAction
→ 验证 request / actor / card / hand zone
→ MoveCardAtom：闪进入弃牌堆
→ Flow.resume
→ 杀被抵消
```

AI 仍采用简单策略：有闪则出、无闪则不响应。但决策结果同样提交 `RespondCardAction` 或 `PassPendingAction`，不再直接调用另一套杀结算函数。

## 7. DamageFlow 与 HP Atom

`DamageContext` 包含：

- `source`
- `target`
- `amount`
- `nature`（normal/fire/thunder）
- `card`
- `effects`
- `cancelled`

事件时机：

```text
DAMAGE_CREATED
DAMAGE_SOURCE_BEFORE
DAMAGE_TARGET_BEFORE
DAMAGE_MODIFY
LoseHpAtom
DAMAGE_APPLIED
DAMAGE_SOURCE_AFTER
DAMAGE_TARGET_AFTER
```

V2 DamageFlow 不直接执行 `target.hp -= amount`。所有新伤害扣血经过 `LoseHpAtom`。

`LoseHpAtom` 本身只表示 HP 减少，因此未来非伤害“失去体力”也可直接使用 Atom，而不必伪装成 DamageFlow。

回复通过 `RecoverHpAtom`，并统一限制到 `max_hp`。

## 8. DyingFlow 与 DeathFlow

DamageFlow 检测到 `hp <= 0` 后启动 DyingFlow。

DyingFlow 数据已经包含：

- `dying_player`
- `source`
- `cause`
- `rescue_order`
- `current_rescuer`

当前 1v1 的 `rescue_order` 只有濒死者本人，但接口允许以后扩展多人轮流救援。

流程：

```text
DYING_ENTERED
→ 查找当前救援者可用桃/酒
→ PendingRequest
→ RespondCardAction
→ RecoverHpAtom
→ 仍 <= 0 则继续请求
→ > 0 发出 DYING_EXITED
→ 无牌或放弃则 DeathFlow
```

DeathFlow：

- 清理新 Pending 与旧 Response UI adapter。
- 设置 `game_over=True`、`phase="over"`。
- 发出 `DEATH`。
- 建立结构化 `GameResult`。
- 设置兼容字段 `game.winner`。

当前结构化结果：`PLAYER_WIN` / `AI_WIN`。

Legacy `player_dies()` / `enemy_dies()` 已变成 DeathFlow wrapper，因此复杂装备旧流程最终也能获得结构化结果。

## 9. 动画与规则推进

V2 核心规则不再通过 `CallbackAction` 继续：

- PendingRequest 负责暂停和恢复 Flow。
- Atom 立即提交逻辑状态。
- Flow 的正确性不依赖动画时间推进。
- `MoveCardAction` 仍用于表现牌移动。
- 动画 `on_finish` 仅维护 `table_cards` 这一显示层缓存，不决定伤害、响应、濒死或死亡是否继续。

Legacy 复杂装备流程仍使用旧 CallbackAction；将在 Phase 3 迁移。

## 10. 装备 Compatibility Adapter

`LegacyEquipmentCompatibility` 集中处理 V2 与旧装备规则的边界。

### 通过 V2 Event Hook 接入

- 仁王盾 / 藤甲的杀直接无效判断：`CARD_EFFECT_BEFORE`
- 青釭剑的无视防具：复用旧 rule query
- 古锭刀、藤甲火伤、白银狮子伤害修正：`DAMAGE_MODIFY`
- 诸葛连弩杀次数：V2 legality query 复用旧查询

Flow 和 ShaEffect 不含这些具体装备名。

### 仍整段回退 Legacy

- 雌雄双股剑
- 寒冰剑
- 青龙偃月刀
- 丈八蛇矛
- 贯石斧
- 方天画戟
- 朱雀羽扇
- 麒麟弓
- 八卦阵

回退判断只存在于 compatibility adapter，不散落进 V2 Flow。

## 11. 旧逻辑处理状态

### 已成为 wrapper / 路由器

- `BasicCardMixin.commit_player_sha`：适合 V2 的杀提交 `UseCardAction`；复杂装备回退旧结算。
- `AIMixin.enemy_attack`：适合 V2 的实体杀提交同一种 `UseCardAction`；复杂装备和丈八虚拟杀回退旧结算。
- `DyingMixin.player_dies` / `enemy_dies`：转发 DeathFlow。

### V2 主链不再调用

普通无复杂装备杀不再依赖：

- `resolve_player_sha`
- `resolve_player_sha_after_bagua`
- `request_player_shan`
- `request_player_shan_after_bagua`
- `apply_player_sha_damage`
- `resolve_enemy_sha_damage`
- Legacy 濒死自救函数

### 暂未删除

上述方法仍服务于复杂装备 compatibility fallback。现在删除会破坏青龙、贯石、寒冰、八卦等既有行为，因此保留到 Phase 3，而不是复制出第四套流程。

## 12. Game 状态边界

本阶段继续使用 `GameContext(state=legacy_game)`，没有强行搬迁完整 GameState，以避免 Renderer 和所有旧模块同时重写。

新增清晰边界：

- `Game.engine`：领域 Action 与 Flow runtime。
- `Game.pending_request`：当前权威 V2 请求。
- `Game.result` / `Game.winner`：结构化结果。
- `Game.submit_action()`：统一领域 Action 入口。

完整 GameState 抽取留到旧装备流程收敛以后进行。

## 13. 测试

新增 10 个 V2 核心测试：

1. UseCardAction 普通杀命中。
2. AI 闪通过 RespondCardAction。
3. 玩家闪暂停并恢复 UseCardFlow。
4. 玩家不出闪后进入伤害。
5. DamageFlow 通过 LoseHpAtom 扣血。
6. 桃自救。
7. 酒自救。
8. 无法自救进入 DeathFlow 并得到 AI_WIN。
9. 电脑死亡得到 PLAYER_WIN。
10. 藤甲通过 compatibility event hook 抵消普通杀。

非法响应牌不会消费 PendingRequest，也有断言覆盖。

总测试：

```text
原 Engine V2                  6
新增 V2 核心战斗            10
Legacy Baseline              18
Legacy 测试工具               4
合计                         38
```

38/38 通过。

## 14. 验证

```text
python -m compileall -q main.py src tests    PASS
python -m unittest discover -s tests -v     38/38 PASS
Pygame dummy main.py 一个事件循环           PASS
enemy_attack → UI 闪 → 回合继续模拟         PASS
```

项目未安装 pytest，继续使用标准库 unittest，没有新增第三方依赖。

## 15. 下一阶段建议

下一阶段应是 Engine V2 Phase 3：装备 Skill 化与 CardEffect/Rules 完整化，优先顺序：

1. 把八卦阵从 Legacy Choice/Callback 迁成 Pending + JudgeFlow。
2. 迁移青龙偃月刀，验证“杀被抵消后”的事件与子 UseCardFlow。
3. 迁移贯石斧，验证多选牌 Pending 和修改原效果结果。
4. 迁移寒冰剑，验证 DamageFlow 前替代伤害。
5. 将藤甲、仁王、白银、青釭、古锭从 compatibility hook 逐个变成正式 Skill。
6. 装备流程收敛后删除 `combat.py` 中对应旧分支。

不建议下一阶段直接开发锦囊、武将或多人。
