# Phase 11.7：丈八蛇矛 = 装备赋予的视为技（单机 + 联机统一）

报告日期：2026-09-24
范围：只修【丈八蛇矛】的发动与结算路径。未改其它武器 / 防具规则，未新增武将，
未调整任何数值。

---

## 1. 问题（用户报告）

装备丈八蛇矛后"不能发动技能确认了"，单机与联机都打不出"两张手牌当【杀】"。

三条独立的成因：

| # | 现象 | 成因 |
| - | ---- | ---- |
| 1 | 单机：点武器进入选牌后，点手牌没反应 | 点击路由仍走旧状态 `game.zhangba_selecting`，而 `LocalHumanController.toggle_card_source` 只处理"新转化流程"（`pending_view_as` / `pending_card_action`），旧状态落到最后的分支上直接返回 |
| 2 | 联机：点武器只有一句提示，选牌也发不动 | 丈八从未注册进统一的 `CardConversion` 转化系统，房主下发的候选里根本没有"两张手牌当【杀】"这一条；客户端点武器只是显示提示 |
| 3 | 单机：响应阶段（【决斗】【南蛮】）打不出杀 | 旧入口 `try_player_zhangba` 在 `response.active` 或非出牌阶段直接返回 |

## 2. 根因

丈八是**装备**，但装备在这个项目里有两种规则实现方式：

* 锁定效果（青釭剑 / 藤甲 / 八卦阵…）：由 `EquipmentEventSkill` 与各 Flow 直接读装备判断；
* 赋予技能：装备在装备区时给持有者一个技能定义。

丈八属于第二种（"你可以将两张手牌当【杀】使用或打出"），却走了**第三套**：
单机专用的一条遗留通道（`zhangba_selecting` / `zhangba_selected` + 4 个 `player_zhangba_*`
方法）。于是：

* 它不在 `SkillManager` 里 → 技能栏、`view_as_options`、`begin_view_as` 全都看不到它；
* 它不在 `ConversionRegistry` 里 → 房主下发的候选（`playable_cards` / `_response_cards`）、
  AI 的候选、响应窗口的候选都没有它；
* 它自己的选牌状态与新的 `pending_view_as` / `pending_card_action` 互不认识。

## 3. 修改

### 3.1 规则层：装备赋予的技能（新文件）

`src/game/equipment_skills/granted.py`

```text
ZHANGBA_SKILL = SkillDef(
    id="equipment.zhangba", kind=VIEW_AS,
    conversions=(CardConversion(
        skill_id="equipment.zhangba", matches=任意实体手牌, name="SHA",
        min_sources=2, max_sources=2,
        source_zones=(HAND_ZONE,), contexts=(PLAY, RESPONSE)),),
)
GRANTED_BY_EQUIPMENT = (("ZHANGBA", "equipment.zhangba"),)

sync_equipment_skills(game, player)   # 幂等：按装备区实际内容绑定 / 解绑
```

* 技能 id 用 `equipment.` 命名空间，`create_default_skill_registry()` 一并注册；
* 绑定时机由**装备区的实际内容**决定，不看"装备了 / 卸下了"这类增量事件：

```text
src/game/atoms_v2.py
    EquipCardAtom.apply   → sync_equipment_skills
    UnequipAtom.apply     → sync_equipment_skills   （EQIPMENT_LOST 事件之后）
src/game/ai.py
    _legacy_ai_equip      → sync_equipment_skills   （旧 1v1 AI 的 set_equipment 路径）
```

于是换装、被拆、被顺、借刀、死亡清理无论走哪条路径，结果都一样：
装备在 = 技能在（转换已注册、技能栏可见、视为技可进入），装备不在 = 技能没了。

### 3.2 单机：点武器走统一的视为技流程

* `ui/interaction.py`：删除 `game.zhangba_selecting` 分支；"点击已装备的武器"改为
  `human.try_zhangba()`（判断依据是"这件武器是否赋予技能"）；选牌中再点那件装备 = 取消。
* `ui/human_control.py`：`LocalHumanController.try_zhangba()` → `game.begin_view_as(装备赋予的技能 id)`；
  删除 `confirm_zhangba` / `cancel_zhangba` 两个动作分支（按钮已不存在）。
* 响应阶段不需要任何特殊处理：`current_card_action_context()` 在响应窗口里给出的
  `allowed_names` 已经包含【杀】，`begin_view_as` → 收齐两张 → `_submit_view_as_response`
  就是【龙胆】把【闪】当【杀】打出的同一条路径。
* 素材凑不齐时不进入选牌（`discovery.assemblable`）：只剩一张手牌时点武器会得到
  "现在不能发动【丈八蛇矛】：至少需要 2 张可以转化的牌。"，而不是进一个永远选不满的界面。
* 收齐后如果结果牌不可用（本回合已出过【杀】/ 距离不足），提示改为 CardEffect 给出的
  具体原因，而不是笼统的"这些牌现在不能使用"。

### 3.3 联机：客户端点武器 = 选中房主下发的用法

房主侧不需要新代码：注册成 `CardConversion` 之后，`playable_cards` 与 `_response_cards`
自动把"两张手牌当【杀】"（`min_sources` / `max_sources` = 2）下发给远程真人。

客户端 `ui/remote_control.py`：

* `try_zhangba()`：在自己装备的武器赋予的技能 id 上，找到房主下发的对应方式
  （`skill_id` 精确匹配），把它记成本次要用的方式并提示"请选择两张手牌"；
  再点一次武器 = 取消已选方式。
* `_tap_play_card` / `_tap_response_card`：如果玩家已经通过点武器选定了方式，
  直接按它继续收集来源，不再要求他在"选择操作"面板里重复挑一次。
* 客户端仍然一条规则都不算：选满两张后提交的是 `card_ids` + `skill_id` + `action_id`
  + `result_name`，房主用 `actions_for_sources` / `validate` 重新解释这组实体牌。

### 3.4 顺手修掉的 ADJACENT 问题

* **AI 不能把候选当单张转化用**：`needs_more_sources` 的候选只是"还差一张"，
  交给结算等于凭空少付一张牌。`AIController.converted_play_card` / `converted_response`
  现在只取已凑齐来源的转化（AI 暂不做多来源收集）。
* 删除死代码：`zhangba_selecting` / `zhangba_selected` 字段与 4 个 `player_zhangba_*`
  方法、渲染层的"确认出杀 / 取消丈八"按钮、提示条分支、`zhangba_confirm` 的 AI 分支。
* **修掉一条既有测试的误报**（与丈八无关，但会让全量套件随随机武将变红）：
  `test_engine_v2_phase11_4_3_lan_identity_runtime.GeneralAndSkillTests.`
  `test_scenario_f_remote_skill_bar_matches_the_host` 期望"客户端技能 == 武将
  `skill_ids`"，但主公技（孙权的【救援】）只在该角色是主公时才绑定；随机抽到
  非主公的孙权时必然失败。期望值改为按引擎的绑定规则去掉"没被绑定的主公技"
  （非主公技的强度不变）。

## 4. 改动文件

规则层：

```text
src/game/equipment_skills/granted.py      新增（装备赋予的技能 + 幂等同步）
src/game/atoms_v2.py                      装备 / 卸下时同步装备技
src/game/skills/__init__.py               注册表包含装备技
src/game/ai.py                            旧 1v1 装备路径同步
src/game/basic_cards.py                   删除旧丈八选牌方法（保留 find_hand_card_index）
src/game/core.py                          删除 zhangba 状态；等待判定改用 pending_view_as
src/game/card_action_session.py           凑不齐不进入；失败提示带真实原因
src/game/card_actions/discovery.py        assemblable()：这些候选凑不凑得齐
src/game/controllers/ai.py                多来源候选不参与单张转化；删死分支
```

UI / 联机：

```text
src/ui/interaction.py                     点武器 → 统一入口；选牌中点装备 = 取消
src/ui/human_control.py                   try_zhangba → begin_view_as
src/ui/remote_control.py                  点武器选中房主下发的方式（多来源）
src/ui/view_adapter.py                    删除 zhangba 兼容字段
src/ui/player.py / src/ui/prompt.py       删除旧丈八高亮 / 提示分支
src/renderer.py                           删除旧"确认出杀"按钮状态
tests/legacy_helpers.py                   装备直塞时同步装备技
```

测试：

```text
tests/test_engine_v2_phase11_7_zhangba_view_as.py   新增 16 个用例
tests/test_engine_v2_phase11_4_3_lan_identity_runtime.py  修掉主公技期望的既有误报
```

## 5. 验证

`tests/test_engine_v2_phase11_7_zhangba_view_as.py`（全部走真实点击）：

| 场景 | 断言 |
| ---- | ---- |
| 装备 / 卸下 | 装备即绑定、卸下即解绑，`view_as_skill_ids` 同步变化 |
| 单机点武器 | 进入 `pending_view_as`，`skill_id = equipment.zhangba`，`required_source_count = 2`，全部手牌可点 |
| 单机结算 | 两张手牌进弃牌堆、`sha_used = True`、战报含【丈八蛇矛】、虚拟【杀】带两张 source |
| 单机取消 | 再点武器 = 取消，不弃牌、不消耗出杀次数 |
| 单机 legacy 响应 | 响应窗口里点武器 → 两张牌 → `response` 回调收到 |
| 单机引擎 Pending | 真实【南蛮入侵】的响应窗口（`engine.pending`）：点武器 → 两张牌 → 战报是"打出" |
| 素材不足 | 只剩一张手牌时点武器不进入选牌，并给出原因 |
| 普通用牌 | 装备丈八后点【桃】仍正常回血，不弹转化面板 |
| 技能栏 | 装备技出现在技能区，点它 = 同一个入口 |
| 联机下发 | 房主给远程真人的候选里有 `skill_id = equipment.zhangba`，`min/max_sources = 2` |
| 联机出牌 | 客户端点武器选中该方式 → 两张牌 + 目标 → 房主接受、记出杀次数、战报可见 |
| 联机响应 | 真实【南蛮入侵】响应里客户端用两张手牌当【杀】打出 → 房主接受、牌离手 |
| AI | AI 不会用一张手牌冒充两张手牌转化的【杀】（出牌与响应两条路径） |

配套冒烟（均通过）：

```text
tools/view_as_smoke.py     视为技 7 项全 PASS（龙胆路径未被破坏）
tools/ui_smoke.py          UI 交互全 PASS（含丈八之外的技能与按钮）
tools/skill_static_audit.py  无死事件；死标记列表里已无 zhangba 状态
```

全量套件：见下方"运行结果"。

## 6. 已知限制

* **AI 不会主动收集多来源**：装备丈八的 AI 只用真【杀】；丈八对 AI 目前是"不误用"
  而不是"会用"。要让它会用，需要在 `AIController` 里加一次两牌组合搜索。
* **旧 1v1 legacy AI**（`src/game/ai.py`）仍保留它自己的丈八实现（直接构造虚拟【杀】），
  与新路径并存但不冲突：它走旧的动作队列，不消费 Conversion。
* 丈八的转化只在**手牌**之间成立（`source_zones=(HAND_ZONE,)`），与卡面一致。

## 7. 运行结果

```text
SDL_VIDEODRIVER=dummy .venv/Scripts/python.exe -m unittest discover -s tests -t . -p "test_*.py"
→ Ran 1167 tests in 191.4s — OK

SDL_VIDEODRIVER=dummy PYTHONPATH=. .venv/Scripts/python.exe tools/view_as_smoke.py   → 通过
SDL_VIDEODRIVER=dummy PYTHONPATH=. .venv/Scripts/python.exe tools/ui_smoke.py        → 通过
SDL_VIDEODRIVER=dummy PYTHONPATH=. .venv/Scripts/python.exe tools/skill_static_audit.py
→ 无死事件；死标记只剩既有的 network / UI 私有字段
```

（1167 = 原基线 1151 + 本次新增 16 个用例；两次全量之间还出现过一次
`test_engine_v2_phase11_4_3_lan_identity_runtime` 的随机武将误报，已在上文
§3.4 修掉。）
