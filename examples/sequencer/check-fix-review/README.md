# Check, fix, and review sequencer example

This example keeps the boundary visible: the sequencer decides what comes next, while your
external agent reads each instruction, changes `target/app.txt`, and writes declared JSON
artifacts.

Copy `target/` into a temporary Git repository, then commit the baseline before you run the
workflow:

```bash
git -C /path/to/temporary-repository init -b main
git -C /path/to/temporary-repository add app.txt
git -C /path/to/temporary-repository commit -m "Added example baseline"
```

The `fix` postcondition compares repository snapshots, so `app.txt` must start as a tracked file.
Then initialize a run:

```bash
fix-die-repeat sequencer \
  --run-id example \
  --repo /path/to/temporary-repository \
  init \
  --workflow /path/to/workflow.yaml
```

Each response contains `step.instruction` and `step.artifact_root`. Use the matching script to
simulate the external work:

```bash
python agent/check.py /path/to/temporary-repository/app.txt ARTIFACT_ROOT
python agent/fix.py /path/to/temporary-repository/app.txt
python agent/review.py /path/to/temporary-repository/app.txt ARTIFACT_ROOT
```

Call `done STEP_ID` after the work, and call `next` when you need the current instruction again.
A mutating `fix` step returns recovery exit `5` if you request it twice. Reconcile the repository,
then acknowledge that retry with `done fix --recover`.

The example test at `tests/test_sequencer_example.py` runs the whole protocol, including a forced
transition, recovery acknowledgement, repeat routing, and terminal completion.
