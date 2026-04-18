# PostgreSQL Development Guide

**This file is manually maintained. Do not auto-edit. Suggest changes to the user instead.**

PostgreSQL internals development, patch implementation, cross-platform testing project.

## Core Principles

### PostgreSQL Regression Testing
- `.out` files capture expected output **including errors**
- **Important**: do not wrap error-producing SQL in DO blocks
- The testing framework expects to see the errors
- When analysing diffs, distinguish platform-specific differences (paths, pointer addresses) from real bugs

### Build System
- **Prefer the Make-based build** (`pg-configure`, `pg-make`, `pg-check`)
- Build/test helpers: `pg-configure`, `pg-make`, `pg-install`, `pg-check`, `pg-regress`
- Use environment variables for paths: `${PG_SOURCE}`, `${PGDATA}`, `${PGHOME}`

## Git Workflow

### Never Do
- **Do not run git commands directly (`git add`, `git commit`, `git push`, etc.)**
- Provide guidance only when the user explicitly asks
- Never use the `git status -uall` flag

### Commit Messages
- **Default format**: a concise single line in English unless otherwise requested
- Follow the PostgreSQL commit style
- **Never add AI markers**: no Co-authored-by tags, no AI-related annotations
- Korean commit messages only if the user explicitly asks

### Branches
- Work on whichever branch is currently checked out

## Code Changes

### Workflow
1. **Read the file first**: always Read before proposing a change
2. Understand the existing code before suggesting edits
3. No unsolicited refactors, features, or "improvements"

### Before Implementation
- For complex tasks (testing, porting) explain first:
  1. Your understanding of the PostgreSQL subsystem
  2. The proposed approach
  3. Potential pitfalls or considerations
  4. The build/test commands you'll use
- Wait for user confirmation before writing code

## Writing Test Cases

### Pre-analysis
- First check whether the code path is actually testable
- Some conditions are compile-time or structurally unreachable
- Identify untestable paths early and suggest alternatives (Assert statements, etc.)

### Procedure
1. Confirm the code path can actually run
2. Identify the required preconditions
3. Define the expected output/behaviour
4. Write the expected output accurately into the `.out` file
5. **Do not wrap error tests in DO blocks**

## Documentation & Translation

### Documentation Principles
- **Include only content present in the original source**
- Do not add extra explanations, elaborations, or details beyond the source material
- When updating SGML files:
  1. Read the original SGML file in full
  2. Extract only existing content — no additions
  3. Diff against the original to confirm nothing was added

### Korean Translations
- Translate the original content only; do not append commentary or examples
- Include the English term alongside Korean when helpful for technical terms

## Cross-platform Development

### Platform Differences
- macOS (Apple Silicon) and Linux
- Locale: Korean (ko_KR.UTF-8)
- When porting scripts, confirm command flags/options are available on the target platform
- Key differences:
  - Package manager: Homebrew (macOS) vs apt/yum (Linux)
  - Command options: `sed -i` vs `sed -i ''`
  - Valgrind: not usable on macOS (AMD64 Linux only)
  - Core dumps: lldb on macOS, gdb on Linux

### Coverage Analysis
- `pg-gcov <branch-base>..<branch>` form
- Example: `pg-gcov RPR-base..RPR`
- Coverage may differ between Mac and Linux

## Response Style

### Must-follow
- Keep responses concise and clear
- Avoid over-explanation and filler
- Stay within the scope the user explicitly requested
- Do not use emoji unless asked
- Reference code using the `file_path:line_number` form

### Tool Usage
- Use Edit, Read, Bash, Grep efficiently
- Run independent operations in parallel
- PostgreSQL build/test helpers (`pg-*`) are pre-defined — use them directly
- Use environment variables for path references
- After code changes, proactively suggest the relevant tests (e.g. `pg-regress <test-name>`)

## Key Command Reference

### Testing
- Full: `pg-check`
- Specific: `pg-regress <test1> [test2...]`
- List: `pg-regress-list`
- On failure: `git status` or `git diff` to locate `.diffs` files

### Building
- Configure: `pg-configure [release|debug|valgrind|coverage]`
- Build: `pg-make`
- Install: `pg-install`
- Clean: `pg-clean`

### Git Helpers
- `git-log`: output sized to the terminal
- `git-clean`: clean up untracked files
