# Phase 8 Hotfix 2：Generic Card Action Discovery & Conversion Pipeline

报告日期：2026-09-19
问题：实体牌的"正常用途"与技能赋予的"转化用途"没有被统一发现，UI 因此
把可转换牌灰掉、响应系统按牌名过滤、濒死救援绕开技能、AI 各自扫牌名。

范围：只建立基础设施并修通已存在的技能（武圣 / 龙胆），不新增正式武将、
不新增卡牌、不动美术、不改身份模式、不重构 AI。

---

## 1. 原根因

审计结论（读真实代码，不猜）：

| # | 位置 | 断点 |
|---|---|---|
| 1 | `src/ui/player.py:playable_hand_indices` | 灰化只查实体牌的 `CardEffect`，完全不看 `ConversionRegistry` |
| 2 | `src/response.py:ResponseSystem.can_play` | 只比对 `card.name in allowed_cards`，杀→闪无法响应 |
| 3 | `src/game/flows/dying.py` | 用 `{card.name for card in hand …}` 生成 allowed，且 `if allowed: break`；手里没有真实桃就直接跳过该救援者 |
| 4 | `src/game/basic_cards.py:player_use_card` | 一次点击 = 一串 `if card.name == …` 分支，没有"动作"概念，无法表达一张牌有两个用途 |
| 5 | `src/game/conversion.py:ConversionRegistry.candidates_for` | 硬编码 `conversion.source_count != 1: continue`，架构上不支持两张牌 |
| 6 | `CardConversion` | 没有声明 source 区域，查询默认只扫 `actor.hand` |
| 7 | `src/game/controllers/ai.py` | 只在"没有真实牌"时用 `candidates_for` 兜底；出牌靠 `_pick_card(name)` 遍历牌名 |
| 8 | 全局 | 没有统一的 `CardActionContext`：场合判定散落在 UI 的 `if game.phase == …` / `if card.name == …` 里 |

## 2. 原 UI 为什么会把可转换牌灰掉

```text
playable_hand_indices：
    effect = card_effects.get(card)          # 只看实体牌的规则
    if effect is None: continue              # 【闪】没有可主动使用的规则
    if _effect_can_be_used(...): playable.add(index)
```

于是赵云的【闪】（本身不能主动使用）被灰，尽管【龙胆】能把它当【杀】用。
新的判定只问一句话：

```python
actions = game.card_actions.actions_for_card(actor, card, context)
is_operable = 存在 enabled 的动作，或能作为多 source 转换的候选
```

## 3. CardActionContext

`src/game/card_actions/context.py`

```python
CardActionContext(actor, context, requirement=None, allowed_names=(), pending_request=None, phase="")
    context ∈ { "play", "response", "rescue" }   # 复用 conversion.py 的常量，不出现第二套字符串
    allows(name)                                 # PLAY 不限结果牌名；RESPONSE / RESCUE 用 allowed_names
```

三种场合由 `CardActionDiscovery` 构造：`play_context` / `response_context` /
`rescue_context`，UI、AI、响应系统、救援流程都只传这个对象，不再自己拼条件。

## 4. CardActionOption

`src/game/card_actions/option.py`（数据驱动，UI 只消费它）

```text
action_id / kind(normal|conversion) / context / owner
source_cards / result_name / result_category / result_nature
skill_id / skill_name / enabled / disabled_reason
min_sources / max_sources / requires_targets
label / detail / log_text / kind_context
```

稳定 ID（不使用对象地址，也不用 name+suit+rank）：

```text
normal:<card_uid>:<context>
convert:<skill_id>:<uid1+uid2>:<result_name>:<context>
```

## 5. Action Discovery

`src/game/card_actions/discovery.py`，通过 `game.card_actions` 暴露：

```python
play_context / response_context / rescue_context
actions_for_card(actor, card, context)      # 单卡：normal + conversion
actions_for_sources(actor, cards, context)  # 已选 source 集合的完整动作
actions_in(actor, context)                  # 整个可操作区域
usable_options(actor, context)              # enabled 且满足 requirement
is_operable(actor, card, context)           # 灰化专用（轻量）
effective_card(option)                      # 结果牌（实体牌或 VirtualCard）
legal_targets(actor, option)
validate(option, sources, targets, context) # 提交前二次校验
```

六个可发现场合由 `kind_context` 表达：
`normal_play / normal_response / normal_rescue / conversion_play / conversion_response / conversion_rescue`。

## 6. source candidate 与完整 action

```python
option.complete            # 已选 source 是否够组成这个动作
option.needs_more_sources  # 还差几张
```

一张实体牌即使**单独**不足以组成动作（例如未来的丈八类"两张手牌→杀"），
也会作为 `source_candidate` 返回 `needs_more_sources=True` 的候选，
因此它不会被灰掉，点击后进入 source 收集状态。

## 7. multi-source

`CardConversion` 现在声明区间与区域：

```python
CardConversion(
    skill_id=..., matches=..., name="SHA",
    min_sources=2, max_sources=2,
    source_zones=(HAND_ZONE, EQUIPMENT_ZONE),
    contexts=(PLAY_CONTEXT,),
    keep_with_normal=False,
)
```

- `source_count` 保留为兼容写法（等价于 min = max）。
- 同一张实体牌不能重复充当两个 source（按 `is` 身份判重）。
- 收集流程：点第一张 → Picker / 收集态 → 点第二张 → 收齐后自动进入目标选择。
- 统一入口：`Game.begin_card_action` / `choose_card_action` /
  `toggle_card_action_source` / `confirm_card_action` / `cancel_card_action`。

## 8. VirtualCard

Phase 8 已有的 `VirtualCard` 继续统一使用，未新增第二套表示：

```text
name（结果牌名） / category / subtype / nature（火杀、雷杀可表达）
source_cards（实体牌）
skill_id / owner
suit / rank / card_color（继承主 source）
```

结果牌的规则完全由结果牌决定：`card_effects.get(virtual)` 按 name 找到
【杀】的 `CardEffect`，因此 TargetRule / Attack Range / Slash Quota /
DamageFlow / 武器防具交互全部沿用【杀】的规则。禁止改写实体牌。

## 9. Card provenance

`UseCardAction.metadata` 记录完整来源：

```python
metadata["card_action"] = option              # 本次动作
metadata["conversion"] = {
    "skill_id", "skill_name", "source_cards", "result_name", "log"
}
```

- 目标选择阶段带着它，Prompt 能显示来源技能。
- 战报记录完整句子：
  `赵云发动【龙胆】，将 ♦ 7【闪】当【杀】使用`
  `关羽发动【武圣】，将 ♥ 3【桃】当【杀】使用`
- 曹操【奸雄】一类"取得造成伤害的牌"读取的仍是 `source_cards` 里的实体牌。

## 10. Card Movement：实体牌只移动一次

新增 `Game.move_source_card_to_processing(player, card)`：

```text
手牌里的 source      → MoveCardAtom(手牌 → 处理区)
装备区里的 source    → remove_equipment_with_effects(触发 EQUIPMENT_LOST / 枭姬)
                      → 处理区
```

`UseCardFlow` 与 `RespondCardAction` 都改为调用它，因此：

- 不再把 source 硬编码成"从手牌移出"；
- 处理区 → 弃牌堆的搬运仍在 flow 的 finish 阶段，且带
  "已被技能取走就不再移动"的身份检查（奸雄 / 天妒）；
- 虚拟牌本身从不进入任何区域，只有实体 source 移动，**一次**。

## 11. Play Context

```text
点击实体牌 → Card Action Discovery → 0 / 1 / N 个动作
    0 → 显示 disabled_reason，不做任何事
    1 → 直接进入标准目标选择
    N → CardActionPicker
→ 构造有效牌（实体牌或 VirtualCard）
→ 标准 Target Selection → Confirm
→ 提交前二次校验（source 仍在、技能仍在、谓词成立、结果牌可用、目标合法）
→ UseCardAction → UseCardFlow → CardEffect
```

## 12. Response Context

- 真人 RESPOND_CARD：`present_or_auto_resolve` 用 Discovery 判定
  "有没有任何合法响应（真实牌或转化）"；无懈链完全没有合法动作时自动放弃。
- 点击手牌 → `begin_card_action` → 结果牌在 `allowed_cards` 内才算动作；
  真实【闪】与【龙胆】杀当闪同时列出，各自唯一时直接响应。
- 提交的牌：Engine Pending 路径提交 `VirtualCard`（`_respond_card` 按其
  `source_cards` 校验与移动）；legacy 响应路径（1v1 濒死等）按手牌下标移除实体牌。
- AI 的响应候选来自同一个 `usable_options`。

## 13. Rescue Context

`DyingFlow` 的 allowed 不再扫描真实牌名：

```python
names = ("TAO", "JIU") if rescuer is dying_player else ("TAO",)
context = game.card_actions.rescue_context(rescuer, dying_player=…, allowed_names=names)
allowed = {option.result_name for option in game.card_actions.usable_options(rescuer, context)}
if allowed: break
```

于是"红牌→桃"这类救援转化（Probe B）能真正救到人；没有任何合法救援动作时
该角色被跳过，流程与既有规则一致。

## 14. CardActionPicker

`src/ui/action_picker.py`：与 SkillPicker 语义不同但复用同一套 Widgets 与视觉。

- 标题「选择操作」+ 副标题显示实体牌（如 `♦ 3【桃】`）。
- 每行 `option.label` + `option.detail`：`使用【桃】` / `【武圣】将此牌当【杀】使用`。
- 蓝色边框 = 技能转化，金色边框 = 正常使用，禁用行灰化并显示原因。
- 模态：打开期间吞掉其它点击，`action_cancel` 与固定按钮的「取消选择」同名。
- 布局全部来自 `LayoutMetrics`，F11 / Resize 后重建（绘制 Rect == 命中 Rect）。

## 15. UI enabled / disabled

```text
只要存在至少一个可达成动作（正常使用 或 技能转化）
    → 这张实体牌保持可操作（不灰）
所有动作都不合法
    → 灰化，并在悬浮提示里给出原因
```

原因文案由引擎给出（不在 UI 里重算）：
`你的体力已经是满的。` / `你已经使用【酒】，现在必须使用【杀】。` /
`本回合已经使用过【杀】。` / `攻击距离不足。` / `这张牌不能主动使用。`

装备区是否可点也由 Discovery 决定：
`card_actions.source_zones_in_use(actor)` 里包含 `equipment` 时，装备槽才会
响应点击并画蓝框（当前正式武将只声明手牌）。

## 16. AI 接入

```python
response_options(request)      # 统一查询：真实牌 + 转化
converted_response(request)    # 没有真实牌时取最便宜的转化
converted_play_card(name)      # 出牌阶段取最便宜的转化
```

- 决策层只负责在合法 Action 之间评分：有真实牌优先真实牌，其次按牌价值取转化。
- 不再遍历 `card.name` 猜可用性，也没有任何 `if general_id == …`。
- 自动化检查（测试）扫描 `renderer.py / response.py / src/ui / controllers /
  card_effects / engine`，禁止出现具体武将或技能的条件分支。

## 17. 武圣验证（关羽）

按 `docs/rules/phase_8_general_rules_reference.md` 的标准版口径
"将一张红色牌当【杀】使用或打出"：

| 场景 | 结果 |
|---|---|
| 红色【桃】 | 可转（PLAY） |
| 红色【闪】 | 可转（PLAY） |
| 红色锦囊（无中生有） | 可转（PLAY） |
| 红色装备牌（手牌） | 可转（PLAY） |
| 黑色牌 | 不可转 |
| 响应需要【杀】时 | 可转（RESPONSE） |
| 满血【桃】 | 正常使用不可用，但可操作（武圣），点击后弹 Picker |

`source_zones` 保持手牌：规则参考文档未扩大到手牌以外，未凭感觉改技能版本。
装备区通路由 Probe D 验证，未来武将只需在自己的声明里加 `EQUIPMENT_ZONE`。

## 18. 龙胆验证（赵云）

| 场景 | 结果 |
|---|---|
| 【闪】→【杀】（PLAY） | 通过，直接进入目标选择 |
| 【杀】→【闪】（RESPONSE） | 通过，虚拟牌按【闪】需求提交 |
| 【闪】在出牌阶段是否被灰 | 不灰（核心验收） |
| 转换后的【杀】是否计入杀次数 | 是（`sha_used` 置位，二次发动被拒） |
| 距离不足 | 转换动作 disabled（`攻击距离不足。`） |
| 实体牌移动 | 只移动一次，最终在弃牌堆 |
| 花色 / 点数 | 继承自实体 source |

## 19. test-only probes

`src/game/skills/conversion_probes.py`（不注册到正式游戏，测试显式绑定）：

| Probe | 规则 | 验证点 |
|---|---|---|
| 墨守 `probe_black_wuxie` | 黑色牌 →【无懈可击】，仅 RESPONSE | 非杀闪类响应走同一系统 |
| 舍身 `probe_red_tao` | 红色牌 →【桃】，仅 RESCUE | 濒死救援转化（DyingFlow 能找到救援者） |
| 双刃 `probe_pair_sha` | 任意两张手牌 →【杀】，PLAY | 多 source 的完整链路 |
| 卸甲 `probe_gear_sha` | 装备区一张牌 →【杀】，PLAY | 装备区 source（含移动与失去装备规则） |

## 20. 性能策略

- 灰化（每帧）：`is_operable` 只做单卡谓词 + 结果牌可用性探测，
  **不枚举组合、不枚举所有目标组合**。
- 多 source：先返回"候选"，玩家选齐后再收窄（30 张手牌不会产生 435 个二元组合）。
- 点击 / AI 决策才做完整 `actions_for_card` / `actions_for_sources`。
- 未引入缓存框架：当前查询量级与既有 `_effect_can_be_used` 相当，
  "先保证正确"（第 72 条允许）。

## 21. 修改文件

新增：

```text
src/game/card_actions/__init__.py       统一导出
src/game/card_actions/context.py        CardActionContext / ActionKind / 稳定 ID
src/game/card_actions/option.py         CardActionOption
src/game/card_actions/discovery.py      CardActionDiscovery
src/game/card_action_session.py         Game 侧的 Action 会话（收集 / 取消 / 提交）
src/game/skills/conversion_probes.py    test-only 探针 A/B/C/D
src/ui/action_picker.py                 CardActionPicker
tests/test_engine_v2_phase8_hotfix2_conversion.py
tests/test_engine_v2_phase8_hotfix2_ui.py
```

修改：

```text
src/game/conversion.py            CardConversion：多 source / source_zones / nature / keep_with_normal
src/game/core.py                  game.card_actions、source_container、move_source_card_to_processing、状态清理
src/game/basic_cards.py           出牌入口走 Discovery；酒锁定改为通用规则；转换日志 + SKILL_TRIGGERED
src/game/combat.py                respond_with_card 走同一条 Action 链路
src/response.py                   ResponseSystem.play_action（已由 Discovery 判定的动作）
src/game/engine/runtime.py        响应提交按区域校验；无懈链自动放弃改用 Discovery
src/game/flows/dying.py           救援 allowed 由 Discovery 计算
src/game/flows/use_card.py        source 移动统一走 move_source_card_to_processing
src/game/controllers/ai.py        响应 / 出牌候选统一来自 Discovery
src/ui/player.py                  灰化改用 Discovery；装备槽 source 高亮
src/ui/interaction.py             Card Action 路由（picker / 收集 / 装备 source）
src/ui/prompt.py                  多动作提示、转换来源、进度
src/renderer.py                   Picker 绘制与命中、按钮状态、装备 source 高亮
tools/ui_smoke.py                 四个真实鼠标点击场景
tools/ui_audit.py                 CardActionPicker 几何检查
```

## 22. 新增测试

```text
tests/test_engine_v2_phase8_hotfix2_conversion.py   55 项
tests/test_engine_v2_phase8_hotfix2_ui.py           24 项
合计                                                79 项
```

覆盖分组：

| 分组 | 覆盖点 |
|---|---|
| Context | 三场合构造、allows、稳定 action_id、label/detail |
| 武圣 | 红桃/红闪/红锦囊/红装备可转、黑牌不可转、响应可转、满血桃仍可操作 |
| 龙胆 | 闪→杀、杀→闪、灰化、杀次数、距离、结果牌身份与继承 |
| 移动 / provenance | 实体牌只移动一次、VirtualCard.source_cards、metadata、日志、SKILL_TRIGGERED 时机、多 source 双移动 |
| Normal + Conversion | 同时列出、单选直达、同结果去重、`keep_with_normal` 保留、0 动作 → disabled |
| Response / Rescue | Probe A/B 场合限制、DyingFlow 找到转化救援者、无救援动作时跳过 |
| Ownership / 校验 | 离手失效、他人牌、区域判定、重复 source、杀次数、确认前状态变化 |
| 装备 source | 声明后可发现、移动正确、未声明时拒绝 |
| 查询 | usable_options 过滤、actions_in 覆盖全手牌、legal_targets、默认区域、probe 未注册 |
| AI | 响应转化、出牌转化、无武将硬编码 |
| 属性 / 扫描 | 火杀可表达、核心层无具体武将分支、技能模块自有规则 |
| UI（真实点击） | 灰化、闪直达、Picker 两行、取消清洁、F11/Resize、双 source 两次点击、确认双移动、source 高亮、响应杀当闪、重置清理、装备槽点击 |

UI 测试全部使用 `pygame.event.post` 投递真实 `MOUSEBUTTONDOWN`，
再交给 `src.ui.interaction.handle_game_click`（与 `main.py` 同一份路由）。

## 23. 回归结果

```text
python -m compileall -q main.py src tests tools   → 通过
python -m unittest discover -s tests              → Ran 418 tests, OK
    （Phase 8 Hotfix 1 基线 339 + 本次 79）
tools/ui_smoke.py                                 → 27 步全部通过（含 4 个转换场景）
tools/ui_audit.py                                 → 105 组合，0 处问题
长局压测：无武将 14 局 + 带武将（含赵云 / 关羽）14 局 = 28 局
    0 exception / 0 hang / 0 stuck pending / 0 duplicated source movement
```

Active Skill（反间 / 结姻）仍走 `[发动技能]` → Skill Picker → `ActivateSkillAction`，
未因本次重构回归（既有 29 项 hotfix 1 测试全部保持通过）。

## 24. 已知限制

1. 正式武将当前只声明手牌作为 source 区域；装备区通路已实现并由 Probe D
   覆盖，但未擅自扩大到武圣（规则文档未要求），需要时改一行声明即可。
2. 多 source 目前只在 PLAY 场合有完整 UI 流程；RESPONSE / RESCUE 的多 source
   由转换自己声明（Probe C 只声明 PLAY），响应阶段没有"再选第二张"的交互。
3. 没有引入 Action Discovery 缓存：查询量级已足够小，按第 72 条要求先保证正确。
4. legacy 1v1 响应路径（`ResponseSystem`）提交的是实体牌 + 手牌下标，
   转型的虚拟牌只用于判定；这是为了不动 1v1 既有的卡牌移动语义。
5. 属性杀（火杀 / 雷杀）已能通过 `nature` 表达，但当前没有武将产出属性杀，
   仅有测试覆盖。
