---
description: Workflow to write Python script for Agent Skills tools with isolated Python 3.13 environment
---

# Write Agent Tools Workflow

This workflow provides step-by-step instructions for writing Python scripts for Agent Skills tools, ensuring a correct setup with Python 3.13 using `pyenv`.

1. **Verify Python Environment**:
   Ensure you are in the isolated Python 3.13 environment.

   ```bash
   python --version # Should output Python 3.13.x
   ```

2. **Create New Tool File**:
   Create a new `.py` file inside the `tools/` directory. Be descriptive with the filename.

   ```bash
   touch tools/my_new_tool.py
   ```

3. **Implement the CLI Interface**:
   Use `argparse` to handle arguments so that the script can act as a standalone tool.

   ```python
   import argparse

   def main():
       parser = argparse.ArgumentParser(description="Description of the capability")
       # add arguments here
       args = parser.parse_args()
       # execute logic

   if __name__ == "__main__":
       main()
   ```

4. **Add Universal ID**:
   Include a Universal ID tag in the comments right above your core logic function/class:

   ```python
   # code:tool-name-001:specific-component
   def core_logic():
       pass
   ```

5. **Write Unit Tests**:
   Create a corresponding test file in the `tests/` directory and ensure it passes before finalizing the tool.

   ```bash
   python -m unittest tests/test_my_new_tool.py
   ```

6. **Log Execution & Ralph Loop**:
   Make sure to output logs to `./logs/` and include Universal ID in debug prints. For complex browser automation (e.g., Playwright CDP port 9222), you must store extensive diagnostics to `./logs/diagnostic/iteration-XXXX/` including `consoleLog`, script logs, and `DOM` states. Because we are working dynamically, you have to try multiple times (implement a "ralph loop" retry mechanism) until you can reach the exact target intent.
