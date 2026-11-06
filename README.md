# TESTAR ASM Python

Native exploration and action selection using concrete execution evidence and abstraction rules.

The library implements Unvisited ASM, Random ASM, directed navigation, and action-count-based switching. It controls the exploration cycle through an `ExplorationDriver` that observes the application, discovers eligible actions, executes selections, and records evidence.

~~~python
from testar_state_model_asm import AsmRun

run = AsmRun(configuration, historical_sources, selected_runs)
stopped = run.explore(driver)
~~~

`historical_sources` and `selected_runs` can be empty. `read_source(directory)` loads a trace through the evidence reader; `evidence_source(evidence)` projects an existing `ReconstructedEvidence` for selection. Configuration follows the [shared contract](https://github.com/ferpasri/testar-state-model-asm-specs/blob/main/docs/contract.md).

## Build and test

Install the dependencies declared in [pyproject.toml](pyproject.toml) from your package source or previously built wheels. From this repository:

~~~powershell
python -m pip install .
python tools/prepare_conformance.py
python -m unittest discover -s tests -t . -v
python -m pip wheel --no-deps . -w dist
~~~

The preparation command downloads the release pinned in [compatibility.json](compatibility.json), verifies its archive digest, and stores the shared cases in an ignored cache.

## Readable algorithm tests

- [State and action matching](tests/test_state_and_action_matching.py): how property normalization groups states and widget descriptions link actions.
- [Unvisited selection](tests/test_unvisited_action_selection.py): local actions, shortest navigation, and target preference.
- [Exploration control](tests/test_exploration_control.py): driver calls, new evidence, switching, and stopping.

The [shared conformance runner](tests/test_shared_conformance.py) verifies every published scenario in normal and reversed collection order. [Specifications](docs/SPEC.md) describe the implementation responsibilities and public interfaces; remaining work is in [TODO](TODO.md).
