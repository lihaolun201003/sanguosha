from src.actions import (
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.constants import (
    TABLE_CARD_RECT,
)

from src.card import Card
from .available_actions import ActionType, AvailableActions
from .engine import Event, EventType, UseCardAction
from .rules import TargetRule, living_players, target_candidates

from .equipment_skills.weapons import (
    has_fangtian_halberd,
    has_zhuque_fan,
)


class BasicCardMixin:

    # ==================================================
    # 酒是否仍要求先出杀
    #
    # 使用【酒】后下一张必须是【杀】；但如果手上没有【杀】，或者没有任何
    # 能攻击到的存活角色，酒的效果作废，不能把玩家永久锁死。
    # ==================================================

    def wine_blocks_other_cards(self):

        return self.wine_requires_sha(self.player)

    def wine_requires_sha(self, player):
        """喝了【酒】后是否必须使用【杀】；没有可用的杀时酒的效果作废。

        "有杀"同时算上技能转化出来的杀，避免把"闪 → 龙胆 → 杀"误判成无杀。
        """

        if not getattr(player, "wine_sha_required", False):
            return False

        reachable = self.has_sha_outlet(player) and any(
            target is not player and self.can_attack(player, target)
            for target in self.get_alive_players()
        )

        if reachable:
            return True

        player.wine_sha_required = False
        player.wine_buff = False
        return False

    def has_sha_outlet(self, player):
        """是否存在能当作【杀】使用的实体牌（正常或经由转化）。"""

        for card in player.hand:
            if card.name == "SHA":
                return True
        for item in self.conversions.sorted_items():
            if item.owner is not player or item.conversion.name != "SHA":
                continue
            if any(item.conversion.matches(card) for card in player.hand):
                return True
        return False

    # ==================================================
    # 玩家主动使用一张手牌
    # ==================================================

    def player_use_card(
        self,
        index,
        source_rect
    ):

        if self.busy:
            return

        if self.choice.active:
            return

        if self.response.active:
            return

        # 还有别人在回答 / 引擎在等答案（共享无懈阶段一类）：锦囊尚未结算，
        # 这时候点手牌不能出牌。
        if self.engine.pending.active:
            return

        # 真人输入锁定：只有轮到自己、或自己正是当前 Pending 的 owner 时
        # 才能出牌，AI 回合与 AI 的五谷选择期间都不能点击手牌。
        if self.current_turn_player is not self.player:
            return

        if self.phase != "play":
            return

        if self.game_over:
            return

        if not (
            0 <= index
            < len(self.player.hand)
        ):

            return

        card = self.player.hand[index]

        # 使用 / 重铸 二选一：**由共同查询决定**这张牌现在有哪几种用法，不按
        # 牌名写死（铁索连环只是当前唯一同时具备两者的牌）。只有"能正常使用"
        # 与"能重铸"同时成立时才需要问玩家，否则直接走各自那一条。
        actions = [item for item in AvailableActions(self).card_actions(
            self.player, card) if item.enabled]
        recast = next(
            (item for item in actions if item.kind == ActionType.RECAST), None)
        usable = [item for item in actions if item.kind != ActionType.RECAST]
        if recast is not None and usable:
            label = str(card.display_name)
            self.choice.request(
                title=label,
                prompt="选择使用【" + label + "】或重铸摸一张牌",
                yes_label="使用",
                no_label="重铸",
                on_yes=lambda c=card, r=source_rect: self.begin_card_action(c, r),
                on_no=lambda c=card, r=source_rect: self._submit_tiesuo(c, r, True),
            )
            return
        if recast is not None:
            # 只剩重铸可用（用不了）：直接重铸，不必再问一次。
            self._submit_tiesuo(card, source_rect, True)
            return

        # 其余全部交给统一的 Card Action Discovery：正常使用与技能转化
        # （武圣 / 龙胆 / 未来的丈八类）走同一条判定与提交链路。
        self.begin_card_action(card, source_rect)

    def _submit_tiesuo(self, card, source_rect, recast):
        """铁索连环的"连环"与"重铸"两条路（重铸复用统一的 metadata 约定）。

        "连环"能打几个目标由**动态规则**决定（不再硬编码 2 个）：技能改写目标
        数量时，这里必须跟着变。
        """

        from src.game.available_actions import RECAST_METADATA

        if recast:
            targets = []
            metadata = dict(RECAST_METADATA)
        else:
            profile = AvailableActions(self).target_profile(self.player, card=card)
            targets = list(profile.players)
            metadata = {}
        self.submit_action(
            UseCardAction(
                self.player, card, targets, source_rect=source_rect,
                metadata=metadata,
            )
        )

    def _begin_v2_card_targeting(self, card, source_rect, metadata=None, ignore_usage_limit=False):
        effect = self.engine.card_effects.require(card)
        # 目标规则与数量走规则层的**动态查询**：技能可以改写它们
        # （【天义】/【神戟】让一张【杀】多指定目标），界面必须问同一处，
        # 否则玩家根本选不到第二个目标。
        rule = effect.target_rule_for(self, self.player, card)
        minimum, maximum = effect.target_bounds_for(self, self.player, card)
        ordered = self.seats.alive_players_in_order(start_after=self.player, include_start=True)
        if rule is TargetRule.SELF:
            return self._submit_selected_card(
                card, source_rect,
                [self.player] if maximum >= 1 else [],
                metadata=metadata, ignore_usage_limit=ignore_usage_limit,
            )
        if rule is TargetRule.ALL_PLAYERS:
            return self._submit_selected_card(card, source_rect, ordered, metadata=metadata, ignore_usage_limit=ignore_usage_limit)
        if rule is TargetRule.ALL_OTHERS:
            return self._submit_selected_card(card, source_rect, [p for p in ordered if p is not self.player], metadata=metadata, ignore_usage_limit=ignore_usage_limit)
        if rule is TargetRule.NO_TARGET:
            return self._submit_selected_card(card, source_rect, [], metadata=metadata, ignore_usage_limit=ignore_usage_limit)

        candidates = []
        for target in target_candidates(self, self.player, rule, card=card):
            probe = UseCardAction(self.player, card, [target], source_rect=source_rect, ignore_usage_limit=ignore_usage_limit)
            if rule is TargetRule.MULTIPLE or effect.can_use(self, probe)[0]:
                candidates.append(target)
        if not candidates:
            self.message = "没有合法目标。"
            return
        if maximum == 1 and len(candidates) == 1:
            # 1v1 与“只剩一个合法目标”的情况保持一步出牌，不额外要求确认。
            return self._submit_selected_card(
                card, source_rect, candidates,
                metadata=metadata, ignore_usage_limit=ignore_usage_limit,
            )
        self.pending_target_selection = {
            "card": card,
            "source_rect": source_rect,
            "candidates": candidates,
            "selected": [],
            "minimum": minimum,
            "maximum": maximum,
            "metadata": dict(metadata or {}),
            "ignore_usage_limit": ignore_usage_limit,
        }
        self.message = self._target_prompt(self.pending_target_selection)

    def start_target_selection(self, candidates, minimum, maximum, prompt, on_complete,
                               on_cancel=None, request_id=None):
        """通用目标选择入口：技能与卡牌共用同一套选目标 UI。

        与出牌路径的区别只在收尾：带 ``on_complete`` 时由回调接管，
        不再组装 UseCardAction。这样任何"选择 N 个角色"的技能都能复用
        点击角色、确认、取消的完整交互。
        """

        candidates = [target for target in candidates]
        if not candidates:
            self.message = "没有合法目标。"
            return False
        self.pending_target_selection = {
            "card": None,
            "source_rect": None,
            "candidates": candidates,
            "selected": [],
            "minimum": int(minimum),
            "maximum": int(maximum),
            "metadata": {},
            "ignore_usage_limit": False,
            "prompt": prompt,
            "on_complete": on_complete,
            "on_cancel": on_cancel,
            "request_id": request_id,
        }
        self.message = self._target_prompt(self.pending_target_selection)
        return True

    def cancel_target_selection(self):
        """放弃当前的目标选择（只影响 UI 选择状态，不改变任何规则状态）。"""

        selection = self.pending_target_selection
        if selection is None:
            return False
        on_cancel = selection.get("on_cancel")
        self.pending_target_selection = None
        if on_cancel is not None:
            on_cancel()
        else:
            self.message = "已取消目标选择。"
        return True

    def _target_prompt(self, selection):
        selected = len(selection["selected"])
        minimum = selection["minimum"]
        maximum = selection["maximum"]
        custom = selection.get("prompt")
        if custom:
            return (custom + "（已选择 " + str(selected) + " / " + str(maximum)
                    + "，点击「确认目标」结算）")
        if maximum == 1:
            return "请选择目标：已选择 " + str(selected) + " / 1，点击角色后自动确认。"
        return ("请选择 " + str(minimum) + "～" + str(maximum) + " 个目标：已选择 "
                + str(selected) + " / " + str(maximum) + "，点击「确认目标」结算。")

    def toggle_target_selection(self, target):
        selection = self.pending_target_selection
        if selection is None or target not in selection["candidates"]:
            return False
        selected = selection["selected"]
        if target in selected:
            selected.remove(target)
        elif len(selected) < selection["maximum"]:
            selected.append(target)
        if selection["maximum"] == 1 and selected:
            self.confirm_target_selection()
        else:
            self.message = self._target_prompt(selection)
        return True

    def confirm_target_selection(self):
        selection = self.pending_target_selection
        if selection is None:
            return False
        if len(selection["selected"]) < selection["minimum"]:
            self.message = "选择的目标数量不足。"
            return False
        on_complete = selection.get("on_complete")
        if on_complete is not None:
            targets = list(selection["selected"])
            self.pending_target_selection = None
            on_complete(targets)
            return True
        if selection["card"].name == "JIEDAO" and selection.get("stage") != "victim":
            wielder = selection["selected"][0]
            victims = [target for target in self.get_alive_players()
                       if target is not self.player and target is not wielder
                       and self.can_attack(wielder, target)]
            if victims:
                selection.update({"stage": "victim", "wielder": wielder,
                                  "candidates": victims, "selected": [],
                                  "minimum": 1, "maximum": 1})
                self.message = "请选择被迫成为【杀】目标的角色（1 / 1）"
                return True
        self.pending_target_selection = None
        metadata = dict(selection.get("metadata", {}))
        targets = selection["selected"]
        if selection.get("stage") == "victim":
            metadata["jiedao_victim"] = targets[0]
            targets = [selection["wielder"]]
        self._submit_selected_card(
            selection["card"], selection["source_rect"], targets,
            metadata=metadata,
            ignore_usage_limit=selection.get("ignore_usage_limit", False),
        )
        return True

    def _submit_selected_card(self, card, source_rect, targets, metadata=None, ignore_usage_limit=False):
        self.pending_target_selection = None
        metadata = dict(metadata or {})
        # 战报上的名字取**出牌的人**，而不是"本机鼠标现在指向谁"：1v1 测试的
        # 双边手动模式下，视角会在这次结算途中切到另一方，用 self.player 记出来
        # 的战报会写成别人的名字（"对手 使用【杀】"其实是"我方"用的）。
        actor = self.player
        result = self.submit_action(UseCardAction(
            actor, card, list(targets),
            source_rect=source_rect,
            ignore_usage_limit=ignore_usage_limit,
            metadata=metadata,
        ))
        # 被规则拒绝的尝试不会真正使用这张牌，日志由 UseCardFlow 在成功时记录。
        if getattr(result.status, "value", None) != "cancelled":
            action = metadata.get("card_action")
            if action is not None and getattr(action, "is_conversion", False):
                # 转换必须写清楚"谁用什么技能把哪张实体牌当成了什么"。
                if action.log_text:
                    self.add_log(action.log_text)
                self.context.emit(Event(
                    EventType.SKILL_TRIGGERED,
                    source=actor,
                    payload={
                        "skill_id": action.skill_id,
                        "skill_name": action.skill_name,
                        "card_action": action,
                    },
                ))
            else:
                self.add_log(actor.name + " 使用【" + card.display_name + "】" +
                             (("，目标：" + "、".join(target.name for target in targets)) if targets else ""))
        return result


    # ==================================================
    # 根据对象身份找到手牌下标
    # ==================================================

    def find_hand_card_index(
        self,
        card
    ):

        for i, item in enumerate(
            self.player.hand
        ):

            if item is card:

                return i

        return None


    # ==================================================
    # Legacy 1v1 出杀入口
    #
    # 真人出杀现在统一走 _begin_v2_card_targeting；这个方法保留给
    # Legacy 回归测试与旧回调链使用，不再承担多人目标选择。
    # ==================================================

    def try_player_sha(
        self,
        card,
        source_rect
    ):

        # ==================================================
        # 每回合杀次数限制
        # ==================================================

        if (
            self.sha_used
            and not self.can_use_unlimited_sha(
                self.player
            )
        ):

            self.message = (
                "本回合已经使用过【杀】。"
            )

            return

        # ==================================================
        # 距离
        # ==================================================

        distance = self.get_distance(
            self.player,
            self.enemy
        )

        if not self.can_attack(
            self.player,
            self.enemy
        ):

            self.message = (
                "攻击距离不足。"
                "当前距离："
                + str(distance)
                + "，攻击范围："
                + str(
                    self.player.attack_range
                )
                + "。"
            )

            return

        # ==================================================
        # 朱雀羽扇
        #
        # 只有普通杀才询问是否转为火杀。
        #
        # 原本就是火杀 / 雷杀时不询问。
        # ==================================================

        if (
            getattr(
                card,
                "nature",
                "normal"
            )
            == "normal"
            and has_zhuque_fan(
                self.player
            )
        ):

            self.message = (
                "是否发动【朱雀羽扇】？"
            )

            self.choice.request(
                title="朱雀羽扇",

                prompt=(
                    "是否将此普通【杀】"
                    "当【火杀】使用？"
                ),

                yes_label="发动",

                no_label="不发动",

                on_yes=(

                    lambda c=card, r=source_rect:
                        self.commit_player_sha(
                            c,
                            r,
                            use_zhuque=True
                        )
                ),

                on_no=(

                    lambda c=card, r=source_rect:
                        self.commit_player_sha(
                            c,
                            r,
                            use_zhuque=False
                        )
                )
            )

            return

        # ==================================================
        # 不需要询问，直接使用
        # ==================================================

        self.commit_player_sha(
            card,
            source_rect,
            use_zhuque=False
        )


    # ==================================================
    # 正式使用玩家的杀
    # ==================================================

    def commit_player_sha(
        self,
        card,
        source_rect,
        use_zhuque=False
    ):

        if self.game_over:
            return

        if self.phase != "play":
            return

        # ==================================================
        # 找到这张牌当前仍然所在的位置
        # ==================================================

        index = self.find_hand_card_index(
            card
        )

        if index is None:
            return

        # ==================================================
        # 再检查一次攻击范围
        # ==================================================

        if not self.can_attack(
            self.player,
            self.enemy
        ):

            self.message = (
                "攻击距离不足。"
            )

            return

        # Engine V2 owns the complete use/response/damage/dying chain when no
        # complex Legacy equipment interaction requires the old callback path.
        if self.engine.compatibility.supports_v2_sha(
            self.player,
            self.enemy,
            card,
        ):

            self.submit_action(
                UseCardAction(
                    actor=self.player,
                    card=card,
                    targets=[self.enemy],
                    source_rect=source_rect,
                    metadata={"zhuque_fire": bool(use_zhuque)},
                )
            )
            return

        was_last_hand_card = (
            len(self.player.hand) == 1
        )

        # ==================================================
        # 从手牌移除
        # ==================================================

        card = self.player.remove_card(
            index
        )

        if card is None:
            return

        # ==================================================
        # 朱雀羽扇
        #
        # 临时把普通杀的 nature 改为 fire。
        #
        # core.py 会在这张牌进入弃牌堆时，
        # 自动恢复原来的 nature。
        # ==================================================

        if use_zhuque:

            card._original_nature = (
                card.nature
            )

            card.nature = "fire"

        # ==================================================
        # 本回合已使用杀
        # ==================================================

        self.sha_used = True

        # ==================================================
        # 基础伤害
        # ==================================================

        damage = 1

        # ==================================================
        # 酒杀
        # ==================================================

        if self.player_wine_buff:

            damage += 1

            self.player_wine_buff = False

            self.wine_sha_required = False

        # ==================================================
        # 提示文字
        # ==================================================

        if use_zhuque:

            self.message = (
                "你发动【朱雀羽扇】，"
                "将此【杀】当【火杀】使用。"
            )

        else:

            self.message = (
                "你使用了【"
                + card.display_name
                + "】。"
            )

        if (
            has_fangtian_halberd(self.player)
            and was_last_hand_card
        ):

            self.message += (
                "【方天画戟】已触发，"
                "当前1v1没有额外目标。"
            )

        # ==================================================
        # 杀飞到桌面
        # ==================================================

        self.actions.add(

            MoveCardAction(
                card,
                source_rect,
                TABLE_CARD_RECT,
                duration=0.30,

                on_finish=(

                    lambda c=card:
                        self.add_table_card(
                            c,
                            TABLE_CARD_RECT
                        )
                )
            )
        )

        self.actions.add(
            WaitAction(
                0.55
            )
        )

        # ==================================================
        # 结算杀
        # ==================================================

        self.actions.add(

            CallbackAction(

                lambda c=card, d=damage:
                    self.resolve_player_sha(
                        c,
                        d
                    )
            )
        )


    # ==================================================
    # 桃的结算
    # ==================================================

    def resolve_player_tao(
        self,
        tao
    ):

        self.player.hp += 1

        self.message = (
            "你回复了 1 点体力。"
        )

        self.actions.add(
            WaitAction(
                0.40
            )
        )

        self.queue_to_discard(
            tao,
            TABLE_CARD_RECT
        )
