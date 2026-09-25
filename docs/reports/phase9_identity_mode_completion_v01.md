# Phase 9 标准身份模式收官报告

## 1. 原开局流程结构

```
menu → begin_general_select() → general_select → confirm_general()
     → start_local_battle() → reset() → start_turn(self.player)
```

胜负内联在 `DeathFlow.advance`（最后存活者胜），没有模式概念、没有身份数据。

## 2. GameMode 架构

新增 `src/game/modes/`：

| 文件 | 内容 |
| --- | --- |
| `base.py` | `GameMode`（允许人数 / 开局钩子 / 首行动 / 死亡钩子 / 胜负 policy / 结果展示）、`DeathResolution`、`GameModeRegistry` |
| `ffa.py` | `FreeForAllMode`：只声明元数据，行为全部来自基类默认实现 |
| `identity.py` | `IdentityMode`：身份配比、主公开局规则、死亡奖惩、身份胜负 |

- 基类的默认 `resolve_death` 就是原来的「最后存活者胜」，因此 **FFA 行为零变化**。
- `DeathFlow` 现在只做通用清理，然后调用 `mode.on_death()` 与 `mode.resolve_death()`；
  流程里不再有任何胜负判断，也没有 `if game.mode == "identity"`。
- 核心 Flow（`flows/`、`engine/`、`card_effects/`）中**没有**任何身份相关代码；
  只有 UI 层通过 `mode.uses_identities` / `mode.identity_label()` /
  `mode.result_headline()` / `mode.result_lines()` 这些通用接口取展示信息。

## 3. Identity 数据与可见性

- `src/game/identity.py`：`Identity` 枚举（`lord/loyalist/rebel/renegade`）、
  中文名表、人数配比表、`visible_identity(player, viewer)`。
- `Player` 新增 `identity` / `identity_revealed`；FFA 下保持 `None`。
- 可见性：主公公开 → 自己可见自己 → 阵亡立即公开 → 对局结束全部公开。
- Renderer 只画 `visible_identity` 的结果；AI 只通过 `mode.public_identity_of()` 取身份。

## 4. 身份配比

| 人数 | 主公 | 忠臣 | 反贼 | 内奸 |
|---|---|---|---|---|
| 5 | 1 | 1 | 2 | 1 |
| 6 | 1 | 1 | 3 | 1 |
| 7 | 1 | 2 | 3 | 1 |
| 8 | 1 | 2 | 4 | 1 |

身份牌由 `game.rng` 打乱后按座次发放；真人可能是任意身份。

## 5. 武将选择

- 候选来自 `GeneralRegistry`（`game.general_pool_ids()`），候选数量由模式配置
  （`IdentityMode.general_choice_count = 3`），界面只显示候选而不是全部 11 名。
- 真人：点卡片选择 → 确认出战。
- AI：`assign_generals` 从剩余池中不重复随机分配（已有能力，沿用）。

## 6. Setup 流程

```
menu（模式 / 总人数）
  → begin_general_select()：reset → 分配身份 → scene = identity_reveal
  → confirm_identity()：抽取候选 → scene = general_select
  → confirm_general()：start_local_battle()
       reset → 写回身份 → mode.setup_battle()（主公体力）→ scene = game
       → start_turn(mode.first_player())
```

- 主公体力上限 +1 且满体力，在**武将绑定之后**应用（否则会被武将体力覆盖）。
- 首行动由 `mode.first_player()` 决定：身份局是主公，FFA 仍是真人。
- 初始手牌沿用既有 `reset()` 逻辑（每人 4 张），模式层不重复实现。

## 7. 胜负规则

| 结果 | 条件 |
| --- | --- |
| 主忠胜（`LORD_SIDE_WIN`） | 反贼与内奸全部身亡，主公存活 |
| 反贼胜（`REBEL_WIN`） | 主公身亡，且内奸不是唯一存活者 |
| 内奸胜（`RENEGADE_WIN`） | 主公身亡后内奸成为唯一存活角色 |

判定只在死亡结算后进行：`DyingFlow`（求桃）→ `DeathFlow`（清理）→ `mode.resolve_death`。
濒死被救回时不会触发任何胜负。

## 8. 死亡奖惩

- 击杀反贼：击杀者摸 3 张（`IdentityMode.REBEL_KILL_REWARD`）。
- 主公击杀忠臣：主公弃置所有手牌与装备。
- 无 killer（闪电、失去体力）或自杀：不发奖励也不发惩罚。
- killer 直接复用 `DeathFlow.source`，没有新增身份专用字段。

## 9. AI 可见身份原则

`AIController` 新增：

- `visible_identity(target)`：自己的身份，或 `mode.public_identity_of(target)`
  （只返回已公开的身份）。
- `identity_bias(target, card)`：按可见身份给目标打分；隐藏身份返回 0。
- `protects(target)`：是否主动用【桃】救（只对公开主公，且自己是忠臣 / 内奸）。

AI 代码里没有任何 `target.identity` 的直接比较；测试用「换掉真身但打分不变」
来锁定这条约束。

## 10. UI 新流程

- 开始菜单：模式按钮（自由混战 / 标准身份）、总人数按模式允许值增减、模式说明。
- 身份展示界面（新）：明确显示「你的身份：X」+ 本局主公 + 「继续」按钮。
- 选将界面：只显示本局候选。
- 桌面座位面板：显示已公开的身份（主公 / 阵亡者），隐藏身份不显示。
- 结算面板：身份局逐行列出每名玩家的身份 / 武将 / 存活情况与胜负阵营；
  FFA 保持原有展示。
- 「重新开始」改为彻底清理并回到开局流程（模式 → 人数 → 身份 → 选将）。

## 11. 修改文件

新增：`src/game/modes/{__init__,base,ffa,identity}.py`、`src/game/identity.py`、
`src/ui/identity_reveal.py`、`docs/rules/phase_9_identity_mode_rules.md`、
`tests/test_engine_v2_phase9_identity.py`、`tests/test_engine_v2_phase9_identity_ui.py`。

修改：`src/game/core.py`（模式注册 / 开局流程 / 人数 / 选将）、
`src/game/flows/death.py`（改用 mode policy）、`src/player.py`（身份字段）、
`src/game/controllers/ai.py`（可见身份与最小策略）、`src/start_menu.py`、
`src/ui/{overlay,seats,interaction}.py`、`src/renderer.py`、`main.py`、
`tools/multiplayer_smoke.py`。

## 12. 测试结果

```
python -m compileall main.py src tests tools   → PASS
python -m unittest discover -s tests -t .      → Ran 586 tests, OK
```

基线 510 → 586（新增 76：引擎 70 + UI 6）。

Smoke：
- FFA 2 / 4 / 8 人各 1 局：`stuck=None, error=None`。
- 身份模式 5 / 8 人共 4 局：全部正常结束，覆盖主忠胜与反贼胜。
- `tools/ui_smoke.py` 全项通过。

## 13. 非阻断问题

- 内奸 AI 只做了「不主动打主公」的温和策略，没有做控场 / 单挑规划；
  身份推理（猜身份、行为推断）明确留到后续 Phase。
- 主公技（激将 / 护驾 / 救援等）未实现，本阶段身份模式不依赖它们。
- 反间仍由引擎自动替目标决定「弃同花色手牌或受伤」（Phase 8 既有简化口径）。
- 身份局结算面板是纯文本行，样式较朴素；未做头像 / 卡图。
- `tools/ui_snapshots/` 下的旧快照未重新生成（开局界面布局有变）。

## 14. 开工流程修复（全屏第一帧布局）

**现象**：全屏启动时界面元素挤在中间一小块，点一下鼠标才恢复正常。

**原因**：界面组件（`StartMenu` / `GeneralSelectScreen` / `ChoiceOverlay` /
`IdentityRevealScreen` / `GameOverOverlay`）的 `draw()` 只在自身矩形还是
初始小值时重算布局；启动时的布局是按设计尺寸（1600×900）算的，而全屏
分辨率（例如 2560×1440）不同，于是第一帧用的是旧布局，直到第一次点击
触发了 `sync_layout(renderer.metrics)` 才恢复。

**修复**：

- 所有屏幕组件的 `draw()` 在检测到传入的 `LayoutMetrics` 与自身持有的不是
  同一个对象（即分辨率已变化）时立即重新布局。
- `main.py` 新增 `resync_screens(surface)`：统一把新显示表面分发给全部组件
  并重建布局，**启动时立即调用一次**，F11 切换与窗口缩放共用同一路径
  （原先 VIDEORESIZE 分支漏了选将与身份展示两个界面）。
- 选将界面在 `draw()` 里重新布局时沿用当前武将列表，从未设置过时退回全量，
  避免出现空白界面。

**回归测试**：`FirstFrameLayoutTests`（3 个）——各分辨率下菜单第一帧必须居中
且在屏内；只调用 `draw()`（不显式同步）时三个界面都要按新分辨率重算；
窗口反复缩放后武将卡仍在屏内且不与确认按钮重叠。
