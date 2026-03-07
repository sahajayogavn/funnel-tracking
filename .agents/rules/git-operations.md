---
trigger: always_on
glob: "*"
description: Git Operations Rules
---

# Git Operations Rules

These rules govern how the Agent interacts with the Git repository.

1. **Explicit Permission Required**: The Agent **MUST ALWAYS** ask the user for explicit permission before executing any `git commit` or `git push` command.
2. **Pre-commit Checks**: Before asking for permission to commit, the Agent must ensure all unit tests pass locally and code coverage meets the required threshold (as defined in `rule:devops-qa`).
3. **Commit Messages**: When permission is granted, the commit message must be in English and should include the relevant Universal IDs associated with the changes (e.g., `code:tool-fbpage-001`).
