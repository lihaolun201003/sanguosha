"""Legacy 1v1 combat callbacks and animations.

The live path for Sha, damage and equipment skills is Engine V2
(``flows/`` + ``card_effects/``); this mixin keeps the Legacy regression tests
and the remaining 1v1 callback helpers working.
"""

from src.actions import (
    CallbackAction,
    MoveCardAction,
    WaitAction,
)

from src.constants import (
    ENEMY_HAND_RECT,
    ENEMY_EQUIPMENT_RECTS,
    PLAYER_EQUIPMENT_RECTS,
    PLAYER_HAND_SOURCE_RECT,
    RESPONSE_CARD_RECT,
    TABLE_CARD_RECT,
)

from .equipment_skills.weapons import (
    has_gender_swords,
    has_ice_sword,
    has_qilin_bow,
    has_qinglong_blade,
    has_stone_axe,
)


class CombatMixin:

    # ==================================================
    # 武器效果共用工具
    # ==================================================

    def have_different_genders(
        self,
        attacker,
        defender
    ):

        return (
            attacker.gender is not None
            and defender.gender is not None
            and attacker.gender != defender.gender
        )


    def player_has_mount(
        self,
        player
    ):

        return (
            player.get_equipment(
                "defensive_horse"
            )
            is not None
            or player.get_equipment(
                "offensive_horse"
            )
            is not None
        )


    def take_cards_for_weapon(
        self,
        player,
        number
    ):

        cards = []

        hand_rect = PLAYER_HAND_SOURCE_RECT
        if player is self.enemy:
            hand_rect = ENEMY_HAND_RECT

        while player.hand and len(cards) < number:

            cards.append(
                (
                    player.remove_card(0),
                    hand_rect,
                )
            )

        return cards


    def queue_weapon_discards(
        self,
        cards,
        after=None
    ):

        if not cards:

            if after is not None:
                after()

            return

        last_index = len(cards) - 1

        for index, (card, rect) in enumerate(cards):

            callback = None

            if index == last_index:
                callback = after

            self.queue_to_discard(
                card,
                rect,
                after=callback
            )

    # ==================================================
    # 玩家对电脑使用杀的结算
    # ==================================================

    def resolve_player_sha(
        self,
        sha,
        damage
    ):

        # 雌雄双股剑：当前 AI 在有手牌时选择弃牌，
        # 没有手牌时令玩家摸一张。
        if (
            has_gender_swords(self.player)
            and self.have_different_genders(
                self.player,
                self.enemy
            )
            and not getattr(
                sha,
                "_cixiong_resolved",
                False
            )
        ):

            sha._cixiong_resolved = True

            if self.enemy.hand:

                discarded = self.enemy.remove_card(0)

                self.message = (
                    "电脑响应【雌雄双股剑】，"
                    "弃置一张手牌。"
                )

                self.queue_to_discard(
                    discarded,
                    ENEMY_HAND_RECT,
                    after=(
                        lambda c=sha, d=damage:
                            self.resolve_player_sha(
                                c,
                                d
                            )
                    )
                )

            else:

                self.message = (
                    "电脑没有手牌，"
                    "【雌雄双股剑】令你摸一张牌。"
                )

                self.queue_draw_cards(
                    self.player,
                    1,
                    PLAYER_HAND_SOURCE_RECT,
                    after=(
                        lambda c=sha, d=damage:
                            self.resolve_player_sha(
                                c,
                                d
                            )
                    )
                )

            return

        # ==================================================
        # 仁王盾 / 藤甲
        #
        # 如果玩家装备青釭剑，
        # get_sha_blocking_armor 会直接返回 None。
        # ==================================================

        blocking_armor = (
            self.get_sha_blocking_armor(
                self.player,
                self.enemy,
                sha
            )
        )

        if blocking_armor is not None:

            self.message = (
                "电脑的【"
                + blocking_armor
                + "】令【"
                + sha.display_name
                + "】无效。"
            )

            self.actions.add(
                WaitAction(0.45)
            )

            self.queue_to_discard(
                sha,
                TABLE_CARD_RECT
            )

            return

        # ==================================================
        # 八卦阵
        #
        # 青釭剑攻击时跳过八卦阵。
        # ==================================================

        if self.has_bagua_armor(
            self.enemy,
            attacker=self.player
        ):

            self.queue_bagua_judgment(
                self.enemy,

                on_success=(

                    lambda c=sha, d=damage:
                        self.enemy_bagua_success(
                            c,
                            d
                        )
                ),

                on_failure=(

                    lambda c=sha, d=damage:
                        self.resolve_player_sha_after_bagua(
                            c,
                            d
                        )
                )
            )

            return

        self.resolve_player_sha_after_bagua(
            sha,
            damage
        )


    # ==================================================
    # 电脑八卦阵判定成功
    # ==================================================

    def enemy_bagua_success(
        self,
        sha,
        damage
    ):

        self.message = (
            "电脑的【八卦阵】判定成功，"
            "视为使用了【闪】。"
        )

        self.actions.add(
            WaitAction(0.40)
        )

        self.actions.add(
            CallbackAction(
                lambda c=sha, d=damage:
                    self.offer_player_missed_sha(
                        c,
                        d
                    )
            )
        )


    # ==================================================
    # 八卦判定失败或没有八卦阵后继续结算
    # ==================================================

    def resolve_player_sha_after_bagua(
        self,
        sha,
        damage
    ):

        # ==================================================
        # AI 有闪则使用
        # ==================================================

        if self.enemy.has_card(
            "SHAN"
        ):

            shan = (
                self.enemy.remove_first(
                    "SHAN"
                )
            )

            self.message = (
                "电脑使用了【闪】。"
            )

            self.actions.add(

                MoveCardAction(
                    shan,
                    ENEMY_HAND_RECT,
                    RESPONSE_CARD_RECT,
                    duration=0.27,

                    on_finish=(

                        lambda c=shan:
                            self.add_table_card(
                                c,
                                RESPONSE_CARD_RECT
                            )
                    )
                )
            )

            self.actions.add(
                WaitAction(0.45)
            )

            self.queue_to_discard(
                shan,
                RESPONSE_CARD_RECT,
                after=(
                    lambda c=sha, d=damage:
                        self.offer_player_missed_sha(
                            c,
                            d
                        )
                )
            )

            return

        # ==================================================
        # 没闪，受到伤害
        # ==================================================

        self.resolve_player_sha_damage(
            sha,
            damage
        )


    # ==================================================
    # 杀被电脑的闪抵消后处理武器
    # ==================================================

    def offer_player_missed_sha(
        self,
        sha,
        damage
    ):

        discardable = len(self.player.hand)

        if (
            has_stone_axe(self.player)
            and discardable >= 2
        ):

            self.choice.request(
                title="贯石斧",
                prompt=(
                    "弃置两张牌，令此【杀】依然命中？"
                ),
                yes_label="弃两张牌",
                no_label="不发动",
                on_yes=(
                    lambda c=sha, d=damage:
                        self.player_activate_stone_axe(
                            c,
                            d
                        )
                ),
                on_no=(
                    lambda c=sha, d=damage:
                        self.offer_player_qinglong(
                            c,
                            d
                        )
                )
            )

            return

        self.offer_player_qinglong(
            sha,
            damage
        )


    def player_activate_stone_axe(
        self,
        sha,
        damage
    ):

        self.start_card_selection(
            zone="player_hand",
            candidates=[
                (card, None)
                for card in self.player.hand
            ],
            number=2,
            prompt="贯石斧：请选择两张要弃置的手牌。",
            on_complete=(
                lambda selected, c=sha, d=damage:
                    self.finish_player_stone_axe(
                        c,
                        d,
                        selected
                    )
            )
        )


    def finish_player_stone_axe(
        self,
        sha,
        damage,
        selected
    ):

        discarded = []

        for card, rect, _ in selected:

            index = self.find_hand_card_index(card)

            if index is not None:
                discarded.append(
                    (
                        self.player.remove_card(index),
                        rect,
                    )
                )

        self.message = (
            "你发动【贯石斧】，弃置两张牌，"
            "令【杀】依然命中。"
        )

        self.queue_weapon_discards(
            discarded,
            after=(
                lambda c=sha, d=damage:
                    self.resolve_player_sha_damage(
                        c,
                        d
                    )
            )
        )


    def offer_player_qinglong(
        self,
        sha,
        damage
    ):

        if (
            has_qinglong_blade(self.player)
            and self.player.has_card("SHA")
        ):

            self.choice.request(
                title="青龙偃月刀",
                prompt="是否对相同目标再使用一张【杀】？",
                yes_label="继续出杀",
                no_label="结束结算",
                on_yes=(
                    lambda old=sha:
                        self.start_card_selection(
                            zone="player_hand",
                            candidates=[
                                (card, None)
                                for card in self.player.hand
                                if card.name == "SHA"
                            ],
                            number=1,
                            prompt=(
                                "青龙偃月刀：请选择要继续使用的【杀】。"
                            ),
                            on_complete=(
                                lambda selected, c=old:
                                    self.finish_player_qinglong(
                                        c,
                                        selected
                                    )
                            )
                        )
                ),
                on_no=(
                    lambda c=sha:
                        self.queue_to_discard(
                            c,
                            TABLE_CARD_RECT
                        )
                )
            )

            return

        self.queue_to_discard(
            sha,
            TABLE_CARD_RECT
        )


    def finish_player_qinglong(
        self,
        old_sha,
        selected
    ):

        new_sha, source_rect, _ = selected[0]

        self.queue_to_discard(
            old_sha,
            TABLE_CARD_RECT,
            after=(
                lambda c=new_sha, r=source_rect:
                    self.commit_player_sha(
                        c,
                        r,
                        use_zhuque=False
                    )
            )
        )


    # ==================================================
    # 玩家使用的杀即将造成伤害
    # ==================================================

    def resolve_player_sha_damage(
        self,
        sha,
        damage
    ):

        if (
            has_ice_sword(self.player)
            and self.enemy.hand
        ):

            self.choice.request(
                title="寒冰剑",
                prompt="防止伤害，改为弃置电脑至多两张牌？",
                yes_label="发动",
                no_label="造成伤害",
                on_yes=(
                    lambda c=sha:
                        self.player_activate_ice_sword(c)
                ),
                on_no=(
                    lambda c=sha, d=damage:
                        self.apply_player_sha_damage(
                            c,
                            d
                        )
                )
            )

            return

        self.apply_player_sha_damage(
            sha,
            damage
        )


    def player_activate_ice_sword(
        self,
        sha
    ):

        count = min(
            2,
            len(self.enemy.hand)
        )

        self.start_card_selection(
            zone="enemy_hand",
            candidates=[
                (card, None)
                for card in self.enemy.hand
            ],
            number=count,
            prompt=(
                "寒冰剑：请选择电脑要弃置的手牌。"
            ),
            on_complete=(
                lambda selected, c=sha:
                    self.finish_player_ice_sword(
                        c,
                        selected
                    )
            )
        )


    def finish_player_ice_sword(
        self,
        sha,
        selected
    ):

        discarded = []

        for card, rect, _ in selected:

            try:
                index = self.enemy.hand.index(card)
            except ValueError:
                continue

            discarded.append(
                (
                    self.enemy.remove_card(index),
                    rect,
                )
            )

        self.message = (
            "你发动【寒冰剑】，防止此次伤害，"
            "弃置电脑的牌。"
        )

        self.queue_weapon_discards(
            discarded,
            after=(
                lambda c=sha:
                    self.queue_to_discard(
                        c,
                        TABLE_CARD_RECT
                    )
            )
        )


    def apply_player_sha_damage(
        self,
        sha,
        damage
    ):

        damage, effects = (
            self.apply_sha_damage_modifiers(
                self.player,
                self.enemy,
                sha,
                damage
            )
        )

        self.enemy.hp -= damage

        self.message = (
            "【"
            + sha.display_name
            + "】命中电脑，造成 "
            + str(damage)
            + " 点伤害。"
        )

        if effects:

            self.message += (
                "（"
                + "；".join(
                    effects
                )
                + "）"
            )

        self.actions.add(
            WaitAction(0.45)
        )

        if (
            has_qilin_bow(self.player)
            and self.player_has_mount(self.enemy)
        ):

            self.choice.request(
                title="麒麟弓",
                prompt="是否弃置电脑装备区的一张坐骑牌？",
                yes_label="弃置坐骑",
                no_label="不发动",
                on_yes=(
                    lambda c=sha:
                        self.player_activate_qilin_bow(c)
                ),
                on_no=(
                    lambda c=sha:
                        self.finish_player_sha_damage(c)
                )
            )

            return

        self.finish_player_sha_damage(
            sha
        )


    def player_activate_qilin_bow(
        self,
        sha
    ):

        candidates = []

        for slot in (
            "defensive_horse",
            "offensive_horse",
        ):

            card = self.enemy.get_equipment(slot)

            if card is not None:
                candidates.append(
                    (
                        card,
                        slot,
                    )
                )

        self.start_card_selection(
            zone="enemy_equipment",
            candidates=candidates,
            number=1,
            prompt="麒麟弓：请选择要弃置的坐骑。",
            on_complete=(
                lambda selected, c=sha:
                    self.finish_player_qilin_bow(
                        c,
                        selected
                    )
            )
        )


    def finish_player_qilin_bow(
        self,
        sha,
        selected
    ):

        _, _, slot = selected[0]

        horse, _ = self.remove_equipment_with_effects(
            self.enemy,
            slot
        )

        self.message += (
            "你发动【麒麟弓】，弃置电脑的坐骑。"
        )

        self.queue_to_discard(
            horse,
            ENEMY_EQUIPMENT_RECTS[slot],
            after=(
                lambda c=sha:
                    self.finish_player_sha_damage(c)
            )
        )


    def finish_player_sha_damage(
        self,
        sha
    ):

        self.queue_to_discard(
            sha,
            TABLE_CARD_RECT,
            after=self.after_enemy_took_damage
        )


    # ==================================================
    # 电脑对玩家使用杀
    # ==================================================

    def request_player_shan(
        self,
        sha,
        damage
    ):

        if (
            has_gender_swords(self.enemy)
            and self.have_different_genders(
                self.enemy,
                self.player
            )
            and not getattr(
                sha,
                "_cixiong_resolved",
                False
            )
        ):

            sha._cixiong_resolved = True

            if self.player.hand:

                self.choice.request(
                    title="雌雄双股剑",
                    prompt=(
                        "弃置一张手牌，或令电脑摸一张牌？"
                    ),
                    yes_label="弃置手牌",
                    no_label="电脑摸牌",
                    on_yes=(
                        lambda c=sha, d=damage:
                            self.start_player_cixiong_discard(
                                c,
                                d
                            )
                    ),
                    on_no=(
                        lambda c=sha, d=damage:
                            self.player_answer_cixiong(
                                c,
                                d
                            )
                    )
                )

            else:

                self.player_answer_cixiong(
                    sha,
                    damage
                )

            return

        # ==================================================
        # 防具直接抵消
        #
        # 如果电脑装备青釭剑，
        # 这里不会触发玩家防具。
        # ==================================================

        blocking_armor = (
            self.get_sha_blocking_armor(
                self.enemy,
                self.player,
                sha
            )
        )

        if blocking_armor is not None:

            self.phase = "enemy"

            self.message = (
                "你的【"
                + blocking_armor
                + "】令电脑的【"
                + sha.display_name
                + "】无效。"
            )

            self.actions.add(
                WaitAction(0.45)
            )

            self.queue_to_discard(
                sha,
                TABLE_CARD_RECT,
                after=self.after_enemy_sha_finished
            )

            return

        # ==================================================
        # 八卦阵
        #
        # 青釭剑攻击时不发动八卦阵。
        # ==================================================

        if self.has_bagua_armor(
            self.player,
            attacker=self.enemy
        ):

            self.phase = "enemy"

            self.choice.request(
                title="八卦阵",
                prompt="是否发动【八卦阵】进行判定？",
                yes_label="发动判定",
                no_label="不发动",
                on_yes=(
                    lambda c=sha, d=damage:
                        self.queue_bagua_judgment(
                            self.player,
                            on_success=(
                                lambda card=c, value=d:
                                    self.player_bagua_success(
                                        card,
                                        value
                                    )
                            ),
                            on_failure=(
                                lambda card=c, value=d:
                                    self.request_player_shan_after_bagua(
                                        card,
                                        value
                                    )
                            )
                        )
                ),
                on_no=(
                    lambda c=sha, d=damage:
                        self.request_player_shan_after_bagua(
                            c,
                            d
                        )
                )
            )

            return

        self.request_player_shan_after_bagua(
            sha,
            damage
        )


    def player_answer_cixiong(
        self,
        sha,
        damage
    ):

        self.message = (
            "你令电脑因【雌雄双股剑】摸一张牌。"
        )

        self.queue_draw_cards(
            self.enemy,
            1,
            ENEMY_HAND_RECT,
            after=(
                lambda c=sha, d=damage:
                    self.request_player_shan(
                        c,
                        d
                    )
            )
        )


    def start_player_cixiong_discard(
        self,
        sha,
        damage
    ):

        self.start_card_selection(
            zone="player_hand",
            candidates=[
                (card, None)
                for card in self.player.hand
            ],
            number=1,
            prompt="雌雄双股剑：请选择一张手牌弃置。",
            on_complete=(
                lambda selected, c=sha, d=damage:
                    self.finish_player_cixiong_discard(
                        c,
                        d,
                        selected
                    )
            )
        )


    def finish_player_cixiong_discard(
        self,
        sha,
        damage,
        selected
    ):

        card, source_rect, _ = selected[0]
        index = self.find_hand_card_index(card)

        if index is None:
            return

        discarded = self.player.remove_card(index)

        self.message = (
            "你响应【雌雄双股剑】，弃置一张手牌。"
        )

        self.queue_to_discard(
            discarded,
            source_rect,
            after=(
                lambda c=sha, d=damage:
                    self.request_player_shan(
                        c,
                        d
                    )
            )
        )


    # ==================================================
    # 八卦阵失败 / 无八卦阵
    # 进入玩家手动闪响应
    # ==================================================

    def request_player_shan_after_bagua(
        self,
        sha,
        damage
    ):

        self.phase = "response"

        self.message = (
            "电脑对你使用【"
            + sha.display_name
            + "】，请选择【闪】或不响应。"
        )

        self.response.request(

            prompt=(
                "请使用【闪】，或选择不响应"
            ),

            allowed_cards={
                "SHAN"
            },

            on_card=(

                lambda index, card, rect, d=damage:
                    self.player_respond_shan(
                        index,
                        card,
                        rect,
                        sha,
                        d
                    )
            ),

            on_pass=(

                lambda:
                    self.player_pass_shan(
                        sha,
                        damage
                    )
            )
        )


    # ==================================================
    # 玩家八卦阵判定成功
    # ==================================================

    def player_bagua_success(
        self,
        sha,
        damage
    ):

        self.phase = "enemy"

        self.message = (
            "你的【八卦阵】判定成功，"
            "视为使用了【闪】。"
        )

        self.actions.add(
            WaitAction(0.40)
        )

        self.actions.add(
            CallbackAction(
                lambda c=sha, d=damage:
                    self.resolve_enemy_missed_sha(
                        c,
                        d
                    )
            )
        )


    # ==================================================
    # 玩家点击响应牌
    # ==================================================

    def respond_with_card(
        self,
        index,
        source_rect
    ):

        if self.busy:
            return

        if not self.response.active:
            return

        if not (
            0 <= index
            < len(self.player.hand)
        ):

            return

        # 响应同样走统一的 Card Action Discovery：真实【闪】与
        # 【龙胆】杀当闪这类转化动作由同一份查询选出，UI 不再自己判断牌名。
        self.begin_card_action(self.player.hand[index], source_rect, index=index)


    # ==================================================
    # 玩家手动使用闪
    # ==================================================

    def player_respond_shan(
        self,
        index,
        card,
        source_rect,
        sha,
        damage
    ):

        shan = self.player.remove_card(
            index
        )

        if shan is None:
            return

        self.phase = "enemy"

        self.message = (
            "你使用了【闪】。"
        )

        self.actions.add(

            MoveCardAction(
                shan,
                source_rect,
                RESPONSE_CARD_RECT,
                duration=0.28,

                on_finish=(

                    lambda c=shan:
                        self.add_table_card(
                            c,
                            RESPONSE_CARD_RECT
                        )
                )
            )
        )

        self.actions.add(
            WaitAction(0.50)
        )

        self.queue_to_discard(
            shan,
            RESPONSE_CARD_RECT,
            after=(
                lambda c=sha, d=damage:
                    self.resolve_enemy_missed_sha(
                        c,
                        d
                    )
            )
        )


    # ==================================================
    # 电脑的杀被抵消后处理武器
    # ==================================================

    def resolve_enemy_missed_sha(
        self,
        sha,
        damage
    ):

        discardable = len(self.enemy.hand)

        if (
            has_stone_axe(self.enemy)
            and discardable >= 2
        ):

            discarded = self.take_cards_for_weapon(
                self.enemy,
                2
            )

            self.message = (
                "电脑发动【贯石斧】，弃置两张牌，"
                "令【杀】依然命中。"
            )

            self.queue_weapon_discards(
                discarded,
                after=(
                    lambda c=sha, d=damage:
                        self.resolve_enemy_sha_damage(
                            c,
                            d
                        )
                )
            )

            return

        if (
            has_qinglong_blade(self.enemy)
            and self.enemy.has_card("SHA")
        ):

            self.message = (
                "电脑发动【青龙偃月刀】，"
                "对你再使用一张【杀】。"
            )

            self.queue_to_discard(
                sha,
                TABLE_CARD_RECT,
                after=self.enemy_attack
            )

            return

        self.queue_to_discard(
            sha,
            TABLE_CARD_RECT,
            after=self.after_enemy_sha_finished
        )


    # ==================================================
    # 点击“不响应”
    # ==================================================

    def pass_response(self):

        if self.busy:
            return

        if not self.response.active:
            return

        self.response.pass_response()


    # ==================================================
    # 玩家没有出闪
    # ==================================================

    def player_pass_shan(
        self,
        sha,
        damage
    ):

        self.phase = "enemy"

        self.resolve_enemy_sha_damage(
            sha,
            damage
        )


    def resolve_enemy_sha_damage(
        self,
        sha,
        damage
    ):

        # 电脑在能弃到牌时自动发动寒冰剑。
        if (
            has_ice_sword(self.enemy)
            and self.player.hand
        ):

            discarded = self.take_cards_for_weapon(
                self.player,
                2
            )

            self.message = (
                "电脑发动【寒冰剑】，防止此次伤害，"
                "改为弃置你的牌。"
            )

            self.queue_weapon_discards(
                discarded,
                after=(
                    lambda c=sha:
                        self.queue_to_discard(
                            c,
                            TABLE_CARD_RECT,
                            after=self.after_enemy_sha_finished
                        )
                )
            )

            return

        damage, effects = (
            self.apply_sha_damage_modifiers(
                self.enemy,
                self.player,
                sha,
                damage
            )
        )

        self.player.hp -= damage

        self.message = (
            "你受到 "
            + str(damage)
            + " 点伤害。"
        )

        if effects:

            self.message += (
                "（"
                + "；".join(
                    effects
                )
                + "）"
            )

        self.actions.add(
            WaitAction(0.50)
        )

        if (
            has_qilin_bow(self.enemy)
            and self.player_has_mount(self.player)
        ):

            slot = "defensive_horse"

            if self.player.get_equipment(slot) is None:
                slot = "offensive_horse"

            horse, _ = self.remove_equipment_with_effects(
                self.player,
                slot
            )

            self.message += (
                "电脑发动【麒麟弓】，弃置你的坐骑。"
            )

            self.queue_to_discard(
                horse,
                PLAYER_EQUIPMENT_RECTS[slot],
                after=(
                    lambda c=sha:
                        self.finish_enemy_sha_damage(c)
                )
            )

            return

        self.finish_enemy_sha_damage(
            sha
        )


    def finish_enemy_sha_damage(
        self,
        sha
    ):

        self.queue_to_discard(
            sha,
            TABLE_CARD_RECT,
            after=self.after_player_took_damage
        )
