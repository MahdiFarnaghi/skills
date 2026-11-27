#!/bin/bash
# Single-node GPU job (training, fine-tuning, inference, evaluation).
# Fill every double-brace placeholder (or delete the line when marked optional) before submitting.
# Validate without submitting:  hpc submit <file> --in <remote_dir> --test-only
#SBATCH --job-name={{NAME}}                 # short, no spaces: used in the log file name
#SBATCH --partition={{PARTITION}}           # main-gpu | itc-gpu | main     (hpc info lists the ones you may use)
#SBATCH --gres=gpu:{{GPU_FAMILY}}:{{GPUS}}  # e.g. lovelace + 1 -> gpu:lovelace:1 ; for ANY family replace the whole value by gpu:{{GPUS}}. CUDA_VISIBLE_DEVICES is set for you.
#SBATCH --constraint={{CONSTRAINT}}         # OPTIONAL, delete if unused: l40s | a40 | a100 | h200nvl | rtx6000pro   (& = AND, | = OR)
#SBATCH --cpus-per-task={{CPUS}}            # 4-8 is enough for one GPU; 64 cores exist on GPU nodes
#SBATCH --mem={{MEM}}                       # integer + K/M/G/T, no decimals: 64G, 1500G, 2T. GPU nodes have ~250G; itc-gpu ~257G
#SBATCH --time={{TIME}}                     # D-HH:MM:SS, e.g. 1-06:00:00. The job is KILLED at the limit -> checkpoint.
#SBATCH --output=logs/%x-%j.out             # %x = job name, %j = job id; stderr goes to the same file
#SBATCH --mail-type=FAIL                    # NONE | BEGIN | END | FAIL | ALL
#SBATCH --mail-user={{EMAIL}}

set -euo pipefail
source /etc/profile.d/modules.sh            # makes `module load` work even if the job was submitted from a non-login shell
cd "$SLURM_SUBMIT_DIR"                      # sbatch starts in the directory it was submitted from; be explicit
echo "$(date '+%F %T') host=$(hostname) job=$SLURM_JOB_ID CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv || true

# --- environment (NOT inherited from the login shell: set everything here) ---
# module load nvidia/nvhpc/25.7             # CUDA 12.9 toolkit: only needed to compile CUDA code (nvcc).
                                            # pip wheels of torch/tensorflow/jax bundle their own CUDA runtime.
{{ENV_ACTIVATE}}                            # e.g.  source ~/venvs/ml/bin/activate   |   source /software/miniconda3/25.7/etc/profile.d/conda.sh && conda activate ml
export PYTHONUNBUFFERED=1                   # stdout shows up in the log immediately
export HF_HOME="$HOME/cache/huggingface"    # keep model caches under /home, not in the synced project dir

# --- optional node-local scratch (fast SSD, wiped when the job ends) ---
SCRATCH=/local/$SLURM_JOB_ID
mkdir -p "$SCRATCH"
trap 'rm -rf "$SCRATCH"' EXIT               # cluster policy: always remove your /local data

# --- the work ---------------------------------------------------------------------
{{COMMAND}}                                 # e.g.  python train.py --epochs 50 --out runs/exp01
