# 技能费用边界复核（2026-09-27）

复核本地提交 `71dedb30be2f15b4024992eb10fd6164ac22d1b9`。上一轮的三项原始缺口在当前代码中已修复：`keep_cards` 素材会检查归属与候选、固定张数按恰好张数检查、获得装备会发失牌事件。重新运行当前工作区的 20 个 `.cache/phase27/probe_*.py`，结果为 **308/308**；`tools/skill_cost_atomicity_audit.py` 为 **31/31**，合计 **339/339**。提交报告写的 301+31=332 与当前工作区脚本数不符：`probe_equipment_discard.py` 现在有 25 条，而报告表中计数仍是此前的 18 条。全部脚本均退出成功。

## 待修：非法【乱击】虽返回失败，仍发出“技能已发动”事件

- 位置：`src/game/skills/activation.py:302-320,374-405`，`src/game/skills/expansions/fire.py:600-623`。
- `plan_activation()` 已确认素材属于袁绍且在手牌中，但没有检查【乱击】的**两张同花色**组合约束。`settle_activation()` 先发 `SKILL_TRIGGERED`，技能函数随后才因花色不同返回 `False`。于是被拒绝的发动仍已通知事件订阅者，也可能触发表现层的技能播报。
- 真实 `Game` 复现：袁绍手里有黑桃【杀】、黑桃【闪】和红桃【桃】，故 `can_activate("luanji") == True`。提交黑桃【杀】+红桃【桃】；`engine.submit()` 返回 `(False, "【乱击】：两张牌的花色必须相同。")`，手牌未移动，但订阅 `EventType.SKILL_TRIGGERED` 的计数器收到 **1** 次事件。
- 修复验收：同花色约束须在 `plan_activation()` 的无副作用阶段完成；非法组合返回规则错误，**不发** `SKILL_TRIGGERED`、不移动牌、不产生效果。合法同花色两张仍正常结算一次。
- 当前 `cost_validator` 已兼容 `keep_cards`：`activation.py:320` 在 `entries` 为空时改传全部 `cards`。可直接把【乱击】的同花色约束接入该钩子，并保留技能结算处的防御性复核。

本次复核未修改游戏代码；仅新增此交接报告，未提交或推送。
