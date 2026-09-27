"""一条命令打出"可以发给别人"的单文件 EXE（Windows / PyInstaller onefile）。

    python tools/build_release.py            # release：dist/Sanguosha.exe（无控制台）
    python tools/build_release.py --debug    # debug：dist/Sanguosha-debug.exe（保留控制台）

产物只有一个文件：``dist/Sanguosha.exe``。素材（``assets/``，约 50MB）会全部
内嵌，所以体积在 60–80MB 之间属正常。

构建期与运行期的分工：

    .spec          决定"收什么"（assets 整目录 + src 全量子模块）
    本脚本         决定"怎么调 PyInstaller"（dist/work 路径、debug 开关、版本号）

版本号取 ``git rev-parse --short HEAD`` + 日期；拿不到 git 就用日期。它会被写进
bundle 的 ``_version.txt``，崩溃日志的抬头会带上它——舍友发回来的 crash.log
能直接对上构建。
"""

import argparse
import datetime
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "Sanguosha.spec")


def _version():
    stamp = datetime.datetime.now().strftime("%Y%m%d")
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
            stderr=subprocess.DEVNULL)
        sha = out.decode("utf-8", "replace").strip()
    except Exception:                                    # noqa: BLE001
        sha = "nogit"
    return "%s-%s" % (stamp, sha or "nogit")


def _python():
    """优先用项目虚拟环境（依赖装在那里）。"""

    exe = sys.executable
    venv = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    if os.path.exists(venv) and os.path.abspath(exe) != os.path.abspath(venv):
        return venv
    return exe


def main():
    parser = argparse.ArgumentParser(description="打包三国杀单文件 EXE")
    parser.add_argument("--debug", action="store_true",
                        help="保留控制台（排错用），产物名为 Sanguosha-debug.exe")
    parser.add_argument("--keep-work", action="store_true",
                        help="保留 build/ 中间目录（默认清掉，只留 dist 里的 exe）")
    args = parser.parse_args()

    python = _python()
    env = dict(os.environ)
    env["SGS_PROJECT_ROOT"] = ROOT
    env["SGS_BUILD_DEBUG"] = "1" if args.debug else "0"
    env["SGS_VERSION"] = _version()
    env["PYTHONIOENCODING"] = "utf-8"

    if not os.path.exists(SPEC):
        print("找不到 spec：", SPEC)
        return 1

    # 打包前先确认依赖在（否则报错信息会很难读）。
    check = subprocess.run(
        [python, "-c", "import PyInstaller, pygame; print(PyInstaller.__version__)"],
        cwd=ROOT, capture_output=True, text=True)
    if check.returncode != 0:
        print("缺少 PyInstaller 或 pygame，请先安装：")
        print("  %s -m pip install pyinstaller pygame" % python)
        print(check.stderr.strip()[-500:])
        return 2
    print("PyInstaller %s · 版本 %s · %s" % (
        check.stdout.strip(), env["SGS_VERSION"],
        "debug（有控制台）" if args.debug else "release（无控制台）"))

    dist = os.path.join(ROOT, "dist")
    work = os.path.join(ROOT, "build")
    cmd = [
        python, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath", dist,
        "--workpath", work,
        SPEC,               # 给了 spec 时不能再传 --specpath
    ]
    print("开始打包…（素材约 50MB，首次可能要几分钟）")
    code = subprocess.call(cmd, cwd=ROOT, env=env)
    if code != 0:
        print("打包失败，退出码", code)
        return code

    name = "Sanguosha-debug.exe" if args.debug else "Sanguosha.exe"
    target = os.path.join(dist, name)
    if not os.path.exists(target):
        print("打包结束但没看到产物：", target)
        return 3

    size = os.path.getsize(target) / (1024.0 * 1024.0)
    print("\n完成：%s（%.1f MB）" % (target, size))

    if not args.keep_work:
        shutil.rmtree(work, ignore_errors=True)
        print("已清理中间目录 build/")

    # dist 里只应该有那一个 exe：多出来的东西说明打成了 onedir。
    others = [item for item in os.listdir(dist) if item != name]
    if others:
        print("注意：dist 里还有别的东西（不该出现）：", others)
    else:
        print("dist/ 里只有这一个文件 —— 确认是 onefile。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
