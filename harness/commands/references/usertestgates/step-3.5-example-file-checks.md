# STEP 3.5 example file-check table (project-specific, stale)

This is a worked EXAMPLE from one historical project (an example internal pipeline, gate
IDs TG-1.1 through TG-4.6) — it is stale/superseded example content, not a live
requirement of the current project. Read it only if you need a concrete pattern to
model your own project's per-gate implementation-file checks after; otherwise skip it
and go straight to the generic instruction in the main command file.

**Generic instruction (what the main file actually needs you to do):** before suggesting
a test gate, define, for each gate ID in your project's `docs/epics.md` config, the set
of implementation files that must exist for that gate's story to be considered
implemented. Customize the `case` statement below (or an equivalent lookup) with your
own project's actual gate IDs and file paths — the example below is illustrative only.

```bash
gate_id="TG-X.Y"  # e.g., "TG-2.3"

# Define expected files for each gate (examples — REPLACE with your project's gates)
case "$gate_id" in
  "TG-1.1")
    # Agent Framework - check for strands setup
    files=("requirements.txt")
    ;;
  "TG-1.2")
    # Word Parser - check for parser implementation
    files=("src/agents/input_parser/word_parser.py" "src/parsers/word_parser.py")
    ;;
  "TG-1.3")
    # Excel Parser - check for parser implementation
    files=("src/agents/input_parser/excel_parser.py" "src/parsers/excel_parser.py")
    ;;
  "TG-2.3")
    # Core Templates - check for 5 key template files
    files=(
      "src/templates/example-project/title_slide.html.j2"
      "src/templates/example-project/big_number.html.j2"
      "src/templates/example-project/three_metrics.html.j2"
      "src/templates/example-project/bullet_list.html.j2"
      "src/templates/example-project/chart_template.html.j2"
    )
    ;;
  "TG-3.3")
    # PptxGenJS POC - check for Node.js conversion script
    files=("src/converters/conversion_scripts/convert_to_pptx.js")
    ;;
  "TG-3.4")
    # Full Pipeline - check for complete conversion implementation
    files=("src/converters/nodejs_bridge.py" "src/converters/conversion_scripts/convert_to_pptx.js")
    ;;
  "TG-4.2")
    # Checkpoint Flow - check for orchestration with checkpoints
    files=("src/orchestration/checkpoints.py")
    ;;
  "TG-4.6")
    # E2E MVP - check for main orchestrator
    files=("src/main.py" "src/orchestration/orchestrator.py")
    ;;
  *)
    # Unknown gate - skip file checks
    files=()
    ;;
esac

# Check if files exist
missing_files=()
for file in "${files[@]}"; do
  if [ ! -f "$file" ]; then
    missing_files+=("$file")
  fi
done

# Output result
if [ ${#missing_files[@]} -gt 0 ]; then
  echo "STORY_NOT_READY"
  printf '%s\n' "${missing_files[@]}"
else
  echo "STORY_READY"
fi
```

**Store the story readiness status** to use in Step 4.
