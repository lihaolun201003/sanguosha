# Phase 17 · 全场游戏系统真实性审计 v01

> 结论先说：**卡牌层面已完成逐张审计**（42 种全部覆盖，227 项断言全通过，
> 56 局长跑 0 卡死 0 异常）；**核心流程达到可长局游玩的状态**；
> **Local / LAN 的规则级能力已对照一致**。仍未覆盖的部分在文末逐条列出。

- 基线：`main @ 884c140`
- 脚手架与临时工具：`.cache/phase17/`（`.cache/` 已 ignore，不进版本库）
- tests/ 未恢复

---

## A. Card Matrix（自动枚举，不信任 README 数字）

`enumerate_cards.py` 从 **CardCatalog 牌堆工厂 + Deck 实际洗牌 + CardEffects 注册表**
三处交叉枚举：

| 类别 | 种数 | 张数 | 清单 |
| --- | --- | --- | --- |
| 基本牌 | **4** | 54 | 杀（含火杀 / 雷杀）、闪、桃、酒 |
| 普通锦囊 | **12** | 49 | 无中生有、过河拆桥、顺手牵羊、决斗、南蛮入侵、万箭齐发、桃园结义、五谷丰登、无懈可击、借刀杀人、火攻、铁索连环 |
| 延时锦囊 | **3** | 6 | 乐不思蜀、兵粮寸断、闪电 |
| 装备 | **23** | 26 | 见 B |
| **合计** | **42** | **129** | |

装备细分：武器 12（诸葛连弩 / 雌雄双股剑 / 寒冰剑 / 青釭剑 / 古锭刀 /
青龙偃月刀 / 丈八蛇矛 / 贯石斧 / 方天画戟 / 朱雀羽扇 / 麒麟弓 / 阴阳鱼枪）、
防具 4（八卦阵 / 仁王盾 / 藤甲 / 白银狮子）、+1 马 4、-1 马 3。

**逐张审计覆盖率 = 42/42（100%）**，无抽样。

| 类别 | Local | Remote | AI | Result |
| --- | --- | --- | --- | --- |
| 基本牌（4） | ✓ | ✓ | ✓ | PASS |
| 普通锦囊（12） | ✓ | ✓ | ✓ | FIXED ×1 / PASS |
| 延时锦囊（3） | ✓ | ✓ | ✓ | FIXED ×1 / PASS |
| 装备（23） | ✓ | ✓ | ✓ | FIXED ×1（通用机制）/ PASS |

---

## B. Equipment Matrix

| 装备 | 检查的规则 | Result | Fix |
| --- | --- | --- | --- |
| 全部 23 件 | 装备到正确槽位、武器攻击范围取自 catalog | PASS | — |
| 全部 23 件 | **被替换时旧装备进弃牌堆** | **FIXED** | `EquipCardAtom` 原先直接覆盖槽位，旧装备从所有区域消失（长局里牌堆会被慢慢掏空），现在走 `UnequipAtom` 离场并发「失去装备」事件 |
| 全部 23 件 | 换装后它赋予的技能失效 | PASS | — |
| 全部 23 件 | 角色死亡后装备离场 | PASS | — |
| 坐骑 | +1 马只影响别人算自己；-1 马只影响自己算别人；两马同时存在 | PASS | — |
| 距离 | `Seat Ring` 2/3/5/8 人：左右邻座恒为 1；对家 = 人数//2；死者退出环 | PASS | — |

> 距离有两个查询：`game.seats.distance`（纯座次步数）与
> `DistanceRule.distance`（含坐骑与技能，**规则层用的是这个**）。审计中已明确区分。

---

## C. Core Mechanics

| 机制 | 覆盖 | Result |
| --- | --- | --- |
| **Judge** | 乐不思蜀 / 兵粮寸断 / 闪电 / 八卦阵 / 鬼才 / 鬼道 / 天妒 / 洛神 / 屯田 / 刚烈 / 铁骑 | PASS（Phase 16 已修，本轮回归 25+17+30 全绿） |
| **Wuxie** | 不开空窗口 / 只放行无懈 / 一张抵消 / **套娃两层后原锦囊重新生效** / 死者不问 / AOE 只开一次 / 实体牌进弃牌堆 | **FIXED**：原先**每一张锦囊都被判成"被无懈抵消"**（回调类型被 `FlowResult` 覆盖），修 `on_settled` 后全部恢复 |
| **Dying** | 自救优先 / 多人求桃顺序 / 别人出桃救活 / 无人救则死亡 / 一张桃只回 1 点 | PASS |
| **Death** | 手牌 / 装备 / 判定区清空 → 弃牌堆；死者退出距离环 | PASS |
| **Identity** | 主公死亡→反贼胜；主公杀忠臣→弃光牌；杀反贼→摸三张（**任何身份都摸**）；内奸独存→内奸胜；阵亡揭示身份 | PASS |
| **Distance** | 座次环数学结果核对（2/3/5/8 人） | PASS |
| **TurnFlow** | 准备→判定→摸牌→出牌 顺序；阶段替代（突袭）后**不会**再正常摸两张；TURN scope 边界清理 | PASS |
| **Discard** | 手牌上限 = 当前体力；真人自己指定弃哪几张（保留他留的那张） | PASS |
| **Chain** | 铁索横置 / **再次使用解除** / 重铸入口 | PASS |
| **Damage** | 普通 / 火焰 / 雷电；酒 +1；属性伤害 | PASS |
| **Ownership** | 每类场景后扫全场：同一张实体牌不得同时属于两个区域；**牌数守恒**（没有凭空少牌） | PASS（正是它暴露了装备替换丢牌） |

---

## D. LAN parity

用真实 TCP（`tools/lan_view_harness`）对照"同一个人的游戏能力"：

| 对照项 | Result |
| --- | --- |
| 客户端能用的牌 = 房主算出的能用牌（**同一份判据**） | PASS |
| 每张牌的可用方式数量与房主查询一致 | PASS |
| 不可用的牌（满血的桃、不能主动用的闪）两边都不给 | PASS |
| 主动技候选（眩惑的红桃手牌）房主下发 = 本地查询 | PASS |
| **恶意提交**：不存在的 card_id / 不是自己的 card_id / 未声明的技能 / 错误目标 / 过期 request_id —— **全部被拒且状态未变** | PASS |
| AI 信息边界：只读手牌**数量**（公开）与**自己的**手牌内容 | PASS |

**仍需注意**：`needs_local_ui=True` 的技能有 3 个（见 §E）。

---

## E. Long-run simulation

| 模式 | 人数 | seed 数 | 局数 | 正常结束 | deadlock | exception | ownership 违规 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| FFA | 2 | 8 | 8 | 8 | 0 | 0 | 0 |
| FFA | 5 | 8 | 8 | 8 | 0 | 0 | 0 |
| FFA | 8 | 8 | 8 | 8 | 0 | 0 | 0 |
| Identity | 5 | 8 | 8 | 8 | 0 | 0 | 0 |
| Identity | 6 | 8 | 8 | 8 | 0 | 0 | 0 |
| Identity | 7 | 8 | 8 | 8 | 0 | 0 | 0 |
| Identity | 8 | 8 | 8 | 8 | 0 | 0 | 0 |
| **合计** | — | — | **56** | **56** | **0** | **0** | **0** |

watchdog（`long_run.py`）：连续 900 步状态完全不变即判 DEADLOCK，并保存
seed / 模式 / 人数 / phase / pending / flow 栈 / 手牌体力 / 日志尾部。本轮**未触发**。

**递归与 monkey patch 猎杀**：全仓库扫描
`X.method = ...` / `setattr` 形式的类方法替换 —— **零命中**
（Phase 16 修掉的 `EnyuanFlow.advance` 是唯一一个，已改为正常类方法）。

**资源观察**：长局期间 ActionQueue / 日志 / 弃牌堆都随对局结束归零，
未发现无界增长。

---

## F. Bug 分类统计（Phase 17 本轮）

| 类型 | 次数 | 具体 |
| --- | --- | --- |
| **错误卡牌去向 / 卡牌消失** | 2 | 装备替换时旧装备从所有区域消失（长局掏空牌堆）；View-As 延时锦囊用虚拟牌移动，进不了判定区 |
| **Flow 回调类型错误（整类卡牌失效）** | 1 | 无懈链回调被 `FlowResult` 覆盖 → 所有锦囊被判"被无懈抵消" |
| 错误目标 | 0 | — |
| 错误时机 | 0 | — |
| 错误响应顺序 | 0 | — |
| 真人被自动选牌 | 0（本轮） | Phase 16 已修 5 处；本轮扫描 4 处可疑点复核后全部合法 |
| 真人被自动选目标 | 0 | — |
| Local / Remote 不一致 | 0 | 对照矩阵全绿 |
| AI 作弊信息 | 0 | 只读公开数量与自己的手牌 |
| 卡死 / 递归 | 0 | 56 局长跑 + 静态猎杀均无 |
| 实体牌 ownership | 0 | 每类场景后扫描 |
| UI 穿透 / 动画逻辑不同步 | 0 | main.py 判定探针 24/24 |
| 多人 Seat 顺序 | 0 | 座次环数学核对 |
| 身份结算 | 0 | 6/6 |

---

## G. 本轮修复清单

| 文件 | 改动 |
| --- | --- |
| `src/game/flows/wuxie.py` | `on_settled` 显式声明对外的 bool（锦囊是否被抵消），修掉"所有锦囊都被判被抵消" |
| `src/game/skills/standard/wei.py` | `TuxiFlow.on_settled` 同样处理（对外是 `{"applied": bool}`） |
| `src/game/flows/use_card.py` | 新增 `material_cards`：这次使用真正动过的**实体牌**（View-As 时是素材） |
| `src/game/card_effects/tricks.py` | 延时锦囊按 `material_cards` 落判定区；素材不在处理区时按普通锦囊收尾 |
| `src/game/atoms_v2.py` | `EquipCardAtom` 换装时用 `UnequipAtom` 移出旧装备（进弃牌堆 + 发失去装备事件） |
| `src/game/ai.py` | legacy AI 装备路径改走 `EquipCardAtom` |

---

## H. 仍未完成 / 已知限制

1. **`needs_local_ui=True` 仍有 3 个技能**，它们的共同点是"**任意顺序**"这一
   交互能力（串行 PendingRequest 表达不了排序）：
   - 神司马懿【攻心】观看他人手牌并选一张红桃处理
   - 【心战】观看牌堆顶三张、任意数量红桃获得、其余任意顺序放回
   - 诸葛亮【观星】查看牌堆顶 N 张并以任意顺序放回

   这三者目前只能由坐在房主电脑前的真人发动；远程真人在出牌阶段不会拿到
   入口（房主会写明原因）。迁移需要先给"排序"设计一个通用 PendingRequest
   表达（例如多次"选一张放最上面"），本轮未做。

2. **Phase 16 遗留的 5 项**本轮处理进度：
   - ✅ 5/8 人桌面：本轮用 2/3/4/5/8 人构造了 Seat Ring、五谷、距离
   - ✅ LAN 全链路：本轮补了出牌阶段 / 技能候选 / 恶意提交的真实 TCP 对照
   - ✅ 真人选择权：本轮重扫，无新增
   - ⬜ **限定技 / 觉醒技 / 翻面 / 拼点仍无专项审计**（只有全量跑局覆盖）
   - ⬜ 不是每个技能都有正反场景（触发技 / 锁定技仍是"跑局 + 静态复核"）

3. **延时锦囊的 category**：数据层把乐不思蜀 / 兵粮寸断 / 闪电都标成
   `category="trick"`，与普通锦囊无法在数据上区分（判定区落位靠 effect 自己声明）。
   本轮未改（改动面涉及卡牌目录与所有按 category 分组的消费点），
   记录为**语义清晰度问题**，非行为缺陷。

4. **音频 / 视觉观感**：判定面板遮挡、动画节奏等仍需人眼确认（程序只能断言
   绘制顺序与交互门控）。

---

## 复现

```powershell
.venv\Scripts\python.exe .cache\phase17\enumerate_cards.py
.venv\Scripts\python.exe .cache\phase17\audit_basic_cards.py
.venv\Scripts\python.exe .cache\phase17\audit_tricks.py
.venv\Scripts\python.exe .cache\phase17\audit_delayed.py
.venv\Scripts\python.exe .cache\phase17\audit_equipment_distance.py
.venv\Scripts\python.exe .cache\phase17\audit_flow_and_lifecycle.py
.venv\Scripts\python.exe .cache\phase17\audit_identity.py
.venv\Scripts\python.exe .cache\phase17\audit_parity.py
.venv\Scripts\python.exe .cache\phase17\long_run.py --seeds 8
```

## 三个结论

- **当前全部卡牌规则是否完成审计：是**（42 种逐张，无抽样）
- **当前核心游戏流程是否达到可正常长局游玩的状态：是**（56 局 0 卡死 0 异常）
- **当前 Local / LAN 是否已经不存在已知规则级差异：是**（对照矩阵全绿；
  唯一的差距是 3 个"任意顺序"技能远程无入口，那是**功能未迁移**而非规则差异）

---

# Phase 17.1 · 借刀杀人完整交互修正

> 本节由用户独立代码复核后发起。它推翻的是 **Phase 17 的两条结论**：
> "42/42 卡牌规则完成审计"与"真人被自动选牌/选目标为 0"。原结论**过早**。

## 18.1 独立复核发现的缺陷

`JiedaoEffect.begin` 当时是这样写的：

```python
victim = flow.action.metadata.get("jiedao_victim")
if victim is None and candidates:
    victim = candidates[0]                     # ① 替使用者选第二目标
sha = next((card for card in wielder.hand if card.name == "SHA"), None)   # ② 只看实体杀
if victim is not None and sha is not None and DistanceRule.in_attack_range(...):
    flow.wait({"reason": "jiedao_sha"})        # ③ 假等待：根本不问持武器者
    flow.engine.submit(UseCardAction(wielder, sha, [victim], ...))
    ...
flow.context.apply(TransferEquipmentAtom(...)) # ④ 直接交武器，不问持武器者
```

| # | 缺陷 | 规则要求 |
| --- | --- | --- |
| ① | 第二目标默认取候选第一名 | "对其攻击范围内、**由你指定**的另一名角色" |
| ② | 只找实体【杀】（`name == "SHA"`）| 火杀 / 雷杀 / 武圣 / 龙胆等转化都算 |
| ③ | `flow.wait({...})` 是**假等待**（不是 PendingRequest）| 持武器者必须自己决定出不出【杀】 |
| ④ | 候选只筛"存活且不等于双方"，合法性留到结算时用第一名去试 | 第一个候选不合法就错误地走到"交武器" |

## 18.2 通用机制：用牌的附加输入

借刀的第二目标**不是这张牌的牌面目标**（不能改成 `max_targets=2`），
但它是"使用这张牌需要的玩家决定"。为此新增**可复用的动作输入契约**：

```python
@dataclass(frozen=True)
class AuxiliaryInput:
    key: str            # metadata 里的键名
    prompt: str
    candidates: Callable  # callable(game, actor, targets) -> list[player]
```

* `CardEffect.required_inputs(game, actor, targets, card)` 声明这张牌还需要
  玩家补全什么（默认返回空）；
* 引擎在**目标选好之后、这次使用提交之前**逐个开窗口收集，全部齐了才提交
  ——所以取消发生在提交之前，一张牌都不会动；
* 本地 UI 走 `start_target_selection`（与普通选目标同一套交互），远程由房主
  下发同名决策，AI 用**同一份候选**自动选第一个。

## 18.3 借刀的完整流程（三处玩家决定权）

```text
A 选【借刀杀人】→ 选持武器者 B
  → 收集附加输入：A 从"B 真的能杀到的其他角色"里**自己指定**被杀目标 D
       （候选按完整规则筛：存活 / 不是 B / 不是 A / 在 B 的攻击范围内 /
         通过真实的【杀】can_use 判定——含距离、坐骑、武器、技能限制）
  → 提交使用 → 正常【无懈可击】窗口（被抵消则后面全部不发生）
  → B 收到抉择：对 D 使用【杀】 / 不使用而交出武器
       ├─ 使用 → 有多种【杀】方式时由 B 自己选（实体杀 / 火杀 / 雷杀 /
       │        武圣 / 龙胆…，走 CardActionDiscovery，不写 name == "SHA"）
       │        → 正常 UseCardAction → 正常闪响应 / 伤害 / Dying / Death
       └─ 不使用（或没有合法【杀】）→ 武器经 UnequipAtom 进 A 的手牌
```

**引擎兜底**：metadata 里连 `jiedao_victim` 这个键都没有（绕过界面的提交）
→ 这次使用判为无效，**不静默替使用者挑一个**。

## 18.4 验证（26 项断言，全通过）

| # | 场景 | 结果 |
| --- | --- | --- |
| 1 | A 必须能自己选 C 或 D（候选里两人都在）| PASS |
| 2 | 超攻击范围的角色不进候选（第一个不合法不影响后面的）| PASS |
| 3 | B 没有合法【杀】→ 武器交给 A | PASS |
| 4 | B 有杀但不使用 → 武器交给 A，杀仍在手里 | PASS |
| 5 | B 使用 → 正常【杀】响应窗口；目标出闪则不受伤害 | PASS |
| 6 | 两张不同实体杀 → 由 B 自己选（开素材窗口，选中的那张进弃牌堆）| PASS |
| 7 | 只有【武圣】能转化出杀 → 正常用出并结算 | PASS |
| 8 | 只有【龙胆】能转化出杀 → 正常用出并结算 | PASS |
| 9 | 借刀被无懈 → B 不收到任何"是否出杀"请求，武器仍在 | PASS |
| 10/11 | 出杀命中 → 正常伤害；结算后武器仍在 B 装备区 | PASS |
| 12 | 出杀杀死目标 → Dying / Death 正常走完 | PASS |
| 13 | 没有合法第二目标时不弹假窗口 | PASS |
| 14 | 远程持武器者收到「使用【杀】/ 交出武器」决策 | PASS |
| 16 | 绕过界面、未指定第二目标的使用被拒且不消耗牌 | PASS |
| — | 全程 ownership 无违规 | PASS |

## 18.5 其余 11 张普通锦囊的重扫

只查"规则要求真人选择"的第二层选择：

| 锦囊 | 第二层选择 | 判断 |
| --- | --- | --- |
| 火攻 | 目标展示手牌（`target=target`）、使用者选同花色手牌弃置（`target=actor`）| 都是真实 PendingRequest ✓ |
| 五谷丰登 | 依次由每个角色自己选公共牌 | ✓ |
| 过河拆桥 / 顺手牵羊 | 使用者选目标的牌 | ✓ |
| 铁索连环 | 重铸 / 横置由使用者决定 | ✓ |
| 无中生有 / 南蛮 / 万箭 / 桃园 / 决斗 / 无懈 | 没有第二层玩家决定 | — |
| **借刀杀人** | **两处都被自动替代** | **FIXED** |

代码里其余的 `[0]` / `next(...)` 逐条复核后都是"玩家已经选过的那一项"
（`flow.targets[0]` = 牌的目标、`resolution.cards[0]` = 玩家的回答）或
"只有一个合法选项时直接使用"，不是替玩家决定。

## 18.6 Phase 17 结论的修正

| Phase 17 原结论 | 修正后 |
| --- | --- |
| 42/42 卡牌规则完成审计 | **修复并重新验证后重新成立**（42 种逐张；借刀从"看起来通过"改为有 26 项断言的专项覆盖）|
| 真人被自动选牌 / 选目标为 0 | **修正为 1 处**（借刀的第二目标与"用哪一种杀"），已修复 |
| "Local / LAN 已不存在已知规则级差异" | **改为更准确的表述**：核心卡牌 / 规则路径 Local 与 LAN 一致（对照矩阵全绿）；但仍有两个缺口——**3 个 `needs_local_ui` 技能尚未迁移**（攻心 / 心战 / 观星），以及**借刀的完整远程流程（3 人局）本轮未跑通**（现有 LAN 脚手架只有两个座位，2 人局下"没有合法第二目标"是正确行为，完整链路未验证）。因此**完整玩家能力尚未 100% 对齐**。|

---

# Phase 17.2 · Local Presentation Settings & Skill Tooltip

> 本轮只动表现层：**表现速度本地化**与**技能描述只跟 hover**。
> 没有改任何武将规则、卡牌规则、JudgeGate 逻辑或 AvailableActions 语义。

## 19.1 表现速度放在哪里

审计结论：项目**已经有**一套节奏机制，本轮不重建第二套。

| 位置 | 现状 |
| --- | --- |
| `Game.SPEED_STEPS` | 6 档：0.4 / 0.55 / 0.75 / 1.0 / 1.5 / 2.0 |
| `Game.speed` | 座主（本机权威）的表现倍率，只喂给 `Game.update` 里的 `ActionQueue` |
| `RemoteGameView.speed_state`（`ViewSpeed`）| 联网客户端**自己的**倍率，状态在本地适配层 |
| `ui/speed.py` 的 `SpeedControl` | 角落里的 `− 值 ＋` 部件 |
| `Effects` / `FXTiming` | 各段动画的基础时长；队列按 `dt * speed` 推进 |

本轮只补了三处缺口：

1. **局内入口**：`SpeedControl` 以前只在**启动菜单**里 hit/draw，牌桌上虽然创建了
   却从没画出来也没命中过。现在 `Renderer.draw` 画它、`Renderer.hit_action`
   命中它（`metrics.speed_control` 的位置是现成的左上角，牌桌布局没动）。
2. **档位文案统一**：档位名字（很慢/慢/稍慢/正常/快/很快）原先只写在客户端那张表里，
   座主显示的是 `0.75×`。现在两边读同一份 `SpeedControl.LABELS`。
3. **客户端速度查询**：`RemoteGameView` 补了 `speed` 属性（原来只有
   `speed_index/speed_label/slower/faster`，`SpeedControl.draw` 读不到值）。

## 19.2 为什么不进入网络同步

**速度是这台机器的表现设置，不是游戏状态。** 落点刻意分开：

```text
座主：Game.speed            ← 只喂 ActionQueue 的 dt 缩放
游客：RemoteGameView.speed  ← 只喂本地表现队列
```

两者之间没有任何通道：

* 不在 `Game` 的权威状态里（客户端的 `Game` 根本不存在）；
* 不在 `ClientGameView` / `view snapshot` 里；
* 不进 `revision`；
* 不进 `DecisionRequest`；
* `src/network/` 与 `src/game/view/` 里**没有任何** speed 相关字段（已 grep 确认）。

客户端点速度控件走的是 `RemoteHumanController.run_action` 的
`slower/faster` 分支——它**只改本地视图**并直接返回，不构造任何决策、不发消息。

## 19.3 Host / Client 独立验证（13 项断言全通过）

| 场景 | 结果 |
| --- | --- |
| 初始：座主 0.75 / 客户端 1.00（两个独立对象）| PASS |
| A：座主调慢 → 只改自己；客户端**没有**被带着变 | PASS |
| B：座主调快 5 档 → 客户端仍是自己那个值 | PASS |
| C：客户端调慢 → 只改自己；座主**没有**被带着变 | PASS |
| 网络载荷里完全没有 speed / pace / animation_speed 字段 | PASS |
| D：两台机器速度差到最大（0.4 vs 2.0）时，客户端照常拿到出牌决策、房主权威状态与 revision 不受影响 | PASS |
| 规则侧：最慢档与最快档结算同一个场景，结果完全一致（目标掉血都是 1 点）| PASS |

## 19.4 Judge presentation 仍然安全

速度只缩放**动画时长**，不参与任何门控判据：

* `JudgeGate` 的三个状态由 `JudgeFlow` 的注册表与 `PendingRequest` 决定，
  与 `speed` 无关；
* `JudgePanel` 的各段时长按 `timing()` 播放，快慢只影响"演多久"；
* 规则上的 `JudgeFlow` 何时完成由引擎决定，**不会因为另一台机器的速度设置改变**。
  两边动画不同步结束是允许的——各自在自己的 presentation 未结束前都不会误操作
  （真人输入门控走 `JudgeGate`，与速度无关）。

## 19.5 技能描述：hover / selected 彻底分离

**问题**：`SkillBar.hit()` 在点击"不可发动 / 非主动"技能时会**切换**
`info_skill_id`，而 `tooltip()` 里有一条 `if self.info_skill_id: return self.info_text(game)`
——于是点击之后鼠标移开，技能描述仍然常驻。

**改法**：把两个概念彻底拆开：

| 状态 | 职责 | 谁决定 |
| --- | --- | --- |
| `selected_skill_id` | **游戏交互状态**（View-As / 主动技选中）| 点击 |
| Tooltip 内容 | **技能介绍** | **只有鼠标 hover** |

* `tooltip(game, mouse_pos)` 现在只有一条路径：鼠标停在哪个技能按钮上就返回
  哪个技能的说明；不在按钮上（手牌 / 座位 / 技能栏空白 / 头像）一律返回 `None`；
* `hit()` 对可发动的技能仍然进入交互并**记下 selected**（按钮的选中视觉不变），
  对锁定技 / 触发技则**什么都不做**（说明已由 hover 给出）；
* `info_text()` 整块删除——它的职责就是那个被修掉的常驻说明。

## 19.6 实际 main.py 手玩结果（18 项断言全通过）

探针挂在**真实主循环**上（`SGS_RUNTIME_SCRIPT=ui_probe`），screen / game /
renderer 都是玩家正常启动时那一个：

| # | 场景 | 结果 |
| --- | --- | --- |
| — | 局内存在速度控件；点「＋」/「−」被识别；点一下立刻改变；档位有中文名 | PASS |
| 1 | hover【武圣】→ 显示技能描述 | PASS |
| 2 | 鼠标移到手牌 → 描述立刻消失 | PASS |
| 3 | 点击【武圣】→ 进入选中状态，View-As 会话建立 | PASS |
| 4 | 鼠标移到手牌 → **描述消失**，但【武圣】仍是选中状态、View-As 仍有效 | PASS |
| 5 | 点合法素材（红桃牌）→ View-As 正常推进 | PASS |
| 6 | 取消 → 没有残留的选中 / 交互状态 | PASS |
| 7 | hover 锁定技【咆哮】→ 显示描述 | PASS |
| 8 | 移开 → 立刻消失 | PASS |
| 9 | 两个技能来回 hover → 说明跟着鼠标走，不残留上一个 | PASS |
| 10 | 技能栏空白处 / 手牌上 → 都不显示任何描述 | PASS |

**远程**：技能描述是纯本地 UI——网络载荷里没有 hover / tooltip 字段
（已 grep 确认），客户端有自己的技能栏，鼠标不在技能上时同样没有描述。

## 19.7 本轮修改清单

| 文件 | 改动 |
| --- | --- |
| `src/ui/skill_bar.py` | `tooltip` 改为 hover-only；`info_skill_id` → `selected_skill_id`（只管视觉）；删除 `info_text` |
| `src/ui/speed.py` | 补档位中文表 `LABELS` / `label_for`；控件标题改"动画速度"；文档写明它是本地 UI 设置 |
| `src/renderer.py` | 牌桌上绘制并命中 `speed_control`（局内入口）|
| `src/ui/remote_control.py` | `slower/faster` 只改本地视图，不发任何决策 |
| `src/ui/view_adapter.py` | `RemoteGameView` 补 `speed` 属性 |
