## Token Efficiency: JSON Output Format

> **Canonical contract:** the orchestrator emits the shared **Uniform Findings Contract**
> (`~/.claude/commands/references/shared/findings-contract.md`). The flat status below is
> the **legacy per-agent shape** — it is the documented *input* to the contract's legacy
> adapter, NOT a second vocabulary. The adapter maps `fixed -> auto-fix`,
> `partial|failed -> ask-user`+blocking, and a **missing status -> ask-user**+blocking
> (never auto-fix). New agents SHOULD emit a `findings-contract/v1` finding directly.

**Legacy per-agent distilled JSON (mapped via the contract's legacy adapter):**

```json
{
  "status": "fixed|partial|failed",
  "issues_fixed": 3,
  "files_modified": ["path/to/file.py"],
  "quality_gates_passed": true,
  "staging_ready": true,
  "summary": "Brief description of fixes"
}
```

**DO NOT return:**
- Full file contents
- Verbose explanations
- Step-by-step execution logs

This reduces token usage by 80-90% per agent response.
