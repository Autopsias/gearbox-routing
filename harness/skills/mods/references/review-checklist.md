# Review checklist for a mod

Use it for a mod of another author before the owner loads it, and for a gearbox mod before
the owner tries it. The verdict is **LOAD**, **LOAD WITH CHANGES** (name them) or
**DO NOT LOAD** (name the finding). A review runs no code of the mod.

## 1. Get the files without running them

- A repo: `git clone --depth 1 <url> <scratch>/<name>`. Do not install it and do not pass it
  to `--plugin-dir`.
- An installed plugin: read it in `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`.
- For a mod of another author: `gh repo view <owner>/<repo> --json
  stargazerCount,pushedAt,licenseInfo,isArchived`. The owner's rule for a new dependency
  applies: maintained in the last weeks, and widely used. Say which Claude Code version the
  README names as tested, and compare it with `claude --version`.

## 2. What the engine reads

```bash
claude plugin validate --json <dir>
```

The `hooks:` note lists the events the mod receives. The `calls:` note lists the mods API
methods it calls. The engine refuses a mod whose calls this command cannot read, so the two
lines are complete. An `env reads:` or `env writes:` line names each variable.

## 3. Calls that need a reason

For each call below that the mod makes, find the line in the source and say in one sentence
why the mod needs it. A call with no reason that fits the stated purpose is a finding.

| Call | What it can do |
|---|---|
| `$.fs.read`, `$.fs.write` | read or write any file the owner can; permission `deny` rules do not cover it |
| `$.process.run`, `$.process.spawn` | start programs as the owner, outside the Bash sandbox |
| `$.http.fetch` | send data off the machine (client work stays on this machine) |
| `$.env.get`, `$.settings.read` | read variables and settings, which can hold keys |
| `$.env.set` | change what every later command and MCP server runs with |
| `$.mcp.call` | call a tool of a connected MCP server |
| `$.model.complete` | spend the owner's usage |
| `$.prompt.submit` | send a prompt as if the owner typed it |
| `$.session.send` | send a message that another session's Claude reads |

Read the full table in the `admin` docs page ("Review what a mod can do") in each run; the
list above can be behind the engine.

## 4. Hooks that need a reason

| Hook | Why it matters |
|---|---|
| `tool.call` | sees every tool call and can rewrite it. When it returns without `next`, the settings hooks of the harness (`PreToolUse`) do not run for that call |
| `tool.check` | decides after the permission rules and the settings hooks, and can approve a call they blocked. On a machine with no managed settings and no Team or Enterprise plan the built-in guard does not load, and the docs give no promise that a `deny` rule then holds over the mod |
| `prompt.submit` | sees and can rewrite every prompt |
| `session.append` | can rewrite each row of the conversation before it is stored |
| `ui.render{component=AskUserQuestion}` | can redraw the dialog in which Claude asks the owner a question |

## 5. Rules of the harness

A finding on any of these is DO NOT LOAD for a mod of another author, and a fix before the
owner tries it for a gearbox mod.

- **No approval.** No `tool.check` hook returns `allow`. No hook approves a permission
  request. The guards of the harness (`git-tree-guard`, `governor-hook`,
  an egress guard, the auto mode classifier) keep the last word.
- **`next` on every path that does not deny.** A `tool.call` hook returns `{ deny }` or
  calls `next`. It never returns its own result in place of the tool.
- **A hook that blocks fails closed.** It has a `.catch` handler that denies. Without one,
  the engine skips a hook that throws or times out, and the call runs.
- **Waits are inside a mods API call.** A hook has 10 seconds of its own time (50 ms for
  `prompt.edit`). Time inside `$.ui.ask` does not count; time awaiting its own promise does.
- **No secret leaves.** No `$.http.fetch` with content from `$.env.get`, `$.settings.read`,
  a prompt or a tool result. No secret in `$.store`, a toast or a log line.
- **Text from outside is data.** A mod that puts fetched text into a prompt, or into what
  Claude reads, opens a path for injected instructions. Name the path.

## 6. Quality

- **It does something where nothing draws.** In `claude -p`, the VS Code panel and a cloud
  session a drawing does not appear. The mod checks the surface and falls back to a
  command's text reply or a transcript line, or the review says that it does nothing there.
- **A pane has a fallback.** Claude Code does not place a pane in a narrow terminal. The mod
  checks `isPlaced` and draws in the band above the prompt instead (the pattern of
  Anthropic's `blast-radius` sample).
- **The safe answer has the focus.** In a dialog that holds a call, Enter refuses.
- **State is in the right place.** A module variable resets at each reload. `$.state` lasts
  for the session and resets on `/clear`, `/resume` and `/branch`. `$.store` lasts across
  sessions, with 4 MiB in total.
- **A render hook is cheap.** `ui.render` runs on every draw. It reads state and returns a
  tree. It does not read files, start processes or call a model.
- **Tests exist and pass.** `claude plugin test <dir>` is green, and every hook that blocks
  or rewrites has a test for each branch.
- **Names are legal.** `validate` fails a plugin name that looks like one of Anthropic's
  (for example one that starts with `claude-`).
- **`validate --strict` is clean**, or each warning is named in the review.

## 7. The verdict

Give the verdict in one sentence. Then give, in plain words: what the mod sees, what it can
change, what it reaches outside the session, and each finding with its `file:line`. For
LOAD, give the exact command the owner runs. Add the ledger row.
