#!/usr/bin/env python3
"""Seal/verify a locally reviewed resume with its evidence and source snapshot."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path must stay within project: {relative}")
    return path


def fact_refs(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "fact_ids" or key.endswith("_fact_ids"):
                if not isinstance(item, list) or any(not isinstance(x, str) for x in item):
                    raise ValueError(f"Invalid fact reference list: {key}")
                found.update(item)
            else:
                found.update(fact_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(fact_refs(item))
    return found


def explicit_source_refs(value):
    found = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "source_ids" or key.endswith("_source_ids"):
                if not isinstance(item, list) or any(not isinstance(x, str) for x in item):
                    raise ValueError(f"Invalid source reference list: {key}")
                found.update(item)
            else:
                found.update(explicit_source_refs(item))
    elif isinstance(value, list):
        for item in value:
            found.update(explicit_source_refs(item))
    return found


def validate_evidence(root, resume):
    records = read(root / "profile/facts.json")["facts"]
    facts = {f["id"]: f for f in records}
    if len(facts) != len(records):
        raise ValueError("Duplicate fact IDs")
    refs = fact_refs(resume)
    if not refs or refs - facts.keys():
        raise ValueError(f"Missing/unknown fact IDs: {sorted(refs - facts.keys())}")
    for required in ("name_fact_ids", "headline_fact_ids"):
        if not resume.get(required):
            raise ValueError(f"Missing {required}")
    if resume.get("contact") and not resume.get("contact_fact_ids"):
        raise ValueError("Contact information needs contact_fact_ids")
    if resume.get("summary", {}).get("text") and not resume["summary"].get("fact_ids"):
        raise ValueError("Summary needs fact_ids")
    for section in resume.get("sections", []):
        for item in section.get("items", []):
            if (item.get("period") or item.get("subheading") or not item.get("bullets")) and not item.get("fact_ids"):
                raise ValueError(f"Item needs fact_ids: {item.get('heading')}")
            for bullet in item.get("bullets", []):
                if not bullet.get("fact_ids"):
                    raise ValueError(f"Bullet needs fact_ids: {bullet.get('text')}")
    allowed = {"confirmed"} if resume["status"] == "ready" else {"confirmed", "imported"}
    invalid = {i: facts[i]["status"] for i in refs if facts[i]["status"] not in allowed}
    if invalid:
        raise ValueError(f"Facts not eligible for {resume['status']}: {invalid}")
    sources = read(root / "sources/index.json")["sources"]
    source_map = {s["id"]: s for s in sources}
    if len(source_map) != len(sources):
        raise ValueError("Duplicate source IDs")
    if explicit_source_refs(resume) - source_map.keys():
        raise ValueError("Resume includes an unknown explicit source ID")
    source_ids = set()
    for fact_id in refs:
        linked = facts[fact_id].get("source_ids", [])
        if not linked:
            raise ValueError(f"Fact has no source: {fact_id}")
        for source_id in linked:
            if source_id not in source_map:
                raise ValueError(f"Unknown source: {source_id}")
            source_ids.add(source_id)
    for source in sources:
        path = inside(root, source["path"])
        if not path.is_file() or digest(path) != source["sha256"]:
            raise ValueError(f"Source missing or changed: {source['id']}")
    return {i: facts[i] for i in sorted(refs)}, sources, sorted(source_ids)


def seal(args):
    root = Path(args.project).resolve()
    draft = Path(args.draft).resolve()
    if not re.fullmatch(r"v\d{4}-[a-z0-9-]+", args.version):
        raise ValueError("Version format: v0001-20261010-baseline (lowercase ASCII)")
    if not args.visual_reviewed:
        raise ValueError("Render and visually inspect all PDF pages, then pass --visual-reviewed")
    required = ["resume.json", "resume.pdf", "resume.html", "build-report.json", "claim-review.md"]
    for name in required:
        if not (draft / name).is_file():
            raise ValueError(f"Missing draft artifact: {name}")
    resume = read(draft / "resume.json")
    if resume.get("status") not in {"draft", "ready"}:
        raise ValueError("Resume status must be draft or ready")
    report = read(draft / "build-report.json")
    if report.get("ok") is not True or report.get("status") != resume["status"]:
        raise ValueError("Build report is not successful or has a different status")
    for name, key in [("resume.json", "input_sha256"), ("resume.pdf", "pdf_sha256"), ("resume.html", "html_sha256")]:
        if digest(draft / name) != report.get(key):
            raise ValueError(f"Build report does not match {name}; rebuild before archiving")
    renderer = root / "skills/haixiang-resume/scripts/build_resume.py"
    if digest(renderer) != report.get("renderer_sha256"):
        raise ValueError("Renderer changed since the last build; rebuild before archiving")
    facts, sources, source_ids = validate_evidence(root, resume)
    from pypdf import PdfReader
    pages = PdfReader(draft / "resume.pdf").pages
    if not 1 <= len(pages) <= 2:
        raise ValueError("Resume must have 1-2 pages; review content before archiving")
    if not all(page.extract_text().strip() for page in pages):
        raise ValueError("PDF must contain selectable text on each page")
    changes = Path(args.changes_file).read_text(encoding="utf-8").strip()
    if not changes:
        raise ValueError("Change notes cannot be empty")
    if not (draft / "claim-review.md").read_text(encoding="utf-8").strip():
        raise ValueError("Claim/visual review record cannot be empty")
    job = inside(root, f"jobs/{args.job}") if args.job else None
    if job and not (job / "job.json").is_file():
        raise ValueError("Selected job must have jobs/<id>/job.json")
    versions = root / "versions"
    versions.mkdir(exist_ok=True)
    target = versions / args.version
    lock = versions / ".archive.lock"
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    stage = None
    try:
        index_path = versions / "index.json"
        index = read(index_path) if index_path.exists() else {"schema_version": 1, "versions": []}
        if target.exists() or any(v["id"] == args.version for v in index["versions"]):
            raise ValueError("Version already exists; use a new ID instead of overwriting")
        if args.parent_version:
            if not any(v["id"] == args.parent_version for v in index["versions"]):
                raise ValueError("Parent version is not in the version index")
            parent = args.parent_version
        else:
            related = [v for v in index["versions"] if v.get("job_id") == args.job]
            parent = related[-1]["id"] if related else None
        stage = Path(tempfile.mkdtemp(prefix=".creating-", dir=versions))
        for name in required:
            shutil.copy2(draft / name, stage / name)
        (stage / "changes.md").write_text(changes + "\n", encoding="utf-8")
        snap = stage / "snapshot"
        for folder in ["profile", "interviews"]:
            shutil.copytree(root / folder, snap / folder)
        (snap / "sources").mkdir(parents=True)
        shutil.copy2(root / "sources/index.json", snap / "sources/index.json")
        for source in sources:
            destination = snap / source["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(inside(root, source["path"]), destination)
        if job:
            shutil.copytree(job, snap / "jobs" / args.job)
        skill = root / "skills/haixiang-resume"
        for relative in ["scripts/build_resume.py", "scripts/archive.py", "SKILL.md", "references/data-contract.md"]:
            source = skill / relative
            if source.exists():
                destination = snap / "skill" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        created_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
        manifest = {
            "schema_version": 1, "id": args.version, "created_at": created_at,
            "status": resume["status"], "job_id": args.job,
            "parent_version": parent,
            "pages": len(pages), "visual_reviewed": True,
            "fact_ids": sorted(facts), "source_ids": source_ids,
            "fact_statuses": {i: f["status"] for i, f in facts.items()},
            "files": {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()},
        }
        write(stage / "manifest.json", manifest)
        index["versions"].append({
            "id": args.version, "created_at": created_at, "status": resume["status"],
            "job_id": args.job, "path": str(target.relative_to(root)),
            "manifest_sha256": digest(stage / "manifest.json"),
        })
        os.rename(stage, target)
        stage = None
        index_tmp = versions / ".index.json.tmp"
        write(index_tmp, index)
        os.replace(index_tmp, index_path)
        print(json.dumps({"version": args.version, "directory": str(target), "pages": len(pages), "status": resume["status"]}, ensure_ascii=False))
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        os.close(lock_fd)
        lock.unlink()


def verify(args):
    root = Path(args.project).resolve()
    index = read(root / "versions/index.json")
    records = [v for v in index["versions"] if not args.version or v["id"] == args.version]
    if not records:
        raise ValueError("No matching version in index")
    for record in records:
        base = inside(root, record["path"])
        if digest(base / "manifest.json") != record["manifest_sha256"]:
            raise ValueError(f"Manifest changed: {record['id']}")
        manifest = read(base / "manifest.json")
        if manifest["id"] != record["id"]:
            raise ValueError("Version ID differs from index")
        actual = {str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()} - {"manifest.json"}
        if actual != set(manifest["files"]):
            raise ValueError(f"File set changed: {record['id']}")
        for relative, expected in manifest["files"].items():
            path = inside(base, relative)
            if digest(path) != expected:
                raise ValueError(f"File changed: {record['id']}/{relative}")
        print(json.dumps({"verified": record["id"], "files": len(manifest["files"])}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("seal", help="Archive a reviewed draft without overwriting a version")
    p.add_argument("--project", default=".")
    p.add_argument("--draft", required=True)
    p.add_argument("--version", required=True)
    p.add_argument("--job")
    p.add_argument("--parent-version", help="Explicit source version; otherwise use latest version for the same job")
    p.add_argument("--changes-file", required=True)
    p.add_argument("--visual-reviewed", action="store_true")
    p = sub.add_parser("verify", help="Verify archived files against the recorded hashes")
    p.add_argument("--project", default=".")
    p.add_argument("--version")
    args = parser.parse_args()
    try:
        seal(args) if args.command == "seal" else verify(args)
    except (ValueError, KeyError, FileNotFoundError, FileExistsError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    main()
