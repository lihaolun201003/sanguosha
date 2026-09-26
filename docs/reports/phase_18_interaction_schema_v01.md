# Phase 18：交互契约（InteractionSchema）与演出契约（PresentationSchema）

> 本轮的目标不是"多写几层抽象"，而是把**同一个界面在单机与联机下必须长得一样**
> 这件事从"两边各自实现、靠自觉保持一致"变成"只有一处判据"。
> 规则一条没改：所有验证都跑在真实引擎 + 真实 Renderer 上。

---

## 1. BASELINE_COMMIT 与上传情况

| 项 | 值 |
| --- | --- |
| 基线提交 | `e04cd2a7faf138dc4f518e0565ef8a69007f832d` |
| 提交信息 | `backup: 保存 Phase 18 架构改造前稳定版本` |
| 上传目标 | `origin/main`（https://github.com/lihaolun201003/sanguosha） |
| push 结果 | 成功：`46007c0..e04cd2a  main -> main` |
| 一致性验证 | `git ls-remote origin main` = `git rev-parse HEAD` = **e04cd2a**（逐字节一致） |
| 上传内容 | 92 个文件（53 个改动 + 39 个新增，含全部报告与武将胜率数据） |

基线备份前做的检查：

* 分支 `main`，无游离 HEAD；
* `.gitignore` 生效：`.venv/`、`__pycache__/`、`.cache/`、`/assets/`（约 50MB 卡面扫描图）、
  `/user_preferences.json`、`/tools/ui_snapshots/`、`.env*`、`*.pem/*.key` 均未进库；
* 未跟踪文件全部是源码与报告数据，最大 780KB，无二进制大产物；
* 敏感信息扫描：唯一命中的 `tools/general_identity_winrate.py` 是**误报**——
  那里的 `token` 指"逐局发牌用的抽样令牌"，不是凭据；
* 被跟踪文件里最大的一个是 `src/game/core.py`（81KB 源码），仓库里没有任何大文件。

---

## 2. 架构改动前后的真实调用链

### 改造前（问题所在）

"现在该显示什么界面"有**两条互不相通的实现**：

```text
房主（联机）  PendingRequest
                 └─ RemoteHumanController._build_pending_request   ← 实现 A
                        └─ DecisionRequest(JSON) ── socket ── Guest
                                                                 └─ decision_presentation
                                                                      └─ view_adapter
                                                                           └─ Renderer

本机（单机）  PendingRequest
                 └─ HumanController.present                        ← 实现 B
                        └─ game.response / game.choice / game.start_card_selection
                             └─ Renderer
```

两份实现只要有一点不一致，同一个窗口在单机与联机下就是两个界面。实测到的
真实分叉（都是本轮修掉的）：

| 分叉 | 单机 | 联机 |
| --- | --- | --- |
| 改判窗口能不能"跳过" | 没有取消按钮 | 恒有"跳过"按钮（`view_adapter.can_cancel_pending_selection` 直接 `return True`） |
| `min_cards = 0` 的语义 | 允许选 0 张 | 被改写为"必须选满"（`decision_presentation` 里 `minimum = maximum if minimum <= 0 else minimum`） |
| 技能提示的类型/说明 | UI 查本地 `SkillDef` | 规则层随事件下发（两条路径，只是碰巧显示一样） |
| 火攻专用面板 | UI 按 reason 白名单 `("huogong_reveal","huogong_discard")` 认领 | 同一份白名单 |
| 火攻规则文案 | 写死在 `ui/huogong.py`："受到 1 点火焰伤害" | 同上（写死同一段） |

演出侧同样是两套词汇：本机是引擎事件 → `Effects._on_*` → `storyboard.submit(Step)`，
联机是引擎事件 → `PresentationBridge` → `EV_*` dict → `client_fx` → 同一个
`storyboard.submit(Step)`。两条链**最终汇聚在同一个队列上**（这是上一轮 Phase 18
建立的），但"有哪几种演出"在马甲上没有任何约束：本地加一个新演出、忘了在网络桥里
加一条，不会有人发现。

### 改造后（唯一判据）

```text
Game / Flow
   ├─ build_interaction(game, request) ──→ InteractionSchema ─┬─ 本机 HumanController.present
   │                       （唯一构造口）                      │      └─ start_card_selection / response / choice
   │                                                          └─ RemoteHumanController ──→ DecisionRequest(JSON)
   │                                                                   └─ socket ──→ Guest: view_adapter → Renderer
   └─ 引擎事件 ──→ PresentationSchema ─┬─ 本机 Effects（storyboard）
                                       └─ PresentationBridge（EV_* → 网络 → client_fx）
                                              └─ 两边都走 storyboard
```

* `InteractionSchema` 的**唯一构造口**是 `src/game/contracts/interaction.py::build_interaction`；
  全项目只有它一处。单机与联机读的是同一份对象（联机只是把它序列化）。
* `PresentationSchema` 由 `contracts/presentation.py::KIND_BY_ENGINE_EVENT` /
  `KIND_BY_FACT` / `KIND_BY_STEP` 三张表共用同一套词汇；`missing_fact_kinds()`
  会在自检里指出"网络侧多了一种演出而契约表没跟上"。

---

## 3. 从 mine-monopoly 学了什么 / 明确没有照搬

**学的**（都是架构层面的思路，不是代码）：

| MineMonopoly 的做法 | 本项目对应实现 |
| --- | --- |
| `UISchema` 声明式 UI | `InteractionSchema`：规则层声明"要谁做什么、能点什么、长什么样"，UI 只消费 |
| GameProcess 与 UI 解耦 | 演出的 `PresentationSchema` 双消费者（本机 Effects / 网络桥），队列与规则互不反向依赖 |
| Command / Modifier | 沿用本项目已有的 `Flow` + `ModifierKind`，**没有**引入 Command 容器（见下） |
| 状态与展示职责分离 | `RuntimeState`（4 态）+ `PresentationGate`：纯视觉动画不改变权威逻辑，只有重要演出才让规则让路 |
| 视觉层级 / 焦点 | 判定面板、技能条、火攻面板的标题 / 阶段 / 文案改由规则层声明，三块专用界面因此有统一的来源与口径 |

**明确没有照搬**：Vue、TypeScript、Electron、WebRTC、Three.js、MySQL、
浏览器架构——本项目继续是 **Python + Pygame 桌面游戏**，没有引入任何前端框架、
没有引入 Node 工具链、没有把 UI 改成网页风。

**也没有引入的**：一个"Command 总线"。规则层已经有唯一入口
（`GameEngine.submit` + `PendingManager`），再加一层 Command 容器只会让
同一个动作有两个身份。本轮的抽象都落在"契约"上，不落在"新的事件总线"上。

---

## 4. InteractionSchema

`src/game/contracts/interaction.py`

```python
@dataclass(frozen=True)
class InteractionSchema:
    id: int                     # = 引擎 request_id
    kind: str                   # InteractionKind
    actor_id: str               # 谁来回答
    source_id: str
    responders: tuple           # 群体请求（共享无懈阶段）的成员
    title: str                  # 规则层给的标题
    prompt: str                 # 引擎原文
    note: str                   # 规则层渲染好的说明文案
    cancellable: bool           # 规则上允不允许放弃
    allowed_actions: tuple      # submit / pass / cancel / end_phase / use_skill
    payload: dict               # 约束与展示语义（见下）
    context: dict               # 原始请求上下文（保持向后兼容）
```

`payload` 里的通用约束（**优先用 payload 表达，不为每张牌造枚举**）：

| 键 | 含义 | 出现场景 |
| --- | --- | --- |
| `purpose` | 规则声明的请求意图 | `judge_replacement` / `huogong_discard` / `wuxie_chain` / `dying_rescue` |
| `zone` / `zone_owner_id` | 从哪个区域取牌、谁的 | 一切选牌 |
| `candidates` | **权威候选实体**（引擎给的） | 一切选牌 |
| `card_ids` / `target_ids` | 网络投影用的稳定 id | 一切 |
| `min_count` / `max_count` | 张数约束 | 一切 |
| `required_suit` / `required_suit_label` | 花色约束（火攻） | 火攻弃置 |
| `revealed_card` / `revealed_by` / `caster_id` | 已公开亮出的牌与相关角色 | 火攻 |
| `panel` / `panel_stage` | 规则层声明的专用面板与阶段 | 火攻（`reveal_and_pick` / `reveal` / `discard`） |
| `judge_card` / `skill_id` | 判定牌 / 相关技能 | 改判窗口 |

**种类（kind）刻意保持粗粒度**，值域与旧的 `DecisionKind`、`PendingRequestType`
完全一致（网络协议一个字节没改）：

```text
NONE / RESPOND_CARD / CONFIRM / SELECT_OPTION / SELECT_CARDS / SELECT_TARGETS / PLAY_PHASE
```

需求里那张更细的语义表（`SELECT_CARD` / `SELECT_TARGET` / `RESPOND_SKILL`…）由
`schema.semantic` **从 kind + 数量约束推导**出来，只用于日志与报告：

```python
schema.kind == SELECT_CARDS and schema.max_count == 1  →  semantic == "SELECT_CARD"
```

刻意不做成独立枚举：单张与多张是同一套交互，拆成两个 kind 只会让本机与联机
各写一份分派。

`DecisionKind` 现在是 `InteractionKind` 的别名（`network/decisions.py`），
所以"有哪几种交互"只有一处定义：

```python
from src.game.contracts import InteractionKind
from src.network.decisions import DecisionKind
assert DecisionKind.SELECT_CARDS is InteractionKind.SELECT_CARDS   # 实测 True
```

### 规则层的展示声明

新增 `src/game/interaction_presentation.py`，与既有的 `judge_presentation.py`
是同一个做法（**扩展已有抽象，不另造一套**）：规则层声明"这种请求用哪块面板、
标题是什么、文案怎么渲染"，UI 只消费。

```python
INTERACTION_SOURCES["huogong_discard"] = InteractionSourceSpec(
    reason="huogong_discard", title="火攻 · 弃置",
    panel=PANEL_REVEAL_AND_PICK, stage="discard",
    note_template="你需要弃置一张{required_suit_label}手牌。",
)
```

同一张表里还**合并了原本重复三份的技能类型中文表**（`skill_bar.KIND_LABELS` /
`judge.py` / `fx._skill_kind_label`），现在只有 `SKILL_KIND_LABELS` 一份。

---

## 5. PresentationSchema

`src/game/contracts/presentation.py`

```python
@dataclass(frozen=True)
class PresentationSchema:
    id, kind, actor_id, target_ids
    card, skill, text, detail, tone
    duration        # 本机秒数（已按本机速度档缩放）
    blocking        # 关键：这一条要不要占据"规则等它演完"的位置
    payload
```

种类覆盖需求列出的那批：`CARD_USED / CARD_REVEALED / SKILL_ACTIVATED /
JUDGE_STARTED / JUDGE_CARD_REVEALED / JUDGE_REPLACED / JUDGE_RESULT /
PHASE_SKIPPED / DAMAGE / RECOVER / DYING / DEATH / IDENTITY_REVEAL /
GAME_RESULT` 等，另有 `CARDS_MOVED / CARDS_DRAWN / RESPONSE_REQUEST /
TABLE_CARDS / CARD_COUNT / TURN_STARTED / PHASE_STARTED / CHAIN / EQUIPMENT /
PUBLIC_POOL / LOG`。

**`blocking` 是本轮的判据，不是装饰**：

* `blocking=True` 的演出：`JudgeStep`（判定全程）、`ResultStep(kind=phase_skip)`
  （判定的结论条）、**主动技**的 `SkillStep`、`GAME_RESULT`；
* 一切飘字 / 摸牌 / 牌移动 / 锁定技与触发技提示都是 `blocking=False`——
  让它们阻塞规则只会把整局变成慢动作。

`Step.presentation()` 把演出队列里的一条演出编译成 `PresentationSchema`，
`PresentationQueue.blocking_schemas()` 把它交给闸门，于是闸门的日志是
`presenting=judge_started/phase_skipped forced=0` 这样的**语义**，而不是对象地址。

三张映射表共用一套词汇，并带自检：

```python
KIND_BY_ENGINE_EVENT   # 本地：引擎事件名 → 演出种类
KIND_BY_FACT           # 联机：game.view.presentation 的 EV_* → 演出种类
KIND_BY_STEP           # 演出队列的步骤名 → 演出种类
missing_fact_kinds(facts)   # 网络侧有没有契约表还没跟上的演出
```

---

## 6. RuntimeState 与演出闸门

`RuntimeState`（刻意只有四个，规则仍在 Flows 里）：

```text
RUNNING               规则在推进
WAITING_INTERACTION   有请求在等人回答
PRESENTING            重要演出在飞，规则让路
GAME_OVER
```

**没有单列 `ANIMATING`**：纯视觉动画按设计不影响权威逻辑；给它一个状态只会诱使
别人拿它做规则判断。`runtime_state(game)` 是纯查询，不产生任何副作用。

`PresentationGate`（`game.presentation_gate`，与既有的 `judge_gate` 并列）：

```
PresentationQueue 汇报"还有 blocking 演出在飞"
    → 闸门打开
    → ① 动作队列不发新动作
       ② 本机界面让路（Renderer 的整个操作层）
       ③ 引擎不唤醒等在这条边界上的回合推进
    → 队列排空 → 闸门关闭 → 引擎继续
```

### 为什么不能在规则层写 sleep

`time.sleep` 会把主线程钉住：渲染停、网络停、AI 停、玩家的窗口无响应。闸门是
"规则时间线让路 + 表现层驱动推进"，画面与输入全程照常。

### 三个必须分开的状态

| 状态 | 谁回答 | 后果 |
| --- | --- | --- |
| 规则上判定还没走完 | `JudgeGate.logical_pending` | 全场只接受判定输入 |
| 判定停在改判窗口 | `JudgeGate.replacement_pending` | 判定的回答说不上来就永远没有结果 |
| 判定结果在屏幕上 | `PresentationGate.presenting` | 规则等它演完，但**不拦引擎提交**（拦了会把 AI 卡死） |

### 改判窗口例外（本轮最危险的坑）

闸门必须**放行正在被问的人**，否则"演出等人回答、人回答等演出"互锁：

```python
def holds_local_input(self):
    if not self._presenting:
        return False
    request = self.game.pending_request
    if request is None:
        return True
    if request is group: ... 只压非成员
    return request.target is not local          # 被问的人 → 不让路
```

动作队列那一层同理：有 `pending_request` 时闸门不压队列（AI 的改判回答说不上来
就永远拿不到最终判定牌）。与 `JudgePanel.holds_actions` 里既有的那条例外同源。

### 有界释放

`PresentationGate.MAX_HOLD = 9.0` 秒。演出层出错 / 面板再也不关闭时强制放行并记
`forced` 计数。**计时只在 `Effects.update` 一处 `tick`**——两处同时 tick 会让上限
提前一倍触发（实测过：判定面板 4.5 秒就被强制放行）。

### LAN 的选择：B（房主控制逻辑时间线，客户端本地按统一节奏播放）

**不采用 A（房主等所有客户端播完）**，理由都是可查的架构事实：

1. `protocol.py` 的消息表里**没有**客户端 → 房主的"演出播完"消息（全表可查）；
   加一条就等于引入一个新的全局同步点。
2. 一台卡顿的客人会**永久**卡住整局——需求明确写了不能这样。
3. 演出时长本来就不同步：`Game.speed` 是座主本地设置、`RemoteGameView.speed`
   是每台客户端自己的设置（`ui/speed.py` 已声明两者互不通信），所以"统一时间线"
   在契约上并不存在。

**采用的方案**：闸门只作用于**每一台机器自己的**表现时间线。房主有 `Game.presentation_gate`，
客户端有 `RemoteGameView.presentation_gate`（本轮新增），两边互不通气。客人慢 →
它自己的画面落后，权威状态照常前进，重同步后靠视觉增量账本对齐；客人卡死 →
只影响它自己。**结构上不可能互相锁死。**

---

## 7. Host / Guest 数据流

```text
房主（权威）                                    Guest（只读）
────────────                                    ────────────
PendingRequest
  └─ build_interaction() → InteractionSchema
       ├─ 本机 HumanController.present
       └─ RemoteHumanController._build_pending_request
            └─ DecisionRequest(JSON)
                 request.kind / constraints / cards / targets / options
                 context.reason / panel / panel_stage / title / note
                         required_suit_label / revealed_card / revealed_by
                 ── DECISION_REQUEST ──→  ClientMatch.decision
                                           └─ ViewAdapter.apply_decision
                                                └─ decision_presentation（照抄）
                                                     └─ Renderer（同一套）

演出：引擎事件 → PresentationBridge(EV_*) ── GAME_EVENT ──→ ClientPresentation
                                                                └─ storyboard.submit（同一个队列）
```

Guest 能拿到的全部：`ClientGameView` / `DecisionRequest`（= InteractionSchema 的
网络形态）/ `GAME_EVENT` 表现事实 / 属于本玩家的私密信息（自己的手牌、自己那份
`SelectionView`、自己的 `ResponseWindow`）。

**Guest 不重新实现任何合法性判断**，本轮又收紧了两处（原来会自己改写房主约束）：

| 原来 | 现在 |
| --- | --- |
| `view_adapter.can_cancel_pending_selection()` 恒 `True` | 读房主声明的 `cancellable` |
| `decision_presentation` 把 `min_cards=0` 改写成"必须选满" | 照抄 `max(0, min_cards)` |

---

## 8. 判定 vertical slice

### 现在的调用链

```text
JudgeFlow._draw
  └─ 翻判定牌 → JUDGE_STARTED / JUDGE_REVEALED（引擎事件）
        └─ Effects._on_judge → storyboard.submit(JudgeStep, blocking=True)
              └─ 闸门打开：界面让路 + 动作队列停 + 回合推进延后

  └─ 改判窗口：pending.create(SELECT_CARDS, min_cards=0, context.reason="judge_replacement")
        └─ build_interaction → InteractionSchema(
               kind=SELECT_CARDS, min_count=0, max_count=1,
               cancellable=True, allowed_actions=(submit, cancel),
               note="选择一张手牌替换当前的判定牌；不选则维持原判定。",
               panel=""  ←  规则层显式声明"用通用界面"）
              ├─ 本机：HumanController._present_selection（通用的选牌界面 + 跳过按钮）
              └─ 联机：同一个 schema → DecisionRequest（Guest 同样有跳过按钮）

  └─ 锁定最终判定牌 → JUDGE_REPLACED → JUDGE_RESULT
        └─ JudgeStep.note("result", …) 落到**对应那一次**判定演出上
        └─ JudgePanel 展示结果语义（来源/规则/结论全部来自 judge_presentation）

  └─ JudgeFlow._settle_judgement
        └─ 判定牌去向：card_recipient（天妒 / 双雄）取走的是 **result.card**
           （改判窗口锁定时就定下来的那张），否则进弃牌堆

  └─ 触发技在 JUDGE_FINISHED 里取走判定牌（天妒）
        └─ SKILL_TRIGGERED（带 skill_id / skill_name / kind / kind_label / description）
              └─ storyboard.submit(SkillStep)  ←  排在判定之后

  └─ 延时锦囊生效（乐不思蜀 / 兵粮寸断）
        └─ PHASE_SKIPPED → ResultStep(kind=phase_skip, blocking=True)
              └─ 结论条也是**整段判定演出的一部分**：演完之前规则不开始下一件事

  └─ Presentation 完成 → 闸门关闭 → TurnFlow 继续
```

**天妒拿到的是最终判定牌**：`JudgeResult.card` 在 `_finalize` 里锁定，改判后的
新牌早已替换进 `judge_context.current_card`，`_settle_judgement` 移动的是
`context.current_card`。`probe_judge` 的 23 项与 `probe_chain` 的 9 项（含
"天妒的技能提示排在判定演出之后"）全部通过。

### 本轮修掉的"一闪而过"

判定结束后、结论条（"跳过出牌阶段"）还在播的那段，界面层此前已经恢复可点
（实测 `t=7.8` 与 `judge:lebu` 结束同一帧恢复），规则也已经跑到下一个阶段。
现在：

```text
判定演出期间界面层让路        让路帧数 = 48/246   （改前 0/246）
演出排空后界面层恢复          恢复于 t = 9.43
判定演出期间没有任何新的回合开始   turn.start = 0 / 共 4
判定演出期间引擎没有唤醒过被推迟的回合推进   0 次
```

---

## 9. 技能发动展示 vertical slice

### 现在的调用链

```text
规则层（全部发射点统一走 interaction_presentation.skill_payload）
  Skill._handle_event（触发技）
  skills/activation.py（主动技）
  basic_cards.py / card_action_session.py / controllers/remote.py（视为技转化）
  equipment_skills/system.py（装备锁定技：没有 SkillDef，类型与说明直接给出）
        └─ SKILL_TRIGGERED payload = {
             skill_id, skill_name, kind("active"/"view_as"/"locked"/"passive"),
             kind_label("主动技"/…), description, targets … }
              ├─ 本机 Effects._on_skill → present_skill → SkillStep
              └─ 联机 PresentationBridge._on_skill → EV_SKILL → client_fx._p_skill
                      （同一个 present_skill，同一份文案）
              └─ storyboard.submit(SkillStep)
                    blocking = (skill_kind == "active")
```

* **UI 不再查技能表**：`show_skill_banner` 拿的就是规则层给的
  `skill_name / kind_label / description`。客户端缺某个技能定义也不会显示成空白，
  房主与客户端显示的是**同一段文案**。
* **不按技能名硬编码**：任何 `SkillDef` 都能生成同一个 `PresentationSchema`。
* 只有**主动技**让规则让路（它是玩家刚刚做出的选择，效果必须等提示播完再发生）；
  锁定技 / 触发技会反复触发，只排队不挡路。
* 技能类型的中文表从三份合并成一份（`interaction_presentation.SKILL_KIND_LABELS`）。

`probe_skills` 13/13 通过，其中主动技 / 视为技 / 锁定技 / 触发技四类提示的类型与
说明都逐项核对。

---

## 10. 火攻 vertical slice

### 现在的调用链

```text
UseCardFlow（火攻）
  └─ HuogongEffect.begin
        request_context = { reason:"huogong_reveal", candidates:目标手牌,
                            zone:"hand", zone_owner:目标, caster:使用者,
                            damage:1, nature:"火焰" }        ← 规则参数，不是界面文案
        └─ build_interaction → InteractionSchema(
               kind=SELECT_CARDS, min=max=1, cancellable=False,
               panel="reveal_and_pick", panel_stage="reveal",
               note="选择一张手牌展示给对方；对方若能弃置同花色的牌，
                     你将受到 1 点火焰伤害。"   ← 由模板 + 规则参数渲染)
              └─ 本机 HumanController → start_card_selection(panel=…, note=…)
              └─ 联机 → DecisionRequest.context[panel/panel_stage/note]

  └─ CARD_REVEALED（公开信息：所有人看到这张牌）
        └─ 联机：EV_CARD_REVEALED → client_fx

  └─ HuogongEffect.resume（第二阶段）
        candidates = [手里与展示牌同花色的牌]        ← 规则算的
        request_context = { reason:"huogong_discard", candidates:…,
                            required_suit:"heart", required_suit_label:"红桃",
                            revealed_card, revealed_by, caster,
                            cancellable:True }        ← 显式声明可以放弃
        └─ InteractionSchema(panel="reveal_and_pick", panel_stage="discard",
                             cancellable=True, note="你需要弃置一张红桃手牌。")
              ├─ 本机：受控面板（左列展示牌 / 右列自己的手牌高亮+暗化 / 放弃选择）
              └─ 联机：同一个 panel 名与同一段文案，跳过按钮的有无与房主一致

  └─ Decision（选牌 / 放弃）
        └─ Host validation（引擎侧 _resolve_simple / _pass_pending）
              ├─ 选了牌 → MoveCardAtom 弃置 → DamageFlow 1 点火焰伤害
              └─ 放弃   → 不弃牌、不受伤害，流程正常收尾
```

### 界面不再按 reason 白名单认领

`ui/huogong.py` 的唯一接管判据变成规则层声明的 `panel`：

```python
PANEL_NAME = "reveal_and_pick"
if str(selection.get("panel") or "") != PANEL_NAME:
    return None                       # 不是本面板负责的交互
```

新增一种"展示一张牌 + 按条件挑牌"的交互，只要在规则层声明同一个面板名，
UI 一行都不用改。

### 规则文案搬出 UI

| 原来（写死在 `ui/huogong.py`） | 现在 |
| --- | --- |
| `"…你将受到 1 点火焰伤害。"` | `HuogongEffect.DAMAGE / NATURE` → 模板渲染 |
| `"你需要弃置一张 %s 手牌。"` | `required_suit_label`（规则层从实体牌花色推出） |
| 标题常量 `TITLE = "火攻"` | `title="火攻 · 展示"/"火攻 · 弃置"` |
| `reveal = reason == "huogong_reveal"` | `panel_stage`（规则层声明） |

### 新行为：放弃是真的能放弃

`cancellable=True` 会被 `_pass_pending` 接受（判据从"`min_cards == 0`"扩展为
"`min_cards == 0` **或** 请求显式声明 `cancellable`"），`cancel_pending_selection`
优先走 `on_cancel`（提交引擎侧的 Pass，而不是"空选择"）。实测：

```text
火攻弃置：放弃操作被接受            True
火攻弃置：放弃后一张牌都不弃         1 → 1
火攻弃置：放弃后目标不受伤害         4 → 4
火攻弃置：放弃之后流程正常收尾（没有卡住）  None
```

---

## 11. 修改文件列表

### 新增

| 文件 | 内容 |
| --- | --- |
| `src/game/contracts/__init__.py` | 契约层出口 |
| `src/game/contracts/interaction.py` | `InteractionKind` / `InteractionSchema` / `build_interaction`（唯一构造口） |
| `src/game/contracts/presentation.py` | `PresentationKind` 词汇表 / `PresentationSchema` / `RuntimeState` / `PresentationGate` |
| `src/game/interaction_presentation.py` | 规则层的交互展示声明（`INTERACTION_SOURCES`）+ 技能载荷/类型标签的唯一份 |

### 修改（24 个）

| 文件 | 改动 |
| --- | --- |
| `src/game/core.py` | 建 `game.presentation_gate` |
| `src/game/engine/runtime.py` | `_flush_deferred_resumes` 尊重演出闸门；`_pass_pending` 读规则层声明的 `cancellable` |
| `src/game/engine/skills.py` | 技能通知载荷改由 `skill_payload` 统一生成（带类型/说明） |
| `src/game/turn.py` | `start_next_turn` / `_enter_play_phase` 在重要演出未演完时让路（有界重试） |
| `src/game/controllers/human.py` | `present` 改为只消费 `InteractionSchema`（含 `_present_selection`） |
| `src/game/controllers/remote.py` | `_build_pending_request` 的约束/意图/文案改由 schema 给；转化为统一载荷 |
| `src/game/card_selection.py` | `start_card_selection` 增加 `panel/panel_stage/title/note/required_suit(_label)/on_cancel`；`cancel_pending_selection` 优先走 `on_cancel` |
| `src/game/card_effects/tricks.py` | 火攻：规则参数（伤害/花色/可放弃）进请求上下文 |
| `src/game/basic_cards.py`、`card_action_session.py`、`skills/activation.py`、`equipment_skills/system.py` | 4 个 `SKILL_TRIGGERED` 发射点统一走 `skill_payload` |
| `src/game/view/view_builder.py` | `build_selection_view` 透传规则层的展示语义与 `cancellable` |
| `src/game/view/view_model.py` | `SelectionView` 新增 `panel/panel_stage/title/note/required_suit_label/cancellable` |
| `src/game/view/presentation.py` | `EV_SKILL` 增加 `kind / description` |
| `src/network/decisions.py` | `DecisionKind` 变为 `InteractionKind` 的别名（值域不变） |
| `src/ui/storyboard.py` | `Step.blocking` / `presentation()`；队列开闸/关闸；判定与阶段跳过为 blocking；技能提示按类型决定 |
| `src/ui/fx.py` | `_holds_actions` / `interaction_hold` 纳入闸门；技能提示不再查技能表；闸门计时唯一入口 |
| `src/ui/client_fx.py` | `_p_skill` 带 `skill_kind` 与说明 |
| `src/ui/huogong.py` | 按规则层的 `panel/panel_stage/title/note` 工作；规则文案搬出 UI |
| `src/ui/view_adapter.py` | 建 `presentation_gate`；`_apply_selection` 透传新字段；`can_cancel_pending_selection` 读房主声明 |
| `src/ui/decision_presentation.py` | 张数约束照抄房主；透传展示语义 |
| `src/card.py` | 新增模块级 `suit_name()` / `SUIT_NAMES`（供规则层写文案） |

---

## 12. 手玩验证结果

需求里 24 项验收，逐条对应的证据：

| # | 项目 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 单机普通判定 | ✅ | `probe_judge` 23/23（乐不思蜀 / 兵粮寸断 / 闪电命中 / 未命中） |
| 2 | LAN Host 判定 | ✅ | `probe_lan` 26/26（闪电场景，房主侧演出） |
| 3 | LAN Guest 判定 | ✅ | `probe_lan`：客户端抓到判定演出中间帧，画面血量 3 / 权威 0 |
| 4 | 乐不思蜀判定 | ✅ | `probe_judge`：`turn_start / judge:lebu / phase_skip` |
| 5 | 兵粮寸断判定 | ✅ | `probe_judge`：`turn_start / judge:bingliang / phase_skip` |
| 6 | 鬼才改判 | ✅ | `probe_schema`：改判窗口 schema（min=0 / cancellable / note） |
| 7 | 天妒拿最终判定牌 | ✅ | `probe_chain` 9/9（含"天妒提示排在判定演出之后"） |
| 8 | 技能展示 Host/Guest 一致 | ✅ | `probe_lan`："客户端收到技能表现事件 — [('仁王盾','锁定技')]" + "客户端真的画出了技能提示面板 — banner=仁王盾"；`probe_skills` 13/13（四类技能的**类型与说明全部来自规则层**，UI 不再查技能表） |
| 9 | 火攻 Host 发起 | ✅ | `probe_huogong` 19/19 |
| 10 | 火攻 Guest 发起 | ✅ | `probe_lan`：客户端拿到 `huogong_discard` 界面与真实展示牌 |
| 11 | 火攻 Guest 响应 | ✅ | `probe_schema` 联机段：网线上 `panel/stage/note/suit/cancel` 与房主逐字一致 |
| 12 | 火攻无合法花色牌 | ✅ | `probe_huogong`："没有弹出弃置窗口 / 目标没有受伤" |
| 13 | 火攻主动放弃 | ✅ | `probe_schema`：放弃被接受、不弃牌、不受伤、流程收尾 |
| 14 | 火攻伤害正常 | ✅ | `probe_huogong`：目标 4→3 |
| 15 | presentation 不会一闪而过 | ✅ | `probe_gate`：判定结论期间界面让路 48/246 帧 |
| 16 | presentation 结束后流程继续 | ✅ | `probe_gate`：让路结束于 t=9.43；`probe_no_deadlock` 12/12 |
| 17 | Guest 不自行计算 legality | ✅ | 审计 + 修掉两处改写房主约束；`probe_schema` 网线取证 |
| 18 | 不出现 stale decision | ✅ | `probe_lan`（决策两级回执）与 `lan_smoke` 44/44 全绿 |
| 19 | 不出现 bad_payload | ✅ | 同上；`probe_lan` 26/26 |
| 20 | 不出现 UI 卡死 | ✅ | `probe_no_deadlock` 12/12（三模式各 1800 帧，闸门零卡住、零强制放行）；`multiplayer_smoke` 8 人整局 `stuck=None` |
| 21 | 原五谷仍正常 | ✅ | `probe_schema` 新增五谷场景：公共池 3 张候选、min=max=1、不允许放弃、`zone=public_pool` 全部由规则层给出 |
| 22 | 原无懈链仍正常 | ✅ | `probe_chain` 9/9（共享无懈阶段跨 4 个回合的连锁）；`lan_smoke` 44/44 |
| 23 | 原濒死求桃仍正常 | ✅ | `probe_judge` 闪电命中链（伤害 → 濒死 → 阵亡）；`probe_no_deadlock` |
| 24 | Identity 模式仍能推进 | ✅ | `probe_draw_smoke` identity 420 帧渲染无异常；`probe_no_deadlock` identity 1800 帧；`probe_turns_plain` 与基线**逐字相同**（`玩家 → AI 1`） |

**没有做到的**：本轮的"手玩"全部是真实引擎 + 真实 Renderer 的探针（SDL dummy 视频驱动），
没有在真实窗口里用鼠标点完整局。判定 / 技能 / 火攻三块的手感（面板位置、停留时长、
放弃按钮是否好找）仍然需要人工看一眼——探针能证明的只有"数据和顺序正确"。

---

## 13. 自动测试 / 探针结果

| 探针 | 结果 |
| --- | --- |
| `.cache/phase18/probe_judge.py` | **23/23** |
| `.cache/phase18/probe_huogong.py` | **19/19** |
| `.cache/phase18/probe_skills.py` | **13/13** |
| `.cache/phase18/probe_chain.py` | **9/9** |
| `.cache/phase18/probe_draw_smoke.py` | **7/7** |
| `.cache/phase18/probe_lan.py` | **26/26** |
| `.cache/phase18/probe_schema.py`（本轮新增） | **53/53** |
| `.cache/phase18/probe_gate.py`（本轮新增） | **8/8** |
| `.cache/phase18/probe_no_deadlock.py`（本轮新增） | **12/12** |
| `.cache/phase18/probe_turns_plain.py`（基线可比脚本，本轮新增） | 与基线**逐字相同** |
| `tools/lan_smoke.py` | **44/44** |
| `tools/multiplayer_smoke.py` | 4 局 8 人整局全部 `over=True / stuck=None / error=None` |

`.cache/` 与 `tools/ui_snapshots/` 都在 `.gitignore` 里；`docs/reports/phase18_*.json`
是探针的输出证据。

`tests/` 目录在本轮之前就已删除（见项目历史），所以"自动测试"= 上面这批探针。
它们的定位是**防回归**，验收以 §12 的行为证据为准。

---

## 14. 当前还有哪些 UI 在猜规则

如实列出**本轮之后仍然存在**的（按风险排序）：

1. **`src/ui/prompt.py:208`** —— 弃牌张数自己算 `len(hand) - hp`，没走
   `game.hand_limit(player)`。庸肆一类改手牌上限的技能下，这个提示与引擎口径不一致。
   （`src/renderer.py:26-38` 是同一个口径的第二份近似实现。）
2. **`src/ui/prompt.py:20-40`** —— `CARD_LABELS` 把 19 张牌的"内部名→中文"完整
   抄了一遍（`src/card.py:DISPLAY_NAMES` 已有同一份）。
3. **`src/ui/seats.py:21-25`** —— `JUDGE_SHORT`（"乐"/"粮"/"电"）是 UI 自己维护的
   延时锦囊白名单。
4. **`src/renderer.py:673-689`** —— 本机侧主动 `import DistanceRule` 算距离角标；
   客户端靠 `local_interaction` 分支返回 None 兜住。
5. **`src/ui/interaction.py:31,226` / `human_control.py:222` / `remote_control.py:252`**
   —— 用 `card.name` 调 `granted_skill_id()`。这是"UI 发起的规则查询"，映射表本身
   在规则层，但调用的时机由 UI 决定。
6. **`src/ui/prompt.py:162-164`** —— 硬编码"借刀杀人：选择被迫出杀的角色"标题，
   按 `stage == "victim"` 猜语义。
7. **`src/ui/player.py:6`** —— 死导入 `TargetRule, target_candidates`（UI 曾自算
   目标合法性的残留），应删。
8. **`src/renderer.py:987-990`** —— UI 直读 `engine.pending.current`，当前被
   `DEBUG_UI = False` 关掉，属定时炸弹。
9. **`src/ui/fx.py:1425-1428`** —— 按 payload 形状（`payload["marks"]`）跳过技能提示，
   是行为特判（不是按名字，风险低）。
10. **`src/ui/view_adapter.py:337-345`** —— `ViewMode.overlay_result_texts()` 返回
    `None`，把结算文案的责任推回 UI 通用推断。

新写/新改的代码（三个 slice）里**没有**新增这类猜测：面板、文案、约束、能不能放弃
全部来自规则层声明。

---

## 15. Reason / Tag 后续方案（本轮只设计，不实现）

现状：伤害 / 移动 / 摸牌 / 回血 / 用牌 / 判定都用**字符串常量**在调用点临时约定，
没有结构化的"这次为什么发生"。

设计（MineMonopoly 的 MoneyTag 思路，落到本项目的真实对象上）：

```python
@dataclass(frozen=True)
class DamageReason:
    source_player_id: str        # 伤害来源
    target_player_id: str
    card_id: str = ""            # 实体牌 id
    card_name: str = ""          # SHA / JUEDOU / SHANDIAN …
    skill_id: str = ""           # 技能造成的（雷击 / 火攻转化…）
    nature: str = "normal"       # normal / fire / thunder
    reason_kind: str = ""        # "card" / "skill" / "chain" / "effect" / "rank"

@dataclass(frozen=True)
class MoveReason:      # from_zone / to_zone / cause（draw/equip/discard/steal/judge-*）
@dataclass(frozen=True)
class DrawReason:      # turn_draw / skill / card（五谷 / 无中生有） / compensate
@dataclass(frozen=True)
class RecoverReason:   # 桃 / 酒 / 技能 / 濒死救援 / 回合内回复
@dataclass(frozen=True)
class LoseHpReason:    # cost / effect / chain
@dataclass(frozen=True)
class UseCardReason:   # 出牌 / 技能转化 / 视为技 / 装备赋予
@dataclass(frozen=True)
class JudgeReason:     # 与既有的 judge_presentation.JudgeSourceSpec 合并
```

目标：以后不再通过调用栈或 `card.name` 猜"这个伤害到底是不是杀造成的"。

**本轮做的兼容准备（极小范围）**：

* 伤害的 `nature` 已经由规则层显式传给 `DamageContext`（火攻就是个例子：
  `nature="fire"` 来自 `HuogongEffect.NATURE`，不是 UI 猜的）；
* `PresentationSchema.payload` 已经是自由 dict，`DamageReason` 落地后可以直接挂进去；
* `interaction_presentation` 的 `purpose` 字符串就是 `JudgeReason` / `UseCardReason`
  的雏形——它已经是"规则声明的意图"，而不是调用点临时写的字符串。

**明确没有做的**：没有把所有 `Damage` / `MoveCard` 调用点重构成 reason 对象。
那是一次跨 40+ 文件的重构，必须单独一轮做，并且要有"按 reason 聚合的对局统计"
作为验收指标才有意义。

---

## 16. 下一阶段最值得迁移的三个场景

按"收益 / 风险"排序（都是本轮之后依然会两套实现的地方）：

1. **出牌阶段的"方式选择"（`PLAY_PHASE`）**
   `RemoteHumanController.playable_cards` 会为每张牌算出全部方式（普通使用 /
   技能转化 / 重铸）与合法目标；本机侧走的是 `CardActionSession` +
   `game.card_actions` 的一套并行查询。这是**两条最复杂的实现**，也是玩家最常
   看到的界面。`build_play_interaction` 已经预留好了（`PLAY_PHASE` 的 schema 骨架），
   迁移时把"每张牌有哪些方式、每个方式能打谁"整段塞进 `payload`。

2. **群体响应阶段（共享无懈 + 濒死求桃）**
   `group_interaction()` 已经能按成员生成 schema，网络侧的 `window_id` /
   `round_id` / `member_state` 也都在。缺的是本机 UI 的"等待其他玩家"提示层
   与 Guest 的 `client_fx` 箭头判据统一到同一份 schema 上（`reason == "wuxie_chain"`
   这类字符串判断在 4 处独立存在）。

3. **身份 / 结算展示（`IDENTITY_REVEAL` / `GAME_RESULT`）**
   身份可见性已经有 `visible_identity_of` 这一条正确路径，但"身份揭示动画"
   与"结算文字"仍由 `ui/identity_reveal.py` / `view_adapter.overlay_result_texts()`
   各自推断。`PresentationSchema` 已经有了 `IDENTITY_REVEAL` 与 `GAME_RESULT`
   两种 kind，落地成本最低、视觉收益明确。

---

## 17. 本轮没有做的事（明确边界）

* 没有迁移整个游戏：只动了判定 / 技能展示 / 火攻三个 slice。出牌阶段、群体响应、
  装备区交互、选将界面仍然走原来的两套实现。
* 没有引入 Command 总线、没有引入前端框架、没有改网络协议的值域。
* 没有实现 Reason / Tag（只做了设计 + 极小兼容准备）。
* 没有重做 UI 视觉：三块专用界面（判定 / 技能条 / 火攻）保持既有的古风视觉语言，
  只是把它们的**文案与面板选择**的来源统一到了规则层。
* 没有做真实窗口的鼠标手玩（见 §12 末尾的说明）。
