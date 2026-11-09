"""Validate reconstructed sources and retain immutable, source-qualified facts."""

from testar_abstraction import preview_abstraction, build_abstract_model, AbstractionLimitError
from testar_evidence import ReconstructedEvidence, EvidenceEvent, Manifest, EvidenceError

from .errors import AsmError, require
from .updates import PreparedSnapshot, same

STATUSES = ("completed", "failed", "incomplete", "awaiting_execution_result", "awaiting_resulting_observation")
PENDING = set(STATUSES[3:])


def ref_key(reference, field):
    """Identify a record by its trace source, run, and record ID; IDs from different sources stay separate."""
    return reference["source"], reference["run"], reference[field]


def validate_source(source, previous=None):
    """Validate new events against an independent copy of the accepted lifecycle."""
    evidence = source["evidence"]
    # Reconstruct event order without copying the widgets held by each observation.
    events = []

    def apply(kind, data):
        events.append((kind, data))

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
    # Ordinary updates append events. Historical insertions use full validation.
    extends_history = previous is not None and len(events) >= len(previous.events) and all(
        before[0] == after[0] and same(before[1], after[1])
        for before, after in zip(previous.events, events))
    new_actions = [action for action in evidence["actions"]
                   if previous is None or action["id"] not in previous.action_ids]
    unchanged_history = extends_history and len(events) == len(previous.events) and not new_actions
    if unchanged_history:
        history = previous.history
    else:
        history = previous.history.snapshot() if extends_history else ReconstructedEvidence(Manifest(source["id"]))
    pending = events[len(previous.events):] if extends_history else events
    actions = new_actions if extends_history else evidence["actions"]
    for kind, data in [("action_described", action) for action in actions] + pending:
        history.apply(EvidenceEvent(str(history.event_count + 1), kind, data))
    # Summary checks still run when no event was appended: a changed status
    # or an observation outside all sequences must not bypass validation.
    actual = history.action_executions
    require(len(actual) == len(evidence["executions"]), "invalid_evidence", "Unresolved execution records.")
    for record in evidence["executions"]:
        require(actual[record["execution"]["id"]]["status"] == record["status"],
                "invalid_evidence", "Execution status does not match its lifecycle.")
    require(sum(kind == "observation_recorded" for kind, _ in events) == len(evidence["observations"]),
            "invalid_evidence", "Unresolved observation records.")
    require(sum(kind == "test_sequence_started" for kind, _ in events) == len(evidence["sequences"]),
            "invalid_evidence", "Unresolved test sequence records.")

    if unchanged_history:
        return previous
    return SourceValidation(history, events, {action["id"] for action in evidence["actions"]})


class SourceValidation:
    """Retain the checked lifecycle so the next GUI observation need not validate old states again."""

    def __init__(self, history, events, action_ids):
        self.history = history
        self.events = events
        self.action_ids = action_ids


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
                require(same(before, after), "conflicting_evidence", "An immutable record changed.")
            elif name == "executions":
                require(same(before["execution"], after["execution"]),
                        "conflicting_evidence", "Execution arguments changed.")
                for field in ("executionResult", "resultingObservation"):
                    require(before[field] is None or same(before[field], after[field]),
                            "conflicting_evidence", "An accepted execution result changed.")
                require(before["status"] in PENDING or before["status"] == after["status"],
                        "conflicting_evidence", "A final execution status changed.")
                require(before["status"] != "awaiting_resulting_observation" or
                        after["status"] != "awaiting_execution_result",
                        "conflicting_evidence", "Execution lifecycle regressed.")
            else:
                immutable_before = {k: v for k, v in before.items() if k not in ("status", "reason")}
                immutable_after = {k: v for k, v in after.items() if k not in ("status", "reason")}
                require(same(immutable_before, immutable_after),
                        "conflicting_evidence", "Run or sequence content changed.")
                require(before["status"] != "finished" or same(before, after),
                        "conflicting_evidence", "A finished lifecycle changed.")


class StateModelData:
    """Hold accepted recorded states, executions, and their derived abstract model.

    Abstraction groups matching states and actions across visits and trace sources.
    Updates retain the recorded facts already used by the ASM.
    """

    def __init__(self, sources, selected_runs, rules):
        """Validate traces, index the selected runs, and apply abstraction to build their model."""
        try:
            prepared = PreparedSnapshot(sources, selected_runs)
        except (EvidenceError, KeyError, TypeError, ValueError) as error:
            raise AsmError("invalid_evidence", str(error)) from error
        self._load(prepared, rules)

    def _load(self, prepared, rules, previous=None):
        """Build candidate data separately, so rejected updates cannot alter the accepted run."""
        try:
            self.snapshot = prepared.snapshot
            self.record_fingerprints = prepared.record_fingerprints
            selected_runs = self.snapshot["selected_runs"]
            self.sources = {source["id"]: source for source in self.snapshot["sources"]}
            require(len(self.sources) == len(self.snapshot["sources"]), "invalid_evidence", "Repeated source identity.")
            previous_validations = previous.validations if previous is not None else {}
            self.validations = {source["id"]: validate_source(source, previous_validations.get(source["id"]))
                                for source in self.sources.values()}
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

    def extend_prepared(self, prepared, rules):
        """Merge an owned update without copying or normalizing it a second time."""
        sources = prepared.snapshot["sources"]
        selected_runs = prepared.snapshot["selected_runs"]
        require(self.runs.issubset({(r["source"], r["run"]) for r in selected_runs}),
                "conflicting_evidence", "Selected runs cannot be removed.")
        require(len({source["id"] for source in sources}) == len(sources),
                "invalid_evidence", "Repeated source identity in update.")
        merged = dict(self.sources)
        for source in sources:
            if source["id"] in merged:
                check_extension(merged[source["id"]], source)
            merged[source["id"]] = source
        candidate = {"sources": sorted(merged.values(), key=lambda item: item["id"]), "selected_runs": selected_runs}
        if same(candidate, self.snapshot):
            return self
        prepared.snapshot = candidate
        prepared.record_fingerprints = {**self.record_fingerprints, **prepared.record_fingerprints}
        updated = object.__new__(StateModelData)
        updated._load(prepared, rules, self)
        return updated
