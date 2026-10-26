#!/usr/bin/env bash
# One command to get MatchLens ready to submit. Run from the matchsim folder in your codespace:
#
#   bash finish.sh                                 # checks, tests, AI match, GitHub, deploy, demo link
#   bash finish.sh --video https://youtu.be/XXXX   # after uploading the video: adds the link and saves
#
# Safe to run again: it skips anything already done.
set -u
cd "$(dirname "$0")"
ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "STOPPED: not inside the GitHub repo folder"; exit 1; }
step() { echo; echo "== $1"; }
fail() { echo; echo "STOPPED: $1"; exit 1; }
save() {
  git -C "$ROOT" add -A
  if git -C "$ROOT" diff --cached --quiet; then echo "   nothing new to save"; else
    git -C "$ROOT" commit -q -m "$1" || fail "git commit failed. Run: git config --global user.name \"Your Name\"; git config --global user.email you@example.com"
  fi
  git -C "$ROOT" push -q || fail "git push failed (see the message above)"
  echo "   saved to GitHub"
}

step "Checking your secret key stays private"
grep -qx '.env' .gitignore || fail ".gitignore is missing '.env'. Don't push; ask for help."
if git -C "$ROOT" ls-files | grep -q '\.env$'; then
  fail "a .env file is already on GitHub. Remove it (git rm --cached matchsim/.env) and create a new key in Azure."
fi
echo "   ok: .env is never uploaded"

# ------------------------------------------------------------------ video link only
if [ "${1:-}" = "--video" ]; then
  VIDEO="${2:-}"
  case "$VIDEO" in https://*) ;; *) fail "give the full video link, e.g. bash finish.sh --video https://youtu.be/abc123";; esac
  sed -i "s#_add your YouTube link here_#$VIDEO#; s#_YouTube link_#$VIDEO#" "$ROOT/README.md" "$ROOT/SUBMISSION.md"
  step "Saving the video link"
  save "Add demo video link"
  echo; echo "Done. Everything is ready: open SUBMISSION.md and copy it into the hackathon form."
  exit 0
fi

[ -f .env ] || fail "no .env found. Run: bash setup_azure.sh"

step "1/6 Installing and testing"
pip install -q -r requirements.txt >/dev/null 2>&1 || fail "pip install failed"
python -m pytest -q tests >/tmp/matchlens_tests.log 2>&1 || { tail -20 /tmp/matchlens_tests.log; fail "tests failed"; }
tail -1 /tmp/matchlens_tests.log

step "2/6 AI-explained demo match"
M=matches/match_00007
if [ -s "$M/explained.jsonl" ] && grep -q '"verified"' "$M/recaps.json" 2>/dev/null; then
  echo "   already done"
else
  echo "   the agents are explaining every moment in 5 languages (5-10 minutes, keep this tab open)"
  python explain_match.py "$M" --languages fr,es,pt,de,ar > /tmp/matchlens_explain.log 2>&1 \
    || { tail -20 /tmp/matchlens_explain.log; fail "the AI run failed"; }
  tail -1 /tmp/matchlens_explain.log
fi
grep -q '"source": "ai"' "$M/explained.jsonl" || fail "the match was explained offline, not by Azure. Check .env"

step "3/6 Making sure the repo is public"
if command -v gh >/dev/null 2>&1; then
  VIS=$(gh repo view --json visibility -q .visibility 2>/dev/null || echo unknown)
  if [ "$VIS" = "PUBLIC" ]; then echo "   ok: public"
  elif [ "$VIS" = "PRIVATE" ] || [ "$VIS" = "INTERNAL" ]; then
    echo "   The repo is $VIS. Judges must be able to open it."
    read -r -p "   Make it public now? (y/n) " ans
    if [ "$ans" = "y" ]; then gh repo edit --visibility public --accept-visibility-change-consequences && echo "   now public"
    else echo "   left $VIS: change it before submitting (Settings > Change visibility)"; fi
  else echo "   couldn't check. Open your repo in a private browser window to confirm it's public."; fi
fi

step "4/6 Saving to GitHub"
save "MatchLens: final version with AI-explained match"

step "5/6 Putting the app online (Azure Container Apps, 5-10 minutes)"
if grep -q "azurecontainerapps.io" "$ROOT/README.md"; then
  URL=$(grep -o 'https://[^ )]*azurecontainerapps.io' "$ROOT/README.md" | head -1)
  echo "   already online: $URL (to redeploy after changes: bash deploy_azure.sh)"
else
  az account show >/dev/null 2>&1 || fail "your Azure sign-in expired. Run 'az login --use-device-code' (add the --tenant part if it asks), then bash finish.sh again"
  az provider register --namespace Microsoft.ContainerRegistry --wait -o none
  bash deploy_azure.sh 2>&1 | tee /tmp/matchlens_deploy.log
  URL=$(grep -o 'https://[^ ]*azurecontainerapps.io' /tmp/matchlens_deploy.log | tail -1)
  [ -n "$URL" ] || fail "deployment didn't finish (see above)"
  sed -i "s#_add your Azure Container Apps link here_#$URL#; s#_Azure Container Apps link_#$URL#" "$ROOT/README.md" "$ROOT/SUBMISSION.md"
fi

step "6/6 Saving the demo link"
save "Add live demo link"

echo
echo "=============================================================="
echo " Ready, except the video."
echo " Live demo:  $URL"
echo " 1. Open the demo in a private window and press Start match."
echo " 2. Record the video (see VIDEO_SCRIPT.md), upload to YouTube as Public or Unlisted."
echo " 3. Run:  bash finish.sh --video https://youtu.be/your-video"
echo " 4. Copy SUBMISSION.md into the form at https://aka.ms/insidethegame and submit."
echo "=============================================================="
