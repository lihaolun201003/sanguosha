"""联机开局屏：**看身份 → 选将**，与单机是同一套屏幕。

联机与单机的差别从来只是"数据从哪来"：

* 单机读 ``game.player.identity`` / ``game.generals`` / ``game.selected_general``；
* 联机读房主发来的 ``IDENTITY_ASSIGN`` / ``GENERAL_CANDIDATES``，选择回传房主。

所以这里一行界面都不重画：它把网络数据装进一个最小适配器（``LanSetupGame``），
再交给 ``IdentityRevealScreen`` 与 ``GeneralSelectScreen`` 直接使用。之前联机
开局跳过这两步，玩家一进桌就既不知道自己是什么身份、也没见过自己的武将——
那正是"看起来不像一局身份局"的来源之一。
"""

import random
from types import SimpleNamespace

import pygame

from ..game.generals import create_default_general_registry
from ..game.identity import Identity
from ..game.skills import create_default_skill_registry
from . import layout, theme
from .general_select import GeneralSelectScreen
from .identity_reveal import IdentityRevealScreen

STAGE_IDENTITY = "identity"
STAGE_GENERALS = "generals"


class LanSetupGame:
    """让单机那两个开局屏能直接吃网络数据的最小适配器。

    它**不是**第二个 Game：没有牌堆、没有规则、没有流程，只有"我是谁、
    我能选哪些武将"。身份与候选都来自房主，选择也回给房主。
    """

    def __init__(self):
        self.generals = create_default_general_registry()
        # 技能表也是纯数据（技能名是公开信息），选将屏要按它列出武将技能。
        self.skill_registry = create_default_skill_registry()
        self.selected_general = None
        self.rng = random.Random()
        self.player = SimpleNamespace(identity=None)
        self.mode = SimpleNamespace(lord=lambda: None)
        self.pending_identities = ()
        self.candidate_ids = ()

    def apply_identity(self, value, lord_name=""):
        try:
            self.player.identity = Identity(value) if value else None
        except ValueError:                                # pragma: no cover - 兜底
            self.player.identity = None
        self.mode = SimpleNamespace(
            lord=(lambda name=str(lord_name or ""):
                  (SimpleNamespace(name=name) if name else None)))
        return self

    def set_candidates(self, entries):
        self.candidate_ids = tuple(
            str(item.get("general_id")) for item in entries or ()
            if isinstance(item, dict) and item.get("general_id"))
        self.selected_general = None
        return self

    def candidate_generals(self):
        """候选武将定义（按房主给的顺序）。"""

        found = []
        for general_id in self.candidate_ids:
            general = self.generals.get(general_id)
            if general is not None:
                found.append(general)
        return tuple(found)


class LanSetupScreen:
    """联机开局的两屏（身份 / 选将），共享同一份布局与屏幕。"""

    def __init__(self, screen):
        self.screen = screen
        self.metrics = None
        # 联机的候选由房主定：随机与返回都没有意义，关掉。
        self.identity_reveal = IdentityRevealScreen(screen)
        self.general_select = GeneralSelectScreen(
            screen, allow_random=False, allow_back=False)
        self.game = LanSetupGame()
        self.stage = STAGE_IDENTITY
        self.notice = ""
        self._identity_value = None

    # ==================================================
    # 数据入口（由网络桥的回调驱动）
    # ==================================================

    def reset(self):
        self.game = LanSetupGame()
        self.stage = STAGE_IDENTITY
        self.notice = ""
        self._identity_value = None
        return self

    def apply_identity(self, value, lord_name=""):
        self.game.apply_identity(value, lord_name)
        self.stage = STAGE_IDENTITY
        self.notice = "看好了就点「继续」，接下来选武将"
        return self

    def apply_candidates(self, entries):
        self.game.set_candidates(entries)
        self.stage = STAGE_GENERALS
        self.notice = "候选由房主给出，选定后确认出战"
        self._sync_candidates_layout()
        return self

    def _sync_candidates_layout(self):
        """选将屏只显示**房主给的候选**（3 个），不是整张武将表。"""

        if self.metrics is None:
            return self
        candidates = self.game.candidate_generals()
        if tuple(self.general_select.generals) != candidates:
            self.general_select.sync_layout(self.metrics, candidates)
        return self

    def sync_from_match(self, match):
        """把房主发来的身份 / 候选同步到屏幕上（幂等，每帧都会调用）。

        同时维护那一行提示：这一步做完了、还在等房主的时候必须说清楚，
        否则玩家会以为整局卡住了。
        """

        if match is None:
            return self
        identity = str(getattr(match, "my_identity", "") or "")
        if identity != self._identity_value:
            self._identity_value = identity
            self.apply_identity(identity, getattr(match, "lord_name", ""))
        if (getattr(match, "candidates_ready", False)
                and self.stage == STAGE_IDENTITY):
            self.apply_candidates(getattr(match, "candidates", ()))
        self.notice = self._notice_for(match)
        return self

    def _notice_for(self, match):
        """当前这一步该说什么：还没做 / 已经做了在等房主。"""

        if self.stage == STAGE_IDENTITY:
            if getattr(match, "identity_seen", False):
                return "已确认身份，等待房主下发候选武将…"
            return "看好了就点「继续」，接下来选武将"
        if getattr(match, "picked_general", ""):
            return "已提交武将，等待其他玩家与房主开局…"
        return "候选由房主给出，选定后确认出战"

    # ==================================================
    # 布局 / 屏幕
    # ==================================================

    def set_screen(self, surface):
        self.screen = surface
        self.identity_reveal.screen = surface
        self.general_select.screen = surface
        return self

    def sync_layout(self, metrics=None):
        self.metrics = metrics or layout.LayoutMetrics(
            layout.DESIGN_WIDTH, layout.DESIGN_HEIGHT)
        self.identity_reveal.sync_layout(self.metrics)
        self.general_select.sync_layout(
            self.metrics, self.game.candidate_generals())
        return self

    # ==================================================
    # 交互
    # ==================================================

    def handle_event(self, event, match):
        """返回 ``"identity_ready"`` / ``"picked"`` / ``None``。

        这里只做两件事：把点击转给对应的单机屏幕，再把它的结论翻译成一条
        网络消息（看完了 / 选了谁）。规则判断一条都没有。
        """

        if match is None or event.type != pygame.MOUSEBUTTONDOWN:
            return None
        if getattr(event, "button", 1) != 1:
            return "handled"

        if self.stage == STAGE_IDENTITY:
            if self.identity_reveal.handle_click(event.pos, self.game) == "continue":
                match.confirm_identity()
                return "identity_ready"
            return "handled"

        action = self.general_select.handle_click(event.pos, self.game)
        if action == "confirm":
            if match.pick_general(self.game.selected_general):
                return "picked"
            self.notice = "这个武将不在房主给的候选里"
            return "handled"
        return "handled"

    # ==================================================
    # 绘制
    # ==================================================

    def draw(self, match, metrics=None):
        metrics = metrics or self.metrics
        if metrics is None or self.metrics is not metrics:
            self.sync_layout(metrics)
            metrics = self.metrics

        # 身份屏自己不带背景（单机里它叠在开始菜单上），联机这边铺一层牌桌底：
        # 否则刚切进来时屏幕上还留着大厅的残影。
        self.screen.blit(theme.table_surface(metrics.screen_w, metrics.screen_h), (0, 0))

        if self.stage == STAGE_IDENTITY:
            self.identity_reveal.draw(self.game, metrics)
        else:
            # 候选由房主给：每帧对齐一次，避免屏幕上出现整张武将表。
            self._sync_candidates_layout()
            self.general_select.draw(self.game, metrics)

        # 一行小字：告诉玩家现在这一步在等谁（房主视角的"等待其他玩家"同理）。
        if not self.notice:
            return
        font = metrics.fonts.get("small")
        text = font.render(self.notice, True, theme.TEXT_DIM)
        self.screen.blit(text, text.get_rect(
            center=(metrics.screen_w // 2, metrics.screen_h - metrics.px(28))))
