# Introspection Categories

Standard categories used in the introspection file and what good template coverage means for each.

## security
**What it covers:** Injection flaws, auth/permission bypasses, secret exposure, unsafe deserialization, insecure defaults.
**Current template coverage:** `local_review.j2` has a security checklist item.
**Good coverage looks like:** The review template explicitly lists common vulnerability patterns. The fix template includes guidance on secure alternatives.

## error-handling
**What it covers:** Missing null/nil checks, unhandled exceptions, resource leaks, missing input validation.
**Current template coverage:** `local_review.j2` mentions "unsafe error handling" in the correctness item.
**Good coverage looks like:** Explicit checklist items for null guards on external inputs, resource cleanup patterns, and error propagation.

## performance
**What it covers:** N+1 queries, unbounded growth, heavy work in hot paths, missing pagination.
**Current template coverage:** `local_review.j2` has a performance checklist item.
**Good coverage looks like:** Specific mention of common anti-patterns (unbounded queries, missing indexes, synchronous blocking in async contexts).

## correctness
**What it covers:** Logic bugs, off-by-one errors, race conditions, wrong return values, type mismatches.
**Current template coverage:** `local_review.j2` has a correctness/reliability item.
**Good coverage looks like:** Mention of boundary condition checking, concurrency hazards, and type safety.

## code-quality
**What it covers:** Suppressed warnings, dead code, naming issues, duplication, overly complex logic.
**Current template coverage:** `local_review.j2` mentions "unjustified suppression of errors or warnings".
**Good coverage looks like:** Guidance on when warning suppression is acceptable vs. when it indicates a real issue.

## testing
**What it covers:** Missing test coverage, fragile tests, test configuration changes.
**Current template coverage:** `local_review.j2` mentions "no test configuration changes without explicit approval".
**Good coverage looks like:** Guidance on what types of changes require new tests and what test quality looks like.

## documentation
**What it covers:** Inaccurate docs, missing docs, stale comments, misleading error messages.
**Current template coverage:** `local_review.j2` mentions "docs/prompts/config instructions match actual behavior".
**Good coverage looks like:** Explicit check that any behavioral change has corresponding doc/comment updates.

## configuration
**What it covers:** Mismatched config values, insecure defaults, missing env var documentation.
**Current template coverage:** Partially covered by the accuracy checklist item.
**Good coverage looks like:** Explicit check for default value safety and config documentation completeness.
