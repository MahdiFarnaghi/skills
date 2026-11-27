---
name: utwente-hpc
description: Operate the University of Twente EEMCS-HPC Slurm cluster over SSH (hpc-head1/hpc-head2.ewi.utwente.nl) through the bundled `hpc.sh` wrapper — pick a head node and a machine/partition/GPU type for a job; inspect partitions, QOS limits, node classes and queues; sync code with rsync; create Python/CUDA environments; write, validate and submit sbatch scripts (GPU, CPU, arrays); monitor, wait, cancel; fetch logs and results; debug failed/OOM jobs. Use when user mentions the UT/Twente HPC cluster, EEMCS-HPC, hpc-head, ewi.utwente.nl, Slurm commands (sbatch, squeue, srun, sinteractive, sacct, scancel, sinfo, module load), GPU or batch jobs "on the cluster"/"on the HPC"/"on Slurm", or asks to run training/scripts there.
---

# UT Twente EEMCS-HPC over SSH

Everything goes through `scripts/hpc.sh` — a thin wrapper that runs commands on a **head node** inside a login
shell (so `module`, the UT proxy and PATH work) and adds the Slurm conveniences below. Shell state does not
persist between tool calls, so give the full path each time:

```bash
HPC=<this skill's directory>/scripts/hpc.sh      # head node: hpc1 by default; --host hpc2 | --host auto | HPC_HOST=…
```

| Command | What it does |
|---|---|
| `hpc info` | one screen: your account/QOS limits, partitions you may use, node classes with idle counts, your queue |
| `hpc machines` | the node classes you may use (GPU model, VRAM, GPUs/node, threads, RAM, idle/busy) and which partition to submit to |
| `hpc pick [--vram 48] [--gpus N] [--gpu-model a40] [--family lovelace] [--cpus N] [--mem GB] [--cpu-only] [--avoid itc-gpu] [--prefer …]` | recommend a partition + ready-to-paste `#SBATCH` header; ranks by idle nodes, then **7-day occupancy** (from accounting), then fleet size; shows least-busy window and median queue wait per candidate (`hpc pick --help`) |
| `hpc busyness [--days N] [--json]` | per node class: occupancy by weekday and 4-hour window, busy now; per partition: queue wait median/p90 and by weekday; pending demand |
| `hpc run '<cmds>'` / `hpc run - arg1 <<'EOF' … EOF` | run shell commands on the head node (login shell, stdin-fed, no quoting problems) |
| `hpc up <local> <remote dir>` / `hpc down <remote> <local dir>` | rsync; `RSYNC_OPTS="--exclude=.git …"` for excludes, `--dry-run` to preview |
| `hpc test <file.sbatch> [-- sbatch args]` | validate a LOCAL script: refuses unfilled `{{PLACEHOLDERS}}`, then `sbatch --test-only` via stdin (nothing copied, no job) |
| `hpc submit <remote dir> <script> [--test-only] [-- sbatch args]` | `sbatch` an already-synced script from inside `<remote dir>` (= the job's submit dir); creates `logs/`; prints `JOBID=<n>` |
| `hpc status [jobid …]` / `hpc logs <jobid> [--tail N]` / `hpc wait <jobid> [--timeout S]` / `hpc cancel <id> …` | queue + accounting, output files, bounded wait, cancel |
| `hpc srun <srun opts> -- '<cmd>'` | run one command on a compute node now (blocks; may queue) |
| `hpc modules [group]` | list module groups/versions — **never** `module avail`/`module show` over ssh (they hang) |
| `hpc tty '<cmd>'` | real TTY, only for a human who wants `sinteractive`/`top-node` |
| `--dry-run` (any command) | print what would be executed |

## Non-negotiable rules

1. **Head nodes are for logging in only.** Light work is fine (edit, `git`, `pip install`, `sbatch`, `python -c "import torch"`).
   Never train, run long scripts or compile big projects there — submit a job.
2. **Never ssh into compute nodes.** To run something on a node now: `hpc srun … -- '<cmd>'` (short, bounded).
3. **Interactive jobs max 1 h** (cluster policy). For anything longer use `sbatch`.
4. **Nodes are exclusive** (`OverSubscribe=NO`): a `-c 1` job holds a whole node. CPU jobs go to `main-cpu`/`itc-cpu`,
   GPU jobs to `main-gpu`/`itc-gpu` (or `main`) — don't park CPU-only work on GPU nodes.
5. **Always set `--time`, `--mem`, `--output`** in every job (defaults are 24 h on the common partitions and whole-node memory;
   jobs are killed at the limit).
6. **Clean node-local scratch**: if the job uses `/local/$SLURM_JOB_ID`, remove it in an `EXIT` trap (templates do).
7. **No busy-waiting.** Check a job at most once per turn (`hpc status`, `hpc logs`); if it will run for hours, report the
   job id and how to check it, and stop. `hpc wait` is only for jobs that finish within its `--timeout` (default 300 s).
   `hpc pick`/`hpc busyness` scan ~90k accounting rows (≈3 s CPU on the head node): run them when deciding where/when
   to submit, not repeatedly.
8. **Confirm before destructive or expensive actions**: `cancel`, deleting remote files, syncing gigabytes, resubmitting many jobs.
9. `sbatch --test-only` does **not** check partition permissions — trust `hpc info` for what you may use.
10. Sync code, not data: exclude `.git`, `runs/`, caches, datasets. Large static data belongs in `/datasets/<faculty>` (read-only; request a folder).

## Choosing the machine

"Machine" means three different things — resolve what the user means, then act:

1. **Head node** (`hpc1` = hpc-head1, `hpc2` = hpc-head2): identical; either is fine. Use `--host hpc2` when one is down or
   `--host auto` to take the first reachable one. Only the SSH alias differs; nothing else changes.
2. **Partition** — what you are *allowed* to use: `hpc info` lists it (account-based). For account `itc-tech`:
   `main`, `main-cpu`, `main-gpu`, `itc-cpu`, `itc-gpu`.
3. **Node class / GPU model** — what the job *runs on*: `hpc machines` lists every class you may use with VRAM, GPUs per node,
   threads, RAM and how many are idle right now.

When the user names a target, map it: "the A100s" → `--constraint=a100` (`hpc pick --gpu-model a100`); "an L40s" →
`--constraint=l40s`; "the Blackwell / 96 GB cards" → `--constraint=rtx6000pro` (itc-gpu); "the big memory box" → caserta
(`main-cpu`, 2 TB) or `itc-cpu` (1.9 TB); "itc-gpu" / "main" → `--partition=`. Pinning one physical node
(`--nodelist=hpc-node09`) works but queues indefinitely if that node is busy — prefer `--constraint`.

When the user is unsure or the request is only "run this", turn the workload into requirements and ask `hpc pick`:
VRAM per GPU (model size, default 48), number of GPUs, threads, RAM, CPU-only or not. It ranks the classes you may use
by idle nodes, then by how busy each class was over the last 7 days (from accounting), then by fleet size, and prints
the header plus the least-busy window and typical queue wait. `hpc busyness` gives the full table (occupancy per
weekday/window, wait per partition). Present 2–3 candidates with their trade-offs (VRAM, busy %, wait, partition) when
the choice matters, otherwise take the top one and say which class/partition you chose and why.

Colleague tips live in `references/cluster.md` → *Local knowledge* — check them against `hpc busyness` and keep
that section current (date each entry). Example: itc-gpu is chronically 100 % busy with ~12-day waits, so
`pick --avoid itc-gpu` matches what experienced users do. The skill itself stays data-driven (no tip is a rule);
if the user wants a habit applied every time, put it in `~/.config/hpc/pick.conf` or `HPC_PICK_DEFAULTS`
(e.g. `--avoid itc-gpu`) — those defaults are parsed before the command line, so an explicit flag always wins.

### Getting scheduled sooner
- Pick the least-busy class that fits (`hpc pick`): e.g. `a40` (~60 % busy) instead of `l40` (~99 %) for a 48 GB job.
- Request a short, honest `--time`: backfill slots short jobs into gaps before long pending jobs start.
- Submit long jobs Thursday/Friday so they start on the weekend (measured: `main-gpu` median wait 2 min on Fri/Sat
  vs 1–2 h Mon–Tue; `itc-cpu` ~40 % busy on Saturdays vs 100 % Mon–Wed).
- Prefer big fleets (`main`/`main-gpu`: 26 GPU nodes) over 1–2-node partitions; `--constraint` for a model, not `--nodelist`.
- Throttle arrays with `%N` instead of many separate jobs; keep ≤ 8 running / 100 pending.

## Workflow

### 1. Orient — `hpc info`, then `hpc pick` for the job at hand
Read: partitions you may use, node classes with idle counts, QOS limits (jobs running / pending / GPUs), current queue.
Pick partition and resources from this plus the contention data in `pick`/`busyness`, not from `sinfo` alone (it lists
partitions you cannot use and shows only this instant). Start estimates: `hpc run 'squeue --start -j <id>'` or the `%S`
column of `hpc status`; `hpc test <script>` gives the scheduler's own estimate for a concrete script.

### 2. Environment (once per project) — on the head node, light work only
```bash
hpc run 'python3 -m venv ~/venvs/ml && source ~/venvs/ml/bin/activate && pip install -U pip && pip install torch'   # CUDA wheels from PyPI
hpc modules nvidia                                                  # CUDA toolkits (nvidia/nvhpc/25.7 = CUDA 12.9) — only needed for nvcc
```
- `python3` is 3.12 with `venv` + `ensurepip` available; `pip` reaches PyPI through the pre-set proxy. This is the
  recommended path. `pip install --user` is **blocked** (externally-managed environment) — install inside a venv.
- conda: `module load miniconda3/25.7` adds `/software/miniconda3/25.7/bin`, but the `conda` CLI **hangs over
  non-interactive ssh** (as of 2026-11-27). `source /software/miniconda3/25.7/etc/profile.d/conda.sh && conda activate <env>`
  works, so existing envs are usable in jobs; create envs from an interactive login (`hpc tty`) if you must.
- Never pipe `module load` (`module load x | tail`) — the PATH change happens in the subshell and is lost.
- Keep envs in `~/venvs` (not inside synced project directories). pip wheels of torch/TF/JAX bundle the CUDA runtime —
  no CUDA module needed unless compiling. Verify end-to-end with `assets/sbatch/gpu-smoke.sbatch` (2–5 min job).

### 3. Sync code
Convention: `~/projects/<name>/` on the cluster (your home; **not** `/projects/<faculty>`, which is read-only).
```bash
RSYNC_OPTS="--exclude=.git --exclude=runs --exclude=data --exclude=__pycache__ --exclude=.venv" \
  $HPC up ./ ~/projects/myproj/
$HPC run 'ls -la ~/projects/myproj'          # verify
```
`up`/`down` take directories (trailing slash = contents). Preview big transfers with `--dry-run`.

### 4. Write the job script
Copy a template from `assets/sbatch/`, fill every `{{…}}`, delete optional lines you don't need, and keep it in the project
(`jobs/*.sbatch`) so it is synced with the code:

| Template | For |
|---|---|
| `gpu-single.sbatch` | one node, 1–4 GPUs: training, fine-tuning, inference |
| `cpu-single.sbatch` | one CPU node (384 threads / 1.9 TB on `itc-cpu`): preprocessing, classical ML, simulations |
| `cpu-array.sbatch` | parameter sweep: one task per config, `%N` throttle, per-task logs, `--gres` line for GPU sweeps |
| `gpu-smoke.sbatch` | 2-minute environment check on a real GPU |

Scripts are self-contained: they `source /etc/profile.d/modules.sh`, activate the env, `cd $SLURM_SUBMIT_DIR`, write
`logs/%x-%j.out`, and clean `/local`. `hpc submit` creates `logs/` (or `mkdir -p logs` yourself before a raw `sbatch`).
Let `hpc pick` choose partition/GPU/constraint; `--mem` is an integer with `K|M|G|T` (`1500G`, never `1.5T`).
Checkpoints: save periodically and resume — jobs are killed at `--time`.

### 5. Validate without submitting
```bash
$HPC test jobs/train.sbatch                 # local file: placeholder check + sbatch --test-only (start estimate or error)
$HPC test jobs/train.sbatch -- --array=0-3  # extra sbatch options as they would be given on the CLI
```
"Requested node configuration is not available" = no node in that partition matches cpus+mem+gres+constraint (run
`hpc pick` with the same numbers); "Invalid --mem specification" = decimal unit. See `references/slurm.md` → *Error messages*.

### 6. Submit
```bash
$HPC up ./ ~/projects/myproj/                               # the script must be on the cluster
$HPC submit ~/projects/myproj jobs/train.sbatch             # -> "Submitted batch job 123456" + "JOBID=123456"
$HPC submit ~/projects/myproj jobs/sweep.sbatch -- --array=0-9%4 --dependency=afterok:123456
$HPC run 'cd ~/projects/myproj && mkdir -p logs && sbatch jobs/train.sbatch'   # raw form
```
The submit dir is `<remote dir>`, so relative paths in the script resolve against the project root. CLI options override
`#SBATCH` lines. Arrays: each pending task counts toward your 100-submitted limit; at most 8 of your jobs run at once.
**Always tell the user the job id.**

### 7. Monitor
```bash
$HPC status                 # your queue with state, elapsed, limit, reason, estimated start
$HPC status 123456          # + accounting: State, Elapsed, ExitCode, MaxRSS, NodeList
$HPC logs 123456 --tail 60  # finds the output file(s) via scontrol/sacct WorkDir
$HPC wait 123456 --timeout 900   # only when it is about to finish; exit 0 = COMPLETED, 1 = other, 2 = still queued
$HPC run 'seff 123456'      # CPU/memory efficiency after the run
```
Humans can also watch http://hpc-status.ewi.utwente.nl/slurm. Pending reasons are explained in `references/slurm.md`.
GPU utilisation of a running job: `hpc run 'module load monitor/node && nvidia-smi-node <node>'` (node from `hpc status`).

### 8. Retrieve results
```bash
$HPC --dry-run down 'projects/myproj/runs/exp01/' ./runs/exp01/     # preview, then run without --dry-run
$HPC down 'projects/myproj/logs/' ./logs/
```

### 9. Debug
`hpc status <id>` gives the facts: `State` (`FAILED`, `TIMEOUT`, `OUT_OF_MEMORY`, `CANCELLED`), `ExitCode` `code:signal`,
`MaxRSS`, node. Then `hpc logs <id>`. Map the message with the table in `references/slurm.md`; the frequent ones: OOM →
raise `--mem` or shrink the batch; `TIMEOUT` → checkpoints / more time / arrays; `module: command not found` → the script
lacks `source /etc/profile.d/modules.sh`; CUDA errors → check `CUDA_VISIBLE_DEVICES` and `nvidia-smi` lines at the top of
the log; dependency never satisfied → `cancel` and resubmit. Extend a running job with
`hpc run 'scontrol update JobId=<id> TimeLimit=2-00:00'`.

## Quick test on a compute node
```bash
$HPC srun -p main-gpu --gres=gpu:lovelace:1 -c 2 --mem 8G --time 00:10:00 -- 'source ~/venvs/ml/bin/activate && nvidia-smi -L && python -c "import torch; print(torch.cuda.is_available())"'
```
Blocks until the command finishes (give the tool call a timeout ≥ the time limit); it waits in the queue if the cluster is
busy, so prefer `sbatch` for anything but smoke tests.

## References
- `references/cluster.md` — hosts, scheduler settings, partitions and who may use them, node classes, GPU families and
  constraints, QOS table, storage/quota, modules and Python/CUDA environments, proxy, policy, gotchas (verified 2026-11-27).
- `references/slurm.md` — sinfo/sbatch/squeue/scontrol/sacct/seff formats, job states and pending reasons, error → fix
  table, arrays and dependencies, srun/sinteractive, where output files land.
