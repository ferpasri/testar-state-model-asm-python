"""Boundary behavior beyond the selection examples."""

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from testar_evidence import EvidenceEvent, JsonlStore, Manifest
from testar_state_model_asm import AsmError, AsmRun, read_source
from tests.support import case


class InputBoundaryTests(unittest.TestCase):
    def test_equivalent_numeric_values_are_an_unchanged_update(self):
        data, _ = case("empty-start")
        snapshot = deepcopy(data["evidence_sets"]["observed"])
        observation = snapshot["sources"][0]["evidence"]["observations"][0]
        observation["recorded_state"]["properties"]["example.number"] = 1
        run = AsmRun(data["configuration"], **snapshot)
        same = deepcopy(snapshot)
        same["sources"][0]["evidence"]["observations"][0]["recorded_state"]["properties"]["example.number"] = Decimal("1.0")
        self.assertFalse(run.update_evidence("same", **same)["changed"])
        self.assertEqual(0, run.asm_data_revision)

    def test_a_duplicate_source_update_is_rejected_atomically(self):
        data, _ = case("empty-start")
        snapshot = data["evidence_sets"]["observed"]
        run = AsmRun(data["configuration"], **snapshot)
        before = run.model
        with self.assertRaises(AsmError) as error:
            run.update_evidence("duplicate", snapshot["sources"] * 2, snapshot["selected_runs"])
        self.assertEqual("invalid_evidence", error.exception.code)
        self.assertEqual(before, run.model)
        self.assertEqual(0, run.asm_data_revision)

    def test_returned_models_and_original_configuration_are_independent(self):
        data, _ = case("empty-start")
        run = AsmRun(data["configuration"], **data["evidence_sets"]["observed"], choose_index=lambda size: 0)
        data["configuration"]["asms"][0]["name"] = "invalid"
        returned = run.model
        returned["nodes"].clear()
        run.set_context(data["steps"][2]["context"])
        self.assertEqual("selected", run.select()["result_type"])
        self.assertTrue(run.model["nodes"])

    def test_a_real_evidence_trace_can_be_loaded_for_selection(self):
        data, _ = case("empty-start")
        original = data["evidence_sets"]["observed"]["sources"][0]["evidence"]
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace"
            with JsonlStore.create(path, Manifest("trace")) as store:
                events = [
                    ("test_run_started", {k: v for k, v in original["runs"][0].items() if k not in ("status", "reason")}),
                    ("test_sequence_started", {k: v for k, v in original["sequences"][0].items() if k not in ("status", "reason")}),
                    *[("action_described", action) for action in original["actions"]],
                    ("observation_recorded", original["observations"][0])
                ]
                for index, (kind, value) in enumerate(events):
                    store.record(EvidenceEvent(str(index), kind, value))
            source = read_source(path, "live")
            run = AsmRun(data["configuration"], [source], [{"source": "live", "run": "run"}], choose_index=lambda size: 0)
            run.set_context(data["steps"][2]["context"])
            self.assertEqual("local_unvisited_action", run.select()["reason"])

    def test_finishing_the_current_sequence_retires_its_selection_context(self):
        data, _ = case("empty-start")
        snapshot = deepcopy(data["evidence_sets"]["observed"])
        run = AsmRun(data["configuration"], **snapshot, choose_index=lambda size: 0)
        run.set_context(data["steps"][2]["context"])
        selected = run.select()
        snapshot["sources"][0]["evidence"]["sequences"][0].update(status="finished", reason="Sequence ended")
        run.update_evidence("finished", **snapshot)
        self.assertEqual("no_context", run.select()["reason"])
        with self.assertRaises(AsmError) as failure:
            run.begin_execution(selected, {"source": "live", "run": "run", "execution": "too-late"})
        self.assertEqual("stale_selection", failure.exception.code)
