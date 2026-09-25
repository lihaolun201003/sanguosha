# Phase 11.5：LAN 游客端完整可玩性 / 状态稳定 / 动画修复

> 范围：房主 + 游客的真实局域网对局。本阶段只动**联机链路**（协议、远程控制器、
> 客户端交互、表现层落位、共用绘制函数），不改单机规则、不改 AI、不改身份规则。
> 单机与房主的交互/视觉仍然是同一套实现，本阶段是把游客端补齐到同一水平。

本文件是 round 的收口报告：根因 → 改法 → 验证。所有结论都能用仓库里的测试或
工具复现（命令见文末「复现方式」）。

---

## 0. 结论先行

| 问题 | 状态 | 证据 |
| --- | --- | --- |
| 五谷公共牌被当成"我的装备"，游客点了没反应 | 已修 | 新增 `WuguGuestSelectionTests`；`tools/lan_playability_sync.py` 场景 9 由 2 项"假通过"改为 6 项真验证 |
| 一张牌多种用法时，游客点第二种仍按第一种提交 | 已修 | `WayChoiceTests`（回传 `action_id`，房主按方式重算） |
| `CHOOSE_OPTION` 只支持"刚好两个选项"，1 个选项时流程静默死锁 | 已修 | `ChoiceOptionTests`（1 / 2 / N 选项都能提交） |
| 多来源 View-As（两张牌当一张）在响应窗口被截断成 1 张 | 已修 | `MultiSourceResponseTests`（选第一张不提交，选满两张才提交） |
| 房主拒绝后游客永久停留在「等待结算」 | 已修 | `RejectRecoveryTests` + 协议新增 `DECISION_ACCEPTED/REJECTED` |
| `controller.resolve() == False` 被当成成功 | 已修 | `_handle_response` 两级成功；`test_resolve_false_keeps_the_decision_open` |
| 飞牌动画用整块手牌区当牌矩形（白影） | 已修 | `AnimationGeometryTests`（宽度 ≤ 单张手牌宽） |
| 同一次出牌被登记两段相同飞行 | 已修 | `test_one_move_is_animated_once` / `test_same_card_to_same_place_is_deduped` |
| 摸牌动画从屏幕中央起飞 | 已修 | `_origin_key` 改回传区域对象 + `test_draw_origin_is_the_draw_pile` |
| 响应牌落在出牌位（落点不合理） | 已修 | `zone_rect("response_card")` 落在响应牌位 |
| 弃牌堆有牌时暴露最顶弃牌牌面 | 已修 | `test_discard_cover_is_always_a_card_back`（单机/房主/游客共用同一函数） |
| 游客结算页按钮无效 | 已修 | `ResultOverlayTests`（menu 真的离开；restart 由房主决定） |

单机回归：完整测试套件 **1110 项全部通过**；单机 UI 工具（`ui_smoke` /
`view_as_smoke` / `phase10_4_smoke` / `phase10_5_smoke`）全通过；`ui_snapshot`
重新出图正常（弃牌堆封面为卡背）。

---

## 1. 五谷丰登：公共牌被误标成"我的装备"（P1）

**根因**　`src/game/controllers/remote.py` 的 `_card_candidates()` 收下了请求声明的
`zone`，却没用它：第 262 行把"不在 owner 手牌里"的牌一律写成 `zone="equipment"`。
五谷的公共牌 `owner_id` 是选牌者本人、又不在手牌里，于是被客户端当成自己的装备牌：

* `ui/decision_presentation.py` 据此把选牌区域算成 `player_equipment`；
* `ui/interaction.py` 只接受装备槽点击 → 公共区的牌画得出来但点不动；
* 游客什么都没提交，房主一直等。界面甚至同时显示"请选择一张公共牌"和"点击装备区完成选择"。

**改法**　区域语义改为**按真实容器判定**（`_zone_index()` 一次建表：公共池 /
处置区 / 牌堆 / 弃牌堆 / 判定区 / 装备槽 / 手牌 / 桌面）：

* 候选条目的 `zone` 取真实区域，`slot` 取真实槽位；
* 只有"另一个玩家**手牌**里的牌"才退化成不透明 token（顺手牵羊的隐藏信息不受影响）；
* 装备区 / 公共池 / 判定区 / 桌面 / 处置区本来就是公开信息，照常发真实 card_id。

这样五谷的候选变成 `zone="public_pool"`，UI 走公共区点击分支，游客点击 → 提交 →
房主校验 → 公共池 -1、游客手牌 +1、流程进入下一人。**没有为五谷写任何特判坐标或按钮。**

## 2. 游客"选择使用方式"没有接通（P2）

**根因**　一张牌有多种用法时，渲染层会弹出与单机相同的「选择操作」面板
（`CardActionPicker`），点击返回 `("action", action_id)`；但
`ui/remote_control.py` 的 `run_action()` 没有 `("action", …)` 分支，元组最终掉进
`_submit()`——于是"点了第二种用法却按第一种提交"，或者界面停在原地面板不消失。

**改法**

* 面板返回的 `action_id` 改成**房主下发的字符串 id**（`view_adapter.CardWayOption`），
  不再是面板下标；
* 客户端新增 `choose_way(action_id)`：校验它确实是本次请求里的候选、置
  `way_chosen` 与 `RemoteDecisionState.action_id`，再决定下一步（继续选来源 / 选目标 / 提交）；
* 提交时把 `action_id` + `skill_id` + `result_name` 一起回传；
* 房主用 `actions_for_sources()` + `validate()` **重新解析**这次操作（不信任客户端）；
* 未知动作一律安全忽略并给出提示，**绝不**落进默认提交。

## 3. CHOOSE_OPTION：1 个选项 / N 个选项

**根因**　`ui/remote_table.py` 的选择框只处理"刚好两个选项"：`len(options) != 2`
时直接 `self.choice.clear()` ——面板没了、也没人提交，`雌雄双股剑`在只剩 `draw`
一个选项时整局卡死。同时客户端控制器也缺 `CHOOSE_OPTION` 的提交分支。

**改法**

* `src/choice.py` 的 `ChoiceSystem/ChoiceOverlay` 支持任意数量选项（`options` 列表）：
  ≤2 个沿用单机的左右按钮布局（单机一行没改），≥3 个改竖排列表；
  只有 1 个选项时两个按钮都是它——**点哪边都是那一个答案**，不提交的情况不再存在；
* 客户端 `_sync_choice` 不再按数量丢面板；`_submit` 补上 `CHOOSE_OPTION` / `CONFIRM`
  的显式分支；
* 房主侧 `DecisionRegistry` 仍然按"选项值必须在本请求的 options 里"复核。

## 4. 多来源 View-As / RESPOND_CARD（两张牌当一张）

**根因**　响应窗口的约束被写死成 `min_cards=1 / max_cards=1`，客户端选中第一张就
`_submit()`。于是"两张实体牌凑一张逻辑牌"的响应（丈八蛇矛一类）在联机里不可能完成。

**改法**（概念上把"逻辑牌"与"来源实体牌"分开）

* 房主：`_response_cards()` 按**牌**聚合，每条候选带上它全部可用方式（含
  `min_sources/max_sources`，多来源候选是 discovery 的"未凑齐"态）；
  约束 `min_cards/max_cards` 由这些方式实际的范围算出（不是写死 1）；
* 客户端：`_tap_response_card()` 与出牌阶段同一套逻辑——选中第一张
  **不提交**，继续收集到该方式的 `min_sources` 为止；选满后按"要不要目标"决定
  进目标选择还是直接提交；
* 房主：`_resolve_option()` 先用 `action_id` 精确匹配，匹配不上退回
  `(skill_id, result_name)`（凑齐后 action_id 里带了具体 source，会变），
  再复核 `source_zones`（普通使用必须来自手牌；转化按 conversion 自己声明的区域）、
  去重、以及 `card_actions.validate()`。

> 说明：本仓库里 **丈八蛇矛** 目前仍是单机遗留实现（`basic_cards.py` 的
> `zhangba_selecting` 路径，只能本地点、只能出杀），它在 Card Action Discovery
> 里没有对应的 conversion，所以联机侧也拿不到"两张手牌当【杀】"这个候选——
> 这是规则层的历史缺口，不是联机链路问题。本阶段把**机制**修完整，并用
> discovery 里真实存在的两来源转化验证（play 场合用既有探针 `probe_pair_sha`；
> response 场合新增测试专用探针 `probe_pair_shan`：两张手牌当【闪】）。
> 让丈八也走 discovery（= 单机与联机都变成"牌的一种用法"）属于**规则改动**，
> 需要单独一轮评估，已记在「遗留」里。

## 5/6. 提交 ≠ 接受：拒绝原因体系 + 两级成功

**根因**　旧流程是"客户端发出即清空请求 → 房主协议校验 → 标记 RESOLVED →
再调 `controller.resolve()`（返回值被忽略）"。一旦第 5 步失败：

* 客户端在等结算、面板没了；
* 房主还在等输入；
* 死锁，而且只在日志里留一句 `bad_payload`。

**改法**

1. 协议层新增两条消息：`DECISION_ACCEPTED` / `DECISION_REJECTED`（带
   `code` / `reason` / `detail`）。
2. 房主 `_handle_response()` 改成明确的两级：
   `协议校验 → controller.resolve() → 只有真的落到 Game 上才 ack`；
   任何一步失败都**保持决策 OPEN**，并给客户端发 REJECT + 重发面板。
3. 拒绝原因从 `bad_payload` 升级为可区分的机器码（debug / 日志用）：

   `malformed_payload / unknown_decision / wrong_player / stale_decision /
   decision_closed / duplicate_answer / bad_card_count / illegal_card /
   illegal_source / illegal_target / invalid_option / cancel_not_allowed /
   operation_failed / unknown_skill / skill_disabled / bad_action`

   客户端显示的是按码规范化的**一句中文**（`decisions.REJECT_TEXTS`），
   房主的具体说明留在 `detail` 里只进日志。
4. 客户端：提交后进入 `waiting_ack`（UI 显示"已提交，等待房主结算…"），
   **决策不再被清掉**；收到 REJECT 时面板原样恢复、显示原因、可以立刻重答。
5. 兜底重发：一条决策开着超过 3 秒仍未被回答时，房主重发一次请求
   （幂等）——保证"房主还在等输入时，远端一定有可操作的面板"这条不变量。

## 7. 结算页：返回主菜单 / 重新开始

**根因**　结算页按钮经 `Renderer.hit_action` 返回 `"restart"/"menu"`，而游客控制器
没有这两个分支，最后掉进 `_submit()`——此时没有决策，于是什么都不发生。

**改法**（房主始终是权威）

* 游客「返回主菜单」→ 离开当前对局（清理 remote view + 会话），回大厅/菜单；
* 游客「重新开始」→ 只发 `RESTART_REQUEST` 并显示"已请求房主重新开始，等待房主…"；
* 房主「重新开始」→ `HostMatch.return_to_lobby()`：作废决策、广播 `MATCH_RESTART`、
  把房间状态放回"未开始"，所有人回大厅；游客收到后回大厅等下一局的 `GAME_SETUP`；
* 房主「返回主菜单」→ 关房（客户端收到终止通知，不会被丢在死界面上）。
* 附带修掉一个连带问题：**结算之后**游客离开房间不再让房主 `abort`
  整局（否则别人的结算画面会被一起踢回大厅）。

## 8. 动画：白影 / 重复飞行 / 落点 / 摸牌起点

**根因 1（白影）**　`client_fx.zone_rect()` 把**区域矩形**当**单张牌矩形**返回：
自己的手牌区返回 `metrics.hand_area`（1600×900 下 1024 宽），别人的座位返回整个
座位面板，公共池返回整个中央区。`MoveCardAction` 直接拿这个矩形插值 →
124 px 的牌被拉成 1024 px，`cards.draw_card` 先铺浅色牌底再放卡图，两侧露出大片浅色。

**根因 2（重复飞行）**　同一次出牌，房主会发 `cards_moved(hand→processing)` 与
`card_used` 两条事件，客户端两条都登记了"手牌 → 中央"的同一段飞行（起点终点完全相同）。

**根因 3（摸牌起点）**　`client_fx._origin_key()` 返回 `id(draw_pile)`（整数），
而 `fx.Effects._origin_key()` 会对传入值**再取一次 `id()`**：类型约定不一致 →
永远匹配不上牌堆 → 退回中央。

**改法**

* 区域只提供**锚点**，飞出去的牌永远是**一张牌**的大小（`_card_sized` /
  `_anchor_card` / `card_size()`）；具体卡位优先（手牌取卡位、装备取槽位、
  响应取响应位、公共池逐张取池位）；
* "手牌 → 处置区/桌面"这段飞行的所有权归**语义事件**（`card_used` /
  `card_response`），普通移动事件跳过；同一张牌飞向同一落位在 1.2 秒窗口内只登记
  一次（**按牌按落位**的窄规则，不是"同类动画一律忽略"，所以连摸两张、同一张牌
  先后飞不同区域都不受影响）。被挡掉的次数记在 `presentation.duplicate_flights`；
* `_origin_key` 统一为"传区域对象"，并让只读视图的 `deck.draw_pile` /
  `public_card_pool` **原地更新**（对象身份稳定），摸牌因此真的从牌堆起飞；
* 响应牌落在响应牌位（`RESPONSE_CARD_RECT`），不再和主动出牌挤同一个位置。

## 9. 弃牌堆默认卡背

`ui/table.py::draw_piles()` 原来"有牌就画最顶弃牌的正面，空堆才画卡背"。改为
**常驻封面一律卡背**（空堆压暗），数量仍由下方标签给出；**悬停预览**仍然显示最近
弃牌的牌面（产品原有的详情入口）。这个函数被单机、房主、游客三方共用，所以三种
视角天然一致。

## 10. 顺手修掉的同类 Remote bug

* `_card_candidates` 之外，**出牌阶段的候选来源**也只列了手牌：转换声明"吃装备区的牌"
  （`source_zones`）时游客永远看不到那些候选。现在手牌 + 自己的装备区都会列出，
  并带 `zone` / `slot`。
* 客户端 `_tap_card` 原来对"未知 kind + 带 options 的请求"没有分支，现在统一走
  出牌链路。
* `ClientMatch.answer()` 的幂等判断与"新请求顶掉旧请求"的顺序问题：新增
  `_pending_answer_id`，避免"新面板已到、旧 ACK 才到"时把面板卡在"已提交"。
* `RemoteDecisionState` 里一个 bool 表达多个语义的问题：新增
  `RemoteInteractionState` 阶段枚举（IDLE / SELECTING_WAY / SELECTING_CARDS /
  SELECTING_TARGETS / CHOOSING_OPTION / SUBMITTED / REJECTED），UI 与提交逻辑只读它。

## 11. 有意的行为变更（会影响既有测试断言）

| 位置 | 旧行为 | 新行为 |
| --- | --- | --- |
| `ClientMatch.answer()` | 发出即 `decision = None` | 保留到收到 ACK / 被拒绝 |
| 拒绝码 | `unknown_request / not_yours / stale_match / already_resolved / unknown_card / unknown_target / unknown_option / missing_confirm / pass_not_allowed` | 见 §5 的规范码（`unknown_decision / wrong_player / stale_decision / duplicate_answer\|decision_closed / illegal_card / illegal_target / invalid_option / cancel_not_allowed`） |
| 弃牌堆常驻封面 | 显示最顶弃牌 | 卡背（悬停仍可看） |
| `test_others_draws_are_face_down` | 要求别人摸牌有 2 段牌背动画 | 实现从 Phase 10 起就是"别人摸牌不播动画"，测试改为断言"没有动画且牌进了手牌"（这条是审查报告已指出的过期断言） |

## 12. 复现方式（全部可重跑）

```bash
# 1) 完整测试套件（单机 + 联机 + 新回归）
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest discover -s tests -t .

# 2) 联机可玩性闭环（真实点击驱动：手牌 / 座位 / 公共区 / 技能键 / 固定按钮）
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/lan_playability_sync.py

# 3) 其它联机工具
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/lan_gameplay_bridge.py   # 协议层
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/lan_gameplay_ui.py       # 真实界面
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/lan_smoke.py             # 大厅/连接/心跳
SDL_VIDEODRIVER=dummy PYTHONPATH=. .venv/Scripts/python.exe tools/lan_two_process.py  # 跨进程：真实 main.py 当房主

# 4) 单机对照
SDL_VIDEODRIVER=dummy PYTHONPATH=. .venv/Scripts/python.exe tools/ui_smoke.py
SDL_VIDEODRIVER=dummy PYTHONPATH=. .venv/Scripts/python.exe tools/ui_snapshot.py
```

本阶段的实际运行结果：

| 命令 | 结果 |
| --- | --- |
| 完整测试套件 | **1110 项全部通过** |
| `lan_playability_sync` | **62/62**（含五谷 6 项真验证） |
| `lan_gameplay_bridge` | **41/41** |
| `lan_gameplay_ui` | **23/23** |
| `lan_smoke` | **44/44** |
| `lan_presentation_sync` | **87/87** |
| `lan_view_sync` | **48/48** |
| `lan_two_process` | **11/11** |
| `ui_smoke` / `view_as_smoke` / `phase10_4` / `phase10_5` | 全部通过 |

### 工具本身的修正（这些不是"改测试凑通过"）

* `tools/lan_playability_sync.py`：`answer_via_ui()` 原来"点了某个位置"就返回成功，
  五谷场景只用"收到过 SELECT_CARDS"证明"游客取到了牌"——正是这个宽松判定掩盖了
  P1。现在判据是"回答真的发出去了"，五谷场景额外验证：候选 zone 是公共池、
  牌真的进手牌、公共池真的 -1、五谷真的结束。
* `tools/lan_gameplay_bridge.py` / `lan_gameplay_ui.py` / `lan_smoke.py`：这三个是
  Phase 11.1/11.2 时期的工具，它们的场景按"2 人自由混战"写，而联机默认模式后来
  变成了标准身份局（补位 AI + 先看身份再选将），于是"开局后客户端直接进牌桌"的
  期望不再成立。现在它们显式选择自由混战模式（工具自己的场景假设），
  身份局那一段由 Phase 11.4 的测试与 `lan_playability_sync` 覆盖。

## 13. 主要修改文件

主机侧

* `src/network/decisions.py`：拒绝码体系、`DecisionResult.action_id/result_name`、
  两级成功（`validate` / `ack`）、决策生命周期日志。
* `src/network/match.py`：ACK/REJECT、兜底重发、`RESTART_REQUEST`/`MATCH_RESTART`、
  结算后离房不再 abort。
* `src/network/protocol.py`：新消息类型与拒绝文案。
* `src/game/controllers/remote.py`：区域语义（`_zone_index`）、多来源响应、
  `action_id` 方式解析、来源区域校验、`resolve()` 的两级返回。

客户端侧

* `src/ui/remote_control.py`：交互状态机（选方式 / 多来源 / CHOOSE_OPTION）、
  未知动作安全忽略、提交时回传方式。
* `src/ui/view_adapter.py`：`RemoteDecisionState`（`action_id` + 阶段枚举）、
  方式面板、响应方式链路、区域对象原地更新。
* `src/ui/remote_table.py`：任意数量选项、拒绝提示、restart/menu 接线。
* `src/ui/decision_presentation.py`：方式查找与"还差几张牌"文案。
* `src/choice.py`：任意数量选项的选择框。
* `src/ui/client_fx.py`：单张牌尺寸、落点语义、飞行去重、摸牌起点。
* `src/ui/table.py`：弃牌堆常驻卡背封面。
* `src/ui/lan_scene.py`：房主结算页动作过网络、`MATCH_RESTART` 回大厅。

测试与工具

* 新增 `tests/test_engine_v2_phase11_5_lan_client_full_fix.py`（15 项，覆盖上述全部修复）。
* `tests/test_engine_v2_phase11_4_lan_client...`、`tests/test_engine_v2_phase11_2_bridge.py`、
  `tests/test_engine_v2_phase11_4_3_lan_identity_runtime.py` 按新语义更新断言。
* `tools/lan_playability_sync.py`、`tools/lan_gameplay_bridge.py`、
  `tools/lan_gameplay_ui.py`、`tools/lan_smoke.py` 修正误报/过期假设。
* `src/game/skills/conversion_probes.py`：新增测试探针 `probe_pair_shan`
  （两来源的响应型转化，用于验证多来源响应链路）。

## 14. 遗留与下一步

1. **丈八蛇矛（及其它"多张实体牌当一张"的装备转化）在联机里仍然发不出来**：
   它只有单机遗留路径（`basic_cards.py` 的 `zhangba_selecting`），discovery 里没有
   对应 conversion。让它在两个场合都成为"牌的一种用法"是一次**规则改动**
   （单机交互也会多出一个"选择操作"入口），建议单独一轮做，并同时迁移单机与 AI。
   联机链路本身已经就绪：本阶段用 `probe_pair_sha`（play）与 `probe_pair_shan`
   （response）验证了"两张实体牌 → 一张逻辑牌"的完整往返。
2. **真人手感验收**：本轮在 SDL dummy 下用真实鼠标事件 + 真实 TCP 完成了
   Host + Client 的全部场景（含跨进程 `main.py --host`），但**没有**在两台物理电脑、
   两块屏幕前手玩过。建议下一轮按审查报告的场景 A～F 双机手玩一遍，
   重点看：飞牌节奏、响应牌落点、弃牌堆封面、结算页两个按钮。
3. `RemoteHumanController` 里仍有一批技能因为"交互还没远程化"
   （`needs_local_ui`）而下发为 `enabled=False`：它们不会卡死，但游客点不动。
   这批技能需要逐个把选牌/选目标搬进 `DecisionRequest`，属于后续阶段。
4. 决策生命周期日志默认关闭（`SANGGUOSHA_DECISION_DEBUG=1` 打开）：
   排查线上问题时建议临时打开，正常游玩不刷屏。

---

## 追加（同日）：指向箭头走线 / 节奏 + "判定之后的行动等判定结束"

这三条来自实机反馈，改的都是**单机 / 房主 / 游客共用**的表现层，所以三种视角
一起生效。

### A. 箭头太乱、有些太短、上下两个面板的走线

**根因**　`ui/table.py::arrow_path()` 只在"两个面板中心距离 < 200 设计像素"时才画
折线，而左侧栏上下相邻的两个面板中心正好相距 **200**（`SEAT_SIDE_STEP`），
`max(|dx|,|dy|) >= 200` 判定成立 → 这两块面板之间画的是一条 **40 来像素的直连线**
（就是"太短"）；顶端座位 → 真人状态条则是一条从 (800,170) 直插 (800,580) 的
**穿过中央出牌位与提示条的直线**（就是"太乱"）。

**改法**（`src/ui/table.py`）

* 同一列（中心横向差 ≤ 60px）→ 走侧面走廊：**横向出去 → 纵向走 → 横向进入**；
  偏左的一对从右侧绕（"向右 → 向下 → 向左"，正是五人局左侧栏那两个面板的形状），
  偏右的一对镜像；来/回两条箭头走**不同车道**（相差 28px），不会完全重叠。
* 居中的一对（顶端座位 ↔ 真人状态条）→ 向下走桌面右侧走廊、向上走左侧走廊，
  两条一上一下各占一边，贴着桌面边缘（内缩 8px），只压到提示条/手牌区的边框。
* 同一行（纵向差 ≤ 60px）→ 走桌面内侧的水平走廊（中央出牌位上方）。
* 其余斜对角 → 保持直线；贴在一起的斜对角仍走原有的侧面绕行兜底。

目检截图：`tools/ui_snapshots/phase11_5_arrows_left_column.png`（五人局左侧栏上下
两个面板：向右 → 向下 → 向左 + 镜像的一条）与 `phase11_5_arrows_2p.png`。

### B. 箭头速度太快

* `arrow_enter` 0.30 → **0.60**，`arrow_hold` 0.90 → **1.15**（都随节奏档位缩放）。
* 箭头不再整体淡入，而是**沿路径长出来**：`TargetArrow.progress` 0→1，
  只画到当前进度、箭头跟着尖端走（`truncate_path`）。方向与指向一眼可见。

### C. 任何判定之后的行动必须等判定结束

**根因**　判定面板是纯表现状态机，和动作队列完全无关：引擎的判定是同步跑完的，
之后排进队列的行动（动画、AI 的延迟回调）立刻就开始播，于是"判定还在演，
下一件事已经发生"。

**改法**

* `ActionQueue.hold`：表现层可以压住队列；压住时**不开始下一个动作**（正在播的
  那个照常跑完），`busy` 仍然为真（回合守卫不会误判成卡死）。
* `JudgePanel.holds_actions`：判定面板 active 期间为真。
  **唯一的例外**是 `REVEALED_HOLD` 且还没有最终判定牌——那一刻是**引擎在等人**
  （改判窗口，AI 的改判回答本身排在动作队列里），压住会把判定永久卡死：
  实测这个例外的必要性（`JudgeGateIntegrationTests` 的两个用例 + 连跑两遍身份局
  联机测试 41 项全过）。
* `Effects.sync_action_gate()` 在每帧 `update` 与每帧 `draw` 各同步一次，装到当前
  这份队列上（单机/房主是 `game.actions`，游客是只读视图的 `view.actions`）。
* 没有 UI 的批量演算（无头测试、`multiplayer_smoke`）走的是同一条 `ActionQueue`，
  但没人装门控 → 行为完全不变。

### D. 本轮验证

| 命令 | 结果 |
| --- | --- |
| 完整测试套件（1125 项，含新增 15 项） | 全部通过 |
| `lan_playability_sync` | 62/62 |
| `lan_gameplay_bridge` / `lan_gameplay_ui` / `lan_smoke` | 41/41 · 23/23 · 44/44 |
| `lan_presentation_sync` / `lan_two_process` | 87/87 · 11/11 |
| `ui_smoke` / `view_as_smoke` / `phase10_4` / `phase10_5` | 全部通过 |
| 身份局联机模块连跑两遍 | 41 项 · 41 项 全过（判定门控没有拖慢或卡住流程） |

新增测试：`tests/test_engine_v2_phase11_5_arrow_and_judge_pacing.py`（15 项）。
其中 `test_actions_wait_for_the_judge_and_then_continue` / `test_the_judge_panel_always_ends`
是**真引擎 + 真渲染**的集成用例：判定期间排进队列的行动不许开始，判定演完必须继续。
