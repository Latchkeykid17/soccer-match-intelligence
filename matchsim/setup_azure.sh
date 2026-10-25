#!/usr/bin/env bash
# One-time Azure setup: creates a Microsoft Foundry resource + project, deploys a small
# model, and writes the connection settings into .env (which is never committed).
#
# Usage (from the matchsim folder, after `az login --use-device-code`):
#   bash setup_azure.sh
# Optional overrides:
#   REGION=swedencentral PREFIX=myname bash setup_azure.sh

REGION="${REGION:-eastus2}"
PREFIX="${PREFIX:-matchintel}"
SUFFIX="$(head -c 400 /dev/urandom | tr -dc 'a-z0-9' | head -c 5)"
RG="${RG:-rg-match-intelligence}"
RESOURCE="${RESOURCE:-${PREFIX}${SUFFIX}}"
PROJECT="${PROJECT:-match-intelligence}"
MODELS=("gpt-4.1-mini" "gpt-5-mini" "gpt-4o-mini")

fail() { echo; echo "STOPPED: $1"; exit 1; }

echo "== Checking Azure sign-in"
az account show --query "{subscription:name, user:user.name}" -o table || fail "run: az login --use-device-code"

echo "== Creating resource group $RG in $REGION"
az group create --name "$RG" --location "$REGION" -o none || fail "could not create the resource group"

echo "== Creating Microsoft Foundry resource $RESOURCE (takes a minute)"
if ! az cognitiveservices account create --name "$RESOURCE" --resource-group "$RG" --kind AIServices \
     --sku S0 --location "$REGION" --custom-domain "$RESOURCE" --assign-identity \
     --allow-project-management true --yes -o none 2>/tmp/foundry_err.txt; then
  echo "   (retrying without project management, older Azure CLI)"
  az cognitiveservices account create --name "$RESOURCE" --resource-group "$RG" --kind AIServices \
     --sku S0 --location "$REGION" --custom-domain "$RESOURCE" --yes -o none \
     || { cat /tmp/foundry_err.txt; fail "could not create the Foundry resource"; }
fi

echo "== Creating Foundry project $PROJECT"
az cognitiveservices account project create --name "$RESOURCE" --resource-group "$RG" \
   --project-name "$PROJECT" --location "$REGION" -o none 2>/dev/null \
   || echo "   (project not created; that's fine, the model works without it. Try 'az upgrade' later.)"

echo "== Choosing a model available in $REGION"
MODEL=""; VERSION=""
for m in "${MODELS[@]}"; do
  VERSION=$(az cognitiveservices model list --location "$REGION" \
    --query "[?model.name=='$m' && model.format=='OpenAI'] | [?contains(model.skus[].name, 'GlobalStandard')].model.version | [-1]" -o tsv 2>/dev/null)
  if [ -n "$VERSION" ] && [ "$VERSION" != "None" ]; then MODEL="$m"; break; fi
done
[ -n "$MODEL" ] || fail "none of ${MODELS[*]} is available in $REGION. Try: REGION=swedencentral bash setup_azure.sh"
echo "   using $MODEL version $VERSION"

echo "== Deploying $MODEL"
DEPLOYED=""
for cap in 50 20 10 5; do
  if az cognitiveservices account deployment create --name "$RESOURCE" --resource-group "$RG" \
       --deployment-name "$MODEL" --model-name "$MODEL" --model-version "$VERSION" --model-format OpenAI \
       --sku-name GlobalStandard --sku-capacity "$cap" -o none 2>/dev/null; then
    DEPLOYED="$cap"; break
  fi
  echo "   capacity $cap not allowed on this subscription, trying smaller"
done
[ -n "$DEPLOYED" ] || fail "could not deploy the model (quota). Check Quotas in the Foundry portal."
echo "   deployed with ${DEPLOYED}K tokens per minute"

echo "== Writing .env"
KEY=$(az cognitiveservices account keys list --name "$RESOURCE" --resource-group "$RG" --query key1 -o tsv)
[ -n "$KEY" ] || fail "could not read the API key"
cat > .env <<EOF
# Created by setup_azure.sh. Keep this file private: it is listed in .gitignore.
AZURE_OPENAI_ENDPOINT=https://${RESOURCE}.openai.azure.com
AZURE_OPENAI_API_KEY=${KEY}
AZURE_OPENAI_MODEL=${MODEL}
FOUNDRY_RESOURCE=${RESOURCE}
FOUNDRY_RESOURCE_GROUP=${RG}
FOUNDRY_PROJECT_ENDPOINT=https://${RESOURCE}.services.ai.azure.com/api/projects/${PROJECT}
EOF

echo
echo "Done. Test it with:"
echo "  python explain_match.py matches/match_00007 --limit 3 --languages fr"
