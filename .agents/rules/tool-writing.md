---
trigger: always_on
glob: "tools/*.py"
description: Tool Writing Rules
---

# Tool Writing Rules

These rules govern the creation and modification of Python tools within the `tools/` directory.

1. **CLI Design**: All Python scripts in the `tools/` directory (`tools/*.py`) must be designed and implemented to function as standalone CLI-like applications.
2. **Argument Parsing**: Use standard libraries like `argparse` to handle inputs so tools can be executed easily both manually and programmatically by the Agent.
3. **Universal IDs**: Any significant functionality within a tool must be tagged with a Component Universal ID in the code comments (e.g., `# code:tool-webhook-001:process-comment`).
4. **Testing**: Every new tool must be accompanied by its respective unit tests before being finalized.
5. **FrankenSQLite Storage**: For tools that fetch sequential data or need to preserve state, you must configure a local database structure and store this information using `FrankenSQLite`. Ensure the database schema explicitly stores the output reliably and can be analyzed efficiently.
