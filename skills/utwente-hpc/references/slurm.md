# Slurm on EEMCS-HPC: commands, output formats, states, errors

Slurm 23.11.4. All commands below run through `hpc run '…'` (or the `hpc status|logs|wait|cancel|srun`
subcommands). `man <cmd>` works on the head nodes.

## Contents
- Inspecting resources: sinfo
- Submitting: sbatch
- Queues and jobs: squeue, scontrol show job
- After the fact: sacct, seff
- Job states and pending reasons
- Error messages and what to do
- Arrays, dependencies, time-limit changes
- Getting scheduled sooner
- Interactive / one-shot commands on a compute node: srun, sinteractive
- Where the output file is

## Inspecting resources: sinfo

```bash
sinfo -s                                   # partitions, nodes A/I/O/T (allocated/idle/other/total)
sinfo -p main-gpu -o "%P %D %c %m %G %f"   # partition, nodes, cpus/node, mem MB, gres, features
sinfo -N -o "%N %c %m %G %t"               # per node: name, cpus, mem, gres, state
sinfo -t idle -p main-gpu -o "%N %G"       # idle GPU nodes right now
sinfo -O "Partition:.12,NodesAvailable:.10,CPUsState:.20,Memory:.10,Gres:.28"
```

`%P` prints `main*` for the default partition. GRES strings look like `gpu:lovelace:4(S:0-1)` (4 GPUs on sockets 0–1).

## Submitting: sbatch

```bash
sbatch job.sbatch                          # -> "Submitted batch job 123456"; script is copied into Slurm at submit time
sbatch --test-only job.sbatch              # estimate start time; NO job created; does NOT check partition/QOS permission
sbatch --array=0-9%4 job.sbatch            # override a directive on the command line (CLI wins over #SBATCH)
sbatch --wrap="python train.py" --job-name=t -c4 --mem 16G --time 01:00:00   # no script file
sbatch --export=ALL,EPOCHS=10 job.sbatch   # pass variables to the job
sbatch < job.sbatch                        # script on stdin (#SBATCH lines are honoured); name it with --job-name
```

Formats: time `D-HH:MM:SS` or `HH:MM:SS`; memory integer + `K|M|G|T` (`1500G`, not `1.5T`); `--gres=gpu[:family]:n`;
`--constraint="a&b|c"`; `--cpus-per-task=N` (short `-c`), `--ntasks` (`-n`, processes), `--nodes` (`-N`).

Rules of thumb on this cluster (nodes are exclusive, `OverSubscribe=NO`):
- GPU job: `--gres=gpu:<family>:1 -c 8 --mem 64G`, partition `main-gpu` (or `itc-gpu`). More GPUs → more cores/memory proportionally.
- CPU job: `main-cpu` or `itc-cpu`, `-c` equal to what you really parallelise over; `--mem` up to the node size.
- Always `--time` (default 24 h, then killed) and `--output=logs/%x-%j.out` (create `logs/` before submitting).

## Queues and jobs: squeue, scontrol show job

```bash
squeue -u $USER -o "%.10i %.12P %.30j %.3t %.10M %.10l %.6D %R"   # id partition name state elapsed timelimit nodes reason
squeue -u $USER -t PD -o "%i %R %S"                                 # pending jobs: reason and expected start
squeue -j 123456 --start                                            # estimated start / allocated resources
sprio                                                               # your priority components
scontrol show job 123456                       # everything: Command, WorkDir, StdOut, StdErr, TRES, NodeList, StartTime
scontrol show job 123456 -d                    # with dependency details
scontrol write batch_script 123456 stdout     # recover the exact script that was submitted
scontrol show step 123456.batch                # resource use of the running batch step
```

Useful `scontrol show job` fields for scripts: `JobState=`, `NodeList=`, `StdOut=`, `WorkDir=`, `RunTime=`, `TimeLimit=`, `Reason=`.

## After the fact: sacct, seff

```bash
sacct -j 123456 -X -o JobID,JobName%30,State,Elapsed,ExitCode,MaxRSS,NodeList      # -X: allocation rows only
sacct -u $USER -S yesterday -X -o JobID,JobName%30,Partition,State,Elapsed,ExitCode # everything since yesterday
sacct -j 123456 -o JobID%18,State,ExitCode,MaxRSS,AveRSS,Elapsed                  # include .batch/.extern steps (per-step memory)
sacct -j 123456 -X -o WorkDir%200                                                # where it ran (for finding logs)
seff 123456                                                                      # CPU & memory efficiency summary
```

`MaxRSS` is the peak resident memory of the task (MB). `ExitCode` is `code:signal` — `0:0` is clean; `1:0` a
non-zero exit; `0:9` killed by signal 9 (often the time limit or OOM).

## Job states and pending reasons

States (`%t` short / full): `PD` pending, `R` running, `CG` completing, `CD` completed, `F` failed, `TO` timeout,
`OOM` out_of_memory, `CA` cancelled, `NF` node_fail, `PR` preempted, `BOOT_FAIL`, `DL` deadline.

Pending reasons worth knowing (`%R`):
- `Resources` / `Priority` — normal queueing; `%S` gives an estimate.
- `QOSMaxJobsPerUserLimit` — 8 jobs of yours already running (`research` QOS).
- `QOSMaxSubmitJobPerUserLimit` — 100 jobs of yours pending (array tasks count).
- `QOSMaxGRESPerUser` / `AssocGrpGRES` — GPU quota of your QOS/account reached.
- `AssociationResourceLimit` / `AssocMaxJobs` — account limits.
- `PartitionTimeLimit` / `QOSMaxWallDurationPerJobLimit` — your `--time` exceeds what is allowed: lower it.
- `ReqNodeNotAvail` / `QOSRequireNode` — no node matches `--constraint`/`--gres`/`--mem`.
- `DependencyNeverSatisfied` — a dependency failed/was cancelled: `scancel` and resubmit.
- `JobHeldUser` / `JobHeldAdmin` — `scontrol release <id>` (user hold) or ask the admins.

## Error messages and what to do

| Message | Meaning | Fix |
|---|---|---|
| `sbatch: error: Invalid --mem specification` | decimal unit like `1.5T` | `1500G` |
| `sbatch: error: Batch script is empty` | stdin was empty / wrong file | check the path; don't feed stdin to two commands |
| `allocation failure: Requested node configuration is not available` | no node in the partition matches cpus+mem+gres+constraint | check `hpc info` node classes; the GPU model may be in another partition |
| `sbatch: error: Job violates accounting/QOS policy (...)` / `... not allowed to use this partition` | your account may not use the partition, or a QOS limit | use a partition from `hpc info`; check QOS limits |
| `sbatch: error: Invalid account or account/partition combination specified` | bad `--account` | omit `--account` (default is right) |
| `sbatch: error: Unable to open batch script` | file missing on the cluster | sync first (`hpc up`) |
| `srun: error: ... Unable to allocate resources` / `Job exceeds ...` | same causes as above | as above |
| State `OUT_OF_MEMORY` / log ends abruptly, `ExitCode 0:9` | cgroup killed a process over `--mem` | raise `--mem`, reduce batch size, check `MaxRSS` with `seff` |
| State `TIMEOUT` | hit `--time` | save checkpoints; request more time or split work (arrays) |
| `module: command not found` in the job | job was submitted from a shell that had no `module` function (plain `ssh host cmd`, cron) | `source /etc/profile.d/modules.sh` at the top of the script (templates do), or submit via `hpc run` |
| `ModuleCmd_Load.c(…): Unable to locate a modulefile` | wrong name/version | `hpc modules <group>` for exact names |
| `CUDA out of memory` / `no CUDA GPUs are available` | model too big, or no GPU requested / wrong visible devices | check `CUDA_VISIBLE_DEVICES` in the log header, `nvidia-smi` output |
| `Connection refused` / `Temporary failure in name resolution` in a job | no proxy variables | jobs inherit the environment at submit time from a login shell — submit via `hpc run`, or `export http_proxy=… https_proxy=…` in the script |
| `Host key verification failed` (locally) | head node reinstalled | `ssh-keygen -R hpc-head1.ewi.utwente.nl` then reconnect |

## Arrays, dependencies, time-limit changes

```bash
#SBATCH --array=0-99%8                      # 100 tasks, max 8 at once (QOS: 8 running, 100 pending — count them!)
#SBATCH --output=logs/%x-%A_%a.out          # per-task log: %A array id, %a task id
#SBATCH --dependency=afterok:123456         # start after 123456 succeeded (afterany: afterok: afternotok: aftercorr:)
#SBATCH --dependency=afterok:123456:123457  # after several jobs
sbatch --dependency=singleton job.sbatch    # array task N waits for task N of the previous array
scancel 123456                              # whole array; scancel 123456_7 for one task
scancel -u $USER -t PD                      # cancel all my pending jobs
scontrol update JobId=123456 TimeLimit=2-00:00   # extend a running/pending job's limit
scontrol hold 123456 ; scontrol release 123456
scontrol update JobId=123456 Nice=1000      # lower a job's priority (be nice)
sbatch --nodelist=hpc-node09 job.sbatch     # pin one physical node: works, but waits until THAT node is free — prefer --constraint
```

Inside an array task: `SLURM_ARRAY_JOB_ID`, `SLURM_ARRAY_TASK_ID`, `SLURM_ARRAY_TASK_COUNT`, `SLURM_ARRAY_TASK_MAX`,
`SLURM_ARRAY_TASK_STEP`. Resubmit unfinished tasks with `--array=3,7,11`.

## Getting scheduled sooner

- **Short, honest `--time`**: with `sched/backfill`, short jobs fill gaps ahead of long pending jobs; a 4 h request
  starts far sooner than 4 days for the same work.
- **Least-busy class that fits**: `hpc pick` ranks by 7-day occupancy (e.g. `a40` ≈ 60 % vs `l40` ≈ 99 %).
- **Big fleets over small partitions**: `main`/`main-gpu` (26 GPU nodes) vs `itc-gpu` (2 nodes, 11-day waits).
- **Submit before the weekend** for long jobs (Fri/Sat waits are minutes on `main-gpu`).
- **Arrays with `%N` throttle** rather than dozens of separate submissions (QOS: 8 running, 100 pending).
- `--constraint=<model>` rather than `--nodelist=<node>`; drop constraints that are not needed.
- `sbatch --test-only` (via `hpc test`) shows the scheduler's expected start time for a concrete request.

## Interactive / one-shot commands on a compute node: srun, sinteractive

```bash
# one command, blocking, agent-friendly (give it --time; it may queue):
hpc srun -p main-gpu --gres=gpu:lovelace:1 -c 2 --mem 8G --time 00:10:00 -- 'source ~/venvs/ml/bin/activate && python -c "import torch; print(torch.cuda.is_available())"'
# raw form: srun -N1 -n1 -c2 --mem 8G --time 00:10:00 --gres=gpu:1 python -c "…"   (add --pty for a terminal)
# for humans only (needs a real TTY; max 1 h):
hpc tty 'module load slurm/utils && sinteractive -c 4 --mem 16G --time 60 -p main-gpu'   # then: exit
```

`srun` inside an `sbatch` script starts a step on the allocated nodes (`srun -N4 -n4 --mpi=pmix ./a.out` after
`module load openmpi/4.1.5`). `srun` from the head node creates its own allocation and waits in the queue like a job.

Monitoring a running job from the head node: `module load monitor/node && nvidia-smi-node hpc-node09`,
`nvtop-node hpc-node09`, `top-node hpc-node09` (reads only; never log in to the node).

## Where the output file is

- Live job: `scontrol show job <id>` → `StdOut=` / `StdErr=` / `WorkDir=`. Finished job: `sacct -j <id> -X -o WorkDir%200`.
- Default pattern `slurm-%j.out` in the submission directory. Templates here use `logs/%x-%j.out`
  (`%x` job name, `%j` id) and stderr goes to the same file unless `--error` is set. Arrays: `%A_%a`.
- `hpc logs <id>` finds and tails these automatically (searches `WorkDir` for `*<id>*.out|err|log`).
- Pattern file names (`--output=logs/%4j.out`) are expanded per task; `--open-mode=append` avoids truncation on requeue.
