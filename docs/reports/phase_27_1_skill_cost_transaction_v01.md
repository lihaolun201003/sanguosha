# 技能费用的交易语义（Phase 27.1）

复核依据：`docs/reports/skill_rules_audit_20260927_hotfix_review.md`（复核提交
`dc9d1e78fbc4532de2a0b7df9171db33c365bdc1`）。本轮把"技能费用合法性 → 预验证 →
原子支付 → Card Move reason → 对应事件"这条链路收口，修的是一类**引擎边界**
问题，不是三个孤立的 if。

没有 push GitHub。

---

## 1. 重复实体费用 Bug 的根因

复核给出的复现：孙权手牌只有一张【杀】，提交
`ActivateSkillAction(sunquan, "zhiheng", cards=[sha, sha])`。

旧代码在支付前做的是"把每张**提交的牌**定位一次"：

```python
for card in payable:
    placement = cost_placement(player, card, zones)   # 两次问的都是同一张
    if placement is None:
        return False, "选择的牌已经不在可以支付的区域"
    placements.append(placement)
```

两次 `cost_placement` 问的都是"**那张还躺在手牌里的**【杀】在不在合法区域"——
第一次问的时候它在，第二次问的时候它当然**还在**（还没有任何人动过它）。
于是校验全票通过，进入支付：

1. 第一次移动：`sha` 从手牌进弃牌堆；
2. 第二次移动：`MoveCardAtom` 找不到来源牌 → `raise ValueError`。

结果是三种状态同时成立：技能**没发动**、没摸牌、`used == 0`，但那张【杀】
已经永久进了弃牌堆。玩家的损失无法恢复，而技能看起来还能再发动一次。

根因不是"移动会失败"，而是**校验的粒度错了**：它逐张验证"这张牌现在合法吗"，
却从来没有验证过"我要支付的是一组**互不重复**的牌"。

## 2. 为什么"先定位、后支付"仍然不原子

这个顺序本身是对的，缺的是"定位"这一步的信息量。定位只回答了
"这张牌**现在**在哪"，没回答：

| 问题 | 旧实现 | 后果 |
| --- | --- | --- |
| 同一张牌提交了两次吗 | 没问 | 第二次支付必然失败 |
| 牌现在归谁 | 没问（隐含"就是发动者"） | 别人的牌混进来时定位失败得晚 |
| 数量在范围内吗 | 问了，但只问下限 | — |
| 候选资格（谓词） | 问了，但和区域校验分成两段 | — |

关键在于"定位"是**对每一张牌独立**的判断，它天然看不到跨牌的约束。
只要集合里存在重复，逐张定位的结果就永远是"全部合法"——支付阶段才第一次
发现两张其实是同一张，而那时候第一张已经动了。

## 3. 最终的 Preflight 机制

`resolve_activation` 拆成三段，顺序固定（`src/game/skills/activation.py`）：

```
plan_activation(engine, action)  →  (plan, message)   纯校验，不改任何状态
pay_cost(engine, plan)                                 只读计划，按已定位置支付
settle_activation(engine, plan)                        发技能事件 + 执行技能效果
```

`plan_activation` 按固定顺序把十件事一次问完，**任何一项不过就返回
`(None, 原因)`**：

1. 技能本身现在能不能发动（`can_activate` / `granted_state`：时机、次数、
   标记、阵营）
2. 目标合法（存在且在候选里）
3. 费用张数（下限、`max_cost_cards` 上限）
4. **实体唯一**（新增，见第 4 节）
5. 每张牌：对象存在 → 归属是发动者 → 在 `allowed_zones` 里 → 在候选谓词里
6. 组合约束（`cost_validator` 钩子，目前没有技能声明，见第 20 节）

通过之后得到一份 `ActivationPlan`：`((card, zone, slot), ...)` + 目的地 +
`keep_cards` 标志。**支付阶段只读这份计划，不再查任何状态**，所以"付到一半
发现不合法"在结构上不可能发生——它要么在 preflight 就被拒，要么必然全部付得出去。

返回值只有两种：`(True, "")` 或 `(False, 规则原因)`。

## 4. Duplicate Identity 校验

```python
def duplicate_cost_card(cards):
    seen_objects = set(); seen_ids = set()
    for card in cards:
        if id(card) in seen_objects: return card          # 同一张牌提交两次
        cid = str(getattr(card, "id", "") or "")
        if cid and cid in seen_ids: return card           # 两个对象、同一个牌
        seen_objects.add(id(card))
        if cid: seen_ids.add(cid)
    return None
```

两个维度都查，因为它们的现实来源不同：

* **对象身份**（`id(card)`）——界面 / AI / 脚本把同一张牌放进列表两次；
* **牌自己的 `card.id`**——反序列化或恶意构造出来的"两个对象、同一个牌子"。

`Card.id` 由 `Card` 自动分配（`init=False` 的递增字段）、进程内唯一，所以
第二个维度不会误伤合法提交。项目里"实体牌一律按身份比较、绝不用 `==`"
这条约定在这里同样成立——`Card` 是 dataclass，值相等会让两张同名同花色的
牌互相冒充。

校验发生在**任何移动之前**（`plan_activation` 的第 4 步），返回
`"同一张牌不能重复作为费用"`。

**不靠界面去重**：LAN 客户端、脚本、未来的 replay 都能自己构造
`ActivateSkillAction`；协议层（`network/decisions.py` 的 `_validate_skill`）
只校验"提交的牌在房主下发的候选里"，重复 id 两次都在候选里，所以协议层
**不会**拦——拦住的必须是引擎。

## 5. stale candidate 校验

同一个 preflight 里的第 5 步，对每张要支付的牌重新定位一次区域：

```python
placement = cost_placement(player, card, zones)
if placement is None:
    return None, "选择的牌已经不在可以支付的区域"
```

覆盖三种漂移：牌已经被移走（不在任何合法区域）、牌换到了别的区域（手牌 →
装备区，而这次技能只吃手牌）、槽位已经被别人占（`slot` 指向的对象已经不是
那张牌）。因为校验与支付读的是**同一个** `cost_placement`，界面能选中的牌
与引擎会接受的牌永远是同一个集合。

## 6. Rule Error 如何返回

* **玩家输入问题** → `(False, 中文原因)`，同时写 `game.message` 与战报。
  引擎不抛异常，所以 LAN 房主的 `_activate_skill` 拿到的是"这次发动被拒绝"，
  而不是一个冒到 `match.py` 兜底里的异常（那个兜底会 `self.abort(...)`
  把整局终止）。
* **编程错误**（`skill_id` 根本不存在 / 定义没有 `activate`）→ 仍然
  `raise ValueError`。这不是玩家能造出来的输入，装成"规则拒绝"只会掩盖缺陷。

新增的错误原因（都有对应断言）：`同一张牌不能重复作为费用`、
`选择的牌已经不在可以支付的区域`、`选择的牌不存在`、`至少选择一张牌`、
`最多只能选择 N 张牌`、`这张牌不能用于这次发动`、`目标不合法`。

## 7. 制衡验证结果

`probe_zhiheng.py` 41/41 通过（原有 39 条 + 交易语义相关的新断言），
`tools/skill_cost_atomicity_audit.py` 的 1.1–1.8 覆盖恶意提交：

| 用例 | 结果 |
| --- | --- |
| 手牌里同一张【杀】提交两次 | 拒绝、牌不动、弃牌堆不变 |
| 装备区同一张【诸葛连弩】提交两次 | 拒绝、槽位不变 |
| 手牌 + 装备混选里夹一个重复 | 整次拒绝，**两张都留在原区域** |
| 同样的混选但不重复 | 正常发动（正面对照） |
| stale 费用（提交时装备已被拿走） | 拒绝，手牌不动 |
| 手里有牌却提交空费用 | 拒绝（`至少选择一张牌`） |
| 费用里混入非牌对象 | 拒绝（`选择的牌不存在`） |
| 两个对象、同一个 `card.id` | 拒绝（`同一张牌不能重复作为费用`） |

## 8. 极略·制衡验证结果

同一条费用入口，只是代价多一枚「忍」标记。断言：

* 重复费用 → 拒绝，且 `mark_count(player, "renjie", "ren")` **一枚都没扣**；
* 合法发动 → 扣一枚「忍」并摸一张；
* `probe_jilue_lianpo.py` 14/14 与 `probe_zhiheng.py` 的极略场景全部通过。

标记只在技能自己的 `_activate_jilue` 里扣，而它只在支付成功之后被调用——
"费用失败"与"技能效果为空"是两件事，前者走不到扣标记那一行。

## 9. 举荐规则版本核实

项目选用版本的文本（`src/game/skills/expansions/yijiang.py` 与
`src/game/generals/expansions.py` 的徐庶条目）：

> 【举荐】：出牌阶段限一次，你可以弃置至多三张**牌**令一名其他角色摸等量
> 的牌；弃置不少于三张且类别相同时，你回复 1 点体力。

写的是"牌"，不是"手牌"——与【制衡】同一条措辞。旧实现的三处把区域收窄成了
手牌：`_can_jujian` 的 `if not player.hand`、`ActiveSkillSpec` 未声明区域
（默认只有手牌）、`_activate_jujian` 自己移动牌时也只找手牌。

## 10. 举荐的费用范围

改成与【制衡】**同一个**机制，没有一行按技能名分支：

```python
JUJIAN_SPEC = ActiveSkillSpec(
    needs_target=True, target_candidates=_jujian_targets, ...,
    variable_cost=True, max_cost_cards=3,
    cost_prompt="【举荐】：请选择至多三张牌弃置",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)
```

`_can_jujian` 的"有没有牌可弃"改调同一份候选查询（`cost_candidates`），
所以"手牌 0 + 装备区有牌"时它会如实回答"可以发动"。同时删掉了
`_activate_jujian` 里那段**自己移动牌**的代码（`MoveCardAtom(card,
source=player.hand, ...)`）——引擎已经按 spec 支付过了，技能再动一次就
等于两边各说一遍"到底弃了几张"；那段代码靠 `if any(item is card for item
in player.hand)` 侥幸没出错，但它是一处真实的技术债。

`probe_jujian.py` 17/17 通过：只用手牌 / 只用装备 / 手牌 0 + 装备 1 /
手牌 + 装备混选都能发动；上限 3 张生效；取消与 stale 不动牌；重复实体拒绝；
装备按卸装原子离开（槽空 + 装备技能卸载 + 进弃牌堆）；三张同类回复 1 点、
类别不同不回复；本回合限一次。

## 11. UnequipAtom 的原问题

`UnequipAtom` 是装备区离开的统一出口（换装、被拆、被顺、被弃、死亡清理都
走它）。它只发 `EQUIPMENT_LOST`，然后把牌 `append` 到 destination——**从来
不发 `CARD_DISCARDED`**。于是"因弃置进入弃牌堆"这条规则语义在装备区整条
丢失：

* 制衡弃掉梅花装备 → 弃牌堆里确实有那张牌，但【落英】收不到任何通知；
* 过河拆桥弃掉装备 → 更糟：牌由随后的 `MoveCardAtom(source=None)` 放进弃牌堆，
  而 `source is None` 时区域反查不出主人，`payload["owner"]` 是 `None`，
  落英即使收到事件也会因为"不知道是谁的牌"而不发动。

## 12. Move reason 的最终设计

装备离开装备区的**原因**必须由调用方声明，不再由目的地推断：

```python
@dataclass
class UnequipAtom(Atom):
    player: object
    slot: str
    destination: object = None
    reason: str = ""          # 与 MoveCardAtom.reason 同一套词汇
```

并新增一个全项目唯一的牌移动通知出口（`atoms_v2.emit_card_moved`），
`MoveCardAtom` 与 `UnequipAtom` 都走它：

```python
def emit_card_moved(context, card, *, source, destination, reason, owner, extra=None):
    if not reason:
        return                                    # 没声明语义 → 不发任何通知
    ...payload = {card, reason, owner, from, **extra}
    if destination is deck.discard_pile:          # 进弃牌堆 → CARD_DISCARDED
        emit(CARD_DISCARDED, reason=reason, ...)
    elif owner is not None:                       # 离开某人的区域 → CARD_LOST
        emit(CARD_LOST, ...)
```

三条设计决定：

1. **`reason` 为空 = 不通知**。这是兼容承诺：那些"尚未归类"的装备移动
   （换装、死亡清理）不会因为这次收口被误当成弃置。`MoveCardAtom` 保留
   它原有的"留空即弃置"（它的调用点绝大多数本来就是弃牌，且现有 252 条
   断言依赖它），所以这一改动对既有路径是**零行为变化**。
2. **`owner` 显式传**。装备区的牌在移动的一瞬间已经不在槽里了，靠区域
   反查是查不出主人的——`UnequipAtom` 直接用 `self.player`；过河拆桥那类
   "先卸下、再放进弃牌堆"的两段式路径由调用方把原拥有者传下来。
3. **`reason` 由订阅者解释，不由出口过滤**。落英只认 `discard` / `judge`；
   出口把 `reason` 如实发出去，`CARD_DISCARDED` 仍然会因为"使用后置入"、
   "判定后置入"而发出——过滤是订阅者的事。

装备移动原因的实际赋值：

| 调用点 | 语义 | reason |
| --- | --- | --- |
| `skills/activation.py`（技能费用弃装备） | 弃置 | `discard` |
| `card_effects/tricks.py`（过河拆桥） | 弃置 + 原拥有者 | `discard` + `owner` |
| `card_effects/tricks.py`（顺手牵羊） | 拿走 | `lose` + `owner` |
| `equipment_skills/system.py`（麒麟弓弃坐骑） | 弃置 | `discard` |
| `expansions/mountain.py`（挑衅 / 悲歌 ×2） | 弃置 | `discard` |
| `expansions/god.py`（神愤弃全场装备） | 弃置 | `discard` |
| `expansions/fire.py`（猛进 / 涅槃） | 弃置 | `discard` |
| `standard/wu2.py`（奇袭） | 弃置 | `discard` |
| `expansions/yijiang.py`（挥泪） | 弃置 | `discard` |
| `modes/identity.py`（主公误杀忠臣弃装备） | 弃置 | `discard` |

每条的理由都很直白：这些技能的文本本身就写着"弃置"。

## 13. CARD_DISCARDED 与 EQUIPMENT_LOST 的关系

它们回答的是两个不同的问题，同一次移动可以**两条都发**：

* `EQUIPMENT_LOST` —— "这张牌**离开了装备区**"（装备技能卸载、装备修正
  移除、枭姬 / 白银狮子一类响应都挂在它上面）；
* `CARD_DISCARDED` —— "这张牌**因为某个原因进了弃牌堆**"，`reason` 与
  `owner` 都在 payload 里。

顺序固定：取下装备 → 放进 destination → 发 `EQUIPMENT_LOST` →
`sync_equipment_skills`（卸载装备赋予的技能）→ 按 `reason` 发牌移动通知。
所以制衡弃【丈八蛇矛】时，探针实测到的事件序列是：

```
equipment.lost   card=ZHANGBA  slot=weapon
card.discarded   card=ZHANGBA  reason=discard  owner=孙权
```

## 14. 落英验证

`probe_equipment_discard.py` 18/18 通过，用真实的曹植（【落英】是可触发技，
探针替真人回答那个"你可以…"窗口）。梅花装备的各种去向：

| 场景 | 期望 | 实测 |
| --- | --- | --- |
| 制衡弃梅花装备 | 落英可以拿 | ✓（事件序列见上） |
| 举荐弃梅花装备 | 落英可以拿 | ✓ |
| 过河拆桥弃梅花装备 | 落英可以拿 | ✓（同时验证装备技能卸载） |
| 顺手牵羊拿走梅花装备 | 不算弃置 | ✓（牌进曹操手里，落英没动） |
| 新装备替换旧装备 | 不算弃置 | ✓（旧装备进弃牌堆，落英没动） |
| 角色死亡清理 | 不算弃置 | ✓ |
| 红桃装备被弃置 | 花色不对，不拿 | ✓ |

## 15. 哪些装备移动不属于 discard

明确**没有**标 `discard` 的路径，以及为什么：

1. **换装**（`EquipCardAtom` 与 `card_effects/equipment.py`）——旧装备"置入
   弃牌堆"，是替换的结果，不是任何人"弃置"了它。项目里这条路径一直没有
   弃置语义，本轮保持原状并在第 20 节列为待确认项。
2. **角色死亡清理**（`flows/death.py`）——文件里已有明确注释："死亡清理不是
   '失去装备'，枭姬一类技能不会因为角色退场而被触发"。牌不属于任何人，
   落英的"其他角色的牌"条件也不成立。
3. **顺手牵羊 / 技能拿走**（`tricks.py`、`mechanics.py`）——牌进的是别人的
   手牌，不是弃牌堆。
4. **借刀杀人转交**（`TransferEquipmentAtom`）——装备转给另一个人，仍然
   在场上。
5. **甘露**（两人交换装备区）、**行殇 / 烈刃**（获得死者的装备）——都是
   "获得"，不是弃置。
6. **通用取下**（`equipment.py:remove_equipment_with_effects`）——没有
   destination 的中间态，调用方随后自己决定去哪儿。

## 16. Engine boundary 恶意提交结果

新增 `tools/skill_cost_atomicity_audit.py`（**22 项，全部通过**，退出码 0）。
它绕过所有界面，直接 `game.engine.submit(ActivateSkillAction(...))`，每一条
都同时断言两件事：**不抛未捕获异常** + **状态指纹一个字节都没变**
（手牌对象序列、装备槽、弃牌堆长度、全部 skill_state）。覆盖：

* 制衡：重复手牌 / 重复装备 / 混选含重复 / 空费用 / 非牌对象 / 两个对象同
  一个牌 id；
* 举荐：超过三张 / 无目标 / 目标是自己 / 本回合已用过；
* 授予型（黄天）：没有授予关系 / 真实授予关系下重复费用 / 非候选牌；
* 资源：极略的「忍」标记在非法提交下**不扣**；
* 未知技能 id 仍然抛 `ValueError`（编程错误，故意保留）；
* 房主侧落地入口 `game.skills.activate` 对同一份重复提交同样拒绝且状态不变。

**LAN 侧**：远程提交最终也走 `game.skills.activate` → `resolve_activation`
（`RemoteHumanController._activate_skill`），所以引擎这一层的阻断对 Guest
提交同样生效。协议层 `_validate_skill` 只校验"牌在候选里"，重复 id 会通过
协议层、由引擎拒绝——这正是本轮要保证的层次关系：**界面与协议都不是安全
边界，引擎才是**。`tools/lan_smoke.py` 44/44 通过（含真实 TCP 的两个实例）。

## 17. 原 252 条测试结果

17 个 phase27 探针原样保留，**252/252 全部通过**：

| 脚本 | 条数 | | 脚本 | 条数 |
| --- | --- | --- | --- | --- |
| probe_beige | 8 | | probe_luanwu | 10 |
| probe_fankui_liuli | 17 | | probe_optional_skills | 21 |
| probe_gongxin | 9 | | probe_prepare_skills | 14 |
| probe_guixin | 15 | | probe_shelie | 10 |
| probe_guzheng | 10 | | probe_shenfen | 10 |
| probe_huangtian | 34 | | probe_wuhun | 5 |
| probe_jilue_lianpo | 14 | | probe_wushen | 13 |
| probe_jixi | 10 | | probe_xinzhan | 11 |
| probe_zhiheng | 41 | | | |

其他检查：

| 检查 | 结果 |
| --- | --- |
| `python -m compileall src/ tools/` | 退出码 0 |
| `tools/multiplayer_smoke.py` | 4/4 `over=True`，无 stuck / error |
| `tools/lan_smoke.py` | 44/44 |
| `tools/input_priority_audit.py` | 45/45 |
| `tools/skill_cost_atomicity_audit.py` | 22/22（新增） |
| `tools/soak_play.py 40 1` | 见第 18 节 |

## 18. 新增测试结果

| 新增 | 条数 | 内容 |
| --- | --- | --- |
| `.cache/phase27/probe_jujian.py` | 17 | 举荐费用区域 / 上限 / 取消 / stale / 重复 / 装备卸载 / 效果 |
| `.cache/phase27/probe_equipment_discard.py` | 18 | 装备移动的 reason 语义（落英口径，7 种去向） |
| `tools/skill_cost_atomicity_audit.py` | 22 | engine boundary 恶意提交 + 状态指纹不变 + 标记不扣 |

合计新增 **57 条**，加上原有 252 条共 **309 条断言全部通过**。

批量试玩（40 局，与上一轮基线同起始种子）：

| 指标 | 上一轮基线 | 本轮 |
| --- | --- | --- |
| 完成 | 37 | 36 |
| 等待超时（在等演出 / 别人） | 2 | 2 |
| 跑满帧数上限 | 1 | 2 |
| 崩溃 / 被闸门拦下的点击 | 0 / 0 | 0 / 0 |
| 点击被收下但没效果 | 6 | 9 |
| 总帧数（中位 / 最大） | 3150 / 9000 | 3315 / 9000 |
| "费用重复"不变量命中 | — | **0** |

40 局里三局非完成（seed 10 / 28 等待超时、seed 31 帧数上限）与上一轮**逐局
一致**，是既有问题。唯一多出来的一局是 seed 33（上一轮 7122 帧完成、本轮
跑满 9000）——但单跑两次都是 7122 帧完成、3 局连跑（31–33）也完成
（7064 帧），而且它在 40 局连跑里的 91 次点击**全部有效**，说明局面一直在
推进、不是卡死。连跑逐局本来就不逐位可复现（AI 随机源在非 1v1 下未播种，
牌 id 计数器又随局累积），所以这一局的长度差异是波动，不是回归。

帧数整体比上一轮长约 5%（中位 3150 → 3315），与新特性一致：试玩现在真的会
发动带费用的主动技，局面因此更复杂一点。

**试玩顺手补上了一个覆盖空白**：原来的脚本在技能输入界面里只点目标和确认，
**从来不点费用牌**，所以带费用的主动技（制衡 / 举荐 / 仁德…）在批量试玩里
从来没被发动过。现在它会按 `max_cost_cards` 挑够用的最小张数（最多两张），
并新增一条常驻不变量：**同一次技能费用里不允许出现同一张实体牌两次**，
一旦出现就记下 seed / 技能 / 牌 id（引擎自己也会拦，这条只是把"界面是怎么
凑出这份费用的"记下来）。

## 19. 修改文件

| 文件 | 改了什么 |
| --- | --- |
| `src/game/atoms_v2.py` | 抽出 `emit_card_moved`（唯一的牌移动通知出口）+ `DISCARD_REASON`；`UnequipAtom` 增加 `reason`；`TransferEquipmentAtom` 透传 `reason` |
| `src/game/skills/activation.py` | 三段式 `plan_activation` / `pay_cost` / `settle_activation`；实体唯一校验；区域与候选在同一段校验；装备费用走 `UnequipAtom(reason="discard")` |
| `src/game/skills/expansions/yijiang.py` | 举荐：`JUJIAN_SPEC`（手牌 + 装备区）+ `_can_jujian` 共用候选查询 + 删掉技能内自付费用的代码；挥泪的装备弃置补 reason |
| `src/game/card_effects/tricks.py` | 过河拆桥 / 顺手牵羊：装备来源显式声明 reason 与原拥有者 |
| `src/game/equipment_skills/system.py` | 麒麟弓弃坐骑标 `discard` |
| `src/game/skills/expansions/mountain.py` | 挑衅 / 悲歌 ×2 标 `discard` |
| `src/game/skills/expansions/god.py` | 神愤弃全场装备标 `discard` |
| `src/game/skills/expansions/fire.py` | 猛进 / 涅槃标 `discard` |
| `src/game/skills/standard/wu2.py` | 奇袭标 `discard` |
| `src/game/modes/identity.py` | 主公误杀忠臣弃装备标 `discard` |
| `tools/soak_play.py` | 技能输入里会点费用牌；新增"费用不重复"常驻不变量 |
| `tools/skill_cost_atomicity_audit.py` | **新增**：engine boundary 自检（21 项） |

探针（`.cache/` 不进版本库）：`probe_jujian.py`、`probe_equipment_discard.py` 新增。

## 20. 仍然存在的 Card Move 技术债

1. **换装的弃置语义没有定论**。旧装备"置入弃牌堆"到底算不算【落英】意义上
   的"弃置"，需要按项目选用的规则版本敲定。本轮**故意不动**：改它会影响
   所有换装场景（每一次换武器都会触发弃置类技能），而"不确定就别改"比
   "猜一个"安全。真要改，只需给 `EquipCardAtom` 里的那次 `UnequipAtom`
   补一个 `reason`。
2. **`MoveCardAtom` 的"留空即弃置"仍是隐式默认**。它的绝大多数调用点本来
   就是弃牌，但这也意味着任何一处"忘传 reason"都会被当成弃置。彻底的做法
   是让 `reason` 变成必填——那是一次跨几十个文件的大迁移，本轮不做。
3. **三处 `deck.discard(card)` 旁路**（`core.py` 的虚拟牌素材、`forest.py`
   的【再起】红桃、`wind.py` 的【不屈】清空）直接往弃牌堆里追加牌，不发
   任何通知。它们目前都不需要（牌堆顶的牌 / 专属牌区的牌没有"其他角色的
   牌"这层归属），但它们是"绕过标准移动"的路径，值得在下一轮统一。
4. **技能"获得装备"时不发 `CARD_LOST`**（`mechanics.py` 的
   `UnequipAtom(player, slot, taker.hand)`、`yijiang.py` 的 `_move_anywhere`、
   甘露的交换）——牌确实离开了原拥有者的区域，但因为没有声明 reason，出口
   保持沉默。**【屯田】一类"失去牌"的技能因此在"被拿走装备"时收不到通知**，
   与落英修复前的形态是同一个 bug 家族。
5. **`keep_cards` 的技能自行移动牌**。举荐那段死代码已经删掉，但同类写法
   （技能自己在 `activate` 里 `MoveCardAtom`）在别处仍然存在；它们与引擎的
   支付是两条并行的真相，值得逐步收敛到"技能只描述结果、移动由原子统一做"。
6. **`cost_validator` 钩子目前没有技能使用**。它已经接进 `plan_activation`
   （第 6 步），但项目里还没有"费用组合约束"的真实需求（【举荐】的"三张
   同类"是**效果**判定，不是费用合法性）。等真有需要时再声明，本轮先留
   接口位而不硬造用例。

---

## Git

本轮改动**本地提交**，没有 push。

提交内容 = 本轮的费用交易语义收口（引擎 preflight / 装备移动 reason /
举荐费用区域）+ 新增的 engine 边界自检工具与两个探针。
