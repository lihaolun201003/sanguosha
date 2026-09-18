# Engine V2 Phase 3：装备 Skill、锦囊与牌堆基础

## 结论

Phase 3 已完成。当前实际运行链统一为 `UseCardAction -> GameEngine -> UseCardFlow -> CardEffectRegistry`。项目目标牌池明确为标准版 + 军争篇，不包含国战模式、国战卡牌、双将或国战规则。

## 装备 Skill 化

已登记并接入 15 件装备与坐骑距离规则：诸葛连弩、雌雄双股剑、寒冰剑、青釭剑、古锭刀、青龙偃月刀、丈八蛇矛、贯石斧、方天画戟、朱雀羽扇、麒麟弓、八卦阵、仁王盾、藤甲、白银狮子，以及进攻马/防御马的统一距离规则。

- 锁定技通过 `EquipmentEventSkill` 订阅卡牌效果、伤害修正和失去装备事件。
- 可选技由 `EquipmentSkillController` 创建 `CONFIRM`、`CHOOSE_OPTION`、`SELECT_CARDS` 请求，并恢复原 `UseCardFlow`。
- 青龙追杀重新提交 `UseCardAction`，没有复制杀结算。
- 贯石斧只允许选择两张手牌。
- 寒冰剑只选择目标手牌并防止原伤害。
- 八卦阵由玩家确认，实际调用 `JudgeFlow`；红色视为闪，黑色回到正常闪请求。
- 青釭剑统一走 `ArmorRule.is_effective()`，防具不识别青釭剑名称。
- 诸葛连弩仍由使用次数 Rule 查询；坐骑由 `DistanceRule` 查询。
- 丈八蛇矛的两张素材通过 `MoveCardAtom` 进入弃牌堆，虚拟杀进入标准 `UseCardFlow`。
- 方天画戟保留 `extra_targets` 结构；1v1 不增加目标。
- 白银狮子失去装备回血已从 `equipment.py` 的名称分支迁至装备事件 Skill。

## Legacy 退出情况

`LegacyEquipmentCompatibility` 已变成无结算能力的薄 API 壳；`supports_v2_sha()` 不再根据复杂武器/八卦阵把杀分流回旧系统。原 adapter 中防具拦截和伤害修正订阅已删除。真实实体杀与丈八虚拟杀均进入 V2。

为保护原 18 个 Legacy baseline 和现有 Pygame 输入 façade，`combat.py` 中旧函数暂保留为未被 V2 主链调用的兼容表面；本阶段不做高风险的大文件机械删除。`equipment.py` 的活动职责收敛为距离 façade、装备/替换/卸下与动画，特殊结算由 Skill/Event 接管。

## CardEffectRegistry 与目标规则

新增 `CardEffect`、`CardEffectRegistry`，注册 8 个效果：杀及七张锦囊。物理 `Card` 只保存 id、名称、类别、花色、点数、属性等数据；规则保存在 Effect 中。

`TargetRule` 支持 `NO_TARGET`、`SELF`、`SINGLE_OTHER`、`SINGLE_ANY`、`MULTIPLE`、`ALL_OTHERS`、`ALL_PLAYERS`。南蛮入侵、万箭齐发使用 `targets[] = 所有其他存活角色`；桃园结义使用所有存活角色。当前 UI 在 1v1 中自动形成合法目标集合，规则层不写死 enemy。

`DistanceRule` 统一处理基础距离、进攻马、防御马、攻击范围和顺手牵羊距离 1。

## JudgeFlow

`JudgeFlow` 从牌堆顶取得一张牌，生成包含 `card/suit/color/rank` 的 `JudgeResult`，依次发出开始、展示、完成事件，并通过 `MoveCardAtom` 将判定牌放入弃牌堆。当前八卦阵已实际复用该 Flow；Phase 4 的延时锦囊可以直接扩展。

## 新增锦囊

- 无中生有：`DrawCardsAtom` 摸 2。
- 过河拆桥：选择目标手牌或装备，`MoveCardAtom` 弃置。
- 顺手牵羊：距离 1，选择目标手牌或装备并移动到使用者手牌。
- 决斗：可暂停/恢复的交替杀响应；响应杀不增加出牌阶段杀次数。
- 南蛮入侵：逐目标请求杀。
- 万箭齐发：逐目标请求闪。
- 桃园结义：逐存活角色执行 `RecoverHpAtom(1)`，不超过上限。

五谷丰登未实现，按任务许可留到 Phase 4，避免在本阶段引入公共牌池 UI。

## 正式实体数据与当前牌堆

新增锦囊均是带唯一 `Card.id`、真实花色和点数的独立实体：

- 无中生有：♥7、♥8、♥9、♥J（4）
- 过河拆桥：♠3、♠4、♠Q、♥Q、♣3、♣4（6）
- 顺手牵羊：♠3、♠4、♠J、♦3、♦4（5）
- 决斗：♠A、♣A、♦A（3）
- 南蛮入侵：♠7、♠K、♣7（3）
- 万箭齐发：♥A（1）
- 桃园结义：♥A（1）

当前 `create_standard_military_deck()` 共 102 张：54 张现有基本牌 + 25 张现有装备牌 + 23 张新增锦囊。它是标准版 + 军争篇的可玩基础，并非完整最终军争牌表；后续继续补齐时复用同一 catalog。不存在 `GUOZHAN_DECK`。

## AI 与 UI

AI 会通过 `UseCardAction` 使用七张新增锦囊，并通过 `RespondCardAction`、`PassPendingAction`、`ConfirmPendingAction`、`SelectCardsAction`、`ChooseOptionAction` 响应杀、闪、决斗和装备请求；AI 不直接改 HP 或移动牌。

玩家点击锦囊仍走 `player_use_card` 输入 façade，随后进入同一个 V2 规则入口。新增选择请求复用现有蓝/黄框选牌系统，支持对方手牌与装备混合候选；判定状态暴露为 `judge_card`。

## 验证

- `python -m compileall -q main.py src tests`：通过。
- `python -m unittest discover -s tests`：51/51 通过（原 38 + 新增 13）。
- Pygame dummy：成功初始化并完成至少一个事件循环后正常退出。
- 组合烟雾：青龙+闪、贯石+闪、寒冰、八卦、火杀+藤甲、青釭+仁王、酒杀+白银、决斗+杀、南蛮+杀、万箭+闪均由自动测试覆盖。
- 实际链烟雾：玩家决斗、玩家万箭、AI 万箭触发玩家 Pending UI 三个场景 3/3 通过，未遗留 Pending。

## Phase 4 建议

下一阶段实现无懈可击响应链、判定区与延时锦囊（乐不思蜀、兵粮寸断、闪电），并建立正式阶段/跳过阶段系统。随后可在同一多目标框架上实现五谷丰登公共牌池。Phase 4 开始前可删除 `combat.py` 内已无调用的旧装备私有函数，但应继续保留最薄的 Pygame 输入 façade，避免把 UI 与规则重新耦合。
