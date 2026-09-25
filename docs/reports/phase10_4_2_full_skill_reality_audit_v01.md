# Phase 10.4.2：全武将技能真实玩法审计 + 批量修复

- 报告日期：2026-09-20
- 基线：Phase 10.5（934 tests / compileall / smoke 全通过）
- 工作区：`C:\Users\lihao\Desktop\sanguosha\sanguosha -zcode`
- 审计立场：**只以"真实 Gameplay Flow 里规则是否真的改变了结果"为准**，
  不采信"注册表里有 ID / UI 有名字 / 测试是绿的"。

---

## 1. 当前武将总数

从 `Game.generals.list_generals()` 真实读取：**25**。

## 2. 当前技能总数

`gen.skill_ids` 展开后共 **40 条技能绑定**，去重后仍是 **40 个唯一 skill id**
（`SkillRegistry.ids()` 长度 40）。没有任何武将绑定重复 id。

## 3. 完整技能矩阵

审计列的含义：

- **注册**：`SkillRegistry` 里能取到定义
- **Engine 接入**：它的效果真的挂在引擎的某条计算 / 事件 / 流程上
- **真人 / AI**：人类路径与 AI 路径都走同一个入口（不是两套逻辑）
- **多人**：不依赖 `game.enemy`、不写死 seat、距离只问座次环
- **Card Movement**：产生的牌移动全部经 `MoveCardAtom` / `UnequipAtom` 等原子

| 武将 | 技能 | 类型 | 注册 | Engine 接入 | 真人 | AI | 多人 | Card Movement | 状态 | 问题 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 张飞 | 咆哮 | locked | ✓ | SLASH_QUOTA → `slash_quota()` | ✓ | ✓ | ✓ | — | COMPLETE | 端到端：连续两次出杀成功 |
| 黄月英 | 集智 | passive | ✓ | CARD_USED | ✓ | ✓ | ✓ | DrawCardsAtom | COMPLETE | 端到端：使用锦囊手牌净增 2 |
| 黄月英 | 奇才 | locked | ✓ | TRICK_RANGE_IGNORE | ✓ | ✓ | ✓ | — | COMPLETE | 数值查询为真，对照为假 |
| 夏侯惇 | 刚烈 | passive | ✓ | DAMAGE_SETTLED + JudgeFlow | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：非红桃判定→来源掉血 |
| 曹操 | 奸雄 | passive | ✓ | DAMAGE_SETTLED | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：拿到造成伤害的牌 |
| 司马懿 | 反馈 | passive | ✓ | DAMAGE_SETTLED | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：从来源获得一张牌 |
| 司马懿 | 鬼才 | passive | ✓ | judge_replacement | ✓ | ✓ | ✓ | ✓ | COMPLETE | Phase 10.5 已端到端验证改判 |
| 郭嘉 | 天妒 | passive | ✓ | JUDGE_FINISHED | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：获得判定牌（进手牌） |
| 郭嘉 | 遗计 | passive | ✓ | DAMAGE_SETTLED | ✓ | ✓ | ✓ | DrawCardsAtom | COMPLETE | 端到端：受伤后摸两张 |
| 张辽 | 突袭 | passive | ✓ | phase_replacement(DRAW) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 摸牌阶段真实给出替代 |
| 关羽 | 武圣 | view_as | ✓ | conversions(红牌→杀) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 普通点击不转换；点技能才进模式 |
| 赵云 | 龙胆 | view_as | ✓ | conversions(杀↔闪) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：响应把【杀】当【闪】 |
| 周瑜 | 英姿 | locked | ✓ | DRAW_COUNT | ✓ | ✓ | ✓ | DrawCardsAtom | COMPLETE | 摸牌数 3，对照 2 |
| 周瑜 | 反间 | active | ✓ | `_can_fanjian`/`_activate_fanjian` | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：目标掉 1 血 + used 标记 |
| 孙尚香 | 结姻 | active | ✓ | spec(cost 2 + 目标) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：双方回血 + 弃两张 |
| 孙尚香 | 枭姬 | passive | ✓ | EQUIPMENT_LOST | ✓ | ✓ | ✓ | DrawCardsAtom | COMPLETE | 端到端（走 UnequipAtom）摸两张 |
| 刘备 | 仁德 | active | ✓ | spec(可变费用 + transfer) | ✓ | ✓ | ✓ | ✓ | PARTIAL | 可发动、输入通道正确；未做端到端给牌场景 |
| 刘备 | 激将 | active | ✓ | 主公技 | — | — | ✓ | — | COMPLETE | FFA 不绑定、身份模式仅主公绑定（设计如此） |
| 诸葛亮 | 观星 | active | ✓ | spec | ✓ | ✓ | ✓ | ✓ | PARTIAL | 阶段限制正确（仅准备阶段）；未做端到端 |
| 诸葛亮 | 空城 | locked | ✓ | TARGET_FORBIDDEN | ✓ | ✓ | ✓ | — | COMPLETE | 空手不可被指定；有手牌即可 |
| 马超 | 马术 | locked | ✓ | DISTANCE_OUTGOING | ✓ | ✓ | ✓ | — | COMPLETE | 距离差 1（只问座次环） |
| 马超 | 铁骑 | passive | ✓ | CARD_USED + JudgeFlow | ✓ | ✓ | ✓ | ✓ | **COMPLETE（本轮修复）** | 修前 `_cannot_respond` 无人读，技能完全不生效 |
| 甄姬 | 倾国 | view_as | ✓ | conversions(黑牌→闪) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 普通点击不转换 |
| 甄姬 | 洛神 | passive | ✓ | PHASE_START(准备) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 端到端：黑色判定获得判定牌 |
| 许褚 | 裸衣 | passive | ✓ | phase_replacement(DRAW) | ✓ | ✓ | ✓ | ✓ | PARTIAL | 可发动、modifier 联动正确；未做端到端伤害 +1 场景 |
| 许褚 | 裸衣强化 | locked | ✓ | DAMAGE_DEALT + DRAW_COUNT | ✓ | ✓ | ✓ | — | PARTIAL | 同上（裸衣发动后才生效） |
| 孙权 | 制衡 | active | ✓ | spec(可变费用) | ✓ | ✓ | ✓ | ✓ | PARTIAL | 可发动、输入通道正确；未做端到端换牌 |
| 孙权 | 救援 | passive | ✓ | 主公技 | — | — | ✓ | — | COMPLETE | 同激将（主公技绑定规则） |
| 吕蒙 | 克己 | passive | ✓ | phase_replacement(DISCARD) | ✓ | ✓ | ✓ | — | PARTIAL | 注册与阶段挂点正确；未做端到端跳过弃牌 |
| 大乔 | 国色 | view_as | ✓ | conversions(方块→乐不思蜀) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 普通点击不转换 |
| 大乔 | 流离 | passive | ✓ | BECOME_TARGET | ✓ | ✓ | ✓ | ✓ | PARTIAL | 监听挂点正确；未做端到端转目标 |
| 甘宁 | 奇袭 | view_as | ✓ | conversions(黑牌→过河拆桥) | ✓ | ✓ | ✓ | ✓ | COMPLETE | 普通点击不转换 |
| 陆逊 | 谦逊 | locked | ✓ | TARGET_FORBIDDEN | ✓ | ✓ | ✓ | — | COMPLETE | 挡顺手牵羊 / 乐不思蜀；不挡过河拆桥（符合标准） |
| 陆逊 | 连营 | passive | ✓ | ATOM_AFTER | ✓ | ✓ | ✓ | DrawCardsAtom | COMPLETE | 端到端：失去最后手牌后摸一张 |
| 黄盖 | 苦肉 | active | ✓ | `_activate_kurou` | ✓ | ✓ | ✓ | ✓ | PARTIAL | 可发动；未做端到端掉血摸牌 |
| 华佗 | 急救 | view_as | ✓ | conversions(红牌→桃) | ✓ | ✓ | ✓ | ✓ | PARTIAL | 濒死限制正确（出牌阶段不可发动）；未做端到端救援 |
| 华佗 | 青囊 | active | ✓ | spec(目标 + cost 1) | ✓ | ✓ | ✓ | ✓ | PARTIAL | 可发动、输入通道正确；未做端到端回血 |
| 吕布 | 无双 | locked | ✓ | RESPONSE_COUNT | ✓ | ✓ | ✓ | ✓ | **COMPLETE（本轮修复）** | 【杀】本来就对；**决斗完全没接** |
| 貂蝉 | 离间 | active | ✓ | 走通用 `UseCardAction(决斗)` | ✓ | ✓ | ✓ | ✓ | COMPLETE | 不复制决斗结算，直接复用 |
| 貂蝉 | 闭月 | passive | ✓ | PHASE_START(结束) | ✓ | ✓ | ✓ | DrawCardsAtom | COMPLETE | 端到端：结束阶段摸一张 |

**统计**：COMPLETE 29 / PARTIAL 11 / BROKEN 2（均已修复）/ REGISTERED_ONLY 0 /
NOT_REACHABLE 0 / UNCERTAIN 0。

PARTIAL 的含义严格限定为："接入点、限制条件、真人/AI 入口都核对无误，
但本轮没有构造端到端场景把它的效果跑到底"。不是"疑似没实现"。

## 4. 所有 BROKEN / PARTIAL 技能

### 4.1 吕布【无双】—— BROKEN → COMPLETE

**用户报告的现象**（"吕布使用决斗后，对方只出 1 张杀就被判定响应成功"）复核属实。

真实代码路径：

```
DuelEffect._request()           # 旧实现
    allowed_cards={"SHA"}, min_cards=0, max_cards=1
    request_context={"reason": "duel", "card": flow.card}
```

* 每轮只创建一个"要一张【杀】"的请求，`resolution.card is not None` 就直接
  交换响应方 —— 完全没有读 `Game.response_required_count()`。
* 对照：`ShaEffect` 早就读了同一个函数（`required=2`，提示"还需要 2 张"），
  所以**同一份规则能力只接了一半**。

### 4.2 马超【铁骑】—— BROKEN → COMPLETE

真实代码路径：

```
shu.py:362   card._cannot_respond = True      # 唯一一处
```

全项目（含 `getattr`）**没有任何地方读取这个标记**。
判定照常跑、`skill_state["hit"]` 照常写、伤害来源照常掉血，
但"目标不能使用【闪】"从未生效 —— 目标可以正常出闪躲开。

同文件同时存在一个字段名错误：`getattr(result, "card_color", None)`，
`JudgeResult` 上没有 `card_color`（实体牌上才叫这个名字，判定结果上叫
`color`）。旧代码靠"OR 条件的后半段"歪打正着，行为正确但意图不清。

### 4.3 朱雀羽扇（装备技能，非武将技能）—— BROKEN → COMPLETE

```
use_card.py:84   self.card._original_nature = self.card.nature
                 self.card.nature = "fire"
```

只写不读：这张实体【杀】结算结束后**永久保持 fire 属性**，进弃牌堆再被摸到
仍然是火杀。

### 4.4 PARTIAL 的 11 项

见矩阵备注。它们不是坏掉，而是本轮没有为它们各写一个端到端场景；
其中 **仁德 / 制衡 / 青囊 / 裸衣 / 观星 / 苦肉** 都在同一套 `ActiveSkillSpec`
通道上，而该通道已被**反间 / 结姻**端到端验证过。

## 5. 共性架构缺口

1. **规则能力只接了一半**：`response_required_count()` 存在、`RESPONSE_COUNT`
   modifier 存在、【杀】接了、**决斗没接**。同一份能力在不同效果里复制粘贴时
   漏掉一处 —— 这是无双 bug 的真正根因。
2. **"逐张响应"缺通用载体**：【杀】用 `shan_required` / `shan_played` 自己攒进度，
   别的效果（决斗）想实现同样的语义就得再写一遍，于是干脆没写。
3. **技能写下的标记没有读取者**：`_cannot_respond`（铁骑）这类"技能把结论写在
   实体牌上、由规则层统一读取"的约定，缺少一个明确的读取入口，
   写入方写完了也没人消费。
4. **属性改写没有还原**：`_original_nature`（朱雀羽扇）只写不读，
   实体牌的临时属性变永久。
5. **`ModifierKind.FORCED_USE` 是死枚举**：定义了但全项目零引用
   （离间实际走通用 `UseCardAction`，并不需要它）。

未发现的缺口（本轮**逐一排除**过，附证据）：

* 事件名不匹配 —— 静态扫描 40 个技能的全部 `SkillBinding.event_name`
  与全项目 `EventType.*` / `emit("...")` 比对，**零个死事件**。
* Modifier 没人查 —— 11 个 `ModifierKind` 全部有查询者，且都在真实计算路径上。
* AI 绕过引擎 —— AI 与真人共用 `PendingRequest` + `CardAction` 通道，
  无双修复后 AI 同样会被要求两张牌。
* 多人假设 `game.enemy` —— 技能层没有任何 `game.enemy` 引用；
  距离/座次全部走 `seats` 环。

## 6. 本轮修复内容

### 根因 A：缺少"多张响应"的通用载体

新增 `src/game/flows/response_requirement.py`（`ResponseRequirement`）：

```
required / accepted_cards / remaining / satisfied
accept(card)              # 只在牌已被引擎真实移动之后调用
prompt(base)              # "…（还需要 N 张）"
request_context()         # reason / card / required / accepted
for_source(game, source, responder, card, allowed, reason)
create_request(engine, flow=..., source=..., responder=..., prompt=...)
```

`for_source` 的 `source` 是**决定张数的那一方**（响应者的对手），
所以同一份代码同时覆盖"吕布用决斗"和"别人决斗吕布"两个方向。

用它重写了：

* `ShaEffect`（【杀】）—— 删除 `shan_required` / `shan_played` 私有状态
* `DuelEffect`（决斗）—— **本次修复的主体**

决斗的新生命周期：`begin → _open_round`（按对手重建需求）`→ _ask`（还要 N 张）
`→ resume`（`accept` 未满则继续 `_ask`；满了才换人；彻底交不出才结算伤害）。
每一张响应牌都先被引擎移出，再记进度，所以**第一张交了就不会退回**。

### 根因 B：技能写的标记没有读取入口

`Game.cannot_respond_to(card)` —— 规则层唯一读取点。
`ShaEffect.request_shan()` 在要牌**之前**查一次：命中就跳过整个响应直接进入伤害。
铁骑只负责写标记，不复制响应流程。

### 根因 C：临时属性没有还原

`UseCardFlow.finish()` 在收尾时把 `_original_nature` 还原并清空。

### 附带修正

`shu.py` 铁骑判定结果改读 `JudgeResult.color`（真实字段），去掉原先依赖
"OR 条件后半段"的巧合写法。

**没有为任何武将写特判**：吕布 / 马超 / 许褚 / 貂蝉 全都通过通用能力生效。

## 7. 修复后仍未完成的技能

诚实结论：**没有 BROKEN / REGISTERED_ONLY / NOT_REACHABLE 的技能**。
剩余 11 项是 **PARTIAL**（第 4.4 节），即"接入正确、本轮未补端到端场景"：

`仁德`、`观星`、`裸衣`、`裸衣强化`、`制衡`、`克己`、`流离`、`苦肉`、`急救`、`青囊`
（`激将`、`救援` 是主公技，FFA 下按设计不绑定，不算未完成）。

## 8. 修改文件

```
新增  src/game/flows/response_requirement.py
改动  src/game/card_effects/sha.py          改用 ResponseRequirement；读 cannot_respond_to
改动  src/game/card_effects/tricks.py       决斗逐张要牌（无双修复主体）
改动  src/game/core.py                      Game.cannot_respond_to()
改动  src/game/skills/standard/shu.py       铁骑判定字段修正 + 注释
改动  src/game/flows/use_card.py            朱雀羽扇属性还原
```

## 9. 实际验证场景

用真实引擎流程跑确定性场景（`UseCardAction` 提交、`PendingRequest` 驱动、
AI 与真人共用通道），最终 **26/26 通过**：

| # | 场景 | 结果 |
| --- | --- | --- |
| A | 吕布出杀 → 要求 2 张闪，两张都真实消耗后躲开 | PASS |
| B | 目标只有 1 张闪 → 第一张离手，**仍然受伤** | PASS |
| C | 吕布用决斗 → 首轮 required=2，提示"还需要 2 张" | PASS |
| D | 曹操决斗吕布（**双向**）→ 吕布响应 required=1，曹操 required=2 | PASS |
| E | 对手只有 1 张杀 → 该杀被消耗，本轮失败并受伤 | PASS |
| F | 对照：普通武将决斗 required=1；普通杀 required=1 | PASS |
| G | 铁骑红色判定 → 目标不能出闪，直接掉血 | PASS |
| H | 铁骑黑色判定 → 目标正常出闪躲开 | PASS |
| I | 朱雀羽扇 → 结算后【杀】属性还原为 normal | PASS |
| J | View-As：普通点击不转换；点【龙胆】后响应把【杀】当【闪】成功 | PASS |
| K | 奸雄拿到伤害牌 / 枭姬失去装备摸两张 / 天妒获得判定牌 | PASS |
| L | 反间造成伤害 / 结姻双方回血且弃两张 | PASS |
| M | 咆哮连续出杀 / 英姿摸 3 / 马术距离 -1 / 空城挡指定 | PASS |

额外单独验证：集智、刚烈、反馈、遗计、洛神、连营、闭月、突袭、离间
（走通用决斗流程）、六名 View-As 的"必须先点技能"防护。

**窗口模式（用户反馈的 compact）**：实测 `WINDOWED_SIZE = (1280, 800)` →
scale 0.80，手牌 85×118 **高于** `ART_MIN`(76×104)，走真实卡面、不 compact；
全项目不存在 `if not fullscreen: compact = True` 这类耦合，尺寸只来自
`LayoutMetrics`。截图：`tools/ui_snapshots/phase10_4_2_window_1280x800.png`。

**回归**：`compileall` 通过；全量 **934 tests 全部通过**（无失败）。

## 10. 后续建议

1. **先补齐 PARTIAL 的端到端场景**，再考虑继续 UI 阶段。优先
   `裸衣`（伤害 +1 与摸牌 -1 的联动）、`流离`（转目标）、`急救`（濒死救援）
   —— 这三者跨的规则面最广。
2. 把 `ModifierKind.FORCED_USE` 删掉或真正接入；当前它是纯死枚举。
3. `response_required_count` 现在被两个效果共用。将来任何"要求多张响应牌"的
   新效果（如某些武将技）应当直接复用 `ResponseRequirement`，
   **不要再在效果里自己攒进度**。
4. 审计工具建议固化成脚本：本次用的两条静态检查（"技能监听的事件是否有
   发出者"、"实体牌/玩家上的私有标记是否有读取者"）各自抓到过真实问题，
   值得进 CI。
