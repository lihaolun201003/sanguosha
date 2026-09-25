# Phase 10.1 Visual / Seat Distance / Highlight Hotfix 收官报告

- 日期：2026-09-20
- 基线：Phase 10 的 **682 tests** → 本次 **736 tests 全绿**（新增 54），compileall PASS
- 范围：横版素材竖版化、座次环距离核验、座位布局、高亮体系强化
- 未触碰：`src/game/**` 规则层（flows / rules / skills / card_effects / engine / deck 全部零改动）

---

## 1. 横版素材原问题

素材库里有 3 张 `width > height` 的图：

| 素材 | 尺寸 | 比例 |
| --- | --- | --- |
| `cards/standard/tricks/乐不思蜀.png` | 436×320 | 1.363 |
| `cards/junzheng/兵粮寸断.png` | 436×320 | 1.363 |
| `cards/misc/闪电.png` | 337×240 | 1.404 |

Phase 10 把它们按 `fit_contain` 塞进竖版卡位，结果是**中间一条横带 + 上下大片留白**，与旁边的竖版卡牌视觉不统一。

**先实际看图判断类型**（按要求不能盲目旋转）：两张延时锦囊的牌名是**竖排且字正立**的（「乐不思蜀」「兵粮寸断」在卡面右侧竖排，效果文字在左侧竖排），这是延时锦囊**横置形态的正常卡面设计**，不是"文件方向存反了"。

判定结论：**B 类（内容本身就是横向构图）**。若旋转 90°，牌名会从"竖排正立"变成"横排侧躺"，直接不可读——所以不能旋转。

## 2. Portrait Adaptation 方案

在既有 `src/ui/assets.py` 上扩展，**没有新建第二套资源系统，没有生成任何派生 PNG**。

新增两层：

- `AssetRegistry.is_landscape(asset_id)` — 按原始 Surface 的宽高自动判定，任何新加入的横版素材自动进入该流程。
- `AssetRegistry.oriented(asset_id, size, title, category)` — 返回适配竖版卡位的 Surface：
  - 竖版素材 → 走原有的缩放缓存（行为不变）；
  - 横版素材 → 走 `portrait_composite()`，结果进**独立的合成缓存**（LRU 256）。

`LANDSCAPE_MODES` 映射表显式标注处理方式，默认 `frame`：

```python
LANDSCAPE_MODES = {
    "card:LEBU": "frame",
    "card:BINGLIANG": "frame",
}
DEFAULT_LANDSCAPE_MODE = "frame"       # 未标注的横版素材一律走容器合成
# 另支持 "rotate_cw" / "rotate_ccw"：将来若发现真有"存储方向反了"的图，
# 在这里加一行即可，不必改渲染代码。
```

竖版容器结构（运行时合成，原图始终按比例、居中、不拉伸）：

```
┌──────────────┐
│  ♠6          │  ← 真实花色点数（由卡牌层用 Card 数据画在最上层）
│ ┌──────────┐ │
│ │ 横版原图 │ │  ← 保持比例，尽量放大
│ └──────────┘ │
│  乐不思蜀     │  ← 牌名
│   锦囊       │  ← 类型
└──────────────┘
```

顶部专门留出 `17.5%` 高度给花色点数徽标，牌名放在图片**下方**——这样容器内文字与徽标永不重叠。极小区域（判定区标签、装备图标位）自动退化为"只放居中缩略图"，仍然是竖卡而不是横条。

**结果：手牌、hover 大图、桌面牌、五谷池、弃牌堆、判定区缩略全部是统一的竖版视觉，不再出现"普通牌竖着、乐不思蜀突然变横条"。**

性能：含 4 张横版手牌的 1920×1080 场景实测 **3.10 ms/帧（≈323 FPS 上限）**，帧内 `loads=0 scales=0 portraits=0`——合成只在首次发生，之后全是缓存命中。

## 3. 原距离算法审计

按要求逐项审计了 `SeatManager` / `distance` / `effective_distance` / `attack_range` / 马 / `DISTANCE_OUTGOING` / `DISTANCE_INCOMING`：

**结论：规则层的距离实现原本就是正确的座次环算法，并不存在"按屏幕从左到右算距离"的问题。**

`src/game/rules/seats.py::SeatManager.distance()`：

```python
alive = self.alive_players_in_order()        # 只含存活（且 hp > 0）玩家
left, right = alive.index(source), alive.index(target)
clockwise = (right - left) % len(alive)
counterclockwise = (left - right) % len(alive)
return min(clockwise, counterclockwise)
```

- 基础距离**只**来自排序后的存活座次环，取顺/逆时针的较小值；
- 与屏幕坐标、Rect、UI 排列数组**完全无关**；
- `DistanceRule.distance()` 在其上叠加 `+1 马 / -1 马 / game.distance_modifier()`（技能），`attack_range()` 叠加武器范围，层次正确。

**真相来源唯一性已确认**：

| 位置 | 角色 |
| --- | --- |
| `src/game/rules/seats.py::SeatManager.distance` | 基础距离唯一实现 |
| `src/game/rules/distance.py::DistanceRule` | 加 modifier + 范围判定 |
| `src/game/equipment.py`（1 处调用） | 装备技能取距离 |
| `src/ui/**`、`src/renderer.py` | **零距离计算**（本次之前与之后都是） |

UI 需要显示距离时，通过 `Renderer.distance_to()` **查询** `DistanceRule`，不自己算——符合"Renderer 不计算规则距离"的架构约束。

所以本项的工作是：**核验 + 用测试锁定行为 + 确保 UI 不引入第二套实现**，而不是改写一个本来正确的算法。

## 4. 新 Seat-Ring 距离（行为锁定）

距离定义（存活玩家围成一环，取双向最小）：

| 人数 | 左右邻居 | 第二层 | 第三层 | 正对面 |
| --- | --- | --- | --- | --- |
| 3 | 两个都是 1 | — | — | — |
| 4 | 1 | — | — | 2 |
| 5 | 1 | 2 | — | — |
| 6 | 1 | 2 | — | 3 |
| 7 | 1 | 2 | 3 | — |
| 8 | 1 | 2 | 3 | 4 |

以上每一行都有对应测试（`SeatRingDistanceTests`），并且额外锁定：

- 距离对称性；
- **打乱 `game.players` 列表顺序后距离不变**（证明规则只认 `seat`）；
- `game.seats` 必须就是规则层的 `SeatManager` 实例（没有第二套实现）。

## 5. 死亡角色处理

`alive_players_in_order()` 过滤 `player.alive and player.hp > 0`，**尸体不占距离位**，环自动收缩。

测试覆盖：

- 5 人局杀掉中间邻居后，原本距离 2 的角色变成距离 1；
- 7 人局连续剔除两人，目标的距离 3 → 2 → 1；
- 阵亡角色本身距离返回 999（不可达）；
- `hp == 0` 同样视为退出环。

## 6. UI 座次映射

`src/ui/layout.py::_build_seat_rects` 重写为**按座次环分层**（规则距离完全不受影响，这里只决定画在哪）：

```
              正对面（偶数人数的第 n/2 个）
   距离最远 ─┐                   ┌─ 距离最远
             │                   │
   距离最近 ─┘                   └─ 距离最近
                    真人
```

算法：对每个对手算环偏移 `offset = (index - my_index) % total`；`offset*2 == total` 放正上方，`offset*2 < total` 放左侧，否则放右侧；每侧**按距离降序从上往下排**，于是两个距离 1 邻居永远落在自己左右下方。

实际截图验证（8 人）：

```
                AI 4 (距离 4)
     AI 3 (3)              AI 5 (3)
     AI 2 (2)              AI 6 (2)
     AI 1 (1)              AI 7 (1)
                  真人
```

2 人：对手正上方居中；3 人：左上 / 右上；4 人：左 / 上 / 右；5～8 人沿两侧均匀展开——全部符合要求。

**没有为 UI 重排 `game.players`**：`_build_seat_rects` 只读排序结果并输出 rect，测试断言调用前后 `players` 顺序不变。

## 7. 高亮体系变化

新增 `theme.VISUAL_STATES` 作为**高亮的唯一参数来源**，每个状态给出 `border / width / glow / glow_width / dim / label`；`theme.resolve_state(*names)` 按优先级归并。组件不再各写各的颜色与线宽。

| 状态 | 用途 | 表现 |
| --- | --- | --- |
| `hover` | 鼠标悬停 | 4px 亮蓝边 + 外发光 |
| `selected` | 手牌 / 来源牌已选 | 6px 金边 + 8 层发光 + 「已选」角标 |
| `valid_target` | 合法目标 | 4px 蓝边 + 7 层发光（覆盖**整块 seat**） |
| `valid_target_hover` | 合法目标 + 悬停 | 5px + 11 层发光 + 「可选」角标 |
| `selected_target` | 已选目标 | 6px 金边 + 12 层发光 + 「目标」角标 |
| `invalid_target` | 目标模式下的非法者 | 轻微压暗（dim 92），仍看得清 |
| `current_turn` | 当前回合 | 5px 金边 + 8 层发光 + 「当前回合」角标 |
| `pending_response` | 等待响应的角色 | 青色发光 + 「响应中」角标 |
| `view_as_candidate` / `view_as_source` | View-As 合法 / 已选来源 | 3px 蓝边 / 6px 蓝边 + 发光 + 「来源」角标 |
| `playable` | 可发动技能按钮 / 可出牌 | 绿色发光 |
| `disabled` / `dead` | 不可用 / 阵亡 | 压暗但不隐藏 |

强度层次刻意拉开：**合法目标 < hover < 选中目标**（glow 7 → 11 → 12），有测试逐级断言。

关键实现细节：

- 发光用 `theme.glow_border()` 生成**缓存 Surface**（按尺寸/颜色/宽度缓存），一次 blit 完成描边 + 发光，不每帧重建、不用高斯模糊。
- 状态描边画在**目标 surface** 上（而不是卡片自己的小 surface），所以发光可以溢出卡片边界而不会被裁掉。
- **画得更大更亮，但不改变点击目标**：所有命中测试仍然用原本的 rect/hit-rect。座位的高亮覆盖整个 seat 面板（含武将缩略、名字、血量、身份、手牌数、装备、判定），点击区域与绘制区域始终是同一个 `TableLayout` rect。

其他强化点：

- **技能按钮**：真的存在可发动技能时才变金色 + 发光，否则保持普通禁用样式。
- **距离可视提示**：进入目标选择模式、或鼠标停在某个座位上时，该座位右上角显示「距离 N」（数值来自 `DistanceRule`）；平时不常驻，不占屏幕。
- **装备槽热区**：向外扩 6px；**判定区标签热区**：向外扩 8×10px，鼠标不必命中 13px 的小缩略图即可弹出大卡面预览。
- **重叠手牌**：命中测试依旧从最上层往下（`_hit_test` 逆序遍历），hover 区域扩大后"点哪张就是哪张"，并有测试锁定。

## 8. 修改文件

**新增**

- `tests/test_engine_v2_phase10_1_hotfix.py`（54 个测试）

**修改（全部在表现层）**

| 文件 | 改动 |
| --- | --- |
| `src/ui/assets.py` | 横版判定 / `oriented()` / `portrait_composite()` / 合成缓存 |
| `src/ui/cards.py` | `card_art` 接入 `oriented`；状态描边改画在目标 surface 上；`category_label` 抽出 |
| `src/ui/theme.py` | `VISUAL_STATES` + `resolve_state` + 缓存的 `glow_border()` |
| `src/ui/widgets.py` | `draw_state_border()` / `draw_state_label()` 统一入口 |
| `src/ui/layout.py` | `_build_seat_rects` 按座次环分层 |
| `src/ui/seats.py` | 座位高亮走状态体系；状态角标；距离提示 |
| `src/ui/player.py` | 状态条高亮走状态体系；装备槽状态；判定区标签回传 rect |
| `src/ui/skill_bar.py` | 可发动技能时按钮更醒目 |
| `src/renderer.py` | 传入 hovered / 目标模式 / 距离提示；`distance_to()` 查询接口；扩大装备与判定区热区 |

**零改动**：`src/game/**` 全部（规则层最后修改时间仍停留在 09-19），`main.py`，`src/start_menu.py`。

## 9. 测试结果

| 项 | 结果 |
| --- | --- |
| 原基线 | 682 全绿 |
| 新增 | **54**（座次距离 8 / 死亡环 4 / Modifier 5 / 【杀】目标合法性 5 / UI 座次映射 9 / 横版竖版化 7 / 视觉状态与交互 16） |
| 总计 | **736 全绿**（8.9 s） |
| compileall | PASS |
| smoke | FFA 4 人（11 回合）、Identity 5 人（28 回合）、Identity 8 人（24 回合）均正常结束，无卡死无异常；UI smoke 900 帧 + 6 次 resize，`missing=0` |
| 性能 | 8 人 4.3 ms/帧；含横版手牌 3.1 ms/帧，合成 0 次重复 |

重点测试：

- **真实【杀】目标合法性**：8 人无武器时候选恰好 2 个，且就是座次环上的左右邻居；并有一条回归测试明确断言"候选 **不是** 屏幕最左边的两个人"。
- **死亡开位**：左邻阵亡后，下一位立即成为合法目标。
- **武器扩展**：装备青龙偃月刀后候选从 2 个变 6 个。

## 10. Screenshot 结果

生成并逐张查看了（输出在临时目录，未覆盖 `tools/ui_snapshots/`）：

| 画面 | 检查结论 |
| --- | --- |
| 2 / 3 / 4 / 5 / 6 / 7 / 8 人座位 | 与座次环一致；2 人正上方、3 人左上右上、4 人左上右、8 人左右各三层 + 正对面 |
| 8 人目标选择 | 距离 1 的两个邻居整块蓝色发光，其余轻微压暗；每个座位标注真实距离（1/2/3/4） |
| 座位悬停 | hover 状态可见，距离提示出现 |
| 手牌 hover | 上浮 + 亮边可见，点击仍精确对应 |
| View-As（武圣） | 红色【桃】发光标记为可用来源，黑【杀】【过河拆桥】压暗 |
| Pending 响应 | 可响应的【闪】高亮，其余压暗 |
| 判定区 + 横版素材 | 判定区标签带真实缩略图；**乐不思蜀/兵粮寸断已是竖版卡面**，与旁边竖版卡视觉统一 |
| 当前回合 | 座位与真人状态条均有金边 + 「当前回合」角标 |

过程中发现并修复的问题：

1. **横版容器内牌名与花色点数徽标重叠**（首版把牌名放顶部）→ 改为"顶部给徽标留白、牌名移至图片下方"。
2. **阵亡状态优先级过低**，会被目标高亮盖掉 → `dead` 提到最高优先级（100）。

## 11. 非阻断问题

1. **距离算法本身没有 bug**。审计结论是原实现已经正确，本次的工作是核验、补测试锁定、并确认 UI 未引入第二套实现。如果有其他现象看起来像"距离不对"，更可能来自目标候选以外的环节，需要具体复现。
2. **`cards/misc/闪电.png` 也走竖版容器**，但它是插画局部（非完整卡面），容器里的"牌名"位置会显示为空白区域——该素材本身不参与默认显示（闪电默认用 EX 完整卡面），只在备用版本里存在。
3. **难度趋势**：5 人与 7 人是奇数人数，没有正对面，顶部会空出一行，视觉上两侧略靠上；这是"偶数才有正对面"的必然结果，未强行填补。
4. **距离提示只在目标模式或悬停时出现**，不做常驻——如果希望游戏全程显示距离，需要再定一个常驻位置。
5. **技能按钮在选杀目标时仍可点**（既有行为，不在本次范围），本次只让它"有技能可发动时更亮"。
6. 前几次 Fast 阶段已记录的问题（骅骝缺素材、普通牌背缺失、iCCP 提示）本次按要求**未处理**。
