# Phase 8 第一批武将收官报告

## 1. 开工时各技能真实状态

11 名武将 / 16 个技能在开工前**全部已注册**，但有两个技能存在结构性问题，
其余通过回归确认可用（详见 `phase8_first_roster_audit.md`）：

| 技能 | 开工状态 |
| --- | --- |
| 咆哮 / 集智 / 刚烈 / 鬼才 / 天妒 / 武圣 / 龙胆 / 英姿 / 反间 / 结姻 | 已实现，本次只做回归 |
| 奸雄 | 已实现，补火杀 / 虚拟牌 / 不能凭空取得 的覆盖 |
| **突袭** | 实现方式无法被真人发动（摸牌阶段没有交互窗口），且不能选目标 |
| **枭姬** | 订阅的事件只在一条装备离场路径上发出，另外 6 条路径漏触发 |

顺带修掉两个真实 Bug（见第 8 节）。

## 2. 新增的通用 Engine 能力

| 能力 | 位置 | 作用 |
| --- | --- | --- |
| `PendingRequestType.SELECT_TARGETS` + `SelectTargetsAction` | `engine/pending.py`、`engine/domain_actions.py`、`engine/runtime.py` | 通用"选择 N 个角色"请求；数量上下限复用 `min_cards/max_cards`，引擎校验候选与去重 |
| `Game.start_target_selection(...)` | `game/basic_cards.py` | 技能与出牌共用同一套选目标 UI（点击角色 / 确认 / 取消），带 `on_complete` / `on_cancel` 回调 |
| `PhaseReplacement.flow` | `skills/definitions.py`、`flows/turn.py` | 阶段替换可以交出子流程：TurnFlow 先挂起，等技能流程结束后按结果决定是否跳过该阶段 |
| `UnequipAtom` | `atoms_v2.py` | 装备区离开的唯一出口：取下 + 可选移动 + 发出 `EQUIPMENT_LOST` |
| `_clear_request_ui` | `engine/runtime.py` | 请求被引擎解决后清理对应的真人选择状态（按 request_id 匹配，兜底非 UI 提交路径） |

`AI` 侧新增 `_respond_select_targets`（按候选顺序取满上限），真人侧新增
`pending_target_selection` 的技能用法（`prompt` 定制 + `on_complete` 回调）。

## 3. 本次真正新增/修复的技能

- **突袭（重写入口）**：从"出牌阶段主动技"改为 `PhaseReplacement(phase=DRAW)` 的
  阶段钩子 + `TuxiFlow`。摸牌阶段开始前询问是否发动 → 通用选目标（1～2 名有手牌的
  其他存活角色）→ 各取一张手牌 → 本回合摸牌阶段整体跳过。取消 = 不发动，摸牌照常。
- **枭姬（触发范围补齐）**：所有装备离场路径统一走 `UnequipAtom`：
  主动换装、过河拆桥、顺手牵羊、麒麟弓、反馈、借刀杀人、死亡清理。
  装备*进入*装备区不触发；同一件装备离开只触发一次。
- **奸雄（覆盖补齐）**：普通杀 / 火杀 / 武圣虚拟杀（还原为原始实体牌）/ 不能凭空取得，
  并修掉被取走后仍被弃置一次的重复移动。

## 4. Card Movement / Draw Phase 关键设计

**装备区离开**：`UnequipAtom(player, slot, destination=None)` 是唯一出口。它在
`apply` 里取下牌、可选移动、发出 `EQUIPMENT_LOST`（payload 含 card / slot /
destination），订阅者（枭姬摸牌、白银狮子回血）不再关心是谁把牌拿走的。
死亡清理时先落死亡标记再清空区域，所以阵亡不触发"失去装备"类技能。

**摸牌阶段**：
```
PHASE_START(DRAW) → phase_offers(DRAW) → can_offer 过滤
  → CONFIRM 询问 → 确认后拿到子流程 → 本回合挂起
  → 子流程（选目标 / 取牌）结束 → applied=True 则 skip(DRAW)，否者照常 _draw_phase()
```
`draw_count(player)` 始终由 `DRAW_COUNT` modifier 汇总计算，与阶段替换互不覆盖：
取消突袭时依然按修正后的数量摸牌，发动突袭时只是整体放弃这一阶段的摸牌。

## 5. 修改文件

| 文件 | 改动 |
| --- | --- |
| `src/game/engine/pending.py` | `SELECT_TARGETS` 请求类型 + `PendingResolution.targets` |
| `src/game/engine/domain_actions.py` | `SelectTargetsAction` |
| `src/game/engine/runtime.py` | 目标选择的路由与校验、`_clear_request_ui`、`discard_processing_card` 修复 |
| `src/game/engine/__init__.py` | 导出 `SelectTargetsAction` |
| `src/game/basic_cards.py` | `start_target_selection`、确认/取消支持技能回调 |
| `src/game/card_selection.py` | 选牌状态记录 `request_id` |
| `src/game/controllers/ai.py` | `_respond_select_targets` |
| `src/game/atoms_v2.py` | `UnequipAtom`；`TransferEquipmentAtom` 复用它 |
| `src/game/flows/turn.py` | 阶段替换支持子流程（`_finish_replacement_child`） |
| `src/game/flows/death.py` | 死亡清理走 `UnequipAtom` 并先落死亡标记 |
| `src/game/card_effects/equipment.py` | 装备替换先按"失去装备"结算旧牌 |
| `src/game/card_effects/tricks.py` | 过河拆桥 / 顺手牵羊的装备路径 |
| `src/game/equipment.py` | `remove_equipment_with_effects` 改用 `UnequipAtom` |
| `src/game/equipment_skills/system.py` | 麒麟弓弃坐骑走 `UnequipAtom` |
| `src/game/skills/definitions.py` | `PhaseReplacement.flow` |
| `src/game/skills/standard/wei.py` | 突袭重写（`TuxiFlow`）、反馈装备路径 |
| `tools/multiplayer_smoke.py` | 支持 `select_targets`、脚本真人对阶段替换一律发动 |
| `docs/rules/phase_8_general_rules_reference.md` | 突袭口径与机制速查更新 |

## 6. 新增测试

| 文件 | 数量 | 覆盖 |
| --- | --- | --- |
| `tests/test_engine_v2_phase8_first_roster.py` | 42 | 注册表 3 · 奸雄 6 · 突袭 9 · 英姿 4 · 枭姬 8 · 已有技能回归 9 · 通用扩展 4 |
| `tests/test_engine_v2_phase8_first_roster_ui.py` | 6 | 突袭真人完整点击链路（含取消）、反间/结姻技能入口、武圣必须先点技能 |

引擎测试的关键点：连续目标选择与上限、排除自己/阵亡/无手牌、取消后正常摸牌、
每回合一次、引擎拒绝非法目标与超量目标、`DRAW_COUNT` 与阶段替换组合、
装备离开的 4 条真实路径（换装 / 过河拆桥 / 顺手牵羊 / 反馈）、装备进入不触发、
同一装备只触发一次、阵亡不触发、奸雄的虚拟牌 provenance 与不可凭空取得。

## 7. 最终测试数字

```
python -m compileall main.py src tests   → PASS
python -m unittest discover -s tests -t . → Ran 510 tests, OK（连续 3 轮稳定通过）
```

基线 462 → 510（新增 48 个确定性测试）。

## 8. Smoke

- `tools/ui_smoke.py`：全部场景通过。
- `tools/multiplayer_smoke.py`：2/4/8 人局 `stuck=None, error=None`。
- 带张辽 / 孙尚香 / 曹操的武将池跑 5 局多人对战：无卡死无异常；
  插桩统计到 **突袭发动 4 次、装备区离开 37 次**，说明新增的两条主路径
  在真实对局中确实被走到。

## 9. 非阻断问题

- 反间目前由引擎自动替目标决定"弃同花色手牌 or 受伤"，没有给目标一次真实的
  二选一 Pending（与规则文档锁定的第一版简化口径一致），记录为后续可选改进。
- 突袭取牌固定取目标手牌的第一张（规则文档写的是"随机获得"）。当前手牌顺序
  对玩家不可见，等效随机；若要严格随机应改用洗牌器的随机源。
- `tools/ui_smoke.py` 旧流程里"Pending 响应按钮 = 不出"的偶发时序问题依旧存在，
  与本阶段无关。
- 逐鹿/救援一类主公技、界限突破技能仍不在本阶段范围内。
