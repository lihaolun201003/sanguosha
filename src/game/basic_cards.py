from src.actions import (
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.constants import (
    PLAYER_HAND_SOURCE_RECT,
    TABLE_CARD_RECT,
)

from src.card import Card
from .engine import UseCardAction
from .rules import TargetRule, living_players, target_candidates

from .equipment_skills.weapons import (
    has_fangtian_halberd,
    has_zhangba_spear,
    has_zhuque_fan,
)


class BasicCardMixin:

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

        # ==================================================
        # 喝酒以后下一张必须是杀
        # ==================================================

        if (
            self.wine_sha_required
            and card.name != "SHA"
        ):

            self.message = (
                "你已经使用【酒】，"
                "现在必须使用一张【杀】。"
            )

            return

        # ==================================================
        # 装备牌
        # ==================================================

        if card.category == "equipment":

            self.player_equip(
                index,
                source_rect
            )

            return

        if card.category == "trick":
            if card.name == "TIESUO":
                self.choice.request(
                    title="铁索连环",
                    prompt="选择使用【铁索连环】或重铸摸一张牌",
                    yes_label="连环",
                    no_label="重铸",
                    on_yes=lambda c=card, r=source_rect: self._begin_v2_card_targeting(c, r),
                    on_no=lambda c=card, r=source_rect: self._submit_tiesuo(c, r, True),
                )
                return
            if len(self.players) == 2:
                if card.name in ("WUZHONG", "SHANDIAN"):
                    targets = [self.player]
                elif card.name in ("TAOYUAN", "WUGU"):
                    targets = living_players(self)
                elif card.name in ("NANMAN", "WANJIAN"):
                    targets = [target for target in living_players(self) if target is not self.player]
                else:
                    targets = [self.enemy]
                self.submit_action(UseCardAction(self.player, card, targets, source_rect=source_rect))
                return
            self._begin_v2_card_targeting(card, source_rect)
            return

        # ==================================================
        # 杀
        # ==================================================

        if card.name == "SHA":
            if len(self.players) == 2:
                self.try_player_sha(card, source_rect)
            else:
                self._begin_v2_card_targeting(card, source_rect)
            return

        # ==================================================
        # 桃
        # ==================================================

        if card.name == "TAO":

            if (
                self.player.hp
                >= self.player.max_hp
            ):

                self.message = (
                    "你的体力已经是满的。"
                )

                return

            card = self.player.remove_card(
                index
            )

            self.message = (
                "你使用了【桃】。"
            )

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
                    0.40
                )
            )

            self.actions.add(

                CallbackAction(

                    lambda c=card:
                        self.resolve_player_tao(
                            c
                        )
                )
            )

            return

        # ==================================================
        # 酒
        # ==================================================

        if card.name == "JIU":

            if self.jiu_used:

                self.message = (
                    "本回合已经使用过【酒】。"
                )

                return

            # ==================================================
            # 没有诸葛连弩时，
            # 如果已经出过杀，
            # 再喝酒会造成死锁。
            # ==================================================

            if (
                self.sha_used
                and not self.can_use_unlimited_sha(
                    self.player
                )
            ):

                self.message = (
                    "本回合已经使用过【杀】，"
                    "当前不能再使用【酒】强化杀。"
                )

                return

            # ==================================================
            # 必须持有杀
            # ==================================================

            if not self.player.has_card(
                "SHA"
            ):

                self.message = (
                    "你没有【杀】，"
                    "现在不能使用【酒】。"
                )

                return

            # ==================================================
            # 必须能够攻击目标
            # ==================================================

            if not self.can_attack(
                self.player,
                next((target for target in self.get_alive_players()
                      if target is not self.player and self.can_attack(self.player, target)), self.enemy)
            ):

                self.message = (
                    "当前没有能够使用【杀】"
                    "攻击到的目标，"
                    "不能使用【酒】。"
                )

                return

            card = self.player.remove_card(
                index
            )

            self.jiu_used = True

            self.player_wine_buff = True

            self.wine_sha_required = True

            self.message = (
                "你使用了【酒】，"
                "现在必须使用【杀】。"
            )

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
                    0.45
                )
            )

            self.queue_to_discard(
                card,
                TABLE_CARD_RECT
            )

            return

        # ==================================================
        # 闪
        # ==================================================

        if card.name == "SHAN":

            self.message = (
                "【闪】不能主动使用，"
                "需要在响应【杀】时使用。"
            )

    def _submit_tiesuo(self, card, source_rect, recast):
        targets = [] if recast else living_players(self)[:2]
        self.submit_action(
            UseCardAction(
                self.player, card, targets, source_rect=source_rect,
                metadata={"recast": recast, "skip_wuxie": recast},
            )
        )

    def _begin_v2_card_targeting(self, card, source_rect):
        effect = self.engine.card_effects.require(card)
        rule = effect.target_rule
        ordered = self.seats.alive_players_in_order(start_after=self.player, include_start=True)
        if rule is TargetRule.SELF:
            return self._submit_selected_card(card, source_rect, [self.player])
        if rule is TargetRule.ALL_PLAYERS:
            return self._submit_selected_card(card, source_rect, ordered)
        if rule is TargetRule.ALL_OTHERS:
            return self._submit_selected_card(card, source_rect, [p for p in ordered if p is not self.player])
        if rule is TargetRule.NO_TARGET:
            return self._submit_selected_card(card, source_rect, [])

        candidates = []
        for target in target_candidates(self, self.player, rule):
            probe = UseCardAction(self.player, card, [target], source_rect=source_rect)
            if rule is TargetRule.MULTIPLE or effect.can_use(self, probe)[0]:
                candidates.append(target)
        if not candidates:
            self.message = "没有合法目标。"
            return
        self.pending_target_selection = {
            "card": card,
            "source_rect": source_rect,
            "candidates": candidates,
            "selected": [],
            "minimum": effect.min_targets,
            "maximum": effect.max_targets,
        }
        self.message = ("请选择目标（1 / 1）" if effect.max_targets == 1 else
                        "请选择 1～" + str(effect.max_targets) + " 个目标，已选择 0 / " + str(effect.max_targets))

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
            self.message = "请选择 1～" + str(selection["maximum"]) + " 个目标，已选择 " + str(len(selected)) + " / " + str(selection["maximum"])
        return True

    def confirm_target_selection(self):
        selection = self.pending_target_selection
        if selection is None:
            return False
        if len(selection["selected"]) < selection["minimum"]:
            self.message = "选择的目标数量不足。"
            return False
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
        metadata = {}
        targets = selection["selected"]
        if selection.get("stage") == "victim":
            metadata["jiedao_victim"] = targets[0]
            targets = [selection["wielder"]]
        self._submit_selected_card(selection["card"], selection["source_rect"], targets, metadata=metadata)
        return True

    def _submit_selected_card(self, card, source_rect, targets, metadata=None):
        self.pending_target_selection = None
        result = self.submit_action(UseCardAction(self.player, card, list(targets), source_rect=source_rect, metadata=dict(metadata or {})))
        self.add_log(self.player.name + " 使用【" + card.display_name + "】" +
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
    # 玩家准备使用杀
    # ==================================================

    def try_player_zhangba(self):

        if (
            self.busy
            or self.choice.active
            or self.response.active
            or self.phase != "play"
            or self.game_over
        ):
            return

        if not has_zhangba_spear(self.player):
            return

        if len(self.player.hand) < 2:

            self.message = (
                "发动【丈八蛇矛】至少需要两张手牌。"
            )

            return

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

        if not self.can_attack(
            self.player,
            self.enemy
        ):

            self.message = "攻击距离不足。"
            return

        self.zhangba_selecting = True
        self.zhangba_selected = []

        self.message = (
            "丈八蛇矛：请依次点击两张手牌；"
            "再次点击武器可取消。"
        )


    def toggle_player_zhangba_card(
        self,
        index,
        source_rect
    ):

        if not self.zhangba_selecting:
            return

        if not (
            0 <= index < len(self.player.hand)
        ):
            return

        card = self.player.hand[index]

        for selected in self.zhangba_selected:

            if selected[0] is card:

                self.zhangba_selected.remove(selected)
                self.message = (
                    "丈八蛇矛：已取消这张牌，"
                    "还需选择 "
                    + str(2 - len(self.zhangba_selected))
                    + " 张。"
                )
                return

        self.zhangba_selected.append(
            (
                card,
                tuple(source_rect),
            )
        )

        if len(self.zhangba_selected) < 2:

            self.message = (
                "丈八蛇矛：已选择一张，"
                "请再选择一张。"
            )
            return

        self.commit_player_zhangba()


    def cancel_player_zhangba(self):

        if not self.zhangba_selecting:
            return

        self.zhangba_selecting = False
        self.zhangba_selected = []
        self.message = "已取消发动【丈八蛇矛】。"


    def commit_player_zhangba(self):

        if (
            not self.zhangba_selecting
            or len(self.zhangba_selected) != 2
        ):
            return

        selected = list(self.zhangba_selected)

        self.zhangba_selecting = False
        self.zhangba_selected = []

        material_cards = [card for card, _rect in selected]
        if len(material_cards) == 2 and all(
            any(hand_card is card for hand_card in self.player.hand)
            for card in material_cards
        ):
            virtual_sha = Card(
                name="SHA",
                category="basic",
                color=(245, 205, 195),
            )
            virtual_sha._virtual = True
            base_damage = 2 if self.player_wine_buff else 1
            self.player_wine_buff = False
            self.wine_sha_required = False
            self.submit_action(
                UseCardAction(
                    actor=self.player,
                    card=virtual_sha,
                    targets=[self.enemy],
                    source_rect=PLAYER_HAND_SOURCE_RECT,
                    ignore_usage_limit=True,
                    metadata={
                        "skill": "ZHANGBA",
                        "materials": material_cards,
                        "base_damage": base_damage,
                    },
                )
            )
            return

        materials = []

        for card, source_rect in selected:

            index = self.find_hand_card_index(card)

            if index is None:
                continue

            materials.append(
                (
                    self.player.remove_card(index),
                    source_rect,
                )
            )

        if len(materials) != 2:

            self.message = (
                "选中的手牌已经发生变化，"
                "请重新发动【丈八蛇矛】。"
            )
            return

        virtual_sha = Card(
            name="SHA",
            category="basic",
            color=(245, 205, 195),
        )
        virtual_sha._virtual = True

        damage = 1

        if self.player_wine_buff:

            damage += 1
            self.player_wine_buff = False
            self.wine_sha_required = False

        self.sha_used = True

        self.message = (
            "你发动【丈八蛇矛】，"
            "将两张手牌当【杀】使用。"
        )

        self.queue_weapon_discards(
            materials,
            after=(
                lambda c=virtual_sha, d=damage:
                    self.play_virtual_player_sha(
                        c,
                        d
                    )
            )
        )


    def play_virtual_player_sha(
        self,
        sha,
        damage
    ):

        self.submit_action(
            UseCardAction(
                actor=self.player,
                card=sha,
                targets=[self.enemy],
                source_rect=PLAYER_HAND_SOURCE_RECT,
                ignore_usage_limit=True,
                metadata={"base_damage": damage, "skill": "ZHANGBA"},
            )
        )
        return

        self.actions.add(
            MoveCardAction(
                sha,
                PLAYER_HAND_SOURCE_RECT,
                TABLE_CARD_RECT,
                duration=0.30,
                on_finish=(
                    lambda c=sha:
                        self.add_table_card(
                            c,
                            TABLE_CARD_RECT
                        )
                )
            )
        )

        self.actions.add(
            WaitAction(0.55)
        )

        self.actions.add(
            CallbackAction(
                lambda c=sha, d=damage:
                    self.resolve_player_sha(
                        c,
                        d
                    )
            )
        )

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
