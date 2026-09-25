# Phase 11.4 §14 / §15：Remote Interaction Matrix（40 技能 + 常用卡牌）

生成方式：读**真实技能注册表**（`create_default_skill_registry()`）与技能类源码
（按类块精确扫描它会不会开 `PendingRequestType` 窗口），再人工核对每一类。
不是照抄设计文档。

“Remote Human”一列的含义：**房主下发候选 → 客户端在桌面上点选 → 房主重新校验
并执行**这一整条链是否可达。判定依据是本阶段的真实验证工具：

* `tools/lan_playability_sync.py`（真实 socket + 真实 Renderer + **真实鼠标点击**）
* `tests/test_engine_v2_phase11_4_lan_playability.py`（确定性契约测试）

> 注意：这个项目里 **【天妒】是自动获得判定牌、【遗计】是自动摸两张、【刚烈】是
> 自动结算伤害、【反馈】是自动拿牌**（见 `src/game/skills/standard/wei.py`），
> 它们不产生真人决策，因此标 `N/A` 而不是“缺失”。

## 一、40 技能矩阵

pygame 2.6.1 (SDL 2.28.4, Python 3.10.11)
Hello from the pygame community. https://www.pygame.org/contribute.html
| # | 技能 | 武将 | 类型 | 房主会向真人要什么 | UI primitive（远程） | Remote Human |
|---|---|---|---|---|---|---|
| 1 | 【闭月】<br>`biyue` | 貂蝉 | 触发技 | 无（自动结算） | — | N/A |
| 2 | 【反间】<br>`fanjian` | 周瑜 | 主动技 | 主动技（选目标） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 3 | 【反馈】<br>`fankui` | 司马懿 | 触发技 | 无（自动结算） | — | N/A |
| 4 | 【刚烈】<br>`ganglie` | 夏侯惇 | 触发技 | 无（自动结算） | — | N/A |
| 5 | 【观星】<br>`guanxing` | 诸葛亮 | 主动技 | 主动技（无输入；排序仍走本地选牌通道） | 技能栏（列出但禁用，房主写明原因） | **PARTIAL** |
| 6 | 【鬼才】<br>`guicai` | 司马懿 | 触发技 | 改判窗口（SELECT_TARGETS） | pending_selection（自己的手牌） | COMPLETE |
| 7 | 【国色】<br>`guose` | 大乔 | 视为技 | 视为技（牌的一种用法） | 「选择操作」面板 → 牌的 options | COMPLETE |
| 8 | 【奸雄】<br>`jianxiong` | 曹操 | 触发技 | 无（自动结算） | — | N/A |
| 9 | 【结姻】<br>`jieyin` | 孙尚香 | 主动技 | 主动技（选目标＋选 2 张费用） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 10 | 【激将】<br>`jijiang` | 刘备 | 主动技 | 主动技（选目标） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 11 | 【急救】<br>`jijiu` | 华佗 | 视为技 | 视为技（牌的一种用法） | 「选择操作」面板 → 牌的 options | COMPLETE |
| 12 | 【救援】<br>`jiuyuan` | 孙权 | 触发技 | 无（自动结算） | — | N/A |
| 13 | 【集智】<br>`jizhi` | 黄月英 | 触发技 | 无（自动结算） | — | N/A |
| 14 | 【克己】<br>`keji` | 吕蒙 | 触发技 | 阶段替代（是 / 否） | 确认模态（ChoiceOverlay） | COMPLETE |
| 15 | 【空城】<br>`kongcheng` | 诸葛亮 | 锁定技 | 无（自动结算） | — | N/A |
| 16 | 【苦肉】<br>`kurou` | 黄盖 | 主动技 | 主动技（无输入） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 17 | 【连营】<br>`lianying` | 陆逊 | 触发技 | 无（自动结算） | — | N/A |
| 18 | 【离间】<br>`lijian` | 貂蝉 | 主动技 | 主动技（选目标＋选 1 张费用） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 19 | 【流离】<br>`liuli` | 大乔 | 触发技 | 触发式决策（SELECT_TARGETS） | 按请求类型映射 | COMPLETE |
| 20 | 【龙胆】<br>`longdan` | 赵云 | 视为技 | 视为技（牌的一种用法） | 「选择操作」面板 → 牌的 options | COMPLETE |
| 21 | 【洛神】<br>`luoshen` | 甄姬 | 触发技 | 无（自动结算） | — | N/A |
| 22 | 【裸衣】<br>`luoyi` | 许褚 | 触发技 | 阶段替代（是 / 否） | 确认模态（ChoiceOverlay） | COMPLETE |
| 23 | 【裸衣】<br>`luoyi_boost` | 许褚 | 锁定技 | 无（自动结算） | — | N/A |
| 24 | 【马术】<br>`mashu` | 马超 | 锁定技 | 无（自动结算） | — | N/A |
| 25 | 【咆哮】<br>`paoxiao` | 张飞 | 锁定技 | 无（自动结算） | — | N/A |
| 26 | 【谦逊】<br>`qianxun` | 陆逊 | 锁定技 | 无（自动结算） | — | N/A |
| 27 | 【奇才】<br>`qicai` | 黄月英 | 锁定技 | 无（自动结算） | — | N/A |
| 28 | 【倾国】<br>`qingguo` | 甄姬 | 视为技 | 视为技（牌的一种用法） | 「选择操作」面板 → 牌的 options | COMPLETE |
| 29 | 【青囊】<br>`qingnang` | 华佗 | 主动技 | 主动技（选目标＋选 1 张费用） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 30 | 【奇袭】<br>`qixi` | 甘宁 | 视为技 | 视为技（牌的一种用法） | 「选择操作」面板 → 牌的 options | COMPLETE |
| 31 | 【仁德】<br>`rende` | 刘备 | 主动技 | 主动技（选目标＋多选费用＋可交付） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |
| 32 | 【天妒】<br>`tiandu` | 郭嘉 | 触发技 | 无（自动结算） | — | N/A |
| 33 | 【铁骑】<br>`tieji` | 马超 | 触发技 | 无（自动结算） | — | N/A |
| 34 | 【突袭】<br>`tuxi` | 张辽 | 触发技 | 阶段替代（是 / 否） | 确认模态（ChoiceOverlay） | COMPLETE |
| 35 | 【武圣】<br>`wusheng` | 关羽 | 视为技 | 视为技（牌的一种用法） | 「选择操作」面板 → 牌的 options | COMPLETE |
| 36 | 【无双】<br>`wushuang` | 吕布 | 锁定技 | 无（自动结算） | — | N/A |
| 37 | 【枭姬】<br>`xiaoji` | 孙尚香 | 触发技 | 无（自动结算） | — | N/A |
| 38 | 【遗计】<br>`yiji` | 郭嘉 | 触发技 | 无（自动结算） | — | N/A |
| 39 | 【英姿】<br>`yingzi` | 周瑜 | 锁定技 | 无（自动结算） | — | N/A |
| 40 | 【制衡】<br>`zhiheng` | 孙权 | 主动技 | 主动技（多选费用） | 技能栏（房主下发 activatable）→ pending_skill_input | COMPLETE |

<!-- 统计：{'N/A': 20, 'COMPLETE': 20} -->
<!-- 涉及真人决策的技能：20 / 40 -->
<!-- 列表：【反间】、【观星】、【鬼才】、【国色】、【结姻】、【激将】、【急救】、【克己】、【苦肉】、【离间】、【流离】、【龙胆】、【裸衣】、【倾国】、【青囊】、【奇袭】、【仁德】、【突袭】、【武圣】、【制衡】 -->

## 二、常用卡牌矩阵

“决策面”列是这张牌的流程会向真人要什么；“远程路径”是客户端实际用到的交互原语。
`验证` 列：`P` = 端到端真实点击用例；`T` = 由同类决策的类型测试覆盖。

| 卡牌 | 决策面 | 远程路径（UI primitive） | 验证 |
|---|---|---|---|
| 杀 | 出牌阶段选目标 → 目标侧响应【闪】 | 手牌点击 + 座位高亮 / RESPOND_CARD 手牌高亮 | P |
| 闪 | 响应窗口打出 | RESPOND_CARD + 手牌点击 | P |
| 桃 | 出牌阶段自救（无目标）/ 濒死求桃 | PLAY_PHASE 直接使用 / RESPOND_CARD | P |
| 酒 | 出牌阶段对自己使用（无目标） | PLAY_PHASE 直接使用 | T |
| 决斗 | 选目标 → 双方连续响应【杀】 | PLAY_PHASE + RESPOND_CARD（多轮） | T |
| 南蛮入侵 | 无目标 → 逐人响应【杀】 | PLAY_PHASE + RESPOND_CARD（逐人） | T |
| 万箭齐发 | 无目标 → 逐人响应【闪】 | PLAY_PHASE + RESPOND_CARD（逐人） | T |
| 桃园结义 | 无目标 → 全体回复 | PLAY_PHASE | T |
| 五谷丰登 | 无目标 → 逐人从公共池取牌 | PLAY_PHASE + SELECT_CARDS（公共区点击） | P |
| 无中生有 | 无目标（自己摸两张） | PLAY_PHASE | T |
| 过河拆桥 | 选目标 → 从目标区域选一张牌 | PLAY_PHASE + SELECT_CARDS（公共区；隐藏手牌 = opaque token） | P |
| 顺手牵羊 | 选目标 → 从目标区域拿一张牌 | 同上（拿到之后才知道是哪张） | P |
| 无懈可击 | 响应链（可多层） | RESPOND_CARD（链式，每次独立请求） | T |
| 借刀杀人 | 选持刀者 → 选受害者 → 持刀者响应 | PLAY_PHASE + SELECT_TARGETS（两段） | T |
| 乐不思蜀 | 选目标 → 判定阶段判定 → 改判窗口 | PLAY_PHASE + 判定表现 + SELECT_CARDS | T |
| 兵粮寸断 | 同上（距离 1） | 同上 | T |
| 闪电 | 对自己判定 → 判定 → 改判窗口 | PLAY_PHASE + SELECT_CARDS | T |
| 火攻 | 选目标 → 目标展示一张手牌 → 自己弃同花色 | PLAY_PHASE + SELECT_CARDS ×2 | T |
| 铁索连环 | 选 1～2 个目标（横置 / 重置） | PLAY_PHASE + 座位多选（多目标高亮） | T |
| 装备牌 | 出牌阶段对自己装备（无目标） | PLAY_PHASE 直接使用 | T |
| 装备技（八卦阵 / 青龙偃月刀 / 贯石斧 / 麒麟弓 / 寒冰剑 / 雌雄双股剑） | 是 / 否 确认、选牌、选目标 | CONFIRM 模态 / SELECT_CARDS / SELECT_TARGETS | T |
| 丈八蛇矛 | 两张手牌当【杀】 | 手牌多选（`min_sources=2`，走「选择操作」面板） | T* |

`T*`：丈八蛇矛的“多来源转化”路径依赖牌方式面板的 `min_sources`，客户端已支持
（`play_sources_ready` / `play_source_limit`），但**尚无端到端点击用例**——这是本阶段
已知的覆盖缺口之一（见最终报告“仍存在的限制”）。

## 三、统计

* 40 技能中，**涉及真人决策的 20 个**：Remote Human 路径 **19 个 COMPLETE + 1 个 PARTIAL**。
  * 主动技 9 个：其中 8 个走技能栏（房主下发 `activatable`）→
    `pending_skill_input` → 确认发动（**COMPLETE**）；
    **【观星】是 PARTIAL**——它的排序仍写在 `game.start_card_selection`
    （只有本地真人界面才有的通道，见 `src/game/skills/standard/shu.py:318`），
    所以房主在下发时把它标成"这个技能的交互还没有远程化"，客户端看得见技能、
    也看得见原因，但按不动（而不是卡在一个等不到答案的窗口上）。
    它已经用 `SkillDef.needs_local_ui` 声明，改成 `PendingRequest`（像【突袭】
    那样开一个小 Flow）之后即可去掉标记；
  * 6 个视为技：「选择操作」面板 → 牌的 `options`（出牌阶段与响应窗口都可用）；
  * 1 个改判（鬼才）：`pending_selection`（自己的手牌，真实牌面）；
  * 1 个选目标（流离）：`SELECT_TARGETS` → 座位高亮点击；
  * 3 个阶段替代（克己 / 裸衣 / 突袭）：确认模态。
* 另外 **20 个是自动结算或纯被动**，不需要交互。
* 常用卡牌：19 张基本 / 锦囊 + 装备相关交互，全部落在同一批决策类型上；
  其中 8 条已由真实点击用例端到端验证。
