#!/usr/bin/env python3
"""busyness.py — how busy each node class and partition really is, from Slurm accounting.

Runs ON A HEAD NODE (fed on stdin by hpc.sh:  ssh <host> 'python3 - --days 7' < busyness.py).
Reads `sacct -a` (all users' jobs), `sinfo`, `sacctmgr`, `scontrol`; writes nothing.

  python3 - [--days N] [--json] [--all]

Outputs, restricted to the partitions your account may use (--all = every partition):
  * occupancy % of each node class (nodes are exclusive, so a job owns its nodes for its
    whole run): overall, per weekday, and the least-busy 4-hour windows,
  * queue wait Submit -> Start per partition: median / p90 and per-weekday median,
  * demand right now: pending jobs per partition, busy nodes per class.
"""
import argparse, datetime as dt, json, os, re, statistics, subprocess, sys
from collections import defaultdict

VRAM = {"l40": 48, "l40s": 48, "a40": 48, "a100": 40, "h200nvl": 141, "rtx6000pro": 96,
        "rtx-6000": 24, "rtx-2080ti": 8, "t4": 16}
FAMILY = {"l40": "lovelace", "l40s": "lovelace", "a40": "ampere", "a100": "ampere", "h200nvl": "hopper",
          "rtx6000pro": "blackwell", "rtx-6000": "turing", "rtx-2080ti": "turing", "t4": "turing"}
BUCKETS = 6  # per day -> 4-hour windows
WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
T = "%Y-%m-%dT%H:%M:%S"


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout


def parse_gres(g):
    if not g.startswith("gpu:"):
        return "-", 0
    rest = g[4:]
    fam = rest.split(":")[0]
    cnt = rest.split(":")[1] if ":" in rest else "0"
    m = re.match(r"\d+", cnt)
    return fam, int(m.group(0)) if m else 0


def model_of(features):
    tags = features.split(",")
    for m in VRAM:
        if m in tags:
            return m
    return "-"


def expand(hostlist):
    """'hpc-node[03,09-12],ctit086' -> 12 names (commas inside brackets are not separators)"""
    out = []
    for m in re.finditer(r"([^\[\],]+)\[([^\]]*)\]|([^\[\],]+)", hostlist):
        if m.group(1):
            pre, rng = m.group(1), m.group(2)
            for r in rng.split(","):
                if "-" in r:
                    a, b = r.split("-")
                    for i in range(int(a), int(b) + 1):
                        out.append(pre + str(i).zfill(len(a)))
                else:
                    out.append(pre + r)
        else:
            out.append(m.group(3))
    return out


def parse_t(s):
    try:
        return dt.datetime.strptime(s, T)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="ignore partition permissions")
    a = ap.parse_args()
    user = os.environ.get("USER", "")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    win_start = now - dt.timedelta(days=a.days)

    # --- partitions this account may use ---------------------------------------
    allowed = set()
    accts = {l.strip() for l in sh(f"sacctmgr -nP show assoc user={user} format=account").splitlines() if l.strip()}
    for block in sh("scontrol show partition").split("\n\n"):
        m = re.search(r"PartitionName=(\S+)", block)
        if not m:
            continue
        aa = re.search(r"AllowAccounts=(\S+)", block)
        if a.all or not aa or aa.group(1) == "ALL" or accts & set(aa.group(1).split(",")):
            allowed.add(m.group(1))
    if not allowed:
        sys.exit("no partitions found for this account")

    # --- node classes: node -> (class key, partitions) ----------------------------
    node_class, node_parts = {}, defaultdict(set)
    for line in sh('sinfo -h -N -o "%N|%c|%m|%G|%f|%P"').splitlines():
        f = line.split("|")
        if len(f) != 6:
            continue
        n, c, m, g, feat, parts = f
        node_parts[n] |= {p.strip("*") for p in parts.split(",")}
        if node_parts[n] & allowed:
            fam, gn = parse_gres(g)
            node_class[n] = (c, int(m) // 1024, gn, fam, model_of(feat), feat)
    classes = defaultdict(list)  # key -> nodes
    for n, key in node_class.items():
        classes[key].append(n)
    if not classes:
        sys.exit("no nodes in the allowed partitions")

    # --- jobs -----------------------------------------------------------------------
    jobs = sh(f"sacct -a -S {win_start.strftime('%Y-%m-%dT%H:%M:%S')} -X -n -P "
              "-o JobID,Partition,State,Submit,Start,End,NodeList").splitlines()
    intervals = defaultdict(list)   # node -> [(start, end)]
    waits = defaultdict(list)       # partition -> minutes
    waits_dow = defaultdict(list)   # (partition, weekday) -> minutes
    n_jobs = n_used = 0
    for line in jobs:
        f = line.split("|")
        if len(f) < 7:
            continue
        _, part, state, submit, start, end, nodes = f[:7]
        if not start:
            continue
        n_jobs += 1
        s = parse_t(start)
        if s is None:
            continue
        e = parse_t(end) if end else None
        if e is None:  # RUNNING or unknown end
            e = now
        s, e = max(s, win_start), min(e, now)
        sub = parse_t(submit)
        if sub is not None and s > sub and part in allowed:
            w = (s - sub).total_seconds() / 60
            waits[part].append(w)
            waits_dow[(part, sub.weekday())].append(w)
        if part not in allowed:
            continue
        for n in expand(nodes):
            if n in node_class and e > s:
                intervals[n].append((s, e))
                n_used += 1

    # --- occupancy per class: overall, per weekday, per (weekday, 4h bucket) --------------
    def split_into(acc_dow, acc_bkt, s, e):
        """add the minutes of [s, e) to the per-weekday and per-(weekday, 4h bucket) accumulators"""
        while s < e:
            bk = s.hour // (24 // BUCKETS)
            bk_end = dt.datetime.combine(s.date(), dt.time(0)) + dt.timedelta(hours=(bk + 1) * 24 // BUCKETS)
            hi = min(e, bk_end)                      # bucket ends also end the day
            mins = (hi - s).total_seconds() / 60
            acc_dow[s.weekday()] += mins
            acc_bkt[(s.weekday(), bk)] += mins
            s = hi

    cap_dow, cap_bkt = defaultdict(float), defaultdict(float)
    split_into(cap_dow, cap_bkt, win_start, now)
    cap_total = sum(cap_dow.values())

    def merged(iv):
        out = []
        for s, e in sorted(iv):
            if out and s <= out[-1][1]:
                out[-1][1] = max(out[-1][1], e)
            else:
                out.append([s, e])
        return out

    rows = []
    for key, nodes in sorted(classes.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        busy_dow, busy_bkt = defaultdict(float), defaultdict(float)
        for n in nodes:
            for s, e in merged(intervals.get(n, [])):
                split_into(busy_dow, busy_bkt, s, e)
        total = sum(busy_dow.values())
        nn = len(nodes)   # capacity scales with the class size; nodes are exclusive, so busy minutes add up per node
        occ = 100.0 * total / (cap_total * nn) if cap_total else 0.0
        dow = [100.0 * busy_dow[d] / (cap_dow[d] * nn) if cap_dow[d] else None for d in range(7)]
        bkt = {(d, b): (100.0 * busy_bkt[(d, b)] / (cap_bkt[(d, b)] * nn) if cap_bkt.get((d, b)) else None) for d in range(7) for b in range(BUCKETS)}
        best = sorted((v, d, b) for (d, b), v in bkt.items() if v is not None)[:3]
        c, mem, gn, fam, model, feat = key
        rows.append({
            "class": model if model != "-" else "(cpu)", "vram": VRAM.get(model), "gpus_per_node": gn, "family": fam,
            "threads": int(c), "ram_gb": mem, "nodes": len(nodes), "occupancy_pct": round(occ),
            "occupancy_by_weekday": {WEEK[d]: (round(v) if v is not None else None) for d, v in enumerate(dow)},
            "least_busy_windows": [f"{WEEK[d]} {b * 24 // BUCKETS:02d}-{(b + 1) * 24 // BUCKETS:02d} ({round(v)}%)" for v, d, b in best],
            "busy_now": sum(1 for n in nodes if intervals.get(n) and max(i[1] for i in intervals[n]) >= now),
        })

    # --- queue wait per partition ------------------------------------------------------------
    def pct(v, p):
        if not v:
            return None
        v = sorted(v)
        return v[min(len(v) - 1, int(p * len(v)))]

    def fmt_h(m):
        if m is None:
            return "-"
        m = int(round(m))
        return f"{m // 1440}d{(m % 1440) // 60:02d}h" if m >= 1440 else f"{m // 60}h{m % 60:02d}"

    qrows = []
    for part in sorted(allowed):
        v = waits.get(part, [])
        qrows.append({"partition": part, "jobs_started": len(v), "median_wait": fmt_h(statistics.median(v)) if v else "-",
                      "p90_wait": fmt_h(pct(v, 0.9)) if v else "-",
                      "median_wait_by_weekday": {WEEK[d]: fmt_h(statistics.median(waits_dow[(part, d)])) if waits_dow.get((part, d)) else "-" for d in range(7)}})

    pending = defaultdict(int)
    for line in sh("squeue -h -t PD -o %P").splitlines():
        for p in line.split(","):            # a job may request several partitions; count each
            pending[p.strip("*")] += 1

    if a.json:
        print(json.dumps({"window": [win_start.strftime(T), now.strftime(T)], "jobs_seen": n_jobs,
                          "classes": rows, "queue_wait": qrows, "pending_now": dict(pending)}))
        return

    print(f"== busyness {win_start.strftime('%Y-%m-%d %H:%M')} -> {now.strftime('%Y-%m-%d %H:%M')}  "
          f"({n_jobs} jobs started cluster-wide, {n_used} node-runs in your partitions)")
    print("-- node-class occupancy (share of node-time busy; nodes are exclusive). weekday columns are % busy.")
    hdr = f"   {'class':<12}{'GPU':>5}{'x/node':>7}{'thr':>5}{'RAM':>6}{'nodes':>6}{'busy%':>7}{'now':>7}  " + " ".join(f"{w:>4}" for w in WEEK) + "   least-busy windows"
    print(hdr)
    for r in rows:
        gpu = f"{r['vram']}G" if r["vram"] else "-"
        dows = " ".join(f"{(r['occupancy_by_weekday'][w] if r['occupancy_by_weekday'][w] is not None else '-'):>4}" for w in WEEK)
        print(f"   {r['class']:<12}{gpu:>5}{r['gpus_per_node']:>7}{r['threads']:>5}{r['ram_gb']:>6}{r['nodes']:>6}{r['occupancy_pct']:>6}%{r['busy_now']:>5}/{r['nodes']:<2}  {dows}   {', '.join(r['least_busy_windows'])}")
    print("-- queue wait Submit -> Start for jobs that started (median / p90), and median by weekday of submission")
    print(f"   {'partition':<12}{'jobs':>6}{'median':>9}{'p90':>9}   " + " ".join(f"{w:>7}" for w in WEEK))
    for q in qrows:
        by = " ".join(f"{q['median_wait_by_weekday'][w]:>7}" for w in WEEK)
        print(f"   {q['partition']:<12}{q['jobs_started']:>6}{q['median_wait']:>9}{q['p90_wait']:>9}   {by}")
    pend = ", ".join(f"{p} {pending[p]}" for p in sorted(pending, key=lambda p: -pending[p]) if p in allowed) or "none"
    print(f"-- pending right now in your partitions (all users, jobs may count in several): {pend}")
    print("hint: submit long jobs before the weekend; shorter --time backfills into gaps sooner; pick classes with low busy%.")


if __name__ == "__main__":
    main()
