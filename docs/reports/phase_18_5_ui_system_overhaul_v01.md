# Phase 18.5：统一游戏交互与视觉系统

> 这一轮不是"把三个弹窗弄好看一点"，而是把"界面长什么样、什么状态该被看见"
> 从散落在 25 个文件里的经验值，收成一套**唯一的令牌表 + 唯一的绘制入口**。
>
> 规则一行没改。所有验证都跑在真实引擎 + 真实 Renderer 上，关键场景跑在
> **真实窗口**（``driver=windows``）里。

---

## 1. 原 UI 的主要问题

审计（``src/ui`` 全量 + ``renderer.py``）查出 16 类问题，最要命的几条都是有
**明确因果**的缺陷，不是"不够好看"：

| # | 问题 | 后果 |
| --- | --- | --- |
| 1 | ``hover`` 参与状态归并且优先级(46) **高于** ``current_turn``(44) | 鼠标移到当前回合角色上，金色描边被换成蓝色，**「当前回合」角标直接消失** |
| 2 | ``hover``(46) 高于 ``invalid_target``(20) | 鼠标移到非法目标上，压暗被取消，那个座位看起来反而像能点 |
| 3 | ``dim`` 遮罩画在内容**之前** | 座位变暗只暗了底板：头像、名字、血量、装备原样亮着，「非法目标」「阵亡」看起来只暗了一半 |
| 4 | 卡牌有一份**私有**的状态描边实现，不读 ``label`` | 手牌选中后永远看不到「已选」角标（那个角标只有座位会画） |
| 5 | ``view_as_candidate`` 与 ``hover`` 同色同发光，只差 1px | 候选牌被鼠标一碰就"变成另一种东西" |
| 6 | 手牌只有"不能出的变灰"，**没有"能出的"正向高亮** | 玩家靠排除法猜哪些牌能用 |
| 7 | 响应窗口（求桃 / 出闪 / 无懈）单机侧**没有任何手牌高亮** | 濒死求桃时玩家不知道手上哪张是【桃】 |
| 8 | 弃牌阶段既不灰化也不高亮 | 玩家不知道"现在该点什么" |
| 9 | 座位**没有濒死状态** | 只靠一次性的"濒死"飘字 + 提示条 |
| 10 | 座位**没有技能发动高亮** | "技能发动了但不知道是谁" |
| 11 | Modal 遮罩 alpha 有 168/170/172/176/190 **五档**，圆角有 10/12/16/18/22 **五种** | 14 个弹层像不同阶段拼的 |
| 12 | 武将 Tooltip 自己算位置，**没有避让名单** | 会盖住座位面板、装备区、公共牌池，内容高时还会画出屏幕 |
| 13 | 技能提示只有**一种尺寸** | 郭嘉这类连续触发技能会让大横幅一直弹，打断玩家 |
| 14 | 身份揭示是"界面"不是"演出"，没有动画 | 死亡时身份只体现为副标题多了两个字 |
| 15 | 非身份模式的结算**只有一条战报** | 看不到这局都有谁、谁活着 |
| 16 | 无懈链只显示"窗口 2 · 第 1 轮" | 玩家看不懂在无懈哪张牌、谁在用 |

---

## 2. 最终 Design System

三层，改一处全场生效：

```text
theme.py         颜色 / 尺寸 / 字体 / 视觉状态   ← 唯一参数来源
widgets.py       绘制入口（描边、压暗、模态、提示框避让）
组件             只调用，不自己配颜色与圆角
```

新增 / 强制的两条架构约束：

1. **hover 与语义状态正交**。``hover`` 不再参与 ``resolve_state`` 归并，改由
   ``widgets.draw_state_border(..., hovered=True)`` 叠一圈柔光。于是
   "当前回合 + 鼠标在上面" 与 "非法目标 + 鼠标在上面" 都能同时表达。
2. **压暗与描边分离**。``draw_state_dim`` 与 ``draw_state_border`` 是两个函数，
   调用方按 `底板 → 内容 → 压暗 → 描边 → 角标` 的顺序画，整块面板才会一起暗。

---

## 3. Theme Token

``src/ui/theme.py``（**没有新增第二套主题系统**，全部扩展在已有文件里）。

### 颜色语义

```text
BACKGROUND / TABLE_SURFACE / PANEL / PANEL_ELEVATED / OVERLAY_DIM
TEXT_PRIMARY / TEXT_SECONDARY / TEXT_MUTED
ACCENT / WARNING / DANGER / SUCCESS
LEGAL / ILLEGAL / SELECTED / HOVER
CURRENT_TURN / CURRENT_RESPONDER / SKILL_ACTIVE
```

其中 ``PANEL_ELEVATED / TEXT_SECONDARY / LEGAL / SELECTED / CURRENT_TURN …``
都是既有颜色的**别名**（同一个值），新增界面请用语义名；旧名字继续可用，
所以 35 个界面一行调用都不用改。

### 尺寸

```text
RADIUS_SMALL=8   RADIUS_MEDIUM=9   RADIUS_LARGE=18   RADIUS_MODAL=16
BORDER_NORMAL=3  BORDER_STRONG=4   BORDER_MODAL=3
PANEL_PADDING=16 MODAL_PADDING=24  CARD_GAP=10       SECTION_GAP=18
```

### 文字（按用途取档，不再散落 ``large``/``small`` 字面量）

```text
FONT_TITLE / FONT_MODAL / FONT_SECTION / FONT_BODY / FONT_SMALL
FONT_STATUS / FONT_CARD / FONT_SKILL / FONT_BADGE
```

### 视觉状态

```text
none / hover / selected          ← 通用
current_turn / pending_response / dying / dead / skill_acting   ← 角色级
valid_target / valid_target_hover / invalid_target / selected_target ← 目标级
legal_card / response_candidate / discard_candidate / view_as_candidate / view_as_source ← 卡牌级
disabled / playable
pool_selected / pool_hover / draft_focus   ← 武将池
HOVER_LAYER                      ← 与上面全部正交的悬停叠加层
```

优先级（数字越大越优先）：``dead 120 > dying 118 > selected_target 100 >
skill_acting 86 > pending_response 84 > current_turn 82 > valid_target_hover 76 >
valid_target 74 > legal_card 62 > view_as_candidate 56 > hover 50 >
invalid_target 30 > disabled 15``。

### 模态统一参数

```text
MODAL_VEIL_ALPHA=176   MODAL_FILL=PANEL   MODAL_BORDER=GOLD
MODAL_WIDTH=3          MODAL_RADIUS=16
```

五个模态（技能选择 / 操作选择 / 二选一 / 结算 / 武将池确认框）现在全部走
``widgets.draw_modal_veil`` + ``widgets.draw_modal_panel``——**原来它们各写各的
遮罩浓度与圆角**。

---

## 4. SeatPanel 状态矩阵

绘制顺序：`底板 → 内容（头像/名字/血量/装备/判定/手牌数）→ 压暗 → 描边+hover
→ 角标`。

**两处角标，互不抢占**：右上第一枚是**角色状态**（当前回合 / 响应中 / 濒死 /
阵亡），第二枚是**操作态**（可选 / 目标），左侧再排横置。目标选择期间仍然能
一眼看出"谁在行动、谁快死了"。

| 状态 | 描边 | 发光 | 压暗 | 角标 | 触发来源 |
| --- | --- | --- | --- | --- | --- |
| 普通 | 暗金 3px | — | — | — | 默认 |
| 当前回合 | 亮金 5px | 金 8 层 | — | 「当前回合」 | `is_current` |
| 当前响应者 | 青 5px | 青 9 层 | — | 「响应中」 | `is_responding`（含濒死救援者） |
| 濒死 | **危险红 6px** | 红 12 层 | — | **「濒死」** | `game.dying_state()` |
| 阵亡 | 灰 2px | — | **150** | 「阵亡」 | `alive=False` |
| 技能发动 | 玉绿 5px | 玉绿 9 层 | — | — | `effects.skill_acting(player)` |
| 合法目标 | 蓝 4px | 蓝 7 层 | — | — | `candidate` |
| 合法目标+hover | 亮蓝 5px | 蓝 11 层 | — | 「可选」 | `candidate and hovered` |
| 已选目标 | 金 6px | 金 12 层 | — | 「目标」 | `selected` |
| 非法目标 | 灰 2px | — | **92** | — | `in_target_mode and not candidate` |
| 任何状态 + 鼠标 | 叠加 3px | 柔光 6 层 | — | 保留 | `hovered`（正交） |

**濒死与技能发动是本轮新增的两个状态**，数据来源都走规则层：
``Game.dying_state()``（只读活着的 ``DyingFlow``，不判断规则）、
``Effects.skill_acting()``（技能提示播出的那段时间内有效）。

---

## 5. Card 状态矩阵

| 状态 | 描边 | 说明 |
| --- | --- | --- |
| 普通 | 无（只有素材自身边框） | — |
| hover | 蓝 3px + 柔光 6 层（**叠加**，抬起 28px） | 与任何状态共存 |
| selected | 金 6px + 金晕 8 层，抬起 **40px** | 角标「已选」现在真的会画 |
| legal_card | 玉绿 3px + 玉绿晕 | **本回合可以打出**（本轮新增的正向高亮） |
| response_candidate | 玉绿 4px + 绿晕 5 层 | 响应窗口里许可的牌（闪/桃/无懈） |
| discard_candidate | 青铜 3px | 弃牌阶段可以弃的牌 |
| view_as_candidate | 蓝 3px + 蓝晕 | 技能转化的合法来源 |
| view_as_source | 蓝 6px + 蓝晕，角标「来源」 | 已选来源 |
| disabled / dimmed | 灰 2px + 卡面纱罩 α150 | 灰化 |

**两处关键修复**：

* 卡牌与座位现在**共用** ``widgets.draw_state_border``（删掉了 ``cards`` 里那份
  不读 ``label`` 的私有复制），手牌选中终于有「已选」角标；
* ``hover`` 从归并里移除，候选牌被悬停时不会再"变成另一种东西"。

---

## 6. Interaction UI

Phase 18 的 ``InteractionSchema`` 保持不变（本轮不动规则层）。界面侧的变化是
**提示与状态不再靠猜**：

* **单机响应窗口的手牌许可**：新增 ``player.respondable_hand_indices(game)``，
  判据来自引擎的 ``CardActionDiscovery.respondable_options``（含龙胆这类转化）。
  以前这条路径在单机下恒返回 ``None``，濒死求桃时没有任何手牌高亮。
* **弃牌阶段**：手牌全部标为 ``discard_candidate``（本地弃牌走 ``player_discard``）。
* **公共牌池**：新增"谁正在挑选"标注（``draw_pool(chooser=...)``）。
* **濒死提示标题**：从"需要你的响应"改成"**AI 1 濒死，请出【桃】**"，
  名字来自 ``Game.dying_state()``。
* **模态互斥**：技能选择与操作选择现在不会同时铺两层遮罩（见下）。

---

## 7. Presentation UI

| 演出 | 级别 | 位置 | 时长（默认档） |
| --- | --- | --- | --- |
| 技能发动（主动技 / 视为技） | **MAJOR** | 顶部居中偏下（0.16 屏高），武将卡 + 技能名 + 类型 chip + 完整说明 | 2.02s |
| 技能发动（锁定技 / 触发技） | **MINOR** | 贴顶细横条，`【技能名】 · 类型 · 角色名`，无武将卡 | 1.11s |
| 判定 | 面板 | 居中，阶段化流程 | 见 ``FXTiming.judge_*`` |
| 身份揭示 | 面板 | 正中，武将牌 + 姓名 + **大字号身份** | 2.10s（新增） |
| 结算 | 模态 | 居中，标题 + 阵营 + 逐人结果卡 | 常驻 |
| 无懈链 | 条 | 顶部（0.048 屏高），牌 + 使用者 + 轮次点 + 状态 | 常驻至链结束 |

**MAJOR/MINOR 分级的理由**：郭嘉这类技能会连续触发，用大横幅会让玩家一直被
弹窗打断；而主动技是玩家**自己点出来**的，必须看清。分级判据来自规则层给的
``skill_kind``（``active``/``view_as`` → MAJOR），界面不按技能名猜。

---

## 8. 判定演出

判定面板在 Phase 18 已经重做过（``JudgeStage`` 九阶段：OPEN → SOURCE_HOLD →
DRAW_ANIMATION → REVEALED_HOLD →（可多次 REPLACEMENT）→ FINAL_RESULT →
OUTCOME_HOLD → FADE_OUT），本轮**没有重写它**，做的是：

* 结论大字（``outcome.title``，如"判定成功 · 跳过出牌阶段"）已由规则层
  ``judge_presentation`` 提供，本轮确认它在最显眼位置（分隔线下、tone 上色）；
* 改判窗口的 schema（``min_count=0 / cancellable``）与演出对齐——改判记录
  （"被谁用什么技能换过"）在面板右列显示；
* 判定期间**座位不再抢戏**：``JudgeGate`` 继续压制本机输入，演出闸门让规则时间线
  等它演完（Phase 18 的行为，本轮回归确认仍然成立）。

截图：[05_judge.png](assets/phase18_5_ui/05_judge.png)、
[06_judge_replace.png](assets/phase18_5_ui/06_judge_replace.png)。

---

## 9. 火攻

专用面板保持 Phase 18 的设计（左：对方展示牌；右：自己的手牌合法/非法；底部：
规则层渲染的说明），本轮补齐交互收口：

* ``panel="reveal_and_pick"`` 由规则层声明，UI 不按 reason 白名单认领；
* "放弃选择"按钮与规则层的 ``cancellable`` 一致（单机与联机同一个按钮）；
* 没有同花色手牌时**不弹弃置窗口**，直接收尾（不会让玩家以为卡死）。

截图：[07_huogong.png](assets/phase18_5_ui/07_huogong.png)。

---

## 10. 五谷丰登

公共牌区**不复用装备槽**（``layout.public_rect_list`` 是独立布局）：

* 一律真实卡面 + 花色点数（在卡面之上由程序绘制，素材不冒充游戏数据）；
* hover 放大上浮 1.18 倍并画在最后；
* selected / candidate 与手牌同一套状态；
* **新增**：当前挑选者标注（"XX 正在挑选"）——五谷是公开的集体流程，不标出来
  其他人不知道轮到了谁；
* 被移动动画取走的牌当帧不画（格子留着），不会出现两个副本。

截图：[08_wugu.png](assets/phase18_5_ui/08_wugu.png)。

---

## 11. 无懈链

新增 ``src/ui/wuxie_chain.py``。**用图形表达链深度，不写"链深度 3"**：

```text
[牌] 【南蛮入侵】 玩家 使用   ●───●───◉   等待其他玩家响应
                              └ 过去的轮   └ 当前轮
```

* 左边是被无懈的牌（真实卡面）+ 使用者；
* 中间是轮次指示：实心 = 已经过去的轮、亮点 = 当前轮、空心 = 还没到；
* 右边是状态（"等你决定" / "你已放弃本轮" / "等待其他玩家响应"）。

数据来自两条路径、字段一致：单机读 ``pending_request.context``，联机读房主下发的
``response_window``。**本轮补上了联机缺失的字段**——``build_response_window``
原先只给 ``window_id/round_id/reason/status``，客户端根本拿不到"在无懈哪张牌"。
被无懈的牌与其使用者都是桌上已公开的信息（牌已经打出来了），不存在泄露。

截图：[09_wuxie_chain.png](assets/phase18_5_ui/09_wuxie_chain.png)。

---

## 12. 濒死

四处一起改，缺一个玩家就不知道"谁快死了、该谁救、该出什么"：

| 位置 | 表现 |
| --- | --- |
| 濒死角色座位 | **红色面板**（危险色描边 + 12 层红晕 + 「濒死」角标 + 血点全空 + 头像暗化） |
| 当前救援者座位 | 青色「响应中」边框（**与濒死角色分开标注**） |
| 提示条 | 红色标题 "**AI 1 濒死，请出【桃】**" + 引擎正文 + 右侧"点击手牌中的【桃】" |
| 手牌 | 可响应的【桃】绿色描边（本轮新修的单机路径） |

数据来源 ``Game.dying_state()``：扫"当前待回答请求的 ``owner_flow``"里的
``DyingFlow``（这是所有可恢复流程的统一挂法；濒死流程**不在** ``active_flows``
里，这一点是实测查出来的）。

截图：[10_dying.png](assets/phase18_5_ui/10_dying.png)。

---

## 13. 技能演出

见第 7 节的 MAJOR/MINOR 表。另外：

* **技能类型的中文表只有一份**（``interaction_presentation.SKILL_KIND_LABELS``），
  技能条 / 判定面板 / 演出队列 / 武将图鉴共用；
* 技能提示的**说明文本由规则层随事件下发**（Phase 18 的成果），本轮确认
  MAJOR/MINOR 两条路径都带 ``kind_label`` 与 ``description``；
* 座位上的**武将区高亮**（``skill_acting``）与横幅同时出现，解决"技能发动了但
  不知道是谁"。

截图：[03_skill_major.png](assets/phase18_5_ui/03_skill_major.png)、
[04_skill_minor.png](assets/phase18_5_ui/04_skill_minor.png)。

---

## 14. 身份揭示

新增 ``src/ui/identity_flash.py``：**阵亡后翻开身份**的演出。

```text
武将牌 + "AI 1 阵亡"  →  【反贼】（大字号，按身份上色）  →  "身份已公开"
```

* 触发点：``Effects._on_death`` → ``_maybe_reveal_identity``；可见性走规则层的
  ``visible_identity``（身份模式下阵亡即公开，非身份模式没有身份可翻，界面不自己
  决定谁能看）。
* 身份配色：主公金 / 忠臣玉绿 / 反贼红 / 内奸蓝（``IDENTITY_TONES``），
  键是规则层给的身份名，界面不判断谁和谁一伙。
* 时长走 ``FXTiming.story_identity``，与其它演出同一套速度档。

截图：[11_identity_reveal.png](assets/phase18_5_ui/11_identity_reveal.png)。

---

## 15. GameResult

结算面板从"标题 + 一条战报"补齐为**逐人结果卡**：

* 身份模式：阵营标题（``mode.result_headline()``）+ 每人一张卡（武将牌缩略 +
  身份牌缩略 + 姓名/身份/武将 + 存活/阵亡）；
* **非身份模式也有列表**（原来只有最后一条战报）——按同一张表的字段从
  ``game.players`` 现取，没有身份字段就少画一张缩略图、整行左移，不留空位；
* 没有武将素材时框里画**武将名首字**，而不是留一个空框。

截图：[12_game_result.png](assets/phase18_5_ui/12_game_result.png)。

---

## 16. Tooltip

| 项 | 改动 |
| --- | --- |
| 武将 Tooltip | 从"自己算位置"改成走 ``widgets.place_tooltip``（右侧→左侧→下方→上方→四角，取第一个完全在视口内且与 avoid 不相交的位置） |
| 避让名单 | 补上**技能条按钮区、战报区、速度控件、公共牌池、技能横幅**（原来只有座位/手牌/提示条/按钮/判定面板） |
| 越界 | 三个提示框（卡牌 / 技能 / 武将）现在共用同一个函数，不会再画出屏幕 |

---

## 17. 动画系统

* 时长集中在 ``src/ui/fx.py`` 的 ``FXTiming``（**不在 theme.py**，本轮没有搬动，
  避免为了整齐制造无收益的改动）；速度档：very_slow 1.75 / slow 1.30（默认）/
  normal 1.0 / fast 0.70。
* 本轮新增 ``story_skill_minor = 0.85`` 与 ``story_identity = 2.10``。
* 开局发牌**逐张飞**（``initial_deal_card = 0.28``），飞行中画牌背，
  手牌区不重复画——Phase 18 已有，本轮确认未回归。
* 动画速度仍然是**客户端本地设置**（Phase 18 的结论），本轮没有引入任何跨机同步。

---

## 18. 分辨率结果

真实窗口下逐个切换（``pygame.display.set_mode`` + ``resync``），实际生效尺寸
与截图一致：

| 分辨率 | 结果 | 截图 |
| --- | --- | --- |
| 1280×800 | ✅ 无越界、无重叠 | [res_1280x800.png](assets/phase18_5_ui/res_1280x800.png) |
| 1366×768 | ✅ 可玩（手牌 6 张不挡按钮、技能栏不压手牌） | [res_1366x768.png](assets/phase18_5_ui/res_1366x768.png) |
| 1600×900 | ✅ | [res_1600x900.png](assets/phase18_5_ui/res_1600x900.png) |
| 1920×1080 | ✅ | [res_1920x1080.png](assets/phase18_5_ui/res_1920x1080.png) |
| 2560×1440 | ✅ | [res_2560x1440.png](assets/phase18_5_ui/res_2560x1440.png) |

1366×768 下 `micro` 字号约 13px、座位名约 20px，在截图里逐行可读。

---

## 19. LAN Host / Guest 结果

* ``tools/lan_smoke.py``：**44/44 通过**（含大厅、开局流程、断线、局域网真实 IP）
* ``.cache/phase18/probe_lan.py``：**26/26 通过**（客户端真的建了 Renderer 并逐帧绘制）
* ``.cache/phase18/probe_schema.py``：**53/53 通过**——含"从 socket 原始报文里
  取 DECISION_REQUEST，逐字段比对房主 schema"这一段

**Host / Guest 视觉一致性的本轮改动**：无懈链的数据补进了
``build_response_window``（原来客户端拿不到被无懈的牌），因此两端现在显示
**同一条链**（同样的牌名、使用者、轮次）。其余演出（判定 / 技能 / 火攻 /
身份 / 结算）在 Phase 18 已统一到同一份 schema 上，本轮未破坏。

**没有截到 LAN Guest 的真实窗口截图**：需要在真实窗口里跑两个进程 + 两台
"机器"（同机两个窗口）。这一项用探针覆盖（26/26 + 53/53 都包含 Guest 侧的真实
渲染），但没有真实窗口截图——如实标注。

---

## 20. 修改文件

### 新增（2 个 UI 模块）

| 文件 | 内容 |
| --- | --- |
| ``src/ui/wuxie_chain.py`` | 无懈链的视觉表达（轮次点、牌、使用者、状态） |
| ``src/ui/identity_flash.py`` | 身份揭示演出（武将牌 + 身份大字，淡入淡出） |

### 修改（22 个）

``src/ui/theme.py``（Design Token + 状态矩阵重写）、``src/ui/widgets.py``
（压暗/描边分离、hover 叠加、模态 helper、``place_tooltip`` 扩容）、
``src/ui/seats.py``（座位状态系统：顺序、双角标、濒死、技能高亮）、
``src/ui/cards.py``（卡牌状态 + 共用绘制入口）、``src/ui/player.py``
（手牌状态矩阵 + ``respondable_hand_indices``）、``src/ui/tooltip.py``
（接入统一避让）、``src/ui/skill_banner.py``（MAJOR/MINOR 两级）、
``src/ui/overlay.py``（结算逐人列表 + 无身份模式）、``src/ui/prompt.py``
（濒死标题 + 传 game）、``src/ui/table.py``（公共池挑选者标注）、
``src/ui/storyboard.py``（技能两级）、``src/ui/fx.py``（技能高亮、身份演出、
两个新时长）、``src/ui/skill_bar.py`` / ``action_picker.py`` / ``choice.py`` /
``favorite_generals.py``（模态外观统一）、``src/renderer.py``（濒死/技能状态、
无懈链、提示框避让、卡解析）、``src/game/core.py``（``dying_state``）、
``src/game/view/view_builder.py``（无懈链字段）。

---

## 21. 删除的重复逻辑

| 删除 / 收敛 | 原来有几份 |
| --- | --- |
| 卡牌私有的状态描边实现 ``cards._draw_state_border`` | 2 份（座位/卡牌各一）→ 1 |
| 模态遮罩与面板画法 | 5 种遮罩浓度 + 5 种圆角 → 1 套 helper |
| 技能类型中文表 | 4 份（技能条/判定面板/演出队列/图鉴）→ 1（Phase 18 已收敛，本轮确认） |
| 势力色表 | 2 份（1v1 设置 / 图鉴，且"神"色值不一致）→ 1（Phase 18.4） |
| 座位状态角标 | 1 处 → 2 处（角色态 + 操作态，**这是拆分不是重复**） |
| ``STATUS_DEAD`` / ``STATUS_RESPONDING`` 死常量 | 删除（实际文案来自 ``theme`` 的 label） |

---

## 22. 剩余 UI 技术债

如实列出**本轮没做**或**没做完**的：

1. **视觉层级 dim（第五章）**：没有实现"非当前重点区域整体压暗"。原因是它需要
   Renderer 引入一个全局 dim 通道，与已有的 ``interaction_hold`` / 判定面板 /
   演出队列会互相干扰，风险大于收益。**已实现的是局部 dim**：座位级
   ``invalid_target``(92) / ``dead``(150) / 角色死亡头像(150)。
2. **HUD 布局重排（第九章）**：没有重排。现有布局（左上速度、右上投降、底部
   手牌+按钮、右侧技能）已在 5 档分辨率下验证不越界，重排的收益不明确。
3. **判定演出（第十三章）**：没有重写（Phase 18 已完成九阶段流程），本轮只做
   对齐与确认。
4. **火攻"中间火焰关系/箭头"**：没有加。火攻面板仍是"左展示牌 / 右手牌"，
   理由是牌与目标的关系已经由动作横幅（"玩家 对 AI 1 使用【火攻】"）表达。
5. **动画语义统一（第二十章）**：没有把 ``FXTiming`` 搬进 theme，也没有为
   每种动画语义建枚举。时序表本身是集中的，搬文件没有收益。
6. **箭头 FX（第二十一章）**：没有改。箭头逻辑在 Phase 18 已按"动作 → 停顿 →
   箭头 → 结算 → 释放"组织，本轮未动。
7. **装备 Tooltip（第十八章）**：位置算法与避让已统一，但**没有给基本牌加
   Tooltip**（现在只有装备/锦囊会弹提示，因为基本牌信息量小）。
8. **主菜单视觉（第二十四章）**：配色已统一（Phase 18.4），本轮只加了截图确认，
   没有重排布局。
9. ``ui/prompt.py`` 仍自己算弃牌张数（``len(hand) - hp``，不考虑庸肆这类
   手牌上限修正）；``ui/seats.py`` 的 ``JUDGE_SHORT`` 仍是 UI 侧的缩写表
   （但它不参与规则判断，只是显示缩写）；``ui/interaction.py`` 仍用
   ``card.name`` 调规则层的 ``granted_skill_id``。这三处是 Phase 18 报告里
   列的 10 处"UI 猜规则"中剩下的。
10. **``FXTiming`` 与 ``theme`` 的边界**：时序在 ``fx.py``、颜色/尺寸在
    ``theme.py``。这个分工是有意的（时序要能被演出层独立调整），但如果以后
   想要"一套令牌打通"，需要再评估。

---

## 23. 自动测试

| 项 | 结果 |
| --- | --- |
| ``python -m compileall src main.py`` | ✅ exit 0 |
| ``probe_draw_smoke``（ffa/identity/duel 各 420 帧真实绘制） | **7/7** |
| ``probe_judge``（乐不思蜀/兵粮/闪电/未命中） | **23/23** |
| ``probe_huogong`` | **19/19** |
| ``probe_skills``（触发/主动/视为/锁定四类提示） | **13/13** |
| ``probe_chain``（天妒 + 界面让路） | **9/9** |
| ``probe_gate``（演出闸门 + 规则让路） | **8/8** |
| ``probe_schema``（Host/Guest 同一份 schema） | **53/53** |
| ``probe_lan``（联机演出与视觉账本） | **26/26** |
| ``probe_no_deadlock``（三模式各 1800 帧） | **12/12** |
| ``tools/lan_smoke.py`` | **44/44** |
| ``tools/multiplayer_smoke.py`` | 4 局整局全部 ``stuck=None / error=None`` |

---

## 24. 真实窗口手玩

**必须先把边界说清楚**：我这边的"手玩"是**真实窗口 + 程序化事件注入**，
**不是人手点击**。

已经做到的（``ui_scenes.py``，真实窗口 ``driver=windows``，2560×1440）：

1. 主菜单 → 点模式（自由混战）→ 点人数 +
2. 点「开始游戏」→ 选将界面 → 点武将卡 → 点「确认出战」
3. 开局发牌（逐张飞行）
4. 出牌阶段牌桌
5. 手牌悬停
6. 点「结束回合」→ 回合交出去
7. 逐个切到 5 档分辨率并截图

事件是通过 ``pygame.event.post`` 投进真实事件队列的，走的是 ``main.py`` 那条
真实循环 → ``handle_game_click`` → 引擎提交，**不是直接调引擎 API 绕过界面**。

真实窗口截图（13 张，见 ``docs/reports/assets/phase18_5_ui/``）：
``01_main_menu`` / ``02_general_select`` / ``03_general_chosen`` / ``04_dealing`` /
``05_table_play`` / ``06_hand_hover`` / ``07_ai_turn`` / ``08_table_late`` /
``res_1280x800`` / ``res_1366x768`` / ``res_1600x900`` / ``res_1920x1080`` /
``res_2560x1440``。

**机制场景**（11 张）用确定性构造 + 同一套真实 Renderer 渲染（SDL dummy 驱动）：
``02_target_selection`` / ``03_skill_major`` / ``04_skill_minor`` / ``05_judge`` /
``06_judge_replace`` / ``07_huogong`` / ``08_wugu`` / ``09_wuxie_chain`` /
``10_dying`` / ``11_identity_reveal`` / ``12_game_result``。

### 用户要求的 21 项，逐条对照

| # | 项目 | 状态 |
| --- | --- | --- |
| 1 | 开局发牌 | ✅ 真实窗口截图（逐张飞） |
| 2 | 普通杀 / 闪 | ✅ 引擎层（探针）+ 出牌界面（真实窗口） |
| 3 | 选目标 | ✅ 场景截图（`02_target_selection`） |
| 4 | 主动技能 | ✅ 场景截图（`03_skill_major`，MAJOR 横幅） |
| 5 | 触发技能 | ✅ 场景截图（`04_skill_minor`，轻量条）+ probe_skills |
| 6 | 乐不思蜀 | ✅ probe_judge + `05_judge` |
| 7 | 兵粮寸断 | ✅ probe_judge |
| 8 | 鬼才 | ✅ `06_judge_replace`（真实提交一次改判） |
| 9 | 天妒 | ✅ probe_chain（判定牌归属 + 提示排在判定之后） |
| 10 | 五谷 | ✅ `08_wugu`（含"XX 正在挑选"） |
| 11 | 火攻 | ✅ `07_huogong`（含放弃） |
| 12 | 南蛮 | ✅ `09_wuxie_chain`（南蛮触发的无懈链） |
| 13 | 万箭 | ⚠️ 引擎层与南蛮同一条路径（AOE 无懈窗口），**没有单独截图** |
| 14 | 无懈 | ✅ `09_wuxie_chain` |
| 15 | 多层无懈 | ⚠️ 链的**渲染**已验证（轮次点），但只跑出 1 轮；多层需要两个玩家都有无懈并连续使用，脚本没构造出来 |
| 16 | 濒死求桃 | ✅ `10_dying` |
| 17 | 装备 Tooltip | ⚠️ 避让逻辑已改并接入，**没有截图**（需要真实鼠标悬停在装备格上） |
| 18 | Identity 死亡揭示 | ✅ `11_identity_reveal` |
| 19 | GameResult | ✅ `12_game_result` |
| 20 | LAN Host | ✅ probe_lan 26/26 + probe_schema 53/53（含真实 socket） |
| 21 | LAN Guest | ⚠️ 同上，探针覆盖 HTTP 但**没有真实窗口截图** |

**没做到的**：真实鼠标手玩（人手点击）——这一步必须你来做。我的事件注入能证明
"真实窗口 + 真实事件路径下界面正确"，证明不了手感（按钮好不好找、动画快不快、
字号在真实显示器上累不累眼睛）。

---

## 25. 截图路径

``docs/reports/assets/phase18_5_ui/``（24 张）

* 真实窗口 13 张（``driver=windows``，事件注入驱动）
* 机制场景 11 张（确定性构造 + 真实 Renderer）
* 另有 Phase 18.4 的界面配色对照：``.cache/uicolors/shots_before`` 与
  ``shots_after``（改前/改后逐界面）

---

## 26. 下一步建议

按收益排序：

1. **真实人手验收这 24 张截图对应的界面**，特别是 1366×768 下的字号与
   技能横幅的停留时长——这是唯一机器验不了的部分。
2. **视觉层级 dim**：如果要让"当前焦点"更突出，正确的做法是给 Renderer 加一个
   全局 dim 通道，并且让演出队列统一申报"谁在让路"；这是一次需要
   ``interaction_hold`` / 判定面板 / 演出队列三方一起改的小重构。
3. **多层无懈的真实构造**：现在链渲染已验证，缺的是"两个玩家连续无懈"的
   deterministic 场景（需要给两个座位都发无懈并让 AI 按顺序使用）。
4. **装备 / 卡牌 Tooltip 的基本牌支持**：现在只有装备与锦囊会弹提示。
5. **把 ``FXTiming`` 的语义枚举化**（DRAW / PLAY_CARD / JUDGE / …），
   让"改某类动画的节奏"不用翻时序表。
