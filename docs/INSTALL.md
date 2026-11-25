# Install Gearbox

Gearbox has two parts, and each part installs a different way.

| Part | What it is | How it installs |
|---|---|---|
| **Routing framework** (`claude/`) | One policy file that says which model tier and which effort level each kind of task gets, plus the scripts that check and apply it | `install.sh` — one command, with backups and an uninstall |
| **Harness modules** (`harness/`) | Optional skills, slash commands, subagents, hooks and scripts for Claude Code | By hand — you copy only the modules you want. Each module has a card in [`MODULES.md`](MODULES.md) |

The two parts are independent. You can install the routing framework and no
harness module, or harness modules and no routing framework. Some harness
modules read the routing policy; their cards say so.

## Before you start

- **Claude Code**, **OpenAI Codex**, **Google Antigravity**, or another agent that reads `CLAUDE.md` or `AGENTS.md`.
- **bash**, **git** and **Python 3**. The scripts use only the Python standard
  library. CI tests them on Python 3.12.
- **A backup of the parts of your agent home you will change.** For Claude Code,
  `~/.claude` also holds your session history, which can be large, so back up
  only the configuration folders:

  ```bash
  mkdir -p ~/claude-backup
  cp -Rp ~/.claude/{CLAUDE.md,settings.json,skills,agents,commands,hooks,scripts,rules} ~/claude-backup/ 2>/dev/null
  ```

---

## Part 1 — The routing framework

### Step 1: try it in a scratch folder

The installer never touches your real `~/.claude`, `~/.codex`, or
`~/.gemini/config` unless you ask for it twice. Run it into a new folder first.
With no `--target-home` (or `--claude-home`), it makes a new
`./gearbox-install-<timestamp>` folder:

```bash
git clone https://github.com/Autopsias/gearbox-routing.git
cd gearbox-routing
./install.sh --accept-example-profile
# Or for Codex / Antigravity:
./install.sh --platform codex --accept-example-profile
./install.sh --platform antigravity --accept-example-profile
```

The last lines of the output tell you if it worked:

```
install.sh summary
  platform:         claude (CLAUDE.md)
  target home:      /path/to/gearbox-routing/gearbox-install-20261030082043
  active_provider:  anthropic
  files copied:     30
  files backed up:  0
  guard:            PASS
```

`guard: PASS` means the final check (`verify-routing.sh --full`) found no
drift between the policy file and the files made from it.

| Flag | Why you need it |
|---|---|
| `--platform NAME` | Target agent platform: `claude` (default, writes `CLAUDE.md`, defaults `--provider` to `anthropic`), `codex` (writes `AGENTS.md`, defaults `--provider` to `openai`), or `antigravity` (writes `AGENTS.md`, defaults `--provider` to `gemini`). |
| `--target-home DIR` | The target folder (`--claude-home DIR` is kept as an alias). Without it, the installer makes a new `./gearbox-install-<timestamp>` folder. |
| `--accept-example-profile` | The shipped model ids, prices and effort maps are dated **examples**, not a researched policy. This flag says you know that. Use `--profile FILE` instead when you have your own policy file. |
| `--provider NAME` | Which shipped profile becomes active: `anthropic`, `openai`, `gemini` or `zai` (overrides the `--platform` default). See [`PROVIDERS.md`](PROVIDERS.md). |
| `--force` | Lets the installer replace a target policy file it would otherwise keep: one with a higher version, or the same version without `EXAMPLE` markers. The old file is still backed up. |
| `--i-understand-this-mutates-live-home` | Needed in addition to `--target-home` before the installer writes to a real live agent home (`~/.claude`, `~/.codex`, `~/.gemini`, or `~/.gemini/config`). Legacy alias: `--i-understand-this-mutates-live-claude`. |
| `--uninstall` | Restores the files the installer backed up and removes the routing block. See [Remove](#remove). |

Exit codes: `0` installed and the check passed · `1` installed, but the check
found drift · `2` refused before it wrote anything (bad flag, missing opt-in,
version guard, a missing source file, or `ROUTING` markers in the rules file
that the renderer refuses — it tries the render on a scratch copy first). A tool
failure after the first write also exits `2`; the ledger below then lists what
was already written, and `--uninstall` reverts it.

Every backup the installer makes and every file it creates is listed in
`<home>/claude/.gearbox-install-ledger`. `--uninstall` reads that list.

### Step 2: what lands where

```
<target>/claude/model-routing.yaml        the policy file (--provider sets active_provider:)
<target>/claude/model-routing.digest.md   the template for the routing block
<target>/claude/scripts/                  verify-routing.sh (drift check), resolve_route.py (resolver),
                                          render-routing-digest.py (writes the routing block)
<target>/skills/routing-update/           /routing-update: researches a model change and updates the policy
<target>/skills/routing-retro/            /routing-retro: reads past sessions and reports misroutes (read-only)
<target>/skills/repo-review/              /repo-review: whole-repo, agentic-readiness, evals & ways-of-working audit (read-only)
<target>/claude/evals/routing/            runbook and an empty misroute ledger (MISROUTES.md)
<target>/claude/fixtures/                 test fixtures for the check and the resolver
<target>/CLAUDE.md or <target>/AGENTS.md  gets a <!-- BEGIN ROUTING --> … <!-- END ROUTING --> block
```

The installer backs up every existing file that it changes to
`<file>.bak-<timestamp>`. A second run with the same flags changes nothing.

The three skills land in `<target>/skills/`:

- **Claude Code** (`~/.claude/skills/`): loaded automatically as personal skills ([Claude Code docs: skills](https://code.claude.com/docs/en/skills)).
- **Google Antigravity** (`~/.gemini/config/skills/`): loaded automatically in both Antigravity App and Antigravity CLI, alongside `~/.gemini/config/AGENTS.md`.
- **OpenAI Codex** (`~/.codex/skills/`): loaded by Codex for backward compatibility alongside `~/.codex/AGENTS.md` (symlink into `~/.agents/skills/` if you prefer Codex's shared user skills path).

After an install, start a new session and `/routing-retro`, `/routing-update`,
and `/repo-review` are available. Run `/routing-update` in your clone of this
repo: it edits the policy and the changelog there, and you then run `install.sh`
again. See the [routing skills](modules/routing-skills.md) card.

### Step 3: install into your live agent home

When the scratch install looks right, run the matching command for your
platform. Every live home needs both `--target-home` and the opt-in flag:

```bash
# Claude Code (~/.claude/CLAUDE.md + ~/.claude/skills/)
./install.sh --platform claude --target-home "$HOME/.claude" \
  --i-understand-this-mutates-live-home --accept-example-profile

# OpenAI Codex (~/.codex/AGENTS.md + ~/.codex/skills/)
./install.sh --platform codex --target-home "$HOME/.codex" \
  --i-understand-this-mutates-live-home --accept-example-profile

# Google Antigravity (~/.gemini/config/AGENTS.md + ~/.gemini/config/skills/)
./install.sh --platform antigravity --target-home "$HOME/.gemini/config" \
  --i-understand-this-mutates-live-home --accept-example-profile
```

Your existing `CLAUDE.md` or `AGENTS.md` keeps its content. The installer adds
the routing block, or replaces an existing one in place, and backs up the old
file.

### Check it works later

Run these from the install target (`~/.claude`, `~/.codex`, `~/.gemini/config`,
or your scratch folder):

```bash
CLAUDE_HOME="$PWD" SSOT="$PWD/claude/model-routing.yaml" bash claude/scripts/verify-routing.sh --full
python3 claude/scripts/resolve_route.py
```

- The first command ends with `PASS: verify-routing.sh clean`.
- The second command prints a demo: the baseline decision for one task class,
  two escalation steps and one degrade step, for each provider.

### Change the provider

**On the shipped example policy,** run `install.sh` again with the same flags
and the new `--provider`. It sets `active_provider:`, renders the routing
block (`CLAUDE.md` or `AGENTS.md`) and runs the check. It also replaces the
installed policy file with this repo's copy (the old one is backed up), so use
the next steps instead if you edited the installed policy.

**On a policy you edited,** run these from the install target:

1. Set `active_provider:` in `claude/model-routing.yaml`. This is the only line
   a provider change edits.
2. Render the routing block from the edited policy (omitting `--target`
   auto-detects `AGENTS.md` or `CLAUDE.md`):

   ```bash
   python3 claude/scripts/render-routing-digest.py --repo-dir "$PWD" \
     --yaml "$PWD/claude/model-routing.yaml" \
     --source "$PWD/claude/model-routing.digest.md"
   ```

3. Run `verify-routing.sh --full` (above). It fails if the new provider cannot
   serve every task class. Run it after step 2: before the render, the
   routing block still shows the old provider and the check fails.

Details: [`PROVIDERS.md`](PROVIDERS.md).

### Update to a newer Gearbox

1. Run `git pull` in your clone.
2. Run `install.sh` again with the same flags.

The installer keeps your installed policy file (exit `2`, nothing written)
when its version is higher than the shipped one, or the same version without
`EXAMPLE` markers. An older installed file is replaced, even one you edited;
it is backed up first. Add `--force` only if you want the shipped file in the
first case.

### Remove

```bash
# Claude Code (pass --target-home "$HOME/.codex" or "$HOME/.gemini/config" for Codex / Antigravity)
./install.sh --target-home "$HOME/.claude" --i-understand-this-mutates-live-home --uninstall
```

- It restores each file the installer backed up to its state before your
  first install, from the list in `claude/.gearbox-install-ledger`. It does not
  touch `.bak-*` files that other tools made. The `.bak-*` files stay in place.
- It removes the routing block from `CLAUDE.md` or `AGENTS.md` when the
  installer created that file (auto-detected from the ledger); a rules file you
  had before is restored from its backup instead.
- It does **not** delete files that the installer added new. The ledger lists
  them as `created`; delete those files yourself if you want them gone. Do not
  delete whole folders: a folder can also hold files that uninstall just
  restored.
- An install made before the ledger existed has no list. Uninstall then
  restores only the installer's own backups (`<file>.bak-<14-digit timestamp>`)
  of the files it manages, the oldest one per file. The first update of such an
  install records those backups in a new ledger first, so they stay the restore
  target. One limit remains for these older installs: if the installer created
  `CLAUDE.md` and a later update backed it up, that backup already holds the
  routing block. Remove the block by hand if it is still there after uninstall.

---

## Part 2 — Harness modules

`harness/` is a copy of a working Claude Code setup. You do not install it as
a whole. You pick modules from [`MODULES.md`](MODULES.md) (or run
`./install-module --list`), and `./install-module` installs them from their
cards.

### Five rules

1. **Install only what you will use.** Claude Code adds the name and
   description of every installed skill, slash command and subagent to its
   context on every turn, also in sessions that do not use them
   ([Claude Code docs: plugins](https://code.claude.com/docs/en/plugins)).
   The harness has 140 of them.
2. **Install into `~/.claude`.** Many harness files name `~/.claude/...` paths
   directly. A module copied anywhere else does not find its parts.
3. **Never copy `harness/settings.json` or `harness/CLAUDE.md` over your own.**
   They would replace your settings and your global instructions. Merge only
   the hook entries that a module card gives you.
4. **Copy every path on the card.** A module can need helper files in other
   folders (`references/`, `scripts/`, other skills). The card lists them all.
5. **Read the card's "Cautions" first.** Some modules block commands, kill
   processes or delete files. The card says which.

Some skills and commands tell you to edit `~/your-private-harness` and run
`gearbox deploy`. That is the original author's deploy flow: a private source
repo that deploys to `~/.claude`. Without it, edit the files in `~/.claude`
directly and skip those steps.

### The steps for one module

1. Find the module: `./install-module --list`, or the cards in [`MODULES.md`](MODULES.md).
2. Read its card. Install what it lists under **Needs** first, and read its **Cautions**.
3. Preview, then install:

   ```bash
   ./install-module --dry-run <name>
   ./install-module <name>
   ```

   It copies the files the card lists into `~/.claude` and adds the card's
   hook entries to `~/.claude/settings.json`. It backs up `settings.json` before
   each change, never adds an entry twice, and keeps your own entries.
   It runs only `mkdir`, `cp`, `chmod`, `cd`, and `rm` inside `~/.claude`.
   It prints any other step on the card for you to run yourself.
   Parts that a card marks optional are skipped; add `--with-optional` to
   include them.
4. Start a new Claude Code session.
5. Do the card's **Check it works** step.

`--claude-home DIR` installs into another folder, for example to try a module
first. Each card also shows its steps as plain shell commands, if you prefer
to copy the files by hand.

To check your setup after a merge, run `/doctor` in Claude Code. It reports a
settings file that it cannot parse, slow hooks, and unused skills compared
with their context cost. It asks before it changes anything
([Claude Code docs: skills](https://code.claude.com/docs/en/skills)).

### Remove a module

1. Run `./install-module --remove <name>`. It takes the module's entries out of
   `~/.claude/settings.json` (after a backup) and prints the card's **Remove**
   steps.
2. Read those steps, then run them. The tool does not delete files itself:
   some files are shared with other modules, and some steps delete your own
   data, such as run history.
