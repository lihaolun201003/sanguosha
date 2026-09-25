# Phase 8 第一批武将收官 · 开工审计

审计时间点：第一批 11 名武将收官 Sprint 开工时。

## 一、技能状态盘点（A 已完成 / B 有缺口 / C 占位 / D 未实现）

| 技能 | 状态 | 说明 |
| --- | --- | --- |
| 咆哮 | A | `SLASH_QUOTA` modifier，有测试 |
| 集智 | A | `CARD_USED` 触发 |
| 刚烈 | A | `DAMAGE_SETTLED` + 通用 JudgeFlow（上一阶段已验证可改判） |
| 奸雄 | A | 已按 `damage.card` / `source_cards` 取实体牌，缺火杀与"牌已不在合法区域"的覆盖 |
| 鬼才 | A | Judge Replacement（上一阶段完成） |
| 天妒 | A | `JUDGE_FINISHED` 取最终判定牌 |
| 突袭 | **B** | 注册为"出牌阶段主动技"，但 `can_activate` 要求 `turn_phase is DRAW`，而摸牌阶段没有任何交互窗口 —— **真人永远无法发动**；且目标由引擎自动挑选，玩家不能选 |
| 武圣 | A | CardConversion，先点技能再选牌 |
| 龙胆 | A | CardConversion 双向 |
| 英姿 | A | `DRAW_COUNT` +1 |
| 反间 | A | `FanjianFlow` 分步结算 |
| 结姻 | A | 主动技 + 费用牌 + 目标校验 |
| 枭姬 | **B** | 只订阅 `EQUIPMENT_LOST`，但该事件只由 `remove_equipment_with_effects` 一处发出；过河拆桥 / 顺手牵羊 / 麒麟弓 / 反馈 / 借刀杀人 / 换装旧牌 / 死亡清理都不发事件 |

## 二、发现的三个结构性问题

1. **阶段替换无法交互**：`PhaseReplacement.apply` 只能同步返回 bool，技能一旦需要
   玩家补全输入（选目标）就没法接。
2. **没有"选择角色目标"的 Pending**：只有 RESPOND_CARD / CONFIRM / SELECT_CARDS /
   CHOOSE_OPTION，突袭这类"选 N 个角色"的技能无处表达。
3. **装备区离开没有统一出口**：`player.remove_equipment(slot)` 被 7 处直接调用，
   事件只在其中一条路径上发出，订阅 `EQUIPMENT_LOST` 的技能（枭姬、白银狮子）
   会漏触发。

## 三、附带发现的真实 Bug

- `GameEngine.discard_processing_card` 在牌已被技能取走时（奸雄 / 天妒）仍会把同一张
  实体牌追加进弃牌堆 —— 造成同一张牌同时存在于手牌与弃牌堆。已修复。
- 真人用引擎接口（脚本 / 测试）直接解决请求时，`pending_target_selection` /
  `pending_selection` 会残留，导致下一次提交带着过期 request_id 撞上引擎校验。
  已通过 `_clear_request_ui` 兜底修复。
- `tests/test_engine_v2_phase5_ui.py` 的一个用例依赖随机武将分配（抽到曹操时奸雄会
  拿走那张杀），属于既有 flaky；已改为不依赖具体武将的断言。
