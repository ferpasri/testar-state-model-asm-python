"""Shared fixtures are downloaded from the pinned specs release, never copied into this repository."""

from copy import deepcopy
import json
import hashlib
from pathlib import Path

from testar_state_model_asm import AsmRun, AsmError

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / ".cache/specs/conformance"


def read(path):
    marker = ROOT / ".cache/specs/files.json"
    if not marker.exists():
        raise OSError("Run python tools/prepare_conformance.py before testing.")
    hashes = json.loads(marker.read_text(encoding="utf-8"))["files"]
    name = path.relative_to(ROOT / ".cache/specs").as_posix()
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != hashes.get(name):
        raise ValueError("Cached conformance file changed: " + name)
    return json.loads(content.decode("utf-8"))


def case(name):
    path = CASES / "cases" / name
    return read(path / "input.json"), read(path / "expected.json")


class RandomChoices:
    def __init__(self):
        self.draws = []

    def set(self, draws):
        if self.draws:
            raise AssertionError("The previous random choices were not consumed.")
        self.draws = deepcopy(draws)

    def __call__(self, size):
        if not self.draws:
            raise AssertionError("Unexpected random draw.")
        draw = self.draws.pop(0)
        if draw["size"] != size:
            raise AssertionError(f"Expected random size {draw['size']}, got {size}.")
        return draw["index"]


def run_case(test, data, expected):
    original = deepcopy(data)
    random = RandomChoices()
    initial = data["evidence_sets"][data["initial_evidence"]]
    try:
        run = AsmRun(data["configuration"], **initial, choose_index=random)
        initialized = {"result_type": "ready", "asm_data_revision": 0}
    except AsmError as error:
        initialized = {"result_type": "error", "asm_data_revision": 0, "code": error.code}
        test.assertEqual(expected["initialization"], initialized)
        return
    test.assertEqual(expected["initialization"], initialized)
    for index, (step, wanted) in enumerate(zip(data["steps"], expected["responses"])):
        arguments = {k: v for k, v in step.items() if k not in ("operation", "random_choices", "evidence_set")}
        if "evidence_set" in step:
            arguments.update(data["evidence_sets"][step["evidence_set"]])
        random.set(step.get("random_choices", []))
        before = run.model
        revision = run.asm_data_revision
        try:
            actual = getattr(run, step["operation"])(**arguments)
            if actual["result_type"] in ("selected", "no_action"):
                test.assertTrue(actual.pop("message"))
        except AsmError as error:
            actual = {"result_type": "error", "asm_data_revision": run.asm_data_revision, "code": error.code}
            test.assertEqual(before, run.model, f"Rejected step {index} changed model.")
            test.assertEqual(revision, run.asm_data_revision)
        test.assertEqual(wanted, actual, f"Step {index}: {step['operation']}")
        test.assertFalse(random.draws, "Expected random choices were not consumed.")
    test.assertEqual(original, data, "Caller-owned evidence was modified.")


def reversed_case(data):
    result = deepcopy(data)
    for evidence in result["evidence_sets"].values():
        evidence["sources"].reverse()
        evidence["selected_runs"].reverse()
        for source in evidence["sources"]:
            for records in source["evidence"].values():
                records.reverse()
    for step in result["steps"]:
        if step["operation"] == "set_context":
            step["context"]["choices"].reverse()
    return result
