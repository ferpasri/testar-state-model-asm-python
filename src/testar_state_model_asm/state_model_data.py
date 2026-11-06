"""Validate reconstructed sources and retain immutable, source-qualified facts."""

from copy import deepcopy
from decimal import Decimal

from testar_abstraction import preview_abstraction, build_abstract_model, AbstractionLimitError
from testar_evidence import ReconstructedEvidence, EvidenceEvent, Manifest, EvidenceError
from testar_evidence.codec import to_data

from .errors import AsmError, require

STATUSES = ("completed", "failed", "incomplete", "awaiting_execution_result", "awaiting_resulting_observation")
PENDING = set(STATUSES[3:])


def ref_key(reference, field):
    """Identify a record by its trace source, run, and record ID; IDs from different sources stay separate."""
    return reference["source"], reference["run"], reference[field]


def canonical(value):
    """Compare JSON numbers by value, while retaining array order and boolean types."""
    if isinstance(value, dict):
        return ("object", tuple((key, canonical(value[key])) for key in sorted(value)))
    if isinstance(value, list):
        return ("array", tuple(canonical(item) for item in value))
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return (type(value).__name__, value)
    number = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    sign, digits, exponent = number.as_tuple()
    digits = list(digits)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return ("number", sign if digits else 0, tuple(digits), exponent if digits else 0)


def normalized_snapshot(snapshot):
    """Order copied records consistently so input reordering does not look like new evidence."""
    value = deepcopy(snapshot)
    for source in value["sources"]:
        for collection in source["evidence"].values():
            collection.sort(key=lambda item: item.get("id", item.get("execution", {}).get("id", "")))
    value["sources"].sort(key=lambda item: item["id"])
    value["selected_runs"].sort(key=lambda item: (item["source"], item["run"]))
    return value


def validate_source(source):
    """Use the evidence library to check whether the reconstructed lifecycle is possible."""
    evidence = source["evidence"]
    # These temporary validation events are never written to a trace.
    history = ReconstructedEvidence(Manifest(source["id"]))
    counter = 0

    def apply(kind, data):
        nonlocal counter
        counter += 1
        history.apply(EvidenceEvent(str(counter), kind, data))

    for action in evidence["actions"]:
        apply("action_described", action)
    for run in sorted(evidence["runs"], key=lambda item: item["id"]):
        apply("test_run_started", {k: v for k, v in run.items() if k not in ("status", "reason")})
        sequences = sorted((s for s in evidence["sequences"] if s["test_run"] == run["id"]),
                           key=lambda item: item["sequence_index"])
        for sequence in sequences:
            apply("test_sequence_started", {k: v for k, v in sequence.items() if k not in ("status", "reason")})
            observations = sorted((o for o in evidence["observations"] if o["test_sequence"] == sequence["id"]),
                                  key=lambda item: item["observation_index"])
            records = [r for r in evidence["executions"] if r["execution"]["test_sequence"] == sequence["id"]]
            for observation in observations:
                apply("observation_recorded", observation)
                for record in records:
                    if record["resultingObservation"] == observation["id"]:
                        apply("transition_recorded", {"action_execution": record["execution"]["id"],
                                                      "resulting_observation": observation["id"]})
                for record in records:
                    if record["execution"]["source_observation"] == observation["id"]:
                        apply("action_selected", record["execution"])
                        if record["executionResult"] is not None:
                            apply("action_execution_reported", record["executionResult"])
            if sequence["status"] == "finished":
                apply("test_sequence_finished", {"test_sequence": sequence["id"], "reason": sequence["reason"] or "Reconstructed sequence finished"})
            else:
                require(sequence["status"] == "running", "invalid_evidence", "Unknown sequence status.")
        if run["status"] == "finished":
            apply("test_run_finished", {"test_run": run["id"], "reason": run["reason"] or "Reconstructed run finished"})
        else:
            require(run["status"] == "running", "invalid_evidence", "Unknown run status.")
    actual = history.action_executions
    require(len(actual) == len(evidence["executions"]), "invalid_evidence", "Unresolved execution records.")
    for record in evidence["executions"]:
        require(actual[record["execution"]["id"]]["status"] == record["status"],
                "invalid_evidence", "Execution status does not match its lifecycle.")
    require(len(history.observations) == len(evidence["observations"]),
            "invalid_evidence", "Unresolved observation records.")
    require(len(history.test_sequences) == len(evidence["sequences"]),
            "invalid_evidence", "Unresolved test sequence records.")


def check_extension(previous, incoming):
    """Allow additional evidence and execution progress while preserving already accepted facts."""
    for name in ("runs", "sequences", "observations", "actions", "executions"):
        old = previous["evidence"][name]
        new = incoming["evidence"][name]
        identifier = lambda item: item["execution"]["id"] if name == "executions" else item["id"]
        indexed = {identifier(item): item for item in new}
        require(len(indexed) == len(new), "conflicting_evidence", "Repeated record identity.")
        for before in old:
            after = indexed.get(identifier(before))
            require(after is not None, "conflicting_evidence", "An accepted record was removed.")
            if name in ("observations", "actions"):
                require(canonical(before) == canonical(after), "conflicting_evidence", "An immutable record changed.")
            elif name == "executions":
                require(canonical(before["execution"]) == canonical(after["execution"]),
                        "conflicting_evidence", "Execution arguments changed.")
                for field in ("executionResult", "resultingObservation"):
                    require(before[field] is None or canonical(before[field]) == canonical(after[field]),
                            "conflicting_evidence", "An accepted execution result changed.")
                require(before["status"] in PENDING or before["status"] == after["status"],
                        "conflicting_evidence", "A final execution status changed.")
                require(before["status"] != "awaiting_resulting_observation" or
                        after["status"] != "awaiting_execution_result",
                        "conflicting_evidence", "Execution lifecycle regressed.")
            else:
                immutable_before = {k: v for k, v in before.items() if k not in ("status", "reason")}
                immutable_after = {k: v for k, v in after.items() if k not in ("status", "reason")}
                require(canonical(immutable_before) == canonical(immutable_after),
                        "conflicting_evidence", "Run or sequence content changed.")
                require(before["status"] != "finished" or canonical(before) == canonical(after),
                        "conflicting_evidence", "A finished lifecycle changed.")


class StateModelData:
    """Hold accepted recorded states, executions, and their derived abstract model.

    Abstraction groups matching states and actions across visits and trace sources.
    Updates retain the recorded facts already used by the ASM.
    """

    def __init__(self, sources, selected_runs, rules):
        """Validate traces, index the selected runs, and apply abstraction to build their model."""
        try:
            snapshot = to_data({"sources": sources, "selected_runs": selected_runs})
            self.snapshot = normalized_snapshot(snapshot)
            self.sources = {source["id"]: source for source in self.snapshot["sources"]}
            require(len(self.sources) == len(sources), "invalid_evidence", "Repeated source identity.")
            for source in self.sources.values():
                validate_source(source)
            self.runs = set()
            self.observations = {}
            self.executions = {}
            self.sequences = {}
            self.run_records = {}
            for reference in selected_runs:
                key = reference["source"], reference["run"]
                require(key not in self.runs, "invalid_evidence", "Repeated selected run.")
                self.runs.add(key)
                source = self.sources.get(reference["source"])
                require(source is not None, "invalid_evidence", "Unknown selected source.")
                run = next((r for r in source["evidence"]["runs"] if r["id"] == reference["run"]), None)
                require(run is not None, "invalid_evidence", "Unknown selected run.")
                self.run_records[key] = run
                evidence = source["evidence"]
                for sequence in evidence["sequences"]:
                    if sequence["test_run"] == key[1]:
                        self.sequences[(key[0], sequence["id"])] = sequence
                        for observation in evidence["observations"]:
                            if observation["test_sequence"] == sequence["id"]:
                                self.observations[(*key, observation["id"])] = observation
                        for record in evidence["executions"]:
                            if record["execution"]["test_sequence"] == sequence["id"]:
                                self.executions[(*key, record["execution"]["id"])] = record
            self.preview = preview_abstraction(list(self.sources.values()), rules, selected_runs)
            self.model = build_abstract_model(self.preview, list(self.sources.values()))
        except AbstractionLimitError as error:
            raise AsmError("processing_limit", str(error)) from error
        except (EvidenceError, KeyError, TypeError, ValueError) as error:
            if isinstance(error, AsmError):
                raise
            raise AsmError("invalid_evidence", str(error)) from error

    def extended(self, sources, selected_runs, rules):
        """Build an updated model from additional evidence, reusing this instance when nothing changed."""
        require(self.runs.issubset({(r["source"], r["run"]) for r in selected_runs}),
                "conflicting_evidence", "Selected runs cannot be removed.")
        require(len({source["id"] for source in sources}) == len(sources),
                "invalid_evidence", "Repeated source identity in update.")
        merged = deepcopy(self.sources)
        for source in sources:
            if source["id"] in merged:
                check_extension(merged[source["id"]], source)
            merged[source["id"]] = deepcopy(source)
        candidate = normalized_snapshot({"sources": list(merged.values()), "selected_runs": selected_runs})
        if canonical(candidate) == canonical(self.snapshot):
            return self
        return StateModelData(candidate["sources"], candidate["selected_runs"], rules)
