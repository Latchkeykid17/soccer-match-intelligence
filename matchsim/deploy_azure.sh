#!/usr/bin/env bash
# Put MatchLens online with Azure Container Apps (gives judges a public link).
#
# Run in Azure Cloud Shell (portal.azure.com, the >_ icon), after setup_azure.sh:
#   git clone https://github.com/<you>/soccer-match-intelligence
#   cd soccer-match-intelligence/matchsim
#   cp ~/.env .env          # the settings setup_azure.sh wrote in your Cloud Shell home
#   bash deploy_azure.sh
APP="${APP:-matchlens}"
fail() { echo; echo "STOPPED: $1"; exit 1; }

[ -f .env ] || fail "no .env here. Run setup_azure.sh first, then: cp ~/.env .env"
set -a; . ./.env; set +a
RG="${FOUNDRY_RESOURCE_GROUP:-rg-match-intelligence}"
REGION="${REGION:-$(az group show -n "$RG" --query location -o tsv 2>/dev/null)}"
REGION="${REGION:-eastus2}"
[ -n "$AZURE_OPENAI_API_KEY" ] || fail ".env has no AZURE_OPENAI_API_KEY"

echo "== Preparing Azure Container Apps (first time takes a few minutes)"
az extension add --name containerapp --upgrade -y -o none 2>/dev/null
az provider register -n Microsoft.App --wait -o none
az provider register -n Microsoft.OperationalInsights --wait -o none
az provider register -n Microsoft.ContainerRegistry --wait -o none
az group create -n "$RG" -l "$REGION" -o none

echo "== Building and deploying $APP from this folder"
az containerapp up --name "$APP" --resource-group "$RG" --location "$REGION" \
  --source . --ingress external --target-port 8000 \
  --env-vars AZURE_OPENAI_ENDPOINT="$AZURE_OPENAI_ENDPOINT" AZURE_OPENAI_MODEL="$AZURE_OPENAI_MODEL" \
  || fail "deployment failed (see the message above)"

echo "== Storing the API key as a secret (not as plain text)"
az containerapp secret set -n "$APP" -g "$RG" --secrets openai-key="$AZURE_OPENAI_API_KEY" -o none
az containerapp update -n "$APP" -g "$RG" --set-env-vars AZURE_OPENAI_API_KEY=secretref:openai-key \
  --min-replicas 1 --max-replicas 3 -o none

URL=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "MatchLens is live at: https://$URL"
