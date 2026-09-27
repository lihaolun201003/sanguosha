"""本机玩家的输入槽位：**现在谁在等我、我在等谁**（Phase 18.5）。

规则层表达"等某个人回答"只有一个对象：``PendingRequest``（``engine.pending``）。
但**本地交互槽位**不经过它——选目标是本地收集状态（点角色 → 确认 → 才提交），
选牌、技能输入、视为技来源、出牌方式、响应面板、二选一浮层同理。于是
"本机玩家现在有事可做吗"有两半：

    PendingRequest（引擎请求）        判定改判 / 无懈 / 求桃 / 选目标请求…
    本地交互槽位（game.pending_* 等） 出牌选目标 / 五谷选牌 / 技能输入 / 响应…

只查其中一半的判据都会在某条链路上出错，而且两种错法都真实发生过：

* 只看 ``pending_request``（``PresentationGate`` 原来就是这么写的）：出牌
  选目标期间引擎侧没有请求 → 判成"没人需要回答" → 让路闸门关着 → 玩家的
  每一次点击都被吞掉（Phase 18.5 批量试玩实测 741 次无效点击的成因之一）。
* 只看本地槽位（``renderer`` 的高亮查询一类）：改判窗口开着时判定面板还在
  演，谁都点不动。

因此这里给出**唯一**的判据，规则侧（``JudgeGate`` / ``PresentationGate``）、
表现侧（``storyboard`` / ``Effects``）、点击路由与诊断工具都读它。

本模块只读状态、不改状态，也不导入任何 UI 模块——联机客户端把自己的只读
视图传进来同样成立（视图提供的是同一组槽位字段）。
"""

#: 本地交互槽位：这些属性非空就表示"本机玩家正被要求做一件事"。
#: 顺序即优先级（诊断文案按这个顺序拼接）。
LOCAL_SLOT_ATTRS = (
    "pending_target_selection",   # 出牌选目标 / 技能选目标（本地收集）
    "pending_selection",          # 选牌（五谷 / 弃牌 / 火攻…）
    "pending_skill_input",        # 主动技的目标与费用
    "pending_view_as",            # 视为技的来源牌
    "pending_card_action",        # 多来源动作的方式 / 来源收集
    "pending_skill_picker",       # 一张牌有多个技能转化可选
)

#: 槽位属性名 → 诊断用的短名字。
SLOT_LABELS = {
    "pending_target_selection": "选目标",
    "pending_selection": "选牌",
    "pending_skill_input": "技能输入",
    "pending_view_as": "视为技来源",
    "pending_card_action": "出牌方式",
    "pending_skill_picker": "技能选择",
    "pending_request": "引擎请求",
    "response": "响应窗口",
    "choice": "二选一",
}


def request_targets_player(request, player):
    """这条引擎请求问的是不是**这名玩家**。

    单人请求看 ``target``；共享响应阶段（无懈）看成员状态——只有"还没回答"
    才算在等他，已经放弃 / 已出牌的人不再被问（否则会让路闸门永远放行）。
    """

    if request is None or player is None:
        return False
    status = getattr(request, "status", "")
    if status and status != "pending":
        return False
    is_group = getattr(request, "is_group", False)
    if not is_group:
        return getattr(request, "target", None) is player
    is_member = getattr(request, "is_member", None)
    member_status = getattr(request, "member_status", None)
    if not callable(is_member) or not callable(member_status):
        # 只读视图的请求替身没有成员查询：能出现在本机队列里就是"问我"。
        return True
    return bool(is_member(player) and member_status(player) == "pending")


def _slot_owner_is_local(system, player):
    """响应 / 二选一面板是不是属于本机玩家。

    ``responder`` 为 None 表示"没有归属声明"（旧调用点与联网客户端的替身），
    一律当作属于本机——面板本来就只为本机玩家弹。
    """

    current = getattr(system, "current", None)
    if current is None:
        return False
    responder = getattr(current, "responder", None)
    return responder is None or responder is player


def local_interaction_slots(game):
    """本机玩家此刻被要求做的所有事（稳定短名的元组，空元组 = 没人问他）。"""

    if game is None:
        return ()
    player = getattr(game, "player", None)
    names = []
    request = getattr(game, "pending_request", None)
    if request_targets_player(request, player):
        names.append("pending_request")
    for attr in LOCAL_SLOT_ATTRS:
        if getattr(game, attr, None):
            names.append(attr)
    for attr, label in (("response", "response"), ("choice", "choice")):
        system = getattr(game, attr, None)
        if getattr(system, "active", False) and _slot_owner_is_local(system, player):
            names.append(label)
    return tuple(names)


def local_awaiting_input(game):
    """本机玩家现在有没有必须做的决定。"""

    return bool(local_interaction_slots(game))


def local_response_live(game):
    """本机玩家是不是有一条**仍然有效**的响应请求要回答（响应窗口）。

    只有一个用途：让响应窗口里的提交在**动作队列还忙着播动画**时也能进去
    （``CombatMixin.respond_with_card`` / ``pass_response`` 的 busy 例外）。
    "队列忙"与"轮不到我回答"是两件事：上一张牌的余波、AI 那边的结算都会让
    队列一直忙，而响应窗口早就开着等本机玩家点了——旧代码在 busy 时直接
    ``return``，于是点【闪】、点「不出」全部被静默吞掉（Phase 18.5 实测）。

    它只回答"还轮得到我回答吗"：请求 id 对得上、回答者就是本机玩家、
    共享阶段里还没轮到我放弃。**牌合不合法不在这里判**——牌名、牌所在区域、
    重复提交都由引擎在 ``_respond_card`` 里复核，因此"只结算一次"不受影响
    （面板提交时先清空自己，引擎再按 request_id 取走请求）。
    """

    if game is None:
        return False
    panel = getattr(getattr(game, "response", None), "current", None)
    if panel is None:
        return False
    player = getattr(game, "player", None)
    responder = getattr(panel, "responder", None)
    if responder is not None and responder is not player:
        return False
    request = getattr(game, "pending_request", None)
    if request is None:
        # 旧版 1v1 濒死自救 / 出闪的响应链没有引擎请求：那套流程由动作队列
        # 驱动，busy 是它的必要约束，这里维持原样（不享受 busy 例外）。
        return False
    panel_id = getattr(panel, "request_id", None)
    if panel_id is not None and panel_id != getattr(request, "request_id", None):
        # 面板属于另一条请求（已经解决 / 被替换）：它不该再提交任何东西。
        return False
    return request_targets_player(request, player)


def describe_slots(names):
    """把槽位名整理成给人看的文案（日志 / 报告 / 让路说明）。"""

    return "、".join(SLOT_LABELS.get(str(name), str(name)) for name in names or ())
