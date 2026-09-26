"""统一结算演出队列（Presentation / Resolution Queue，Phase 18）。

规则层与网络层可以继续全速推进，但**画面**必须按顺序消费表现事件：
判定牌还在屏幕中央的时候，这次判定的伤害 / 濒死 / 阵亡 / 下一个回合
都不许抢先出现（玩家反馈："判定动画还没看完，人已经死了"）。

# 它是什么

一条**只读、纯表现**的队列。每个进入队列的"演出"（``Step``）占住画面一段
时间；前一个演完，后一个才开始。队列自己一行都不画——真正画画的仍然是
既有的 ``Effects.show_*`` / ``JudgePanel`` / ``SkillBanner``。

# 与既有机制的分工（不要合并成一套）

    ActionQueue     管"实体牌的移动"（谁飞到哪、飞多久）
    JudgeGate       管"现在允不允许玩家操作"（规则侧的唯一判据）
    PresentationQueue  管"画面现在轮到谁演"（本模块）

三者都不 ``sleep``、都不改规则数据、都不阻塞引擎。队列唯一向外表达的
"压制"是 ``holding`` / ``Effects.interaction_hold()``：技能提示与判定面板
还在演时，本机玩家的操作界面先不出现（避免两个窗口叠在一起）。

# 速度是每台机器自己的

整条队列由**本机**的表现速度驱动：座主读 ``Game.speed``、客户端读
``RemoteGameView.speed``（两者互不同步，见 ``ui.speed`` 的说明）。
关键演出有"最短可读时间"下限，极速档下判定牌也不会只闪 0.05 秒。

# 视觉状态与权威状态分开

``VisualLedger`` 记录"还没轮到播的体力增减 / 阵亡"。显示值 = 权威值 −
未播增量：快照可以立刻把体力改成 0，但画面在伤害演完之前仍然显示 3。
权威状态（规则、决策、命中测试）永远是已经更新的那一份。
"""

from collections import deque

# ==================================================
# 演出种类（供测试与报告引用；不是规则事件）
# ==================================================

STEP_CARD = "card_used"
STEP_JUDGE = "judge"
STEP_RESULT = "judge_result"
STEP_SKILL = "skill"
STEP_DAMAGE = "damage"
STEP_LOSE_HP = "lose_hp"
STEP_RECOVER = "recover"
STEP_DYING = "dying"
STEP_DEATH = "death"
STEP_CHAIN = "chain"
STEP_PHASE_SKIP = "phase_skip"
STEP_TURN = "turn_start"

#: 队列积压上限：正常的客户端会立刻排空；积压这么多说明这台机器已经跟不上。
#: 超限时优先丢掉**非关键**演出（伤害飘字一类），判定与技能提示一定保留。
MAX_PENDING = 64

#: 表现速度的基准档（与 ``Game.DEFAULT_SPEED`` 一致）。
#: 队列的实时推进速度 = dt * speed / BASE，因此默认档与 Phase 17 完全一致，
#: 慢档更长、快档更短。
BASE_SPEED = 0.75

#: 实时推进倍率的上下限（防手改出离谱的值）。
MIN_SPEED_FACTOR = 0.35
MAX_SPEED_FACTOR = 2.5


def _timing():
    from .fx import timing

    return timing()


def _local_speed(owner):
    """本机表现速度（座主 ``Game.speed`` / 客户端 ``RemoteGameView.speed``）。"""

    speed = getattr(owner, "speed", None)
    if not isinstance(speed, (int, float)) or speed <= 0:
        return BASE_SPEED
    return float(speed)


class VisualLedger:
    """还没轮到播的视觉增量（显示值 = 权威值 − 未播增量）。

    例：体力 3 的角色被判闪电命中 3 点。权威体力立刻变成 0，但"−3"这条演出
    还排在判定面板后面。此时 ``hp(player, 0)`` 仍然返回 3——画面不会提前
    跳成濒死 / 阵亡。演出开始时释放增量，显示值自然回到权威值。
    """

    def __init__(self):
        self._hp = {}           # player -> {step: 未播增减}
        self._death = {}        # player -> set(step)

    def reserve_hp(self, player, step, delta):
        if player is None or not delta:
            return
        self._hp.setdefault(player, {})[step] = int(delta)

    def reserve_alive(self, player, step):
        """这一步还没播完之前，画面仍然按"存活"显示。

        **濒死也要预定**：濒死在规则上还没死（可以求桃），而快照可能先把
        ``alive=False`` 发过来——只盯"阵亡演出"的话，玩家会在濒死演出期间
        就看到座位变灰。阵亡演出真正开始播时释放，画面才切到"已阵亡"。
        """

        if player is None:
            return
        self._death.setdefault(player, set()).add(step)

    def release(self, step):
        """这一步开始播（或被丢弃）：它预定的增量不再影响显示。"""

        table = self._hp
        for player in list(table):
            if step in table[player]:
                del table[player][step]
                if not table[player]:
                    del table[player]
        table = self._death
        for player in list(table):
            table[player].discard(step)
            if not table[player]:
                del table[player]

    def hp(self, player, base):
        entry = self._hp.get(player)
        if not entry:
            return base
        return base - sum(entry.values())

    def alive(self, player, base):
        if base:
            return True
        return bool(self._death.get(player))

    def clear(self):
        self._hp.clear()
        self._death.clear()

    def pending_count(self):
        return sum(len(item) for item in self._hp.values()) + \
            sum(len(item) for item in self._death.values())


# ==================================================
# 演出项目
# ==================================================

class Step:
    """一次排出画面的演出。

    ``start`` 在轮到这个项目时调用一次（真正去调 ``Effects`` 的表现入口）；
    ``finished`` 每帧问一次"演完了吗"；``cancel`` 只在被丢弃 / 重置时调用。
    """

    #: 演出种类（报告与测试用）。
    kind = "step"
    #: 关键演出：播放期间压住本机玩家的操作界面（技能提示 / 判定面板）。
    key = False
    #: 播放期间要不要让**操作界面**先别出现（技能提示 = 要；判定面板由
    #: ``JudgeGate`` 单独管——改判窗口开着时必须放行，不能在这里一刀切）。
    holds_ui = False
    #: 最短可读时间（秒，绝对时间，不再按速度倍率缩小）。
    min_duration = 0.0

    def __init__(self):
        self.started = False
        self.elapsed = 0.0
        #: 演出真正开始的那一帧由队列写进来（诊断用）。
        self.skipped = False

    def start(self, effects):                       # pragma: no cover - 抽象
        raise NotImplementedError

    def advance(self, effects, dt):
        """演出进行中的逐帧推进（默认什么都不用推）。"""

    def finished(self, effects):
        return True

    def note(self, name, payload, effects):
        """演出开始**之前**到达的补充信息（判定结果 / 改判）。"""

        return False

    def cancel(self, effects):
        """演出被丢弃 / 队列被清空（默认什么都不用收尾）。"""

    def describe(self):
        return self.kind


class TimedStep(Step):
    """按固定时长占住画面的演出。"""

    def __init__(self):
        super().__init__()
        self.duration = 0.0

    def finished(self, effects):
        return self.elapsed >= self.duration


class JudgeStep(Step):
    """一次判定：整个 ``JudgePanel`` 生命周期就是一次演出。

    判定牌翻开（``JUDGE_REVEALED``）到这里排队，之后到达的改判
    （``JUDGE_REPLACED``）与最终结果（``JUDGE_RESULT``）会通过 ``note``
    落到**对应那一次**判定上——即使前一条演出还没演完也不会串台。
    """

    kind = STEP_JUDGE
    key = True
    min_duration = 0.0

    def __init__(self, result=None):
        super().__init__()
        self.result = result
        self.final = None
        self.replacement = None

    def start(self, effects):
        if self.result is not None:
            effects.judge_begin(self.result)
        if self.replacement is not None:
            effects.judge_replace(self.replacement, getattr(effects, "game", None))
        if self.final is not None:
            effects.judge_finish(self.final)

    def advance(self, effects, dt):
        # 判定面板是这条演出的"身体"：它自己决定什么时候演完。
        effects.judge_panel.update(dt, getattr(effects, "game", None))

    def finished(self, effects):
        return not effects.judge_panel.active

    def note(self, name, payload, effects):
        if name == "replaced":
            self.replacement = payload
            if self.started:
                effects.judge_replace(payload, getattr(effects, "game", None))
            return True
        if name == "result":
            self.final = payload
            if self.started:
                effects.judge_finish(payload)
            return True
        return False

    def cancel(self, effects):
        if self.started:
            effects.judge_panel.cancel()

    def describe(self):
        reason = getattr(self.result, "reason", "")
        return "judge:%s" % (reason or "?")


class SkillStep(TimedStep):
    """技能发动提示（武将卡 + 技能名 + 类型 + 说明）。"""

    kind = STEP_SKILL
    key = True
    holds_ui = True
    min_duration = 1.10

    def __init__(self, player, skill_id, skill_name, targets=(), text="", kind_label=""):
        super().__init__()
        self.player = player
        self.skill_id = str(skill_id or "")
        self.skill_name = str(skill_name or "")
        self.targets = tuple(targets or ())
        self.text = str(text or "")
        self.kind_label = str(kind_label or "")
        self.duration = _timing().story_skill

    def start(self, effects):
        effects.show_skill_banner(
            self.player, self.skill_name, self.targets,
            skill_id=self.skill_id, kind_label=self.kind_label, text=self.text)

    #: 同一个技能连续触发时合并成一条，避免锁定技刷屏。
    def same_as(self, other):
        return (isinstance(other, SkillStep)
                and other.player is self.player
                and other.skill_id == self.skill_id)


class CardStep(TimedStep):
    """出牌：中央横幅 + 指向箭头（"谁对谁使用了什么"）。"""

    kind = STEP_CARD

    def __init__(self, actor, targets, card, *, sequential=False, virtual_name="",
                 skill_name=""):
        super().__init__()
        self.actor = actor
        self.targets = tuple(targets or ())
        self.card = card
        self.sequential = bool(sequential)
        self.virtual_name = str(virtual_name or "")
        self.skill_name = str(skill_name or "")
        self.duration = _timing().story_card

    def start(self, effects):
        effects.show_card_used(self.actor, self.targets, self.card,
                               sequential=self.sequential)


class ResultStep(TimedStep):
    """结算提示条：判定结果 / 延时锦囊生效 / 阶段跳过。

    "跳过出牌阶段"这一类**结论**必须停留到玩家看清，再继续后面的阶段。
    """

    kind = STEP_RESULT

    def __init__(self, text, *, detail="", tone="", kind=STEP_RESULT, min_duration=1.20):
        super().__init__()
        self.text = str(text or "")
        self.detail = str(detail or "")
        self.tone = str(tone or "")
        self.kind = kind
        self.min_duration = float(min_duration)
        self.duration = _timing().story_result
        if kind == STEP_PHASE_SKIP:
            self.duration = _timing().story_phase_skip

    def start(self, effects):
        effects.show_story_banner(self.text, self.detail, self.tone, self.kind)


class DamageStep(TimedStep):
    kind = STEP_DAMAGE

    def __init__(self, player, amount, *, hp_delta=None):
        super().__init__()
        self.player = player
        self.amount = int(amount)
        #: 这一步要"补回去"的体力增减（负数是掉血）。
        self.hp_delta = -self.amount if hp_delta is None else int(hp_delta)
        self.duration = _timing().story_damage

    def start(self, effects):
        effects.show_damage(self.player, self.amount)

    def reserve(self, ledger):
        ledger.reserve_hp(self.player, self, self.hp_delta)


class LoseHpStep(TimedStep):
    kind = STEP_LOSE_HP

    def __init__(self, player, amount):
        super().__init__()
        self.player = player
        self.amount = int(amount)
        self.hp_delta = -self.amount
        self.duration = _timing().story_lose_hp

    def start(self, effects):
        effects.show_lose_hp(self.player, self.amount)

    def reserve(self, ledger):
        ledger.reserve_hp(self.player, self, self.hp_delta)


class RecoverStep(TimedStep):
    kind = STEP_RECOVER

    def __init__(self, player, amount):
        super().__init__()
        self.player = player
        self.amount = int(amount)
        self.hp_delta = self.amount
        self.duration = _timing().story_recover

    def start(self, effects):
        effects.show_recover(self.player, self.amount)

    def reserve(self, ledger):
        ledger.reserve_hp(self.player, self, self.hp_delta)


class DyingStep(TimedStep):
    kind = STEP_DYING

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.duration = _timing().story_dying

    def start(self, effects):
        effects.show_dying(self.player)

    def reserve(self, ledger):
        # 濒死 = 还没死：座位不许提前变灰（真的阵亡演出开始时才释放）。
        ledger.reserve_alive(self.player, self)


class DeathStep(TimedStep):
    kind = STEP_DEATH

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.duration = _timing().story_death

    def start(self, effects):
        effects.show_death(self.player)

    def reserve(self, ledger):
        ledger.reserve_alive(self.player, self)


class ChainStep(TimedStep):
    kind = STEP_CHAIN

    def __init__(self, player, chained):
        super().__init__()
        self.player = player
        self.chained = bool(chained)
        self.duration = _timing().story_chain

    def start(self, effects):
        effects.show_chain(self.player, self.chained)


class TurnStep(TimedStep):
    kind = STEP_TURN

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.duration = _timing().story_turn

    def start(self, effects):
        effects.show_turn_start(self.player)


# ==================================================
# 队列
# ==================================================

class PresentationQueue:
    """按顺序消费表现事件的队列；由 ``Effects`` 每帧推进。"""

    def __init__(self, effects=None):
        self.effects = effects
        self.pending = deque()
        self.current = None
        self.ledger = VisualLedger()
        self.played = 0
        self.dropped = 0
        self.speed_factor = 1.0

    # ---- 生产者 ----

    def submit(self, step):
        """把一条演出排到队尾（返回这条演出，方便调用方留引用）。"""

        if step is None:
            return None
        if self._merge(step):
            return step
        self.pending.append(step)
        self._reserve(step)
        self._trim()
        return step

    def _merge(self, step):
        """连续两条**同一个技能**的提示合并成一条（锁定技不刷屏）。"""

        merge = getattr(step, "same_as", None)
        if merge is None:
            return False
        tail = self.current if self.current is not None else (
            self.pending[-1] if self.pending else None)
        if tail is None or not merge(tail):
            return False
        # 已经在播 / 已在队尾：把这条并进去，时长按"重新计时"延长一点，
        # 而不是再排一条新的。
        tail.duration = max(tail.duration, step.duration)
        tail.elapsed = max(0.0, tail.elapsed - tail.duration * 0.35)
        return True

    def _reserve(self, step):
        reserve = getattr(step, "reserve", None)
        if reserve is not None:
            reserve(self.ledger)

    def _release(self, step):
        self.ledger.release(step)

    def absorb(self, kind, player, amount):
        """吃掉一条**还没开始播**的同类演出（返回是否吃到了）。

        伤害在引擎里就是"失去体力"（``DamageFlow`` 会先应用 ``LoseHpAtom``
        再发伤害事件），所以一次伤害会同时产生"失去体力"和"受到伤害"两条
        表现——同一次结算只该播一次。两条都还排在队里的时候，后到的那条
        把前一条吃掉（与 ``view.presentation`` 的 ``_absorb_hp_lost`` 同源）。
        """

        for item in self.pending:
            if item.kind != kind or getattr(item, "player", None) is not player:
                continue
            if int(getattr(item, "amount", 0) or 0) != int(amount):
                continue
            self.pending.remove(item)
            self._release(item)
            return True
        return False

    def _trim(self):
        """积压过多时丢掉最老的非关键演出（关键演出一定保留）。"""

        while len(self.pending) > MAX_PENDING:
            victim = None
            for item in self.pending:
                if not item.key:
                    victim = item
                    break
            if victim is None:
                victim = self.pending[0]
            self.pending.remove(victim)
            self._release(victim)
            self.dropped += 1

    # ---- 补充信息（判定改判 / 最终结果）----

    def judge_note(self, name, payload):
        """把改判 / 最终结果交给**对应的那一次**判定演出。"""

        step = self.active_judge()
        if step is not None:
            return bool(step.note(name, payload, self.effects))
        # 兜底：队列里已经没有这次判定了（极端积压时判定演出被丢弃）。
        # 直接喂给面板——否则面板会一直停在"等待判定结果"直到兜底超时。
        effects = self.effects
        if effects is None:
            return False
        if name == "result":
            effects.judge_finish(payload)
            return True
        if name == "replaced":
            effects.judge_replace(payload, getattr(effects, "game", None))
            return True
        return False

    def active_judge(self):
        """最近一次还没演完的判定演出（正在播的优先，其次队尾）。"""

        if isinstance(self.current, JudgeStep):
            return self.current
        for item in reversed(self.pending):
            if isinstance(item, JudgeStep):
                return item
        return None

    def has_pending_judge(self):
        return self.active_judge() is not None

    # ---- 消费 ----

    def clear(self):
        for item in list(self.pending):
            self._release(item)
            item.skipped = True
            item.cancel(self.effects)
        self.pending.clear()
        if self.current is not None:
            self._release(self.current)
            self.current.skipped = True
            self.current.cancel(self.effects)
        self.current = None
        self.ledger.clear()

    def reset(self):
        self.clear()
        self.played = 0
        self.dropped = 0

    @property
    def busy(self):
        return self.current is not None or bool(self.pending)

    @property
    def lag(self):
        return len(self.pending)

    @property
    def holding(self):
        """正在播"关键演出"（判定面板 / 技能提示）：操作界面先让路。"""

        return bool(self.current is not None and self.current.key)

    def driving_judge(self):
        """队列此刻是不是在替判定面板计时（避免外面重复推进它）。"""

        return isinstance(self.current, JudgeStep)

    def holds_interaction(self):
        """现在要不要让**操作界面**先别出现（技能提示还没播完）。

        判定面板不在这里判：它开着改判窗口时必须放行，那个判据在
        ``JudgeGate.allows_local_input`` 里（见 ``Effects.interaction_hold``）。
        """

        return bool(self.current is not None and self.current.holds_ui)

    def speed_for(self, owner):
        speed = _local_speed(owner)
        factor = speed / BASE_SPEED
        return max(MIN_SPEED_FACTOR, min(MAX_SPEED_FACTOR, factor))

    def update(self, dt, owner=None):
        """推进队列；``owner`` 提供本机速度（座主 Game / 客户端视图）。"""

        if owner is None:
            owner = getattr(self.effects, "game", None)
        self.speed_factor = self.speed_for(owner)
        step_dt = max(0.0, float(dt)) * self.speed_factor

        if self.current is None:
            self._start_next()
        if self.current is None:
            return
        self.current.elapsed += step_dt
        self.current.advance(self.effects, step_dt)
        if self.current.finished(self.effects):
            done = self.current
            self._release(done)
            self.current = None
            self.played += 1
            # 同一帧内继续消费"零时长 / 已完成"的演出：瞬时演出不会拖慢画面。
            self._start_next()

    def _start_next(self):
        while self.pending:
            step = self.pending.popleft()
            self.current = step
            step.started = True
            self._release(step)
            try:
                step.start(self.effects)
            except Exception:                        # pragma: no cover - 兜底
                # 单条演出出错绝不能把整条队列卡死。
                if self.effects is not None:
                    errors = getattr(self.effects, "story_errors", None)
                    if errors is not None:
                        import traceback

                        errors.append(traceback.format_exc(limit=3))
                        del errors[:-6]
                self.current = None
                self.played += 1
                continue
            if not step.finished(self.effects):
                return
            # 立刻结束的演出（数据不全一类）不占画面，继续下一条。
            self._release(step)
            self.current = None
            self.played += 1

    # ---- 诊断 ----

    def describe(self, limit=6):
        """当前画面顺序（报告 / 测试取证用）。"""

        items = []
        if self.current is not None:
            items.append("▶" + self.current.describe())
        for step in list(self.pending)[:limit]:
            items.append(step.describe())
        return items

    def kinds(self):
        items = [step.kind for step in self.pending]
        if self.current is not None:
            items.insert(0, self.current.kind)
        return items
