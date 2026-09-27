# Presentation System 2.0（UI 演出系统升级）

本轮**不新增武将 / 技能 / 卡牌，不改 GameEngine 的规则语义**，只把现有 Pygame
项目的演出层升级成统一、可排队、可跳过的系统。规则与网络照旧：**表现层只读
状态、只画画面**，任何动画都不参与规则判定，客户端也不因为动画改动 GameState。

---

## 一、架构变化

### 1.1 新增模块（六件事，各管一段）

| 模块 | 职责 | 关键约束 |
| --- | --- | --- |
| `src/ui/tween.py` | **唯一**补间实现：缓动函数表 + `Tween` / `PointTween` + `TweenTrack` / `PointTrack` + `TweenManager` | 不导入 pygame、不知道规则；目标不变时不重新计时；改目标从当前值出发 |
| `src/ui/anim_config.py` | **唯一**界面时长表（`BASE_DURATIONS`）+ 速度档换算 | 速度只有这一处生效；规则 timeout 不在表里、也不乘倍率 |
| `src/ui/input_lock.py` | **唯一**输入优先级判据（`InputPriority` / `InputLock` / `resolve`） | 纯查询；不做全局粗暴禁用（`ALWAYS` 层永远可点） |
| `src/ui/card_transfer.py` | **唯一**实体牌移动表现（`CardTransfer` / `CardFlight` / `CardTransferPresentation` + 八个牌区词汇） | 不移动数据；`owned_card_ids` 决定"谁独占画这张牌" |
| `src/ui/toast.py` | 普通信息提示（不拦点击、同 key 合并） | 不用模态框 |
| `src/ui/hand_motion.py` | 手牌位置补间（悬停 / 选中 / 重排） | 命中测试用的就是它算出来的 rect |
| `src/ui/debug_overlay.py` | F1 演出调试面板（默认关闭） | 只读；不提供任何"跳过规则"的操作 |

### 1.2 三层分工（这一轮把它写死）

```
ActionQueue                 实体牌的移动动画（谁飞到哪、飞多久）
JudgeGate / PresentationGate 现在允不允许玩家操作、规则要不要等演出
PresentationQueue（storyboard）画面现在轮到谁演
Tween / CardTransfer / Toast 具体怎么画（本轮的统一实现）
```

三者都不 sleep、都不碰规则数据。演出队列唯一的"压制"是 `holding` /
`interaction_hold()`：**只延后界面，不阻塞引擎**。

### 1.3 规则侧的唯一改动：一条事件

无懈抵消原本只在 `game.message` 里有一句文案，UI 要么读文案、要么猜。本轮加了

```python
EventType.CARD_NULLIFIED = "card.nullified"      # src/game/engine/events.py
```

由 `UseCardFlow._after_wuxie` 在**结论已经定案**之后发出（`use_card.py`），表现层
据此播"【XX】被【无懈可击】抵消"。这是"规则先产生确定结果，再播放对应动画"的
落实点：界面不解析文案，规则也不画画面。

---

## 二、Tween System

统一支持：**位置、缩放、透明度、旋转、数值**（旋转走同一条标量 `Tween`，由调用方
解释），缓动有 `linear / ease_in_quad / ease_out_quad / ease_in_out_quad /
ease_in_cubic / ease_out_cubic / ease_in_out_cubic / ease_out_back / ease_in_back /
ease_out_elastic`。

两条容易踩的语义（都写了注释并有自检）：

1. **目标不变时不重新计时**。调用方每帧都会把"这一帧算出来的目标"喂进来，
   无条件 retarget 会把计时反复归零，动画永远走不到终点（实测：手牌停在半路）。
2. **首次出现默认"就位"**（`to` 不给 `start`），需要首帧也补间就显式给 `start`。
   按钮补间因此显式传 `start=1.0 / 0.0 / 1.0`——不给的话第一帧就跳到目标值，
   悬停没有动画（自检 1.5 / 1.6 与按钮那一条就是这么发现的）。

---

## 三、动画统一时间

`anim_config.BASE_DURATIONS`（默认速度档下的秒数；实现在 `anim_config.py`）：

| 动作 | 秒 | 动作 | 秒 |
| --- | --- | --- | --- |
| hover | 0.12 | damage | 0.30 |
| button_press | 0.08 | heal | 0.30 |
| card_select | 0.15 | skill_banner | 0.65 |
| card_move | 0.28 | turn_banner | 0.55 |
| card_use | 0.35 | judge_reveal | 0.45 |
| modal | 0.18 | hand_reorder | 0.22 |

换算与既有档位**同源**：`倍率 = speed / 0.75`（`Game.SPEED_STEPS =
0.4/0.55/0.75/1.0/1.5/2.0`），`时长 = 基准 / 倍率`，上下限 0.35x–2.5x。
速度档只有这一处生效点：表现层每帧 `anim_config.set_speed(game.speed)`，按钮 /
提示 / 手牌上浮与演出队列走同一个节奏。

**规则 timeout 完全不受影响**：判定面板的 `MIN_STAGE`、`JudgeGate` 的兜底上限、
`PresentationGate.MAX_HOLD` 都不在表里，也没有任何地方拿它们乘倍率（自检 2.5）。

---

## 四、PresentationQueue

沿用 Phase 18 的 `storyboard.PresentationQueue`，本轮补两件：

* **`flush(keep_blocking=True)`** —— 强制放行：丢掉排队中的**非关键**演出，
  让关键演出立刻上台。联网客户端在 `turn_start` 时调用（房主的规则时间线不等
  客人，客人的画面不能一直落后；判定 / 濒死 / 阵亡一律保留）。
* **`skip_current()` + `fast_forward`** —— 见第七节。

队列仍然是唯一的"画面顺序"：**出牌 → 目标高亮 → 结算 → 弃牌**由队列顺序保证，
不允许任何模块抢画面。诊断在 F1 面板里：`current / pending / played / dropped`。

---

## 五、InputPriority（输入锁）

层级（`input_lock.InputPriority`）：

```
JUDGE 100 > MANDATORY_RESPONSE 80 > MODAL 70 > TARGET_SELECTION 60
          > CARD_SELECTION 50 > NORMAL_PLAY 20 > AMBIENT 10 > ALWAYS 0
```

* **判定期间**：判定层最高，全体玩家不能普通出牌、不能选目标；只有判定流程自己
  要的输入能进去（那一条仍由 `judge_gate` 判定，本模块只是转述）。
* **演出期间不是全局禁用**：`ALWAYS` 层（动画速度、跳过演出、F1）任何时刻都可点。
  这是"点了没反应"的主要来源，必须在架构上留出口。
* 点击路由的两处入口（`renderer.hit_action` / `interaction.handle_game_click`）
  现在都读同一个 `resolve()`，不再各自推导。

---

## 六、CardTransfer（实体牌移动）

八个牌区：`deck / hand / table / judge / equipment / discard / public_pool / seat`。
每次移动声明 `source_zone → destination_zone + visibility`，落点由表现层每帧注入
（分辨率变化同一帧生效），飞行带轻微上抛弧线与"起飞淡入 / 落位淡出"的交接。

本轮接进去的路径：

* 开局发牌、摸牌（原有，改走统一实现，落点改"牌中心"语义）；
* **获得判定牌 / 技能拿牌**（`note_card_moved`）：以前是"牌瞬间消失、手牌数 +1"；
* **装备**（手牌 → 对应装备槽，槽位落位脉冲）；
* **五谷选走 / 剩余牌进弃牌堆**；
* 弃牌堆永远画牌背（不泄露内容），别人手牌同理。

`owned_card_ids()` 仍是唯一的视觉所有权查询：飞行中的牌，静态区域一律不再画。
发现并修掉一个**静默失效**：落点解析的 key 形式与写入端不一致时不会报错，只是
"装备 / 获得牌"这类移动悄悄没有动画（见已知问题 §13.1）。

---

## 七、Skip Animation（只跳动画，绝不跳规则）

* `Space`（对局中）或**演出让路期间点击画面任意处**；
* 实现是打开一个 0.35 秒的高速窗口（`storyboard.fast_forward`）+ 立刻推完牌移动 /
  界面补间 / 判定面板的展示阶段；
* 队列里排着的演出仍会被依次**开始**（各自的 `start` 都会跑到），因此依赖"演出
  开始"的收尾逻辑（释放箭头、关闸门、清面板）不会被绕过；
* **不提交任何 DecisionResponse、不推进引擎、不改任何游戏数据**（自检 6.3）。

---

## 八、判定 / 技能 / 伤害 / 濒死 / 死亡

* **判定**：仍是最高优先级演出，整段（面板 + 结论条）让规则让路；本轮加了
  **背景轻微压暗**（0→1 补间，`Effects.background_dim`），判定面板因此成为视觉焦点。
* **技能 Banner**：主动技 / 视为技是大横幅（武将卡 + 技能名 + 类型 + 说明），
  锁定技 / 高频触发技是轻量条；同一个技能连续触发会合并（不刷屏）。
* **伤害**：属性（`normal / fire / thunder`）随事件下发，飘字带属性符号、座位叠一层
  属性染色（火焰偏橙、雷电偏紫），配合闪光 + 抖动。**不做重型粒子系统**。
* **回血**：绿色 ✚ 飘字；体力显示值走补间（血点一格格稳定地掉 / 涨，不跳变）。
* **濒死**：座位危险脉冲 + **屏幕中央"XXX 进入濒死 / 等待【桃】救援"**（只靠座位上
  一个小角标，其他人根本不知道轮到自己救人）。
* **死亡**：座位暗化 + 身份揭示演出 + 手牌 / 装备按真实规则分别飞向对应区域 +
  保留灰色"阵亡"（不瞬间删人）。

---

## 九、五谷 / 无懈 / 火攻 / 装备 / 阶段

* **五谷丰登**：公共牌是真实卡面；新翻出的牌**从牌堆逐张飞入**（`sync_public_pool`），
  被选走的牌飞向玩家手牌，剩余牌**平滑重排**（位置补间），绘制与命中用同一份 rect。
* **无懈可击**：链路界面（第几层、谁在响应）沿用 `wuxie_chain`；本轮补上**结论条**
  "【XX】被【无懈可击】抵消"（走新的 `CARD_NULLIFIED` 事件）。
* **火攻**：专用 `HuogongPanel`（左栏展示牌 / 右栏可弃牌 / 花色匹配提示）；手上
  没有同花色牌时面板明确写出"你手上没有与展示牌同花色的手牌，无需弃置"。
* **装备**：手牌 → 槽位飞行 + 槽位落位脉冲（换装时看清"新牌进了哪个格"）。
* **阶段指示**：换阶段时文字**滑入 + 淡入**（`phase_change` 补间），跳过阶段仍有
  明确的结论条。
* **换回合**：中央横幅（"XXX 的回合"）+ 旧当前玩家高亮淡出、新高亮增强。

---

## 十、Hover / 按钮 / Tooltip / Toast

* **手牌**：悬停平滑上浮（设计值 28）、选中 40，取消平滑回位；出牌 / 摸牌后的
  **重排是移动过去的**，不是瞬移；摸到的牌由飞行动画落位（手牌区在飞行期间不画它）。
* **命中测试永远用"画出来的那一份 rect"**：手牌补间期间点到的仍然是眼睛看到的那张。
* **按钮**：hover 放大 1.02 + 亮度 +14%，pressed 缩到 0.97，disabled 降饱和 + 降亮度，
  全部走补间（不再瞬间换色）。
* **Tooltip**：`TooltipManager` 是唯一出口，**悬停 200ms 才出现**、同时只显示一份
  （技能 / 装备 / 角色状态 / 卡牌 / 按钮共用一个通道），划过不再闪。
* **Toast**：普通信息（"不是你的回合"/"等结算完成再操作"/"现在是弃牌阶段"）走 Toast，
  同 key 合并刷新，**不用模态框**。

---

## 十一、LAN

* 客户端与房主**共用同一套 `Effects`**（`client_fx` 只把网络事件翻译成语义调用），
  因此两端画面结构一致（实测截图：客户端有同样的技能条 / 高亮 / 回合横幅）。
* 动画速度是**客户端本地设置**：`RemoteGameView.speed` 与 `Game.speed` 互不同步，
  客户端调 2x 不影响房主规则。
* 客人画面落后时：`PresentationQueue.flush(keep_blocking=True)` 在换回合时丢掉
  **非关键**演出（判定 / 濒死 / 阵亡一定保留），避免"我在看上一回合，对面已经摸牌"。
* 队列积压仍有上限（`MAX_PENDING`，丢最老的非关键演出），不会无限堆积。

---

## 十二、F1 调试面板

默认关闭（`debug_overlay.ENABLED`）。显示：演出队列（当前 / 排队 / 已播 / 丢弃）、
输入层与优先级、动画速度与倍率、待回答请求、当前响应者、飞行 / 箭头 / 飘字 / Toast
计数、`story_errors`。只读，**不提供任何推进规则的操作**。

---

## 十三、实际手玩验证

工具：`tools/present_acceptance.py`（真实主循环 + `runtime_hook`，事件注入走
`handle_game_click`，与真人点击同一条路径）。命令：

```bash
SGS_RUNTIME_SCRIPT=present_acceptance SGS_PA_SECONDS=150 python main.py
```

一次 5 人身份局的实测（`tools/ui_snapshots/present/report.json`）：

* 9066 帧、660 次真实点击、**0 失败事件**、`story_errors` 为空；
* 演出队列：播放 146 条、丢弃 0、峰值积压 12；
* 覆盖到的演出：技能横幅 / 判定 / 阶段跳过（乐不思蜀）/ 濒死 / 阵亡 / 结算界面；
* 覆盖到的输入层：`normal / mandatory / selection / judge / presentation / game_over`；
* 截图逐张人工核对：判定面板（含压暗与判定来源）、濒死（座位红框 + 中央求助文案 +
  响应者高亮 + 手牌【桃】许可高亮）、结算（全员武将 + 身份 + 存活/阵亡）、
  客户端 LAN 牌桌（技能条 / 手牌许可高亮 / 回合横幅）。

**响应式**：`SGS_PA_SIZE=1280x720,1920x1080,1600x900` 三种尺寸下自动断言
"关键元素（座位 / 手牌 / 提示条 / 按钮 / 判定面板 / 技能横幅 / 公共池）全部在屏内" ——
三种尺寸各 16–17 项，全部通过（`report.json` 的 `sizes` / `out_of_screen`）。

**LAN**：

* `python tools/lan_smoke.py` —— 44/44 通过（真实 socket + 真实界面路径，无窗口）；
* `python tools/run_local_lan_pair.py --capture` —— 两个真实 `main.py` 进程走 127.0.0.1，
  退出码均为 0，房主与客户端各自截图成功（LOCALHOST MULTI-PROCESS）。

**自检**（可重复跑）：

```bash
python tools/presentation_audit.py      # 50 项，本轮新增
python tools/input_priority_audit.py    # 45 项，Phase 18.5 既有
```

---

## 十四、已知问题

1. **落点解析静默失效（本轮已修，但值得记住）**：`CardTransferPresentation._resolve`
   的 key 形式必须与写入端完全一致；不一致时不报错、只是"这类移动没有动画"。
   自检 4.1 现在会挡住它。
2. **手牌命中区的形状是刻意的**：命中区 = 显示位置 ∪ 其正下方一个上浮高度。
   早先写成"基础排列 ∪ 显示位置"，重排过程中每张牌的命中区会横向张大并互相侵占，
   点到的不是眼睛看到的那张（自检 3.7b 挡住）。
3. **判定面板的时长下限是绝对的**：`MIN_STAGE` 不随速度档缩短，2x 档下判定也不会
   一闪而过。这是有意为之，但它意味着"最快档也不是全速"。
4. **五谷公共池的"逐张飞入"按 0.05s 递进**：牌很多（8 人局）时最后一张要等约 0.4s；
   实测可接受，但如果以后公共牌更多，需要改成"整批错开 + 更短的间隔"。
5. **旋转补间没有实际使用点**：`Tween` 支持，但当前界面没有旋转动画（身份牌翻转
   是既有实现，走自己的 alpha 序列）。留着是为了下次要翻转时不再各写一套。
6. **火攻 / 部分扩展包技能界面没有走本轮的统一时长表**：它们有自己的面板节奏
   （`huogong.py` 的固定值）。功能正常，但严格说还没有全部收口到 `anim_config`。
7. **`DEBUG_UI`（旧调试文本）与 F1 面板并存**：前者是历史开关（默认关闭），
   没有清理，避免影响既有工具。
8. **验收工具会真实打完一局**（默认 150 秒上限），耗时较长；它顺便验证了"长局不会
   因为演出队列卡死"。

---

## 十五、回归与提交

* 未改动任何武将 / 技能 / 卡牌定义，未改动 LAN 协议、未重写 Renderer。
* 规则层唯一改动：新增 `CARD_NULLIFIED` 事件（无懈抵消的结论声明）。
* 自检：`presentation_audit` 50/50、`input_priority_audit` 45/45、
  `lan_smoke` 44/44、真实一局 0 失败事件。
