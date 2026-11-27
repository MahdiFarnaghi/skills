#!/bin/bash
# 2-minute end-to-end check of a Python/CUDA environment on a real GPU node.
# Submit this first after creating a venv/conda env; `hpc wait <jobid>` then `hpc logs <jobid>`.
#SBATCH --job-name=smoke
#SBATCH --partition={{PARTITION}}           # main-gpu | itc-gpu | main
#SBATCH --gres=gpu:{{GPU_FAMILY}}:1         # family name, or replace the whole value by gpu:1 for any GPU
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --time=00:05:00
#SBATCH --output=logs/%x-%j.out

set -uo pipefail
source /etc/profile.d/modules.sh            # makes `module load` work even if the job was submitted from a non-login shell
cd "$SLURM_SUBMIT_DIR"
echo "host=$(hostname) job=$SLURM_JOB_ID CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
nvidia-smi -L

{{ENV_ACTIVATE}}                            # e.g.  source ~/venvs/ml/bin/activate   |   source /software/miniconda3/25.7/etc/profile.d/conda.sh && conda activate ml
which python; python --version
python - <<'EOF'
import sys
try:
    import torch
    print("torch", torch.__version__, "| built for CUDA", torch.version.cuda, "| cuda available:", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("device:", torch.cuda.get_device_name(0), "| mem:", torch.cuda.get_device_properties(0).total_memory // 2**20, "MiB")
        x = torch.randn(4096, 4096, device="cuda"); y = x @ x; torch.cuda.synchronize()
        print("matmul ok:", float(y[0, 0]))
    else:
        print("CUDA not available: check the GPU request and the wheel you installed (cpu-only wheel?)")
        sys.exit(1)
except ImportError:
    print("torch not installed in this environment")
    sys.exit(2)
EOF
