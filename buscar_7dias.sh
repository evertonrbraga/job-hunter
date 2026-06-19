#!/usr/bin/env bash
# Busca vagas dos ÚLTIMOS 7 DIAS e popula/atualiza vagas.csv.
# Uso: ./buscar_7dias.sh
set -euo pipefail
cd "$(dirname "$0")"

# cria o ambiente virtual e instala dependências na primeira vez
if [ ! -d .venv ]; then
  echo "Criando ambiente virtual e instalando dependências..."
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi

.venv/bin/python job_hunter.py --since-days 7 --out vagas.csv --config config.yaml
