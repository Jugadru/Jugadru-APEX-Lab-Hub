#!/usr/bin/env python3
"""Independent APEX Lab Hub: reviewed, immutable multi-repository jobs."""
import argparse
import hashlib
import json
import os
import signal
from pathlib import Path
import re
import subprocess
import sys
import time

SCHEMA = 2
REPOSITORIES = {
    "hub": "Jugadru/Jugadru-APEX-Lab-Hub",
    "game": "Jugadru/APEX-Simulation",
    "hyper": "Jugadru/APEX-Hyper-Engine",
    "engineering": "Jugadru/APEX-Engineering-Lab",
    "architect": "Jugadru/apex-godot-simulation-architect",
}
MAX_TASKS = 16
MAX_MANIFEST = 64_000
MAX_SECONDS = 300
ID = re.compile(r"[a-z][a-z0-9_-]{0,47}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
# Only this reviewed registry can select executable code and arguments.
CAPABILITIES = {
    "repo.revision": {"targets": sorted(REPOSITORIES), "script": None,
                      "artifact": "revision.json", "args": [], "timeout": 15,
                      "permission": "read", "criterion": "exit_zero"},
    "source.inventory": {
        "targets": ["game"], "script": "tools/apex_source_inventory.py", "artifact": "source-inventory.json",
        "args": ["--check", "--output"], "timeout": 90,
        "permission": "read", "criterion": "exit_zero",
    },
    "architecture.audit": {
        "targets": ["game"], "script": "tools/apex_lab_architecture.py", "artifact": "architecture.json",
        "args": ["--output"], "timeout": 90,
        "permission": "read", "criterion": "exit_zero",
    },
    "foundation.catalog": {
        "targets": ["game"], "script": "tools/apex_foundation.py", "artifact": None,
        "args": ["validate"], "timeout": 30,
        "permission": "read", "criterion": "catalog_pass",
        "requires": ["docs/references/references.json", "docs/references/possibilities.json",
                     "docs/references/domains.json"],
    },
}
# Profile is selected by the trusted caller, never by task JSON.
PROFILES = {"research": {"read"}, "read": {"read"}, "development": {"read"},
            "graphics": {"read"}, "test": {"read"}, "publication": set(), "administration": set()}
TASK_FIELDS = {"id", "objective", "scope", "priority", "depends_on", "capability",
               "permissions", "acceptance", "timeout_seconds", "target"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(path):
    with path.open("rb") as stream:
        h = hashlib.sha256()
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
        return h.hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args],
                                   text=True, timeout=10).strip()


def checked_targets(bindings):
    """Bindings come from operator CLI, never task JSON; no network or clone."""
    targets = {}
    for name, path, sha in bindings:
        require(name in REPOSITORIES and name not in targets, "Unknown or duplicate target")
        require(SHA.fullmatch(sha), "Exact SHA required")
        root = Path(path).resolve()
        require(Path(git(root, "rev-parse", "--show-toplevel")).resolve() == root, "Not checkout root")
        require(git(root, "rev-parse", "HEAD") == sha, "Checkout SHA mismatch")
        urls = {"https://github.com/" + REPOSITORIES[name], "git@github.com:" + REPOSITORIES[name]}
        remote = git(root, "remote", "get-url", "origin").removesuffix(".git")
        require(remote in urls, "Unexpected origin for target")
        require(not git(root, "status", "--porcelain", "--untracked-files=all"), "Checkout is dirty")
        targets[name] = (root, sha)
    require(len({v[0] for v in targets.values()}) == len(targets), "Duplicate checkout root")
    return targets


def available(root, cap):
    if cap["script"] is None:
        return True
    paths = [cap["script"], *cap.get("requires", [])]
    return all((root / p).is_file() and (root / p).resolve() == root / p
               and git(root, "ls-files", "--error-unmatch", "--", p) == p for p in paths)


def discover(targets):
    rows = []
    for key, cap in sorted(CAPABILITIES.items()):
        for name in cap["targets"]:
            bound = name in targets
            rows.append({"id": key, "target": name, **cap,
                         "available": bound and available(targets[name][0], cap),
                         "sha": targets[name][1] if bound else None, "max_attempts": 1})
    return {"schema": SCHEMA, "repositories": REPOSITORIES, "capabilities": rows,
            "profiles": {k: sorted(v) for k, v in PROFILES.items()},
            "limitations": ["File presence is not execution proof. No remote dispatch or publication.",
                            "Architect remains private; no private source copied into Hub."]}


def unique_object(pairs):
    data = {}
    for k, v in pairs:
        require(k not in data, "Duplicate JSON key")
        data[k] = v
    return data


def read_manifest(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_MANIFEST + 1)
    require(len(raw) <= MAX_MANIFEST, "Manifest exceeds size limit")
    return json.loads(raw, object_pairs_hook=unique_object)


def plan(manifest, profile):
    require(profile in PROFILES, "Unknown operator profile")
    require(isinstance(manifest, dict) and set(manifest) == {"schema", "tasks"}
            and type(manifest["schema"]) is int and manifest["schema"] == SCHEMA,
            "Invalid manifest schema")
    rows = manifest["tasks"]
    require(isinstance(rows, list) and 0 < len(rows) <= MAX_TASKS, "Invalid task count")
    tasks = {}
    for task in rows:
        require(isinstance(task, dict) and set(task) == TASK_FIELDS, "Invalid task fields")
        ident = task["id"]
        require(isinstance(ident, str) and ID.fullmatch(ident) and ident not in tasks, "Invalid/duplicate task ID")
        for field in ("objective", "scope"):
            require(isinstance(task[field], str) and 0 < len(task[field].strip()) <= 500, "Invalid task text")
        require(type(task["priority"]) is int and 0 <= task["priority"] <= 100, "Invalid priority")
        deps = task["depends_on"]
        require(isinstance(deps, list) and all(isinstance(d, str) and ID.fullmatch(d) for d in deps)
                and len(set(deps)) == len(deps), "Invalid dependencies")
        key = task["capability"]
        require(isinstance(key, str) and key in CAPABILITIES, "Capability not allowlisted")
        cap = CAPABILITIES[key]
        require(task["target"] in cap["targets"], "Capability not allowed for target")
        require(task["permissions"] == [cap["permission"]] and cap["permission"] in PROFILES[profile],
                "Permission denied")
        require(task["acceptance"] == cap["criterion"], "Acceptance must match registry")
        seconds = task["timeout_seconds"]
        require(type(seconds) is int and 1 <= seconds <= cap["timeout"], "Invalid timeout")
        tasks[ident] = task
    require(sum(t["timeout_seconds"] for t in rows) <= MAX_SECONDS, "Session timeout budget exceeded")
    require(all(set(t["depends_on"]) <= tasks.keys() for t in rows), "Unknown dependency")
    order, remaining = [], set(tasks)
    while remaining:
        ready = [i for i in remaining if set(tasks[i]["depends_on"]) <= set(order)]
        require(bool(ready), "Dependency cycle")
        chosen = min(ready, key=lambda i: (-tasks[i]["priority"], i))
        order.append(chosen)
        remaining.remove(chosen)
    return [tasks[i] for i in order]


def default_manifest():
    def task(i, cap, deps, objective, target):
        entry = CAPABILITIES[cap]
        return {"id": i, "objective": objective, "scope": "exact checkout",
                "priority": 50, "depends_on": deps, "capability": cap, "target": target,
                "permissions": [entry["permission"]], "acceptance": entry["criterion"],
                "timeout_seconds": entry["timeout"]}
    return {"schema": SCHEMA, "tasks": [
        task("hub-revision", "repo.revision", [], "Verify Hub revision", "hub"),
        task("inventory", "source.inventory", ["hub-revision"], "Check game inventory", "game"),
        task("architecture", "architecture.audit", ["inventory"], "Inspect game architecture", "game"),
        task("hyper-revision", "repo.revision", ["hub-revision"], "Verify Hyper revision", "hyper"),
    ]}


def run_case(argv, log, timeout, root):
    # Fixed allowlisted tools only. A trusted checkout is not an OS sandbox.
    env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8",
           "PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null"}
    with log.open("w", encoding="utf-8") as stream:
        try:
            proc = subprocess.Popen(argv, cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                                    env=env, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return {"status": "BLOCKED", "reason": "timeout"}
        except OSError:
            return {"status": "BLOCKED", "reason": "executable unavailable"}
    return {"status": "PASS" if code == 0 else "FAIL", "exit": code}


def run(manifest, profile, targets, out, runner=None):
    out = out.resolve()
    ordered = plan(manifest, profile)
    require(not any(out.is_relative_to(root) or root.is_relative_to(out)
                    for root, sha in targets.values()), "Evidence must be outside all checkouts")
    out.mkdir(parents=True, exist_ok=False)
    runner = runner or run_case
    report = {"schema": SCHEMA, "profile": profile, "status": "BLOCKED",
              "targets": {k: {"repository": REPOSITORIES[k], "sha": v[1]} for k, v in targets.items()},
              "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True,
                  ensure_ascii=True, separators=(",", ":")).encode()).hexdigest(),
              "capabilities": discover(targets), "tasks": [
                  {**t, "status": "NOT_RUN", "attempts": 0, "evidence": []} for t in ordered],
              "limitations": ["Sequential synchronous execution; one attempt per task.",
                              "Supplemental diagnostics, not official FAST or FULL.",
                              "No CPU/RSS isolation, remote dispatch or asynchronous worker.",
                              "Trusted checkouts required; task JSON cannot authorize code."]}
    def save():
        tmp = out / "report.tmp"
        tmp.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
        tmp.replace(out / "report.json")
    save()
    states = {}
    deadline = time.monotonic() + MAX_SECONDS
    try:
        for task in report["tasks"]:
            cap = CAPABILITIES[task["capability"]]
            if any(states[d] != "PASS" for d in task["depends_on"]):
                task.update(status="BLOCKED", reason="dependency did not pass")
            elif task["target"] not in targets:
                task.update(status="BLOCKED", reason="target checkout not supplied")
            elif not available(targets[task["target"]][0], cap):
                task.update(status="BLOCKED", reason="capability unavailable at exact checkout")
            elif deadline - time.monotonic() < 1:
                task.update(status="BLOCKED", reason="session budget exhausted")
            else:
                root, head = targets[task["target"]]
                work = out / task["id"]
                work.mkdir()
                log = work / "execution.log"
                task.update(status="BLOCKED", attempts=1, reason="execution interrupted or incomplete")
                save()
                try:
                    require(git(root, "rev-parse", "HEAD") == head and
                            not git(root, "status", "--porcelain", "--untracked-files=all"), "Checkout changed")
                    if cap["script"] is None:
                        (work / cap["artifact"]).write_text(json.dumps(
                            {"repository": REPOSITORIES[task["target"]], "sha": head}) + "\n")
                        result = {"status": "PASS", "exit": 0}
                    else:
                        argv = [sys.executable, "-E", "-B", cap["script"], *cap["args"]]
                        if cap["artifact"]:
                            argv.append(str(work / cap["artifact"]))
                        result = runner(argv, log, min(task["timeout_seconds"],
                                        max(1, int(deadline - time.monotonic()))), root)
                    require(git(root, "rev-parse", "HEAD") == head and
                            not git(root, "status", "--porcelain", "--untracked-files=all"), "Checkout changed during job")
                    status = result["status"]
                    require(status in ("PASS", "FAIL", "BLOCKED"), "Unknown executor result")
                    task.update(status=status, reason=result.get("reason", ""))
                    task["executor"] = result
                    if status == "PASS":
                        if cap["artifact"]:
                            require((work / cap["artifact"]).is_file() and not (work / cap["artifact"]).is_symlink(), "Expected artifact missing")
                        if cap["criterion"] == "catalog_pass":
                            require(log.stat().st_size <= MAX_MANIFEST, "Catalog response oversized")
                            data = json.loads(log.read_text(encoding="utf-8"))
                            if not (isinstance(data, dict) and data.get("status") == "PASS"
                                    and data.get("scope") == "catalog structure only"):
                                task.update(status="FAIL", reason="Catalog acceptance not met")
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
                    task.update(status="BLOCKED", reason=type(error).__name__ + ": " + str(error)[:200])
                for p in sorted(work.iterdir()):
                    if p.is_file() and not p.is_symlink():
                        task["evidence"].append({"path": str(p.relative_to(out)), "bytes": p.stat().st_size,
                                                 "sha256": digest(p)})
            states[task["id"]] = task["status"]
            save()
        report["status"] = ("FAIL" if "FAIL" in states.values() else
                            "PASS" if all(s == "PASS" for s in states.values()) else "BLOCKED")
    finally:
        save()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    discover_parser = sub.add_parser("capabilities")
    discover_parser.add_argument("--checkout", nargs=3, action="append", default=[], metavar=("TARGET", "PATH", "SHA"))
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("manifest", type=Path)
        p.add_argument("--profile", choices=PROFILES, default="read")
        if name == "run":
            p.add_argument("--checkout", nargs=3, action="append", required=True, metavar=("TARGET", "PATH", "SHA"))
            p.add_argument("--trusted-checkouts", action="store_true", required=True,
                           help="Operator confirms reviewed code and isolated execution environment")
            p.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.mode == "capabilities":
            result = discover(checked_targets(args.checkout))
        elif args.mode == "plan":
            result = {"schema": SCHEMA, "status": "NOT_RUN", "tasks": plan(read_manifest(args.manifest), args.profile)}
        else:
            result = run(read_manifest(args.manifest), args.profile, checked_targets(args.checkout), args.output)
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0 if args.mode != "run" or result["status"] == "PASS" else 1
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print("APEX_ORCHESTRATION_BLOCKED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
