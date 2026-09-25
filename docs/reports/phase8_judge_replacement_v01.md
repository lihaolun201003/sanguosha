# Phase 8 · 通用 Judge Replacement / 改判系统（鬼才 + 天妒）

## 1. 原 JudgeFlow 是什么结构

`src/game/flows/judge.py` 是一个可恢复（resumable）流程：

```
deck.draw() → processing_zone → JUDGE_STARTED / JUDGE_REVEALED
  → JudgeContext(judged_player, reason, original_card, current_card)
  → 改判窗口：judge_replacers() 按座次依次询问
  → _finalize：锁定 current_card → JUDGE_BEFORE_RESULT / JUDGE_RESULT
  → JUDGE_FINISHED（技能可取走判定牌）
  → 仍在 processing_zone 的牌移入弃牌堆
```

- 判定牌始终是**实体 `Card` 对象本体**，全过程放在 `game.processing_zone`（处理区）中，
  没有第二个平行区域系统。
- 改判顺序来自 `SeatManager`：`game.skills.judge_replacers(judged)` 从判定者本人开始按座次
  枚举，只依赖座次与技能注册顺序，不依赖字典遍历顺序。
- 调用方通过 `on_complete` 接回自己的结算：刚烈、延时锦囊（闪电 / 乐不思蜀 / 兵粮寸断）
  都已按这个约定挂接。

开工前审计发现三处缺口（详见 `phase8_judge_replacement_audit.md`）：

1. **八卦阵未接入改判窗口**：直接取 `JudgeFlow(...).start().value`，判定一进 `WAITING`
   就会拿到 `None`，改判结果被丢弃，八卦阵被判失败。
2. **真人无法放弃改判**：`pending_selection` 状态下固定按钮的次级键被禁用，没有 Pass 入口。
3. **缺少引擎侧二次校验**：替换牌是否仍是该角色的实体手牌没有复核。

## 2. 新增的通用扩展点

| 扩展点 | 位置 | 说明 |
| --- | --- | --- |
| `SkillDef.judge_replacement` + `JudgeReplacement` | `src/game/skills/definitions.py` | 技能声明"我能改判"与候选牌来源，核心流程不认识任何武将 ID |
| `SkillManager.judge_replacers()` | `src/game/skills/registry.py` | 按座次给出本次判定的合法改判者列表 |
| `JudgeContext` | `src/game/flows/judge.py` | 判定期间的活状态：`judged_player / reason / original_card / current_card / replacements / locked` |
| `_replacement_is_legal()` | `src/game/flows/judge.py` | 引擎侧二次校验，见第 4 节 |
| 选牌整单跳过 | `src/game/card_selection.py` + `src/renderer.py` + `src/ui/interaction.py` | 「允许选 0 张」的请求才提供通用「跳过」按钮，语义等同引擎 Pass |
| `_resume_bagua()` | `src/game/equipment_skills/system.py` | 八卦阵挂起并等改判窗口结束的通用写法（与 TurnFlow 的 `on_complete` 约定一致） |

`JudgeFlow` 里没有任何形如 `if general_id == "simayi"` 的分支；排查确认
`flows/`、`engine/`、`ui/`、`renderer.py` 中没有司马懿 / 郭嘉 / 鬼才 / 天妒的特判。

## 3. 判定牌生命周期

```
A = deck.draw()          A: 牌堆 → processing_zone
司马懿用 B 改判           A: processing_zone → 弃牌堆
                         B: 其手牌 → processing_zone
第二个人用 C 改判         B: processing_zone → 弃牌堆
                         C: 其手牌 → processing_zone
改判窗口关闭              用 C 计算 JudgeResult
JUDGE_FINISHED           天妒可取走 C（此时 C 仍在 processing_zone）
收尾                      C 若仍在 processing_zone → 弃牌堆，且只移动一次
```

- `replacement_history` 记录 `[(玩家, 技能, 旧牌, 新牌), ...]`，即 `A → B → C`，
  全部是同一批实体对象引用，不复制 `Card`。
- 任何瞬间一张牌只属于一个区域：`hand / equipment / judgement_zone / processing_zone /
  discard_pile / draw_pile / table / public_pool`，新增测试逐张断言。

## 4. 鬼才如何接入

1. `SkillDef(id="guicai", judge_replacement=JudgeReplacement(candidates=guicai_candidates))`，
   候选 = `list(player.hand)`，不会生成虚拟牌。
2. 判定翻开后，`JudgeFlow` 逐个询问 `judge_replacers()` 给出的角色；每人一次机会。
3. 真人：走 `PendingRequestType.SELECT_CARDS`（`min_cards=0, max_cards=1`）→
   `PromptPanel` + 手牌点选；不发动就点固定按钮的「跳过」。
4. AI：`AIController._judge_replacement_choice` 只在"自己判定且当前结果不利"时换牌，
   否则 Pass；不会卡流程。
5. **Engine 校验**（`_replacement_is_legal`）：窗口仍开放、请求与技能匹配、角色存活、
   牌确实在该角色手牌中、且不是当前判定牌。任一条不满足就按 Pass 处理并写日志，
   不会凭空移动牌。

## 5. 天妒如何取得 final judge card

`Tiandu` 绑定 `JUDGE_FINISHED`，在事件里读取 `JudgeResult.card`（= 改判后的最终牌）：

```python
if self.owner.alive and any(item is card for item in context.state.processing_zone):
    MoveCardAtom(card, source=processing_zone, destination=owner.hand)
```

`JudgeFlow._finalize` 的顺序是"先发事件、后默认弃置"，并且以**处理区里实际还有没有这张牌**
决定是否弃置，没有任何 `if skill == "tiandu"` 之类的判断。判定牌被取走后不会再进弃牌堆；
这套机制同样适用于以后其他"取得判定牌"类技能。

## 6. 主要改动文件

| 文件 | 改动 |
| --- | --- |
| `src/game/flows/judge.py` | 新增 `_replacement_is_legal` 引擎侧校验，非法替换按 Pass 处理 |
| `src/game/equipment_skills/system.py` | 八卦阵接入改判窗口；新增 `_resume_bagua` |
| `src/game/card_selection.py` | 新增 `can_cancel_pending_selection` / `cancel_pending_selection` |
| `src/renderer.py` | 允许选 0 张的选牌请求提供「跳过」按钮（`pass_selection`） |
| `src/ui/interaction.py` | `pass_selection` 动作路由 |
| `tests/test_engine_v2_phase8_judge_replacement.py` | 新增 21 个确定性引擎测试 |
| `tests/test_engine_v2_phase8_judge_replacement_ui.py` | 新增 4 个真人 UI 链路测试 |
| `docs/reports/phase8_judge_replacement_audit.md` | 判定流程审计 |

本阶段没有新增武将，没有改动张辽 / 曹操 / 周瑜 / 孙尚香等既有技能，
没有触碰 View-As / Conversion 入口与 AI 策略框架。

## 7. 新增关键测试

**引擎层（21 个）**

| 类别 | 覆盖 |
| --- | --- |
| 基础判定 | 无改判者时同步完成、结果与区域收尾 |
| 单个改判者 | Pass（引擎入口 + UI「跳过」）、点手牌替换 |
| 实体牌移动 | 原判定牌 / 替换牌 / 最终牌各只在一个区域，弃牌堆不重复 |
| 连续改判 | AI 判定者 → 真人 → 最终牌为最后替换者，history 长度 2 |
| 引擎校验 | 选中的牌已离开手牌时拒绝改判并保持原判定牌 |
| 刚烈 | 红桃→黑桃改判后反伤；黑桃→红桃改判后不反伤；不改判按原牌 |
| 闪电 | 改成黑桃 2~9 命中（3 点雷电）；改成红桃不命中且闪电转移到下家 |
| 乐不思蜀 | 最终牌非红桃跳过出牌阶段；改成红桃不跳过 |
| 八卦阵 | 鬼才改成红色 → 视为出闪；最终牌仍为黑色 → 判定失败 |
| 天妒 | 无改判取得判定牌；A→B 后取得 B，且 B 不进弃牌堆 |
| 区域唯一性 | 改判后全部相关牌 `assert_single_zone`；跳过改判后处理区为空 |

**UI 层（4 个）**：固定按钮出现「跳过」并映射到 `pass_selection`；
点击手牌完成改判；非 0 张请求不提供跳过；强制选牌请求无法被取消。

## 8. 最终测试结果

```
python -m compileall main.py src tests   → PASS（exit 0）
python -m unittest discover -s tests -t . → Ran 462 tests, OK
```

（基线 437 → 本次 +25，其中引擎 21、UI 4，全部确定性，不依赖随机 seed。）

## 9. 非阻断问题

- `tools/ui_smoke.py` 旧流程里"Pending 响应按钮 = 不出"的偶发时序问题依旧存在，
  与本阶段无关，未做修改。
- AI 改判策略保持最小实现（自己判定且不利才换），没有做多改判者的博弈；
  基础能力（顺序询问、每人一次、可以 Pass）已完备。
- 判定窗口目前对每个合法改判者只询问一次（标准做法）；若以后要支持
  "同一角色在窗口内多次改判"的技能，需要在 `JudgeFlow` 的 replacer 游标上另开扩展点。
