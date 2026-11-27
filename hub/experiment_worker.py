"""Fixed, single-process synthetic calibration worker; not a physics model."""
import argparse
import hashlib
import json
import random
import resource
import signal
import sys

MIB = 1024 * 1024


def apply_limits(cpu, memory, wall):
    # Linux RLIMIT_AS bounds virtual address space, not a cgroup RSS quota.
    resource.setrlimit(resource.RLIMIT_AS, (memory * MIB, memory * MIB))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(wall)


def calibrate(seed, samples, stop_hits):
    rng = random.Random(seed)
    hits = total = 0
    reason = "sample_budget"
    for index in range(1, samples + 1):
        value = rng.getrandbits(32)
        total = (total + value) % (1 << 64)
        hits += value < (1 << 31)
        if stop_hits and hits >= stop_hits:
            reason = "hit_target"
            break
    result = {"seed": seed, "samples": index, "hits": hits, "sum_mod64": total,
              "stop_reason": reason}
    result["result_sha256"] = hashlib.sha256(json.dumps(result, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("seed", "samples", "stop-hits", "cpu", "memory", "wall"):
        p.add_argument("--" + key, type=int, required=True)
    a = p.parse_args()
    if not (0 <= a.seed <= 2**32 - 1 and 1 <= a.samples <= 20_000_000
            and 0 <= a.stop_hits <= a.samples and 1 <= a.cpu <= 30
            and 64 <= a.memory <= 512 and 1 <= a.wall <= 60):
        return 2
    apply_limits(a.cpu, a.memory, a.wall)
    result = calibrate(a.seed, a.samples, a.stop_hits)
    usage = resource.getrusage(resource.RUSAGE_SELF)
    print(json.dumps({"result": result, "peak_rss_kib": usage.ru_maxrss,
                      "cpu_seconds": usage.ru_utime + usage.ru_stime,
                      "python": sys.version.split()[0],
                      "limits": {"cpu": list(resource.getrlimit(resource.RLIMIT_CPU)),
                                 "address_space": list(resource.getrlimit(resource.RLIMIT_AS))}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
