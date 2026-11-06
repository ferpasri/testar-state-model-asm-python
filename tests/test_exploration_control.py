"""The library controls the cycle; a driver implementation performs and records operations."""

from copy import deepcopy
import unittest

from testar_state_model_asm import AsmRun, ObservationUpdate, ExecutionUpdate
from tests.support import case, run_case


class RecordedExplorationDriver:
    """Simulate GUI operations using shared evidence instead of a browser.

    observe supplies the initial form and actions. execute supplies pending or
    completed evidence. calls records what the exploration loop requested.
    """

    def __init__(self, data, *, pending=False, unexpected_error=False):
        self.data = data
        self.pending = pending
        self.unexpected_error = unexpected_error
        self.calls = []

    def observe(self):
        self.calls.append("observe and discover")
        snapshot = self.data["evidence_sets"]["initial"]
        return ObservationUpdate(self.data["steps"][0]["context"], **snapshot)

    def prepare_execution(self, selection):
        self.calls.append("check applicability")
        return self.data["steps"][2]["execution"]

    def execute(self, selection, execution):
        self.calls.append("execute and record")
        if self.unexpected_error:
            raise RuntimeError("The driver connection was lost.")
        return ExecutionUpdate(**self.data["evidence_sets"]["pending" if self.pending else "completed"])


class ExplorationControlTests(unittest.TestCase):
    def test_one_action_is_executed_recorded_and_used_to_update_the_model(self):
        # Start with a form and two clicks, then report the selected click as completed.
        # A one-action limit must stop exploration after updating the model, before another capture.
        data, _ = case("execution-lifecycle")
        configuration = deepcopy(data["configuration"])
        configuration["total_action_limit"] = 1
        run = AsmRun(configuration, **data["evidence_sets"]["initial"], choose_index=lambda size: 0)
        driver = RecordedExplorationDriver(data)

        stopped = run.explore(driver)

        self.assertEqual("action_limit_reached", stopped["reason"])
        self.assertEqual(["observe and discover", "check applicability", "execute and record"], driver.calls)
        first = next(action for action in run.model["actionEvidence"]["actions"] if action["executions"])
        self.assertEqual("completed", first["executions"][0]["status"])

    def test_a_pending_execution_stops_the_loop_until_the_integration_reports_its_result(self):
        # Return evidence that the selected action is still awaiting a result.
        # Exploration must wait instead of selecting another action against an uncertain GUI state.
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"], choose_index=lambda size: 0)
        stopped = run.explore(RecordedExplorationDriver(data, pending=True))
        self.assertEqual("execution_in_progress", stopped["reason"])

    def test_an_unexpected_integration_error_does_not_invent_an_execution_result(self):
        # Simulate a lost driver connection after execution began. Keep it outstanding
        # for investigation, without inventing a successful or failed execution record.
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"], choose_index=lambda size: 0)
        with self.assertRaisesRegex(RuntimeError, "connection"):
            run.explore(RecordedExplorationDriver(data, unexpected_error=True))
        self.assertEqual("execution_in_progress", run.select()["reason"])
        self.assertFalse(any(action["executions"] for action in run.model["actionEvidence"]["actions"]))

    def test_the_controller_switches_after_one_action_then_returns_after_the_next(self):
        # The case configures Unvisited at index 0 and Random at index 1, one action each.
        # Verify its full sequence of context, selection, and execution-report operations: 0 -> 1 -> 0.
        data, expected = case("controller-action-limits-and-return")
        selected_indexes = [r["asm_index"] for r in expected["responses"] if r["result_type"] == "selected"]
        self.assertEqual([0, 1, 0], selected_indexes)
        run_case(self, data, expected)
