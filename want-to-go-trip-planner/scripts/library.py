#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local travel library shared with earlier agent editions."""
import argparse
import errno
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from route_quality import normalize_area


LANGUAGE = "en-US"
ZH = {
    'Library error: ': '想去库错误：',
    'Cannot overwrite the library database or original sources': '不能覆盖想去库数据库或原始素材',
    ' Places Passport': '想去地点护照',
    'Confirm and distinguish branches with identical names before export': '同名地点需要先核实并区分分店，避免遗漏或误合并',
    'Run the skill script by absolute path from the user project, not from the installed skill directory.': '请在用户项目目录通过技能脚本的绝对路径运行，避免将想去库误存到技能安装目录',
    'Local Go Passport library': '本地想去库',
    'A place is already part of a merge. Review the existing merge first.': '地点已合并或包含其他合并项，需人工核对',
    'Both places must exist in the same city. Confirm the city first.': '两个地点必须存在且城市一致，先核实城市',
    'Cannot merge a place into itself': '不能将地点合并到自身',
    'A source that failed to save cannot be marked processed': '保存失败的来源不能标为已处理',
    'Source not found': '未找到来源',
    'Original batch not found': '未找到原收藏批次',
    'Invalid additional place name, city, aliases, or status': '补充地点的名称、城市、别名或状态无效',
    'Additional places must be a non-empty array': '补充地点必须是非空数组',
    'Invalid updated name, status, or city aliases': '更正后的名称、状态或城市别名无效',
    'Place not found': '未找到该地点',
    'The update contains unsupported fields': '更正文件包含不支持的字段',
    'Provide a destination': '请提供目的地',
    'Source file changed during copying': '源文件复制期间发生变化',
    'Source file is missing or unreadable': '源文件不存在或不可读取',
    'This batch ID exists with different content. Use a new batch ID.': '该批次 ID 已存在且内容不同，请使用新的批次 ID',
    'Identified places require a city. Use pending if uncertain.': '已识别地点必须有城市；不确定时标记 pending',
    'city_aliases must be an array': 'city_aliases 必须为数组',
    'Place status must be identified or pending': '地点状态必须为 identified 或 pending',
    'Each place requires a name': '地点必须有名称',
    'Each source requires path, url, or text': '每条来源必须提供 path、url 或 text',
    'Source type must be image, video, file, link, or text': '来源类型必须为 image、video、file、link 或 text',
    'places must be an array': 'places 必须是数组',
    'Each batch requires a non-empty sources array': '每批必须提供非空 sources 数组',
    'Two libraries found. Specify the library you previously used.': '发现两个想去库，请指定原来使用的库，避免遗漏收藏',
}


ZH["area must be text of at most 80 characters"] = "片区须为不超过 80 个字符的文本"


def tr(message):
    return ZH.get(message, message) if LANGUAGE == "zh-CN" else message


def normal(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as f:
        name = f.name
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(name, path)


def connect(root):
    root.mkdir(parents=True, exist_ok=True)
    try:
        root.chmod(0o700)
    except OSError:
        pass
    db = sqlite3.connect(root / "library.sqlite3", timeout=30)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, created TEXT NOT NULL, manifest TEXT NOT NULL, digest TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), kind TEXT NOT NULL, payload TEXT NOT NULL, saved_path TEXT, status TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS places(id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES batches(id), name TEXT NOT NULL, city TEXT NOT NULL, aliases TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions(id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES places(id), created TEXT NOT NULL, previous TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS merges(duplicate_id TEXT PRIMARY KEY REFERENCES places(id), keep_id TEXT NOT NULL REFERENCES places(id), created TEXT NOT NULL);
    """)
    try:
        (root / "library.sqlite3").chmod(0o600)
    except OSError:
        pass
    return db


def resolve_default_root(default_root, project_root):
    """Keep an existing library; fall back only when a new default is denied."""
    default_db = default_root / "library.sqlite3"
    project_db = project_root / "library.sqlite3"
    if default_root.resolve() == project_root.resolve():
        return default_root, "documents"
    if default_db.exists() and project_db.exists():
        raise ValueError(tr('Two libraries found. Specify the library you previously used.'))
    if default_db.exists():
        return default_root, "documents"
    if project_db.exists():
        return project_root, "project"
    try:
        db = connect(default_root)
        db.close()
        return default_root, "documents"
    except PermissionError:
        return project_root, "project"
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return project_root, "project"
        raise
    except sqlite3.OperationalError as exc:
        # Do not abandon a database that was already created, even partially.
        if default_db.exists():
            raise
        if getattr(exc, "sqlite_errorcode", None) in (sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY):
            return project_root, "project"
        raise


def ingest(root, manifest):
    data = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list) or not data["sources"]:
        raise ValueError(tr('Each batch requires a non-empty sources array'))
    places = data.get("places", [])
    if not isinstance(places, list):
        raise ValueError(tr('places must be an array'))
    for source in data["sources"]:
        if not isinstance(source, dict) or source.get("type") not in ("image", "video", "file", "link", "text"):
            raise ValueError(tr('Source type must be image, video, file, link, or text'))
        if not str(source.get("path") or source.get("url") or source.get("text") or "").strip():
            raise ValueError(tr('Each source requires path, url, or text'))
    for place in places:
        if not isinstance(place, dict) or not str(place.get("name", "")).strip():
            raise ValueError(tr('Each place requires a name'))
        if place.get("status", "pending") not in ("identified", "pending"):
            raise ValueError(tr('Place status must be identified or pending'))
        if not isinstance(place.get("city_aliases", []), list):
            raise ValueError(tr('city_aliases must be an array'))
        if place.get("status") == "identified" and not str(place.get("city", "")).strip():
            raise ValueError(tr('Identified places require a city. Use pending if uncertain.'))
    batch = str(data.get("batch_id") or uuid.uuid4())
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    db = connect(root)
    existing = db.execute("SELECT digest FROM batches WHERE id=?", (batch,)).fetchone()
    if existing:
        if existing[0] != digest:
            raise ValueError(tr('This batch ID exists with different content. Use a new batch ID.'))
        return {"batch_id": batch, "already_saved": True}
    results = []
    with db:
        db.execute("INSERT INTO batches VALUES(?,?,?,?)", (batch, datetime.now(timezone.utc).isoformat(), json.dumps(data, ensure_ascii=False), digest))
        for source in data["sources"]:
            sid, saved, error, status = str(uuid.uuid4()), None, None, "saved"
            if source["type"] in ("image", "video", "file"):
                source_path = Path(str(source.get("path", ""))).expanduser()
                try:
                    if not source_path.is_file():
                        raise FileNotFoundError(tr('Source file is missing or unreadable'))
                    hasher = hashlib.sha256()
                    with source_path.open("rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            hasher.update(chunk)
                    ext = source_path.suffix.lower()
                    if len(ext) > 12 or any(not (c.isalnum() or c == ".") for c in ext):
                        ext = ""
                    target = root / "originals" / (hasher.hexdigest() + ext)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists():
                        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
                        try:
                            with os.fdopen(fd, "wb") as out, source_path.open("rb") as src:
                                shutil.copyfileobj(src, out)
                            with open(tmp, "rb") as check:
                                copied = hashlib.sha256()
                                for chunk in iter(lambda: check.read(1024 * 1024), b""):
                                    copied.update(chunk)
                                if copied.hexdigest() != hasher.hexdigest():
                                    raise ValueError(tr('Source file changed during copying'))
                            os.replace(tmp, target)
                        finally:
                            if os.path.exists(tmp):
                                os.unlink(tmp)
                    saved = str(target)
                except (OSError, ValueError) as exc:
                    status, error = "failed", str(exc)
            db.execute("INSERT INTO sources VALUES(?,?,?,?,?,?,?)", (sid, batch, source["type"], json.dumps(source, ensure_ascii=False), saved, status, error))
            results.append({"id": sid, "type": source["type"], "status": status, "saved_path": saved, "error": error})
        for p in places:
            db.execute("INSERT INTO places VALUES(?,?,?,?,?,?,?)", (str(uuid.uuid4()), batch, str(p["name"]).strip(), str(p.get("city", "")).strip(), json.dumps(p.get("city_aliases", []), ensure_ascii=False), p.get("status", "pending"), json.dumps(p, ensure_ascii=False)))
    return {"batch_id": batch, "received": len(data["sources"]), "saved": sum(x["status"] == "saved" for x in results), "failed": sum(x["status"] == "failed" for x in results), "places": len(places), "sources": results}


def search(root, city):
    needle = normal(city)
    if not needle:
        raise ValueError(tr('Provide a destination'))
    db = connect(root)
    matched, pending = [], []
    for pid, batch, name, city_value, aliases_json, status, payload in db.execute("SELECT id,batch_id,name,city,aliases,status,payload FROM places WHERE id NOT IN (SELECT duplicate_id FROM merges) ORDER BY rowid"):
        aliases = json.loads(aliases_json)
        values = [normal(city_value)] + [normal(a) for a in aliases]
        p = json.loads(payload)
        p.update({"id": pid, "batch_id": batch, "status": status})
        p["also_from_batches"] = [x[0] for x in db.execute("SELECT places.batch_id FROM merges JOIN places ON places.id=merges.duplicate_id WHERE merges.keep_id=? ORDER BY places.rowid", (pid,))]
        p["merged_records"] = [json.loads(x[0]) for x in db.execute("SELECT places.payload FROM merges JOIN places ON places.id=merges.duplicate_id WHERE merges.keep_id=? ORDER BY places.rowid", (pid,))]
        if needle in values:
            (matched if status == "identified" else pending).append(p)
        elif not city_value:
            pending.append(p)
    unprocessed = []
    for batch, in db.execute("SELECT id FROM batches ORDER BY rowid"):
        sources = []
        any_places = bool(db.execute("SELECT 1 FROM places WHERE batch_id=? LIMIT 1", (batch,)).fetchone())
        for sid, kind, payload, saved, status, error in db.execute("SELECT id,kind,payload,saved_path,status,error FROM sources WHERE batch_id=? ORDER BY rowid", (batch,)):
            original = json.loads(payload)
            if not any_places or original.get("processing_status", "pending") != "processed":
                sources.append({"id": sid, "type": kind, "original": original, "saved_path": saved, "status": status, "error": error})
        if sources:
            unprocessed.append({"batch_id": batch, "sources": sources})
    batches = {}
    for batch in {batch for p in matched + pending for batch in [p["batch_id"]] + p.get("also_from_batches", [])}:
        batches[batch] = [{"type": kind, "original": json.loads(payload), "saved_path": saved, "status": status, "error": error} for kind, payload, saved, status, error in db.execute("SELECT kind,payload,saved_path,status,error FROM sources WHERE batch_id=? ORDER BY rowid", (batch,))]
    failed_sources = [{"batch_id": batch, "type": kind, "original": json.loads(payload), "error": error} for batch, kind, payload, error in db.execute("SELECT batch_id,kind,payload,error FROM sources WHERE status='failed' ORDER BY rowid")]
    return {"city": city, "places": matched, "pending": pending, "batches": batches, "unprocessed_batches": unprocessed, "failed_sources": failed_sources}


def update(root, place_id, input_path):
    patch = json.loads(Path(input_path).read_text(encoding="utf-8"))
    allowed = {"name", "city", "city_aliases", "status", "address", "hours", "note", "branch", "area", "source_urls", "verified_at"}
    if not isinstance(patch, dict) or set(patch) - allowed:
        raise ValueError(tr('The update contains unsupported fields'))
    if "area" in patch:
        try:
            patch["area"] = normalize_area(patch["area"])
        except ValueError as exc:
            raise ValueError(tr(str(exc))) from None
    db = connect(root)
    row = db.execute("SELECT payload FROM places WHERE id=?", (place_id,)).fetchone()
    if not row:
        raise ValueError(tr('Place not found'))
    value = json.loads(row[0]); value.update(patch)
    if not str(value.get("name", "")).strip() or value.get("status", "pending") not in ("identified", "pending") or not isinstance(value.get("city_aliases", []), list) or (value.get("status") == "identified" and not str(value.get("city", "")).strip()):
        raise ValueError(tr('Invalid updated name, status, or city aliases'))
    with db:
        db.execute("INSERT INTO revisions VALUES(?,?,?,?)", (str(uuid.uuid4()), place_id, datetime.now(timezone.utc).isoformat(), row[0]))
        db.execute("UPDATE places SET name=?,city=?,aliases=?,status=?,payload=? WHERE id=?", (value["name"].strip(), str(value.get("city", "")).strip(), json.dumps(value.get("city_aliases", []), ensure_ascii=False), value.get("status", "pending"), json.dumps(value, ensure_ascii=False), place_id))
    return {"id": place_id, "updated": True}


def add_places(root, batch_id, input_path):
    places = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if not isinstance(places, list) or not places:
        raise ValueError(tr('Additional places must be a non-empty array'))
    for p in places:
        if not isinstance(p, dict) or not str(p.get("name", "")).strip() or p.get("status", "pending") not in ("identified", "pending") or not isinstance(p.get("city_aliases", []), list) or (p.get("status") == "identified" and not str(p.get("city", "")).strip()):
            raise ValueError(tr('Invalid additional place name, city, aliases, or status'))
    db = connect(root)
    if not db.execute("SELECT 1 FROM batches WHERE id=?", (batch_id,)).fetchone():
        raise ValueError(tr('Original batch not found'))
    ids = []
    with db:
        for p in places:
            pid = str(uuid.uuid4())
            db.execute("INSERT INTO places VALUES(?,?,?,?,?,?,?)", (pid, batch_id, p["name"].strip(), str(p.get("city", "")).strip(), json.dumps(p.get("city_aliases", []), ensure_ascii=False), p.get("status", "pending"), json.dumps(p, ensure_ascii=False)))
            ids.append(pid)
    return {"batch_id": batch_id, "added": ids}


def mark_source(root, source_id):
    db = connect(root)
    row = db.execute("SELECT payload,status FROM sources WHERE id=?", (source_id,)).fetchone()
    if not row:
        raise ValueError(tr('Source not found'))
    if row[1] != "saved":
        raise ValueError(tr('A source that failed to save cannot be marked processed'))
    payload = json.loads(row[0]); payload["processing_status"] = "processed"
    with db:
        db.execute("UPDATE sources SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), source_id))
    return {"id": source_id, "processed": True}


def merge(root, keep_id, duplicate_id):
    if keep_id == duplicate_id:
        raise ValueError(tr('Cannot merge a place into itself'))
    db = connect(root)
    keep = db.execute("SELECT city FROM places WHERE id=?", (keep_id,)).fetchone()
    other = db.execute("SELECT city FROM places WHERE id=?", (duplicate_id,)).fetchone()
    if not keep or not other or not normal(keep[0]) or normal(keep[0]) != normal(other[0]):
        raise ValueError(tr('Both places must exist in the same city. Confirm the city first.'))
    if db.execute("SELECT 1 FROM merges WHERE duplicate_id IN (?,?) OR keep_id=?", (keep_id, duplicate_id, duplicate_id)).fetchone():
        raise ValueError(tr('A place is already part of a merge. Review the existing merge first.'))
    with db:
        db.execute("INSERT INTO merges VALUES(?,?,?)", (duplicate_id, keep_id, datetime.now(timezone.utc).isoformat()))
    return {"kept": keep_id, "merged": duplicate_id, "original_records_retained": True}


def main():
    parser = argparse.ArgumentParser(description=tr('Local Go Passport library'))
    parser.add_argument("--root", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    capture = sub.add_parser("capture")
    capture.add_argument("manifest")
    sub.add_parser("catalog")
    lookup = sub.add_parser("find")
    lookup.add_argument("city")
    export = sub.add_parser("export")
    export.add_argument("city")
    export.add_argument("output")
    export.add_argument("--map-region", choices=("mainland", "outside_mainland", "unknown"), default="unknown")
    edit = sub.add_parser("update")
    edit.add_argument("place_id")
    edit.add_argument("input")
    add = sub.add_parser("add-places")
    add.add_argument("batch_id")
    add.add_argument("input")
    reviewed = sub.add_parser("source-processed")
    reviewed.add_argument("source_id")
    combined = sub.add_parser("merge")
    combined.add_argument("keep_id")
    combined.add_argument("duplicate_id")
    parser.add_argument("--locale", choices=("zh-CN", "en-US"), default="en-US")
    args = parser.parse_args()
    global LANGUAGE
    LANGUAGE = args.locale
    explicit = args.root or os.environ.get("KORNVIA_LIBRARY_DIR")
    if explicit:
        root, mode = Path(explicit).expanduser().absolute(), "specified"
    else:
        skill_root = Path(__file__).resolve().parent.parent
        if Path.cwd().resolve() == skill_root or skill_root in Path.cwd().resolve().parents:
            raise ValueError(tr('Run the skill script by absolute path from the user project, not from the installed skill directory.'))
        root, mode = resolve_default_root(Path.home() / "Documents" / "Kornvia想去库", Path.cwd() / "Kornvia想去库")
    if args.command == "capture":
        result = ingest(root, args.manifest)
        result.update({"library_path": str(root), "storage_mode": mode})
        output(result)
    elif args.command == "catalog":
        db = connect(root)
        places = [{"id": pid, "batch_id": batch, "name": name, "city": city, "status": status} for pid, batch, name, city, status in db.execute("SELECT id,batch_id,name,city,status FROM places WHERE id NOT IN (SELECT duplicate_id FROM merges) ORDER BY rowid")]
        output({"library_path": str(root), "storage_mode": mode, "places": places, "merged_records": db.execute("SELECT COUNT(*) FROM merges").fetchone()[0], "batch_count": db.execute("SELECT COUNT(*) FROM batches").fetchone()[0], "source_count": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]})
    elif args.command == "update":
        output(update(root, args.place_id, args.input))
    elif args.command == "add-places":
        output(add_places(root, args.batch_id, args.input))
    elif args.command == "source-processed":
        output(mark_source(root, args.source_id))
    elif args.command == "merge":
        output(merge(root, args.keep_id, args.duplicate_id))
    else:
        result = search(root, args.city)
        result.update({"library_path": str(root), "storage_mode": mode})
        if args.command == "export":
            names = [normal(p["name"]) for p in result["places"]]
            if len(names) != len(set(names)):
                raise ValueError(tr('Confirm and distinguish branches with identical names before export'))
            report = {"locale": args.locale, "title": args.city + tr(' Places Passport'), "city": args.city, "map_region": args.map_region, "places": result["places"]}
            target = Path(args.output).resolve()
            if target == (root / "library.sqlite3").resolve() or (root / "originals").resolve() in target.parents:
                raise ValueError(tr('Cannot overwrite the library database or original sources'))
            atomic_json(args.output, report)
            result["output"] = str(Path(args.output).absolute())
        output(result)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(tr('Library error: ') + str(exc), file=sys.stderr)
        sys.exit(1)
