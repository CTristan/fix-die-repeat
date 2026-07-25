# ADR 0001: Sequencer contract

Status: Accepted

Date: 2026-07-25

Issue: [#75](https://github.com/CTristan/fix-die-repeat/issues/75)

## Context

The sequencer lets an external agent perform a declared workflow while
`fix-die-repeat` owns the cursor, validation, routing, recovery state, and protocol. It never
runs the work itself, because it cannot safely assume how a consumer starts or controls its
agent.

The workflow and protocol become public contracts as soon as another repository depends on
them. This note settles those contracts before we write the engine, so implementation changes
cannot quietly choose behavior that consumers already depend on.

## Decision drivers

- Existing `fix-die-repeat [options]` invocations must keep their current behavior.
- The sequencer must never perform the external agent's work or modify its repository.
- A caller must recover deterministically after losing process or conversation state.
- Configuration must remain declarative, because a workflow cannot become another code-execution
  surface.
- Every transition must survive process death and concurrent callers without splitting the
  cursor.
- Git results must come from live repository state, but probes cannot refresh or rewrite that
  state.
- The first protocol must stay small enough to test as a complete contract.

## Options considered

### Work ownership

- **External-agent sequencer (selected):** The sequencer returns instructions, validates results,
  and owns routing. The external agent owns execution and presentation.
- **Internal worker runner:** `fix-die-repeat` starts and supervises every agent. This repeats the
  existing loop and cannot support agents that a consumer already controls.
- **Callback or plugin hooks:** Consumer code runs inside the sequencer process. This makes
  configuration executable and mixes repository ownership with engine ownership.

### CLI namespace

- **Root Click group with a `sequencer` subgroup (selected):** Existing options stay on the root
  callback, which runs when no subcommand appears.
- **New root flags:** Flags such as `--sequencer-next` avoid a Click migration, but they flatten
  four commands and their different arguments into one error-prone option list.
- **Separate executable:** A `fix-die-repeat-sequencer` entry point isolates parsing, but it
  creates a second public binary, help surface, and installation contract.
- **Replace the root command with subcommands:** `fix-die-repeat run` would produce a cleaner
  hierarchy, but it would break every existing invocation.

### Run and workflow selection

- **Repository plus run ID, with `--workflow` required by `init` (selected):** A run ID is unique
  inside one repository. State stores the workflow ID, normalized workflow, source path, and
  fingerprint, so later commands can locate recovery state without reparsing a missing file.
- **Workflow path on every command:** Every call proves which configuration it means, but
  `status` becomes unusable when that file moves, disappears, or stops parsing.
- **Repository, workflow ID, and run ID on every command:** This locates state without a file, but
  it makes callers repeat an ID already stored in the run and creates two workflow selectors.
- **One implicit active run per repository:** This removes `--run-id`, but concurrent workflows
  collide and stale state can capture an unrelated caller.

### Output selection

- **Unconditional JSON in protocol version 1 (selected):** Every completed command returns one
  JSON object, so callers do not negotiate a format that has only one valid choice.
- **`--format json` with one accepted value:** This advertises an extension point, but it adds
  parsing and documentation without adding behavior.
- **JSON Lines stream:** Streaming can expose probe progress, but partial output complicates
  retries and breaks the one-result transaction model.
- **Human text by default with optional JSON:** This helps terminal use, but third-party agents
  can accidentally depend on prose and invoke the wrong mode.

### Exit-code strategy

- **Prototype outcomes plus `sysexits` values (selected):** `2`, `3`, and `4` preserve the proven
  environment, terminal, and blocked results. `64` and `70` separate usage and internal failures.
- **Exit `0` for every JSON response:** The response carries enough information, but shell callers
  would treat blocked and failed operations as success.
- **Click's default exit codes:** This keeps less custom code, but usage errors collide with the
  prototype's environment error.
- **One unique code for every terminal reason:** Shell callers gain detail, but the code space
  becomes workflow-dependent and cannot remain stable.

### Workflow serialization

- **Strict safe YAML (selected):** Consumers can read nested routes easily, and the project already
  ships PyYAML. A strict loader rejects duplicate keys, aliases, custom tags, and non-JSON values.
- **JSON:** The parser has fewer ambiguous types, but hand-authored workflows become noisy.
- **TOML:** TOML works well for flat configuration, but ordered nested predicates become difficult
  to read.
- **Python objects or modules:** Python gives maximum flexibility, but loading a workflow executes
  consumer code.
- **A custom grammar:** A custom format can match the domain exactly, but it adds a parser and
  escaping rules before the engine proves its value.

### Workflow model

- **Ordered steps and routes (selected):** Each step declares one instruction, mutability,
  postconditions, and ordered destinations.
- **A full statechart model:** Hierarchical and parallel states add expressive power, but the first
  consumer needs one serial agent and would force us to define unused semantics.
- **A fixed phase list:** A list is easy to validate, but it cannot express check-to-fix loops,
  terminal branches, or flag-dependent routing.
- **Agent-selected next steps:** The agent could name the next edge, but that gives routing judgment
  back to the component the sequencer must keep honest.

### Route matching

- **Ordered first match with a required final fallback (selected):** The file shows priority
  directly, and `done` always produces one destination.
- **Require exactly one true predicate:** This catches overlap, but common fallback routes overlap
  every successful condition unless the schema grows a separate default field.
- **Unordered routes with numeric priorities:** Priorities make ordering explicit, but list order
  already carries that information without another conflict rule.
- **Evaluate a consumer expression:** Expressions are concise, but they create another language
  and can hide unbounded or executable behavior.

Every route has an ID in version 1. Validation rejects duplicate IDs, duplicate canonical
predicates, and `always` anywhere except the last route. Runtime history records the selected route
and every predicate result, so an intended priority does not become invisible.

### Flags and applicability

- **Immutable boolean and enum flags (selected):** The engine can validate every supplied value and
  freeze one active graph for the run.
- **Arbitrary scalar flags:** Strings and numbers cover more workflows, but they weaken static
  validation and make near-duplicate values easy to introduce.
- **Mutable flags:** A caller could redirect a run without starting over, but persisted validators
  and cursor history would no longer describe one workflow.
- **Environment variables read on every call:** This avoids CLI repetition, but environment drift
  can silently change routing.

### Validator execution

- **Closed built-in registry (selected):** The engine owns every operation and can document its
  filesystem and Git behavior.
- **Configured shell commands:** Shell validators cover every project, but configuration becomes
  executable and can modify the repository.
- **Python plugin entry points:** Plugins provide typed extension, but they create installation,
  trust, and version compatibility contracts.
- **JSON Schema supplied by the consumer:** JSON Schema handles artifact shape well, but it does
  not cover Git predicates or path existence and would still need a separate operation model.

### Artifact formats

- **JSON artifact inspection in version 1 (selected):** `json.valid`, `json.pointer_equals`, and
  `json.pointer_type` cover the reference workflow with one deterministic data model.
- **JSON and YAML artifact inspection:** YAML is convenient, but it doubles scalar and pointer
  edge cases without helping the first consumer.
- **Existence and non-empty checks only:** These keep the registry small, but they cannot prove that
  an agent produced the declared result.
- **Opaque artifact hashes:** Hashes prove a file changed, but they cannot validate meaning.

### Validator path scope

- **Repository and run-artifact scopes (selected):** Every path stays relative to one declared
  root, and the engine rejects resolved escapes.
- **Arbitrary absolute read-only paths:** The engine still would not write them, but a workflow
  could read unrelated credentials or host data.
- **Artifact paths only:** This creates the narrowest boundary, but Git-adjacent workflows cannot
  validate repository-owned files.
- **Repository-local sequencer artifacts:** Consumers gain shorter paths, but the engine would
  create files in the repository it promised not to modify.

Path containment prevents accidental or configured escape. It is not a security boundary against
another local process that can replace files during a probe, because the external agent already
controls the target repository.

### State location

- **`FDR_HOME` per-run state (selected):** State stays outside the target repository and follows
  the project's existing central-state convention.
- **State beside the workflow:** The state travels with the configuration, but it dirties the
  consumer repository.
- **Operating-system temporary state:** Temporary files avoid repository writes, but cleanup or a
  restart can erase recovery state.
- **One shared SQLite database:** Transactions and indexing are built in, but corruption or lock
  contention affects every repository and complicates manual recovery.

### Repository identity

- **Canonical worktree root plus Git common directory (selected):** Separate clones and linked
  worktrees cannot share a cursor accidentally.
- **Normalized remote URL:** State follows another clone of the same repository, but concurrent
  clones would read and mutate the same run against different working trees.
- **Remote URL plus directory basename:** This matches the existing central artifact slug, but two
  same-named clones still collide and linked worktrees remain ambiguous.
- **Consumer-supplied repository ID:** An explicit ID survives moves, but a typo can attach an old
  run to the wrong working tree.

### Workflow drift detection

- **Canonical parsed-model fingerprint (selected):** Semantic changes block progress, while
  comments and mapping order do not.
- **Raw file hash:** Every byte change is visible, but harmless formatting edits wedge a run.
- **Workflow ID and schema version only:** This permits editing live behavior under an existing
  run, which can skip or repeat work.
- **Automatic state migration:** Migration preserves long runs, but there is no general way to map
  an old cursor and history onto an arbitrary new graph.

`status` always reads persisted state, even when the source workflow is missing or drifted. It
reports configuration health without advancing. `next` and `done` fail closed until the selected
workflow matches the stored fingerprint.

### State persistence

- **One atomically replaced JSON state file (selected):** The old or new cursor survives a crash,
  and a person can inspect the complete run without another tool.
- **Append-only event log:** Events preserve perfect history, but recovery must replay and validate
  the whole log before every command.
- **State file plus rotating backup:** A backup helps manual repair, but two candidate cursors make
  automated recovery ambiguous.
- **SQLite transaction:** SQLite gives atomic commits, but it adds a database lifecycle for one
  small record.

### Transition locking

- **Operating-system file lock (selected):** The operating system releases it on process death,
  and the project already has Unix and Windows implementations.
- **PID lock file:** A crash leaves stale ownership, and PID reuse makes automatic cleanup unsafe.
- **Atomic lock-directory creation:** Directory ownership works across platforms, but a crash still
  needs lease and stale-timeout rules.
- **SQLite write lock:** This is reliable inside SQLite, but it requires selecting SQLite state
  first.

### Mutating-step recovery

- **Persist issue state before returning and require acknowledgement (selected):** A retry cannot
  silently duplicate repository work.
- **Always replay the instruction:** Replay is convenient, but it can apply a partial mutation
  twice.
- **Assume completion when the repository changed:** Any unrelated change could advance the cursor,
  which is exactly the ownership mistake the sequencer must avoid.
- **Timeout lease:** A lease eventually unlocks itself, but elapsed time says nothing about whether
  work finished.

### Successful `done` behavior

- **Return the next instruction in the transition response (selected):** The caller saves one round
  trip, and the engine records mutating issue state before it exposes the instruction.
- **Require a separate `next`:** This makes each command do one thing, but it adds a failure window
  between advancing and learning what to do next.
- **Return only the new step ID:** This reduces response size, but the caller still needs `next` to
  retrieve the contract it needs.

### Force behavior

- **Bypass failed postconditions only (selected):** `--force` handles a deliberate exception while
  preserving identity, configuration, ordering, and probe invariants.
- **Bypass every blocker:** Universal force can recover almost anything, but it can also attach the
  wrong repository or workflow to existing state.
- **No force option:** This keeps validation absolute, but a conservative validator could wedge a
  legitimate run with no auditable escape.

### Git truth

- **Live read-only probes against local refs (selected):** Results describe the repository the
  agent actually sees without modifying it.
- **Fetch before every probe:** Remote truth becomes fresher, but `git fetch` changes Git state and
  can prompt for credentials or block on the network.
- **GitHub API queries:** The server can answer remote questions, but local-only commits, detached
  worktrees, and non-GitHub remotes fall outside that view.
- **Persist Git booleans in sequencer state:** Reads become fast, but staged changes, refs, and
  `HEAD` drift immediately make them stale.

### Working-tree change detection

- **Content digest of `HEAD`, diffs, and untracked data (selected):** A mutating step proves that
  repository content changed even when it was dirty before and after.
- **Dirty booleans:** Booleans are cheap, but `dirty` before and `dirty` after does not prove a
  change.
- **Changed path names:** Paths catch additions and removals, but an edit to an already dirty file
  can keep the same set.
- **Commit-only comparison:** Commits provide a strong marker, but a mutating step may correctly
  leave uncommitted work.

## Adversarial findings

- Requiring a workflow file on every command made `status` fail at the exact moment recovery needs
  persisted truth most.
- A one-choice `--format json` option added a contract without adding a real choice.
- Keying state by workflow ID forced recovery to parse configuration before it could locate the
  state that explains the failure.
- Commit-only `HEAD` drift missed switching between a branch and detached `HEAD` at the same
  commit.
- Dirty-state booleans could report no change when an agent edited a repository that was already
  dirty.
- YAML artifact validators doubled the first registry without supporting the reference consumer.
- Anonymous first-match routes made audit history explain the destination but not the configured
  decision that selected it.
- Promising JSON for an uncatchable process termination was impossible. The protocol can guarantee
  one object for completed calls and deterministic persisted truth after a retry.
- Path containment could be misread as protection from a hostile local process. It prevents
  configured escape, while the external agent remains inside the repository's trust boundary.

These findings changed the original proposal. The decision below uses persisted state for
recovery, unconditional JSON, repository-scoped run IDs, named routes, JSON-only artifact
inspection, content-based Git snapshots, and an explicit local-process trust boundary.

## Decision outcome

We will add a versioned YAML workflow, a JSON response protocol, and four commands under
`fix-die-repeat sequencer`. The sequencer will keep one atomic state record per repository and
run under `FDR_HOME`. The record freezes its workflow identity and normalized configuration, while
an operating-system-backed lock protects every transition.

The external agent owns all repository changes. The sequencer only reads the target repository,
writes its own state and artifact directory under `FDR_HOME`, and returns the next instruction.

## Command surface

The public command shape is:

```text
fix-die-repeat sequencer \
  --run-id RUN_ID \
  [--repo PATH] \
  init \
  --workflow PATH \
  [--flag NAME=VALUE ...]

fix-die-repeat sequencer \
  --run-id RUN_ID \
  [--repo PATH] \
  next \
  [--workflow PATH]

fix-die-repeat sequencer \
  --run-id RUN_ID \
  [--repo PATH] \
  done STEP \
  [--workflow PATH] \
  [--force | --recover]

fix-die-repeat sequencer \
  --run-id RUN_ID \
  [--repo PATH] \
  status \
  [--workflow PATH]
```

`--run-id` and `--repo` sit on the `sequencer` group because every command must select the same
run and repository. `--workflow` sits on each command because `init` requires it, later commands
may override the stored source path, and `status` must still work without it. `--force` and
`--recover` sit on `done` because no other command may advance or reset an attempt.

`--repo` defaults to the current working directory. JSON is the only protocol format in version 1,
so there is no output-format option.

`RUN_ID`, workflow IDs, flag names, step IDs, route IDs, postcondition IDs, and terminal codes must
match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. This keeps them readable while preventing path traversal
and unbounded state paths.

### Existing command compatibility

The root Click command will become a group with `invoke_without_command=True` and
`no_args_is_help=False`. Its existing options stay on the root callback, and that callback runs
the current loop whenever no subcommand appears.

These invocations keep their current meaning:

```text
fix-die-repeat
fix-die-repeat -c "make test"
fix-die-repeat --pr-review
fix-die-repeat --improve-prompts
```

Sequencer options do not become root options. Root options must appear before a root subcommand,
which means `fix-die-repeat sequencer` cannot accidentally consume an existing loop option.

`fix-die-repeat --help` will show the existing loop options plus the new `sequencer` command.
`fix-die-repeat sequencer --help` will show only the sequencer protocol.

`init` stores the canonical workflow source path and normalized workflow. `next` and `done` load
the explicit `--workflow` when present, otherwise they load the stored source path. Either command
returns `blocked` when the stored source is missing, invalid, or semantically drifted. An explicit
path that cannot be read returns `environment_error`.

`status` always reads persisted state first. It reports the workflow as `matching`, `missing`,
`invalid`, or `drifted`, but a configuration problem cannot hide the cursor or recovery state.
An explicit matching source on `init`, `next`, or `done` replaces a missing or moved source path
atomically. `status` may probe an explicit path, but it never persists that path because `status`
does not change state.

## Response protocol

Every completed non-help sequencer invocation writes exactly one UTF-8 JSON object followed by a
newline to `stdout`. Expected outcomes leave `stderr` empty. Usage, environment, and internal
errors also write the JSON response to `stdout`, then write one concise diagnostic line to
`stderr`.

`--help` and `--version` keep Click's normal text output and return `0`.

Every response contains these fields:

| Field | Type | Meaning |
|---|---|---|
| `protocol_version` | integer | Response schema version, initially `1`. |
| `command` | string | `init`, `next`, `done`, or `status`. |
| `outcome` | string | One of the outcomes in the exit-code table. |
| `repository` | string or null | Canonical absolute target repository path. |
| `workflow_id` | string or null | ID from the validated workflow. |
| `run_id` | string or null | Selected run ID. |
| `state_revision` | integer or null | Monotonic revision of the persisted run. |
| `configuration` | object or null | Stored source path, fingerprint, and current matching state. |
| `step` | object or null | Current step ID, instruction, and mutability. |
| `forced` | boolean | Whether this response advanced past failed postconditions. |
| `created` | boolean | Whether `init` created the state record. |
| `repeated` | boolean | Whether the command returned an already established result. |
| `gaps` | array | Named validation, state, or environment gaps. |
| `route` | object or null | Route selected by a successful `done`. |
| `terminal` | object or null | Terminal code, status, and message. |
| `recovery` | object or null | Recovery reason and allowed acknowledgement. |
| `message` | string | Short stable summary for logs. |

`step`, when present, has this shape:

```json
{
  "id": "fix",
  "instruction": "Fix the failures recorded in the check result.",
  "mutates_repository": true,
  "artifact_root": "/absolute/FDR_HOME/sequencer/.../artifacts"
}
```

`gaps` entries use stable codes instead of forcing consumers to parse `message`:

```json
{
  "code": "postcondition_failed",
  "subject": "check-result",
  "message": "artifacts/check-result.json does not exist"
}
```

Consumers must ignore unknown response fields. We will increment `protocol_version` before
removing a field, changing a field's type, or changing the meaning of an existing outcome.

### Outcomes and exit codes

| Exit | `outcome` | Meaning |
|---:|---|---|
| `0` | `proceed` | The command succeeded and the workflow can continue. |
| `2` | `environment_error` | Git, filesystem, encoding, or another required probe failed. |
| `3` | `terminal` | The workflow reached or already occupies a declared terminal state. |
| `4` | `blocked` | Postconditions, ordering, state compatibility, or configuration drift blocked progress. |
| `5` | `recovery` | A mutating instruction was already issued and needs explicit reconciliation. |
| `64` | `usage_error` | CLI syntax or supplied values are invalid. |
| `70` | `internal_error` | An invariant failed or an unexpected implementation error escaped. |
| `130` | `interrupted` | The sequencer received an interrupt before it could return a normal outcome. |

Terminal routes declare both a machine code and a status:

```yaml
terminal:
  code: checks-passed
  status: success
  message: Checks and review passed.
```

`status` may therefore be `success`, `failure`, or `stopped`. All three return exit `3`, because
the caller must inspect the declared terminal status instead of treating terminal failure as a
protocol failure.

We will preserve the prototype's exit codes `2`, `3`, and `4`. Sequencer usage errors move to
`64` so they cannot collide with environment failures, while the existing root command keeps its
current Click behavior.

### Repeated and out-of-order calls

- Repeated compatible `init` returns the existing state with `created: false`,
  `repeated: true`, and no state change. Its outcome reflects the persisted state, so an issued
  mutating step returns `recovery`.
- `init` with a different source path but the same workflow ID and fingerprint records the new
  path and increments `state_revision`.
- `init` against a terminal run returns `terminal`.
- `init` with different flags or a different workflow fingerprint returns `blocked`.
- `next`, `done`, or `status` for a missing run returns `blocked` with `run_not_initialized`.
- Repeated `next` on a read-only step returns the same instruction with `repeated: true`.
- Repeated `next` on an issued mutating step returns `recovery`.
- `done` with the current step validates and advances once.
- `done --recover` outside an issued mutating step returns `blocked` with
  `recovery_not_required`.
- Repeated `done` for a step that already advanced returns `blocked` with `stale_step`.
- `done` for a future or unrelated step returns `blocked` with `out_of_order_step`.
- `next`, `done`, and `status` after terminal completion return the same terminal result.
- `status` returns `recovery` for an issued mutating step, `blocked` for configuration drift, and
  `proceed` for any other incomplete state. Terminal state takes precedence over configuration
  health. `status` never changes state.

A successful `done` returns the next instruction in the same response. This removes an
unnecessary round trip while preserving `next` as a safe way to retrieve that instruction again.

## Workflow configuration

Consumers select an explicit YAML file through `init --workflow`. The file may live in the target
repository, but the sequencer only reads it. Later commands use the stored source path unless the
caller supplies another file with the same workflow ID and fingerprint. The first schema has this
shape:

```yaml
schema_version: 1
id: check-fix-review
start: check

flags:
  review:
    type: boolean
    default: true

steps:
  check:
    instruction: Run the repository checks and write the result artifact.
    mutates_repository: false
    postconditions:
      - id: check-result
        validator:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: check-result.json
          pointer: /schema_version
          expected: 1
      - id: check-passed-type
        validator:
          op: json.pointer_type
          path:
            scope: artifacts
            value: check-result.json
          pointer: /passed
          expected: boolean
    routes:
      - id: fix-failed-checks
        when:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: check-result.json
          pointer: /passed
          expected: false
        to: fix
      - id: review-passing-checks
        when:
          flag:
            name: review
            equals: true
        to: review
      - id: finish-without-review
        when: always
        terminal:
          code: checks-passed
          status: success
          message: Checks passed.

  fix:
    instruction: Fix the failures recorded in the check result.
    mutates_repository: true
    postconditions:
      - id: repository-changed
        validator:
          op: git.working_tree_changed
    routes:
      - id: check-fix
        when: always
        to: check
        repeat: true

  review:
    instruction: Review the current change and write the review result artifact.
    mutates_repository: false
    applies_when:
      flag:
        name: review
        equals: true
    postconditions:
      - id: review-result
        validator:
          op: json.valid
          path:
            scope: artifacts
            value: review-result.json
      - id: review-passed-type
        validator:
          op: json.pointer_type
          path:
            scope: artifacts
            value: review-result.json
          pointer: /passed
          expected: boolean
    routes:
      - id: fix-review-findings
        when:
          op: json.pointer_equals
          path:
            scope: artifacts
            value: review-result.json
          pointer: /passed
          expected: false
        to: fix
        repeat: true
      - id: finish-reviewed-run
        when: always
        terminal:
          code: checks-passed
          status: success
          message: Checks and review passed.
```

### Flags and step applicability

Schema version 1 supports `boolean` and closed `enum` flags. A flag may declare a default;
otherwise `init` requires it. `init --flag NAME=VALUE` may repeat, but duplicate or unknown names
are usage errors.

The engine parses flag values against their declarations, persists the resolved values, and
includes them in the workflow fingerprint. No command can change them after `init`.

`applies_when` uses only flag predicates. The engine resolves step applicability during `init`,
then validates the selected active graph before it writes state. A route that can select an
inactive target for the resolved flags makes the workflow invalid. Inactive steps and their
postconditions never run.

Postconditions may also use `when` with flag predicates. This supports flag-dependent artifacts
without forcing a consumer to invent duplicate steps.

### Steps and routing

Every active step declares:

- `instruction`, a non-empty string returned verbatim to the external agent.
- `mutates_repository`, an explicit boolean that controls recovery behavior.
- `postconditions`, an optional ordered list of named validators.
- `routes`, a non-empty ordered list of predicates and destinations.

The engine evaluates every applicable postcondition when `done` runs. It reports all safe gaps
together. It evaluates routes only after every postcondition passes or `--force` records their
failure.

Routes use first-match order, have unique IDs, and must end in `when: always`. A route selects
exactly one `to` step or one `terminal` object. A route that intentionally returns to an earlier
step must set `repeat: true`.

We reject a graph when removing every `repeat: true` edge still leaves a cycle. This allows
declared work loops while catching accidental cycles that can never terminate.

### Closed validator and predicate registry

Schema version 1 supports these artifact validators:

| Operation | Result |
|---|---|
| `path.exists` | The scoped path exists. |
| `file.non_empty` | The scoped path is a non-empty regular file. |
| `json.valid` | The scoped file contains one valid JSON value. |
| `json.pointer_equals` | The RFC 6901 pointer exists and equals the declared scalar or JSON value. |
| `json.pointer_type` | The RFC 6901 pointer exists and has the declared JSON type. |

Schema version 1 supports these live Git predicates:

| Operation | Result |
|---|---|
| `git.is_clean` | No staged, unstaged, or untracked changes exist. |
| `git.has_staged_changes` | The index differs from `HEAD` (or contains entries in an unborn repository). |
| `git.has_unstaged_changes` | The working tree differs from the index. |
| `git.has_untracked_changes` | At least one untracked path exists. |
| `git.head_changed` | The live `HEAD` commit or symbolic ref differs from the baseline recorded by `init`. |
| `git.has_unpushed_commits` | `HEAD` contains commits not represented by the selected local remote-tracking reference. |
| `git.working_tree_changed` | Any staged, unstaged, or untracked state differs from the snapshot recorded when the step was issued. |

Conditions may use a validator operation directly or combine conditions through `all`, `any`, and
`not`. Flag conditions use `flag.name` and `flag.equals`. The registry rejects every unknown
operation and field. It never imports a callable, evaluates an expression, expands a template, or
runs a configured command.

### Path resolution

Every validator path declares one of two scopes:

- `repo` resolves from the canonical target repository root and remains read-only.
- `artifacts` resolves from the run's artifact directory under `FDR_HOME`.

Paths must be relative UTF-8 paths. We reject absolute paths, empty components, `.` components,
`..` components, NUL bytes, and any symlink resolution that escapes the declared scope.

The sequencer creates the artifact directory during `init` and returns it in every step response.
It never creates the declared artifacts. The external agent may write only the artifacts that its
workflow requires.

### Loading and validation

The loader reads at most 1 MiB as UTF-8, uses a safe YAML loader with duplicate-key rejection, and
then validates a strict Pydantic model with `extra="forbid"`. YAML aliases, custom tags, and
non-string mapping keys are invalid. YAML values must fit the JSON data model, so timestamps,
non-finite numbers, and other loader-specific scalar types are invalid.

Validation reports every independent gap that it can collect safely:

- Unsupported `schema_version`.
- Missing or duplicate workflow, flag, step, postcondition, and route fields.
- Unknown fields, operations, flags, and route targets.
- Invalid IDs and unsupported flag values.
- Missing `start`, inactive `start`, and inactive route targets.
- Steps that are unreachable for the resolved flags.
- Routes without a final `always` fallback.
- Duplicate route IDs and duplicate canonical predicates.
- Routes with both or neither of `to` and `terminal`.
- Cycles that remain after removing declared repeat edges.
- Absolute, escaping, or otherwise invalid validator paths.
- Any validator or predicate field that attempts to name code, a module, an executable, or a
  command.

An unreadable or missing workflow passed explicitly to `init`, `next`, `done`, or `status` is an
`environment_error`. A missing stored source for an existing run returns `blocked` because the
persisted configuration relationship broke. A readable workflow that violates the schema returns
`blocked` with every safe configuration gap.

`init` performs all workflow validation before it creates the run directory or state file. It
rechecks the workflow after acquiring the transition lock, so a file change between validation and
state creation fails without creating `state.json`.

Commands against existing state acquire the run lock before they load the explicit or stored
workflow. This prevents another transition from changing the expected state while configuration
validation runs. `status` reads persisted state under the lock before it probes configuration.

## State, recovery, and concurrency

### State identity and layout

The sequencer requires a Git working tree, including an unborn repository. It resolves the
top-level worktree path without changing directories or Git state.

Repository identity combines the canonical absolute worktree root and the local Git common
directory. We hash both values with SHA-256, which keeps separate clones and linked worktrees from
sharing state accidentally.

The state layout is:

```text
<FDR_HOME>/sequencer/
  repositories/<repository-key>/
    runs/<run-id>/
      state.json
      transition.lock
      artifacts/
```

The run ID must be unique inside one repository. Directory components use the validated
human-readable ID plus a short SHA-256 suffix. The full identity values remain inside `state.json`
so hash collisions fail closed.

`state.json` records:

- State schema version, response protocol version, and monotonically increasing revision.
- Repository identity and diagnostic Git metadata.
- Workflow ID, source path, normalized workflow, workflow fingerprint, and resolved immutable
  flags.
- Current step, run status, terminal result, and mutating-step issue state.
- Initial `HEAD`, initial dirty snapshot, and the snapshot taken when a mutating step was issued.
- Transition history, including failed validators bypassed by `--force`.

The workflow fingerprint is SHA-256 over canonical JSON produced from the fully parsed workflow
model. Formatting, comments, and mapping order do not change it. Any semantic configuration change
does.

### `init` compatibility

- Missing state creates a new run after workflow and repository validation.
- Compatible incomplete state returns the current result without writing.
- Compatible terminal state returns the persisted terminal result.
- Different flags, workflow identity, repository identity, or workflow fingerprint return
  `blocked`.
- A different workflow source with the same ID and fingerprint is compatible and replaces the
  stored source path, because a path move does not change behavior.
- Corrupt, partial, or unknown state schema returns `environment_error`.

Configuration drift never accepts `--force`. The consumer must choose a new `run-id`, because
continuing old state against a new graph can skip work without a reliable audit trail.

### Atomic writes

The state writer serializes canonical JSON to a unique temporary file in the run directory, calls
`flush()` and `fsync()`, replaces `state.json` with `os.replace()`, then synchronizes the parent
directory where the platform supports it. A process death can therefore expose the complete old
state or the complete new state, never a partial cursor.

The writer keeps only `state.json`. It does not rotate backups automatically, because a second
state file would create ambiguity about which cursor is authoritative.

### Locking

Every command opens `transition.lock` and takes an exclusive operating-system lock for the whole
read, probe, validation, route, and write transaction. Unix uses `fcntl.flock`, and Windows uses
`msvcrt.locking`, matching the project's existing cross-platform lock behavior.

The operating system releases the lock when a process exits, so a crashed process cannot leave a
permanent stale lock. The lock file may remain on disk and carries no ownership truth.

Concurrent `done` calls serialize. The first matching call advances and increments
`state_revision`; the second observes the new cursor and returns `stale_step`. No caller can
produce a second transition from the old revision.

### Mutating-step recovery

Every command that returns an instruction treats read-only and mutating steps differently. This
includes `next` and a successful `done` that returns the next step:

- A read-only step remains `pending`, so repeated `next` returns the same instruction.
- A mutating step records `issued`, the current repository snapshot, and a new revision before it
  returns the instruction.

If a caller asks for `next` while that mutating step remains `issued`, the sequencer returns
`recovery`. It cannot know whether the external agent stopped before, during, or after its work.

The consumer reconciles the repository, then chooses one explicit action:

- `done STEP` validates the current repository and advances when the work finished.
- `done STEP --recover` acknowledges that the work did not finish, records the acknowledgement,
  records a new repository baseline, and returns the same instruction as a new issued attempt.

`--recover` and `--force` are mutually exclusive. Recovery never edits the repository. Read-only
steps do not enter recovery because replaying their instruction cannot duplicate a repository
mutation.

A catchable interrupt before a state write returns `interrupted` with the old revision. An
interrupt after an atomic state write returns the newly persisted result when the caller retries,
even if the original process died before writing its response. An uncatchable process termination
cannot return a response, so the retry result remains the source of truth.

## Live Git predicates

Git probes run against the target repository at command time with an argument vector and
`shell=False`. The workflow cannot supply Git arguments.

`init` records:

- `HEAD`, or `null` for an unborn repository.
- Current branch name, or `null` for detached `HEAD`.
- Configured upstream, when one exists.
- Staged, unstaged, and untracked dirty-state booleans.

`git.head_changed` compares the live commit and symbolic ref with the recorded baseline. It
returns `false` when both states are unborn with the same symbolic ref. It returns `true` when one
side is unborn, the commit IDs differ, or `HEAD` switches between branches or detached state at the
same commit.

`git.has_unpushed_commits` uses the current local remote-tracking information without fetching,
because a fetch would modify Git state:

- With an upstream, it checks whether `HEAD` has commits outside the upstream's ancestry.
- Without an upstream, including detached `HEAD`, it returns `false` when any local
  `refs/remotes/*` contains `HEAD`, and `true` when remote-tracking refs exist but none contains
  `HEAD`.
- With no remote-tracking refs, a missing remote, or a configured upstream that no longer
  resolves, it returns `environment_error` because the sequencer cannot prove whether the commit
  was pushed.
- In an unborn repository, it returns `false` because no commit exists to push.

These results describe local remote-tracking refs, not the live server. Consumers that need fresh
remote truth must fetch before they invoke the sequencer.

A dirty repository at `init` is valid. Absolute dirty predicates describe the live state, while
`git.working_tree_changed` compares the live snapshot with the snapshot recorded when the current
mutating instruction was issued.

The working-tree snapshot hashes `HEAD`, its symbolic ref, the staged binary diff, the unstaged
binary diff, and every untracked path. It streams regular-file payloads and hashes symlink targets
without following them. It records other filesystem types without opening them, because reading a
FIFO or device could block or produce side effects. This detects a content change even when the
repository stays dirty before and after the step.

Git commands may use documented non-zero values as data. For example, `git diff --quiet` returns
`1` when a difference exists. Every other probe failure returns `environment_error`; the engine
never converts a permission error, deleted ref, malformed repository, or unavailable Git binary
into an ordinary false predicate.

## Force behavior and audit history

`done STEP --force` may bypass failed postconditions only after the step name and state match. It
cannot bypass:

- Invalid workflow configuration.
- Configuration drift.
- Repository or run identity mismatch.
- A stale or out-of-order step.
- A Git probe that failed before it could produce a predicate result.
- Recovery state.

The transition history records the prior step, selected route, previous and new revision,
timestamp, `forced: true`, and every bypassed gap. The response repeats that information through
`forced`, `gaps`, and `route`.

## Self-contained example

The first shipped example will live under `examples/sequencer/check-fix-review/`. It will include:

- A version 1 workflow.
- A small target fixture copied into a temporary test repository.
- Agent-owned scripts that write `check-result.json` and `review-result.json` only for the
  example.
- A subprocess test that drives `init`, `next`, `done`, recovery, force, repeat routing, and
  terminal completion.

The example will not use the current review sentinel or depend on issue
[#29](https://github.com/CTristan/fix-die-repeat/issues/29). Its JSON artifacts belong only to the
example, so the sequencer schema does not accidentally freeze the future review-verdict contract.

## Consequences

Good, because consumers get deterministic routing, stable machine responses, atomic state, and a
recovery path that never lets the sequencer touch their work.

Good, because closed validators and scoped paths keep workflow configuration declarative.

Good, because the response and workflow versions let us evolve either contract deliberately.

Bad, because consumers must write result artifacts and acknowledge interrupted mutating steps.
That extra protocol work is necessary because the sequencer cannot observe an external agent's
intent.

Bad, because `git.has_unpushed_commits` depends on local remote-tracking refs. The no-write
guarantee prevents the sequencer from refreshing those refs itself.

Bad, because content-based working-tree snapshots can read large untracked files. Streaming keeps
memory bounded, but repository size still affects transition time.

Bad, because one run ID can name only one workflow inside a repository. A consumer must choose a
new run ID instead of reusing an old name for unrelated work.

Bad, because moving a worktree changes its repository identity. This prevents cross-worktree
collisions, but an in-progress run must finish at the original path or start again.

Bad, because `next` and `done` stop when configuration disappears or drifts. `status` remains
available, but progress requires restoring matching configuration or starting a new run.

Bad, because version 1 has no human output mode or YAML artifact validators. We can add either
later without weakening the initial machine contract.

Bad, because adding a root subcommand requires a careful Click migration and complete regression
coverage for every existing root invocation.

## Required implementation tests

The implementation will use test-first slices for:

- Strict workflow loading, aggregated errors, active-graph validation, and fingerprinting.
- Persisted `status` with matching, missing, invalid, drifted, and relocated workflow sources.
- Validator path containment, artifact operations, and closed operation lookup.
- Named ordered routing, duplicate predicate rejection, declared repeat edges, stale calls, and
  forced history.
- Atomic state replacement and configuration-drift rejection.
- Concurrent transition serialization.
- Read-only replay and mutating-step recovery acknowledgement.
- Detached `HEAD`, missing upstreams, unborn repositories, dirty initial state, missing remotes,
  deleted refs, same-commit symbolic-ref changes, special untracked files, and failed Git probes.
- Subprocess responses for proceed, blocked, terminal, recovery, forced, environment error, usage
  error, internal error, stale calls, repeated calls, and interruption.
- Existing root CLI invocations before and after the Click group migration.

The final implementation must pass `./scripts/ci.sh --check-only`, keep coverage at or above 80%,
and pass the Ruff policy check.
