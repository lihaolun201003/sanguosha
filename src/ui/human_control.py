"""HumanController：UI 动作的唯一出口（Phase 11.4.2）。

原来的结构是"一套牌桌、两条鼠标路径"：

* 单机：``ui.interaction.handle_game_click`` 直接调 ``Game`` 的方法；
* 联机：``ui.remote_table.RemoteTableScene`` 另写了一套"点击 → DecisionResult"。

于是"复用单机 UI"只做到了 draw/render 那一半——画是同一份，**操作不是**。
本模块把"操作"也收敛成一份：

    ui.interaction.handle_game_click      ← 唯一的鼠标命中 / 语义分支
                    │
            HumanController                  ← 唯一的末端动作接口
              /            \\
    LocalHumanController   RemoteHumanController
            │                     │
          Game                DecisionResponse → 房主

``LocalHumanController`` 只是把动作转发给本机权威 ``Game`` 的公开入口（原来
写在 interaction.py 里的那些 ``game.xxx()`` 调用原样搬过来，没有重写规则）。
``RemoteHumanController`` 把**同一批动作**翻译成 ``DecisionResult`` 发给房主，
由房主用自己的规则校验后提交。

因此下面这些东西在 Local 与 Remote 之间是同一份实现，不再各写一遍：
绘制、布局、鼠标命中、选择与高亮、技能按钮、确认 / 取消。差异只剩
Controller 边界与网络传输。
"""

from src.game.equipment_skills.granted import granted_skill_id


class HumanController:
    """一名真人玩家能做出来的全部 UI 动作。

    方法名对应"玩家点了什么"，不对应游戏规则。默认实现全部是空操作，
    这样只读视图（观看）也能挂在同一条点击路径上而不报错。
    """

    #: 是否持有本机权威状态（Local 为 True；联网客户端为 False）。
    local_interaction = True

    # ---- 固定按钮 / 面板动作 ----

    def run_action(self, action, renderer):
        """固定按钮与选择面板的动作（``Renderer.hit_action`` 的返回值）。"""

        return None

    # ---- 点牌 ----

    def use_card(self, card, index, rect):
        """出牌阶段：打出这张手牌。"""

        return None

    def discard_card(self, card, index, rect):
        """弃牌阶段：弃掉这张手牌。"""

        return None

    def respond_with_card(self, card, index, rect):
        """响应窗口：用这张牌响应（【闪】/【桃】/【无懈可击】）。"""

        return None

    def select_card(self, card, rect, key=None, zone=""):
        """选牌界面：选中 / 取消这张候选牌（手牌 / 装备 / 公共区通用）。"""

        return None

    def toggle_card_source(self, card, rect):
        """多来源动作（转化 / 视为技）的来源牌增减。"""

        return None

    def select_skill_cost_card(self, card, rect):
        """主动技发动中：把这牌选作费用牌。"""

        return None

    def begin_card_action(self, card, rect):
        """开始一次"用这张牌"的动作（选出它作为来源，进入方式 / 目标选择）。"""

        return None

    def try_zhangba(self):
        """点击已装备的丈八蛇矛：进入"两张手牌当【杀】"的选牌流程。"""

        return None

    # ---- 点人 ----

    def toggle_target(self, player):
        """选目标：把这个人加入 / 移出目标集合（出牌目标与技能目标共用）。"""

        return None

    # ---- 点技能 ----

    def activate_skill(self, skill_id):
        """技能栏上按下一个技能。"""

        return None

    # ---- 收尾 ----

    def cleanup(self):
        """离开这一局时的清理（清空本地选择状态）。"""

        return None


class LocalHumanController(HumanController):
    """本机就是权威：所有动作直接交给 ``Game`` 的公开入口。

    这些转发就是``ui.interaction`` 里原本写在分支里的那几行——只是从"散在
    点击路由里"变成"集中在一个类上"，规则一行都没有改。
    """

    local_interaction = True

    def __init__(self, game):
        self.game = game

    # ---- 固定按钮 / 面板动作 ----

    def run_action(self, action, renderer):
        game = self.game
        if action == "end_turn":
            game.end_player_turn()
        elif action == "surrender":
            # 二次确认由按钮自己管（见 Renderer.request_surrender）。
            if renderer.request_surrender():
                game.return_to_menu()
        elif action == "confirm_target":
            game.confirm_target_selection()
        elif action == "cancel_target":
            game.cancel_target_selection()
        elif action == "pass_response":
            game.pass_response()
        elif action == "pass_selection":
            game.cancel_pending_selection()
        elif action == "choice_no":
            game.choice.choose_no()
        elif action == "open_skills":
            game.begin_skill_activation()
        elif action == "cancel":
            game.cancel_skill_input()
        elif action == "view_as_cancel":
            game.cancel_view_as()
        elif action == "action_cancel":
            game.cancel_card_action()
        elif action == "confirm_card_action":
            game.confirm_card_action()
        elif isinstance(action, tuple) and action and action[0] == "action":
            game.choose_card_action(action[1])
        elif action == "confirm_skill":
            game.confirm_skill_input()
        elif action == "cancel_skill":
            game.cancel_skill_input()
        elif action == "skill_info":
            pass                      # 只展开说明，不改任何规则状态
        elif isinstance(action, tuple) and action and action[0] == "view_as":
            game.begin_view_as(action[1])
        elif isinstance(action, tuple) and action and action[0] in ("skill", "activate_skill"):
            game.start_skill_activation(action[1])
        elif action == "slower":
            game.slower()
        elif action == "faster":
            game.faster()
        elif action == "restart":
            renderer.reset_effects()
            # 重新开始 = 回到开局流程（模式 / 人数 → 身份 → 选将）。
            # 模式可以先自己接住它：1v1 测试是"原配置重开"，不离开对局。
            game.restart_battle()
        elif action == "menu":
            renderer.reset_effects()
            game.return_to_menu()
        return action

    # ---- 点牌 ----

    def use_card(self, card, index, rect):
        self.game.player_use_card(index, rect)

    def discard_card(self, card, index, rect):
        self.game.player_discard(index, rect)

    def respond_with_card(self, card, index, rect):
        self.game.respond_with_card(index, rect)

    def select_card(self, card, rect, key=None, zone=""):
        self.game.select_pending_card(card, rect, key=key)

    def toggle_card_source(self, card, rect):
        game = self.game
        if game.pending_view_as is not None:
            game.toggle_view_as_source(card, rect)
        else:
            game.toggle_card_action_source(card, rect)

    def select_skill_cost_card(self, card, rect):
        self.game.select_skill_cost_card(card)

    def begin_card_action(self, card, rect):
        self.game.begin_card_action(card, rect)

    def try_zhangba(self):
        """点击已装备的牌：进入它赋予的视为技（丈八蛇矛：两张手牌当【杀】）。

        "哪件装备赋予哪个视为技"由绑定表（``granted_skill_id``）说了算，这里
        不写死武器槽或具体牌名——装备区里任何赋予视为技的牌走同一条入口。
        再点一次（已经在选牌中）等同取消发动，与"点击取消"的旧手感一致。
        """

        game = self.game
        skill_id = None
        for slot in ("weapon", "armor", "defensive_horse", "offensive_horse"):
            card = game.player.get_equipment(slot)
            if card is None:
                continue
            skill_id = granted_skill_id(getattr(card, "name", None))
            if skill_id is not None:
                break
        if skill_id is None:
            return
        if game.pending_view_as is not None:
            game.cancel_view_as()
            return
        game.begin_view_as(skill_id)

    # ---- 点人 ----

    def toggle_target(self, player):
        game = self.game
        if game.pending_skill_input is not None:
            game.toggle_skill_target(player)
        else:
            game.toggle_target_selection(player)

    # ---- 点技能 ----

    def activate_skill(self, skill_id):
        self.game.start_skill_activation(skill_id)
