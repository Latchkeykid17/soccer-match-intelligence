"""
Choose the model connection.

Settings come from environment variables or a `.env` file in the matchsim folder:

  Option A (simplest, API key from your Foundry resource):
    AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com
    AZURE_OPENAI_API_KEY=<key>
    AZURE_OPENAI_MODEL=<deployment name, e.g. gpt-4.1-mini>

  Option B (Microsoft Foundry project with your Azure sign-in, no keys):
    FOUNDRY_PROJECT_ENDPOINT=https://<resource>.services.ai.azure.com/api/projects/<project>
    FOUNDRY_MODEL=<deployment name>
    (run `az login` first)

  Neither set, or --offline: a built-in rule-based stand-in, for testing without Azure.
"""
import os

from .offline import OfflineChatClient


def load_env(path=".env"):
    """Minimal .env loader: KEY=VALUE lines, # comments. Existing env vars win."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def make_client(offline=False):
    """Return (chat_client, description)."""
    load_env()
    if offline:
        return OfflineChatClient(), "offline stand-in (no AI calls)"

    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    key = os.environ.get("AZURE_OPENAI_API_KEY")
    model = os.environ.get("AZURE_OPENAI_MODEL")
    if endpoint and key and model:
        from agent_framework.openai import OpenAIChatClient
        client = OpenAIChatClient(model=model, azure_endpoint=endpoint.rstrip("/"), api_key=key)
        return client, f"Microsoft Foundry model '{model}' (API key)"

    project = os.environ.get("FOUNDRY_PROJECT_ENDPOINT")
    fmodel = os.environ.get("FOUNDRY_MODEL")
    if project and fmodel:
        from agent_framework.foundry import FoundryChatClient
        from azure.identity import AzureCliCredential
        client = FoundryChatClient(project_endpoint=project, model=fmodel, credential=AzureCliCredential())
        return client, f"Microsoft Foundry project model '{fmodel}' (Azure sign-in)"

    return OfflineChatClient(), "offline stand-in (no Azure settings found)"
