# Keeping a mod

A mod that Claude writes in a session lives in `~/.claude/dev-mods/<session id>/<name>/`. It
loads only in that session, and Claude Code deletes the folder after `cleanupPeriodDays`. To
keep a mod, it moves into the harness source repo and gets a load route.

The owner approves each step below by name. A kept mod runs in every session with the
owner's permissions, so keeping one widens what the harness can do. That is the owner's call.

## 1. Move it into the source repo

The home is `mods/<name>/` in `~/your-private-harness`. Never write it under `~/.claude`: that
tree is the deploy target.

```bash
cp -R ~/.claude/dev-mods/<session id>/<name> ~/your-private-harness/mods/<name>
```

**The first kept mod needs two lines in `.gitignore`.** The repo ignores every top-level path
that is not named there, so `mods/` is not versioned until the file says so:

```gitignore
!/mods/
mods/*/.claude-plugin/types/
```

The second line matters for route A below. The engine writes type files into
`.claude-plugin/types/` of a mod that it loads from a directory. In the deploy target those
files are dirt, and `gearbox deploy` refuses to run over dirt in harness source.

Fill `author` in `plugin.json`, and write in a `README.md` which Claude Code version the mod
was tested with (`claude --version`).

## 2. Choose the load route

Both routes are documented. Neither route is proven until the first keep (step 3).

| | A. Plugin directory | B. Local marketplace |
|---|---|---|
| How | `env.CLAUDE_CODE_PLUGIN_DIRS` in `settings.json`: absolute paths, one for each mod, separated by `:` | `mods/.claude-plugin/marketplace.json`, then `claude plugin marketplace add ~/.claude/mods` and `claude plugin install <name>@<marketplace>` |
| An edit arrives | with `gearbox deploy`; open terminal sessions reload the mod, and desktop or SDK sessions only with `env.CLAUDE_CODE_PLUGIN_DIR_WATCH=1` (without it they keep the version they started with) | only after a version bump and `claude plugin update`: Claude Code runs a cached copy for each version |
| Turn one mod off | remove its path from the variable | disable it in `/plugin` |
| Cost | the engine writes type files into the mod's directory; the directory is a protected path, so an edit by Claude asks for approval | two more steps for each change; the cached copy can be behind the repo with no sign |

Recommended: route A while the harness has few mods of its own. Route B fits a mod that
other people install.

`env` in `settings.json` is harness source, and Claude does not edit its own settings to
widen its access. Give the owner the exact line, and make the edit in `~/your-private-harness`
only after the owner says yes to that line.

## 3. Prove it loads

The owner runs these; Claude does not start `claude` from the Bash tool for this.

1. Commit, push and run `~/.claude/scripts/gearbox deploy`.
2. Start a new session and run `/plugin`. The dim line under the tabs names each mod that
   loaded, for example `1 mod active · <name>`.
3. Use the mod once: run its command, or do the thing its hook reacts to.

If the mod is not named, read the `troubleshoot` docs page ("Find out why a mod does
nothing"). `claude --debug` writes one line for every module that loads or is refused.

## 4. After each `claude update`

Events and methods can change between releases. For every kept mod:

```bash
for m in ~/your-private-harness/mods/*/; do claude plugin validate "$m" && claude plugin test "$m"; done
```

A red result is a finding for `/mods improve`.

## 5. Record it

Add the mod to `docs/mods/LEDGER.md` under "Kept mods": name, version, load route, the
`hooks:` and `calls:` lines, the pain it answers, and the date.
