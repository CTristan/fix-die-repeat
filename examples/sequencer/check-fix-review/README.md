# Check, fix, and review sequencer example

This example keeps the boundary visible: the sequencer decides what comes next, while your
external agent reads each instruction, changes `target/app.txt`, and writes declared JSON
artifacts.

Copy the contents of `target/` into a temporary Git repository so `app.txt` sits at the repository
root, then commit the baseline before you run the workflow:

```bash
mkdir -p /path/to/temporary-repository
cp -R target/. /path/to/temporary-repository/
git -C /path/to/temporary-repository init
git -C /path/to/temporary-repository symbolic-ref HEAD refs/heads/main
git -C /path/to/temporary-repository config user.email "example@example.invalid"
git -C /path/to/temporary-repository config user.name "Example"
git -C /path/to/temporary-repository add app.txt
git -C /path/to/temporary-repository commit -m "Added example baseline"
```

The `fix` postcondition compares repository snapshots, including tracked diffs, untracked paths,
and untracked contents. The committed baseline gives this example a clean starting point; the
validator does not require `app.txt` itself to be tracked. Then initialize a run:

```bash
fix-die-repeat sequencer \
  --run-id example \
  --repo /path/to/temporary-repository \
  init \
  --workflow /path/to/workflow.yaml
```

Each non-terminal response that contains `step` includes `step.id`, `step.instruction`, and
`step.artifact_root`. Run this procedure from the example directory, and inspect each `done`
response before you execute the next step. Stop when the response contains `terminal` instead of
executing another step:

1. Copy the `check` response's `step.artifact_root`, run the check, and complete `check`.

   ```bash
   CHECK_ARTIFACT_ROOT="/absolute/path/from-the-check-response"
   python agent/check.py /path/to/temporary-repository/app.txt "$CHECK_ARTIFACT_ROOT"
   fix-die-repeat sequencer --run-id example --repo /path/to/temporary-repository done check
   ```

2. If the response selects `step.id` `fix`, run the fix, and complete `fix`.

   ```bash
   python agent/fix.py /path/to/temporary-repository/app.txt
   fix-die-repeat sequencer --run-id example --repo /path/to/temporary-repository done fix
   ```

3. The `fix` route returns to `check`, so run the newly returned check instruction and complete
   `check` again.

   ```bash
   CHECK_ARTIFACT_ROOT="/absolute/path/from-the-new-check-response"
   python agent/check.py /path/to/temporary-repository/app.txt "$CHECK_ARTIFACT_ROOT"
   fix-die-repeat sequencer --run-id example --repo /path/to/temporary-repository done check
   ```

4. If the response selects `step.id` `review`, copy its artifact root, run the review, and
   complete `review`.

   ```bash
   REVIEW_ARTIFACT_ROOT="/absolute/path/from-the-review-response"
   python agent/review.py /path/to/temporary-repository/app.txt "$REVIEW_ARTIFACT_ROOT"
   fix-die-repeat sequencer --run-id example --repo /path/to/temporary-repository done review
   ```

Call `next` when you need the current instruction again. A mutating `fix` step returns recovery
exit `5` if you request it twice. Reconcile the repository, then acknowledge that retry with
`done fix --recover`.

The example test at `tests/test_sequencer_example.py` runs the whole protocol, including a forced
transition, recovery acknowledgement, repeat routing, and terminal completion.
