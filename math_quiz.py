#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小学四则运算出题程序
=========================================================

功能
----
1. 随机生成加、减、乘、除四则运算题，支持括号嵌套；
2. 全程保证「结果是整数」——除法必定整除，减法不会出现负数，
   中间步骤也全是整数，适合小学生练习；
3. 两种模式：
     * quiz   交互练习：逐题作答、即时判分、统计正确率与错题
     * paper  打印试卷：排版成练习卷，答案可单独成页或另存文件
4. 可调参数：题目数量、数字范围、运算符、嵌套深度、括号比例、随机种子等。

用法示例
--------
    python math_quiz.py                                  # 交互练习 10 题
    python math_quiz.py paper -n 20                      # 生成 20 题试卷
    python math_quiz.py paper -n 20 --show-answers       # 试卷附带答案
    python math_quiz.py paper -n 30 -o 试卷.txt --answer-file 答案.txt
    python math_quiz.py quiz -n 15 --max-num 50 --no-div
    python math_quiz.py paper --brackets-heavy --depth 3 # 多括号、多层嵌套
    python math_quiz.py quiz --seed 42                   # 固定随机种子，可复现

提示：要让题目出现括号，请把 --depth 设为 2 或 3（默认即 2）。
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

def setup_console() -> None:
    """Windows 控制台 / 重定向时统一按 UTF-8 输出，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


# ======================================================================
# 一、表达式树
# ======================================================================

PREC = {"+": 1, "-": 1, "*": 2, "/": 2}          # 运算符优先级
SYMBOL = {"+": "+", "-": "-", "*": "×", "/": "÷"}  # 输出时用的符号


@dataclass
class Node:
    """表达式树节点：op 为 None 表示数字叶子，否则表示二元运算。"""

    op: Optional[str]
    val: int
    left: Optional["Node"] = None
    right: Optional["Node"] = None


def render(node: Node) -> str:
    """把表达式树渲染成字符串，只在数学上必要时添加括号。"""

    def rec(n: Node, parent_prec: int, is_right: bool, parent_op: Optional[str]) -> str:
        if n.op is None:
            return str(n.val)
        p = PREC[n.op]
        left = rec(n.left, p, False, n.op)      # type: ignore[arg-type]
        right = rec(n.right, p, True, n.op)     # type: ignore[arg-type]
        text = f"{left} {SYMBOL[n.op]} {right}"
        need = p < parent_prec or (
            p == parent_prec and is_right and parent_op in ("-", "/")
        )
        return f"({text})" if need else text

    return rec(node, 0, False, None)


def tree_depth(node: Node) -> int:
    """统计表达式树的运算层数。"""
    if node.op is None:
        return 0
    return 1 + max(tree_depth(node.left), tree_depth(node.right))  # type: ignore[arg-type]


# ======================================================================
# 二、出题参数与题目
# ======================================================================


@dataclass
class QuizConfig:
    min_num: int = 1            # 数字下限
    max_num: int = 20           # 数字上限
    ops: str = "+-*/"           # 允许的运算符
    max_depth: int = 2          # 运算嵌套层数
    max_result: int = 100       # 每一步结果的允许上限
    paren_ratio: float = 0.5    # 希望带括号的题目占比
    allow_zero: bool = False    # 是否允许结果为 0 的减法


@dataclass
class Question:
    text: str                   # 题干，例如 "12 + 5 × 3"
    answer: int                 # 正确答案
    depth: int                  # 运算层数


# ======================================================================
# 三、题目生成
# ======================================================================


def _leaf(rng: random.Random, cfg: QuizConfig, num_max: Optional[int] = None) -> Node:
    hi = cfg.max_num if num_max is None else max(cfg.min_num, num_max)
    return Node(None, rng.randint(cfg.min_num, hi))


def _try_op(
    rng: random.Random, cfg: QuizConfig, op: str, depth: int, num_max: Optional[int]
) -> Optional[Node]:
    """尝试按指定运算符构造一个满足约束的子树，失败返回 None。"""
    tries = 15

    if op == "+":
        for _ in range(tries):
            left = make_expr(rng, cfg, depth - 1, num_max)
            right = make_expr(rng, cfg, depth - 1, num_max)
            value = left.val + right.val
            if value <= cfg.max_result:
                return Node("+", value, left, right)

    elif op == "-":
        for _ in range(tries):
            left = make_expr(rng, cfg, depth - 1, num_max)
            right = make_expr(rng, cfg, depth - 1, num_max)
            if left.val < right.val:            # 保证不被减成负数
                left, right = right, left
            value = left.val - right.val
            if value > 0 or (value == 0 and cfg.allow_zero):
                return Node("-", value, left, right)

    elif op == "*":
        # 乘法增长快，用更小的数字范围，避免结果爆掉
        small = min(cfg.max_num, max(2, math.isqrt(max(cfg.max_result, 4))))
        for _ in range(tries):
            left = make_expr(rng, cfg, depth - 1, small)
            right = make_expr(rng, cfg, depth - 1, small)
            value = left.val * right.val
            if left.val >= 2 and right.val >= 2 and value <= cfg.max_result:
                return Node("*", value, left, right)

    elif op == "/":
        for _ in range(tries):
            left = make_expr(rng, cfg, depth - 1, num_max)
            if left.val < 4:
                continue
            # 从被除数的真因子中挑一个当除数，天然的整除
            factors = [f for f in range(2, left.val) if left.val % f == 0]
            if not factors:
                continue
            f = rng.choice(factors)
            return Node("/", left.val // f, left, Node(None, f))

    return None


def make_expr(
    rng: random.Random, cfg: QuizConfig, depth: int, num_max: Optional[int] = None
) -> Node:
    """递归生成表达式树。depth 为剩余可用的运算层数。"""
    if depth <= 0:
        return _leaf(rng, cfg, num_max)

    ops = list(cfg.ops)
    rng.shuffle(ops)
    for op in ops:
        node = _try_op(rng, cfg, op, depth, num_max)
        if node is not None:
            return node

    # 兜底：所有运算符都没构造成功时，退化成最简单的一层加法
    left = _leaf(rng, cfg, num_max)
    right = _leaf(rng, cfg, num_max)
    return Node("+", left.val + right.val, left, right)


def generate_questions(count: int, cfg: QuizConfig, rng: random.Random) -> List[Question]:
    """生成 count 道互不重复的题目。"""
    questions: List[Question] = []
    used: set[str] = set()
    wants = [rng.random() < cfg.paren_ratio for _ in range(count)]

    for i in range(count):
        picked: Optional[Question] = None
        for _ in range(80):                     # 拒绝采样，直到满足要求
            node = make_expr(rng, cfg, cfg.max_depth)
            if node.op is None:                 # 只有一个数字，不成题
                continue
            text = render(node)
            if wants[i] and "(" not in text:    # 这题要求带括号
                continue
            if text in used:                    # 去重
                continue
            picked = Question(text, node.val, tree_depth(node))
            break

        if picked is None:                      # 实在挑不出来就放宽条件
            left = _leaf(rng, cfg)
            right = _leaf(rng, cfg)
            node = Node("+", left.val + right.val, left, right)
            picked = Question(render(node), node.val, 1)

        used.add(picked.text)
        questions.append(picked)

    return questions


# ======================================================================
# 四、试卷排版与交互练习
# ======================================================================


def build_paper(
    questions: List[Question],
    columns: int = 2,
    title: str = "小学四则运算练习",
) -> Tuple[str, str]:
    """返回 (试卷正文, 参考答案) 两段文本。"""
    total = len(questions)
    width = 78
    idx_w = len(str(total)) + 1

    lines: List[str] = []
    lines.append("=" * width)
    lines.append(f"{title}（共 {total} 题）".center(width))
    lines.append("姓名：__________    日期：__________    用时：______    得分：______")
    lines.append("=" * width)
    lines.append("")

    def label(index: int, q: Question) -> str:
        return f"{index:>{idx_w}}) {q.text} ="

    def layout(count: int) -> Tuple[int, List[List[Question]], int]:
        """按 count 列排版，返回（每列题数, 各列题目, 单元格宽度）。"""
        per = math.ceil(total / count)
        groups = [questions[i * per:(i + 1) * per] for i in range(count)]
        groups = [g for g in groups if g]
        cw = 0
        for ci, g in enumerate(groups):
            for r, q in enumerate(g):
                cw = max(cw, len(label(ci * per + r + 1, q)))
        return per, groups, cw + 6

    # 题干太长时自动减少列数，保证不超出页面宽度
    columns = max(1, min(columns, 4))
    while True:
        per_col, cols, cell_width = layout(columns)
        if columns == 1 or cell_width * columns <= 104:
            break
        columns -= 1

    for r in range(per_col):
        row = ""
        for ci, col in enumerate(cols):
            if r < len(col):
                row += label(ci * per_col + r + 1, col[r]).ljust(cell_width)
        lines.append(row.rstrip())

    paper = "\n".join(lines)

    answer_lines = ["-" * width, "参考答案".center(width), "-" * width]
    per_line = 6
    for i in range(0, total, per_line):
        chunk = ""
        for j in range(min(per_line, total - i)):
            k = i + j
            chunk += f"{k + 1:>{idx_w}}) {questions[k].answer}".ljust(idx_w + 10)
        answer_lines.append(chunk.rstrip())
    answers = "\n".join(answer_lines)

    return paper, answers


def run_quiz(questions: List[Question]) -> None:
    """交互练习：逐题作答，即时判分。"""
    total = len(questions)
    print("=" * 56)
    print("口算练习开始啦！输入答案后按回车；输入 q 可以随时退出。".center(46))
    print("=" * 56)
    print()

    start = time.perf_counter()
    correct = 0
    answered = 0
    wrong: List[Tuple[int, Question, Optional[int]]] = []
    stopped = False

    for i, q in enumerate(questions, 1):
        if stopped:
            break
        print(f"({i}/{total})  {q.text} = ", end="", flush=True)
        while True:
            try:
                raw = input().strip()
            except (EOFError, KeyboardInterrupt):
                print()
                raw = "q"

            if raw.lower() in ("q", "quit", "exit", "退出"):
                stopped = True
                print("  已提前结束本次练习。")
                break
            if raw == "":
                continue
            try:
                user_answer = int(raw)
            except ValueError:
                print("  请输入一个整数哦（或输入 q 退出）：", end="", flush=True)
                continue

            answered += 1
            if user_answer == q.answer:
                correct += 1
                print("  ✓ 太棒了，正确！")
            else:
                wrong.append((i, q, user_answer))
                print(f"  ✗ 再想一想～ 正确答案是 {q.answer}")
            break

    elapsed = time.perf_counter() - start
    rate = correct / answered * 100 if answered else 0.0

    print()
    print("=" * 56)
    print("练习结果".center(48))
    print("=" * 56)
    print(f"  共出题 {total} 道，作答 {answered} 道，答对 {correct} 道")
    if answered:
        print(f"  正确率：{rate:.1f}%")
    print(f"  用时：{elapsed:.0f} 秒")

    if answered:
        if rate >= 95:
            praise = "全对或接近全对，计算小达人！"
        elif rate >= 80:
            praise = "表现不错，再细心一点就更好啦！"
        elif rate >= 60:
            praise = "及格啦，把错题再算一遍会进步更快。"
        else:
            praise = "别着急，慢慢来，多练几遍一定能掌握。"
        print(f"  点评：{praise}")

    if wrong:
        print()
        print("  错题回顾：")
        for idx, q, user_answer in wrong:
            shown = "未作答" if user_answer is None else str(user_answer)
            print(f"    {idx:>3}) {q.text} = {q.answer}    你答的是：{shown}")
    print()


# ======================================================================
# 五、命令行入口
# ======================================================================


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="math_quiz.py",
        description="小学生四则运算出题程序（支持括号、保证结果为整数）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python math_quiz.py                            交互练习 10 题\n"
            "  python math_quiz.py paper -n 20                生成 20 题试卷\n"
            "  python math_quiz.py paper -n 20 --show-answers 试卷附带答案\n"
            "  python math_quiz.py quiz -n 15 --max-num 50    数字范围 1~50\n"
        ),
    )
    parser.add_argument(
        "mode", nargs="?", choices=["quiz", "paper"], default=None,
        help="运行模式：quiz 交互练习（默认），paper 生成试卷",
    )
    parser.add_argument(
        "-m", "--mode", dest="mode_opt", choices=["quiz", "paper"], default=None,
        help="运行模式（与直接写 quiz / paper 等价）",
    )
    parser.add_argument("-n", "--count", type=int, default=10, help="题目数量（默认 10）")
    parser.add_argument("--min-num", type=int, default=1, help="参与运算的最小数字（默认 1）")
    parser.add_argument("--max-num", type=int, default=20, help="参与运算的最大数字（默认 20）")
    parser.add_argument(
        "--ops", default="+-*/",
        help="允许的运算符，从 + - * / 里选（默认全部）",
    )
    parser.add_argument("--no-mul", action="store_true", help="不出现乘法")
    parser.add_argument("--no-div", action="store_true", help="不出现除法")
    parser.add_argument(
        "-d", "--depth", type=int, default=2,
        help="运算嵌套层数，2 表示形如 3 × (2 + 5)，3 表示更深（默认 2）",
    )
    parser.add_argument(
        "--max-result", type=int, default=100,
        help="每一步计算结果的允许上限（默认 100）",
    )
    parser.add_argument(
        "--paren-ratio", type=float, default=0.5,
        help="希望带括号的题目占比，0~1（默认 0.5）",
    )
    parser.add_argument(
        "--brackets-heavy", action="store_true",
        help="所有题目都尽量带括号（等价于 --paren-ratio 1）",
    )
    parser.add_argument("--allow-zero", action="store_true", help="允许减法结果为 0")
    parser.add_argument("--columns", type=int, default=2, help="试卷每行排几题（默认 2）")
    parser.add_argument("--show-answers", action="store_true", help="试卷末尾附答案")
    parser.add_argument("-o", "--output", help="把试卷保存到指定文件")
    parser.add_argument("--answer-file", help="把答案单独保存到指定文件")
    parser.add_argument("--seed", type=int, help="随机种子，填了就能复现同一套题")
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> QuizConfig:
    ops = "".join(ch for ch in args.ops if ch in "+-*/")
    if args.no_mul:
        ops = ops.replace("*", "")
    if args.no_div:
        ops = ops.replace("/", "")
    if not ops:
        ops = "+-"

    if args.min_num > args.max_num:
        args.min_num, args.max_num = args.max_num, args.min_num

    ratio = 1.0 if args.brackets_heavy else max(0.0, min(1.0, args.paren_ratio))

    return QuizConfig(
        min_num=max(0, args.min_num),
        max_num=max(1, args.max_num),
        ops=ops,
        max_depth=max(1, args.depth),
        max_result=max(1, args.max_result),
        paren_ratio=ratio,
        allow_zero=args.allow_zero,
    )


def main(argv: Optional[List[str]] = None) -> int:
    setup_console()
    args = parse_args(argv)
    args.mode = args.mode or args.mode_opt or "quiz"

    if args.count <= 0:
        print("题目数量需要大于 0。")
        return 2

    cfg = build_config(args)
    rng = random.Random(args.seed)

    if cfg.paren_ratio > 0 and cfg.max_depth < 2:
        print("提示：想让题目出现括号，请把 --depth 设为 2 或更大（建议 3）。\n")

    questions = generate_questions(args.count, cfg, rng)

    if args.mode == "quiz":
        run_quiz(questions)
        return 0

    # ---- paper 模式 ----
    paper, answers = build_paper(questions, columns=args.columns)

    if args.answer_file:
        Path(args.answer_file).write_text(answers + "\n", encoding="utf-8")
        print(f"答案已保存到：{args.answer_file}")

    text = paper + ("\n\n" + answers if args.show_answers else "")
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"试卷已保存到：{args.output}")
    print()
    print(text)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n已退出。")
        raise SystemExit(130)
