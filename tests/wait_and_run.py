import time
import os
import subprocess

print("Polling .env file for changes...")
while True:
    with open(".env", "r") as f:
        text = f.read()
    if "TELEGRAM" in text or "VEVMRUdSQU0" in text:
        print("Detected changes in .env! Running test_live_hitl_batch.py...")
        subprocess.run([".venv/bin/python", "tests/test_live_hitl_batch.py"])
        break
    time.sleep(2)
