import requests
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tools.env_manager import load_credentials

creds = load_credentials()
bot_token = creds.get("TELEGRAM_BOT_TOKEN")
chat_id = creds.get("SYVN_TELEGRAM_GROUP_ID")

resp = requests.post(f"https://api.telegram.org/bot{bot_token}/setMessageReaction", json={
    "chat_id": chat_id,
    "message_id": 4, # The ID of the E2E message
    "reaction": [{"type": "emoji", "emoji": "💯"}],
    "is_big": True
})
print("Reaction response:", resp.text)
