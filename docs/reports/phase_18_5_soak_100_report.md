# Phase 18.5 批量试玩（soak）报告：100 局

> 用自动试玩脚本在**真实点击路由**（``ui.interaction.handle_game_click``）下连打
> 100 局，只记录问题、不修。原始数据：``docs/reports/assets/phase18_5_ui/soak_100.json``。

---

## 1. 怎么跑的

``tools/soak_play.py``：SDL dummy 驱动 + 真实 ``Renderer`` + 真实点击入口。

主循环与 ``main.py`` 同序（这一步很关键，见第 4 节）：

```text
game.update(dt)       推进引擎与动作队列
renderer.update(dt)   推进表现层（演出队列 / FX）
renderer.draw(game)   构建布局（点击命中依赖它）
观察局面 → 决策 → 点击 → 验证点击是否生效
```

4 路并行，每路 25 局（种子 1..100，每局的 rng 用种子播种，可复现）。
总耗时约 9 分钟，187725 帧、9975 次点击。

玩家策略：能做就做最显然的事（有牌就出、要响应就出/不出、该弃牌就弃、
该选目标就点座位/自己、该结束回合就结束），**不追求打得好**，只要求把一局打完。

---

## 2. 结果

| 指标 | 数值 |
| --- | --- |
| 局数 | 100 |
| 总帧数 | 187 725 |
| 总点击 | 9 975 |
| **无效点击**（点了状态没变） | **6 840（68.6%）** |
| 正常结束（分出胜负） | **41 局（41%）** |
| 卡住提前结束 | 59 局（59%） |
| 崩溃 | **0** |

**41% 的局能打完，59% 走不下去**——这个比例说明问题不在个别边角，而在
"玩家点不动"这条主链路上。

### 走不下去的 59 局，卡住时刻的局面形态

| 形态 | 局数 |
| --- | --- |
| **没有任何待处理状态**（``pending=-``，phase=play，轮到玩家） | **44** |
| 响应窗口推不动（``respond_card``） | 11 |
| 选牌窗口推不动（``selection``） | 3 |
| 跑满帧数但没有卡住记录 | 1 |

### 无效点击被谁吞掉（按局统计）

| 吞掉原因 | 局数 |
| --- | --- |
| ``busy=True``（动作队列还在播动画） | 227 |
| ``hold=yes``（演出让路） | 200 |
| ``hold=no busy=False``（既不是让路也不是动画） | 33 |

---

## 3. 三个真问题（本轮只记录，按要求不改）

### 问题 1：选目标期间，点击被演出让路吞掉（最严重）

实测（seed 1）：**1597 次**点击全部无效，``hold=yes``：

```text
选目标 点 (639,573) 无变化 consumed=False hold=yes busy=False
        slot=True pending=target_selection hand_hit=None/4
```

根因在 ``src/game/contracts/presentation.py`` 的 ``PresentationGate.holds_local_input()``
（Phase 18 我写的）：

```python
request = getattr(self.game, "pending_request", None)
if request is None:
    return True          # ← 让路
```

它只认 ``pending_request``。而**"选目标"不是 PendingRequest**——它是
``game.pending_target_selection``。于是"轮到玩家出牌 → 选目标"时
``pending_request`` 是 ``None`` → 判定成"没有人需要回答" → 让路 → **吞掉所有点击**。

后果：出牌选目标、技能选目标这些**必须先点人才能继续**的操作，只要演出闸门
恰好开着，玩家就点不动，只能等演出过去（有时等很久，因为闸门是按演出队列
汇报的）。100 局里 200 局出现，占卡住局的绝大多数。

正确的判据应该是"本机玩家是否正在被要求做任何事"，包括：

* ``pending_request``（引擎请求，含改判窗口 / 无懈 / 求桃）
* ``pending_target_selection``（本地选目标）
* ``pending_selection``（本地选牌，通常伴随请求所以目前侥幸没暴露）
* ``pending_view_as`` / ``pending_skill_input`` / ``pending_card_action``（技能输入）

### 问题 2：响应窗口点牌/点"不出"无效（``busy=True``）

实测（seed 2/3）：

```text
响应出牌 点 (802,717) 无变化 consumed=True busy=True
        pending=request=respond_card/wuxie_chain+response hand_hit=3/4
不出     点 (1168,749) 无变化 consumed=True busy=True
        pending=request=respond_card/duel+response  hand_hit=None/0
```

* ``consumed=True``：``handle_game_click`` **接受**了这次点击（不是被闸门吞的）；
* ``hand_hit=3``：正确命中了那张牌；
* 但状态**没变**。

即：点击被消费了，却没产生效果。11 局卡在响应窗口，卡住时手上牌数从 0 到 5
都有（不都是"没牌可出"）——所以不是"玩家没有合法牌"，而是**有牌也打不出去**。

上一轮我已经把 ``response.active`` / ``choice.active`` 加进了
``_in_interactive_slot``（那道闸门的例外名单），但**这只解决了"被 busy 吞"这一半**；
``consumed=True`` 却无效是**另一条路径**，需要单独查
（``game.respond_with_card`` 内部是否在 ``busy`` 时拒绝）。

### 问题 3：动画期间手牌命中失准（上一轮已记录，本轮再次出现）

``renderer.card_at_position`` 要求 ``len(table_layout.hand_rects) == len(hand)``，
而 ``table_layout`` 是**上一帧**构建的；手牌数这一帧刚变时不匹配，就退回
``_fallback_hit``，那套算法与实际绘制位置有偏差。dummy 探针里逐手牌都能正确
命中，所以只在动画进行中出现。

---

## 4. 我自己在试玩脚本上踩的坑（说明前几版数据为什么不可用）

第一版数据毫无参考价值（99.7% 点击无效），原因全在脚本，逐条记下来供后续复用：

| 坑 | 表现 | 修法 |
| --- | --- | --- |
| **漏调 ``renderer.update(dt)``** | 演出队列永不消费 → 演出闸门一直开着 → 动作队列被压住 → 整局"卡死"，400 秒游戏时间三个人都还活着 | 补上，主循环三步齐全（``game.update`` / ``renderer.update`` / ``renderer.draw``） |
| 指纹不完整 | ``_sig`` 漏了二选一浮层与目标候选 → "确实打开了浮层"被误判成"点击无效" | 指纹覆盖所有影响界面的状态 |
| 没接二选一浮层 | ``ChoiceOverlay`` 由主循环单独持有（不在 ``Renderer`` 上），漏接就卡在"浮层挡着、点哪都不动" | 照 ``main.py`` 的接线持有并拦截 |
| 目标可能是自己 | 真人自己不在 ``seat_rects`` 里（对手走座位、自己走底部状态条） | 单独处理"目标是自己" |
| 僵局判定没排除"等别人" | 把"等 AI 动作队列播完"误判成死局 | 僵局与等待分开，等待另有更长的阈值 |
| 公共池坐标按单张算 | ``get_public_card_rects`` 是**按传入列表数量**散开居中的，传单张会算出错位坐标 | 先取整池再取第 i 个 |

**教训**：试玩脚本本身要先用"能否打完整局 + 无效率"自检，否则测出来的
"问题"全是脚本的。

---

## 5. 结论

* **崩溃 0**：100 局没有任何 traceback，引擎与 UI 的健壮性没问题。
* **41% 能打完**：能打完的局说明规则链路是通的。
* **59% 走不下去，且高度集中在"点击被吞"**：三条根因里两条明确
  （``holds_local_input`` 漏判本地槽位、``busy`` 期间响应无效），一条是
  布局时序问题。
* 按优先级：**问题 1**（选目标被让路吞掉）影响面最大、修法最明确——
  ``holds_local_input`` 应该问"本机玩家是否正在被要求做任何事"，
  而不只是"是否有引擎请求"。

---

## 6. 复现方式

```bash
# 单跑一局（看得见逐步日志）
.venv\Scripts\python.exe tools\soak_play.py 1 7 .cache/soak_one.json

# 批量（4 路并行，各 25 局）
for /L %i in (0,1,3) do start /B .venv\Scripts\python.exe tools\soak_play.py 25 ^
    %i*25+1 .cache/soak_%i.json
```

``soak.json`` 的每一局都带 ``problems``（去重后的同类问题）与
``clicks`` / ``ineffective`` / ``frames``，足以定位到具体局面。
