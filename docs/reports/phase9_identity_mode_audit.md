# Phase 9 标准身份模式 · 开工审计

审计时间点：Phase 9 开工时（Phase 8 第一批武将收官之后）。

## 一、原开局流程

```
menu（StartMenu：AI 数量 1～7 + 开始游戏）
  → game.begin_general_select()          # 内部先 reset()
  → scene = "general_select"
  → GeneralSelectScreen（11 张武将卡 / 确认 / 随机 / 返回）
  → game.confirm_general()
  → game.start_local_battle(ai_count)    # 内部再次 reset() → scene = "game"
  → game.start_turn(self.player)         # 固定真人先行动
```

`Game.reset()` 负责：清空动作队列 / 响应 / 弹窗 / 引擎 Pending、卸载技能、
重建 players 与 SeatManager、清空所有 UI 选择状态、重置牌堆、每人发 4 张牌、
`assign_generals(human_general=...)`。

## 二、当前胜负判定

写在 `src/game/flows/death.py` 的 `DeathFlow.advance` 里，内联判断：

```
alive = [存活玩家]; winner = alive[0] if len(alive) == 1 else None
game.game_over = already_over or human_eliminated or no_survivors or winner
→ LAST_SURVIVOR / HUMAN_ELIMINATED / NO_SURVIVOR / ELIMINATED
```

即「最后存活者获胜」，没有任何模式概念。

## 三、缺口清单

| # | 缺口 | 影响 |
|---|---|---|
| 1 | 没有 GameMode 概念 | 无法在不改核心 Flow 的前提下新增玩法 |
| 2 | 胜负内联在 DeathFlow | 身份胜负无处安放，会出现 `if mode == "identity"` 特判 |
| 3 | 无身份数据与可见性 | 隐藏身份、死亡揭示、AI 不作弊都无从实现 |
| 4 | 选将显示全部武将 | 没有「候选池」概念，真人一次看到 11 张卡 |
| 5 | 无开局状态机 | 身份展示、模式选择没有落点 |
| 6 | 死亡无 killer 语义 | 反贼击杀奖励 / 主公误杀惩罚缺前提 |

## 四、可复用的既有能力

- `DeathFlow.source` 已经是伤害来源（伤害流程传入），可直接作为 killer。
- `game.rng` 已是统一随机源，身份打乱与选将都用它。
- `GameResult` / `GameOutcome` 结构可继续使用，只是由 mode 产出。
- `SeatManager`、武将 / 技能注册表、Pending 体系都不需要改动。
- `Renderer` 的座位面板已有 `general` 展示位，加一个身份标签即可。

## 五、本阶段的结构性决定

1. 新增 `src/game/modes/`：`base.py`（GameMode + DeathResolution + 注册表）、
   `ffa.py`、`identity.py`；`src/game/identity.py` 放身份枚举与可见性查询。
2. `DeathFlow` 只调用 `mode.on_death()` 与 `mode.resolve_death()`，
   自己不再判断胜负；FFA 的「最后存活者」搬进 `GameMode` 默认实现。
3. 开局流程扩成 `menu → identity_reveal → general_select → game`，
   身份在选将之前分配并展示。
4. 身份规则数据（配比、主公体力、奖励张数）全部放在 mode 里，UI 只读不写。
