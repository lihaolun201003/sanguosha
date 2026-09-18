import os
import pygame

from src.constants import (
    BACKGROUND_COLOR,
    BLACK,
    CARD_GAP,
    CARD_HEIGHT,
    CARD_WIDTH,
    DISCARD_PILE_RECT,
    DRAW_PILE_RECT,
    END_TURN_RECT,
    ENEMY_EQUIPMENT_RECTS,
    PASS_RESPONSE_RECT,
    PLAYER_EQUIPMENT_RECTS,
    PLAYER_HAND_Y,
    RESTART_RECT,
    MAIN_MENU_RECT,
    WHITE,
    WIDTH,
)


# ==================================================
# 中文字体
# ==================================================

def create_chinese_font(size):

    font_names = [
        "Microsoft YaHei",
        "Microsoft JhengHei",
        "DengXian",
        "SimHei",
        "SimSun",
        "KaiTi",
        "FangSong",
        "PingFang SC",
        "Hiragino Sans GB",
        "Heiti SC",
        "STHeiti",
        "Arial Unicode MS",
    ]


    for name in font_names:

        font_path = pygame.font.match_font(
            name
        )

        if font_path:

            return pygame.font.Font(
                font_path,
                size
            )


    possible_paths = [

        "/System/Library/Fonts/PingFang.ttc",

        "/System/Library/Fonts/STHeiti Light.ttc",

        "/System/Library/Fonts/STHeiti Medium.ttc",
    ]


    for path in possible_paths:

        if os.path.exists(path):

            return pygame.font.Font(
                path,
                size
            )


    return pygame.font.Font(
        None,
        size
    )


class Renderer:

    def __init__(
        self,
        screen
    ):

        self.screen = screen

        self.font = create_chinese_font(
            32
        )

        self.small_font = (
            create_chinese_font(
                23
            )
        )

        self.tiny_font = (
            create_chinese_font(
                16
            )
        )

        suit_font_path = pygame.font.match_font(
            "Segoe UI Symbol"
        )

        self.suit_font = pygame.font.Font(
            suit_font_path,
            16
        )

        self.big_font = (
            create_chinese_font(
                60
            )
        )


    # ==================================================
    # 手牌位置
    # ==================================================

    def get_card_rects(
        self,
        hand
    ):

        count = len(hand)

        if count == 0:
            return []


        max_width = 720

        gap = CARD_GAP


        if count > 1:

            natural_width = (
                count * CARD_WIDTH
                +
                (count - 1) * CARD_GAP
            )


            if natural_width > max_width:

                gap = (
                    max_width
                    -
                    count * CARD_WIDTH
                ) / (count - 1)


        total_width = (
            count * CARD_WIDTH
            +
            (count - 1) * gap
        )


        start_x = (
            WIDTH - total_width
        ) / 2


        rects = []


        for i in range(count):

            x = round(
                start_x
                +
                i
                *
                (
                    CARD_WIDTH
                    +
                    gap
                )
            )


            rects.append(

                pygame.Rect(
                    x,
                    PLAYER_HAND_Y,
                    CARD_WIDTH,
                    CARD_HEIGHT
                )
            )


        return rects


    # ==================================================
    # 点击手牌
    # ==================================================

    def card_at_position(
        self,
        position,
        hand
    ):

        rects = self.get_card_rects(
            hand
        )


        for i in range(
            len(rects) - 1,
            -1,
            -1
        ):

            if rects[i].collidepoint(
                position
            ):

                return i


        return None


    def get_enemy_hand_rects(
        self,
        hand
    ):

        shown = min(len(hand), 8)

        if shown == 0:
            return []

        start_x = (
            500
            - ((shown - 1) * 22 + 60) // 2
        )

        return [
            pygame.Rect(
                start_x + i * 22,
                155,
                60,
                85
            )
            for i in range(shown)
        ]


    def enemy_card_at_position(
        self,
        position,
        hand
    ):

        rects = self.get_enemy_hand_rects(hand)

        for i in range(
            len(rects) - 1,
            -1,
            -1
        ):

            if rects[i].collidepoint(position):
                return i

        return None


    # ==================================================
    # 按钮
    # ==================================================

    def get_end_turn_rect(self):

        return pygame.Rect(
            *END_TURN_RECT
        )


    def get_pass_response_rect(self):

        return pygame.Rect(
            *PASS_RESPONSE_RECT
        )


    def get_restart_rect(self):

        return pygame.Rect(
            *RESTART_RECT
        )

    def get_main_menu_rect(self):
        return pygame.Rect(*MAIN_MENU_RECT)


    # ==================================================
    # 卡牌
    # ==================================================

    def draw_card(
        self,
        card,
        rect,
        highlight=False,
        candidate=False
    ):

        pygame.draw.rect(
            self.screen,
            card.color,
            rect,
            border_radius=8
        )


        pygame.draw.rect(
            self.screen,
            BLACK,
            rect,
            3,
            border_radius=8
        )


        if candidate:

            pygame.draw.rect(
                self.screen,
                (100, 205, 255),
                rect.inflate(5, 5),
                2,
                border_radius=9
            )


        if highlight:

            highlight_rect = (
                rect.inflate(
                    8,
                    8
                )
            )

            pygame.draw.rect(
                self.screen,
                (255, 215, 70),
                highlight_rect,
                4,
                border_radius=10
            )


        # ==================================================
        # 左上角花色和点数
        # ==================================================

        if card.identity_label:

            suit_color = BLACK

            if card.card_color == "red":
                suit_color = (190, 35, 45)

            suit_text = self.suit_font.render(
                card.suit_symbol,
                True,
                suit_color
            )

            rank_text = self.tiny_font.render(
                str(card.rank),
                True,
                suit_color
            )

            identity_x = rect.x + 6
            identity_y = rect.y + 4

            self.screen.blit(
                suit_text,
                (
                    identity_x,
                    identity_y
                )
            )

            self.screen.blit(
                rank_text,
                (
                    identity_x
                    + suit_text.get_width()
                    + 2,
                    identity_y
                )
            )


        label = card.display_name


        # 小装备槽字体
        if rect.width <= 70:

            use_font = self.tiny_font


        elif len(label) <= 3:

            use_font = self.font


        elif len(label) <= 5:

            use_font = self.small_font


        else:

            use_font = self.tiny_font


        text = use_font.render(
            label,
            True,
            BLACK
        )


        self.screen.blit(

            text,

            text.get_rect(
                center=rect.center
            )
        )

    def get_public_card_rects(self, cards):
        width, gap = 64, 8
        total = len(cards) * width + max(0, len(cards) - 1) * gap
        start = 500 - total // 2
        return [pygame.Rect(start + i * (width + gap), 240, width, 92) for i in range(len(cards))]

    def draw_engine_state(self, game):
        selection = game.pending_selection
        for card, rect in zip(game.public_card_pool, self.get_public_card_rects(game.public_card_pool)):
            candidate = bool(selection and selection.get("zone") == "public_pool" and game.is_selection_candidate(card))
            selected = bool(selection and selection.get("zone") == "public_pool" and game.is_selection_selected(card))
            self.draw_card(card, rect, highlight=selected, candidate=candidate)
        for player, y in [(p, 118 + p.seat * 16) for p in game.players]:
            flags = []
            if player.chained:
                flags.append("横置")
            if player.judgement_zone:
                flags.append("判定区：" + "、".join(card.display_name for card in player.judgement_zone))
            if flags:
                text = self.tiny_font.render(" | ".join(flags), True, (255, 210, 90))
                self.screen.blit(text, (690, y))

    def get_player_panel_rects(self, game):
        opponents = [player for player in game.players if player is not game.player]
        rects = {game.player: pygame.Rect(330, 570, 340, 75)}
        count = len(opponents)
        if count <= 3:
            width, gap = 220, 22
            total = count * width + max(0, count - 1) * gap
            start = (WIDTH - total) // 2
            for index, player in enumerate(opponents):
                rects[player] = pygame.Rect(start + index * (width + gap), 52, width, 108)
        else:
            top_count, width, gap = min(4, count), 190, 18
            total = top_count * width + (top_count - 1) * gap
            start = (WIDTH - total) // 2
            for index, player in enumerate(opponents[:top_count]):
                rects[player] = pygame.Rect(start + index * (width + gap), 48, width, 106)
            for index, player in enumerate(opponents[top_count:]):
                side, row = index % 2, index // 2
                rects[player] = pygame.Rect(12 if side == 0 else WIDTH - 202, 178 + row * 118, 190, 106)
        return rects

    def player_at_position(self, position, game):
        for player, rect in self.get_player_panel_rects(game).items():
            if rect.collidepoint(position):
                return player
        return None

    def draw_opponents(self, game):
        selection = game.pending_target_selection
        panel_rects = self.get_player_panel_rects(game)
        for player, rect in panel_rects.items():
            if player is game.player:
                continue
            candidate = bool(selection and player in selection["candidates"])
            selected = bool(selection and player in selection["selected"])
            fill = (92, 98, 100) if not player.alive else (205, 205, 198)
            pygame.draw.rect(self.screen, fill, rect, border_radius=9)
            border = (245, 205, 70) if selected else ((80, 175, 255) if candidate else ((255, 220, 90) if player is game.current_turn_player else (90, 90, 90)))
            pygame.draw.rect(self.screen, border, rect, 4 if (selected or candidate or player is game.current_turn_player) else 2, border_radius=9)
            status = "阵亡" if not player.alive else ("横置" if player.chained else "存活")
            lines = [
                player.name + "  座次 " + str(player.seat) + "  " + status,
                "HP " + str(player.hp) + "/" + str(player.max_hp) + "  手牌 × " + str(len(player.hand)),
                "武器:" + getattr(player.get_equipment("weapon"), "display_name", "-") + "  防具:" + getattr(player.get_equipment("armor"), "display_name", "-"),
                "判定:" + ("、".join(card.display_name for card in player.judgement_zone) or "-"),
            ]
            for line_index, line in enumerate(lines):
                surface = self.tiny_font.render(line, True, BLACK)
                self.screen.blit(surface, (rect.x + 7, rect.y + 7 + line_index * 23))

        current_name = game.current_turn_player.name if game.current_turn_player else "-"
        turn = self.small_font.render("当前回合：" + current_name, True, (255, 230, 150))
        self.screen.blit(turn, turn.get_rect(center=(500, 185)))
        if selection and game.player in selection["candidates"]:
            rect = panel_rects[game.player]
            color = (245, 205, 70) if game.player in selection["selected"] else (80, 175, 255)
            pygame.draw.rect(self.screen, color, rect, 4, border_radius=9)

    def draw_recent_log(self, game):
        entries = game.game_log[-3:]
        if not entries:
            return
        panel = pygame.Surface((420, 64), pygame.SRCALPHA)
        panel.fill((15, 45, 28, 175))
        self.screen.blit(panel, (290, 200))
        for index, entry in enumerate(entries):
            text = self.tiny_font.render(entry[:40], True, (225, 232, 220))
            self.screen.blit(text, (300, 204 + index * 19))


    # ==================================================
    # 鼠标悬停卡牌说明
    # ==================================================

    def get_hovered_card(
        self,
        game,
        mouse_pos
    ):

        # ==================================================
        # 玩家手牌
        # 重叠时从最上层牌开始判断
        # ==================================================

        card_rects = self.get_card_rects(
            game.player.hand
        )

        for i in range(
            len(card_rects) - 1,
            -1,
            -1
        ):

            if card_rects[i].collidepoint(
                mouse_pos
            ):

                card = game.player.hand[i]

                if card.category in (
                    "equipment",
                    "trick",
                ):
                    return card

                return None


        # ==================================================
        # 玩家装备区
        # ==================================================

        for slot, rect_data in (
            PLAYER_EQUIPMENT_RECTS.items()
        ):

            rect = pygame.Rect(
                *rect_data
            )

            if rect.collidepoint(
                mouse_pos
            ):

                card = game.player.equipment.get(
                    slot
                )

                if card is not None:
                    return card

                return None


        # ==================================================
        # 电脑装备区
        # ==================================================

        for slot, rect_data in (
            ENEMY_EQUIPMENT_RECTS.items()
        ):

            rect = pygame.Rect(
                *rect_data
            )

            if rect.collidepoint(
                mouse_pos
            ):

                card = game.enemy.equipment.get(
                    slot
                )

                if card is not None:
                    return card

                return None


        # ==================================================
        # 桌面牌
        # 以后锦囊牌结算时也可以直接复用
        # ==================================================

        for card, rect_data in reversed(
            game.table_cards
        ):

            rect = pygame.Rect(
                *rect_data
            )

            if rect.collidepoint(
                mouse_pos
            ):

                if card.category in (
                    "equipment",
                    "trick",
                ):
                    return card

                return None


        return None


    # ==================================================
    # 卡牌类型中文名称
    # ==================================================

    def get_card_type_text(
        self,
        card
    ):

        if card.category == "trick":

            return "锦囊牌"


        if card.category != "equipment":

            return ""


        names = {

            "weapon": "装备牌 · 武器",

            "armor": "装备牌 · 防具",

            "defensive_horse": "装备牌 · +1马",

            "offensive_horse": "装备牌 · -1马",
        }


        return names.get(
            card.subtype,
            "装备牌"
        )


    # ==================================================
    # 自动换行
    # ==================================================

    def wrap_tooltip_text(
        self,
        text,
        font,
        max_width
    ):

        lines = []


        for paragraph in str(text).split(
            "\n"
        ):

            if paragraph == "":

                lines.append("")

                continue


            current = ""


            for char in paragraph:

                candidate = (
                    current
                    + char
                )


                if (
                    current
                    and
                    font.size(
                        candidate
                    )[0]
                    > max_width
                ):

                    lines.append(
                        current
                    )

                    current = char


                else:

                    current = candidate


            if current:

                lines.append(
                    current
                )


        return lines


    # ==================================================
    # 绘制说明框
    # ==================================================

    def draw_card_tooltip(
        self,
        card,
        mouse_pos
    ):

        if card is None:
            return


        if card.category not in (
            "equipment",
            "trick",
        ):

            return


        padding = 14

        box_width = 330

        text_width = (
            box_width
            -
            padding * 2
        )


        title_font = (
            self.small_font
        )

        body_font = (
            self.tiny_font
        )


        lines = []


        type_text = (
            self.get_card_type_text(
                card
            )
        )


        if type_text:

            lines.append(
                (
                    type_text,
                    body_font,
                    (215, 215, 215)
                )
            )


        # ==================================================
        # 武器显示攻击范围
        # ==================================================

        if (
            card.category == "equipment"
            and
            card.subtype == "weapon"
        ):

            lines.append(
                (
                    "攻击范围："
                    + str(
                        card.attack_range
                    ),
                    body_font,
                    (255, 225, 150)
                )
            )


        # ==================================================
        # 效果文字
        # ==================================================

        description = (
            card.description
        )


        if not description:

            description = (
                "效果说明暂未录入。"
                "下一步会统一补全装备牌说明。"
            )


        wrapped = (
            self.wrap_tooltip_text(
                description,
                body_font,
                text_width
            )
        )


        for line in wrapped:

            lines.append(
                (
                    line,
                    body_font,
                    WHITE
                )
            )


        title_height = (
            title_font.get_linesize()
        )

        body_height = (
            body_font.get_linesize()
        )


        box_height = (
            padding
            +
            title_height
            +
            8
            +
            len(lines)
            * body_height
            +
            padding
        )


        screen_width = (
            self.screen.get_width()
        )

        screen_height = (
            self.screen.get_height()
        )


        # ==================================================
        # 默认显示在鼠标右下方
        # ==================================================

        x = (
            mouse_pos[0]
            + 18
        )

        y = (
            mouse_pos[1]
            + 18
        )


        # ==================================================
        # 右边放不下就放左边
        # ==================================================

        if (
            x
            + box_width
            >
            screen_width
            - 8
        ):

            x = (
                mouse_pos[0]
                -
                box_width
                -
                18
            )


        # ==================================================
        # 下边放不下就放上面
        # ==================================================

        if (
            y
            + box_height
            >
            screen_height
            - 8
        ):

            y = (
                mouse_pos[1]
                -
                box_height
                -
                18
            )


        x = max(
            8,
            min(
                x,
                screen_width
                -
                box_width
                -
                8
            )
        )


        y = max(
            8,
            min(
                y,
                screen_height
                -
                box_height
                -
                8
            )
        )


        box_rect = pygame.Rect(
            x,
            y,
            box_width,
            box_height
        )


        shadow_rect = (
            box_rect.move(
                4,
                4
            )
        )


        # ==================================================
        # 阴影
        # ==================================================

        pygame.draw.rect(
            self.screen,
            (25, 25, 25),
            shadow_rect,
            border_radius=10
        )


        # ==================================================
        # 背景
        # ==================================================

        pygame.draw.rect(
            self.screen,
            (55, 55, 55),
            box_rect,
            border_radius=10
        )


        # ==================================================
        # 边框
        # ==================================================

        pygame.draw.rect(
            self.screen,
            (225, 190, 120),
            box_rect,
            2,
            border_radius=10
        )


        # ==================================================
        # 卡牌名称
        # ==================================================

        title_label = card.display_name

        if card.identity_label:

            title_label += (
                "  "
                + card.suit_name
                + " "
                + str(card.rank)
            )


        title = (
            title_font.render(
                title_label,
                True,
                (255, 225, 150)
            )
        )


        self.screen.blit(
            title,
            (
                x + padding,
                y + padding
            )
        )


        text_y = (
            y
            +
            padding
            +
            title_height
            +
            8
        )


        for text, font, color in lines:

            rendered = (
                font.render(
                    text,
                    True,
                    color
                )
            )


            self.screen.blit(
                rendered,
                (
                    x + padding,
                    text_y
                )
            )


            text_y += (
                body_height
            )


    # ==================================================
    # 卡背
    # ==================================================

    def draw_card_back(
        self,
        rect,
        highlight=False,
        candidate=False
    ):

        pygame.draw.rect(
            self.screen,
            (135, 70, 45),
            rect,
            border_radius=8
        )


        pygame.draw.rect(
            self.screen,
            (225, 190, 120),
            rect,
            3,
            border_radius=8
        )


        if candidate:

            pygame.draw.rect(
                self.screen,
                (100, 205, 255),
                rect.inflate(5, 5),
                2,
                border_radius=9
            )


        if highlight:

            pygame.draw.rect(
                self.screen,
                (255, 215, 70),
                rect.inflate(8, 8),
                4,
                border_radius=10
            )


        text = (
            self.small_font.render(
                "牌",
                True,
                WHITE
            )
        )


        self.screen.blit(

            text,

            text.get_rect(
                center=rect.center
            )
        )


    # ==================================================
    # 牌堆
    # ==================================================

    def draw_piles(
        self,
        game
    ):

        draw_rect = pygame.Rect(
            *DRAW_PILE_RECT
        )

        discard_rect = pygame.Rect(
            *DISCARD_PILE_RECT
        )


        # ==================================================
        # 抽牌堆
        # ==================================================

        if game.deck.draw_pile:

            self.draw_card_back(
                draw_rect
            )


        else:

            pygame.draw.rect(
                self.screen,
                WHITE,
                draw_rect,
                2,
                border_radius=8
            )


        draw_text = (
            self.small_font.render(

                "抽牌堆："
                + str(
                    len(
                        game.deck.draw_pile
                    )
                ),

                True,
                WHITE
            )
        )


        self.screen.blit(

            draw_text,

            draw_text.get_rect(
                center=(
                    draw_rect.centerx,
                    405
                )
            )
        )


        # ==================================================
        # 弃牌堆
        # ==================================================

        if game.deck.discard_pile:

            self.draw_card(
                game.deck.discard_pile[-1],
                discard_rect
            )


        else:

            pygame.draw.rect(
                self.screen,
                WHITE,
                discard_rect,
                2,
                border_radius=8
            )


        discard_text = (
            self.small_font.render(

                "弃牌堆："
                + str(
                    len(
                        game.deck.discard_pile
                    )
                ),

                True,
                WHITE
            )
        )


        self.screen.blit(

            discard_text,

            discard_text.get_rect(
                center=(
                    discard_rect.centerx,
                    405
                )
            )
        )


    # ==================================================
    # 装备区
    # ==================================================

    def draw_equipment_zone(
        self,
        player,
        rect_map,
        title,
        game=None
    ):

        labels = {

            "weapon": "武器",

            "armor": "防具",

            "defensive_horse": "+1马",

            "offensive_horse": "-1马",
        }


        for slot, rect_data in (
            rect_map.items()
        ):

            rect = pygame.Rect(
                *rect_data
            )

            card = (
                player.equipment[
                    slot
                ]
            )


            if card is not None:

                candidate = False
                highlight = False

                if (
                    game is not None
                    and game.pending_selection is not None
                    and game.pending_selection["zone"]
                    == "enemy_equipment"
                ):

                    candidate = game.is_selection_candidate(
                        card,
                        slot
                    )
                    highlight = (
                        candidate
                        and game.is_selection_selected(
                            card,
                            slot
                        )
                    )

                self.draw_card(
                    card,
                    rect,
                    highlight=highlight,
                    candidate=candidate
                )


            else:

                pygame.draw.rect(
                    self.screen,
                    (225, 225, 225),
                    rect,
                    2,
                    border_radius=6
                )


                label = (
                    self.tiny_font.render(
                        labels[slot],
                        True,
                        WHITE
                    )
                )


                self.screen.blit(

                    label,

                    label.get_rect(
                        center=rect.center
                    )
                )


        title_text = (
            self.tiny_font.render(
                title,
                True,
                WHITE
            )
        )


        first_rect = pygame.Rect(
            *list(
                rect_map.values()
            )[0]
        )


        self.screen.blit(
            title_text,
            (
                first_rect.x,
                first_rect.y - 18
            )
        )


    # ==================================================
    # 电脑
    # ==================================================

    def draw_enemy(
        self,
        game
    ):

        enemy_rect = pygame.Rect(
            350,
            55,
            300,
            95
        )


        pygame.draw.rect(
            self.screen,
            (215, 215, 215),
            enemy_rect,
            border_radius=10
        )


        hp_text = (
            self.font.render(

                "电脑  体力："
                + str(
                    game.enemy.hp
                )
                + "/"
                + str(
                    game.enemy.max_hp
                ),

                True,
                BLACK
            )
        )


        self.screen.blit(

            hp_text,

            hp_text.get_rect(
                center=(
                    500,
                    88
                )
            )
        )


        info_text = (
            self.tiny_font.render(

                "手牌："
                + str(
                    len(
                        game.enemy.hand
                    )
                )
                + "   攻击范围："
                + str(
                    game.enemy.attack_range
                ),

                True,
                BLACK
            )
        )


        self.screen.blit(

            info_text,

            info_text.get_rect(
                center=(
                    500,
                    123
                )
            )
        )


        enemy_hand_rects = self.get_enemy_hand_rects(
            game.enemy.hand
        )

        for i, rect in enumerate(enemy_hand_rects):

            card = game.enemy.hand[i]
            candidate = (
                game.pending_selection is not None
                and game.pending_selection["zone"]
                == "enemy_hand"
                and game.is_selection_candidate(card)
            )
            highlight = (
                candidate
                and game.is_selection_selected(card)
            )

            self.draw_card_back(
                rect,
                highlight=highlight,
                candidate=candidate
            )


        self.draw_equipment_zone(
            game.enemy,
            ENEMY_EQUIPMENT_RECTS,
            "电脑装备区",
            game=game
        )


    # ==================================================
    # 桌面牌
    # ==================================================

    def draw_table_cards(
        self,
        game
    ):

        for card, rect_data in (
            game.table_cards
        ):

            rect = pygame.Rect(
                *rect_data
            )


            self.draw_card(
                card,
                rect
            )


    # ==================================================
    # 玩家
    # ==================================================

    def draw_player(
        self,
        game
    ):

        player_text = (
            self.font.render(

                "你  体力："
                + str(
                    game.player.hp
                )
                + "/"
                + str(
                    game.player.max_hp
                ),

                True,
                WHITE
            )
        )


        self.screen.blit(
            player_text,
            (
                20,
                585
            )
        )


        info_text = (
            self.tiny_font.render(

                "攻击范围："
                + str(
                    game.player.attack_range
                )
                + "   与电脑距离："
                + str(
                    game.get_distance(
                        game.player,
                        game.enemy
                    )
                ),

                True,
                WHITE
            )
        )


        self.screen.blit(
            info_text,
            (
                20,
                620
            )
        )


        card_rects = (
            self.get_card_rects(
                game.player.hand
            )
        )


        for card, rect in zip(
            game.player.hand,
            card_rects
        ):

            highlight = False
            candidate = False


            # ==================================================
            # 丈八蛇矛：突出显示玩家已经选中的素材牌
            # ==================================================

            if game.zhangba_selecting:

                candidate = True

                highlight = any(
                    selected[0] is card
                    for selected in game.zhangba_selected
                )


            # ==================================================
            # 响应阶段
            # ==================================================

            elif (
                game.pending_selection is not None
                and game.pending_selection["zone"]
                == "player_hand"
            ):

                candidate = game.is_selection_candidate(
                    card
                )
                highlight = (
                    candidate
                    and game.is_selection_selected(card)
                )


            elif game.response.active:

                highlight = (
                    game.response.can_play(
                        card
                    )
                )


            # ==================================================
            # 喝酒以后突出显示杀
            # ==================================================

            elif (
                game.wine_sha_required
                and
                card.name == "SHA"
            ):

                highlight = True


            self.draw_card(
                card,
                rect,
                highlight=highlight,
                candidate=candidate
            )


        self.draw_equipment_zone(
            game.player,
            PLAYER_EQUIPMENT_RECTS,
            "你的装备区",
            game=game
        )


    # ==================================================
    # 动画牌
    # ==================================================

    def draw_moving_card(
        self,
        game
    ):

        move = (
            game.actions.current_move()
        )


        if move is None:
            return


        rect = pygame.Rect(
            *move.current_rect()
        )


        self.draw_card(
            move.card,
            rect
        )


    # ==================================================
    # 响应界面
    # ==================================================

    def draw_response_controls(
        self,
        game
    ):

        if not game.response.active:
            return


        prompt_text = (
            game.response.current.prompt
        )


        prompt = (
            self.small_font.render(
                prompt_text,
                True,
                (255, 230, 150)
            )
        )


        self.screen.blit(

            prompt,

            prompt.get_rect(
                center=(
                    500,
                    485
                )
            )
        )


        pass_rect = (
            self.get_pass_response_rect()
        )


        pygame.draw.rect(
            self.screen,
            (200, 95, 80),
            pass_rect,
            border_radius=8
        )


        pass_text = (
            self.small_font.render(
                "不响应",
                True,
                WHITE
            )
        )


        self.screen.blit(

            pass_text,

            pass_text.get_rect(
                center=pass_rect.center
            )
        )


    # ==================================================
    # 当前阶段中文名
    # ==================================================

    def get_phase_name(
        self,
        phase
    ):

        names = {

            "play": "出牌阶段",

            "discard": "弃牌阶段",

            "enemy": "电脑回合",

            "response": "响应阶段",

            "draw": "摸牌阶段",
            "prepare": "准备阶段",
            "judge": "判定阶段",
            "finish": "结束阶段",

            "dying": "濒死状态",

            "over": "游戏结束",
        }


        return names.get(
            phase,
            phase
        )


    # ==================================================
    # 总绘制
    # ==================================================

    def draw(
        self,
        game
    ):

        self.screen.fill(
            BACKGROUND_COLOR
        )


        title = (
            self.font.render(
                "三国杀",
                True,
                WHITE
            )
        )


        self.screen.blit(

            title,

            title.get_rect(
                center=(
                    500,
                    25
                )
            )
        )


        self.draw_opponents(game)

        self.draw_piles(
            game
        )

        self.draw_table_cards(
            game
        )
        self.draw_engine_state(game)
        self.draw_recent_log(game)


        # ==================================================
        # 提示
        # ==================================================

        message = (
            self.small_font.render(
                game.message,
                True,
                WHITE
            )
        )


        self.screen.blit(

            message,

            message.get_rect(
                center=(
                    500,
                    275
                )
            )
        )


        # ==================================================
        # 阶段
        # ==================================================

        phase = (
            self.small_font.render(

                self.get_phase_name(
                    game.phase
                ),

                True,
                (255, 230, 150)
            )
        )


        self.screen.blit(

            phase,

            phase.get_rect(
                center=(
                    500,
                    305
                )
            )
        )


        self.draw_player(
            game
        )


        # ==================================================
        # 结束回合
        # ==================================================

        end_rect = (
            self.get_end_turn_rect()
        )


        if (
            game.busy
            or
            game.phase
            != "play"
            or
            game.response.active
            or
            game.wine_sha_required
        ):

            button_color = (
                120,
                120,
                120
            )


        else:

            button_color = (
                230,
                190,
                80
            )


        pygame.draw.rect(
            self.screen,
            button_color,
            end_rect,
            border_radius=8
        )


        end_text = (
            self.small_font.render(
                "确认目标" if game.pending_target_selection is not None else "结束回合",
                True,
                BLACK
            )
        )


        self.screen.blit(

            end_text,

            end_text.get_rect(
                center=end_rect.center
            )
        )


        self.draw_response_controls(
            game
        )


        # ==================================================
        # 动画牌最后绘制
        # ==================================================

        self.draw_moving_card(
            game
        )


        # ==================================================
        # 鼠标悬停说明
        # ==================================================

        if not game.game_over:

            mouse_pos = (
                pygame.mouse.get_pos()
            )


            hovered_card = (
                self.get_hovered_card(
                    game,
                    mouse_pos
                )
            )


            self.draw_card_tooltip(
                hovered_card,
                mouse_pos
            )


        # ==================================================
        # 游戏结束
        # ==================================================

        if (
            game.game_over
            and
            not game.busy
        ):

            overlay = pygame.Surface(
                (
                    1000,
                    700
                )
            )

            overlay.set_alpha(
                180
            )

            overlay.fill(
                (
                    0,
                    0,
                    0
                )
            )


            self.screen.blit(
                overlay,
                (
                    0,
                    0
                )
            )


            result = (
                self.big_font.render(
                    game.message,
                    True,
                    WHITE
                )
            )


            self.screen.blit(

                result,

                result.get_rect(
                    center=(
                        500,
                        300
                    )
                )
            )


            restart_rect = (
                self.get_restart_rect()
            )


            pygame.draw.rect(
                self.screen,
                (230, 190, 80),
                restart_rect,
                border_radius=10
            )


            restart = (
                self.font.render(
                    "重新开始",
                    True,
                    BLACK
                )
            )


            self.screen.blit(

                restart,

                restart.get_rect(
                    center=restart_rect.center
                )
            )

            menu_rect = self.get_main_menu_rect()
            pygame.draw.rect(self.screen, (165, 175, 165), menu_rect, border_radius=10)
            menu_text = self.font.render("返回主菜单", True, BLACK)
            self.screen.blit(menu_text, menu_text.get_rect(center=menu_rect.center))
