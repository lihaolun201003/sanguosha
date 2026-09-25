# Phase 10.5：战场可读性 + 通用判定展示（Judge Presentation）

- 报告日期：2026-09-20
- 基线：Phase 10.4（887 tests / compileall / smoke 全通过）
- 工作区：`C:\Users\lihao\Desktop\sanguosha\sanguosha -zcode`

本阶段只做表现层：**没有改动任何判定规则语义**，`JudgeFlow` 的抽取顺序、
改判窗口、天妒取牌、延时锦囊结算逻辑一行未变。

---

## 1. Judge Presentation 架构

判定展示刻意拆成"规则层声明"与"UI 消费"两层，中间没有任何卡名字符串判断。

```
规则层  src/game/judge_presentation.py
        JudgeSourceKind   CARD / EQUIPMENT / SKILL / OTHER
        JudgeOutcomeTone  POSITIVE / NEGATIVE / NEUTRAL
        JudgeSourceSpec   来源名称 + 规则文本 + 结果语义函数
        JUDGE_SOURCES     reason → spec 注册表（lebu / bingliang / shandian /
                          bagua / ganglie / luoshen / tieji）

流程层  src/game/flows/judge.py
        JudgeResult 新增 source_spec / outcome，并暴露 .tone
        JudgeFlow(engine, owner, reason, spec=None)
        JUDGE_REPLACED 事件（改判真的发生时发出）

UI 层   src/ui/judge.py
        JudgeStage   OPEN → SOURCE_HOLD → DRAW_ANIMATION → REVEALED_HOLD
                     →（可多次 REPLACEMENT）→ FINAL_RESULT → OUTCOME_HOLD
                     → FADE_OUT → DONE
        JudgePanel   非阻塞状态机 + 绘制
```

新增一种判定（新的延时锦囊 / 装备技能 / 武将技能）只需要在 `JUDGE_SOURCES`
里加一条声明，UI 不用改一行。判定来源是卡牌时，UI 从判定区里取那张**实体牌**
画真实卡面；来源是技能时画武将牌缩略图，没有素材则退回程序绘制的通用技能卡。

## 2. Judge 生命周期

| 阶段 | 内容 | 节奏（`FXTiming`，跟随速度倍率） |
| --- | --- | --- |
| OPEN | 面板弹出 | `judge_open` 0.32s |
| SOURCE_HOLD | 亮出判定来源与规则文本 | `judge_source_hold` 0.80s |
| DRAW_ANIMATION | 判定牌从右侧滑入判定区 | `judge_draw` 0.65s |
| REVEALED_HOLD | 展示当前判定牌；**改判窗口开着就一直保持** | `judge_revealed_hold` 0.80s |
| REPLACEMENT | 换牌表现 | `judge_replacement` 0.55s |
| FINAL_RESULT | 最终判定牌锁定 | `judge_final` 0.30s |
| OUTCOME_HOLD | 结果语义（跳过出牌 / 受伤 / 反击成功） | `judge_outcome_hold` 1.55s |
| FADE_OUT | 淡出 | `judge_fade_out` 0.40s |

全部由 `dt` 驱动，没有任何 `time.sleep()`。`REVEALED_HOLD` 只有在拿到
`JUDGE_RESULT` 之后才会继续推进——否则改判过程会被跳过；同时有 20 秒兜底，
判定流程异常时面板不会永远挂着。

**不额外抽牌**：面板只读 `JudgeResult.card` / `JudgeContext.current_card`，
这两个都是引擎已经通过 `MoveCardAtom` 移动过的实体牌。测试
`test_panel_never_moves_a_card_itself` 与 `test_judge_draws_exactly_one_card`
分别锁住"面板不动牌"和"判定只抽一张"。

## 3. 改判如何表现

`JudgeFlow._handle_replacement()` 在替换真正生效后发出 `JUDGE_REPLACED`：

```python
payload = {reason, player, skill_id, old_card, new_card, history, context}
```

`JudgePanel.note_replacement()` 收到后**不重开面板**，只：
把 `shown_card` 换成新牌、把旧牌记进 `previous_card`、
把 `replacement_history` 换成引擎的完整记录、进入 `REPLACEMENT` 阶段。

面板右侧因此显示：

```
桃　红桃3
玩家 发动【鬼才】改判（共 1 次）
原判定：杀 黑桃7
```

架构上不限制替换次数：`JudgeContext.replacements` 是列表，
`JUDGE_REPLACED` 每次替换都会再发一次，`JudgePanel` 每次覆盖成最新的历史。

## 4. outcome tone 设计

颜色表达"这个结果对**被判定角色**的实际含义"，与牌的红黑、判定条件真假都无关：

| 判定 | POSITIVE | NEGATIVE | NEUTRAL |
| --- | --- | --- | --- |
| 乐不思蜀 | 红桃 → 正常出牌 | 非红桃 → 跳过出牌阶段 | — |
| 兵粮寸断 | 梅花 → 正常摸牌 | 非梅花 → 跳过摸牌阶段 | — |
| 闪电 | 未命中 → 闪电移走 | 黑桃 2～9 → 受 3 点雷电 | — |
| 八卦阵 | 红色 → 视为打出【闪】 | — | 黑色 → 仍需自己出闪 |
| 刚烈 | **非红桃 → 反击成功** | 红桃 → 反击没生效 | — |
| 洛神 | 黑色 → 获得判定牌 | — | 红色 → 本次结束 |
| 铁骑 | 红色 → 目标不能闪 | — | 非红 → 目标可正常响应 |

刚烈是刻意反色的：它是"受伤后的反击"，非红桃才成功，所以非红桃是 POSITIVE。
判定牌缺失（牌堆抽空）统一 NEUTRAL 且不崩。

颜色只有一处来源：`theme.JUDGE_TONE_COLORS` + `theme.judge_tone_color()`。

## 5. 技能栏数据来源

技能栏在 Phase 11 就已经是数据驱动的，本阶段补了覆盖与一致性：

* 技能列表 = `game.skills.skill_ids_of(player)`（武将绑定 + 装备技能 + 额外绑定）
* 名字 / 类型 / 描述 = `SkillDef.name` / `.kind` / `.description`
* ACTIVE / VIEW_AS 当前可发动时高亮可点，不可发动时变暗并给出原因
* LOCKED / TRIGGERED 显示真实技能名并占一个按钮位，只作为"看说明"的入口
* 说明文本直接引用 `SkillDef.description`，不存在第二份技能描述

## 6. Tooltip placement

`place_tooltip()` 是所有提示框（卡牌 / 技能 / 座位 / 装备）的唯一入口：

候选顺序 右 → 左 → 下 → 上 → 四个角 → **右/左的垂直居中与底对齐变体**（本阶段新增，
座位面板贴边时只按顶端对齐很容易整块撞到别的面板）。取第一个"完整在视口内
且与 avoid 零相交"的位置，全都会相交时取相交面积最小的。

`Renderer._tooltip_avoid_rects()` 现在避开：座位面板、真人状态条、手牌区、
提示条、主次按钮，以及**判定面板**——所以判定展示期间普通提示不会遮住关键事件。

## 7. 高亮优先级

`theme.STATE_PRIORITY` 按战场语义重排（Phase 10.1 的统一状态表继续是唯一来源）：

```
阵亡 > 已选目标 > View-As 来源 > 正在响应 > 合法目标(+hover) > 合法目标
     > hover > 当前回合 > View-As 候选 > 可出牌 > 非法目标 > 灰化
```

关键变化：**"引擎正在等这个人回答"（正在响应）现在高于"他可以被选"（合法目标）**，
且 hover 高于当前回合——与"selected > 当前响应 > 合法 > hover > 当前回合"一致。
座位面板与真人状态条都走同一张表，整个面板一起发光，不是只圈头像。

View-As：`game.view_as_candidate_ids()` 在未进入模式时返回空集合，
所以"没点技能就不该有转化高亮"是引擎层保证的，不靠 UI 判断。

## 8. 修改文件

```
新增  src/game/judge_presentation.py       判定语义声明表（来源 / 规则 / tone）
新增  src/ui/judge.py                      通用判定面板（状态机 + 绘制）
新增  tests/test_engine_v2_phase10_5_judge_presentation.py
新增  tools/phase10_5_smoke.py

改动  src/game/engine/events.py            EventType.JUDGE_REPLACED
改动  src/game/flows/judge.py              JudgeResult 携带 spec/outcome；改判事件
改动  src/ui/fx.py                         判定节奏集中配置；接入 JudgePanel
改动  src/ui/theme.py                      判定语义色 + 高亮优先级重排
改动  src/ui/widgets.py                    place_tooltip 候选扩充
改动  src/ui/layout.py                     牌堆瘦身、公共牌区上移
改动  src/renderer.py                      判定面板最高层级 + 加入 tooltip 避让
删除  src/ui/table.py:draw_judge_banner    被统一判定面板取代
```

## 9. 测试结果

| 项目 | 结果 |
| --- | --- |
| 开工基线 | 887 |
| 新增 | 47（`test_engine_v2_phase10_5_judge_presentation.py`） |
| 全量 | **934 全部通过** |
| compileall | 通过（main.py / src / tests / tools） |
| smoke | `python -m tools.phase10_5_smoke` → **27/27 通过** |

smoke 覆盖：判定面板生命周期（含阶段顺序与两种 tone）、鬼才改判全链路、
技能栏（赵云 / 周瑜 / 孙尚香）、Tooltip placement、5 档分辨率、4 人与 8 人
自由混战各一局、5 人身份模式构建渲染。截图见
`tools/ui_snapshots/phase10_5_*.png`。

开发过程中 smoke 抓到一个真实 bug：`JudgeFlow._build_result()` 把 **Card 对象**
传给 outcome 函数，而函数读的是 `JudgeResult.color`（Card 没有该属性），
导致八卦阵的红判定被判成"未生效"。已改为读 `card.card_color`，
测试替身也收紧成只暴露真实 Card 的字段，避免同类问题再被掩盖。

## 10. 剩余非阻断问题

1. 判定面板里放不下第二张卡（面板高 316 设计坐标，判定牌已占 180），
   所以"原判定牌"用一行文字（`原判定：杀 黑桃7`）表达而不是缩略卡面。
2. 多人局桌面拥挤时，装备 Tooltip 无法保证与**所有**面板零重叠
   （7 人局中部几乎没有 320×260 的空白），目前的保证是：不越屏、
   不压提示条与手牌区、不盖住鼠标指向的那一点。
3. `JudgePanel` 同时只展示一次判定（引擎的判定本身是串行的）；
   如果将来出现并行判定，需要改成队列。
4. 判定展示期间面板画在最上层但不吃指针事件——真人改判仍要点手牌，
   所以没有拦截点击；面板覆盖区域内的座位/手牌依然可点。
