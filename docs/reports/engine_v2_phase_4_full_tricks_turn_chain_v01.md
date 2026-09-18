# Engine V2 Phase 4：完整锦囊、回合判定与属性连环

## 1. 总体完成情况

Phase 4 已完成。项目现在具备普通锦囊、群体锦囊、延时锦囊、无懈响应链、正式判定区、阶段枚举与 TurnFlow、公共牌池、横置状态及火/雷连环伤害。规则目标继续严格限定为标准版 + 军争篇，不包含国战模式、国战牌、双将或国战规则。

## 2. 新增卡牌与 Registry

本阶段新增 8 个 CardEffect：五谷丰登、无懈可击、借刀杀人、乐不思蜀、闪电、火攻、铁索连环、兵粮寸断。

CardEffectRegistry 当前共 16 项：杀、无中生有、过河拆桥、顺手牵羊、决斗、南蛮入侵、万箭齐发、桃园结义，以及上述 8 项。`cancellable_by_wuxie` 仍是 Effect 元数据；基本牌和装备不会散落进行牌名判断。

## 3. 无懈 Response Chain

`WuxieResponseChain` 是迭代 Flow，保存 `responders/current_responder/pass_count/nullified`。每使用一张无懈就反转一次最终状态并重新开放完整响应轮；连续所有角色 Pass 后才结束。因此支持无懈、反无懈及任意更多层。

每次响应都由 `RespondCardAction` 或 `PassPendingAction` 驱动。无懈实体依次经过手牌、处理区、弃牌堆。群体响应锦囊（南蛮/万箭）为每个目标分别建立无懈窗口，不会用一张无懈取消整张牌对所有目标的效果。

## 4. Pending 嵌套

`PendingManager` 已从单槽升级为栈。`current` 是栈顶兼容属性；每个请求包含 request id、owner flow、type、context 和 status。内层请求完成后外层请求仍存在。restart/death 会统一取消并清空遗留请求。

## 5. 处理区与区域一致性

Game 新增 `processing_zone`、`public_card_pool`。主动使用牌走手牌 → 处理区 → 弃牌堆；延时锦囊走处理区 → 判定区；响应牌走手牌 → 处理区 → 弃牌堆。五谷结束时所有牌进入某角色手牌或弃牌堆，公共区清空。

## 6. 判定区与 JudgeFlow

Player 正式拥有 `judgement_zone`。延时锦囊通过 MoveCardAtom 进入和离开该区域；同名延时锦囊不可重复放置。角色死亡时手牌、装备和判定区统一清理到弃牌堆。

JudgeFlow 在 Phase 3 基础上增加 source、target、reason，并发出 `JUDGE_STARTED/JUDGE_REVEALED/JUDGE_BEFORE_RESULT/JUDGE_RESULT/JUDGE_FINISHED`。八卦阵、乐不思蜀、兵粮寸断和闪电共用同一实现。

## 7. TurnFlow 与阶段控制

新增 `TurnPhase`：准备、判定、摸牌、出牌、弃牌、结束。TurnFlow 发出 `TURN_START/TURN_END/PHASE_START/PHASE_END`，并提供完整自动运行和 Pygame 交互式进入出牌阶段两种入口。旧 turn.py 已收敛为 UI/动画 façade，实际判定、摸牌和跳阶段由 TurnFlow 负责。

`PhaseControl` 提供通用 skip，不允许延时锦囊直接跳改 phase。跳过标记属于当前 TurnFlow，回合结束后不会泄漏到下一回合。

## 8. 延时锦囊

- 乐不思蜀：判定非红桃时跳过出牌阶段；判定后弃置。
- 兵粮寸断：距离 1；判定非梅花时跳过摸牌阶段；判定后弃置。
- 闪电：黑桃 2～9 命中，走 3 点雷属性 DamageFlow；未命中移动到下一名存活角色，若无法合法放置则弃置。
- 判定区多牌明确以后放入者先处理，不依赖偶然的正向 list 顺序。
- 延时锦囊在使用进入判定区前接入统一无懈链。

## 9. 五谷丰登

Engine 暴露 `public_card_pool`。按存活角色数展示牌，使用 `selection_order/index/current selector` 逐人选择。玩家使用现有蓝框/黄框 Selection，AI 提交 SelectCardsAction。选择结束公共区清空，未被选择的牌进入弃牌堆。

## 10. 借刀杀人

结构保留 `jiedao_victim` 和合法第三目标列表。存在合法目标且持有杀时重新提交标准 UseCardAction；当前 1v1 没有第三目标时通过 TransferEquipmentAtom 将武器交给使用者，不直接修改装备/手牌状态。

## 11. 火攻

目标通过 SELECT_CARDS 展示一张手牌，Engine 记录花色；使用者选择同花色手牌并通过 MoveCardAtom 弃置，随后以 `DamageContext(nature="fire")` 进入 DamageFlow。玩家可手选，AI 使用同一请求与 Action 自动选择。

## 12. 铁索连环与重铸

Player 正式拥有 `chained: bool`。铁索可选择 1～2 个目标，经 SetChainedAtom 在横置/重置间切换并发出 `CHAIN_STATE_CHANGED`。玩家 UI 提供“连环/重铸”选择；重铸走同一 Effect，以处理区弃置并通过 DrawCardsAtom 摸一张。AI 在双方已经横置时选择重铸，否则选择目标。

## 13. ChainDamageFlow

DamageContext 新增 `chain_id/is_chain_damage/visited_players`。首次横置角色受到火或雷属性伤害并完成自身伤害后，ChainDamageFlow 按存活角色顺序传播同属性、同实际点数伤害。

每个传播目标仍创建 DamageFlow，因此藤甲、白银狮子、伤害事件、LoseHpAtom、DyingFlow/DeathFlow 均正常介入。共享 visited 和 is_chain_damage 防止 A→B→A 无限循环；每个目标传播前解除横置。子 DamageFlow 若进入濒死，ChainDamageFlow 可保持等待状态并在子流程完成后继续。

## 14. UI 必要改动

- 公共五谷牌显示并支持蓝框候选、黄框选择。
- 玩家和电脑区域显示“横置”。
- 判定区牌名显示在角色区域。
- 阶段显示增加准备、判定、摸牌、出牌、弃牌、结束。
- 火攻展示状态由 Engine 的 `revealed_card` 提供。
- 无懈继续复用响应 UI 和 Pending prompt。

Renderer 只读取 Engine 状态，没有加入规则判断。

## 15. AI

AI 继续只提交领域 Action。已支持无懈、火攻选择、五谷选择、延时锦囊、铁索选择/重铸，以及原有杀/闪/决斗/南蛮/万箭响应。AI 不直接判定、扣血、移动延时锦囊或修改横置状态。

## 16. 牌堆数据

新增 26 张实体：

- 五谷丰登：♥3、♥4（2）
- 无懈可击：♠J、♠K、♣Q、♣K、♦Q、♥A、♥K（7，延续项目已采用的标准/EX/军争兼容牌表）
- 借刀杀人：♣Q、♣K（2）
- 乐不思蜀：♠6、♥6、♣6（3）
- 闪电：♠A（1）
- 火攻：♥2、♥3、♦Q（3）
- 铁索连环：♠J、♠Q、♣10、♣J、♣Q、♣K（6）
- 兵粮寸断：♠10、♣4（2）

当前牌堆准确构成为：基本牌 54、装备牌 25、锦囊牌 49，总计 128。所有实体有唯一 id、name、category、suit、rank。数据核对参考标准版牌表、军争篇 52 张牌表与官方规则集；没有加入任何国战专属实体。

核对来源：[标准版牌表](https://zh.wikipedia.org/wiki/%E4%B8%89%E5%9C%8B%E6%AE%BA%E6%A8%99%E6%BA%96%E7%89%88)、[军争篇 52 张牌表](https://wiki.biligame.com/sgs/%E5%86%9B%E4%BA%89%E7%AF%87%E5%8D%A1%E7%89%8C)、[身份局官方规则集 3.0](https://gltjk.com/sanguosha/rules/info/role.html)。

## 17. 旧代码收敛

- turn.py 的摸牌/判定/阶段跳过职责已交给 TurnFlow，保留交互式出牌/弃牌入口。
- basic_cards.py 仅负责玩家点击 façade 和铁索用途选择。
- Legacy Response/Choice 只呈现 Engine Pending。
- combat.py 未新增锦囊、判定、无懈、连环或阶段规则。

## 18. 测试与烟雾

- compileall：通过。
- unittest：67/67 通过（原 51 + Phase 4 新增 16）。
- Pygame dummy：初始化并完成至少一个事件循环后正常退出。
- 场景 A：过河拆桥 → AI 无懈 → 玩家反无懈 → 过河生效，通过。
- 场景 B：玩家乐不思蜀判定失败，交互 TurnFlow 实际跳过出牌阶段，通过。
- 场景 C：闪电命中造成 3 雷伤并向另一横置角色传播，通过。
- 场景 D：铁索横置双方，火攻造成火伤并完整传播，通过。
- 场景 E：AI 使用可无懈锦囊，玩家出现正确无懈 Pending UI，通过。

所有烟雾结束均检查 Pending 未异常残留；需要玩家响应的场景 E 正确保留当前请求供 UI 操作。

## 19. 发现的问题

当前项目仍是 1v1，`living_players` 的座次容器只有两名角色；所有新 Flow 已使用 players/targets/order 列表，但真正扩展到多人时仍需要把 Game 的固定 player/enemy 容器升级为正式 player list。当前牌堆是逐阶段迁移形成的 128 张可玩牌堆，并非一次性重建完整 160 张标准军争实体表。

## 20. 下一阶段建议

下一阶段建议先做多人角色容器与座次系统，再进入武将/身份：统一 `players[]`、当前行动者、死亡后座次、距离环、选将与身份分配。不要在这一步之前把武将技能硬接到固定 player/enemy 字段。
