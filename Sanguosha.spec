# -*- mode: python ; coding: utf-8 -*-
"""三国杀 · Windows 单文件发布（PyInstaller onefile）。

不要直接改这个文件里的路径逻辑——统一走 ``tools/build_release.py``：

    python tools/build_release.py            # release：dist/Sanguosha.exe（无控制台）
    python tools/build_release.py --debug    # debug：dist/Sanguosha-debug.exe（保留控制台）

两种构建共用同一份 spec，靠环境变量区分：

    SGS_PROJECT_ROOT   项目根（build 脚本传进来，避免依赖 cwd）
    SGS_BUILD_DEBUG    "1" → 保留控制台 + 名字带 -debug
    SGS_VERSION        版本字符串，写进 bundle 的 ``_version.txt``（crash.log 抬头用）

# 为什么 datas 里放的是整个 ``assets/``

素材是**运行时才读**的（资产注册表按稳定 id 去磁盘找文件），PyInstaller 的
静态分析看不见它们，必须显式收集。少收一个子目录的表现是"我这儿有图、
别人那儿缺图"，所以整目录收。

# 为什么 hiddenimports 用 collect_submodules("src")

技能 / 武将 / 卡牌效果按包聚合导入（``from .expansions import EXPANSION_SKILLS``
这类），个别地方还有 ``__import__`` 形式的动态导入。全量收集 ``src`` 下的
子模块最省心，代价只是几十 KB 的 .pyc。``src/__init__.py`` 是空的，所以
打包期导入它们不会去开显示设备。
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# 打包期导入 src 子模块时不要真的初始化显示 / 音频设备。
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

DEBUG = os.environ.get("SGS_BUILD_DEBUG") == "1"
VERSION = (os.environ.get("SGS_VERSION") or "dev").strip()
ROOT = os.path.abspath(os.environ.get("SGS_PROJECT_ROOT") or os.getcwd())
APP_NAME = "Sanguosha-debug" if DEBUG else "Sanguosha"

# ==================================================
# 只读资源
# ==================================================

datas = [(os.path.join(ROOT, "assets"), "assets")]

# 版本标记：crash.log 的抬头读它，方便舍友发回来的日志能对上构建。
# 文件名必须与 src/crash.py 读的那个一致（_version.txt），否则崩溃日志里只会
# 显示 "版本: dev"，排错时对不上是哪一次构建。
_version_file = os.path.join(os.environ.get("TEMP") or ROOT, "_version.txt")
with open(_version_file, "w", encoding="utf-8") as handle:
    handle.write(VERSION)
datas.append((_version_file, "."))  # → bundle 根下的 _version.txt

# ==================================================
# 隐藏导入
# ==================================================

hiddenimports = collect_submodules("src") + ["pygame", "pygame.freetype"]

#: 明确排除：开发期依赖与从没被运行路径用到的第三方。写在这里是为了防止
#: 某次间接导入把它们悄悄拉进发布包（体积翻几倍且毫无用处）。
EXCLUDES = [
    "numpy", "matplotlib", "PIL", "pandas", "scipy", "IPython",
    "pytest", "unittest", "doctest", "pydoc",
    "setuptools", "pip", "pkg_resources", "wheel",
    "tkinter", "lib2to3", "test",
]

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# onefile：binaries 与 datas 都进这一个 exe（不给 exclude_binaries）。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    # release 版没有控制台：双击只出现游戏窗口（异常进 crash.log）。
    console=DEBUG,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
