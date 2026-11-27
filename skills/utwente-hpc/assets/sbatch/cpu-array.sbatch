#!/bin/bash
# Parameter sweep as a Slurm job array: one task per parameter set, each with its own log.
# Limits on this cluster: array size <= 1001, and every PENDING task counts toward the
# 100 submitted-jobs limit of the `research` QOS. `%N` caps how many run at once (8 run max).
#SBATCH --job-name={{NAME}}                   # short, no spaces
#SBATCH --partition={{PARTITION}}             # main-cpu | itc-cpu | main-gpu | itc-gpu
#SBATCH --array=0-{{N_TASKS_MINUS_1}}%{{MAX_PARALLEL}}   # task ids 0..N-1, at most MAX_PARALLEL running at once
#SBATCH --gres=gpu:{{GPU_FAMILY}}:1           # family name (or gpu:1 for any); delete for CPU-only sweeps
#SBATCH --cpus-per-task={{CPUS}}
#SBATCH --mem={{MEM}}                       # integer + K/M/G/T, no decimals
#SBATCH --time={{TIME}}                       # per task, D-HH:MM:SS. Killed at the limit.
#SBATCH --output=logs/%x-%A_%a.out            # one log per task: %A = array job id, %a = task id
#SBATCH --mail-type=FAIL
#SBATCH --mail-user={{EMAIL}}

set -euo pipefail
source /etc/profile.d/modules.sh            # makes `module load` work even if the job was submitted from a non-login shell
cd "$SLURM_SUBMIT_DIR"
i=$SLURM_ARRAY_TASK_ID
echo "$(date '+%F %T') task $i / $SLURM_ARRAY_TASK_COUNT of array $SLURM_ARRAY_JOB_ID on $(hostname)"

{{ENV_ACTIVATE}}                              # e.g.  source ~/venvs/ml/bin/activate   |   source /software/miniconda3/25.7/etc/profile.d/conda.sh && conda activate ml
export PYTHONUNBUFFERED=1

# --- map the task id to parameters -----------------------------------------------
# Option A: read task i from a file (one line per task, space separated):
#   read -r lr seed depth <<<"$(sed -n "$((i + 1))p" params.txt)"
# Option B: compute it:
lr=$(awk -v i="$i" 'BEGIN{printf "%g", 10^(-2 - i % 3)}')
seed=$((1000 + i))
out="runs/${SLURM_ARRAY_JOB_ID}_task${i}"   # runs/<arrayid>_task<i>

# --- the work ---------------------------------------------------------------------
mkdir -p "$out"
{{COMMAND}}                                   # e.g.  python train.py --lr "$lr" --seed "$seed" --out "$out"
