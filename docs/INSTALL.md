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

- **Claude Code**, or another agent that reads a `CLAUDE.md` file.
- **bash**, **git** and **Python 3**. The scripts use only the Python standard
  library. CI tests them on Python 3.12.
- **A backup of the parts of `~/.claude` you will change.** `~/.claude` also
  holds your session history, which can be large, so back up only the
  configuration folders:

  ```bash
  mkdir -p ~/claude-backup
  cp -Rp ~/.claude/{CLAUDE.md,settings.json,skills,agents,commands,hooks,scripts,rules} ~/claude-backup/ 2>/dev/null
  ```

---

## Part 1 — The routing framework

### Step 1: try it in a scratch folder

The installer never touches your real `~/.claude` unless you ask for it twice.
Run it into a new folder first:

```bash
git clone https://github.com/Autopsias/gearbox-routing.git
cd gearbox-routing
./install.sh --claude-home /tmp/gearbox-try --accept-example-profile --provider anthropic
```

The last lines of the output tell you if it worked:

```
install.sh summary
  target home:     /tmp/gearbox-try
  active_provider:  anthropic
  files copied:     30
  files backed up:  0
  guard:            PASS
```

`guard: PASS` means the final check (`verify-routing.sh --full`) found no
drift between the policy file and the files made from it.

| Flag | Why you need it |
|---|---|
| `--claude-home DIR` | The target folder. Without it, the installer makes a new `./gearbox-install-<timestamp>` folder. |
| `--accept-example-profile` | The shipped model ids, prices and effort maps are dated **examples**, not a researched policy. This flag says you know that. Use `--profile FILE` instead when you have your own policy file. |
| `--provider NAME` | Which shipped profile becomes active: `anthropic` (default), `openai`, `gemini` or `zai`. See [`PROVIDERS.md`](PROVIDERS.md). |
| `--force` | Lets the installer replace a target policy file that has the same or a higher version, or that has no `EXAMPLE` markers. The old file is still backed up. |
| `--i-understand-this-mutates-live-claude` | Needed in addition to `--claude-home "$HOME/.claude"` before the installer writes to your real Claude home. |
| `--uninstall` | Restores the backups and removes the routing block. See [Remove](#remove). |

Exit codes: `0` installed and the check passed · `1` installed, but the check
found drift · `2` refused before it wrote anything (bad flag, missing opt-in,
version guard).

### Step 2: what lands where

```
<target>/claude/model-routing.yaml        the policy file (--provider sets active_provider:)
<target>/claude/model-routing.digest.md   the template for the CLAUDE.md routing block
<target>/claude/scripts/                  verify-routing.sh (drift check), resolve_route.py (resolver),
                                          render-routing-digest.py (writes the CLAUDE.md block)
<target>/skills/routing-update/           /routing-update: researches a model change and updates the policy
<target>/skills/routing-retro/            /routing-retro: reads past sessions and reports misroutes (read-only)
<target>/claude/evals/routing/            runbook and an empty misroute ledger (MISROUTES.md)
<target>/claude/fixtures/                 test fixtures for the check and the resolver
<target>/CLAUDE.md                        gets a <!-- BEGIN ROUTING --> … <!-- END ROUTING --> block
```

The installer backs up every existing file that it changes to
`<file>.bak-<timestamp>`. A second run with the same flags changes nothing.

The two skills land in `<target>/skills/`, which is where Claude Code loads
personal skills when the target is `~/.claude`
([Claude Code docs: skills](https://code.claude.com/docs/en/skills)). After an
install into `~/.claude`, start a new session and `/routing-retro` and
`/routing-update` are available. Run `/routing-update` in your clone of this
repo: it edits the policy and the changelog there, and you then run `install.sh`
again. See the [routing skills](modules/routing-skills.md) card.

### Step 3: install into your Claude home

When the scratch install looks right, run the same command against your real
home. It needs both opt-in flags:

```bash
./install.sh --claude-home "$HOME/.claude" --i-understand-this-mutates-live-claude \
  --accept-example-profile --provider anthropic
```

Your existing `~/.claude/CLAUDE.md` keeps its content. The installer adds the
routing block, or replaces an existing one in place, and backs up the old file.

### Check it works later

Run these from the install target (`~/.claude` or your scratch folder):

```bash
CLAUDE_HOME="$PWD" SSOT="$PWD/claude/model-routing.yaml" bash claude/scripts/verify-routing.sh --full
python3 claude/scripts/resolve_route.py
```

- The first command ends with `PASS: verify-routing.sh clean`.
- The second command prints a demo: the baseline decision for one task class,
  two escalation steps and one degrade step, for each provider.

### Change the provider

1. Set `active_provider:` in `claude/model-routing.yaml`. This is the only line
   a provider change edits.
2. Run `verify-routing.sh --full` (above). It fails if the new provider cannot
   serve every task class.
3. Run `install.sh` again with the new `--provider`. This updates the
   `CLAUDE.md` block.

Details: [`PROVIDERS.md`](PROVIDERS.md).

### Update to a newer Gearbox

1. Run `git pull` in your clone.
2. Run `install.sh` again with the same flags.

If your installed policy file has the same or a higher version, or has no
`EXAMPLE` markers, the installer refuses (exit `2`) so that it does not
overwrite your own policy. Add `--force` only if you want the shipped file.

### Remove

```bash
./install.sh --claude-home "$HOME/.claude" --i-understand-this-mutates-live-claude --uninstall
```

- It copies every `.bak-*` file back, newest first. When one file has several
  backups, the oldest one is copied last, so the file returns to its state
  before your first install. The `.bak-*` files stay in place.
- It removes the routing block from `CLAUDE.md`, but only if `CLAUDE.md` had no
  backup (that is, the installer created it).
- It does **not** delete files that the installer added new. Delete
  `~/.claude/claude/`, `~/.claude/skills/routing-update/` and
  `~/.claude/skills/routing-retro/` yourself if you want them gone.

---

## Part 2 — Harness modules

`harness/` is a copy of a working Claude Code setup. You do not install it as
a whole. You pick modules from [`MODULES.md`](MODULES.md) and copy their files.

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

1. Open the module's card in [`MODULES.md`](MODULES.md).
2. Install what the card lists under **Needs** first.
3. Run the card's **Install** commands from the repo root.
4. If the card has a `settings.json` fragment, merge it into the `"hooks"`
   object of `~/.claude/settings.json`. Keep your existing entries.
5. Start a new Claude Code session.
6. Do the card's **Check it works** step.

To check your setup after a merge, run `/doctor` in Claude Code. It reports a
settings file that it cannot parse, slow hooks, and unused skills compared
with their context cost. It asks before it changes anything
([Claude Code docs: skills](https://code.claude.com/docs/en/skills)).

### Remove a module

Do the card's **Remove** steps: delete the files it lists, and delete its
entries from `~/.claude/settings.json`. Every hook entry on the cards starts
with a check that its file exists, so a hook file that you delete first does no
harm; still delete its entry to keep the settings file clean.
