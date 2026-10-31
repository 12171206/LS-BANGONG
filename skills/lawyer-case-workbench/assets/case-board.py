#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把案件看板写进飞书多维表格（Base）。

复用本机已保存的飞书应用凭据（Windows DPAPI 加密），不打印、不外传密钥。

用法：
    # 1) 只读探测：这个链接是什么？有哪些表、哪些字段？
    python case-board.py probe  --target "https://<tenant>.feishu.cn/wiki/<node>?from=..."
    python case-board.py probe  --target "<wiki_node_token>" --table-id tblxxx

    # 2) 按案件看板字段建表（缺什么建什么，会重命名主字段）
    python case-board.py ensure-schema --target "..." [--table-id tblxxx]

    # 3) 追加一行案件记录
    python case-board.py append --target "..." --case-json case.json [--dry-run]

    # 4) 列出已有记录（回读校验）
    python case-board.py list --target "..." [--limit 20]

凭据来源优先级：环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET → 本机 credentials.json。
"""

from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import re
import sys
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

FEISHU_API = "https://open.feishu.cn/open-apis"

# 案件看板字段定义：name / type / options（仅单选需要）
# type: 1 文本, 2 数字, 3 单选, 5 日期(毫秒时间戳), 15 超链接
CASE_FIELDS: list[dict] = [
    {"field_name": "案件编号", "type": 1, "primary": True},
    {"field_name": "案件名称", "type": 1},
    {"field_name": "客户姓名", "type": 1},
    {"field_name": "对方当事人", "type": 1},
    {"field_name": "案由", "type": 1},
    {"field_name": "承办律师", "type": 1},
    {"field_name": "当前阶段", "type": 3, "options": ["咨询中", "已接案", "取证中", "已立案", "待开庭", "已判决", "执行中", "已结案"]},
    {"field_name": "下一个动作", "type": 1},
    {"field_name": "截止时间", "type": 5},
    {"field_name": "争议金额", "type": 2},
    {"field_name": "收费状态", "type": 3, "options": ["未收费", "已收一半", "已收全款", "风险代理"]},
    {"field_name": "关联文件", "type": 1},
]


# ---------------------------------------------------------------- credentials
class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _credentials_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
        return base / "AgentSkills" / "feishu-video-topic-miner" / "credentials.json"
    return Path.home() / ".local" / "state" / "agent-skills" / "feishu-video-topic-miner" / "credentials.json"


def _unprotect(encoded: str) -> str:
    if os.name != "nt":
        raise RuntimeError("凭据解密当前仅支持 Windows；可用环境变量提供。")
    raw = base64.b64decode(encoded)
    buffer = ctypes.create_string_buffer(raw)
    blob_in = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    blob_out = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0x01, ctypes.byref(blob_out)
    )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def load_credentials() -> tuple[str, str]:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if app_id and app_secret:
        return app_id, app_secret
    path = _credentials_path()
    if not path.is_file():
        raise RuntimeError(f"未找到飞书凭据：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return str(payload["app_id"]), _unprotect(str(payload["protected_secret"]))


# ---------------------------------------------------------------- api helpers
class Feishu:
    def __init__(self) -> None:
        app_id, app_secret = load_credentials()
        r = requests.post(
            f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=20,
        )
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"取 token 失败：{data}")
        self.token = data["tenant_access_token"]

    def call(self, method: str, path: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=utf-8"}
        r = requests.request(method, f"{FEISHU_API}{path}", headers=headers, timeout=30, **kwargs)
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"非 JSON 响应 {r.status_code}: {r.text[:300]}")


# ---------------------------------------------------------------- target
def parse_target(target: str) -> dict:
    """从链接或裸 token 里解出 wiki 节点 / bitable app token。"""
    target = target.strip()
    if target.startswith("http"):
        parsed = urlparse(target)
        m = re.search(r"/wiki/([A-Za-z0-9]+)", parsed.path)
        if m:
            return {"kind": "wiki", "token": m.group(1), "host": parsed.netloc}
        m = re.search(r"/base/([A-Za-z0-9]+)", parsed.path)
        if m:
            return {"kind": "base", "token": m.group(1), "host": parsed.netloc}
        raise SystemExit(f"无法识别的飞书链接：{target}")
    return {"kind": "token", "token": target, "host": ""}


def resolve_app(fs: Feishu, target: dict) -> tuple[str, str]:
    """返回 (app_token, 说明)。wiki 节点需再查 obj_token。"""
    token = target["token"]
    if target["kind"] == "wiki":
        data = fs.call("GET", "/wiki/v2/spaces/get_node", params={"token": token})
        if data.get("code") != 0:
            raise SystemExit(f"取 wiki 节点失败：{data}")
        node = data["data"]["node"]
        obj_type, obj_token = node.get("obj_type"), node.get("obj_token")
        title = node.get("title", "")
        print(f"  wiki 节点：{title}（obj_type={obj_type}）")
        if obj_type != "bitable":
            raise SystemExit(
                f"这个 wiki 节点不是多维表格，而是 {obj_type}。\n"
                f"  请在飞书里新建一个「多维表格」，把链接发我；或把应用加为该表的可编辑协作者。"
            )
        return obj_token, f"wiki:{title}"
    if target["kind"] == "base":
        return token, "base-link"
    return token, "raw-token"


def list_tables(fs: Feishu, app_token: str) -> list[dict]:
    data = fs.call("GET", f"/bitable/v1/apps/{app_token}/tables", params={"page_size": 100})
    if data.get("code") != 0:
        raise SystemExit(f"列表失败：{data}")
    return data["data"].get("items", [])


def list_fields(fs: Feishu, app_token: str, table_id: str) -> list[dict]:
    data = fs.call("GET", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", params={"page_size": 100})
    if data.get("code") != 0:
        raise SystemExit(f"取字段失败：{data}")
    return data["data"].get("items", [])


def pick_table(tables: list[dict], wanted: str | None) -> dict:
    if wanted:
        for t in tables:
            if t["table_id"] == wanted:
                return t
        raise SystemExit(f"没找到 table_id={wanted}；现有：{[t['table_id'] for t in tables]}")
    if not tables:
        raise SystemExit("这个多维表格里一张表都没有")
    return tables[0]


# ---------------------------------------------------------------- commands
def cmd_probe(args) -> int:
    fs = Feishu()
    print("鉴权：OK")
    target = parse_target(args.target)
    app_token, note = resolve_app(fs, target)
    print(f"  app_token：{app_token}（{note}）")
    tables = list_tables(fs, app_token)
    print(f"  数据表：{len(tables)} 张")
    for t in tables:
        print(f"    - {t.get('name')}  table_id={t['table_id']}  revision={t.get('revision')}")
    table = pick_table(tables, args.table_id)
    fields = list_fields(fs, app_token, table["table_id"])
    print(f"\n表「{table.get('name')}」字段 {len(fields)} 个：")
    print(f"    {'字段名':<14} {'类型':<6} {'字段ID'}")
    for f in fields:
        print(f"    {f.get('field_name',''):<14} {f.get('type'):<6} {f.get('field_id')}")
    print(f"\n案件看板需要 {len(CASE_FIELDS)} 个字段；缺失：",
          [c["field_name"] for c in CASE_FIELDS if c["field_name"] not in {f.get("field_name") for f in fields}])
    return 0


def ensure_field(fs: Feishu, app_token: str, table_id: str, spec: dict, existing: list[dict]) -> str:
    names = {f.get("field_name"): f for f in existing}
    name = spec["field_name"]
    if name in names:
        return "已存在"
    payload: dict = {"field_name": name, "type": spec["type"]}
    if spec.get("options"):
        payload["property"] = {"options": [{"name": o} for o in spec["options"]]}
    data = fs.call("POST", f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields", json=payload)
    if data.get("code") != 0:
        return f"失败({data.get('code')}: {data.get('msg')})"
    return "已创建"


def cmd_ensure_schema(args) -> int:
    fs = Feishu()
    target = parse_target(args.target)
    app_token, note = resolve_app(fs, target)
    table = pick_table(list_tables(fs, app_token), args.table_id)
    table_id = table["table_id"]
    fields = list_fields(fs, app_token, table_id)

    # 主字段通常叫"文本"/"名称"，改名成"案件编号"
    primary = next((f for f in fields if f.get("is_primary")), None)
    if primary and primary.get("field_name") != "案件编号":
        data = fs.call(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{primary['field_id']}",
            json={"field_name": "案件编号", "type": primary.get("type", 1)},
        )
        print(f"  主字段改名：{primary.get('field_name')} → 案件编号  ({'OK' if data.get('code')==0 else data})")
        fields = list_fields(fs, app_token, table_id)

    for spec in CASE_FIELDS:
        result = ensure_field(fs, app_token, table_id, spec, fields)
        print(f"  {spec['field_name']:<8} {result}")
        if result == "已创建":
            fields = list_fields(fs, app_token, table_id)
    print("\n完成。当前字段：")
    for f in list_fields(fs, app_token, table_id):
        print(f"  - {f.get('field_name')} (type={f.get('type')})")
    return 0


def _build_fields(fs: Feishu, app_token: str, table_id: str, record: dict) -> tuple[dict, list[str]]:
    """把 JSON 里的键值转成飞书 fields；日期字符串转毫秒时间戳。"""
    fields = {f.get("field_name"): f for f in list_fields(fs, app_token, table_id)}
    payload: dict = {}
    dropped: list[str] = []
    for key, value in record.items():
        if key not in fields:
            dropped.append(key)
            continue
        if fields[key].get("type") == 5 and isinstance(value, str):
            dt = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=datetime.now().astimezone().tzinfo)
            value = int(dt.timestamp() * 1000)
        payload[key] = value
    return payload, dropped


def cmd_append(args) -> int:
    record = json.loads(Path(args.case_json).read_text(encoding="utf-8"))
    fs = Feishu()
    target = parse_target(args.target)
    app_token, _ = resolve_app(fs, target)
    table = pick_table(list_tables(fs, app_token), args.table_id)
    payload, dropped = _build_fields(fs, app_token, table["table_id"], record)
    if dropped:
        print(f"  跳过表里没有的字段：{dropped}")
    body = {"fields": payload}
    if args.dry_run:
        print(json.dumps({"dry_run": True, "payload": body}, ensure_ascii=False, indent=2))
        return 0
    data = fs.call("POST", f"/bitable/v1/apps/{app_token}/tables/{table['table_id']}/records", json=body)
    if data.get("code") != 0:
        raise SystemExit(f"写记录失败：{data}")
    print(json.dumps({"ok": True, "record_id": data["data"]["record"]["record_id"]}, ensure_ascii=False))
    return 0


def cmd_update(args) -> int:
    record = json.loads(Path(args.case_json).read_text(encoding="utf-8"))
    fs = Feishu()
    target = parse_target(args.target)
    app_token, _ = resolve_app(fs, target)
    table = pick_table(list_tables(fs, app_token), args.table_id)
    payload, dropped = _build_fields(fs, app_token, table["table_id"], record)
    if dropped:
        print(f"  跳过表里没有的字段：{dropped}")
    body = {"fields": payload}
    if args.dry_run:
        print(json.dumps({"dry_run": True, "record_id": args.record_id, "payload": body}, ensure_ascii=False, indent=2))
        return 0
    data = fs.call(
        "PUT",
        f"/bitable/v1/apps/{app_token}/tables/{table['table_id']}/records/{args.record_id}",
        json=body,
    )
    if data.get("code") != 0:
        raise SystemExit(f"更新记录失败：{data}")
    print(json.dumps({"ok": True, "updated": args.record_id}, ensure_ascii=False))
    return 0


def cmd_list(args) -> int:
    fs = Feishu()
    target = parse_target(args.target)
    app_token, _ = resolve_app(fs, target)
    table = pick_table(list_tables(fs, app_token), args.table_id)
    data = fs.call(
        "GET",
        f"/bitable/v1/apps/{app_token}/tables/{table['table_id']}/records",
        params={"page_size": 100},
    )
    if data.get("code") != 0:
        raise SystemExit(f"读记录失败：{data}")
    items = data["data"].get("items", [])
    blank = [it for it in items if not any(v not in (None, "", []) for v in it.get("fields", {}).values())]
    filled = [it for it in items if it not in blank]
    print(f"表「{table.get('name')}」共 {len(items)} 行：有效 {len(filled)} 行，空白行 {len(blank)} 行（空白行通常是手工建的空记录，不删）")
    for it in filled[: args.limit]:
        print("  -", json.dumps(it.get("fields"), ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="案件看板 → 飞书多维表格")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("probe", cmd_probe), ("ensure-schema", cmd_ensure_schema), ("append", cmd_append), ("update", cmd_update), ("list", cmd_list)):
        p = sub.add_parser(name)
        p.add_argument("--target", required=True, help="飞书 wiki/base 链接或 token")
        p.add_argument("--table-id", help="指定数据表；缺省用第一张")
        if name in ("append", "update"):
            p.add_argument("--case-json", required=True)
            p.add_argument("--dry-run", action="store_true")
        if name == "update":
            p.add_argument("--record-id", required=True)
        if name == "list":
            p.add_argument("--limit", type=int, default=20)
        p.set_defaults(func=fn)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
