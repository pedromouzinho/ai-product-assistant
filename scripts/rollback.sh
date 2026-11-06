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
MAX_WAIT=120
POLL_INTERVAL=5

printf "=== AI Product Assistant Rollback: swap back production -> staging ===\n\n"

if [ "${SLOT_EXISTS:-0}" = "0" ]; then
  printf "ERRO: o slot '%s' não existe no App Service '%s'.\n" "$SLOT" "$APP_NAME"
  printf "Não há rollback por swap disponível no estado atual.\n"
  printf "Host de produção atualmente resolvido: %s\n" "$PROD_URL"
  printf "Usa redeploy da versão anterior ou provisiona um slot antes de depender deste script.\n"
  exit 2
fi

az webapp deployment slot swap \
  --name "$APP_NAME" \
  --resource-group "$RG" \
  --slot "$SLOT" \
  --target-slot production

printf "\n⏳ A aguardar readiness de production...\n"
elapsed=0
status="000"
while [ "$elapsed" -lt "$MAX_WAIT" ]; do
  status="$(curl -s -o /dev/null -w "%{http_code}" "$PROD_URL/health" 2>/dev/null || echo "000")"
  if [ "$status" = "200" ]; then
    printf "  ✅ /health respondeu 200 após %ss\n" "$elapsed"
    break
  fi
  printf "  ⏳ %ss — status=%s, novo check em %ss\n" "$elapsed" "$status" "$POLL_INTERVAL"
  sleep "$POLL_INTERVAL"
  elapsed=$((elapsed + POLL_INTERVAL))
done

if [ "$status" != "200" ]; then
  printf "\n❌ Rollback não validado: production não respondeu 200 em %ss (status=%s)\n" "$MAX_WAIT" "$status"
  exit 1
fi

printf "\n🔍 A correr smoke test pós-rollback...\n"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/smoke_test.py" "$PROD_URL"

printf "\n✅ Rollback concluído e validado.\n"
