"""Incremental GUI evidence retains the same validation and model as a fresh load."""

from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from testar_evidence import ReconstructedEvidence
from testar_evidence.codec import to_data
from testar_state_model_asm import AsmError, AsmRun
from tests.support import case, reversed_case


class EvidenceUpdateTests(unittest.TestCase):
    def test_only_the_new_state_is_validated_after_a_completed_click(self):
        # The form was validated at startup. A click adds its result and a second
        # observation; the first observation must not be validated again.
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"])
        completed = data["evidence_sets"]["completed"]
        applied = []
        original_apply = ReconstructedEvidence.apply

        def track(history, event):
            applied.append(event.event_type)
            return original_apply(history, event)

        with patch.object(ReconstructedEvidence, "apply", track):
            self.assertTrue(run.update_evidence("click", **completed)["changed"])
            self.assertEqual(1, applied.count("observation_recorded"))
            self.assertEqual(1, applied.count("action_selected"))
            applied.clear()
            with patch("testar_state_model_asm.state_model_data.preview_abstraction") as abstraction:
                self.assertFalse(run.update_evidence("same-click", **completed)["changed"])
                abstraction.assert_not_called()
            self.assertEqual([], applied)
        self.assertEqual(AsmRun(data["configuration"], **completed).model, run.model)

    def test_accepted_widgets_are_not_converted_again_for_the_next_click(self):
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"])
        completed = data["evidence_sets"]["completed"]
        converted_observations = []

        def count_conversion(value):
            # Count states entering conversion, rather than relying on elapsed
            # time: only the new observation should carry widgets through it.
            observations = value["sources"][0]["evidence"]["observations"]
            converted_observations.append(sum(item is not None for item in observations))
            return to_data(value)

        with patch("testar_state_model_asm.updates.to_data", side_effect=count_conversion):
            run.update_evidence("click", **completed)
            run.update_evidence("same-click", **completed)
        self.assertEqual([1, 0], converted_observations)

    def test_new_data_keeps_the_original_json_depth_limit(self):
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"])
        invalid = deepcopy(data["evidence_sets"]["completed"])
        value = "value"
        for _ in range(60):
            value = [value]
        state = invalid["sources"][0]["evidence"]["observations"][-1]["recorded_state"]
        state["properties"]["example.deep"] = value
        with self.assertRaises(AsmError):
            run.update_evidence("too-deep", **invalid)
        self.assertEqual(0, run.asm_data_revision)

    def test_a_failed_update_does_not_damage_the_accepted_lifecycle_or_consume_its_id(self):
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"])
        completed = deepcopy(data["evidence_sets"]["completed"])
        invalid = deepcopy(completed)
        # The result points outside this trace. Reject it, then accept the valid
        # click under the same update ID, using the original history.
        invalid["sources"][0]["evidence"]["executions"][0]["resultingObservation"] = "missing"
        before = run.model
        with self.assertRaises(AsmError):
            run.update_evidence("click", **invalid)
        self.assertEqual(before, run.model)
        self.assertEqual(0, run.asm_data_revision)
        self.assertTrue(run.update_evidence("click", **completed)["changed"])
        self.assertEqual(AsmRun(data["configuration"], **completed).model, run.model)

    def test_an_invalid_new_widget_is_still_rejected(self):
        data, _ = case("execution-lifecycle")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["initial"])
        invalid = deepcopy(data["evidence_sets"]["completed"])
        new_state = invalid["sources"][0]["evidence"]["observations"][-1]["recorded_state"]
        new_state["widgets"][0]["parent"] = "unknown-widget"
        with self.assertRaises(AsmError):
            run.update_evidence("invalid-widget", **invalid)
        self.assertEqual(0, run.asm_data_revision)

    def test_a_recorded_state_id_cannot_be_reused_for_different_widgets(self):
        data, _ = case("execution-lifecycle")
        initial = data["evidence_sets"]["initial"]
        run = AsmRun(data["configuration"], **initial)
        invalid = deepcopy(data["evidence_sets"]["completed"])
        old_state = initial["sources"][0]["evidence"]["observations"][0]["recorded_state"]
        new_state = invalid["sources"][0]["evidence"]["observations"][-1]["recorded_state"]
        new_state["id"] = old_state["id"]
        new_state["widgets"][0]["properties"]["ui.name"] = "Different widget"
        with self.assertRaises(AsmError):
            run.update_evidence("changed-state-id", **invalid)
        self.assertEqual(0, run.asm_data_revision)

    def test_an_execution_status_cannot_change_without_its_supporting_events(self):
        data, _ = case("execution-lifecycle")
        pending = deepcopy(data["evidence_sets"]["pending"])
        run = AsmRun(data["configuration"], **pending)
        # Changing a status alone does not add an event to the lifecycle. The
        # cached validation must still reject this unsupported completion.
        pending["sources"][0]["evidence"]["executions"][0]["status"] = "completed"
        with self.assertRaises(AsmError):
            run.update_evidence("unsupported-status", **pending)
        self.assertEqual(0, run.asm_data_revision)

    def test_inserting_an_earlier_observation_uses_full_validation(self):
        data, _ = case("empty-start")
        snapshot = deepcopy(data["evidence_sets"]["observed"])
        observations = snapshot["sources"][0]["evidence"]["observations"]
        third = deepcopy(observations[0])
        third.update(id="third", observation_index=3)
        third["recorded_state"]["id"] = "third-state"
        observations.append(third)
        run = AsmRun(data["configuration"], **snapshot)
        second = deepcopy(third)
        second.update(id="second", observation_index=2)
        second["recorded_state"]["id"] = "second-state"
        observations.append(second)
        self.assertTrue(run.update_evidence("earlier-state", **snapshot)["changed"])
        self.assertEqual(AsmRun(data["configuration"], **snapshot).model, run.model)

    def test_retry_fingerprints_preserve_numeric_equivalence_and_collection_order(self):
        data, _ = case("empty-start")
        snapshot = deepcopy(data["evidence_sets"]["observed"])
        properties = snapshot["sources"][0]["evidence"]["observations"][0]["recorded_state"]["properties"]
        properties["example.number"] = 1
        run = AsmRun(data["configuration"])
        run.update_evidence("observed", **snapshot)
        # A retry may reorder collections and use a decimal spelling of 1.
        properties["example.number"] = Decimal("1.00")
        reordered = reversed_case({"evidence_sets": {"same": snapshot}, "steps": []})["evidence_sets"]["same"]
        self.assertFalse(run.update_evidence("observed", **reordered)["changed"])
        properties["example.number"] = True
        with self.assertRaises(AsmError) as error:
            run.update_evidence("observed", **snapshot)
        self.assertEqual("conflicting_update", error.exception.code)
        # Retain only a digest for each accepted update, rather than another
        # complete copy of all states and widgets. This assertion bounds storage.
        self.assertEqual(32, len(run._updates["observed"]))

    def test_mutating_caller_data_does_not_change_accepted_states(self):
        data, _ = case("empty-start")
        snapshot = deepcopy(data["evidence_sets"]["observed"])
        run = AsmRun(data["configuration"])
        run.update_evidence("observed", **snapshot)
        before = run.model
        snapshot["sources"][0]["evidence"]["observations"][0]["recorded_state"]["widgets"].clear()
        self.assertEqual(before, run.model)
        with self.assertRaises(AsmError) as error:
            run.update_evidence("changed-old-state", **snapshot)
        self.assertEqual("conflicting_evidence", error.exception.code)

    def test_an_unchanged_event_plan_cannot_hide_an_orphan_observation(self):
        data, _ = case("empty-start")
        snapshot = deepcopy(data["evidence_sets"]["observed"])
        run = AsmRun(data["configuration"], **snapshot)
        orphan = deepcopy(snapshot["sources"][0]["evidence"]["observations"][0])
        orphan.update(id="orphan", test_sequence="missing")
        snapshot["sources"][0]["evidence"]["observations"].append(orphan)
        with self.assertRaises(AsmError):
            run.update_evidence("orphan", **snapshot)
        self.assertEqual(0, run.asm_data_revision)
