import os
import base64
import json

# code:tool-envmanager-001:credentials-encode-decode

ENV_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env')

def encode_credential(value: str) -> str:
    """Encode string value to base64 to obfuscate in .env file."""
    return base64.b64encode(value.encode('utf-8')).decode('utf-8')

def decode_credential(encoded_value: str) -> str:
    """Decode base64 value back to plaintext."""
    return base64.b64decode(encoded_value.encode('utf-8')).decode('utf-8')

def save_credentials(credentials: dict):
    """
    Save credentials dict encoded in .env.
    Usage example:
    save_credentials({
        'OPENAI_COMPATIBLE_URL': 'https://...',
        'OPENAI_COMPATIBLE_KEY': 'sk-...',
        'OPENAI_COMPATIBLE_MODELS': 'gpt-5.3-codex',
        'GOOGLE_SHEET_CREDENTIALS': '{...}',
        'FACEBOOK_FANPAGE_APP_TOKEN': 'EAA...',
        'TELEGRAM_BOT_TOKEN': '1234:ABC...',
        'TELEGRAM_CHAT_ID': '-100123...'
    })
    """
    with open(ENV_FILE_PATH, 'w') as f:
        for key, val in credentials.items():
            encoded_val = encode_credential(val)
            f.write(f"{key}={encoded_val}\n")
    print(f"DEBUG: Credentials strictly encoded and saved to {ENV_FILE_PATH}")

def load_credentials() -> dict:
    """
    Load encoded .env file and decode it for application usage.
    Returns a dictionary of key: plaintext_value.
    """
    credentials = {}
    if not os.path.exists(ENV_FILE_PATH):
        print(f"DEBUG: Warning - No .env file found at {ENV_FILE_PATH}")
        return credentials

    with open(ENV_FILE_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            if '=' in line:
                key, encoded_val = line.split('=', 1)
                try:
                    credentials[key] = decode_credential(encoded_val)
                    os.environ[key] = credentials[key] # Directly load to os environment
                except Exception as e:
                    print(f"DEBUG: Error decoding key {key}: {e}")
    
    print("DEBUG: Credentials successfully loaded and decoded into environment variables.")
    return credentials

if __name__ == '__main__':
    # CLI mode for testing implementation
    import argparse
    parser = argparse.ArgumentParser(description="Env Manager for encoding/decoding secure credentials in .env")
    parser.add_argument('--init', action='store_true', help='Initialize dummy .env file with encoded guards')
    parser.add_argument('--read', action='store_true', help='Read and print decoded credentials (dry run)')
    args = parser.parse_args()

    if args.init:
        dummy_data = {
            'OPENAI_COMPATIBLE_URL': 'https://api.openai.com/v1',
            'OPENAI_COMPATIBLE_KEY': 'your_api_key_here',
            'OPENAI_COMPATIBLE_MODELS': 'gpt-5.3-codex',
            'GOOGLE_SHEET_CREDENTIALS': 'your_google_json_string',
            'FACEBOOK_FANPAGE_APP_TOKEN': 'your_fb_token',
            'TELEGRAM_BOT_TOKEN': 'your_tg_token',
            'TELEGRAM_CHAT_ID': 'your_tg_chat_id'
        }
        save_credentials(dummy_data)
        print("Initialized encoded .env file securely.")
    
    if args.read:
        data = load_credentials()
        print("Decoded Data preview (for debug only):")
        for k, v in data.items():
            print(f" - {k}: {v[:5]}... (truncated)")

