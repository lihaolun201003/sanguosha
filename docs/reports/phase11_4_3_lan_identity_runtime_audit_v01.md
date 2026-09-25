# Phase 11.4.3 审计：LAN 开局为什么不是身份局

本文件是对**真实运行路径**的静态审计结论，不含任何修改。所有行号取自审计当时的
工作副本（`src/game/core.py`、`src/network/match.py` 等）。

## 一、单机身份局的完整调用链

```
main.py:336  start_menu.handle_click(pos, game)
  → src/start_menu.py:163   game.begin_general_select()            [core.py:818]
        guard.reset()
        mode.roll_identities()          ← 按人数配比生成身份牌（5 人：主/忠/反/反/内）
        mode.apply_identities(...)      ← 按座次写入 player.identity，主公 identity_revealed=True
        scene = "identity_reveal"

main.py:367  identity_reveal.handle_click → game.confirm_identity() [core.py:835]
        general_candidates = roll_general_candidates()   ← 三选一候选
        scene = "general_select"

main.py:382  general_select.handle_click → game.confirm_general(id) [core.py:844]
        general_pool = tuple(self.generals.ids())        ← 真实武将池（25 个）
        selected_general = id
        → game.start_local_battle(ai_count)              [core.py:916]
              self.reset()                                ← 重建 players / 发 4 张 / assign_generals
              mode.apply_identities(pending_identities)   ← 身份真正落到玩家身上
              mode.setup_battle()                         ← 主公 +1 体力上限
              scene = "game"
              start_turn(mode.first_player())             ← 主公先手
```

真正决定"这是一局身份局"的四件事：**身份写入、武将绑定、模式开局修正、首行动角色**。

## 二、LAN 房主的真实调用链

```
src/ui/lobby.py:127  session.start_match()                        [session.py:222]
  → src/network/host.py:205 host.start_match()   （只广播 START_GAME / LOBBY_STATE）
  → src/network/match.py:242 HostMatch(self, self.game, self.host.lobby)
  → src/network/match.py:81  HostMatch.start()
        seats = [(member.player_id, nickname, seat, controller_type, player_id)
                 for member in self.lobby.ordered()]        ← 只来自大厅真人
        game.remote_controller_factory = self.make_controller
        game.start_networked_battle(seats, general_pool=self.general_pool())   [core.py:858]

src/game/core.py:858 start_networked_battle(seats, ...)
        self.reset()
        按 seats 重建 players（只有真人；旧玩家被整体替换）
        deck.reset() + 每人 4 张
        if self.mode.uses_identities: roll_identities + apply_identities
        self.general_pool = tuple(general_pool or ())
        self.assign_generals()
        self.mode.setup_battle()
        scene = "game"
        start_turn(first_player or self.mode.first_player())
```

## 三、分叉点

真实运行下，两条链在**三个地方**分开了：

| 环节 | 单机 | LAN（真实运行） |
| --- | --- | --- |
| 模式从哪来 | 主菜单点「标准身份」→ `game.set_mode("identity")` | `create_room(game_mode=game.mode_id)`（multiplayer_menu.py:276），默认 `"ffa"`；`HostMatch.start()` **从不调用 `set_mode`**，只用 `mode.uses_identities` 判断给不给武将池 |
| 武将池 | `confirm_general` 写入 `generals.ids()` | `HostMatch.general_pool()`（match.py:111）只在 `uses_identities` 时给池；ffa 下返回空元组 |
| 人数 | 菜单选总人数（identity 5～8） | `lobby.ordered()` 的真人数量（AI 不补位） |

## 四、`start_networked_battle()` 到底做了什么

它确实调用了 `mode.roll_identities / apply_identities / setup_battle / first_player`——
**这一点很容易让人误判为"已经接回身份局"**（之前的结论就是这样产生的）。但它：

1. 不设置模式（模式由调用方事先决定，而调用方给的是 ffa）；
2. 人数完全由 seats 决定，不补 AI；
3. 武将池由调用方给，给空就不分配（不报错、不提示）；
4. `reset()` 已按旧名单把技能绑在旧 players 上，随后 `self.players` 被**整体替换**，
   旧绑定残留在 `SkillManager` 里（`skills.clear()` 没有被调用）；
5. 不经过 `begin_general_select → confirm_identity → confirm_general`，没有身份展示。

## 五、"二人裸局"的三个独立原因

**原因 A：LAN 房间的模式默认是 ffa。**
`Game.__init__` 里 `self.mode_id = "ffa"`（core.py:118）。玩家除非在主菜单手动点
「标准身份」，否则进联机时模式就是 ffa。`HostMatch.start()` 与 `start_networked_battle()`
都不改模式 → `uses_identities` 为 False → 身份分支整段跳过；
`HostMatch.general_pool()` 返回 `()` → `assign_generals()` 无池可抽 → 无武将、无技能。

**原因 B：没有 AI 补位。**
seats 只由 `lobby.ordered()`（真人）生成。2 个真人 = 2 个 `Player`。
即使模式是 identity，`distribution_for(2)` 返回 `None` → `roll_identities()` 返回空元组
→ 不分配身份（强行分配会 `ValueError: identity count does not match player count`）。

**原因 C：两套开局初始化并存。**
`start_local_battle`（单机）与 `start_networked_battle`（LAN）是两份独立实现。
后者缺少单机流程里的身份展示、选将、技能解绑/重绑等步骤。

## 六、为什么工具与测试都报"身份局已接回"

`tools/lan_view_sync.py`（scenario_identity）、`tools/lan_view_harness.py HostSide`、
`tests/test_engine_v2_phase11_4_2_runtime_parity.py:166` 都在**自己搭的进程里**
手动执行：

```python
game.set_mode("identity")          # 手动选模式
game.pending_identities = game.mode.roll_identities()   # 手动发身份
game.mode.apply_identities(...)
```

这条路径绕过了 `HostMatch.start()` 与 `start_networked_battle()` 的真实开局，
所以工具全绿、真人运行仍是裸局——**测试走一条路，真人运行走另一条路**。
这正是本阶段要引入 Runtime Marker 的动机。

## 七、修复方向（本阶段实现）

1. **模式来源统一**：LAN 大厅的模式由房主在多人菜单里选定（默认「标准身份」），
   `HostMatch.start()` 真正 `game.set_mode(lobby.game_mode)` 后再建局。
2. **AI 补位**：本局人数 = 模式允许人数中 ≥ 真人数的**最小值**（identity 2 真人 → 5 人局，
   补 3 AI；5 真人 → 5 人局；ffa 2 真人 → 2 人局，与旧行为一致）。多余的座位用
   `ControllerType.AI`，仍走原来的 `AIController`。
3. **统一开局规则序列**：把「身份 → 武将 → 模式开局修正 → 首行动角色」抽成
   `Game._apply_opening_rules()`，单机的 `start_local_battle` 与 LAN 的
   `start_networked_battle` 都调用它；LAN 不再有第二套规则初始化。
4. **武将必有**：LAN 开局一律注入真实武将池（`generals.ids()`），
   沿用单机的 `assign_generals()`（同池不重复抽取）。
5. **技能不残留**：换名单时清空 `skills / modifiers / conversions` 再重新绑定。
6. **自己身份可见**：座位身份显示改走 `visible_identity(player, viewer=game.player)`，
   仍然是可见性函数，不直接读 `player.identity`。
7. **Runtime Marker**：开局在控制台打印真实的人数 / 身份 / 武将 / 控制器构成，
   并标出创建路径，避免再次出现"测试通过但真人运行是另一条路"。
