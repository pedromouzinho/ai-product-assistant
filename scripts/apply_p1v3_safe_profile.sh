#!/bin/bash
set -euo pipefail

RESOURCE_GROUP="${RESOURCE_GROUP:?Set RESOURCE_GROUP}"
PLAN_NAME="${PLAN_NAME:?Set PLAN_NAME}"
AUTOSCALE_NAME="${AUTOSCALE_NAME:-app-service-plan-autoscale}"
MAIN_APP="${MAIN_APP:?Set MAIN_APP}"
WORKER_APP="${WORKER_APP:?Set WORKER_APP}"
PYTHON_RUNTIME="${PYTHON_RUNTIME:-PYTHON:3.12}"

MAIN_STARTUP="${MAIN_STARTUP:-bash /home/site/wwwroot/startup.sh}"
WORKER_STARTUP="${WORKER_STARTUP:-bash /home/site/wwwroot/startup_worker.sh}"

MAIN_DEVOPS_INDEX="${MAIN_DEVOPS_INDEX:-story-devops-index}"
MAIN_OMNI_INDEX="${MAIN_OMNI_INDEX:-story-knowledge-index}"
WORKER_MODE="${WORKER_MODE:-both}"

echo "Applying safe P1v3 profile to ${MAIN_APP} in ${RESOURCE_GROUP}..."

az account show >/dev/null

if ! az webapp show --resource-group "$RESOURCE_GROUP" --name "$WORKER_APP" >/dev/null 2>&1; then
  echo "Creating worker app ${WORKER_APP} on plan ${PLAN_NAME}..."
  az webapp create \
    --resource-group "$RESOURCE_GROUP" \
    --plan "$PLAN_NAME" \
    --name "$WORKER_APP" \
    --runtime "$PYTHON_RUNTIME" \
    >/dev/null
fi

echo "Configuring main app..."
az webapp config set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$MAIN_APP" \
  --startup-file "$MAIN_STARTUP" \
  --always-on true \
  >/dev/null

az webapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$MAIN_APP" \
  --settings \
    STARTUP_FAIL_FAST=false \
    DEVOPS_INDEX="$MAIN_DEVOPS_INDEX" \
    OMNI_INDEX="$MAIN_OMNI_INDEX" \
    UPLOAD_INLINE_WORKER_RUNTIME_ENABLED=true \
  >/dev/null

echo "Configuring worker app (staged, not cut over)..."
az webapp config set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WORKER_APP" \
  --startup-file "$WORKER_STARTUP" \
  --always-on true \
  >/dev/null

az webapp config appsettings set \
  --resource-group "$RESOURCE_GROUP" \
  --name "$WORKER_APP" \
  --settings \
    STARTUP_FAIL_FAST=false \
    DEVOPS_INDEX="$MAIN_DEVOPS_INDEX" \
    OMNI_INDEX="$MAIN_OMNI_INDEX" \
    UPLOAD_INLINE_WORKER_RUNTIME_ENABLED=false \
    WORKER_MODE="$WORKER_MODE" \
    WEBSITES_PORT=8000 \
    PORT=8000 \
  >/dev/null

echo "Applying autoscale 2/2/3 and warming the plan to 2 workers..."
az monitor autoscale update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$AUTOSCALE_NAME" \
  --min-count 2 \
  --count 2 \
  --max-count 3 \
  >/dev/null

az appservice plan update \
  --resource-group "$RESOURCE_GROUP" \
  --name "$PLAN_NAME" \
  --number-of-workers 2 \
  >/dev/null

echo
echo "Applied:"
echo "- Main app uses story indexes and keeps inline async workers enabled."
echo "- App Service Plan autoscale is 2/2/3 with 2 warm workers."
echo "- Worker app is provisioned, running, and exposing /health."
