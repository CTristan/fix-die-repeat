# Template Size Budgets

FDR prompt templates must remain under specific size budgets to ensure high-quality LLM instruction-following. These budgets are based on Anthropic guidance on instruction length and "Lost in the Middle" attention degradation research.

If a template exceeds **either** its line or byte budget after edits, the skill must consolidate existing items before finishing.

## Budget Table

| Template | Current Size | Budget (Lines) | Budget (Bytes) | Token Estimate |
|---|---|---|---|---|
| `local_review.j2` | 88 lines / 8.3KB | 100 lines | 8KB | ~2,000 tokens |
| `fix_checks.j2` | 34 lines / 3.3KB | 50 lines | 5KB | ~1,200 tokens |
| `resolve_review_issues.j2` | 24 lines / 1.4KB | 40 lines | 3KB | ~750 tokens |
| `introspect_pr_review.j2` | 43 lines / 2.1KB | 60 lines | 4KB | ~1,000 tokens |
| Lang check partials (each) | 7 lines / ~450B | 15 lines | 1KB | ~250 tokens |

## Enforcement Rules

1. **Measure First**: After updating a template, measure its line count and byte size.
2. **Consolidate if Over**: If any budget is exceeded, the skill must perform consolidation:
   - Merge overlapping checklist items.
   - Tighten wording to reduce byte count.
   - Group related sub-bullets into a single point.
   - Do not simply delete items to meet the budget.
3. **Redundancy Detection**: Compare newly added items against existing items in the same template. Merge any that cover the same concern.
4. **Effectiveness Audit**: Cross-reference checklist items against the `introspection-summary.md`.
   - Items that have never been triggered across all historical data are candidates for removal or consolidation.
   - **Exception**: Security and correctness items are exempt from removal even if never triggered, as they serve a preventive role.
   - Code-quality, documentation, and configuration items require 20+ PRs analyzed before they're flagged for removal.

## Rationale

- **Instruction Focus**: As prompt length increases, LLMs can "forget" instructions in the middle of the text.
- **Latency**: Smaller prompts result in faster inference.
- **Cost**: Fewer tokens reduce operational costs for long-running agent loops.
- **Maintainability**: Smaller templates are easier for humans and agents to reason about.
