# Phase 11.3：逐人牌局视图 + 权威状态/事件同步

> 目标：让每个 Client 拥有一份**只读的、自己应该看见的**牌局视图，并由房主的
> 权威状态（Snapshot）与 Gameplay 事件（Event）驱动客户端既有的 Pygame 表现层。
>
> 一句话结论：**客户端仍然不是第二个规则引擎**——它只有一份数据视图 + 一层
> 表现适配 + 一个决策面板；隐藏信息在**生成视图/事件时**就已经不在数据里了。

---

## 1. Renderer 原本依赖什么数据（审计结论）

审计对象：`src/renderer.py`、`src/ui/{layout,table,seats,player,prompt,skill_bar,fx,judge,overlay}.py`。

| 展示数据 | 来源 | 客户端如何提供 |
|---|---|---|
| 座位几何 | `layout.TableLayout(game)`：`game.players`、`game.player`、`game.player.hand` | `RemoteGameView.players / player`（只读替身） |
| 座位内容 | `seats.draw_seat(player, ...)`：`name/seat/alive/hp/max_hp/hand(chained)/equipment/judgement_zone/general_id` | 同上的 `ViewPlayer` |
| 武将牌素材 | `game.generals.get(general_id)` | 复用**静态**武将表 `create_default_general_registry()` |
| 技能名 / 说明 | `game.skills.skill_ids_of(player)`、`game.skill_registry` | 视图里的公开技能 id + 静态技能表 |
| 牌堆 / 弃牌堆 | `game.deck.draw_pile`、`game.deck.discard_pile` | `ViewDeck`：只给张数 + 弃牌堆末尾若干张 |
| 中央出牌位 | `game.table_cards`（命名落位字符串） | 快照里的 `table_cards`（虚拟语义牌） |
| 公共牌区 | `game.selection_pool_entries()`、`game.public_card_pool` | 快照里的 `public_pool` / `selection` |
| 当前玩家 / 阶段 | `game.current_turn_player`、`game.phase` | 快照字段 |
| 战报 | `game.game_log` | 快照里的 `log`（房主本来就只留最后 8 条） |
| 结算 | `game.game_over`、`game.result`、`game.mode.result_lines()` | `GAME_RESULT` + 快照 `result` |
| 本地交互态 | `pending_* / choice / response / zhangba / card_actions` | **全部惰性化**（客户端没有这些概念） |
| 动画 / 特效 | `Effects`（订阅引擎事件）、`ActionQueue`、`JudgePanel` | 由**表现事件**驱动，同一套 `Effects` 代码 |

关键发现（本次沿用，未重做）：

* `Effects` 是纯表现的：`attach(game)` 只订阅事件、`update(dt)` 只推进内部计时、
  `owned_card_ids()` 是唯一的"视觉所有权"查询。
* `JudgePanel` 的绘制允许 `game=None`，判定语义全部来自静态声明表
  `judge_presentation.JUDGE_SOURCES`——所以判定结果**不需要过网**。
* `MoveCardAction` 的落位是**命名区域**（`"table_card"` / `"discard_pile"` …），
  没有像素坐标；客户端用自己的 `LayoutMetrics` 解析同名落位即可。
* `ActionQueue` / `MoveCardAction` / `JudgePanel` 都不依赖 `Game`（纯表现容器）。

---

## 2. ClientGameView 最终结构

`src/game/view/view_model.py`（纯数据，可 JSON 往返，可 `dataclass` 直接构造）：

```
ClientGameView
├─ match_id / revision / game_mode / mode_label
├─ local_player_id / host_player_id
├─ current_player_id / current_phase / responding_player_id / message
├─ game_over / result(ResultView) / log
├─ draw_pile_count / discard_count / discard_tail[]     ← 牌堆只有张数，没有顺序
├─ public_pool[] / table_cards[]                         ← 公开区域
├─ hand[]                                                ← 只有自己的手牌内容
├─ players: PlayerView[]                                 ← 逐人公开信息
├─ action(ActionView) / response(ResponseView)
├─ judge(JudgeView) / selection(SelectionView)
└─ decision(dict)                                        ← 房主认为我该做的决定
```

`PlayerView`：`player_id / seat / nickname / general_id / general_name / hp /
max_hp / alive / gender / kingdom / chained / controller / is_self / is_host /
identity（只含可见身份）/ hand_count / hand（仅自己）/ equipment{slot:ViewCard} /
judge_area[] / skills[]`。

`ViewCard`：`card_id / name / label / suit / rank / category / subtype / nature /
attack_range / description / face_down`。

**客户端不构造 `Game`**：`RemoteGameView`（`src/ui/view_adapter.py`）是一个**只读
展示替身**，没有 TurnFlow / DamageFlow / SkillManager / 牌堆随机 / AI / 合法性
判定；任何没有实现的属性访问都会抛出明确的 `AttributeError`（不是静默返回 None），
这样"某条绘制路径偷偷依赖权威状态"会立刻暴露。

---

## 3. Per-Player ViewBuilder

`src/game/view/view_builder.py::build_view(game, viewer_id, revision, ...)`：

* 输入：一份权威 `Game` + 观众 id；输出：这名观众的 `ClientGameView`。
* 同一帧调用两次、传不同 `viewer_id`，得到两份不同的合法视图（本阶段的核心证明）。
* 视图生成是**纯读**：不改任何游戏状态，也不认识任何技能。
* 附带产物：`action / response / judge` 由房主侧的 `PresentationBridge` 维护
  （"当前正在表现的出牌/响应/判定"），随快照一起下发，保证重同步后画面不丢。

隐藏信息过滤规则集中在 `src/game/view/visibility.py`（唯一出处）：

| 信息 | 规则 |
|---|---|
| 自己的手牌 | 本人：完整牌面 |
| 别人的手牌 | 只给 `hand_count`；`hand` 是空列表，**载荷里没有牌面** |
| 装备区 / 判定区 / 弃牌堆 / 结算区 / 桌面牌 / 公共池 | 所有人（公开区域） |
| 牌堆顺序 | 任何人都不给（只给张数） |
| 身份 | 走既有 `identity.visible_identity`：主公公开、阵亡公开、其余只本人知道、结算时按模式规则全公开 |
| "从别人手里拿/弃"的候选 | 本人的牌照常显示；别人的手牌只发牌背（`face_down=True`，连牌名都没有） |

---

## 4. 隐藏信息过滤：从"网络数据层"隔离

不是"发过去让 UI 不画"，而是**房主根本不发**：

* 快照：`visible_hand_cards(viewer_id, player)` 对非本人返回空元组；
  身份走 `visible_identity_of`；牌堆只有 `draw_pile_count`。
* 表现事件：`Fact` 分成 `base`（所有人共享）+ `private{player_id: {...}}`
  （只有特定观众才有的字段）。例如摸牌：

      base    = {player_id, count: 2, from_zone: draw_pile, to_zone: hand}
      private = {摸牌者: {cards: [两张具体牌]}}

  其他人拿到的载荷里**没有 cards 字段**。
* 牌移动：`_emit_move` 只在"离开隐藏区域又进入隐藏区域"（手牌→手牌、牌堆→手牌）
  时才隐藏牌面，并且牌面只发给**获得者**；手牌→弃牌堆（弃置）与手牌→结算区
  （出牌）都算公开。

验证方式：`tools/lan_view_sync.py` 直接审计**原始网络载荷**
（`ClientSide.raw_messages` 记录 socket 上收到的每一条消息），并用结构化审计
（`audit_hidden_cards`）检查"别的手牌 id/牌名有没有出现在任何位置"。

---

## 5. Snapshot / Event 架构

```
房主 Game ──► ViewBuilder(逐人) ──► GAME_VIEW_SNAPSHOT（最终事实）
          └─► PresentationBridge ─► GAME_EVENT（表现：动画/飘字/面板）
```

* **Snapshot 是唯一权威状态来源**：谁在哪、几张牌、多少血、什么阶段、结算结果。
* **Event 只负责表现**：卡牌从哪飞到哪、谁对谁用了什么、判定翻出了什么；
  事件永远不改权威状态，也永远不是状态来源。
* 事件是纯数据（dict），**不含屏幕坐标**：每台客户端用自己的 `LayoutMetrics`
  算动画落点（复用 `MoveCardAction` 的命名落位机制）。
* 状态与事件概念分离：快照重发不会重播动画（客户端按 `event_id` 去重）。

事件类型（`src/game/view/presentation.py`）：
`card_used / card_response / response_request / cards_moved / cards_drawn /
damage / recover / hp_lost / dying / death / chain / skill / turn_start / phase /
equipment / judge / public_pool / log / game_over`。

**一条重要工程约束**：表现桥的每个订阅都包了 `_guard()`——表现层出错**绝不能**
中断引擎的状态变更（分发是同步的；一次异常会让"牌已从处理区拿走、还没进装备槽"
这种半成品状态留在规则层）。异常会被记进 `bridge.errors`，由工具断言为 0。

客户端侧 `src/ui/client_fx.py` 把这些事件喂给**既有** `Effects` / `ActionQueue` /
`JudgePanel`：为此给 `Effects` 增加了一组**公开表现入口**
（`show_damage / show_recover / show_lose_hp / show_dying / show_death /
show_chain / show_skill / show_turn_start / show_card_used / focus_arrow /
note_draw / note_move / note_discard / judge_begin / judge_replace / judge_finish`），
房主侧的事件回调与客户端适配层**共用同一份实现**，不存在第二套动画代码。

---

## 6. revision / resync

* `HostMatch.revision` 单调递增，从 1 开始。
* 触发条件：**可见状态的紧凑指纹**发生变化（含每个人的手牌**身份**——顺手牵羊
  会改变手牌内容而张数不变），并且距离上次广播 ≥ `SYNC_INTERVAL = 0.08s`
  （避免 AI 连续行动时刷屏）；决策前 `push_views(force=True)` 强制刷新。
* 广播语义：**一次状态变化 = 一个 revision = 所有远程客户端各收一份自己的视图**。
  所以每个客户端看到的 revision 序列是连续的，出现跳跃就说明真的漏了消息。
* 客户端：`revision < 本地` 直接忽略（迟到的旧快照不能把状态拉回去）；
  `revision > 本地 + 1` 记为 gap → 发 `STATE_RESYNC_REQUEST`（同一轮只发一次，
  避免"重发→又当跳跃"的风暴）；快照本身是全量的，先采用它保证状态正确。
* 房主收到 `STATE_RESYNC_REQUEST`：重发该玩家的完整视图 + 当前待回答的决策，
  **不踢人、不改状态**。
* 决策顺序（§29）：`DECISION_REQUEST` 带 `base_revision`；客户端的本地
  revision 还没到 `base_revision` 时**不开放**该决策，先挂起并请求重同步。

---

## 7. Card Movement 如何映射 Presentation

* 引擎侧的牌移动统一出口是 `MoveCardAtom`（经 `apply_atom` 自动带
  `ATOM_BEFORE / ATOM_AFTER`），桥只订阅 `ATOM_AFTER`，**不在每个技能里发网络消息**。
* 区域识别集中在 `presentation.zone_descriptor(zone, game)`：把区域对象
  （`player.hand` / `player.judgement_zone` / `deck.draw_pile` / `deck.discard_pile` /
  `processing_zone` / `public_card_pool`）翻译成 `{zone, player_id, slot}`，
  再据此得出动作语义（draw / discard / transfer / play / pool_fill / judge_place /
  pool_take / equip / unequip）。
* 客户端把语义动作变成**本机动画**：`from_zone → to_zone` 各自解析成本机 rect
  （手牌区 / 座位边缘 / 牌堆 / 弃牌堆 / 中央出牌位 / 公共池），用既有的
  `MoveCardAction` 播飞行；内容未知的牌用"牌背占位牌"飞过去。
* 视觉所有权沿用既有机制：`Effects.owned_card_ids()` 决定静态区域这一帧不画哪张牌
  ——飞行动画中的牌与静态副本**不会同时出现**。
* **修了一个真实缺陷**：`GameEngine.place_table_card` 原来只在"牌本身在处理区"
  时登记桌面副本，View-As 的虚拟牌永远不在处理区，于是【龙胆】把【闪】当【杀】
  时中央**什么都不显示**，而收尾逻辑却会去 `remove_table_card(虚拟牌)`。
  现在判据是"牌的实体来源牌还在处理区"，中央稳定显示**语义牌本身**。

---

## 8. Action / Response / Judge 如何同步

**Action（出牌）**
`CARD_USED` → 事件带 `actor_id / card(语义牌) / target_ids / sequential /
skill_name / virtual`；View-As 时 `card` 是 `VirtualCard`（`label=杀`、`virtual=true`），
来源实体牌只作为 provenance 存在。客户端：移动动画 → 中央亮牌 → 指向箭头 →
响应 → `CARD_USE_FINISHED` 释放箭头（复用既有 FX 顺序，不重写）。

**Response（响应）**
`PENDING_RESOLVED`（仅 `respond_card` 类请求，避免与选牌类的 ATOM 事件重复）→
`card_response {player_id, card, label}`。客户端：响应牌从手牌飞到中央响应位、
停留片刻、再飞进弃牌堆，同时打出动作横幅。

**Judge（判定）**
`JUDGE_STARTED / JUDGE_REVEALED / JUDGE_REPLACED / JUDGE_RESULT / JUDGE_FINISHED`
→ 客户端的 `JudgePanel`：
* 开始/翻牌 → `begin()`；改判 → `note_replacement()`（旧牌 → 新牌 + "改判 N 次"）；
  最终结果 → `finish()`（结果语义 tone/title/text 由房主算好随事件下发，
  展示文案（来源名 / 规则文本）由客户端从静态声明表本地查）。
* 客户端的改判历史由事件累积（`ClientPresentation.judge_history`），
  最终结果一起交给面板——否则 `finish()` 会把历史清空，"被改过"标记会丢。

**Damage / Recover / Lose HP**
`DAMAGE_APPLIED` → `damage`；`RecoverHpAtom` → `recover`；`LoseHpAtom` → `hp_lost`
（同一次结算里伤害已经记过账的会被吸收，客户端不会播两次飘字）。
HP 的最终事实永远以快照为准。

---

## 9. View-As（龙胆 / 武圣 / 急救）

远程玩家的第二段交互（先点技能、再选实体牌）现在**真的支持**：

* 出牌阶段的可选牌里，每张牌带 `options`：直接使用 + 每个技能的转化方式
  （`skill_id` / `skill_name` / `result_display` / `min_sources` / `max_sources`），
  多 source 转化（丈八蛇矛一类）会带 `min_sources > 1`，客户端多选几张再确认。
* 响应 / 救援窗口的候选同样带转化方式：`_context_for_request` 按请求原因选择
  `response_context` / `rescue_context`，因此【龙胆】当【闪】、【武圣】当【杀】、
  【急救】当【桃】都会出现在候选里。
* 客户端**只回"哪几张实体牌 + 哪个技能"**（`DecisionResult.skill_id`，
  由 `DecisionRegistry` 校验技能必须在房主列出的 `allowed_skills` 内）；
  虚拟牌由房主用 `CardActionDiscovery.effective_card(option)` 现造。
* 中央展示的是**语义牌**（【杀】），来源【闪】不会作为第二个主体出现；
  其他客户端也不会因为 View-As 拿到不该知道的隐藏信息（来源牌此刻在结算区，
  本来就是公开的）。

---

## 10. Identity 隐藏验证

* 视图里 `PlayerView.identity` 只包含"这名观众有权看见"的身份（`visible_identity`）。
* 覆盖身份模式（`game_mode != "ffa"`）+ 5 人局：主公公开、其余未公开身份
  **既不在视图里、也不在原始载荷里**，每个人仍然看得到自己的身份。
* 验证：`tools/lan_view_sync.py` 的真身 5 客户场景（raw payload 关键词审计）。

---

## 11. 多客户端不同 View 验证

* 同一份权威 `Game`：A 手上【杀】【闪】→ A 的载荷里有这两张牌的 card_id；
  B 的载荷里**一张都没有**，只看到 A 的 `hand_count = 2`；房主自己（第三名玩家）
  视角同样只有张数。
* 两份视图的 JSON 序列化结果不相等（逐人视图确实不同）。
* 5 人身份局：4 个客户端各收到一份合法视图。

---

## 12. 实际运行结果

| 验证入口 | 结果 |
|---|---|
| `tools/lan_view_sync.py`（场景 1～7、14、15：视图/隐藏信息/revision/resync/身份/多客户端） | **48/48 通过** |
| `tools/lan_presentation_sync.py`（场景 8～17：出杀/响应/伤害/装备/顺拆/判定/改判/龙胆/急救/五谷） | **87/87 通过** |
| `tools/lan_smoke.py`（Phase 11.1 大厅回归） | 44/44 通过 |
| `tools/lan_gameplay_bridge.py`（Phase 11.2 决策桥回归） | 41/41 通过 |
| `tools/lan_gameplay_ui.py`（Phase 11.2 真实界面回归，已改为驱动新客户端牌桌） | 23/23 通过 |
| `tools/lan_two_process.py`（真实双进程联机） | 11/11 通过 |
| `tools/ui_smoke.py`（单机 UI：选将/出牌/View-As/回合/重开/回菜单） | 通过 |
| `python -m unittest discover -s tests -t .` | **1012 个测试全通过** |

两个新工具都是**真实链路**：真实 TCP socket、真实 `Game` / TurnFlow / 技能流程、
真实 `Renderer`（SDL dummy）画每一帧、决策由真实点击事件产生
（`lan_gameplay_ui.py`）或同一条 `ClientMatch.answer()` 通道发出（两个新工具）。
不 mock 网络、不 mock 引擎、不 mock 渲染。

可视证据：`tools/ui_snapshots/phase11_3_*.png`（客户端牌桌 17 张，含判定面板、
改判、龙胆中央语义牌、五谷公共池、装备、顺手牵羊等）。

---

## 13. 尚未实现的联网功能（本阶段明确不做）

* **掉线重连 / AI 托管 / 中途加入 / 观战 / 回放**：维持 Phase 11.2 策略
  ——正式对局中掉线即终止本局、回大厅并说明原因。
* **增量 Delta**：只做 Snapshot + Event（§14 允许；§39 明确不要复杂 patch）。
* **完整交互覆盖**：观星排序、流离、仁德、制衡等复合技能的远程交互仍按
  「引擎已支持的 Pending 类型」走（`RESPOND_CARD / CONFIRM / CHOOSE_OPTION /
  SELECT_CARDS / SELECT_TARGETS` + 出牌阶段），没有为每个技能做专门面板。
* **无懈链的多段远程展示**：无懈链本身可用，但链上每一环的"亮牌 → 询问"是
  逐条事件驱动的，没有额外的链式时间轴。
* **火攻展示牌**等少数只在本地真人路径出现的展示，还没有专门的表现事件。
* 客户端不显示"别人手牌的具体内容"（这是设计），也不允许客户端预测/回滚。

---

## 14. 下一阶段建议（Phase 11.4）

1. **40 个技能的远程交互覆盖**：把「一个技能需要多段决策」的情形（观星排序、
   流离、仁德、制衡、急救、鬼才、无懈链、五谷、濒死求桃）逐一在联网下过一遍，
   把缺口补成通用的 Decision 类型（而不是按技能特判）。
2. **掉线恢复**：座位保留 + 重连令牌 + 断线 AI 托管（本阶段刻意不做）。
3. **表现时间轴**：现在事件是"到了就播"，没有服务端权威节奏；若要严格对齐
   （例如"先看清判定牌，再让所有人看到改判"），需要一个事件排队 + 按持续时间
   出队的调度层（`ActionQueue` / `WaitAction` 已是现成骨架）。
4. **客户端指令校验的收口**：把 `RemoteHumanController` 里"可出牌 / 可响应"
   的展示计算与真正提交时的复核继续收敛到同一处，减少两条路径不一致的可能。
5. **观察者/观战视图**：`build_view` 已经天然支持"任意 viewer"，只需一个
   `viewer_id = spectator` 的可见性策略。
