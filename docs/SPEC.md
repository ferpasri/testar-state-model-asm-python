# Implementation specification

| Component | Responsibility |
| --- | --- |
| `AsmRun` | Own the current model, selection, controller limits, action execution contexts, updates, and cancellation. |
| `StateModelData` | Validate reconstructed snapshots with the evidence library, preserve accepted facts, and obtain the model from the abstraction library. |
| `ActionSelector` | Share matching and execution counting between ASMs. |
| `RandomActionSelector` | Choose randomly among current eligible actions. |
| `UnvisitedActionSelector` | Try a local unvisited action or navigate toward a state with unvisited actions. |
| `ExplorationDriver` | Supply `observe()`, `prepare_execution(selection)`, and `execute(selection, execution)`. |
| `evidence_source / read_source` | Prepare selection-relevant data from validated reconstructed evidence or stored traces. |

`AsmRun` exposes `set_context`, `select`, `begin_execution`, `report_execution`, `report_not_invoked`, `update_evidence`, `cancel`, and `explore`. `AsmError.code` identifies contract errors. A supplied `choose_index(size)` function allows controlled selection in tests.

Each ASM has its own selector class. Adding an ASM requires a selector and configuration validation, with its behavior defined in the shared ASM specification and conformance cases.

## Selection and evidence

Historical and current evidence use the same abstraction rules. Matching states and actions can share execution history across sources. Original source-qualified references remain in decision explanations.

Unvisited ASM chooses a current unvisited action first. Otherwise it navigates toward the nearest state with known unvisited actions, preferring more unvisited abstract actions among equally near states. Random ASM selects among current eligible choices. The controller switches configured ASMs after their action limits or when one cannot select.

Execution counts retain the evidence statuses. Failed attempts count as visited; completed recorded transitions support navigation. Observation-order links do not supply executable routes. Unknown matching remains explicit through the exclude or observation-local counting policy.

Updates preserve accepted facts and count identical records once. Decisions are tied to an observation and ASM data revision. An invalid operation leaves accepted state-model data unchanged. Decisions and returned models are independent copies. Calls that change or inspect run state are serialized within that run.

## Exploration entry point

The library requests observation and action discovery, selects, requests execution, accepts the resulting evidence, and repeats. The exploration driver records actual interactions through its evidence adapter and library.

An observation update contains the context, source snapshots, and selected runs. An execution update contains source snapshots, selected runs, and whether invocation occurred. The library retains the historical run scope when combining these updates.

The exploration driver checks applicability before allocating the execution reference. It returns no reference when the observation needs refreshing. An execution reported as never invoked clears the active execution without increasing action counts.

Expected operation errors are recorded by the exploration driver and returned as execution evidence. Unexpected driver errors propagate with its action execution context retained, so the caller can report or investigate the outstanding operation. Pending execution reports stop the synchronous loop until more evidence is supplied.

The optional cycle limit bounds observation attempts, including attempts where execution could not start. The default is 1000 cycles. Cancellation and the overall action limit are checked before another capture.

## Processing and compatibility

Shortest-route search is limited to 10000 queued alternatives per selection and reports `processing_limit` when exceeded. Abstraction and evidence validation retain their own processing limits.

The [compatibility declaration](../compatibility.json) identifies supported shared behavior and the pinned conformance archive. [ASM Specs](https://github.com/ferpasri/testar-state-model-asm-specs) owns the selection and lifecycle contract; Evidence Specs and Abstraction Specs own their corresponding data and matching rules.

The in-memory ASM data revision starts at zero for each ASM run and increases when accepted data changes. It is available through `asm_data_revision` and the response field `asm_data_revision`. It identifies the data used for a selection and is not stored in evidence traces.
