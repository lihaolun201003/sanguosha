# 费用边界的两个缺口与装备移动原因（Phase 27.2）

复核依据：`docs/reports/phase_27_1_skill_cost_transaction_review.md`（复核提交
`86f2bbe87a311064585aabf2dfe782dce0ee221a`）。三项全部处理：

1. **P0** `keep_cards` 技能跳过素材的归属与候选校验（【乱击】能拿别人的牌
   打出【万箭齐发】）
2. **P1** 固定张数技能接受超量费用牌（多出来的被静默截掉）
3. 「获得装备」不发 `CARD_LOST`（复核里"值得下一轮修复"的那一条）

没有 push GitHub。

---

## 1. P0 的根因

`plan_activation` 的第 5 步遍历的是 `payable`：

```python
payable = [] if keep else (list(cards) if variable else list(cards[:cost_cards]))
...
for card in payable:            # ← keep_cards 时这个列表是空的
    ... 对象 / 归属 / 区域 / 候选 ...
```

而 `keep_cards` 的语义是"**这张牌不是费用**，它本身就是这次技能动作"——
引擎不代付，去向由技能自己决定。于是 `payable` 为空、循环一次都不跑，
**整个 `cards` 集合没有经过任何规则检查**，随后 `settle_activation` 把它原样
交给技能。技能自己的校验只覆盖它关心的那一部分（【乱击】查了张数与花色），
"这张牌是不是我的"从来没人问过。

实测复现（袁绍 + 曹操，各两张同花色手牌）：

```
提交 ActivateSkillAction(yuanshao, "luanji", cards=[曹操的闪, 曹操的酒])
→ (True, '')        # 结算出一张【万箭齐发】
→ 曹操的闪进了弃牌堆，袁绍自己的两张牌一动没动
```

这不只是"免费用了技能"：那把**别人的牌**当素材用掉了，等于凭空移动对手的牌。

## 2. P0 的修法

把校验对象从 `payable` 换成 `cards`——**玩家提交的每一张牌都要过同一个边界**，
不管它最后由谁移动：

```python
zones = tuple(inputs["allowed_zones"])
candidates = inputs.get("cost_candidates")
check_candidates = candidates is not None and (cost_cards or variable or keep)
placements = {}
for card in cards:                       # ← 全部提交牌，不止 payable
    if card is None:
        return None, "选择的牌不存在"
    placement = cost_placement(player, card, zones)
    if placement is None:
        return None, "选择的牌已经不在可以支付的区域"
    if check_candidates and not any(card is item for item in candidates):
        return None, "这张牌不能用于这次发动"
    placements[id(card)] = placement

entries = tuple((card, placements[id(card)][0], placements[id(card)][1])
                for card in payable)     # 只有要代付的那些进计划
```

`payable` 的含义因此收窄成"引擎要代付的牌"，而"哪些牌**可以**出现在这次
发动里"由 `cards` 统一回答。区域判据用的是同一份 `allowed_zones`——四个
`keep_cards` 技能（【乱击】【直谏】【眩惑】【明策】）的素材都是手牌，默认的
`(CostZone.HAND,)` 正好正确；`cost_candidates` 谓词这轮才第一次真正生效
（以前它们只是给界面高亮用的）。

## 3. P0 验证结果

新增 `.cache/phase27/probe_keep_cards_cost.py`（**14 条，全部通过**）：

| 用例 | 结果 |
| --- | --- |
| 【乱击】提交别人的两张同花色牌 | 拒绝，对方两张牌一张没动 |
| 【乱击】同一张素材提交两次 | 拒绝（`同一张牌不能重复作为费用`） |
| 【乱击】提交 3 张（恰好两张） | 拒绝（`这次发动需要恰好 2 张牌`） |
| 【乱击】只提交 1 张 | 拒绝 |
| 【乱击】自己的两张同花色手牌 | 正常结算，两张素材进弃牌堆 |
| 【直谏】手里有装备牌、提交一张【杀】 | 拒绝（`这张牌不能用于这次发动`） |
| 【眩惑】候选里只有那张红桃手牌（黑桃被谓词挡掉） | 通过 |
| 【眩惑】提交一张黑桃手牌 | 拒绝 |
| 【明策】手里有【杀】、提交一张【闪】 | 拒绝 |

每条"拒绝"的用例都同时断言：不抛异常、双方手牌、弃牌堆、体力一个字节没变。

## 4. P1 的根因

```python
elif inputs["cost_cards"] and len(cards) < inputs["cost_cards"]:
    return None, ...
```

只查了**下限**。多提交的牌随后被 `cards[:cost_cards]` 静默截掉：引擎收下了一份
与自己声明的输入契约不符的载荷，还照常发动，多出来的牌留在手里——提交方
永远不知道它们被忽略过。

实测复现（黄天声明 `cost_cards=1`，吕布两张【闪】）：

```
提交 ActivateSkillAction(lvbu, "huangtian", target=张角, cards=[闪1, 闪2])
→ (True, '')   # 只交出闪1，闪2 还在手里，used=1
```

值得注意的是**协议层比引擎更严**：`network/decisions.py` 的 `_validate_skill`
写的是 `elif chosen != int(entry.get("cost_cards") or 0)` —— 远程真人提交超量本来
就会被拒。锐角在引擎自己这一侧：本地脚本、AI 路径、未来的 replay 都能绕过协议层。

## 5. P1 的修法

固定费用改成"恰好"：

```python
if variable:
    if not cards:
        return None, "至少选择一张牌"
elif len(cards) != cost_cards:
    if len(cards) < cost_cards:
        return None, (spec.cost_prompt if spec is not None else "需要支付更多牌")
    return None, ("这次发动需要恰好 %d 张牌" % cost_cards
                  if cost_cards else "这次发动不需要选择牌")
```

`cost_cards == 0` 的技能（【神愤】一类）因此**不能**收到任何牌——"这次发动
不需要选择牌"。可变费用（制衡 / 举荐 / 极略·制衡）走原来的区间判断
（`1 .. max_cost_cards`），不受影响。

三条入口的实际行为都对得上：本地界面按 `skill_cost_limit()` 限制张数、AI 取
`ranked[:need]`、协议层要求 `== cost_cards`，所以收紧之后没有合法路径被误伤
（287 条旧断言全绿即是证据）。

## 6. P1 验证结果

加进 `tools/skill_cost_atomicity_audit.py` 的"固定张数必须恰好"组（4 条）：

| 用例 | 结果 |
| --- | --- |
| 固定 1 张的技能提交 2 张 | 拒绝，两张都还在手里、`used=0` |
| 无费用技能（【神愤】）提交一张牌 | 拒绝（`这次发动不需要选择牌`） |
| 恰好一张 | 正常发动（正面对照） |

`keep_cards` 素材校验也在同一工具里另起一组（2c，5 条），把 P0 的复现固化成
engine 边界的契约测试——它直接 `engine.submit(ActivateSkillAction(...))`，
不经过任何界面。

## 7. 「获得装备」的失牌事件

复核点名的第三项：装备被**拿走**时对原拥有者没有通知。

`take_card_from_zone`（`mechanics.py`）是"从某名角色的区域里拿一张牌"的唯一
出口，被【归心】【反馈】共用。手牌分支走 `MoveCardAtom`（区域反查得出主人，
已经会发 `CARD_LOST`），装备分支却是：

```python
context.apply(UnequipAtom(player, slot, taker.hand))     # 没声明 reason
```

而 `UnequipAtom` 的规则是"未声明原因 → 不发任何牌移动通知"（上一轮定的兼容
约定）。于是装备被拿走时只有 `EQUIPMENT_LOST`，没有 `CARD_LOST`——
**【屯田】一类"失去牌"的技能看不到这次移动**，和落英修复前是同一个 bug 家族。

修法：新增原因常量 `TAKE_REASON = "lose"`（与 `DISCARD_REASON` 并列，说明
"牌离开了原拥有者的区域但没有进弃牌堆"），并在这三处声明它：

| 位置 | 场景 | 现在的通知 |
| --- | --- | --- |
| `mechanics.take_card_from_zone` 装备分支 | 【归心】【反馈】拿走装备 | `CARD_LOST(reason=lose, owner=原拥有者)` |
| `yijiang.GanluFlow` 交换装备（两处） | 【甘露】双方互换装备 | 双方各一条 `CARD_LOST(reason=lose)` |
| `yijiang._move_anywhere`（新增 `reason` 参数）+ 【眩惑】调用点 | 从目标那里拿一张牌 | `CARD_LOST(reason=lose)` |

`_move_anywhere` 的 `reason` 是**必填语义、不必填参数**（默认空 = 保持原状），
所以其它调用点（获得死者的牌一类）行为不变。

## 8. 「获得装备」验证结果

`probe_equipment_discard.py` 增加一组，走**真实的技能流程**（不是直接调工具
函数）：曹操装备【丈八蛇矛】→ 打司马懿 1 点伤害 → 【反馈】确认发动 →
选"装备区的武器"。实测事件序列：

```
equipment.lost   card=ZHANGBA  slot=weapon
card.lost        card=ZHANGBA  reason=lose  owner=曹操
```

同时断言：牌进了司马懿手里、曹操的 `equipment.zhangba` 技能卸载、
**牌没有进弃牌堆**（拿走 ≠ 弃置）。

## 9. 回归结果

| 检查 | 结果 |
| --- | --- |
| 20 个 phase27 探针 | **301/301 通过**（原 287 条一条没少 + keep_cards 14 条） |
| `tools/skill_cost_atomicity_audit.py` | **31/31 通过**（新增 9 条：张数恰好 4 + keep_cards 5） |
| `tools/input_priority_audit.py` | 45/45 |
| `python -m compileall src/ tools/` | 退出码 0 |
| `tools/multiplayer_smoke.py` | 4/4 `over=True`，无 stuck / error |
| `tools/lan_smoke.py` | 44/44 |
| `tools/soak_play.py 10 1` | 9 finished / 1 normal_wait（seed 10 的既有问题），`crashed=0`、`blocked=0` |

合计 **332 条断言**（301 + 31）全部通过。

批量试玩 10 局（同起始种子）：

| 指标 | 结果 |
| --- | --- |
| 完成 | 9 |
| 等待超时（在等演出 / 别人） | 1（seed 10，与前几轮逐帧一致的既有问题） |
| 崩溃 / 被闸门拦下的点击 | 0 / 0 |
| "费用重复"不变量命中 | 0 |

（40 局连跑在这台机器上两次都在中途被环境掐掉，10 局这轮是干净跑完的；
前面几轮的 40 局数据可作对比，本轮的代码改动面很小且被 332 条断言覆盖。）

## 10. 修改文件

| 文件 | 改了什么 |
| --- | --- |
| `src/game/skills/activation.py` | 第 3 步改成"固定张数恰好"；第 5 步改成校验**全部提交牌**（含 `keep_cards` 素材），支付计划只取 `payable` |
| `src/game/atoms_v2.py` | 新增原因常量 `TAKE_REASON = "lose"` |
| `src/game/skills/mechanics.py` | `take_card_from_zone` 装备分支声明 `reason=TAKE_REASON` |
| `src/game/skills/expansions/yijiang.py` | 【甘露】交换装备两处声明 `reason`；`_move_anywhere` 增加 `reason` 参数；【眩惑】调用点声明 `reason="lose"` |
| `tools/skill_cost_atomicity_audit.py` | 新增"固定张数必须恰好"（4 条）与"keep_cards 素材校验"（5 条）两组 |

探针（`.cache/` 不进版本库）：`probe_keep_cards_cost.py` 新增；
`probe_equipment_discard.py` 增加"获得装备"一组。

## 11. 仍然存在的技术债

上一轮报告第 20 节的清单里，第 4 条（获得装备不发 `CARD_LOST`）本轮已修。
其余仍在：

1. **换装的弃置语义没有定论**（旧装备"置入弃牌堆"算不算【落英】意义上的
   "弃置"）——需要按项目选用的规则版本敲定，本轮仍未动。
2. **`MoveCardAtom` 的"留空即弃置"仍是隐式默认**——彻底做法是让 `reason`
   必填，那是跨几十个文件的大迁移。
3. **三处 `deck.discard(card)` 旁路**（虚拟牌素材、【再起】红桃、【不屈】清空）
   直接往弃牌堆追加，不发任何通知。
4. **`keep_cards` 技能仍然自己移动素材**——现在素材的**合法性**受引擎边界
   约束了，但"移动"这件事仍是技能自己的代码。长期看应该收敛成"技能只描述
   结果、移动由原子统一做"。
5. **`cost_validator` 钩子仍然没有技能使用**——【乱击】的"两张同花色"是它
   的天然用例，本轮没接（技能自己的校验已经覆盖，接上去只是让"拒绝"提前到
   支付之前，收益是错误信息更准）。下一轮可以考虑把它接上。
