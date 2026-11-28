"""Bounded operator-led development workflow. No code generation, writes or merge."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
import uuid

from . import ci_receipt as ci
from .orchestrator import checked_targets, git, require, SHA
from .registry import Registry, encode, ident, now

MAX_REPAIRS = 2
PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z")
BRANCH = re.compile(r"feature/[a-z0-9][a-z0-9_-]{0,63}\Z")
PROTECTED = (".github/", "AGENTS.md", "hub/ci_receipt.py", "hub/development.py",
             "hub/registry.py", "hub/orchestrator.py", "hub/__init__.py",
             "hub/graphics.py", "hub/scheduler.py", "hub/experiment_worker.py", "hub/research.py", "hub/gateway.py", "hub/foundation.py", "hub/health.py", "hub/dashboard.py", "hub/mission.py", "hub/departments.py", "hub/knowledge.py", "hub/ai_gateway.py", "hub/ai_provider.py", "hub/mcp_server.py", "tests_mcp_smoke.py", "hub/code_builder.py", "tests_code_smoke.py", "hub/geometry.py", "tests_graphics_smoke.py", "hub/benchmark.py", "hub/benchmark_scene.gd", "tests_benchmark_smoke.py", "tests_racecraft_smoke.py", "hub/collaboration.py", "tests_collaboration_smoke.py", "hub/mission_control.py", "tests_control_smoke.py", "hub/development_cycle.py", "tests_cycle_smoke.py", "tests_system_audit_smoke.py")


def note(value):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 2000, "Invalid note")
    return value.strip()


def valid_sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "Exact SHA required")
    return value


def scope(paths):
    require(isinstance(paths, list) and 0 < len(paths) <= 64, "Scope needs 1..64 exact paths")
    require(all(isinstance(p, str) and len(p) <= 240 and PATH.fullmatch(p)
                and all(part not in (".", "..", ".git") for part in p.split("/"))
                and not any(p == x or p.startswith(x) for x in PROTECTED) for p in paths),
            "Invalid or protected scope path")
    require(len(set(paths)) == len(paths), "Duplicate scope path")
    return sorted(paths)


class Development:
    def __init__(self, root):
        self.store = Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS development (id TEXT PRIMARY KEY, state TEXT NOT NULL, snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS development_events (id TEXT NOT NULL REFERENCES development(id), seq INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(id,seq))")

    def create(self, objective, base, paths):
        row = {"schema": 1, "id": uuid.uuid4().hex, "target": "hub", "objective": note(objective),
               "base": valid_sha(base), "scope": scope(paths), "state": "REQUESTED",
               "repairs": 0, "candidates": [], "merge_authorized": False}
        payload = encode(row)
        with self.store.connect() as db:
            db.execute("INSERT INTO development VALUES (?,?,?)", (row["id"], row["state"], payload))
            db.execute("INSERT INTO development_events VALUES (?,?,?,?,?)", (row["id"], 1, now(), "request", payload))
        return row

    def get(self, sid):
        with self.store.connect() as db:
            row = db.execute("SELECT snapshot FROM development WHERE id=?", (ident(sid),)).fetchone()
            require(row is not None, "Unknown development request")
            return json.loads(row[0])

    def history(self, sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq": r["seq"], "at": r["at"], "action": r["action"], "snapshot": json.loads(r["snapshot"])}
                    for r in db.execute("SELECT * FROM development_events WHERE id=? ORDER BY seq", (sid,))]

    def list(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM development ORDER BY rowid DESC LIMIT 100")]

    def change(self, sid, allowed, action, apply):
        # Atomic compare-and-transition; concurrent operator cannot overwrite a newer state.
        with self.store.lock(sid):
            row = self.get(sid)
            require(row["state"] in allowed, "Invalid development transition")
            apply(row)
            payload = encode(row)
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                seq = db.execute("SELECT MAX(seq)+1 FROM development_events WHERE id=?", (sid,)).fetchone()[0]
                db.execute("UPDATE development SET state=?,snapshot=? WHERE id=?", (row["state"], payload, sid))
                db.execute("INSERT INTO development_events VALUES (?,?,?,?,?)", (sid, seq, now(), action, payload))
            return row

    def audit(self, sid, checkout):
        def apply(row):
            root, _ = checked_targets([("hub", checkout, row["base"])])["hub"]
            rules = git(root, "rev-parse", row["base"] + ":AGENTS.md")
            workflow = git(root, "rev-parse", row["base"] + ":" + ci.WORKFLOW)
            row.update(state="AUDITED", audit={"rules_blob": rules, "workflow_blob": workflow,
                                               "base": row["base"], "scope": row["scope"]})
        return self.change(sid, {"REQUESTED"}, "audit", apply)

    def plan(self, sid, description):
        return self.change(sid, {"AUDITED"}, "plan",
                           lambda r: r.update(state="PLANNED", plan=note(description)))

    def candidate(self, sid, checkout, sha):
        valid_sha(sha)
        def apply(row):
            root, _ = checked_targets([("hub", checkout, sha)])["hub"]
            branch = git(root, "symbolic-ref", "--short", "HEAD")
            require(BRANCH.fullmatch(branch), "Candidate must be on isolated feature branch")
            require(sha != row["base"] and sha not in [c["sha"] for c in row["candidates"]], "New candidate SHA required")
            require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", row["base"], sha],
                                   timeout=10, check=False).returncode == 0, "Base not ancestor of candidate")
            if row["candidates"]:
                previous = row["candidates"][-1]
                require(branch == previous["branch"], "Repair branch changed")
                require(subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", previous["sha"], sha],
                                       timeout=10, check=False).returncode == 0, "Repair must descend from previous candidate")
            # NUL-delimited raw paths: never strip whitespace or unquote Git filenames.
            raw = subprocess.check_output(["git", "-C", str(root), "diff", "--name-only",
                                           "--no-renames", "--no-ext-diff", "-z", row["base"], sha], timeout=10)
            changed = raw.decode("utf-8").split("\0")
            changed = [p for p in changed if p]
            require(bool(changed) and set(changed) <= set(row["scope"]), "Candidate exceeds reviewed scope")
            require(git(root, "rev-parse", sha + ":" + ci.WORKFLOW) == row["audit"]["workflow_blob"]
                    and git(root, "rev-parse", sha + ":AGENTS.md") == row["audit"]["rules_blob"], "Gate or operating rules changed")
            if row["candidates"]:
                row["repairs"] += 1
            row["candidates"].append({"sha": sha, "branch": branch, "changed": sorted(changed), "receipts": []})
            row.update(state="AWAITING_CI")
        return self.change(sid, {"PLANNED", "REPAIR_READY"}, "candidate", apply)

    def check_ci(self, sid, run_id, reader=None):
        def apply(row):
            candidate = row["candidates"][-1]
            receipt = ci.receipt(run_id, candidate["sha"], candidate["branch"], reader=reader)
            if receipt["outcome"] == "INCONCLUSIVE":
                row["last_observation"] = receipt
                return
            require(not candidate["receipts"], "Candidate already has terminal receipt")
            candidate["receipts"].append(receipt)
            row.update(state="REVIEW" if receipt["outcome"] == "PASS" else "DIAGNOSE")
        return self.change(sid, {"AWAITING_CI"}, "ci", apply)

    def diagnose(self, sid, description):
        def apply(row):
            row["candidates"][-1]["diagnosis"] = note(description)
            row.update(state="REPAIR_READY" if row["repairs"] < MAX_REPAIRS else "BLOCKED")
        return self.change(sid, {"DIAGNOSE"}, "diagnose", apply)

    def approve(self, sid, sha, description):
        def apply(row):
            require(row["candidates"][-1]["sha"] == valid_sha(sha), "Approval SHA mismatch")
            row.update(state="APPROVED", review={"sha": sha, "note": note(description), "at": now(),
                                               "authority": "local operator declaration, not GitHub identity"})
        return self.change(sid, {"REVIEW"}, "approve", apply)

    def draft(self, sid):
        row = self.get(sid)
        require(row["state"] in {"REVIEW", "APPROVED"}, "Passing candidate required")
        c = row["candidates"][-1]
        receipt = c["receipts"][-1]
        return {"title": row["objective"][:120], "head": c["branch"], "head_sha": c["sha"], "base": "main",
                "base_sha": row["base"], "draft": True, "body": "Hub development request " + sid +
                "\nExact candidate: " + c["sha"] + "\nCI: https://github.com/" + ci.REPO + "/actions/runs/" +
                str(receipt["run_id"]) + "\nChanged files:\n" + "\n".join(c["changed"]) +
                "\nHuman review required. Verify current remote base/head before opening PR.",
                "review_state": row["state"], "merge_authorized": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", required=True, type=Path)
    sub = p.add_subparsers(dest="mode", required=True)
    create = sub.add_parser("request")
    create.add_argument("--objective", required=True)
    create.add_argument("--base", required=True)
    create.add_argument("--path", action="append", required=True)
    sub.add_parser("list")
    for mode in ("show", "history", "audit", "plan", "candidate", "ci", "diagnose", "approve", "draft"):
        q = sub.add_parser(mode)
        q.add_argument("id")
        if mode in ("audit", "candidate"):
            q.add_argument("--checkout", required=True)
        if mode in ("candidate", "approve"):
            q.add_argument("--sha", required=True)
        if mode in ("plan", "diagnose", "approve"):
            q.add_argument("--note", required=True)
        if mode == "ci":
            q.add_argument("--run-id", required=True, type=int)
    args = p.parse_args()
    try:
        d = Development(args.registry)
        if args.mode == "request":
            out = d.create(args.objective, args.base, args.path)
        elif args.mode == "list":
            out = d.list()
        elif args.mode == "show":
            out = d.get(args.id)
        elif args.mode == "audit":
            out = d.audit(args.id, args.checkout)
        elif args.mode == "candidate":
            out = d.candidate(args.id, args.checkout, args.sha)
        elif args.mode == "ci":
            out = d.check_ci(args.id, args.run_id)
        elif args.mode == "approve":
            out = d.approve(args.id, args.sha, args.note)
        elif args.mode in ("plan", "diagnose"):
            out = getattr(d, args.mode)(args.id, args.note)
        else:
            out = getattr(d, args.mode)(args.id)
        print(json.dumps(out, indent=2, ensure_ascii=True))
        return 0
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError, KeyError, TypeError) as exc:
        print("APEX_DEVELOPMENT_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
