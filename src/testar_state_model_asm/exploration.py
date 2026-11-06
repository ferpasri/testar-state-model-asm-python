"""Coordinate exploration using driver-provided observation and execution capabilities."""

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from .errors import require


@dataclass(frozen=True)
class ObservationUpdate:
    """Current observation, eligible actions, and the evidence supporting them."""

    context: dict
    sources: list
    selected_runs: list


@dataclass(frozen=True)
class ExecutionUpdate:
    """Evidence after an execution attempt, including whether the action started."""

    sources: list
    selected_runs: list
    invoked: bool = True


class ExplorationDriver(Protocol):
    """Connect GUI operations and evidence recording to the ASM-controlled cycle.

    Discover and filter actions, capture states, and execute the selected action.
    The ASM decides which action to try; the driver handles the testing technology.
    """

    def observe(self) -> ObservationUpdate:
        """Capture and record the state and discover eligible actions."""

    def prepare_execution(self, selection: dict) -> dict | None:
        """Check applicability and allocate an execution reference; return None to refresh."""

    def execute(self, selection: dict, execution: dict) -> ExecutionUpdate:
        """Execute and record the outcome, including errors; return the new source snapshots."""


def explore(run, driver: ExplorationDriver, *, max_cycles=1000):
    """Repeat observe -> select -> execute -> update until a stopping condition."""
    require(type(max_cycles) is int and max_cycles > 0, "invalid_configuration", "max_cycles must be positive.")
    prefix = str(uuid4())

    def scope(incoming):
        combined = {(r["source"], r["run"]): r for r in run._state_model_data.snapshot["selected_runs"]}
        combined.update({(r["source"], r["run"]): r for r in incoming})
        return list(combined.values())

    for cycle in range(max_cycles):
        stopped = run._exploration_stop()
        if stopped is not None:
            return stopped
        observation = driver.observe()
        run.update_evidence(f"{prefix}:observe:{cycle}", observation.sources, scope(observation.selected_runs))
        run.set_context(observation.context)
        selection = run.select()
        if selection["result_type"] == "no_action":
            return selection
        execution = driver.prepare_execution(selection)
        if execution is None:
            continue
        run.begin_execution(selection, execution)
        # Unexpected driver exceptions propagate, retaining the outstanding execution.
        report = driver.execute(selection, execution)
        if not report.invoked:
            run.report_not_invoked(execution)
            continue
        response = run.report_execution(execution, f"{prefix}:execute:{cycle}",
                                        report.sources, scope(report.selected_runs))
        if response["status"] in ("awaiting_execution_result", "awaiting_resulting_observation"):
            return run.select()
    stopped = run._exploration_stop()
    if stopped is not None:
        return stopped
    return {"result_type": "exploration_stopped", "reason": "cycle_limit_reached",
            "message": "The exploration cycle limit was reached."}
