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
import importlib.util
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


def _load_module(name):
    """按名字或**绝对路径**取到脚本模块。

    三种来源，都服务于同一件事——"把一段驱动脚本挂到真实主循环上"：

    * ``src.`` 前缀：包内脚本（``src/selftest.py``），跟着 bundle 一起走，
      所以"独立目录里只有 exe"时也能自检；
    * ``.py`` 结尾的路径：外部脚本文件（本机做**两个 EXE 同机联机实测**时用
      ``tools/runtime_capture.py`` 这类驱动，EXE 自己不带 tools/）；
    * 其余：``tools/`` 下的模块名，源码运行时的既有用法。
    """

    if name.startswith("src.") or name in sys.modules:
        return importlib.import_module(name)
    if name.endswith(".py") or os.path.sep in name:
        path = os.path.abspath(name)
        if not os.path.exists(path):
            return None
        spec = importlib.util.spec_from_file_location("sgs_runtime_script", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    return importlib.import_module(name)


def load_runtime_hook():
    """按环境变量加载运行期脚本；没有指定时返回 ``None``。

    两种来源：

    * ``tools/`` 下的脚本（用模块名，例如 ``handplay_scene``）——源码运行时用；
      ``tools/`` 不在 sys.path 时先插进去。
    * **包内**脚本（名字带 ``src.`` 前缀，例如 ``src.selftest``）——打包成 EXE
      之后 ``tools/`` 根本不存在，但包内脚本跟着 ``src`` 一起进了 bundle，
      所以"独立目录里只有 exe"时仍然能做自动化验证。
    """

    name = (os.environ.get(HOOK_ENV) or "").strip()
    if not name:
        return None
    module = _load_module(name)
    if module is None:
        return None
    hook = module.Hook()
    hook.bind_environment(dict(os.environ))
    return hook
