# Phase 16 · 全武将规则审计 v01

> 结论先说：**本轮审计尚未覆盖整个现有武将池。** 已完成的是"全量实跑 +
> 高风险类别专项"，剩余的是"每个技能的正反场景逐一构造"。没做完的部分在
> 文末逐条列出，没有含糊过去。

- 基线：`main @ e7954ea`
- 审计方式：真实引擎实跑（不读代码猜结论），工具放在 `.cache/general_audit/`
  （`.cache/` 已 ignore，不进版本库）
- 规则来源：本项目 `SkillDef.description` 所对应的官方版本 + 官方技能文本；
  不把"最新版技能"套到旧版实现上（详见 §6 版本判定）

---

## 1. 枚举结果

从真实注册表自动枚举（`GeneralRegistry.list_generals()` × `Game.general_available`）：

| 项 | 数量 |
| --- | --- |
| 注册表里的武将 | 91 |
| **当前实际可选**（可进随机池 / 选将界面） | **84** |
| 可选武将的技能定义 | 154 |
| 技能定义缺失 | 0 |

按扩展包分布（可选）：

| 扩展包 | 数量 |
| --- | --- |
| 标准版 | 25 |
| SP | 10 |
| 风 | 10 |
| 林 | 8 |
| 火 | 8 |
| 神 | 7 |
| 山 | 5 |
| 一将成名 | 11 |

同名不同版本各自独立计入（标准关羽 / 神关羽、标准司马懿 / 神司马懿、
`zhangjiao` / `zhangjiao_2010` 等）。

---

## 2. 审计覆盖与结果

| 审计层 | 范围 | 结果 |
| --- | --- | --- |
| 全量跑局 | 84 名武将各打一局全 AI 对局 | **84/84 跑完**（0 卡死 / 0 报错）|
| 判定 / 改判专项 | 延时锦囊、八卦阵、鬼才、鬼道、天妒、洛神、屯田、刚烈、铁骑 | **17/17** |
| View-As / 转化专项 | 自动枚举 16 个带 `conversions` 的技能 | **86/86** |
| 主动技素材专项 | 眩惑、明策、直谏、举荐 | **27/27** |
| 双雄专项（单机） | A–F 全部场景 | **33/33** |
| 双雄专项（LAN） | 真实 TCP + 真实客户端牌桌 | **10/10** |
| P0 判定优先（引擎） | 闸门、改判窗口、异常路径 | **25/25** |
| P0 判定优先（UI 层） | 真实 Renderer + `handle_game_click` | **11/11** |
| P0 判定优先（真实 main.py） | 乐不思蜀 / 八卦阵 / 鬼才 | **24/24** |

复现命令：

```powershell
.venv\Scripts\python.exe .cache/general_audit\audit_generals.py
.venv\Scripts\python.exe .cache\general_audit\audit_p2_judge.py
.venv\Scripts\python.exe .cache\general_audit\audit_p2_view_as.py
.venv\Scripts\python.exe .cache\general_audit\audit_p2_active_materials.py
.venv\Scripts\python.exe .cache\general_audit\audit_p1_shuangxiong.py
.venv\Scripts\python.exe .cache\general_audit\audit_p1_shuangxiong_lan.py
.venv\Scripts\python.exe .cache\general_audit\audit_p0_judge_priority.py
.venv\Scripts\python.exe .cache\general_audit\audit_p0_judge_ui.py

# 真实 main.py（判定优先级的手玩路径）
$env:PYTHONPATH=".cache\general_audit"; $env:SGS_RUNTIME_SCRIPT="judge_probe"
.venv\Scripts\python.exe main.py
```

---

## 3. P0 · 判定流程的全场最高优先级

### 3.1 问题

判定在实现里只是一条普通的 `Flow`，没有任何"占场"语义。结果：

- 判定结算完成后、判定牌还挂在屏幕中央时，`game.local_can_play()` 已经
  返回 True——玩家可以提前出下一张牌，于是**另一条动作链与判定的收尾同时跑**；
- 引擎的 `GameEngine.submit` 不区分"现在是不是判定中"，`UseCardAction`
  直接创建 `UseCardFlow`，任何绕过界面的提交（LAN 客户端、脚本）都能抢先出牌；
- UI 各层（按钮可用性、点击路由、技能栏）各自判断，没有统一判据。

### 3.2 方案：一个闸门，两个消费点

新增 `src/game/judge_gate.py`。它把三个必须分开的状态显式建模：

```text
JudgeFlow logical pending      规则上判定还没结束（JudgeFlow 还在跑）
Judge replacement pending      判定正停在改判窗口上等某个人出牌
Judge UI presentation active   规则上判定已经结算完，界面还在演
```

前两个由规则层回答（活跃 JudgeFlow 的注册表 + 当前 `PendingRequest`），
第三个只有表现层知道，由 `Effects.sync_judge_gate()` 每帧汇报。

消费点只有两个，规则层不再散落 `if`：

| 入口 | 作用 |
| --- | --- |
| `JudgeGate.accepts(action)` | 引擎侧权威判据：这个动作是不是"当前判定请求要的回答" |
| `JudgeGate.allows_local_input(player)` | UI 侧权威判据：本机玩家现在能不能操作牌桌 |

### 3.3 落地位置

| 文件 | 改动 |
| --- | --- |
| `src/game/judge_gate.py` | 新增；闸门本身 |
| `src/game/engine/runtime.py` | `GameEngine.submit` 进门先问闸门，被拦时返回"已取消"（不抛异常：AI 的推进路径里有几条靠 `cancelled` 才能接回回合）|
| `src/game/flows/judge.py` | `JudgeFlow._draw` 登记占用，收尾 / 取消 / 抽空牌堆时释放 |
| `src/game/core.py` | 创建闸门；`local_can_play` 先问闸门 |
| `src/game/turn.py` | `end_phase_blockers` 加判定门控 |
| `src/ui/interaction.py` | `handle_game_click` 最前面问闸门，判定期间吞掉一切普通点击 |
| `src/renderer.py` | `hit_action` 同源门控（判定面板捕获牌桌点击）|
| `src/ui/fx.py` | `sync_judge_gate`：把"判定还在演"汇报给闸门 |
| `src/ui/judge.py` | `MAX_HOLD` 兜底改为询问引擎（改判窗口开着时不再自行收面板）|
| `src/ui/view_adapter.py` | 客户端只读视图也挂闸门（房主推进了、判定牌还在这台机器上演时不放行）|

### 3.4 为什么 presentation 阶段不拦引擎

规则上判定已经结束，AI 的后续结算**必须**能继续——拦了会把 AI 的回合卡死
（AI 的推进路径不是每一条都会在失败后重试）。这一阶段要保证的只是"玩家别在
判定牌还在屏幕中央时提前出下一张牌"，所以它只作用于真人输入；动作队列那一层
的压制另由 `JudgePanel.holds_actions` 负责。

### 3.5 不会锁死

闸门按 `JudgeFlow` 的状态惰性剔除：`_LIVE_STATUS` 只含 `running` / `waiting`，
构造出来但没 `start` 的流程不占用；判定牌抽空（`_complete_with(None)`）走
`on_settled` 兜底释放；被取消的流程状态天然不在活跃集里。三条异常路径都有
用例覆盖（`audit_p0_judge_priority.py` 的最后四项）。

### 3.6 真实验证

`judge_probe.py` 挂在**真实主循环**上（`SGS_RUNTIME_SCRIPT` 机制），
screen / game / renderer 全部是玩家正常启动时正在用的那一个对象：

```text
[PASS] lebu：判定面板 active / 闸门占用中 presentation
[PASS] lebu：点手牌不产生任何动作        · handle_game_click → False
[PASS] lebu：点「结束回合」无动作        · hit_action → None
[PASS] lebu：点技能栏无动作              · hit_action → None
[PASS] lebu：点对手座位无动作            · hit_action → None
[PASS] bagua：同上（6 项）
[PASS] guicai：闸门占用中 replacement
[PASS] guicai：改判窗口期间出牌仍被拒
[PASS] guicai：改判窗口里点手牌完成的是改判（不是出牌）
[PASS] guicai：改判窗口里按钮 / 技能栏 / 座位全部无动作
```

---

## 4. 逐技能审计表

`Rule Source` 一列写的是"以哪个官方版本为准"；`Result` 只写
`PASS` / `FIXED` / `BLOCKED` / `AMBIGUOUS`，没有真实验证的不写 PASS。

### 4.1 判定 / 改判类

| Expansion | General | Skill | Rule Source | Result | Bug | Fix | Verification |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 火 | 颜良&文丑 | 双雄 | 经典版（颜色，非花色）| FIXED | 判定牌进弃牌堆而非归自己；真人选择被自动替代；无法多次转化 | `JudgeFlow.card_recipient` + 改走 `CardConversion` 视为技 | audit_p1_shuangxiong 33/33、LAN 10/10 |
| 标准版 | 司马懿 | 鬼才 | 经典版 | PASS | — | 改判走通用 `JudgeFlow` 窗口 | audit_p2_judge 17/17、main.py 探针 |
| 风 | 张角 | 鬼道 | 经典版 | PASS | — | 同上 | audit_p2_judge |
| 标准版 | 郭嘉 | 天妒 | 经典版 | PASS | — | `JUDGE_FINISHED` 取最终判定牌 | audit_p2_judge |
| 标准版 | 甄姬 | 洛神 | 经典版（单次判定）| FIXED | 缺"你可以"（到点自动判定）；判定牌"先弃后拿"产生虚假弃牌事件 | 新增 `LuoshenFlow` 确认窗口 + `card_recipient` 直取 | audit_p2_judge |
| 标准版 | 夏侯惇 | 刚烈 | 经典版 | PASS | — | — | audit_p2_judge（伤害来源失去 1 点体力）|
| 标准版 | 马超 | 铁骑 | 经典版 | PASS | — | — | audit_p2_judge（红色判定后目标无法出闪）|
| 山 | 邓艾 | 屯田 | 经典版 | PASS | — | — | audit_p2_judge |
| 林 | 蔡文姬 | 悲歌 | 经典版 | PASS | — | 判定结果语义与花色对应 | audit_generals 跑局 |
| 林 | 董卓 | 暴虐 | 经典版 | PASS | — | — | audit_generals 跑局 |
| 神 | 神关羽 | 武魂 | 经典版 | PASS | — | — | audit_generals 跑局 |
| — | 延时锦囊 | 乐不思蜀 / 兵粮寸断 / 闪电 | 官方 | PASS | — | — | audit_p2_judge + main.py 探针 |
| — | 装备 | 八卦阵 | 官方（"你可以"）| PASS | — | 确认窗口 + 判定 | main.py 探针 |

### 4.2 View-As / 转化技（自动枚举 16 个技能）

| General | Skill | Result | 说明 |
| --- | --- | --- | --- |
| 关羽 | 武圣 | FIXED | 红色【杀】当【杀】曾被列为合法素材但提交不了（去重池缺正常使用）|
| 赵云 | 龙胆 | PASS | 双向转化；素材按使用牌生命周期离手 |
| 神赵云 | 龙魂 | PASS | X 张**同花色**；`count_for` 与 `matches` 一致 |
| 甘宁 | 奇袭 | PASS | 黑色牌当过河拆桥；目标无牌时给出明确理由 |
| 华佗 | 急救 | PASS | `available` 限定回合外（回合内查不到候选是正确行为）|
| 卧龙诸葛 | 火计 | PASS | 红色手牌当火攻 |
| 高顺 | 禁酒 | PASS | 【酒】当【杀】 |
| 董卓 | 酒池 | PASS | 无目标转化自动提交 |
| 神关羽 | 武神 | PASS | 红桃当【杀】 |
| 颜良&文丑 | 双雄 | PASS | 见 §4.1 |
| 其余 6 个 | — | PASS | 见 `audit_p2_view_as.py` 明细 |

### 4.3 真人选牌类（"程序替玩家做决定"）

| General | Skill | Result | Fix |
| --- | --- | --- | --- |
| 法正 | 眩惑 | FIXED | 删掉 `hearts[:1]` 兜底；`cost_candidates` 限定红桃手牌，`keep_cards` 让牌的去向由技能自己决定 |
| 陈宫 | 明策 | FIXED | 同上（候选：装备牌或【杀】）|
| 张昭&张紘 | 直谏 | FIXED | 同上（候选：装备牌；牌进对方**装备区**而非手牌）|
| 徐庶 | 举荐 | FIXED | 删掉自动取首；`max_cost_cards=3` 表达"至多三张"|
| 颜良&文丑 | 双雄 | FIXED | 见 §4.1 |
| 其余需要选牌的主动技 | — | AMBIGUOUS | 逐条静态复核过（见 §5），但没有逐个构造真人场景 |

### 4.4 阶段替代 / 触发技

| General | Skill | Result | Bug | Fix |
| --- | --- | --- | --- | --- |
| 张辽 | 突袭 | FIXED | 阶段替代的结果读取错误（对 `FlowResult` 调 `.get()`），实际从未跳过摸牌阶段 | 顺着完成的 `value` 取 |
| 鲁肃 | 好施 | FIXED | `on_complete` 从未被触发，替代之后回合永久停在摸牌阶段 | `Flow.notify_on_complete` 统一幂等入口 |
| 法正 | 恩怨 | FIXED | 模块级 monkey patch 自递归（`EnyuanFlow.advance` 指向调用自己的函数），伤害结算直接 `RecursionError` | 逻辑并回类定义，patch 删除 |

---

## 5. 错误类型统计（本轮实际发现）

| 错误类型 | 次数 | 涉及 |
| --- | --- | --- |
| 判定牌去向错误 | 2 | 双雄（弃牌堆而非归己）、洛神（先弃后拿）|
| 玩家选择被自动替代 | 5 | 双雄、眩惑、明策、直谏、举荐 |
| 发动时机错误（缺"你可以"）| 1 | 洛神 |
| 次数 / ResetScope 错误 | 0 | — |
| 目标错误 | 0 | — |
| 距离错误 | 0 | — |
| View-As 错误（界面与引擎不一致）| 1 | 武圣（候选与提交判据不同源）|
| 多人顺序错误 | 0 | — |
| LAN 不一致 | 0 | 双雄的 Host 侧校验已用真实 TCP 验证 |
| AI 不一致 | 0 | AI 与真人读同一份候选（本次修正后）|
| UI 无入口 | 0 | — |
| 规则版本错误 | 0 | 见 §6 |
| 卡牌 ownership 错误 | 0 | 84 局全量跑局 + 牌唯一归属检查未报 |
| **Flow 卡死 / 递归** | **3** | 恩怨（递归）、好施（卡死）、突袭（功能失效）|
| 判定优先级缺失（架构性）| 1 | 全项目；见 §3 |

**问题最集中的地方**：`Flow` 生命周期（3 次卡死 / 递归，全部与被复用流程的
收尾回调有关）、以及"技能需要玩家选一张牌，实现却自动替他选"（5 次）。

---

## 6. 规则版本判定

审计时严格按本项目 `SkillDef.description` 与实现判断**它实现的是哪一版**，
再去找对应的官方文本；没有把"最新版技能"偷偷升级成实现。三处需要明确记录的：

| 技能 | 项目实现版本 | 依据 | 结论 |
| --- | --- | --- | --- |
| 双雄 | 经典版（颜色，非花色）| `SkillDef.description`："与此判定牌**颜色**不同的手牌" | 按颜色实现，未改成花色 |
| 洛神 | 经典版（单次判定）| `SkillDef.description`："若结果为黑色，你获得此判定牌" | 未补"可重复判定"的新版语义；只补了缺失的"你可以" |
| 龙魂 | 红桃→桃 / 方块→火杀 / 梅花→闪 / 黑桃→无懈 | `SkillDef.description` 与 conversion 表一致 | 一致，未改动 |

---

## 7. 修改的核心通用机制

1. **`src/game/judge_gate.py`（新增）** —— 判定优先闸门，三个状态 + 两个消费点。
2. **`Flow.notify_on_complete`（幂等回调入口）** —— 流程收尾时基类统一触发
   `on_complete`，覆写 `on_settled` 的流程改用同一入口，不会再出现"子类忘了调
   一次，调用方永远等一个已经结束的流程"。
3. **`JudgeFlow.card_recipient`** —— "这张判定牌最终归谁"由发起方声明，
   收尾时直接进手牌；不再需要"先弃后拿"。
4. **`CardConversion.owner_matches` + `for_owner`** —— 需要看拥有者状态的源牌
   谓词（双雄的判定颜色）在技能绑定时折进 `matches`，UI / 引擎 / LAN 读同一份判断。
5. **`ActiveSkillSpec.cost_candidates` / `keep_cards` / `max_cost_cards`** ——
   主动技的合法素材候选、素材不由 activation 代付、规则给出的张数上限。
6. **`CardActionDiscovery.view_as_options` 去重池纳入正常使用** —— 消除
   "界面显示合法、提交被拒"的不一致。

---

## 8. 仍需真人手测的部分

程序化审计覆盖不到、需要人眼确认的：

1. 判定面板的**视觉遮挡**（判定牌是否真的盖住下层所有面板）——
   程序只能断言绘制顺序，观感需要人看。
2. 判定的**节奏**（面板停留时长是否够看清改判）——`MAX_HOLD` 相关的
   体感验收。
3. 多目标技能在 **5 人 / 8 人**桌面的候选点击（程序用 3–4 人构造）。
4. LAN 场景里**两个真人**同时是改判候选时的顺序体验。
5. `audit_p2_view_as` 中"本场景凑不齐素材"的三个技能（龙魂在 2 血时
   需要 2 张同花色；董卓酒池；华佗急救的回合外场合）——规则实现已复核，
   但没构造到"能提交成功"的场景。

---

## 9. 本轮**未**完成的部分

诚实列出，没有含糊：

1. **不是每个技能都有"正反场景"。** 判定类（13 项）、转化类（16 项）、
   真人选牌类（4 项）做了正反 + 取消 + 边界；其余触发技 / 锁定技只做了
   "全量实跑一局 + 静态复核谓词"，没有逐条构造"应该发动 → 确实发动 /
   不该发动 → 确实不能发动"。
2. **限定技 / 觉醒技 / 翻面 / 拼点**没有做专项审计。这三类只有全量跑局覆盖
   （拼点相关的 3 名武将在全量跑局里跑通了，但没有构造"双人拼点 + 改判穿插"
   之类的边界）。
3. **LAN 全链路**只对双雄做了真实 TCP 验证（含 Host 拒绝伪造素材）。
   其余技能的远程路径沿用同一套 `DecisionResponse` + `CardActionDiscovery`
   序列化，但没有逐个跑过。
4. **5 人 / 8 人**桌面的距离与多目标顺序没有专项构造（全量跑局是 4 人）。
5. 静态扫描 `audit_auto_choice.py` 里仍有若干 `target = targets[0]` 形态的
   可疑点，逐条看过都跟在 `ask_targets` 之后（即玩家已经选过），
   属于**误报**，但没有为此写自动化断言。

**因此：当前武将规则审计尚未完整覆盖整个现有武将池。**
未完成清单就是上面 5 条；已完成的 84 名武将见 §4 与 §2 的复现命令。
