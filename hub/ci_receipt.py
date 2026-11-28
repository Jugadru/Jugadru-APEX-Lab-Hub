"""Read-only GitHub Actions receipt; fixed repository and workflow policy."""
import hashlib
import json
import os
import urllib.error
import urllib.request

from .orchestrator import REPOSITORIES, SHA, require, unique_object

REPO = REPOSITORIES["hub"]
WORKFLOW = ".github/workflows/hub-ci.yml"
WORKFLOW_ID = 432261422
BASE_STEPS = ("Test contracts and controlled subprocesses",
         "Run accessible cross-project DAG (Hub and public game)",
         "Verify persisted session from a separate process", "Upload evidence")
ADDITIONAL_STEPS = ("Verify real GitHub CI receipt (previous integrated revision)",
    "Run real bounded experiment queue", "Run real department gateway and GitHub receipt",
    "Run actual infrastructure health snapshot", "Build actual private offline dashboard",
    "Run real bounded engineering mission", "Audit completed mission evidence", "Run real department intelligence handoff", "Run real engineering knowledge memory", "Run real approved MCP stdio exchange", "Run real local code candidate construction", "Run real advanced geometry adapter", "Run real isolated Godot benchmark", "Run real Engineering Racecraft integration", "Run real multi-role collaboration", "Run real mission control snapshot", "Run real supervised development cycle")
# Historical exceptions only for exact integrated revisions used as regression anchors.
# Unknown or newer revisions require every current step; never infer policy from API output.
HISTORICAL = {
    "7b5a560093d8f445f084405c0fa21ad09d006bda": 0,
    "0b145f971b29fd78f0748e1a1fe5fc4a14a7a346": 2,
    "89b3104b6205f73f6eb5149519d43f7a6f200b3e": 3,
    "ee04b3c9bb593d9bb36652019be9f5c510a37c47": 6,
    "97abb9ef96492a54a4b3ea0567fe2bf29138b36f": 7,
    "5ffed5f6b88aa52d3ee302982317d23891215f9e": 8,
    "442bc2a8a7e2020dae31ff938e100ac70f4eb461": 9,
    "e8d750048af7da9c6cf609e46c470d4e166d844c": 10,
    "4532ada83c0e2cdf9c1e05e1bd42f73010db9729": 11,
    "b210af17eb79ba468b658fda995a30bf2e0f713d": 12,
    "4da1861dddaae44fdabc87a208fc9f2673c7bbe2": 13,
    "63e8f1ead0b69f4623e3e69fed3aa393b86d4116": 14,
    "c42e118e1396352e7a6fcd680c7651cbb69c7dfc": 15,
    "bb7b851b376f0f8d0ea91b6e10cc9d2c02e22fe3": 16,
}
STEPS = BASE_STEPS + ADDITIONAL_STEPS
LIMIT = 2_000_000


def required_steps(sha):
    return BASE_STEPS + ADDITIONAL_STEPS[:HISTORICAL.get(sha,len(ADDITIONAL_STEPS))]



class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def positive(value):
    return type(value) is int and 0 < value < 10**16


def fetch(path, token):
    require(bool(token), "APEX_CI_READ_TOKEN required")
    opener = urllib.request.build_opener(NoRedirect())
    req = urllib.request.Request("https://api.github.com/repos/" + REPO + path,
                                 headers={"Authorization": "Bearer " + token,
                                          "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28",
                                          "User-Agent": "APEX-Lab-Hub"})
    try:
        with opener.open(req, timeout=15) as response:
            raw = response.read(LIMIT + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError("GitHub API HTTP " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError):
        raise ValueError("GitHub API unavailable") from None
    require(len(raw) <= LIMIT, "CI API response too large")
    return json.loads(raw, object_pairs_hook=unique_object)


def receipt(run_id, sha, branch, token=None, reader=None):
    """Only push runs from same repo, exact SHA/branch and required jobs qualify."""
    require(positive(run_id) and isinstance(sha, str) and SHA.fullmatch(sha), "Invalid CI identity")
    reader = reader or (lambda p: fetch(p, token or os.environ.get("APEX_CI_READ_TOKEN")))
    run = reader("/actions/runs/" + str(run_id))
    require(isinstance(run, dict) and run.get("id") == run_id
            and run.get("head_sha") == sha and run.get("head_branch") == branch
            and run.get("event") == "push" and run.get("workflow_id") == WORKFLOW_ID
            and run.get("path") == WORKFLOW and run.get("repository", {}).get("full_name") == REPO
            and run.get("head_repository", {}).get("full_name") == REPO, "CI run identity mismatch")
    attempt = run.get("run_attempt")
    require(positive(attempt), "Invalid CI attempt")
    result = {"source": "github_rest", "repository": REPO, "workflow": WORKFLOW,
              "workflow_id": WORKFLOW_ID, "run_id": run_id, "attempt": attempt,
              "sha": sha, "branch": branch, "outcome": "INCONCLUSIVE", "jobs": [],
              "diagnostic": "Run not completed"}
    if run.get("status") != "completed":
        return result
    data = reader("/actions/runs/%s/attempts/%s/jobs?per_page=100" % (run_id, attempt))
    jobs = data.get("jobs")
    require(isinstance(jobs, list) and type(data.get("total_count")) is int
            and data["total_count"] == len(jobs) and len(jobs) <= 100, "Incomplete job listing")
    required = [j for j in jobs if j.get("name") == "contracts"]
    require(len(required) == 1, "Required CI job missing or ambiguous")
    job = required[0]
    require(positive(job.get("id")) and job.get("run_id") == run_id
            and job.get("run_attempt") == attempt and job.get("head_sha") == sha, "CI job identity mismatch")
    steps = job.get("steps")
    require(isinstance(steps, list), "Missing CI steps")
    selected = []
    for name in required_steps(sha):
        found = [s for s in steps if s.get("name") == name]
        require(len(found) <= 1, "Ambiguous required step")
        selected.append({"name": name, "status": found[0].get("status") if found else "missing",
                         "conclusion": found[0].get("conclusion") if found else None})
    result["jobs"] = [{"id": job["id"], "name": "contracts", "conclusion": job.get("conclusion"),
                       "steps": selected}]
    if run.get("conclusion") == "success" and job.get("conclusion") == "success" and job.get("status") == "completed" and all(
            s["status"] == "completed" and s["conclusion"] == "success" for s in selected):
        result.update(outcome="PASS", diagnostic="All required checks executed successfully")
    elif run.get("conclusion") in ("failure", "timed_out") and job.get("status") == "completed":
        failed = [s["name"] for s in selected if s["conclusion"] in ("failure", "timed_out")]
        result.update(outcome="FAIL", diagnostic="Failed checks: " + (", ".join(failed) or "setup or non-required step; inspect run"))
    else:
        result["diagnostic"] = "Cancelled, skipped, incomplete or inconsistent required checks"
    result["sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return result
