# 费用事务提交复核（2026-09-27）

复核本地提交 `86f2bbe87a311064585aabf2dfe782dce0ee221a`。19 个 `.cache/phase27/probe_*.py` 重跑 287/287 通过；`tools/skill_cost_atomicity_audit.py` 22/22 通过，合计 309/309。以下是现有探针未覆盖的真实引擎输入。本次未修改游戏代码。

## P0：【乱击】等 `keep_cards` 技能跳过素材归属与候选校验

- 位置：`src/game/skills/activation.py:279-300,317-326,338-389`。当 `spec.keep_cards` 为真时，`payable=[]`；第 5 步只遍历 `payable`，因此没有验证 `cards` 里的素材是否属于发动者、是否仍在允许区域、是否符合 `cost_candidates`。随后 `settle_activation()` 把未验证的 `plan.cards` 直接交给技能。
- 真实 `Game` 复现：袁绍在出牌阶段，自己手里有两张同花色牌，另一名角色手里也有两张同花色牌。提交 `ActivateSkillAction(yuanshao, "luanji", cards=[other_card_1, other_card_2])`；`engine.submit()` 返回 `(True, "")` 并结算【万箭齐发】，袁绍自己的两张素材未移动，对方提交的两张牌也仍在对方手里。这让非法提交获得一次免费的群体锦囊。
- 同一路径还包含【直谏】【眩惑】【明策】（均声明 `keep_cards=True`）。需要逐项验证绕过界面的提交不会使用非本人、过期、花色或种类不符的素材；技能自己的校验不能替代引擎边界。
- 修复验收：预验证对**全部提交素材**检查实体唯一、对象归属、允许区域、候选资格，即使素材由技能自行移动也必须检查。非法提交应返回规则原因，不发技能事件、不移动牌、不造成效果。合法的【乱击】仍能以自己的两张同花色手牌正常结算。

## P1：固定张数技能接受超量费用牌

- 位置：`src/game/skills/activation.py:269-280`。非 `variable_cost` 分支只检查 `len(cards) < cost_cards`，没有检查 `len(cards) > cost_cards`；实际支付只取 `cards[:cost_cards]`，多出的牌被静默忽略。这与输入契约声明的固定张数不一致。
- 真实 `Game` 复现：群将向张角发动【黄天】，提交两张不同的【闪】作为 `cards`（技能声明 `cost_cards=1`）。`engine.submit()` 返回 `(True, "")`，只交出第一张，第二张仍在发动者手里，`used=1`。合法的客户端通常不会生成这种提交，但权威引擎接受了非法载荷。
- 修复验收：固定费用应要求恰好 `cost_cards` 张；无费用技能不应接受额外素材；可变费用继续遵守最小/最大张数。超量提交在任何支付或事件前返回规则错误，牌与次数都不变。

## 已记录、尚未核实修复的范围

提交报告第 20 节列出的“获得装备不发 `CARD_LOST`”仍值得下一轮修复。`src/game/skills/mechanics.py:823-833` 在获得装备时调用 `UnequipAtom(player, slot, taker.hand)`，未传 `reason`；`src/game/atoms_v2.py:35-80` 对空 `reason` 不发牌移动通知，因此【屯田】的失牌事件入口看不到这次装备移动。修复应区分“获得/转移”与“弃置”，并对【甘露】交换装备做同样检查。
