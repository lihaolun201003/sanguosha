"""候选武将生成（General Draft）——"本局给这名玩家抽哪几个武将"。

# 四个互不相干的概念（不要混）

    GeneralRegistry       有哪些武将（规则数据）
    FavoriteGeneralPool   我喜欢哪些武将（本机长期偏好，src/settings）
    GeneralDraft          本局抽到了哪几个候选（业务状态，一次生成）
    SelectedGeneral       本局最终选了谁（对局状态）

本模块只管第三件。**随机只发生在这里**：不在 Renderer、不在 draw() 里，
因此 resize / hover / 重绘 / 快照更新都不会让候选变化——候选是"一局一份"的
业务结果，不是每帧算出来的表现。

# 池子怎么来

``resolve_pool(favorites, available)``：

* 偏好 ∩ 当前模式可用武将，且 ≥ ``MIN_POOL`` → 用偏好池；
* 否则（没配置过 / 配置里大部分武将已被移除 / 不足 5 人）→ 退回**当前模式
  的全部可用武将**，保证任何情况下都开得了局。

``clean_pool`` 负责把"已经不存在的 id"过滤掉：它只认 ``GeneralRegistry``，
所以旧配置里留着被删掉的武将时，只是安静地少一条，不会崩。
"""

from .registry import GeneralRegistry  # noqa: F401  (类型说明用)

#: 本阶段每局抽几个候选。
DRAFT_SIZE = 5

#: 武将池至少要有这么多人才算"可用"。低于它一律退回全部可用武将——
#: 否则会出现"池子里只剩 2 个人，每局候选都不够"的死局。
MIN_POOL = DRAFT_SIZE


def clean_pool(ids, registry, available_ids=None):
    """把一串 id 规整成"真实存在且当前可用"的去重列表（保序）。

    ``available_ids`` 给定时，还会要求 id 在这一局真正可用的集合里
    （模式限制、未实现、测试专用武将都已经在那份集合里被过滤过）。
    """

    allowed = None if available_ids is None else {str(item) for item in available_ids}
    result = []
    for value in ids or ():
        general_id = str(value or "").strip()
        if not general_id or general_id in result:
            continue
        if registry is not None and general_id not in registry:
            continue
        if allowed is not None and general_id not in allowed:
            continue
        result.append(general_id)
    return tuple(result)


def resolve_pool(favorites, available_ids, *, registry=None, minimum=MIN_POOL):
    """真人候选池：偏好优先，不足 ``minimum`` 时退回全部可用武将。

    返回 ``(pool, used_favorites)``——``used_favorites`` 说明这次到底用了
    偏好池还是回退池（界面提示与报告都靠它）。
    """

    available = tuple(str(item) for item in (available_ids or ()) if item)
    preferred = clean_pool(favorites, registry, available)
    if len(preferred) >= max(1, int(minimum)):
        return preferred, True
    return available, False


def roll_candidates(pool, rng, size=DRAFT_SIZE):
    """从池子里**不重复**抽取 ``size`` 个候选（顺序随机）。

    池子比 ``size`` 小就全给（不会重复、不会造牌）；池子为空返回空元组。
    """

    ids = [str(item) for item in (pool or ()) if item]
    if not ids:
        return ()
    count = max(1, min(int(size), len(ids)))
    if rng is None:
        picked = ids[:count]
    else:
        picked = list(ids)
        rng.shuffle(picked)
        picked = picked[:count]
    return tuple(picked)


class GeneralDraft:
    """一名玩家本局的候选武将（生成一次，之后只读）。

    ``roll`` 只会真正抽一次：重复调用（重绘、重连、场景重建）返回同一份结果，
    这就是"本局候选不会因为界面刷新而变化"的落实点。
    """

    def __init__(self, pool=(), size=DRAFT_SIZE):
        self.size = max(1, int(size))
        self.pool = tuple(str(item) for item in (pool or ()) if item)
        self.candidates = ()
        self.used_favorites = False

    # ---- 生成 ----

    def roll(self, pool, rng, *, used_favorites=False):
        """抽一次候选；已经抽过就直接返回原结果。"""

        if self.candidates:
            return self.candidates
        self.pool = tuple(str(item) for item in (pool or ()) if item)
        self.used_favorites = bool(used_favorites)
        self.candidates = roll_candidates(self.pool, rng, self.size)
        return self.candidates

    def reset(self):
        self.candidates = ()
        self.used_favorites = False
        return self

    # ---- 只读查询 ----

    @property
    def rolled(self):
        return bool(self.candidates)

    def contains(self, general_id):
        return str(general_id) in self.candidates

    def index_of(self, general_id):
        try:
            return self.candidates.index(str(general_id))
        except ValueError:
            return None

    def __len__(self):
        return len(self.candidates)

    def __iter__(self):
        return iter(self.candidates)
