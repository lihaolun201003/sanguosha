# Phase 10.4：五谷真实卡面 + AOE 响应链 + 主菜单人数控件 Hotfix

- 报告日期：2026-09-20
- 范围：五谷丰登公共牌池的真实卡面；南蛮入侵 / 万箭齐发的无懈窗口生命周期；
  主菜单人数数字；配套确定性测试与 smoke。
- 工作区：`C:\Users\lihao\Desktop\sanguosha\sanguosha -zcode`

---

## 1. 五谷为什么"没有真实卡面"

结论：**不是渲染路径的问题，是尺寸判定把公共牌挤进了纯文字分支。**

`src/ui/cards.py` 的 `draw_card()` 用格子尺寸决定排版：

```
compact = rect.width < ART_MIN_WIDTH(76) or rect.height < ART_MIN_HEIGHT(104)
```

而公共牌区此前是 `PUBLIC_POOL_SIZE = (80, 112)`（设计坐标）。在 1920×1080 下
换算成 96×134，刚好越过阈值，所以**真实卡面其实已经能画出来**；但

* 尺寸贴近阈值下限，视觉上仍像"小卡片"；
* 只要分辨率低一点（例如 1280×720，scale 0.8 → 64×89）就立刻跌回
  `_draw_face_content()` 的纯文字卡；
* 公共牌池没有 hover 反馈，和手牌的手感不一致。

仓库里 `tools/ui_snapshots/F_pending_wugu.png` 是 Phase 10 之前留下的旧截图，
它才是被反复引用的"小白卡"来源；本轮重新生成截图后已不复现。

## 2. 如何接回统一 Card Visual

公共牌池本来就走 `table.draw_pool()` → `cards.draw_card()` 这一条统一接口，
本轮只补齐了缺失的三块：

1. **响应式尺寸**（`src/ui/layout.py`）
   `public_pool_card_size()` 按牌数算尺寸：牌少用完整竖版（上限 128 高），
   牌多按可用宽度自动缩小并收紧间距（下限 112 高）。下限刻意留在
   `ART_MIN_HEIGHT` 之上，因此**任何牌数都不会退回文字卡**。
   比例与手牌一致（`PUBLIC_POOL_ASPECT = 106/148`）。
2. **真实卡面优先**（`src/ui/table.py`）
   `draw_pool()` 显式传 `compact=False`：公共牌是公开信息，有素材就画卡面，
   不因为格子偏小降级成名字卡。花色点数仍由 `_draw_suit_rank_badge()` 用
   **真实 Card 数据**画在素材之上。
3. **悬停反馈**（`table.pool_hover_rect()` / `pool_hover_index()`）
   当前牌放大 1.18 倍并上浮 12px，锚点固定在**底边中心**，只向上展开，
   不覆盖下方提示条；命中判定仍用未放大的布局矩形，所以"鼠标指哪张就选哪张"。
   悬停牌最后绘制，压在相邻牌之上。

素材来源仍然是 `AssetRegistry`（`assets.card_asset_id()` → `card_art()`），
五谷逻辑里没有任何 `pygame.image.load`。缺素材（如骅骝）时 `card_art()` 返回
`None`，`draw_card()` 自动退回程序绘制，整组五谷不会因为一张图崩掉。

## 3. 南蛮入侵原响应链 Bug

`src/game/card_effects/tricks.py` 的 `_MassResponseEffect` 声明了
`per_target_wuxie = True`，导致 `UseCardFlow` 跳过整牌的无懈窗口，改由效果层
**对每个目标**新建一条 `WuxieResponseChain`。实际时序是：

```
无懈(目标A) → 杀(A) → 无懈(目标B) → 杀(B) → 无懈(目标C) → 杀(C)
```

即"N 次无懈窗口 + N 个杀响应"。

## 4. 万箭齐发原响应链 Bug

同一段代码、同一个开关，只是响应牌是【闪】，症状完全一致。

## 5. 无懈窗口新生命周期

把两种语义拆开，不再混用：

| 阶段 | 归属 | 次数 |
| --- | --- | --- |
| `TRICK_NEGATION_WINDOW`（无懈可击） | 锦囊本身 | 整张牌**一次** |
| `CARD_RESPONSE_REQUIREMENT`（杀 / 闪） | 锦囊开始执行后的效果 | 每个目标一次 |

改动：

* `CardEffect.per_target_wuxie` 删除，换成语义明确的 `sequential_targets`；
* `UseCardFlow` 无条件为 `cancellable_by_wuxie` 的牌开一次无懈窗口，
  效果阶段不再产生任何无懈请求；
* `_MassResponseEffect` 只推进目标下标（`begin → _next → _request_response`），
  响应请求的 `allowed_cards` 严格是 `{"SHA"}` / `{"SHAN"}`；
* 被无懈抵消时 `UseCardFlow._after_wuxie()` 直接结束整张牌，不会进入目标循环；
* 无懈套无懈的任意层反转由 `WuxieResponseChain` 保留，未做任何削减。

动画与箭头同步：`CARD_USED` 事件对逐目标锦囊**不**再一次性画满全场箭头
（payload 新增 `sequential_targets`），改由 `PENDING_CREATED` 驱动——
每个目标的响应请求出现时，先释放上一条箭头，再画当前目标这一条，
所以任一帧最多只有一根箭头，跟牌局不费力。

顺带修掉一个既有笔误：`src/ui/fx.py` 的两处 `request_context` 应为
`PendingRequest.context`（该属性不存在，导致"响应结束释放箭头"从未生效）。

## 6. 主菜单人数控件

`src/start_menu.py` 原先用 `fonts.get("hero")`（72，和标题"三国杀"同一档）画人数，
所以数字比"开始游戏"还抢眼。

* 新增字号 `theme.FONT_SIZES["menu_count"] = 24`：低于 `large`(34) 与
  `normal`(26)，明确表达"它只是配置值"；
* 居中改为按**实际墨迹**（`centered_text_origin()`）而不是按行高矩形：
  数字没有下伸部，按行高居中会整体偏上，且 5/6/7/8 偏移量各不相同；
* 数值框矩形改由 `sync_layout()` 统一计算并保存为 `menu.value_rect`，
  绘制与测试共用同一份几何；
* 模式规则未动：FFA 2～8、Identity 5～8，切模式后非法人数照旧自动 clamp。

## 7. 测试

| 项目 | 数量 |
| --- | --- |
| 开工基线 | 858 |
| 新增（`tests/test_engine_v2_phase10_4_hotfix.py`） | 29 |
| 全量 | **887 通过** |

新增分组：五谷卡面 / 尺寸 / hover / 点击（8）、南蛮无懈窗口（5）、万箭（4）、
逐目标箭头（3）、无懈与其他锦囊回归（5）、主菜单字号与人数限制（4）。

另更新 `tests/test_engine_v2_phase10_2_action_fx.py`：其中三个用例原本拿
【南蛮入侵】当"多目标一次性箭头"的代表，与本轮要求的逐目标语义冲突，
改用【桃园结义】（一次性结算）保留原覆盖面；南蛮/万箭的新语义由 10.4
的箭头用例覆盖。

## 8. Smoke

`python -m tools.phase10_4_smoke` → **23 项全部通过**，截图写入
`tools/ui_snapshots/phase10_4_*.png`：

* 五谷：5 张公共牌全部解析到真实卡面；110×154 竖版；与纯文字卡像素不同；
  hover 放大到 130×182；点击后进入手牌并移出公共池。
* 南蛮：无懈请求 4 个全部集中在最前，之后 4 个响应请求只允许 `SHA`。
* 万箭：同上，响应只允许 `SHAN`。
* 菜单：FFA 2～8 / Identity 5～8；5～8 的数字都居中且不越框；
  1280×720 ～ 2560×1440 均不越框；数字显著小于按钮文字。
* 4 / 5 / 8 人自由混战各跑一局，无 stuck。

## 9. 非阻断问题

* `PUBLIC_POOL_Y/高度`下探后与中央出牌展示位（`TABLE_CARD_RECT`）仍有少量
  水平重叠，实际出牌时展示卡会压在池的上方一层；本轮未调整层级，
  因为改动会牵动中央展示位的整体排版。
* 1280×720 下公共牌屏幕尺寸约 64×89（scale 0.8 的必然结果），
  卡面可辨但偏小；hover 放大可缓解，彻底解决需要小屏专用布局。
* AI 不会主动使用【无懈可击】去反制别人的无懈（引擎支持任意层反转，
  只是 AI 策略未覆盖）；按"本次不扩 AI"的要求未改动。

---

## 修改文件

```
src/game/card_effects/base.py          语义字段：per_target_wuxie → sequential_targets
src/game/card_effects/tricks.py        AOE 效果阶段不再打开无懈链；提示写明要出的牌
src/game/flows/use_card.py             整牌只开一次无懈窗口；CARD_USED 带 sequential_targets
src/ui/fx.py                           逐目标箭头；修正 request_context 笔误
src/ui/layout.py                       公共牌响应式尺寸
src/ui/table.py                        draw_pool：真实卡面优先 + hover 放大
src/renderer.py                        把鼠标位置传给 draw_pool
src/ui/theme.py                        新增 menu_count 字号
src/start_menu.py                      人数数字按墨迹居中、字号下调
tests/test_engine_v2_phase10_4_hotfix.py    新增 29 个确定性测试
tests/test_engine_v2_phase10_2_action_fx.py 多目标箭头代表改用桃园结义
tools/phase10_4_smoke.py                新增：四个场景 + 少量对局 smoke
```
