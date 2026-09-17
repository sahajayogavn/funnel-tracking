import os
import tempfile
import base64
from unittest.mock import patch
import sys

# Ensure tools is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from tools.env_manager import load_credentials, encode_credential

def test_load_credentials_handles_plaintext_and_encoded():
    # Setup a temporary .env file
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        # One valid base64 value
        encoded_val = encode_credential('secret_value_123')
        f.write(f"MY_ENCODED_KEY={encoded_val}\n")
        
        # One plain text value
        f.write("CLASS_CATALOG_SHEET_URL=https://docs.google.com/spreadsheets/d/test/edit#gid=0\n")
        
        # Empty line and comment
        f.write("\n")
        f.write("# This is a comment\n")
        
        temp_env_path = f.name
        
    try:
        # Patch ENV_FILE_PATH in env_manager
        with patch('tools.env_manager.ENV_FILE_PATH', temp_env_path):
            credentials = load_credentials()
            
            assert credentials.get('MY_ENCODED_KEY') == 'secret_value_123', "Failed to decode base64 value"
            assert credentials.get('CLASS_CATALOG_SHEET_URL') == 'https://docs.google.com/spreadsheets/d/test/edit#gid=0', "Failed to fallback to plaintext value"
            assert os.environ.get('MY_ENCODED_KEY') == 'secret_value_123', "Environment variable for encoded key not set"
            assert os.environ.get('CLASS_CATALOG_SHEET_URL') == 'https://docs.google.com/spreadsheets/d/test/edit#gid=0', "Environment variable for plaintext key not set"
            
    finally:
        os.unlink(temp_env_path)
