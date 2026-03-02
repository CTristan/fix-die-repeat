---
name: prompt-introspect
description: Analyze accumulated PR review introspection data and update FDR prompt templates to close identified gaps. Use when you want to improve fix-die-repeat's prompts based on real-world PR review patterns.
disable-model-invocation: true
---

# Prompt Introspect

Reads the global introspection file and updates FDR's prompt templates to address recurring gaps.

## Workflow

1. Read `~/.config/fix-die-repeat/introspection.yaml` (inbox)
2. Filter to entries with `status: pending`
3. Read `~/.config/fix-die-repeat/introspection-summary.md` (cumulative context)
4. Read the current prompt templates:
   - `fix_die_repeat/templates/fix_checks.j2`
   - `fix_die_repeat/templates/local_review.j2`
   - `fix_die_repeat/templates/resolve_review_issues.j2`
   - `fix_die_repeat/templates/introspect_pr_review.j2`
   - `fix_die_repeat/templates/lang_checks/*.j2`
5. Analyze pending entries using the summary for trend context:
   - **Pattern detection**: Are multiple entries flagging the same category across different projects?
   - **Gap analysis**: Is this category already covered by the templates? If so, is the coverage sufficient?
   - **Actionability**: Can a template change realistically catch this class of issue earlier?
6. Edit templates to address identified gaps:
   - Add checklist items to `local_review.j2` for categories not currently covered
   - Add guidance to `fix_checks.j2` for fix patterns that were commonly needed
   - Add context to `resolve_review_issues.j2` for issue types the agent struggled with
   - Update `lang_checks/*.j2` for language-specific footguns discovered during PR review
7. **Perform Template Health Check**:
   - **Size budget**: Ensure each edited template stays within its line/byte budget (see `references/template-budgets.md`)
   - **Redundancy check**: Merge overlapping checklist items within the same template
   - **Effectiveness audit**: Consolidate checklist items that haven't been triggered by real-world data (see `references/template-budgets.md`)
8. Mark processed entries as `status: reviewed` in the introspection file
9. **Archive + Summarize**:
   - Generate/regenerate `~/.config/fix-die-repeat/introspection-summary.md` incorporating new data + existing summary (see `references/summary-format.md`)
   - Move all `reviewed` entries from the inbox (`introspection.yaml`) to the current archive file
   - Clear `introspection.yaml` (or leave only remaining `pending` entries)
   - **Rotate Archive**: If the current archive file exceeds 2,000 lines, use the `fix-die-repeat introspection rotate <file_path>` command to safely rotate it to a dated filename (`introspection-archive-YYYY-MM.yaml`) with proper file locking and safe YAML serialization.
10. Summarize what was changed and why

## Guidelines

- **Be conservative**: Only add template changes when there's a clear pattern (2+ entries in the same category, or 1 entry with high relevance)
- **Be language-agnostic**: Main prompt templates (`local_review.j2`, `fix_checks.j2`, `resolve_review_issues.j2`) must remain language-agnostic. Use `lang_checks/*.j2` for language-specific patterns.
- **Preserve structure**: Follow the existing template style and formatting conventions
- **Don't duplicate**: If a checklist item already covers the gap, strengthen the wording rather than adding a new item
- **Explain changes**: Each template edit should have a clear rationale tied to specific introspection entries

## File Layout

- `introspection.yaml` — pending entries only (inbox)
- `introspection-summary.md` — cumulative Markdown summary (LLM context)
- `introspection-archive.yaml` — current raw data archive
- `introspection-archive-YYYY-MM.yaml` — rotated historical archives (not read by skill)

## Introspection File Archive & Rotation

**When**: The skill finishes processing pending entries.

**Strategy**:
- **Move to Archive**: All entries marked `status: reviewed` are moved from `introspection.yaml` to `introspection-archive.yaml` using the `fix-die-repeat introspection append` command to ensure safe concurrent access and proper YAML document separation.
- **Rotate**: If `introspection-archive.yaml` reaches 2,000+ lines (per project file size policy):
  - Use `fix-die-repeat introspection rotate ~/.config/fix-die-repeat/introspection-archive.yaml` to safely rotate the file with locking and safe YAML serialization.
- **Summarize**: Regenerate `~/.config/fix-die-repeat/introspection-summary.md` by reading it and writing back with the updated summary, ensuring locking by using `fix-die-repeat introspection append` with `--content` replacing the file content if needed, or by ensuring the skill itself performs safe operations.

**Note**: The file size policy (AGENTS.md) requires all code and documentation files be kept under 2000 lines. Archives are rotated to remain maintainable for manual audit.

## First-Run Migration

On the first run under the new model (Archive + Summarize), the skill performs a one-time migration:
1. Read all `reviewed` entries from `introspection.yaml` and `introspection-archive.yaml`.
2. Generate the initial `introspection-summary.md` from all historical entries.
3. Consolidate all historical `reviewed` entries into `introspection-archive.yaml`.
4. Clear `reviewed` entries from `introspection.yaml`.
5. Perform initial archive rotation if necessary.

## Invocation

```
/skill:prompt-introspect
```

## Reference

See `references/categories.md` for the standard category definitions and what good template coverage looks like for each.
