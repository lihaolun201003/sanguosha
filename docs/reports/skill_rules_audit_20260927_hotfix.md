# 技能规则热修：黄天发动窗口 + 制衡费用区域（2026-09-27）

本轮只做两件事，都是复核报告 `skill_rules_audit_20260927_review.md` 里已经真实
复现、规则依据明确的缺口：

1. 【黄天】只在出牌阶段**开始**时能交牌 → 改成整个出牌阶段内的主动动作
2. 【制衡】/【极略·制衡】只吃手牌 → 改成"任意张**牌**"（手牌 + 装备区）

没有新增武将、没有重构技能系统、没有 push。

---

## 1. 【黄天】根因

旧实现把黄天做成**拥有者（张角）身上的 PHASE_START 触发技**：

```python
class Huangtian(Skill):
    def bindings(self):
        return (SkillBinding(EventType.PHASE_START, priority=30),)

    def can_trigger(self, context, event):
        if event.payload.get("phase") is not TurnPhase.PLAY: return False
        donor = event.source                     # 进入出牌阶段的那个人
        ...
        return bool(_huangtian_cards(donor))
```

于是"能不能发动"的判断发生在**某一个瞬间**（群势力角色进入出牌阶段的那一
帧），而不是**整个阶段**。由此派生出三个必然的错误：

| 现象 | 原因 |
| --- | --- |
| 阶段中途摸到【闪】/【闪电】时没有入口 | 那一刻的窗口早就开过了，之后不再有任何查询时机 |
| 阶段开始时拒绝一次 → 本阶段再也交不了 | 事件只发一次，没有第二次入口 |
| 一进阶段就弹窗，玩家必须处理 | 它是"事件驱动的自动弹窗"，不是玩家主动点的技能 |

还有第三个语义错误（与本轮两处修复无关，但在同一条链上）：交不交、交哪张的
决定权在**交牌的人**手里，而技能却挂在张角身上。

## 2. 为什么"阶段开始触发"是错的

官方文本（2010 修订版，本项目的标准版张角）：

> 主公技，其他群势力角色的出牌阶段限一次，该角色可以将一张【闪】或【闪电】
> 交给你。

"出牌阶段限一次"限定的是**整个出牌阶段**里的**成功使用次数**，不是
"只能在出牌阶段开始时发动"——这两者在游戏规则里从来不是一回事（对比
"出牌阶段开始时，你可以…"这类真正限定起点的写法）。把次数限制交给"事件只在
阶段开始时发一次"来实现，等于把**时机**和**次数**两件事绑死在一个事件上，
于是"放弃一次"就被错误地当成了"本阶段已使用"。

## 3. 【黄天】最终建模方式

引入**授予型主动技**（`SkillDef.grant = GrantedSpec(...)`）：技能属于张角，
发动权与费用在**那名群势力角色**手里。它和普通主动技共用同一套输入契约、
同一套校验、同一套支付，区别只有"谁是 owner"。

```python
SkillDef(
    id="huangtian", name="黄天", kind=SkillKind.ACTIVE,
    activate=_activate_huangtian,
    grant=GrantedSpec(can_offer=_huangtian_can_offer, spec=HUANGTIAN_SPEC),
    tags=("active", "granted_to_others", "card_transfer"),
    is_lord_skill=True,
)
```

输入契约（`ActiveSkillSpec`）：`needs_target=True`（目标 = 持有黄天的张角，
多张角时就是"交给谁"的候选）、`cost_cards=1`、`transfer_cards=True`（那张牌
直接进张角手牌，不是"先弃掉再凭空造一张给他"）、`cost_candidates` 只认
【闪】/【闪电】。

**判定"现在能不能发动"的唯一入口**：

```python
def _huangtian_can_offer(game, owner, actor):
    """这名角色现在能不能把一张【闪】/【闪电】交给持有【黄天】的张角。"""
    if game.current_turn_player is not actor or game.phase != "play":
        return False, "只能在你的出牌阶段发动"
    if actor.kingdom != "qun":            return False, "只有群势力角色可以发动"
    if actor.skill_state.get("huangtian", "used", 0):
        return False, "本出牌阶段已经交过一次"
    if not _huangtian_cards(actor):       return False, "没有可以交出的【闪】或【闪电】"
    return True, ""
```

技能栏、`AvailableActions`、AI、远程下发四条入口**全部**读这一份
（`SkillManager.granted_state` / `granted_offers`），所以它们看到的永远是同一个
答案。它**不再监听任何事件**——没有 PHASE_START 绑定，也就没有"自动弹窗"。
玩家体验是：能交的时候技能栏里【黄天】亮起，由玩家自己点。

## 4. 阶段中获得牌后为什么能发动

判据里没有任何缓存：`_huangtian_cards(actor)` 每次查询都按**当前手牌**算，
而 `granted_offers` 是每次查询都重新扫描的技能入口。技能栏每帧 `sync`、
`AvailableActions` 每次调用都会重新问一遍。所以在同一个出牌阶段里：

* 拿到牌 → 下一次查询立刻出现黄天（阶段没有切换，`current_turn_player` 与
  `phase` 都没变）
* 牌被打出/弃掉 → 下次查询立刻消失（如实显示"没有可以交出的牌"）

探针 `probe_huangtian.py` 的"阶段中途获得【闪】/【闪电】"两条就是钉这一点
（断言里同时检查了 `phase == "play"` 且仍是同一名角色，防止"其实是切了阶段"）。

## 5. 取消为什么不消耗次数

次数标记写在**发动者**（交牌的那个人）身上，且只在结算函数里写：

```python
def _activate_huangtian(game, player, target=None, cards=None):
    if target is None or not (cards or ()): return False
    player.skill_state.set("huangtian", "used", 1, ResetScope.PHASE)
```

`_activate_huangtian` 只有在"校验全部通过 → 费用已经支付"之后才会被调用
（见 `resolve_activation`）。所以以下情况都走不到写标记那一行：

* 没打开技能 / 打开后取消（`cancel_skill_input` 只清界面状态）
* 选了牌、选了目标之后取消
* 提交的牌不在候选里、或牌已经不在可支付区域（校验失败整体返回 False）
* 目标（张角）在这期间阵亡（`granted_state` 复核失败）

`ResetScope.PHASE` 由 `TurnFlow._reset_phase_scopes()` 在每个阶段边界清理
（清理发生在事件之前），所以"下一次出牌阶段重新可发动"是项目既有的
scope 机制保证的，不是靠一个永久 bool。

## 6. 【制衡】根因

`variable_cost` 的候选与支付**三处硬编码 `player.hand`**：

| 位置 | 旧代码 |
| --- | --- |
| `activation.cost_candidates` | `candidates = list(getattr(player, "hand", ()))` |
| `activation.resolve_activation` | 逐张 `any(item is card for item in player.hand)`，支付统一 `MoveCardAtom(source=player.hand)` |
| `_can_zhiheng` / `_can_jilue` | `if not player.hand: return False, "没有可以弃置的手牌"` |

加上描述层的 `available_actions._active_skill` 里候选也写死 `actor.hand`，
四处合起来使【制衡】在"手牌 0 + 装备区有牌"时直接判为不可发动。

顺带修掉的一个真实交互缺陷：真人点手牌选费用牌的分支条件是
`state["cost_cards"]`，而可变费用的 `cost_cards` 恰好是 0 —— 真人打开
【制衡】后**点手牌没有任何反应**（这条走的是 `ui/interaction.py` 的点击路由）。
本轮一并修好，并让装备槽也能点。

## 7. `variable_cost` 如何扩展区域

费用来源变成**显式声明**，不再是隐式假设：

```python
class CostZone(str, Enum):
    HAND = "hand"
    EQUIPMENT = "equipment"

@dataclass(frozen=True)
class ActiveSkillSpec:
    ...
    allowed_zones: Tuple[str, ...] = (CostZone.HAND,)   # 默认只有手牌

ZHI_HENG_SPEC = ActiveSkillSpec(
    variable_cost=True,
    cost_prompt="【制衡】：请选择要弃置的牌",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)
```

`activation.cost_candidates` 按 `allowed_zones` 收集（手牌在前、装备在后），
`activation_inputs` 把它连同 `allowed_zones` 一起返回，于是**候选、界面高亮、
引擎校验、远程下发读的是同一份列表**。

支付改成"先全部定位、再全部支付"：

```python
placements = []
for card in payable:
    placement = cost_placement(player, card, zones)   # (区域, 槽位) 或 None
    if placement is None:
        return False, "选择的牌已经不在可以支付的区域"
    placements.append(placement)
...
for card, (zone, slot) in zip(payable, placements):
    if zone is CostZone.EQUIPMENT:
        engine.context.apply(UnequipAtom(player, slot, destination))
    else:
        engine.context.apply(MoveCardAtom(card, source=player.hand,
                                          destination=destination))
    paid.append(card)
```

一张不合法就**整体不执行**——不会出现"弃了一部分才发现剩下的不合法"。
交给技能的是 `paid`（**实际支付成功**的牌），所以摸牌数按真实支付量算。

## 8. 其他技能为什么仍然只限手牌

因为默认值就是 `(CostZone.HAND,)`，而且没有任何一处按技能名分支：

* 没声明 `allowed_zones` 的技能，候选仍然是"全部手牌"（【举荐】"弃置至多
  三张"、【眩惑】红桃手牌、【明策】装备或【杀】…）
* 装备区的牌**只有**声明了 `CostZone.EQUIPMENT` 才进候选；没声明的技能
  即使玩家从别的入口（远程 / 脚本）提交一张装备牌，也会被
  `cost_placement` 以"不在可以支付的区域"拒绝

`probe_zhiheng.py` 里"只有手牌"那条断言锁的就是这一点：老行为一个字节都没变。

## 9. 装备牌如何经过标准移动原子被弃置

装备区的费用牌统一走 `UnequipAtom(player, slot, destination)`——它是本项目
装备离开装备区的**唯一出口**（换装、拆、顺、死亡清理都走它）。它按顺序做三件
事：把牌从槽里取下 → 放进目标区域（这里是弃牌堆）→ 发 `EQUIPMENT_LOST`
事件并 `sync_equipment_skills`（装备赋予的技能随之解绑）。

所以没有被"旁路"的规则：

* **装备技能**（丈八蛇矛这类 `equipment.*` 技能）随装备卸载
* **装备修正**（诸葛连弩的无限出杀、攻击范围）下次查询立即失效
* **区域归属 / 卡牌所有权**：牌在弃牌堆里，不在任何一个装备槽
* **移动原因**：`UnequipAtom` 的 payload 带上 `slot` 与 `destination`

探针的验收点：弃【诸葛连弩】后 `game.can_use_unlimited_sha(player)` 从 True
变 False；弃【丈八蛇矛】后 `skills.has(player, "equipment.zhangba")` 变 False。

## 10. 【极略·制衡】如何同步

它和孙权【制衡】共用**同一条费用规则**，只是各有一份 spec 常量（两边都声明
`allowed_zones=(HAND, EQUIPMENT)`）：

```python
JILUE_ZHI_HENG_SPEC = ActiveSkillSpec(
    variable_cost=True,
    cost_prompt="【极略·制衡】：请选择要弃置的牌",
    allowed_zones=(CostZone.HAND, CostZone.EQUIPMENT),
)
```

`_can_jilue` 的"有没有牌可弃"也改成调同一份候选查询，所以"只有装备也能发动"
对极略版同时成立。代价（一枚「忍」标记）仍然只在 `_activate_jilue` 里扣，
而它同样只在支付成功之后被调用——取消、校验失败都不扣标记。

## 11. 原 188 条回归结果

15 个原脚本原样保留，**177/177 通过**（188 条里属于【黄天】的 11 条在下一节
说明）。新增两个脚本后合计 **252 条断言全部通过**：

| 脚本 | 条数 | 结果 |
| --- | --- | --- |
| probe_beige | 8 | 通过 |
| probe_fankui_liuli | 17 | 通过 |
| probe_gongxin | 9 | 通过 |
| probe_guixin | 15 | 通过 |
| probe_guzheng | 10 | 通过 |
| probe_jilue_lianpo | 14 | 通过 |
| probe_jixi | 10 | 通过 |
| probe_luanwu | 10 | 通过 |
| probe_optional_skills | 21 | 通过 |
| probe_prepare_skills | 14 | 通过 |
| probe_shelie | 10 | 通过 |
| probe_shenfen | 10 | 通过 |
| probe_wuhun | 5 | 通过 |
| probe_wushen | 13 | 通过 |
| probe_xinzhan | 11 | 通过 |
| **probe_huangtian（重写）** | 34 | 通过 |
| **probe_zhiheng（新增）** | 41 | 通过 |

**关于 probe_huangtian 的 11 条旧断言**：其中 9 条（交牌者本人收到窗口、
候选只有【闪】/【闪电】、放弃后什么都不发生、张角拿不到牌、非群势力不触发、
黄天不是张角的主动技…）的语义被完整保留，只是"窗口"从自动弹窗换成了技能入口；
另外 2 条断言的恰恰是**被本轮判定为错误的行为**——"群将的出牌阶段开始时收到
黄天窗口"与"交牌场景拿到窗口"。它们在正确规则下必然为假（阶段开始时不再自动
弹窗），所以按复核报告的结论改写成新语义的断言（阶段开始时**可发动**但不弹窗、
技能栏出现可点状态）。这不是为了让测试通过而放宽断言，旧断言锁定的正是本次
要修的错误。

其他工具：

| 检查 | 结果 |
| --- | --- |
| `python -m compileall src/` | 退出码 0 |
| `tools/multiplayer_smoke.py` | 4 局全部 `over=True`，无 stuck / error |
| `tools/lan_smoke.py` | 44/44 通过 |
| `tools/input_priority_audit.py` | 45/45 通过（见下） |
| `tools/soak_play.py 40 1` | 37 finished / 2 normal_wait / 1 frame_cap，`crashed=0`、`blocked=0` |

**批量试玩（40 局，同起始种子，与改动前的基线对比）**：

| 指标 | 改动前基线 | 本轮改动后 |
| --- | --- | --- |
| 完成 | 38 | 37 |
| 等待超时（在等演出 / 别人） | 1 | 2 |
| 跑满帧数上限 | 1 | 1 |
| 崩溃 / 被闸门拦下的点击 | 0 / 0 | 0 / 0 |
| 点击被收下但没效果 | 11 | 6 |
| 总帧数 | 134757 | 139619 |

两个"等待超时"里，seed 10 与基线**逐帧一致**（都是 3493 帧、"演出积压=16"），
seed 31 的帧数上限也与基线一致——都是既有问题。唯一多出来的一局（seed 28）
单跑三次里两次正常完成（4557 / 2402 帧）、一次复现同样形状的等待，属于
"同一局不可复现"的波动：本项目的 AI 随机源在非 1v1 模式下没有播种
（既有事实），所以逐局的帧数本来就不逐位可复现。没有任何一局崩溃，也没有
任何一次点击被闸门拦下。

`input_priority_audit.py` 的 5.1（"别人弃置的梅花牌被【落英】获得"）在本轮开始时
是失败的，但它与本轮改动**无关**：【落英】在未提交的上一批工作里已经改成
"可选触发"（`optional_trigger`），会在真人座位上挂一条 CONFIRM 请求等人点，
而工具没有回答它——所以判据停在"没发动"。实测：回答"是"之后落英立刻获得那张
梅花牌（规则本身是对的）。工具已补上"替真人点掉这个窗口"这一步；5.3 也从
"或 在弃牌堆"这种弱断言变成真正验到落英把判定牌拿到了手里。

## 12. 新增探针结果

**`probe_huangtian.py`（34 条，重写）**——覆盖本轮要求的 11 个定点：

1. PLAY 开始有【闪】→ 可发动，并真的交到张角手里
2. PLAY 开始无牌 → 不可发动（并且报的是"没有可以交出的牌"）
3. PLAY 中途获得【闪】→ 变为可发动（断言里同时确认阶段没切换）
4. PLAY 中途获得【闪电】→ 变为可发动，并能真的交出去
5. 第一次取消（选牌前 / 选完牌与目标后）→ 牌没动、无 used、稍后仍可发动
6. 成功交牌一次 → 本阶段不可再次发动
7. 下一次 PLAY → 重新可发动（阶段 scope 已清）
8. 非群势力（魏将）→ 不可发动，牌还在自己手里
9. 非自己 PLAY（张角回合 / 别人的回合）→ 不可发动
10. 成功后牌进入张角手牌、交出的那张离开交牌者手牌
11. 取消 / 放弃后牌不移动、张角拿不到任何牌

外加 6 条接口与端到端：技能栏里【黄天】可发动且按钮能按、阶段开始时**没有**
它自己的弹窗、AI 从同一个入口发动（忠臣吕布把【闪】交给主公张角）、输入契约
字段（1 目标 / 1 费用 / transfer）、**房主下发给远程客户端的载荷**里有【黄天】
且目标 / 候选正确（走 `RemoteHumanController._activatable_skills` 这条真实
编码路径）。

**`probe_zhiheng.py`（41 条，新增）**——覆盖本轮要求的 14 个定点：

12–14. 只有手牌 / 只有装备 / 手牌 + 装备混选，三种都能发动
15–16. 弃 1 张装备 → 摸 1；弃 1 手牌 + 2 装备 → 摸 3
17. 装备按卸装原子离开：槽空、进弃牌堆、【诸葛连弩】效果立即消失、
    【丈八蛇矛】的装备技能解绑
18. 取消（选了 1 手牌 + 1 装备之后）→ 手牌不动、装备不动、不摸牌、不消耗次数
19. 校验失败（提交时那张装备已离开装备区）→ 整次发动不成立，同样什么都不变
20. 成功发动一次 → 本阶段不能再发动
21–25. 极略·制衡：只有装备也能发动、混选正常、成功才扣「忍」、取消不扣、
    校验失败不扣
26. 真人点击路由：可变费用时点手牌与点装备槽都能选上费用（真实 `Renderer` +
    `handle_game_click`）
27. 下发给远程客户端的候选里含装备区的牌（走房主真实的
    `RemoteHumanController._activatable_skills` 编码路径）

## 13. 两个真实 Game 原始复现

**A. 黄天（张角主公 + 吕布群将，中途获得【闪】）**：已修复。

```
出牌阶段开始时手里只有【杀】  →【黄天】不可发动（reason=没有可以交出的【闪】或【闪电】）
同一出牌阶段中途获得【闪】    →【黄天】可发动（phase 仍是 play、仍是同一名角色）
点【黄天】→ 选那张【闪】→ 选张角 → 确认
                              → 张角手牌 = ['SHAN']，吕布少一张，used=1，本阶段关闭
```

**B. 制衡（孙权手牌 0 + 装备【诸葛连弩】）**：已修复。

```
can_activate(sunquan, "zhiheng") == (True, "")        # 旧行为：False, "没有可以弃置的手牌"
可用动作候选 = [("诸葛连弩", zone=equipment, slot=weapon)]
选它 → 确认 → 装备槽空、牌进弃牌堆、摸 1 张、can_use_unlimited_sha 变 False
```

两条都在 `probe_huangtian.py` / `probe_zhiheng.py` 里跑真实 `Game`（不 mock 规则）。

## 14. 修改文件

| 文件 | 改了什么 |
| --- | --- |
| `src/game/skills/definitions.py` | 新增 `CostZone`、`GrantedSpec`；`ActiveSkillSpec.allowed_zones`；`SkillDef.grant` / `is_granted` |
| `src/game/skills/registry.py` | 授予型不进"拥有者可发动"表；新增 `granted_state` / `granted_offers` / `granted_owners` |
| `src/game/skills/activation.py` | 费用区域化（`allowed_zones` / `cost_candidates` / `cost_placement`）；先定位再支付；支付走 `UnequipAtom`；技能拿到实际支付的牌；授予型发动路径 |
| `src/game/available_actions.py` | `active_skills` 纳入授予型（`_granted_skill`）；费用候选改读共同查询（含 zone/slot） |
| `src/game/skills/expansions/wind.py` | 【黄天】从 PHASE_START 触发技重写为授予型主动技（删除 `HuangtianFlow`） |
| `src/game/skills/standard/wu2.py` | 【制衡】`ZHI_HENG_SPEC`（手牌 + 装备区）；`_can_zhiheng` 用同一份候选查询 |
| `src/game/skills/expansions/god.py` | 【极略·制衡】`JILUE_ZHI_HENG_SPEC`；`_can_jilue` 同步 |
| `src/game/core.py` | 真人技能输入：授予型入口、费用上限按候选数、提示文案 |
| `src/game/controllers/ai.py` | AI 授予型入口（`_granted_skill_target`）；删除旧的黄天选牌窗口分支 |
| `src/ui/skill_bar.py` | 技能栏显示"现在由我发动的"授予型技能 |
| `src/ui/interaction.py` | 点击路由：可变费用时点手牌 / 点装备槽都能选费用 |
| `src/ui/remote_control.py` | 客户端允许选中房主下发的装备候选（不再限手牌） |
| `src/ui/view_adapter.py` | 客户端 `granted_offers`、自己的牌含装备、费用上限按候选数 |
| `tools/input_priority_audit.py` | 落英可选窗口的回答（工具跟上"可选触发"架构） |

探针（`.cache/` 不进版本库）：`probe_huangtian.py` 重写、`probe_zhiheng.py` 新增。

## 15. 其他规则缺口（本轮未改，如实记录）

1. **【举荐】的官方文本同样写"牌"**——"出牌阶段限一次，你可以弃置至多三张
   牌，然后令一名其他角色摸等量的牌"。按本轮"不扩大范围"的要求，它的
   `allowed_zones` 保持默认（只有手牌）。**如果要与制衡口径一致，它也应该
   声明手牌 + 装备区**：现在改是加一个 `allowed_zones=(HAND, EQUIPMENT)` 的事，
   但那是另一条武将的规则判定，留给下一次确认。
2. **装备牌因弃置进入弃牌堆时不发 `CARD_DISCARDED` 事件**。`UnequipAtom` 把牌
   放进 destination 后直接 append，不经过 `MoveCardAtom`，所以【落英】这类
   "因弃置进入弃牌堆"的技能看不到被弃掉的梅花**装备**（本轮实测：制衡弃掉
   梅花【丈八蛇矛】，曹植的【落英】没有拿到）。这是全项目统一的既有口径
   （换装、拆、顺、死亡清理都一样），不是本轮引入；要修就得在 `UnequipAtom`
   里补一条"目的地是弃牌堆"的通知，并评估它对其它技能链的连带影响。
3. **黄天只有一名拥有者时的目标点击**：目标就是技能拥有者，界面会要求玩家
   点一次张角（虽然通常只有一个候选）。这是刻意的——多张角（非标准配置）
   时"交给谁"是个真实的选择，用既有的选目标交互表达比另造一套规则更省。

---

## Git

本轮改动已**本地提交**（提交信息：`fix: 修正黄天发动窗口与制衡费用区域`），
**没有 push**：远端 `origin/main` 已有仓库里没有的提交，本地提交停在本地。

提交内容 = 本轮两项修复 + 工作区此前未提交的技能审计工作（两者在
`standard/wu2.py`、`expansions/god.py` 这类文件里已经混在一起，只提交子集会让
HEAD 出现"import 了未提交符号"的坏状态）。提交后 `git status` 干净。
