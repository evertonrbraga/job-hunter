#!/usr/bin/env bash
# Busca vagas das ÚLTIMAS 24 HORAS e acrescenta as novas a vagas.csv.
# (É o que o GitHub Actions roda sozinho todo dia às 06:00 BRT.)
# Uso: ./buscar_24h.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo "Criando ambiente virtual e instalando dependências..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

.venv/bin/python job_hunter.py --since-days 1 --out vagas.csv --config config.yaml
