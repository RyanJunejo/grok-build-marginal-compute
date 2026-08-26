#!/bin/zsh
# One-shot migration to an amd64 Ubuntu VM. Usage: zsh runner/migrate_vm.sh <VM_IP> [user]
# Assumes the VM was provisioned with ~/.ssh/fastmodel_vm.pub in authorized_keys.
set -e
IP=$1; USER=${2:-root}
SSH="ssh -i $HOME/.ssh/fastmodel_vm -o StrictHostKeyChecking=accept-new $USER@$IP"
cd /Users/ryanjunejo/fastmodel

echo "[1/5] docker + python on VM"
$SSH 'command -v docker >/dev/null || (curl -fsSL https://get.docker.com | sh); \
      command -v python3 >/dev/null; apt-get install -y -q python3-venv rsync >/dev/null 2>&1 || true'

echo "[2/5] rsync repo (code, manifest, binaries, key, results metadata — not sessions/events bulk)"
rsync -az -e "ssh -i $HOME/.ssh/fastmodel_vm" \
  --exclude .venv --exclude bin/grok-1.0.5-macos-aarch64 --exclude results/sessions --exclude results/events \
  --exclude results/grades/logs --exclude 'results/preds/*.diff' \
  ./ $USER@$IP:~/fastmodel/

echo "[3/5] venv + deps on VM"
$SSH 'cd ~/fastmodel && python3 -m venv .venv && .venv/bin/pip -q install swebench==5.0.2 datasets matplotlib'

echo "[4/5] pull first-wave images natively (datacenter bandwidth)"
$SSH 'cd ~/fastmodel && .venv/bin/python - <<PY | xargs -n1 -P4 docker pull
import json
m = json.load(open("tasks/manifest.json"))
for iid in m["first_wave"]: print(m["instances"][iid]["image"])
PY'

echo "[5/5] smoke: grok binary + gold grading"
$SSH 'cd ~/fastmodel && chmod +x bin/grok-1.0.5-linux-x86_64 && ln -sf grok-1.0.5-linux-x86_64 bin/grok && \
      docker run --rm -v ~/fastmodel/bin/grok-1.0.5-linux-x86_64:/g:ro ubuntu:jammy /g --version && \
      cd results/grades 2>/dev/null || mkdir -p results/grades && cd results/grades && \
      ../../.venv/bin/swebench eval verified --gold -i sympy__sympy-20590 --run-id vm-gold 2>&1 | tail -4'

echo "MIGRATION COMPLETE — run experiments with: $SSH 'cd ~/fastmodel && ...'"
