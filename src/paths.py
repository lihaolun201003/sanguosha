"""统一路径：只读资源在哪、可写数据写哪（源码运行与打包 EXE 共用一套判据）。

# 为什么要有这个模块

PyInstaller ``--onefile`` 启动时把内部资源解压到一个**临时目录**
（``sys._MEIPASS``），进程退出即删。于是同一个"仓库根目录"在两种运行方式下
含义完全不同：

    源码运行        C:\\...\\sanguosha -zcode\\               （稳定，可写）
    EXE 运行        C:\\Users\\x\\AppData\\Local\\Temp\\_MEIxxxx\\   （临时，随时消失）

把**用户配置**写进后者，表现就是"设置好了、关掉再开全没了"——而且不会报错，
最难查的一类问题。所以这里把两类路径分开：

* :func:`bundle_root` / :func:`assets_root` —— **只读**资源（素材、数据文件）。
  开发时是仓库根，打包后是 ``_MEIPASS``；两者都能读到 ``assets/``。
* :func:`user_data_dir` / :func:`user_file` / :func:`log_dir` —— **可写**数据
  （偏好、日志）。开发时仍是仓库根（不打扰现有工具与测试），打包后固定到
  ``%LOCALAPPDATA%\\Sanguosha\\``，与 EXE 放哪、临时目录叫什么名字都无关。

全项目只在这里回答这两个问题：别处不要再自己算 ``__file__`` 的相对层级。
"""

import os
import sys

#: 应用名：可写数据目录与日志目录都用它。
APP_NAME = "Sanguosha"

#: 环境变量：把可写数据目录指到别处（验收脚本 / 探针用，别动玩家的真实配置）。
USER_DATA_ENV = "SGS_USER_DATA"


def is_frozen():
    """现在是不是从打包后的 EXE 里跑的。"""

    return bool(getattr(sys, "frozen", False))


def bundle_root():
    """**只读**资源根目录。

    打包后是 ``sys._MEIPASS``（一次性解压目录），源码运行时是仓库根。
    两个位置下面都有 ``assets/``，所以 ``assets_root()`` 在两边都成立。
    """

    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return meipass
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))     # src/
    return os.path.dirname(here)                          # 仓库根


def assets_root(*parts):
    """``assets/`` 下的某个路径（不存在时返回计算出的路径，不抛异常）。"""

    return os.path.join(bundle_root(), "assets", *parts)


def user_data_dir(*parts):
    """**可写**数据目录（需要时会创建）。

    * 打包后：``%LOCALAPPDATA%\\Sanguosha\\``（拿不到 LOCALAPPDATA 时退回
      用户主目录下的 ``.Sanguosha``）；
    * 源码运行：仓库根目录（保持既有行为：``user_preferences.json`` 仍在根目录）；
    * 两者都可被 ``SGS_USER_DATA`` 覆盖。

    创建失败（只读介质 / 权限）时返回**可以尝试写入**的路径，由调用方在真正
    写盘时处理错误——路径计算本身绝不抛异常，否则游戏起不来。
    """

    override = (os.environ.get(USER_DATA_ENV) or "").strip()
    if override:
        base = override
    elif is_frozen():
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        if not local:
            local = os.path.join(os.path.expanduser("~"), ".Sanguosha")
        base = os.path.join(local, APP_NAME)
    else:
        base = bundle_root()
    path = os.path.join(base, *parts) if parts else base
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


def user_file(name):
    """可写目录下的一个文件（偏好、导出文件…）。"""

    return os.path.join(user_data_dir(), name)


def log_dir():
    """日志目录（``crash.log`` 写在这里）。"""

    return user_data_dir("logs")


def describe():
    """一行诊断（F1 面板 / 报告 / 崩溃日志抬头都用它）。"""

    return "frozen=%s bundle=%s userdata=%s" % (
        is_frozen(), bundle_root(), user_data_dir())
