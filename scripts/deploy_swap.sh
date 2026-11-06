#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${APP_NAME:?Set APP_NAME to your App Service name}"
RG="${RG:?Set RG to your resource group}"
SLOT="${SLOT:-staging}"

DEFAULT_HOST="$(az webapp show --name "$APP_NAME" --resource-group "$RG" --query defaultHostName -o tsv)"
if [ -z "$DEFAULT_HOST" ]; then
  printf "ERROR: could not resolve the default host name for %s\n" "$APP_NAME" >&2
  exit 2
fi
PROD_URL="${PROD_URL:-https://${DEFAULT_HOST}}"
SLOT_EXISTS="$(az webapp deployment slot list --name "$APP_NAME" --resource-group "$RG" --query "[?name=='${SLOT}'] | length(@)" -o tsv)"
SLOT_HOST="$(az webapp deployment slot list --name "$APP_NAME" --resource-group "$RG" --query "[?name=='${SLOT}'].defaultHostName | [0]" -o tsv)"
BASE_URL="${BASE_URL:-https://${SLOT_HOST}}"

printf "=== AI Product Assistant Deploy: swap staging -> production ===\n\n"

if [ "${SLOT_EXISTS:-0}" = "0" ]; then
  printf "ERRO: o slot '%s' não existe no App Service '%s'.\n" "$SLOT" "$APP_NAME"
  printf "Este script só funciona com deployment slot e swap real.\n"
  printf "Host de produção atualmente resolvido: %s\n" "$PROD_URL"
  printf "Opções: criar o slot '%s' ou usar um fluxo de deploy in-place separado.\n" "$SLOT"
  exit 2
fi

printf "1. Running smoke test on staging...\n"
python3 scripts/smoke_test.py "$BASE_URL"

printf "\n2. Swapping staging -> production...\n"
az webapp deployment slot swap \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --slot "$SLOT" \
  --target-slot production

printf "\n3. Verifying production...\n"
sleep 5
python3 scripts/smoke_test.py "$PROD_URL"

printf "\nDeploy swap concluido com sucesso.\n"
