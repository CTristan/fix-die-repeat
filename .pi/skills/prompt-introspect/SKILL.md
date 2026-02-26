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
8. Summarize what was changed and why

## Guidelines

- **Be conservative**: Only add template changes when there's a clear pattern (2+ entries in the same category, or 1 entry with high relevance)
- **Be language-agnostic**: All prompt templates must remain language-agnostic. Do not add language-specific checks.
- **Preserve structure**: Follow the existing template style and formatting conventions
- **Don't duplicate**: If a checklist item already covers the gap, strengthen the wording rather than adding a new item
- **Explain changes**: Each template edit should have a clear rationale tied to specific introspection entries

## Invocation

```
/skill:prompt-introspect
```

## Reference

See `references/categories.md` for the standard category definitions and what good template coverage looks like for each.
