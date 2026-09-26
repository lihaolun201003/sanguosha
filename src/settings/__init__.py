"""本机偏好层：与"这一局怎么打"无关的长期配置。

目前只有 ``preferences``（我的武将池）。它刻意与引擎解耦：规则层不知道
偏好存在，偏好的合法性与它怎么参与候选生成都在
``src/game/generals/draft.py`` 里说明。
"""

from .preferences import Preferences, shared, set_shared

__all__ = ["Preferences", "shared", "set_shared"]
