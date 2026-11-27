#!/bin/bash
# Single-node CPU-only job (data processing, classical ML, simulations).
# Nodes are exclusive on this cluster: even a 2-core job holds the whole node,
# so request what the node has and use it (parallelise inside the job).
#SBATCH --job-name={{NAME}}                 # short, no spaces
#SBATCH --partition={{PARTITION}}           # main-cpu | itc-cpu    (keep GPU partitions free for GPU jobs)
#SBATCH --cpus-per-task={{CPUS}}            # itc-cpu nodes have 384 threads; caserta (main-cpu) 192
#SBATCH --mem={{MEM}}                       # integer + K/M/G/T, no decimals: 128G, 1900G. itc-cpu ~1.9T, caserta ~2T
#SBATCH --time={{TIME}}                     # D-HH:MM:SS. The job is KILLED at the limit.
#SBATCH --output=logs/%x-%j.out             # %x = job name, %j = job id; stderr goes to the same file
#SBATCH --mail-type=FAIL
#SBATCH --mail-user={{EMAIL}}

set -euo pipefail
source /etc/profile.d/modules.sh            # makes `module load` work even if the job was submitted from a non-login shell
cd "$SLURM_SUBMIT_DIR"
echo "$(date '+%F %T') host=$(hostname) job=$SLURM_JOB_ID cpus=$SLURM_CPUS_PER_TASK"

# --- environment (NOT inherited from the login shell: set everything here) ---
{{ENV_ACTIVATE}}                            # e.g.  source ~/venvs/ml/bin/activate      or      module load python/3.13.14
export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK # make BLAS/numpy use all allocated cores

# --- optional node-local scratch (fast SSD, wiped when the job ends) ---
SCRATCH=/local/$SLURM_JOB_ID
mkdir -p "$SCRATCH"
trap 'rm -rf "$SCRATCH"' EXIT               # cluster policy: always remove your /local data

# --- the work ---------------------------------------------------------------------
{{COMMAND}}                                 # e.g.  python preprocess.py --workers $SLURM_CPUS_PER_TASK
