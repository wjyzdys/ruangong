# -*- coding: utf-8 -*-
"""统计文件夹内全部 Python 模块的导入运行时长。

用法:
    python folder_runtime.py [目标文件夹]      # 默认 src_python

做法:在同一个进程里依次 import 目标文件夹下的每一个 .py 文件
(按 `.py` 路径还原成模块名,`__init__.py` 还原成包名),
import 前把该模块从 sys.modules 里清掉,保证每次都真正执行一遍。
import 失败的文件跳过,单独记入 skipped 清单,不计入总时长。
"""

import contextlib
import importlib
import io
import json
import os
import sys
import time
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))


def find_source_files(root):
    """返回目标文件夹下全部 .py 文件的相对路径(用 / 分隔),按路径排序。"""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def to_module_name(rel_path):
    """distutils/__init__.py -> distutils; email/mime/text.py -> email.mime.text"""
    parts = rel_path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def evict(module_name):
    """把目标模块自身从 sys.modules 里清掉,使下一次 import 真正重新执行。"""
    sys.modules.pop(module_name, None)
    importlib.invalidate_caches()


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "src_python")
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        print("目标文件夹不存在: %s" % root)
        return 1

    files = find_source_files(root)

    # 让目标文件夹优先于标准库解析,否则同名包(distutils / email / encodings)
    # 会直接命中标准库里的实现,根本没跑到目标文件夹的代码。
    sys.path.insert(0, root)

    results = []
    skipped = []
    total_success_s = 0.0

    wall_start = time.perf_counter()
    for rel in files:
        mod = to_module_name(rel)
        evict(mod)

        t0 = time.perf_counter()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    importlib.import_module(mod)
        except BaseException as exc:  # 语法错误/缺依赖/运行期异常都算失败
            elapsed = time.perf_counter() - t0
            skipped.append({
                "file": rel,
                "module": mod,
                "error": "%s: %s" % (type(exc).__name__, exc),
                "elapsed_ms": round(elapsed * 1000, 4),
            })
            continue

        elapsed = time.perf_counter() - t0
        total_success_s += elapsed
        results.append({"file": rel, "module": mod, "elapsed_ms": round(elapsed * 1000, 4)})

    wall_total_s = time.perf_counter() - wall_start

    out_dir = os.path.join(HERE, "results")
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "folder_runtime.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "root": root,
            "total_files": len(files),
            "succeeded": len(results),
            "skipped": len(skipped),
            "total_success_ms": round(total_success_s * 1000, 4),
            "wall_clock_ms": round(wall_total_s * 1000, 4),
            "per_file": results,
            "skipped_files": skipped,
        }, fh, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "skipped.txt"), "w", encoding="utf-8") as fh:
        for item in skipped:
            fh.write("%s\t%s\n" % (item["file"], item["error"]))

    print("目标文件夹 : %s" % root)
    print("扫描到文件 : %d 个 .py" % len(files))
    print("成功执行   : %d 个" % len(results))
    print("跳过(失败): %d 个" % len(skipped))
    print("-" * 46)
    print("代码运行总时长(成功文件累加): %.4f ms  = %.6f s" % (
        total_success_s * 1000, total_success_s))
    print("整体墙钟耗时(含失败与被跳过部分): %.4f ms  = %.6f s" % (
        wall_total_s * 1000, wall_total_s))
    print("-" * 46)
    print("明细已写出: results/folder_runtime.json")
    if skipped:
        print("失败清单  : results/skipped.txt")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
