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
        # Patch ENV_FILE_PATH in env_manager and use clean environment
        with patch('tools.env_manager.ENV_FILE_PATH', temp_env_path):
            with patch.dict(os.environ, {'MY_ENCODED_KEY': 'shell_override_value'}, clear=True):
                credentials = load_credentials()
                
                assert credentials.get('MY_ENCODED_KEY') == 'shell_override_value', "Failed to prefer shell environment variable"
                assert credentials.get('CLASS_CATALOG_SHEET_URL') == 'https://docs.google.com/spreadsheets/d/test/edit#gid=0', "Failed to fallback to plaintext value"
    finally:
        os.remove(temp_env_path)


def test_llm_setup_rejects_legacy_provider_even_when_shell_has_endpoint():
    from tools.l5_inbox_mas_context import get_llm_config, setup_llm_env

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(f"OPENAI_COMPATIBLE_URL={encode_credential('https://configured.example/v1')}\n")
        f.write(f"OPENAI_COMPATIBLE_KEY={encode_credential('configured-key')}\n")
        f.write(f"OPENAI_COMPATIBLE_MODELS={encode_credential('configured-model')}\n")
        temp_env_path = f.name

    try:
        with patch("tools.env_manager.ENV_FILE_PATH", temp_env_path), patch.dict(
            os.environ,
            {
                "OPENAI_API_BASE": "http://10.0.1.42:8317/v1",
                "OPENAI_API_KEY": "stale-key",
                "ADK_MODEL": "openai/stale-model",
            },
            clear=True,
        ):
            try:
                get_llm_config()
                assert False, "legacy OpenAI-compatible config must not be selected"
            except RuntimeError as exc:
                assert "Gemini credentials missing" in str(exc)
    finally:
        os.remove(temp_env_path)


def test_llm_setup_selects_native_gemini_from_project_env():
    from tools.l5_inbox_mas_context import get_llm_config, setup_llm_env

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        f.write(f"GOOGLE_API_KEY={encode_credential('AIza-test-key')}\n")
        f.write(f"GEMINI_MODEL={encode_credential('gemini-2.5-flash-lite')}\n")
        temp_env_path = f.name

    try:
        with patch("tools.env_manager.ENV_FILE_PATH", temp_env_path), patch.dict(
            os.environ,
            {
                "OPENAI_API_BASE": "http://stale.example/v1",
                "OPENAI_API_KEY": "stale-key",
                "ADK_MODEL": "openai/stale-model",
            },
            clear=True,
        ):
            config = get_llm_config()
            assert config == {
                "provider": "google",
                "api_key": "AIza-test-key",
                "model": "gemini-2.5-flash-lite",
            }

            setup_llm_env()
            assert os.environ["GOOGLE_API_KEY"] == "AIza-test-key"
            assert os.environ["ADK_MODEL"] == "gemini-2.5-flash-lite"
            assert "OPENAI_API_BASE" not in os.environ
            assert "OPENAI_API_KEY" not in os.environ
    finally:
        os.remove(temp_env_path)
