#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验案件台账：编号唯一、连续、可追溯，且全案文书引用的编号都能在台账找到。

设计说明：
- 台账里的"空行占位"（只有 ID、名称/描述列为空）视为**尚未登记**，不参与编号连续性判断；
- 某个编号种类（TF/E/IS）一条都没登记时，只在输出里提示，不因"文书引用了证据"而报失败
  ——新案子刚建台就是这个状态；
- 一旦该类编号登记过，就严格校验：文书里出现的编号必须能在台账找到对应已登记行。

用法：
    python check-ledger.py <案件目录>
    python check-ledger.py <案件目录> --quiet

退出码：0 = 通过；1 = 有问题（会逐条列出）。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

LEDGER_REL = Path("00-案件档案") / "案件台账.md"

CN_NUM = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
    "九": 9, "十": 10, "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15,
    "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}

ID_RE = re.compile(r"\b(?:TF|E|IS)-(\d{1,3})\b")
CN_EVIDENCE_RE = re.compile(r"证据([一二三四五六七八九十]{1,3})")


def norm(prefix: str, num: int) -> str:
    return f"{prefix}-{num:02d}"


def split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells) if cells else False


def parse_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    tables: list[tuple[list[str], list[list[str]]]] = []
    header: list[str] | None = None
    rows: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            if header is not None:
                tables.append((header, rows))
                header, rows = None, []
            continue
        cells = split_row(line)
        if is_separator(cells):
            continue
        if header is None:
            header = cells
        else:
            rows.append(cells)
    if header is not None:
        tables.append((header, rows))
    return tables


def collect_ids(text: str, prefix: str) -> tuple[list[int], dict[int, list[str]]]:
    """收集某个前缀的**已登记**编号（名称/描述列非空的才算登记）。"""
    nums: list[int] = []
    rows_by_num: dict[int, list[str]] = {}
    for header, rows in parse_tables(text):
        for idx, col in enumerate(header):
            if not col.upper().startswith(prefix):
                continue
            for row in rows:
                if idx >= len(row):
                    continue
                m = re.fullmatch(rf"{prefix}-(\d{{1,3}})", row[idx].strip().upper())
                if not m:
                    continue
                # 第 2 列（编号后那一列）为空 → 模板占位行，视为未登记
                desc = row[idx + 1].strip() if idx + 1 < len(row) else ""
                if not desc:
                    continue
                n = int(m.group(1))
                nums.append(n)
                rows_by_num[n] = row
    return nums, rows_by_num


def referenced_ids_in_docs(docs: list[Path]) -> tuple[set[str], dict[str, list[str]]]:
    found: set[str] = set()
    where: dict[str, list[str]] = {}
    for doc in docs:
        try:
            text = doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        ids: set[str] = set()
        for m in ID_RE.finditer(text):
            ids.add(m.group(0).upper())
        for m in CN_EVIDENCE_RE.finditer(text):
            n = CN_NUM.get(m.group(1))
            if n:
                ids.add(f"E-{n:02d}")
        for want in ids:
            found.add(want)
            where.setdefault(want, []).append(doc.name)
    return found, where


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("case_dir", type=Path)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    case_dir = args.case_dir.resolve()
    ledger = case_dir / LEDGER_REL
    issues: list[str] = []
    warnings: list[str] = []

    if not ledger.is_file():
        print(f"[FAIL] 台账不存在：{ledger}")
        return 1

    text = ledger.read_text(encoding="utf-8")

    tf_nums, tf_rows = collect_ids(text, "TF")
    e_nums, e_rows = collect_ids(text, "E")
    is_nums, _ = collect_ids(text, "IS")

    registered = {"TF": set(tf_nums), "E": set(e_nums), "IS": set(is_nums)}

    # 1) 未登记的编号种类 → 提示不失败；已登记的 → 查唯一与连续
    for label, nums in (("TF", tf_nums), ("E", e_nums), ("IS", is_nums)):
        if not nums:
            warnings.append(f"{label} 表尚未登记编号（新案子属正常；派发对应技能前必须补齐）")
            continue
        dupes = sorted({n for n in nums if nums.count(n) > 1})
        if dupes:
            issues.append(f"{label} 编号重复：{sorted(norm(label, n) for n in dupes)}")
        missing = [n for n in range(1, max(nums) + 1) if n not in nums]
        if missing:
            issues.append(f"{label} 编号跳号（缺）：{sorted(norm(label, n) for n in missing)}")

    # 2) 行内交叉引用（只查已登记行）
    for n, row in e_rows.items():
        for m in ID_RE.finditer(" ".join(row)):
            if not m.group(0).upper().startswith("TF"):
                continue
            ref_n = int(m.group(1))
            if ref_n not in registered["TF"]:
                issues.append(f"E-{n:02d} 指向未登记的待证事实 {norm('TF', ref_n)}")
    for n, row in tf_rows.items():
        cells = " ".join(row)
        has_e = any(m.group(0).upper().startswith("E") for m in ID_RE.finditer(cells))
        if not has_e and not any(k in cells for k in ("断点", "待补")):
            issues.append(f"TF-{n:02d} 没有挂证据，且状态不是 断点/待补")

    # 3) 全案文书引用必须可追溯（该类编号一条都没登记时跳过，只提示）
    docs = [p for p in case_dir.rglob("*.md") if p.resolve() != ledger.resolve()]
    referenced, where = referenced_ids_in_docs(docs)
    for ref in sorted(referenced):
        prefix = ref.split("-")[0]
        if prefix not in registered:
            continue
        if not registered[prefix]:
            continue
        if int(ref.split("-")[1]) not in registered[prefix]:
            kind = {"TF": "待证事实", "E": "证据", "IS": "争点"}[prefix]
            issues.append(f"{ref} 被 {where[ref][0]} 引用，但台账里没有登记这条{kind}")

    if args.quiet and not issues:
        return 0

    print(f"案件：{case_dir.name}")
    print(f"台账：{LEDGER_REL}  TF={len(tf_nums)}  E={len(e_nums)}  IS={len(is_nums)}  文书={len(docs)}")
    if warnings:
        print("\n[WARN] 提示：")
        for i, msg in enumerate(warnings, 1):
            print(f"  {i}. {msg}")
    if issues:
        print(f"\n[FAIL] 发现 {len(issues)} 个问题：")
        for i, msg in enumerate(issues, 1):
            print(f"  {i}. {msg}")
        print("\n修完台账再往下派发技能。")
        return 1
    print("\n[PASS] 编号唯一、连续、可追溯；全案文书引用的编号都能在台账找到。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
