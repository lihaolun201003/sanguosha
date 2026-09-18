# Engine V2 Step 2：Legacy 行为基线 v0.1

- 日期：2026-09-16
- 项目：Python + Pygame 单机 1v1 三国杀原型
- 本轮范围：测试基础设施与 Legacy 行为回归测试
- 规则源码修改：无
- Engine V2 修改：无

## 1. 结果摘要

Step 2 已完成。新增 4 个测试工具自测和 18 个 Legacy 场景基线；连同 Step 1 原有 6 个 Engine V2 测试，共 28 个测试全部通过。

本轮没有修改 `main.py`、`src/` 下任何现有游戏规则、AI、Renderer、动画或 Engine V2 文件。所有新增内容仅位于 `tests/` 和 `docs/reports/`。

## 2. 新增测试基础设施

### 2.1 Deterministic Game Factory

`tests/legacy_helpers.py` 提供 `make_test_game(...)`，可固定：

- 玩家与电脑 HP
- 双方手牌
- 双方装备
- 摸牌顺序
- 初始 phase 与各类临时标志
- 全局随机种子（兼容 Legacy AI 直接使用 `random` 的现状）

工厂仍构造真实 `Game`，随后清除随机开局结果并安装显式测试状态。它没有复制或 mock 游戏规则。

### 2.2 Canonical Card Helpers

基本牌通过真实 `src.card_catalog` 工厂创建：

- `normal_sha(card_color)`
- `fire_sha()`
- `thunder_sha()`
- `shan()`
- `tao()`
- `jiu()`

装备通过 `equipment(internal_name)` 从真实 `create_equipment_cards()` 中取得新实例，覆盖青龙、贯石、寒冰、青釭、古锭、藤甲、仁王、白银狮子和八卦阵等场景。没有建立第二套 Card 定义。

### 2.3 固定牌堆

`set_draw_order(game, cards)` 接收“期望被抽出的顺序”，内部根据 `Deck.draw()` 从列表末尾 `pop()` 的真实行为反向放置。

这也避免了弃牌堆为空牌堆时自动洗回造成的非预期测试状态。

### 2.4 ActionQueue Drain

`drain_actions(game)` 反复调用真实 `game.update(dt)`：

```text
ActionQueue.busy
    ↓
大 dt 完成 WaitAction / MoveCardAction
    ↓
CallbackAction 按真实顺序执行
    ↓
callback 新增动作则继续推进
    ↓
队列为空时停止
```

没有 `time.sleep()`，也没有直接调用 `on_finish` 或篡改 Action 内部字段。

队列为空后通过 `settlement_status(game)` 区分：

- `IDLE`：动作完成且没有玩家请求。
- `WAITING_FOR_PLAYER`：存在 response、choice、pending selection 或丈八选择。
- `BUSY`：仍有可推进动作。

Legacy AI 当前不会创建一个等待外部输入的 AI Pending；它在 callback 中同步作出决定，所以本轮没有虚构 `WAITING_FOR_AI` 状态。

`assert_game_settled(game)` 进一步确认：

- ActionQueue 没有 current action 或排队动作。
- 没有残留 response。
- 没有残留 choice。
- 没有残留 generic selection。
- 没有残留丈八选择。

正常 `play`、`enemy` 或 `over` phase 不会被机械地当作未结算。

## 3. 固定的 Legacy 行为

### 3.1 基本战斗（3）

1. 普通杀命中无防具、无闪目标：目标失去 1 HP，杀进入弃牌堆。
2. 电脑持有闪时自动响应玩家的杀：不掉血，杀与闪均进入弃牌堆。
3. 玩家使用酒后被强制要求出杀；杀造成 2 点伤害；酒 buff 和强制标志清除，`jiu_used` 保持到回合结束。

### 3.2 装备与组合（12）

4. 藤甲抵消普通杀。
5. 火杀命中藤甲时造成 2 点伤害。
6. 青釭剑忽略藤甲，普通杀造成 1 点伤害。
7. 青釭剑忽略仁王盾，黑色普通杀造成 1 点伤害。
8. 白银狮子把酒杀的 2 点伤害限制为 1。
9. 青龙偃月刀在首张杀被闪后，可以选择指定第二张杀追杀；第二张杀命中。
10. 青龙偃月刀询问时可以放弃，第二张杀保留在手牌。
11. 贯石斧在杀被闪后，仅从手牌选择并弃置两张牌，令原杀命中。
12. 寒冰剑可把伤害替换为弃置电脑两张指定手牌，电脑不掉血。
13. 古锭刀攻击无手牌目标时造成 2 点伤害。
14. 电脑装备八卦阵时，红色判定视为闪，杀被抵消。
15. 玩家装备八卦阵时，选择发动后黑色判定失败，继续进入真实闪响应；不响应后受到伤害。

### 3.3 濒死与死亡（3）

16. 玩家从 1 HP 受到 1 点伤害后进入濒死，可使用桃回复到 1 HP，并继续游戏。
17. 同一场景可使用酒自救，结果与当前 Legacy 规则一致。
18. 玩家没有桃或酒时立即死亡：`game_over=True`、`phase="over"`、消息为“你阵亡了！”，不继续下一回合。

## 4. 测试工具自测（4）

1. Factory 正确固定 HP、手牌、装备、牌堆且没有 Pending。
2. 固定牌堆顺序与真实 `Deck.draw()` 顺序一致。
3. Drain 可跳过长 WaitAction 并执行后续 CallbackAction。
4. Drain 不会自动回答 ResponseRequest，并能报告 `WAITING_FOR_PLAYER`。

## 5. Deferred 场景

本轮要求的核心场景无 deferred。

八卦阵的红色成功和黑色失败路径均可在不修改 Legacy 源码的情况下稳定测试，因此一并纳入。

未扩展到本轮范围外的组合，例如雷杀专属规则、白银狮子卸下回血、麒麟弓、雌雄双股剑、丈八蛇矛、朱雀羽扇和方天画戟；它们不是本轮验收必需项，可在对应机制迁移前补充。

## 6. 发现与分类

### B. 测试工具/场景设置问题（已修正测试）

首轮有 3 个测试失败：桃自救、酒自救、玩家八卦黑色失败后的杀牌不在弃牌堆。

原因不是规则错误。上述路径会结束电脑回合并开始玩家摸牌；当测试牌堆为空时，真实 `Deck` 会把弃牌堆洗回摸牌堆，因此刚弃置的牌随后被重新抽取。测试补充了固定的后续两张摸牌后，现象稳定，所有测试通过。

### C. Legacy 已有行为

- 电脑的闪是同步自动响应，不经过 `ResponseSystem`。
- 电脑八卦阵自动发动；玩家八卦阵需要 Choice。
- 玩家获救后，当前电脑杀流程继续结束，随后进入玩家新回合并摸两张。
- 酒自救是当前开发阶段规则，已按现状固定。
- 贯石斧只弃手牌、寒冰剑只弃目标手牌，已按开发阶段特殊规则固定。

### D. Legacy 真 Bug

本轮场景没有暴露必须记录为确定规则 Bug 的新失败，因此没有修改任何 Bug。

架构审计中已记录的 stale selection、Pending 覆盖、动态 Card 临时属性和重复弃牌风险仍存在；本轮测试没有触发这些风险，不代表它们已被解决。

### 结构化结果限制

当前死亡状态没有独立 winner 字段。测试只能通过 `game_over`、`phase` 和最终消息锁定玩家失败结果。这是状态模型限制，本轮不改。

## 7. 开发阶段特殊规则

测试明确保留：

- 贯石斧只能弃手牌。
- 寒冰剑只弃目标手牌。
- 酒可在自己濒死时自救。
- 当前 1v1 固定玩家男性、电脑女性。
- AI 自动选择闪及部分装备行为。

Baseline 表示“当前项目如此运行”，不证明最终官方规则。

## 8. 验证结果

使用项目 `.venv` 中的 Python 3.10.11：

```text
python -m compileall -q main.py src tests    PASS
python -m unittest discover -s tests -v     28/28 PASS
Pygame dummy driver 启动 main.py 一轮       PASS
```

项目环境没有安装 pytest（`ModuleNotFoundError: pytest`），所以没有新增第三方依赖，使用真实可用的标准库 unittest 命令。

## 9. 是否具备迁移普通杀的安全条件

是，已经具备“开始一个极小垂直切片”的基础条件：

- 普通杀命中和闪避有行为基线。
- 酒增伤、主要防具修正和三种关键武器介入有组合基线。
- 伤害后的濒死、自救和死亡有基线。
- 流程可在无窗口、无真实时间条件下稳定推进。
- Pending 会被保留给测试显式回答，不会被 drain 吞掉。

但迁移时仍需保持 feature boundary，避免同一张杀同时进入 Legacy 和 V2 两条路径。

## 10. Step 3 建议

下一步建议仍是统一 PendingRequest 的最小适配层，而不是直接批量迁移装备：

1. 定义 request ID、request type、source/target、options 和完成状态。
2. 让现有 `ChoiceSystem`、`ResponseSystem`、`CardSelectionMixin` 先作为 UI adapter，不改变画面。
3. 对重复 request、完成两次、选择对象失效增加守卫。
4. 让 AI 能回答同一种 Request，但暂不重写 AI 策略。
5. 保持本轮 18 个 Legacy baseline 全部通过。

Step 3 本轮未开始。
