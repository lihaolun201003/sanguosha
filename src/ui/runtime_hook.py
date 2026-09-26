"""运行期脚本挂钩：让验证工具能挂在**真实主循环**上（Phase 11.4.2）。

为什么需要它：UI 一致性只能由"玩家真正看到的那个画面"来验收。此前的一致性
工具自己搭了一套渲染循环（自己 set_mode、自己 new Renderer、自己调 draw），
于是"测试通过"与"真人看到的界面"之间隔着一层谁也没验证过的胶水。

这里换一种做法：``main.py`` 正常启动，唯一的变化是——如果环境变量
``SGS_RUNTIME_SCRIPT`` 指定了一个 ``tools/`` 下的模块，主循环每帧结束后把
自己真实用到的那一组对象（screen / game / renderer / 各场景）交给它，
由脚本驱动操作与截图。

没有设置这个环境变量时 ``load_runtime_hook()`` 返回 ``None``，主循环一行
都不受影响。
"""

import importlib
import os
import sys

#: 环境变量：要加载的运行期脚本模块名（相对 ``tools/``）。
HOOK_ENV = "SGS_RUNTIME_SCRIPT"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS_DIR = os.path.join(_ROOT, "tools")


class RuntimeContext:
    """主循环交给脚本的一整套真实对象（全部是 main.py 正在用的那一个）。"""

    __slots__ = ("screen", "game", "renderer", "start_menu", "general_select",
                 "identity_reveal", "choice_overlay", "lan_scene", "resync",
                 "settings", "hud", "preferences", "favorite_generals")

    def __init__(self, **values):
        for key in self.__slots__:
            setattr(self, key, values.get(key))


def load_runtime_hook():
    """按环境变量加载运行期脚本；没有指定时返回 ``None``。"""

    name = (os.environ.get(HOOK_ENV) or "").strip()
    if not name:
        return None
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    module = importlib.import_module(name)
    hook = module.Hook()
    hook.bind_environment(dict(os.environ))
    return hook
