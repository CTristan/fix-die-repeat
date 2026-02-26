---
name: prompt-introspect
description: Analyze accumulated PR review introspection data and update FDR prompt templates to close identified gaps. Use when you want to improve fix-die-repeat's prompts based on real-world PR review patterns.
disable-model-invocation: true
---

# Prompt Introspect

Reads the global introspection file and updates FDR's prompt templates to address recurring gaps.

## Workflow

1. Read `~/.config/fix-die-repeat/introspection.yaml`
2. Filter to entries with `status: pending`
3. Read the current prompt templates:
   - `fix_die_repeat/templates/fix_checks.j2`
   - `fix_die_repeat/templates/local_review.j2`
   - `fix_die_repeat/templates/resolve_review_issues.j2`
   - `fix_die_repeat/templates/pr_threads_header.j2`
4. For each pending entry, analyze the threads:
   - **Pattern detection**: Are multiple entries flagging the same category across different projects?
   - **Gap analysis**: Is this category already covered by the templates? If so, is the coverage sufficient?
   - **Actionability**: Can a template change realistically catch this class of issue earlier?
5. Create a new git branch: `introspect/prompt-updates-YYYY-MM-DD`
6. Edit templates to address identified gaps:
   - Add checklist items to `local_review.j2` for categories not currently covered
   - Add guidance to `fix_checks.j2` for fix patterns that were commonly needed
   - Add context to `resolve_review_issues.j2` for issue types the agent struggled with
7. Mark processed entries as `status: reviewed` in the introspection file
8. **Compact the introspection file** if it has reached 2000+ lines:
   - Archive reviewed entries older than 6 months to `~/.config/fix-die-repeat/introspection-archive.yaml`
   - Keep the most recent 50 reviewed entries in the main file
   - Keep all `pending` entries in the main file
   - Delete the archive file if it would be empty after compaction
9. Summarize what was changed and why

## Guidelines

- **Be conservative**: Only add template changes when there's a clear pattern (2+ entries in the same category, or 1 entry with high relevance)
- **Be language-agnostic**: All prompt templates must remain language-agnostic. Do not add language-specific checks.
- **Preserve structure**: Follow the existing template style and formatting conventions
- **Don't duplicate**: If a checklist item already covers the gap, strengthen the wording rather than adding a new item
- **Explain changes**: Each template edit should have a clear rationale tied to specific introspection entries

## Introspection File Compaction

**When**: The introspection file has reached 2000+ lines (per project file size policy).

**Strategy**:
- Keep all `pending` entries in the main file (these still need processing)
- Keep the 50 most recent `reviewed` entries in the main file (for trend analysis)
- Archive `reviewed` entries older than 6 months to `introspection-archive.yaml`
- Delete the archive file if it would be empty after compaction

**Implementation**:
1. Count lines in `~/.config/fix-die-repeat/introspection.yaml`
2. If < 2000 lines, skip compaction
3. If ≥ 2000 lines:
   - Parse all YAML documents (separated by `---`)
   - Identify `pending` entries → keep in main file
   - Identify `reviewed` entries:
     - Sort by date (most recent first)
     - Keep top 50 in main file
     - Archive entries older than 6 months (based on `date` field)
   - Write compacted entries to main file
   - Write archived entries to `introspection-archive.yaml` (prepend with `---` separator)
   - If archive is empty, delete `introspection-archive.yaml`

**Note**: The file size policy (AGENTS.md) requires all code and documentation files be kept under 2000 lines. The introspection file follows this policy to remain maintainable.

## Invocation

```
/skill:prompt-introspect
```

## Reference

See `references/categories.md` for the standard category definitions and what good template coverage looks like for each.
