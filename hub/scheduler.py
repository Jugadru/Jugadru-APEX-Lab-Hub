"""Persistent single-worker experiment queue with bounded Linux calibration."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

from .orchestrator import SHA, checked_targets, digest, git, require, unique_object
from .registry import Registry, encode, ident, now

FIELDS = {"schema", "capability", "seed", "samples", "stop_hits", "priority",
          "cpu_seconds", "memory_mib", "wall_seconds"}
WORKER = "hub/experiment_worker.py"
GLOBAL_LOCK = "0" * 32
MAX_ACTIVE = 64
TERMINAL = {"PASS", "FAIL", "BLOCKED", "CANCELLED", "INTERRUPTED"}
MAX_OUTPUT = 65536


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def contract(data):
    require(isinstance(data, dict) and set(data) == FIELDS and type(data["schema"]) is int
            and data["schema"] == 1 and data["capability"] == "synthetic.calibration",
            "Unsupported experiment contract")
    for key, low, high in (("seed", 0, 2**32-1), ("samples", 1, 20_000_000),
                           ("priority", 0, 100), ("cpu_seconds", 1, 30),
                           ("memory_mib", 64, 512), ("wall_seconds", 1, 60)):
        require(integer(data[key], low, high), "Invalid experiment " + key)
    require(integer(data["stop_hits"], 0, data["samples"]), "Invalid stop condition")
    return dict(data)


def load(path, limit=MAX_OUTPUT):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "Input exceeds limit")
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def sha(value):
    require(isinstance(value, str) and SHA.fullmatch(value), "Exact SHA required")
    return value


def accept(data, spec):
    require(isinstance(data, dict) and set(data) == {"result", "peak_rss_kib", "cpu_seconds", "python", "limits"},
            "Invalid worker output")
    result = data["result"]
    require(isinstance(result, dict) and set(result) == {"seed", "samples", "hits", "sum_mod64",
                 "stop_reason", "result_sha256"}, "Invalid result")
    require(type(result["seed"]) is int and result["seed"] == spec["seed"]
            and integer(result["samples"], 1, spec["samples"])
            and integer(result["hits"], 0, result["samples"])
            and integer(result["sum_mod64"], 0, 2**64-1), "Invalid result counts")
    if result["stop_reason"] == "hit_target":
        require(spec["stop_hits"] > 0 and result["hits"] == spec["stop_hits"], "Invalid stop condition")
    else:
        require(result["stop_reason"] == "sample_budget" and result["samples"] == spec["samples"]
                and (not spec["stop_hits"] or result["hits"] < spec["stop_hits"]), "Invalid sample budget")
    payload = {k: v for k, v in result.items() if k != "result_sha256"}
    expected = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    require(result["result_sha256"] == expected, "Result hash mismatch")
    require(data["limits"] == {"cpu": [spec["cpu_seconds"], spec["cpu_seconds"] + 1],
                               "address_space": [spec["memory_mib"] * 1048576] * 2}, "Worker resource limit mismatch")
    require(integer(data["peak_rss_kib"], 1, 2**31-1) and type(data["cpu_seconds"]) in (int, float)
            and math.isfinite(data["cpu_seconds"]) and data["cpu_seconds"] >= 0
            and isinstance(data["python"], str), "Invalid resource telemetry")
    return data


class Scheduler:
    def __init__(self, root):
        self.store = Registry(root)
        with self.store.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, created TEXT NOT NULL, priority INTEGER NOT NULL, state TEXT NOT NULL, snapshot TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS experiment_events (id TEXT NOT NULL REFERENCES experiments(id), seq INTEGER NOT NULL, at TEXT NOT NULL, action TEXT NOT NULL, snapshot TEXT NOT NULL, PRIMARY KEY(id,seq))")

    def _get(self, db, sid):
        row = db.execute("SELECT snapshot FROM experiments WHERE id=?", (ident(sid),)).fetchone()
        require(row is not None, "Unknown experiment")
        return json.loads(row[0])

    def get(self, sid):
        with self.store.connect() as db:
            return self._get(db, sid)

    def _save(self, db, row, action):
        payload = encode(row)
        db.execute("UPDATE experiments SET state=?,snapshot=? WHERE id=?", (row["state"], payload, row["id"]))
        seq = db.execute("SELECT MAX(seq)+1 FROM experiment_events WHERE id=?", (row["id"],)).fetchone()[0]
        db.execute("INSERT INTO experiment_events VALUES (?,?,?,?,?)", (row["id"], seq, now(), action, payload))

    def enqueue(self, spec, revision):
        spec = contract(spec)
        row = {"id": uuid.uuid4().hex, "created": now(), "schema": 1, "hub_sha": sha(revision),
               "spec": spec, "state": "QUEUED", "attempts": 0, "cancel_requested": False}
        payload = encode(row)
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            require(db.execute("SELECT COUNT(*) FROM experiments WHERE state IN ('QUEUED','RUNNING')").fetchone()[0] < MAX_ACTIVE,
                    "Experiment queue full")
            db.execute("INSERT INTO experiments VALUES (?,?,?,?,?)", (row["id"], row["created"], spec["priority"], "QUEUED", payload))
            db.execute("INSERT INTO experiment_events VALUES (?,?,?,?,?)", (row["id"], 1, now(), "enqueue", payload))
        return row

    def list(self):
        with self.store.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT snapshot FROM experiments ORDER BY priority DESC,created,id LIMIT 100")]

    def history(self, sid):
        self.get(sid)
        with self.store.connect() as db:
            return [{"seq": r["seq"], "at": r["at"], "action": r["action"], "snapshot": json.loads(r["snapshot"])}
                    for r in db.execute("SELECT * FROM experiment_events WHERE id=? ORDER BY seq", (sid,))]

    def cancel(self, sid):
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self._get(db, sid)
            require(row["state"] in {"QUEUED", "RUNNING"}, "Experiment already terminal")
            if not row["cancel_requested"]:
                row["cancel_requested"] = True
                if row["state"] == "QUEUED":
                    row.update(state="CANCELLED", reason="cancelled before launch")
                self._save(db, row, "cancel")
            return row

    def recover(self, sid):
        with self.store.lock(GLOBAL_LOCK):
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = self._get(db, sid)
                require(row["state"] == "RUNNING", "Only abandoned running experiment can be recovered")
                row.update(state="INTERRUPTED", reason="No replay; inspect evidence and allow orphan limits to expire")
                self._save(db, row, "recover")
                return row

    def run_next(self, checkout, revision):
        root, head = checked_targets([("hub", checkout, sha(revision))])["hub"]
        require(not self.store.root.is_relative_to(root) and not root.is_relative_to(self.store.root),
                "Registry must be outside checkout")
        path = root / WORKER
        require(path.is_file() and path.resolve() == path
                and git(root, "ls-files", "--error-unmatch", "--", WORKER) == WORKER, "Reviewed worker missing")
        with self.store.lock(GLOBAL_LOCK):
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                require(not db.execute("SELECT 1 FROM experiments WHERE state='RUNNING'").fetchone(),
                        "Recover abandoned experiment first")
                item = db.execute("SELECT snapshot FROM experiments WHERE state='QUEUED' ORDER BY priority DESC,created,id LIMIT 1").fetchone()
                if item is None:
                    return {"state": "EMPTY"}
                row = json.loads(item[0])
                require(row["hub_sha"] == head, "Next experiment requires different exact Hub SHA")
                row.update(state="RUNNING", attempts=1, worker_sha256=digest(path), started=now())
                self._save(db, row, "claim")
            try:
                state, reason, telemetry, evidence = self._execute(row, root, path)
            except (OSError, ValueError, subprocess.SubprocessError, KeyError, TypeError) as exc:
                state, reason, telemetry, evidence = "BLOCKED", type(exc).__name__ + ": " + str(exc)[:160], None, []
            with self.store.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self._get(db, row["id"])
                if current["cancel_requested"]:
                    state, reason = "CANCELLED", "operator cancellation"
                current.update(state=state, reason=reason, telemetry=telemetry,
                               evidence=evidence, finished=now())
                self._save(db, current, "finish")
                return current

    def _execute(self, row, root, path):
        spec = row["spec"]
        out = self.store.root / ("experiment-" + row["id"])
        out.mkdir(mode=0o700, exist_ok=False)
        log = out / "worker.json"
        argv = [sys.executable, "-I", "-B", str(path), "--seed", str(spec["seed"]),
                "--samples", str(spec["samples"]), "--stop-hits", str(spec["stop_hits"]),
                "--cpu", str(spec["cpu_seconds"]), "--memory", str(spec["memory_mib"]),
                "--wall", str(spec["wall_seconds"])]
        state, reason, telemetry = "BLOCKED", "worker not completed", None
        started = time.monotonic()
        with log.open("xb") as stream:
            proc = subprocess.Popen(argv, cwd=root, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
                                    stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while proc.poll() is None:
                    if self.get(row["id"])["cancel_requested"]:
                        reason = "operator cancellation"
                        break
                    if time.monotonic() - started >= spec["wall_seconds"]:
                        reason = "wall timeout"
                        break
                    time.sleep(0.025)
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                elif proc.returncode == 0:
                    try:
                        telemetry = accept(load(log), spec)
                        state, reason = "PASS", telemetry["result"]["stop_reason"]
                    except (ValueError, TypeError, KeyError) as exc:
                        state, reason = "FAIL", "invalid worker output: " + str(exc)[:100]
                elif proc.returncode in (-signal.SIGXCPU, -signal.SIGALRM, -signal.SIGKILL, -signal.SIGXFSZ):
                    reason = "resource signal " + str(-proc.returncode)
                else:
                    state, reason = "FAIL", "worker exit " + str(proc.returncode)
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
        require(git(root, "rev-parse", "HEAD") == row["hub_sha"]
                and not git(root, "status", "--porcelain", "--untracked-files=all")
                and digest(path) == row["worker_sha256"], "Checkout changed during experiment")
        evidence = [{"path": str(log.relative_to(self.store.root)), "sha256": digest(log), "bytes": log.stat().st_size}]
        return state, reason, telemetry, evidence


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", required=True, type=Path)
    sub = p.add_subparsers(dest="mode", required=True)
    e = sub.add_parser("enqueue")
    e.add_argument("manifest", type=Path)
    e.add_argument("--sha", required=True)
    sub.add_parser("list")
    for mode in ("show", "history", "cancel", "recover"):
        sub.add_parser(mode).add_argument("id")
    run = sub.add_parser("run-next")
    run.add_argument("--checkout", required=True)
    run.add_argument("--sha", required=True)
    run.add_argument("--trusted-checkout", action="store_true", required=True)
    a = p.parse_args()
    try:
        s = Scheduler(a.registry)
        if a.mode == "enqueue":
            out = s.enqueue(load(a.manifest), a.sha)
        elif a.mode == "run-next":
            out = s.run_next(a.checkout, a.sha)
        elif a.mode == "list":
            out = s.list()
        else:
            out = getattr(s, "get" if a.mode == "show" else a.mode)(a.id)
        print(json.dumps(out, indent=2, ensure_ascii=True))
        return 1 if a.mode == "run-next" and out["state"] not in ("PASS", "EMPTY") else 0
    except (ValueError, OSError, sqlite3.Error, subprocess.SubprocessError, TypeError, KeyError) as exc:
        print("APEX_EXPERIMENT_BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
