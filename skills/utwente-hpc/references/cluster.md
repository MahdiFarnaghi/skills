# EEMCS-HPC cluster reference (University of Twente)

Facts below were verified against the live cluster on 2026-11-27 (Slurm 23.11.4, Ubuntu 24.04).
Wiki: https://hpc.wiki.utwente.nl/eemcs-hpc — Dashboard (queue, partitions, QOS tab): http://hpc-status.ewi.utwente.nl/slurm

## Contents
- Access and hosts
- Scheduler configuration
- Partitions and who may use them
- Node classes (what hardware you get)
- GPU families and constraints
- QOS limits
- Storage and quota
- Software: modules, Python environments, CUDA
- Network and proxy
- Contention patterns (measured)
- Local knowledge (tips from users)
- Cluster policy (non-negotiable)
- Gotchas discovered the hard way

## Access and hosts

| Item | Value |
|---|---|
| Head (login) nodes | `hpc-head1.ewi.utwente.nl`, `hpc-head2.ewi.utwente.nl` — both identical; use either for failover |
| SSH | port 22, key auth; `~/.ssh/config` aliases `hpc1` / `hpc2` (user `farnaghim`) |
| Username | staff: surname + initials; students: `s` + number; guests: `x`… |
| Compute nodes | reachable only through Slurm. **Never `ssh` to them.** |
| OS | Ubuntu 24.04 LTS on every node |
| Dashboard | http://hpc-status.ewi.utwente.nl/slurm |
| Support | Geert Jan Laanstra (EEMCS-DMB/SCS), Martin Wilens (ET-TFE-MSM); password issues: ICTS/LISA Servicedesk |
| Mailing list | EEMCS-Hpc-Cluster-Users (outages, maintenance) |

Maintenance days take the whole cluster offline (completed: 2026-10-28/29 OS + Slurm upgrade; planned: Apr 2027 software updates).

## Scheduler configuration

| Setting | Value | Consequence |
|---|---|---|
| Slurm | 23.11.4, cluster name `ctit`, `sched/backfill`, `select/cons_tres` (CR_CORE) | standard Slurm CLI semantics |
| `OverSubscribe` | `NO` on every partition | **nodes are exclusive per job**: a `-c 1` job still occupies a whole node |
| `DefMemPerNode` | UNLIMITED | without `--mem` a job may use the whole node's memory; still set `--mem` explicitly |
| `DefaultTime` | `1-00:00:00` on `main*`, `itc-*`, `bmpi`, `smm`, `tfe-cpu/gpu`, `dmb-gpu`, `dacs/scs/caes-gpu`; `NONE` on most group partitions | a job without `--time` gets 24 h on the common partitions, nothing elsewhere |
| `MaxTime` | UNLIMITED per partition | longer is possible, but QOS `research` has no MaxWall either — always state `--time` |
| `MaxArraySize` | 1001 | array task ids < 1001 |
| `KillWait` | 30 s | after `--time` or `scancel`, processes get 30 s to exit |
| `sbatch --test-only` | validates and estimates start time | **does not check partition ACLs or QOS** — a forbidden partition still "passes" |

## Partitions and who may use them

`hpc info` prints the partitions your account may use. Access is by Slurm account (`AllowAccounts`); the
common partitions allow `ALL`, the others list faculty/group accounts. For account `itc-tech`: `main`,
`main-cpu`, `main-gpu`, `itc-cpu`, `itc-gpu`.

| Partition | Nodes | Accounts | Use for |
|---|---|---|---|
| `main` (default) | all common nodes: ctit084-094, caserta, hpc-node01-12,14,16-18 | ALL | anything; GPU jobs land on the same nodes as `main-gpu` |
| `main-gpu` | ctit084-086,088-094, hpc-node01-12,14,16-18 | ALL | GPU jobs |
| `main-cpu` | caserta (192c/2T), ctit087 (128c, T4) | ALL | CPU jobs (long queue: a 1-core job holds the 2 TB node) |
| `itc-cpu` | hpc-node27,28,32 (384 threads, 1.9 TB each) | itc* | CPU jobs (ITC) |
| `itc-gpu` | hpc-node29,30 (2× RTX 6000 Pro 96 GB each) | itc* | GPU jobs (ITC) |
| `dacs-gpu` `scs-gpu` `caes-gpu` `dmb-gpu` | hpc-node33-39 (3× RTX 6000 Pro 96 GB) | dacs / scs / caes / dmb | funder-only first year |
| `tfe` `tfe-cpu` `tfe-gpu` | hpc-node16-19, 20-26 | et-* (TFE) | includes the H200NVL node (hpc-node19) |
| `am` `mia` `mia-pof` `dmb` `m3i` `ps` `bss` `bmpi` `smm` `ram` `bdsi` | various | group accounts | group priorities |
| `students` | hpc-node08 | students | course work only |
| `debug` | many | admin,root | admins only |

Listing multiple partitions (`--partition=main,dmb`) only helps if your account is allowed in each.

## Node classes (what hardware you get)

From `hpc info` / `sinfo`. Memory in GB, "c" = hardware threads Slurm counts as CPUs.

| Nodes (partition) | c | RAM | GPUs | CPU | Feature tags |
|---|---|---|---|---|---|
| hpc-node01-08 (main, main-gpu) | 64 | 250 | 4× NVIDIA L40 48 GB (lovelace) | Intel | `l40` `intel` `avx512` |
| hpc-node09-12 (main, main-gpu) | 64 | 250 | 4× L40s 48 GB (lovelace) | Intel | `l40s` |
| hpc-node14 (main, main-gpu) | 64 | 250 | 3× L40 48 GB | AMD | `l40` |
| hpc-node16-18 (main, main-gpu, bmpi) | 64 | 250 | **1×** L40s 48 GB | AMD | `l40s` `cn5000` |
| hpc-node15 (main, main-gpu, bss) | 64 | 250 | 1× RTX 6000 Pro 96 GB (blackwell) | AMD | `rtx6000pro` |
| ctit086,090-094 (main, main-gpu) | 64 | 120–250 | 4× A40 48 GB (ampere, NV4) | Intel | `a40` |
| ctit089 (main, main-gpu, ram) | 64 | 250 | 4× A100 40 GB (ampere, NV12) | Intel | `a100` |
| ctit084-085 (main, main-gpu, dmb) | 64 | 250 | 4× RTX 6000 24 GB (turing, NV2) | Intel | `rtx-6000` `quadro` |
| ctit088 (main, main-gpu) | 64 | 250 | 2× RTX 2080 Ti 8 GB + 1× A4500 | Intel | `rtx-2080ti` `geforce` |
| ctit087 (main, main-cpu, bdsi) | 128 | 250 | 1× T4 16 GB | AMD | `t4` |
| caserta (main, main-cpu) | 192 | 2000 | — | Intel | (the "fat" node) |
| hpc-node29-30 (itc-gpu) | 64 | 257 | 2× RTX 6000 Pro 96 GB (blackwell) | AMD | `rtx6000pro` |
| hpc-node27,28,32 (itc-cpu) | 384 | 1934 | — | AMD | `avx512` |
| hpc-node19 (tfe, tfe-gpu, smm) | 64 | 250 | 1× H200 NVL 141 GB (hopper) | AMD | `h200nvl` `cn5000` |
| hpc-node33-39 (dacs/scs/caes/dmb-gpu) | 64 | 257 | 3× RTX 6000 Pro 96 GB | AMD | `rtx6000pro` |

Note: the H200NVL node and the 1-GPU L40s nodes are **not** in `main`/`main-gpu`; `gpu:hopper:1` on `main`
fails with "Requested node configuration is not available".

## GPU families and constraints

- `--gres=gpu[:family]:count` — families: `turing` (T4, RTX 6000, RTX 2080 Ti), `ampere` (A40, A100),
  `lovelace` (L40, L40s), `blackwell` (RTX 6000 Pro), `hopper` (H200NVL). Plain `gpu:1` = any.
- `--constraint=<features>` pins a model: `l40` `l40s` `a40` `a100` `h200nvl` `rtx6000pro` `rtx-6000`
  `rtx-2080ti` `t4`, plus CPU tags `amd` `intel` `avx512` `cn5000`. Combine with `&` (AND) and `|` (OR):
  `--constraint="intel&avx512"`, `--constraint="l40|l40s"`.
- `CUDA_VISIBLE_DEVICES` is set by Slurm; only those GPUs may be used.
- NVLink-paired GPUs (A100, A40, RTX 6000): request two with `--gres=gpu:a100:2 --sockets-per-node=1`.
- NVIDIA driver 615.71.10 on every GPU node; load a CUDA module only if you need `nvcc`/system cuDNN.

## QOS limits

Each user has a default QOS (`sacctmgr show assoc user=$USER`); extra QOS must be requested/granted.

| QOS | MaxWall | MaxTRESPerUser | MaxJobsPU | MaxSubmitPU |
|---|---|---|---|---|
| `research` (default for account itc-tech) | — | — | 8 | 100 |
| `research-deadline` | — | — | 32 | 100 |
| `normal` | — | — | 2 | 20 |
| `guest-research` | — | gres/gpu=2 | 4 | 25 |
| `students` / `guest-students` / `students-deadline` | — | gres/gpu=1–2 | 2–8 | 25–50 |
| `serial-small` | 02:00:00 | — | 32 | — |

Every pending array task counts toward MaxSubmitPU. The dashboard's QOS tab shows the live numbers.

## Storage and quota

| Path | Scope | Notes |
|---|---|---|
| `/home/<user>` | all nodes, NFS/ZFS | 1 TB per user — writes are blocked above it. Code, venvs, small data, caches. Not an archive. |
| `/projects/<faculty>` | all nodes | shared project space; **read-only unless a subfolder was requested for you**. The volume is ~92 % full. |
| `/datasets/<faculty>` | all nodes | static/large datasets, read-only. Ask for a folder. |
| `/local/<jobid>` | compute node only | node-local SSD (1.8–7.3 TB). Create at job start, `rm -rf` in an EXIT trap. Fastest I/O. |
| `/software` | all nodes | modulefile tree and module software (`/software/Modulefiles/<group>/<version>`) |

Account removal wipes `/home`. Home usage: `df -h ~` (per-user filesystem) or `quota -s`.

## Software: modules, Python environments, CUDA

Environment Modules 5.4.0. `MODULEPATH=/software/Modulefiles:/usr/share/modules/modulefiles`.

- `module` exists only in login shells (`bash -l`), where Modules exports it as a shell function; jobs inherit
  that function through `sbatch --export=ALL`. A job submitted from a non-login shell (plain `ssh host cmd`)
  therefore has no `module`. `hpc run` always uses a login shell; the templates also `source /etc/profile.d/modules.sh`
  (idempotent) so they work either way.
- **Never pipe `module load`** (`module load x | tail`): the function runs in the subshell and the PATH change is lost.
- **`module avail` and `module show` hang indefinitely over non-interactive ssh.** Discover modules with
  `hpc modules [group]` (a directory listing) instead. `module load`, `module list`, `module purge` are fine.
- Modules are not inherited by jobs: every `#SBATCH` script must `module load` what it needs.
Frequently used groups (`hpc modules <group>` for all versions):

| Group | Versions | Notes |
|---|---|---|
| `python` | 3.7.3, 3.9.9, 3.10.7, 3.13.14, 3.14.6 | system `python3` is 3.12.3 — fine for `python3 -m venv` |
| `miniconda3` | 25.7 (py 3.13), 4.9 (py 3.8), 3.8 | `module load miniconda3/25.7 && conda create -n ml python=3.11` |
| `anaconda3` | 2019.03 … 2025.06 | |
| `nvidia` | `cuda-8.0` … `cuda-11.8`, `cuda-12.4`; `cuda-X.Y_cudnn-A.B`, `cuda-X.Y_nccl-A.B`, `cuda-X.Y_tensorrt-A.B`; `nvhpc/{23.3,24.3,25.7,26.3}` = CUDA 12.0/12.3/12.9/13.1 | CUDA 12/13 only via `nvhpc/<ver>` |
| `slurm` | `utils` | provides `sinteractive` |
| `monitor` | `node` | `top-node`, `nvtop-node`, `nvidia-smi-node <node>` (run on the head node) |
| `openmpi` | 4.1.5 (default 4.0.5) | `module load openmpi/4.1.5` then `srun` |
| `singularity` | 3.7.4, 3.8.0, 3.9.5 | containers |
| `microsoft` / `coder` | vscode 1.112, code-server 4.128 | |
| others | `gnu/gcc-5,7`, `cmake`, `rust`, `go`, `julia`, `R`, `hdf5`, `petsc`, `valgrind`, `ollama`, `mathworks` (Matlab), `wolfram`, `comsol`, `ansys_inc`, `gurobi`, `simnibs`, `salvus`, `paraview`, `openfoam`, `SU2`, `yade` … | |

`seff <jobid>` (job efficiency: CPU and memory use) is at `/usr/local/bin/seff`, no module needed.

Python environments (system `python3` = 3.12.3 with `venv`, `ensurepip`, `pip 24.0`, `virtualenv 20.25`):

```bash
# venv + pip: the recommended path (works over plain ssh; proxy is pre-set in login shells)
python3 -m venv ~/venvs/ml && source ~/venvs/ml/bin/activate && pip install -U pip torch
# uv (fast installer) can live in its own venv:  python3 -m venv ~/venvs/tools && ~/venvs/tools/bin/pip install uv
# conda: the module adds /software/miniconda3/25.7/bin, but the `conda` CLI HANGS over non-interactive ssh (2026-11-27).
module load miniconda3/25.7
source /software/miniconda3/25.7/etc/profile.d/conda.sh && conda activate <env>   # this part works: use it inside jobs
```

`pip install --user` is blocked by Ubuntu's externally-managed marker (`EXTERNALLY-MANAGED`) — install into a venv.
pip-installed `torch`/`tensorflow`/`jax` wheels bundle the CUDA runtime; no CUDA module is needed unless you
compile CUDA code (then `module load nvidia/nvhpc/25.7` for `nvcc`).

## Network and proxy

Outbound internet only through `http://proxy.utwente.nl:3128`. Login shells already export
`http_proxy`, `https_proxy`, `HTTP_PROXY`, `HTTPS_PROXY` and `no_proxy=.utwente.nl,127.0.0.1,localhost`.
Non-login shells do not have them — another reason to go through `hpc run`. `rsync`, `scp`, `git`, `tmux`, `screen` are installed on the head nodes. GPU nodes have 10 GbE (some 400 Gb Omnipath, tag `cn5000`).

## Contention patterns (measured 2026-11-27 with `hpc busyness`, 7 days, all users)

Node-class occupancy (share of node-time busy) and queue wait Submit→Start for jobs that started:

| Class | Nodes | Busy | Pattern | Median wait (partition) |
|---|---|---|---|---|
| `l40` 4×48 GB | 8 | 99 % | flat all week — effectively unobtainable | 0h51 (main-gpu) |
| `a40` 4×48 GB | 5 | 60 % | least busy of the 48 GB classes; Wed 08–12 ≈ 49 % | 0h51 (main-gpu) |
| `l40s` 4×48 GB | 4 | 91 % | Sun 75 % | 0h51 (main-gpu) |
| `l40s` 1×48 GB | 3 | 90 % | Wed 08–12 ≈ 37 % | 0h51 (main-gpu) |
| `rtx6000pro` 2×96 GB (itc-gpu) | 2 | **100 % every day** | chronically saturated | **11 d 19 h** (itc-gpu), 3 jobs started in a week |
| `a100` 4×40 GB | 1 | 100 % | one job has run since 2026-11-02 (MaxTime unlimited) | — |
| `rtx-6000` 4×24 GB | 2 | 85 % | Sun 54 % | 0h51 (main-gpu) |
| itc-cpu 384 thr / 1.9 TB | 3 | 79 % | Mon–Wed 100 %, Fri 52 %, Sat 40 % | 0h06 (itc-cpu) |
| caserta 192 thr / 2 TB | 1 | 100 % | — | 0h05 (main-cpu) |

Weekend effect on `main-gpu` waits (median by weekday of submission): Mon 1h23, Tue 2h09, Wed 1h37, Thu 2h06,
**Fri 0h02, Sat 0h02**, Sun 1h13. Pending demand seen: main 83, main-gpu 70, itc-gpu 52 (for 2 nodes), itc-cpu 11.
Re-run `hpc busyness` before relying on these numbers; patterns shift with courses and deadlines.

## Local knowledge (tips from users — verify with `hpc busyness`, keep dated)

- *2026-11, colleague:* "You have a higher chance to get something running on the weekend; itc-gpu is almost always
  full, I never run on it anymore — I use the main partition with L40/L40S/A40 GPUs." → **Confirmed by the data above**
  (itc-gpu 100 % / 11-day median wait; weekend waits minutes instead of hours). Practical form:
  `hpc pick --vram 48 --avoid itc-gpu` → `--partition=main-gpu --gres=gpu:ampere:1 --constraint=a40` currently wins.
- Design rule for this section: **data decides, knowledge annotates.** Tips are never baked into `pick` as rules;
  they are recorded here, dated, and verified against `hpc busyness`. Personal habits (not facts) can be persisted
  per user in `~/.config/hpc/pick.conf` or `HPC_PICK_DEFAULTS` (e.g. `--avoid itc-gpu`); explicit flags override them.

## Cluster policy (non-negotiable)

1. Log in only to the head nodes; never `ssh` to compute nodes.
2. No compute work on the head nodes: no training, no heavy compiles, no long-running scripts. Light tasks are fine: editing, `git`, `pip install`, `sbatch`, small tests (`python -c "import torch"`).
3. Interactive jobs (`sinteractive`, `srun --pty`) are for short tests, **max 1 hour**.
4. Remove everything you created under `/local` when the job ends (EXIT trap).
5. CPU-only jobs must not occupy GPU nodes: use `main-cpu` / `itc-cpu`, and don't request all cores of a mixed node without using its GPUs.
6. Keep `/home` under 1 TB; the cluster is not an archive.
7. `students` partition: course work only.

## Gotchas discovered the hard way

- `sbatch --test-only` passes for partitions your account cannot use; a real submit then fails with an accounting/QOS policy error.
- Memory is an integer with unit `K|M|G|T`: `--mem=1.5T` is rejected ("Invalid --mem specification"); use `1500G`.
- "allocation failure: Requested node configuration is not available" means no node in that partition satisfies the combined `--mem`/`--cpus`/`--gres`/`--constraint` request (e.g. the GPU model is not in that partition).
- `sinteractive` is "command not found" until `module load slurm/utils`.
- The `conda` CLI hangs (also under a pseudo-tty, also with `CONDA_NO_PLUGINS`) when started from non-interactive ssh;
  `pip`, `python`, `git`, `curl` and `source conda.sh && conda activate` all work. Reported for miniconda3/25.7.
- `pip install --user <pkg>` → `externally-managed-environment`: use a venv.
- `module load x | tail -1` silently does nothing (subshell) — never pipe `module load`.
- `nvidia-smi` does not exist on the head nodes (no GPUs there) — check GPUs inside a job.
- Queue depth differs by partition: `main`/`main-gpu` GPU jobs typically start within hours; `main-cpu`/`itc-cpu` estimates of several days were seen for small CPU jobs because nodes are exclusive.
- Head-node host keys changed with the Oct 2026 reinstall: on `Host key verification failed`, remove the old entry with `ssh-keygen -R hpc-head1.ewi.utwente.nl` and reconnect.
