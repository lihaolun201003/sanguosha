# Phase 12：1v1 测试模式（开战前自由指定双方武将）

## 一、目标与结果

在**不复制整套游戏**的前提下，给玩家一个专门用来验证武将组合的两人模式：

* 主菜单多一个模式入口「1v1 测试」，点「开始游戏」直接进**开战前设置页**；
* 双方武将都从**完整武将注册表**里挑（不受三选一限制），支持搜索、势力筛选、
  扩展包筛选、分页，能看卡面 / 体力 / 势力 / 完整技能；
* 先手（我方 / 对方 / 随机）、操作方式（玩家对AI / 双边手动测试）、随机种子
  都在开战前定好，**看清楚双方选谁之后才开局**；
* 双边手动测试时，视角跟着"当前正在决策的那一方"切换，杀 / 闪、无懈可击、
  主动技能、濒死救援这些**嵌套响应**都由同一个人在本机轮流操作；
* 对局中与结算后都能：原配置重开 / 返回设置换将 / 返回主菜单。

规则引擎、牌桌、技能与装备系统、结算与控制器**全部复用**，模式只负责模式业务
（见 `src/game/modes/duel.py` 的模块说明）。

## 二、启动与操作

```bash
# 全屏启动（默认就是在桌面分辨率下全屏；ESC 退出全屏，F11 切换）
.venv/Scripts/python.exe main.py
```

1. 主菜单 → 点游戏模式里的 **「1v1 测试」** → 点 **「开始游戏」**；
2. 设置页左上角是武将浏览器：
   * 点右侧 **「我方」/「对手」面板**切换"现在给谁选"；
   * 在搜索框里打武将名 / 技能名，或点 **势力 / 扩展包** 按钮过滤；
   * 点武将牌即可指定；下方 **「上一页 / 下一页」**（或鼠标滚轮在网格上滚动）翻页；
   * 选错的条目会灰掉并写明原因（未实现的武将**不允许开局**，不会被悄悄换掉）；
3. 右侧定 **先手**、**操作方式**、**随机种子**（留空 = 每次随机）；
4. **「开始对战」**（双方都选好才可点）；战斗中控制条常驻左上角：
   * 显示 **「当前操作：我方 · 关羽（本方回合 · 出牌阶段）」**；
   * **原配置重开** / **换将** / **主菜单** 三个按钮随时可用，结算面板出现后照样可点。

双边手动测试下的操作方式与单机完全一致：**谁在操作，就点谁的手牌与按钮**。
轮到对手时，桌面会整体切换成对手视角（他的手牌在下方、我方缩到上方），
左下角的大字提示会同时告诉你现在是谁在操作。

## 三、文件清单

### 新增

| 文件 | 作用 |
| --- | --- |
| `src/game/modes/duel.py` | 模式本体：`DuelConfig`（双方武将 / 先手 / 操作方式 / 种子）、`DuelTestMode`（开局、首行动、视角跟随、群体请求顺序询问、胜负、结算文案） |
| `src/ui/duel_setup.py` | 开战前设置页（武将浏览器 + 双方选择 + 设置 + 按钮），纯数据驱动 |
| `src/ui/duel_hud.py` | 对局内控制条（当前操作提示 + 重开 / 换将 / 主菜单） |
| `tools/duel_capture.py` | **挂真实主循环**的验收脚本：按真人动作走一遍并截图（见第六节） |
| `tools/duel_snapshot.py` | 离屏渲染 1920×1080 的大图（设置页 / 牌桌 / 响应窗口） |
| `tests/test_engine_v2_phase12_duel_test.py` | 49 项测试（设置层 / 开局 / 双边手动 / 种子 / 重开清理 / 兼容性） |

### 改动（都是通用能力，没有模式特判）

| 文件 | 改动 |
| --- | --- |
| `src/game/modes/base.py` | 新增模式钩子：`before_reset` / `prepare_players` / `open_setup` / `on_frame` / `operator_target` / `present_group` / `general_note` / `overlay_result_texts` / `restart_battle`；注册 `duel_test` |
| `src/game/core.py` | 随机源提前到建牌堆之前（`Deck(rng)`）；`ai_rng`（可复现的 AI 随机）；`set_operator` / `sync_operator`（当前操作者）；`reset()` 调模式钩子；`update()` 调 `on_frame`；`restart_battle()`；`return_to_menu()` 清 `general_assignments` / `general_picks` |
| `src/deck.py` | 洗牌改用注入的随机源（不传时行为不变） |
| `src/player.py` | `set_general` 同时写入 `kingdom`（见"顺带修掉的两个真实缺陷"） |
| `src/game/generals/definitions.py` | `GeneralDef` 新增 `pack` / `version` / `implemented` / `unavailable_reason`、`display_name`、`availability` |
| `src/game/generals/registry.py` | `packs()`、`by_pack()`、`labelled_names()`（同名不同版本的标签） |
| `src/game/engine/runtime.py` | `present_group` 改为"由模式决定怎么问"；呈现请求前切一次当前操作者 |
| `src/game/controllers/human.py` | `holds_panel`；响应 / 二选一面板带上 `responder`（面板归属） |
| `src/response.py`、`src/choice.py` | 两个请求对象新增 `responder` 字段（只用于界面归属） |
| `src/game/basic_cards.py` | 战报按**出牌的人**记名字（见"顺带修掉的两个真实缺陷"） |
| `src/ui/overlay.py` | 结算文案可以交给模式（1v1 用中立文案） |
| `src/ui/human_control.py` | 「重新开始」改走 `game.restart_battle()`（模式可先自己接住） |
| `src/ui/layout.py` | 底部那一组内容改为**贴屏幕底边**（见第五节） |
| `main.py` | 新场景 `duel_setup` 的事件路由 / 绘制分发；对局内控制条接管自己的三个按钮；`settings` / `hud` 注入运行期挂钩 |

## 四、规则口径（1v1 测试模式）

* 固定两人；**不套用**身份局的胜负与主公规则，也**不套用**官方竞技 1v1 的选将、
  替补与专属技能改写；
* 双方按所选武将初始化体力（`Player.set_general` 把体力设成武将上限），
  普通开局各四张手牌；先手方的摸牌阶段照常摸两张，武将的准备阶段能力
  （观星 / 洛神之类）按既有规则生效；
* 先手按设置：我方 / 对方 / 随机（随机走全局随机源，固定种子时可复现）；
* 濒死、救援、复活与死亡能力全部走既有流程；死亡结算 = **最后存活者获胜**，
  文案是中立的"我方 · 关羽 获胜"，不写"你赢了"；
* **主公技不激活**：本模式没有身份（`uses_identities = False`），刘备的【激将】、
  孙权的【救援】绑定阶段就被门控掉；设置页会在这些武将的说明里写明
  （`GameMode.general_note`），不会让人以为"技能坏了"；
* 技能自己说"没有合法目标"时照原样显示原因，本模式不为两人对局强行改写效果；
* 非标准扩展包 / 形态的武将会在详情里标 **「测试用配置」**，不擅自套用完整特殊场景。

## 五、实现要点

### 1. 视角跟随：一套界面，两个操作者

`game.player` 在这套代码里本来就是"本机鼠标现在代表谁"。双边手动模式声明
`tracks_operator = True`，由 `Game.sync_operator()` 在**每帧**与**每次呈现请求之前**
把它切到"当前该操作的人"：

1. 有人正在被问（`engine.pending.current`）→ 切到那个人；
2. 本机响应 / 二选一面板开着（`response/choice.current.responder`）→ 切到面板主人；
3. 否则 → 切到当前回合角色。

因此手牌、按钮、提示、装备区、目标高亮全都跟着走，**不需要在回合开始时切换**，
也不需要第二套界面。玩家对 AI 模式下 `tracks_operator` 为 False，视角永远在我方，
原有单机行为一行不变。

### 2. 只有一个鼠标：群体请求改成按顺序问

共享无懈阶段原本是"同一轮同时问所有有资格的人"。双边手动模式下本地只有一块
面板，同时问会丢掉前一块，于是：

* `GameEngine.present_group` 改为委托给 `GameMode.present_group`；
* 默认实现仍是"同时问所有人"（自由混战 / 身份局 / 联机一字未改）；
* 1v1 双边手动模式覆写成**按座次逐个问**：谁拿到面板就停下来等他；
  他放弃后再问下一位；每帧再由 `on_frame` 补问"还没被问到的人"，
  所以"一方点不出 → 另一方照样拿到面板"这条路径不会卡死（有测试守着）。

### 3. 可复现随机：一颗种子覆盖四处

固定种子时 `before_reset` 会 `game.rng.seed(种子)`，并让 AI 控制器共用这条流：

| 随机点 | 覆盖方式 |
| --- | --- |
| 洗牌 / 重洗 | `Deck(rng)` —— Game 把自己的随机源交给牌堆 |
| 先手（随机时） | `mode.first_player()` 用 `game.rng.choice` |
| AI 决策 | `Game.ai_rng = game.rng`（`AIController` 直接用它打分与选择） |
| 选将 / 身份 | 沿用既有 `game.rng`（1v1 不用随机选将，但种子仍然管着这条流） |

留空则每次 `rng.seed()` 取系统熵，与以前一样随机。

### 4. 重开与清理

「原配置重开」= `Game.restart_battle()` → 模式的 `restart_battle()` → `start_local_battle(1)`：
`reset()` 里已经按正确顺序清掉动作队列、响应、二选一、挂起请求、技能监听
（`skills.clear()` → 逐个 `unsubscribe`）、修正器、转化、旧角色对象与它们的
`skill_state`，随后重建角色、重新洗牌发牌、重新绑定双方武将。测试里反复重开
5 次，**监听数不涨、技能状态为空、队列干净**（`test_many_restarts_stay_stable`）。

「返回设置换将」走 `game.begin_general_select()`：先 reset，再进设置页；
`general_pool` / `general_assignments` / `general_picks` 一并清空，
**指定武将不会泄漏给自由混战 / 身份局**（有测试守着）。

### 5. 底部布局：手牌不再悬在半空

原来整块界面按设计比例居中，4:3 屏幕上会上下各留一条空带——手牌因此停在中下部，
下面空出一大片（这就是玩家看到的问题）。现在 `LayoutMetrics` 区分两组锚点：
上半部分（对手座位 / 中央出牌位）保持设计坐标，**底部那一组**（提示 / 状态条 /
手牌 / 固定按钮）用 `to_bottom_screen()` 贴住真正的屏幕底边。

* 16:9（1600×900 / 1920×1080）逐像素与原来一致（有断言）；
* 1024×768 上，手牌底边从"离屏幕 107px"变成"离屏幕 11px"，与设计留白一致；
* 布局审计 `tools/ui_audit.py`：105 组合 0 处问题。

## 六、验收

### 自动化测试

```bash
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest discover -s tests -t . -p "test_*.py"
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe tools/ui_audit.py
```

* 新增 `tests/test_engine_v2_phase12_duel_test.py`：**49 项全通过**；
* 全量套件：**1216 项全通过**（含既有的架构守卫：通用层不许按具体武将分支）。

覆盖的验收点：

| 验收要求 | 对应测试 |
| --- | --- |
| 双方都能从完整武将池选，新增武将自动出现 | `test_full_roster_is_selectable`、`test_new_general_and_pack_appear_automatically` |
| 同名不同版本有明确标签 | `test_same_name_different_version_has_label` |
| 未实现条目展示但禁止开局 | `test_unimplemented_entry_is_visible_but_blocked` |
| 非法配置不能开局 | `test_illegal_config_cannot_start`、`test_start_blocked_until_both_sides_chosen` |
| 双方选同一武将、状态互不影响 | `test_mirror_match_keeps_skill_state_separate` |
| 指定的 AI 武将不被重抽 | `test_ai_general_is_not_re_randomised` |
| 我方 / 对方 / 随机先手 | `test_first_player_options` |
| 双边手动：杀 / 闪 | `test_sha_and_shan_in_manual_mode` |
| 双边手动：无懈（含一方放弃） | `test_wuxie_window_asks_both_sides_in_order`、`test_wuxie_window_survives_a_pass` |
| 双边手动：主动技能 | `test_active_skill_activation_by_mouse` |
| 双边手动：濒死救援 | `test_dying_rescue_is_played_by_the_other_side` |
| 嵌套响应后控制权恢复 | 上述测试里对 `local_operator_label` / `game.player` 的断言 |
| 固定种子可复现（含 AI） | `TestDeterministicSeed` 六项 |
| 连续重开 / 换将无残留 | `test_restart_keeps_the_config_and_clears_the_state`、`test_many_restarts_stay_stable`、`test_switching_generals_between_battles` |
| 搜索框不被当快捷键 | `test_keyboard_goes_to_search_box_not_shortcuts` |
| 原有模式不受影响 | `test_original_modes_still_work`、`test_leaving_the_mode_does_not_leak_the_chosen_generals`、`test_design_size_is_unchanged` |

### 真实主循环截图

```bash
# 双边手动：主菜单 → 设置页 → 选将 → 开局 → 出杀 → 闪 → 无懈 → 重开 → 结算
SDL_VIDEODRIVER=dummy SGS_RUNTIME_SCRIPT=duel_capture SGS_DUEL_SCENARIO=manual \
    SGS_DUEL_OUT=tools/ui_snapshots/duel_manual .venv/Scripts/python.exe main.py
# 玩家对AI：同上但 SGS_DUEL_SCENARIO=ai
```

脚本的每一次点击都走真人那条入口（主菜单 `StartMenu.handle_click`、设置页
`DuelSetupScreen.handle_event`、牌桌 `ui.interaction.handle_game_click`），
截图就是主循环刚画完的那一帧。

| 截图 | 内容 |
| --- | --- |
| `duel_manual_01_setup_empty.png` | 设置页：未选择（开始对战为禁用态） |
| `duel_manual_02_setup_ready.png` | 设置页：我方关羽 / 对手吕布、双边手动、种子 20240925 |
| `duel_manual_03_battle_my_turn.png` | 开局：当前操作 = 我方 · 关羽 |
| `duel_manual_04_battle_next_turn.png` | 结束回合后：整块桌面切换成对手视角，手牌贴在屏幕最下方 |
| `duel_manual_06_manual_shan_request.png` | 我方出【杀】→ 对手的【闪】响应窗口（视角已在对手身上） |
| `duel_manual_07_manual_after_shan.png` | 【闪】结算后控制权回到我方 |
| `duel_manual_08_manual_wuxie_window.png` | 共享无懈阶段（逐个询问） |
| `duel_manual_10_manual_restart.png` | 原配置重开：双方武将 / 先手 / 种子不变，状态干净 |
| `duel_manual_11_duel_over.png` | 结算面板（中立文案"我方 · 关羽 获胜"） |
| `duel_1_setup_empty.png` / `duel_2_setup_chosen.png` | 1920×1080 大图：设置页 |
| `duel_3_battle.png` / `duel_4_response.png` | 1920×1080 大图：牌桌与控制条 |

## 七、顺带修掉的两个真实缺陷

1. **主公技永远发动不了**（`src/player.py`）：【激将】【救援】要问"你是不是蜀 / 吴
   势力"，但 `Player` 上从来没有 `kingdom` 字段——绑得上、却永远触发不了。
   现在 `set_general` 会把武将的势力写进角色。（1v1 模式不用，但身份局成立。）
2. **双边手动模式下战报写错人**（`src/game/basic_cards.py`）：那张"谁使用了什么牌"
   的战报读的是 `self.player`（本机鼠标指向谁），视角一跟随就会写成另一方。
   现在按**出牌的人**记。

## 八、已知限制

* 双边手动是"一个人轮流操作两边"，因此共享响应阶段（无懈）按座次**逐个询问**，
  而不是两个人同时抢答——这是本地单鼠标下唯一可行的语义，非联机抢答；
* 1v1 测试模式只提供本地对局，没有联机入口（房主侧若把模式设成 `duel_test`，
  开局流程仍按本地走）；
* 响应窗口沿用既有单机行为：手里没有合法响应牌时仍会弹面板（只能点"不出"），
  这是所有模式共有的旧表现，本次没有改动它；
* 底部锚点只调整了"底部那一组"的位置；整块界面在极端比例（如 21:9）下仍按
  设计比例居中，中间留白属于既有设计。
