# Phase 8 判定改判审计（JudgeFlow / Judge Replacement）

审计对象：`src/game/flows/judge.py` 及其调用点，时间点为本阶段开工前。

## 一、七个关键问题的答案

| # | 问题 | 现状 |
| --- | --- | --- |
| 1 | 判定牌从哪里抽 | `JudgeFlow._draw` → `self.game.deck.draw()`（牌堆顶） |
| 2 | 判定过程中是否有临时 zone | 有：`game.processing_zone`（处理区）。翻开后立即入区，替换与最终结算都按真实区域移动 |
| 3 | 何时计算判定结果 | 改判窗口关闭后：`_finalize` 中 `JUDGE_BEFORE_RESULT` → `JUDGE_RESULT`，结果对象为 `JudgeResult` |
| 4 | 何时把判定牌放进弃牌堆 | `_finalize` 尾部：先发 `JUDGE_FINISHED`（技能可在此取走），仍在 `processing_zone` 的才移入 `deck.discard_pile` |
| 5 | 技能拿到的是什么 | 实体 `Card` 对象本体（`JudgeContext.current_card` / `JudgeResult.card`），从不复制、不重造 |
| 6 | 原有判定是否共用同一流程 | 是：刚烈、八卦阵、闪电、乐不思蜀、兵粮寸断全部走 `JudgeFlow`，reason 分别为 `ganglie` / `bagua` / `shandian` / `lebu` / `bingliang` |
| 7 | 是否已有通用判定事件 | 有：`JUDGE_STARTED` / `JUDGE_REVEALED` / `JUDGE_BEFORE_RESULT` / `JUDGE_RESULT` / `JUDGE_FINISHED` |

## 二、审计结论

已有的 `JudgeContext` + 改判窗口是本阶段的基础设施，但存在三个必须修补的缺口：

1. **八卦阵没有接入改判窗口**。`EquipmentSkillController.resume_sha` 直接取
   `JudgeFlow(...).start().value`；判定一旦因改判进入 `WAITING`，`.value` 是 `None`，
   八卦阵会被判为失败，且改判结果被丢弃。
2. **真人无法放弃改判**。`pending_selection` 状态下固定按钮的次级键被禁用
   （`("", False, "noop")`），玩家打开选牌后只能选牌，没有 Pass 的 UI 入口。
3. **缺少引擎侧二次校验**。`JudgeFlow._handle_replacement` 直接信任 UI/AI 传来的选牌结果，
   没有复核"这张牌是否仍是该角色的实体手牌"。

## 三、判定牌生命周期（本阶段目标形态）

```
deck.draw()                      → 判定牌进入 processing_zone
JUDGE_REVEALED                   → 展示当前判定牌
改判窗口（按座次依次询问）        → 每次 Replace：旧牌 processing_zone → 弃牌堆，
                                    新牌 手牌 → processing_zone
JUDGE_BEFORE_RESULT / JUDGE_RESULT → 用 current_card 计算结果
JUDGE_FINISHED                    → 天妒一类技能在此取牌
仍在 processing_zone 的最终判定牌 → 弃牌堆（只一次）
```

任何时刻一张实体牌都只属于一个区域；替换不复制 `Card`，只在区域间移动同一对象。
