"""An ASM run owns selection, controller limits, and execution coordination."""

from copy import deepcopy
import random
from functools import wraps
from threading import RLock

from testar_abstraction import parse_abstraction_rules
from testar_evidence.codec import dumps, to_data

from .errors import AsmError, require
from .state_model_data import StateModelData, PENDING, ref_key
from .updates import PreparedSnapshot, same
from .action_selector import ActionSelector


def _serialized(operation):
    @wraps(operation)
    def call(self, *arguments, **options):
        with self._lock:
            return operation(self, *arguments, **options)
    return call


class AsmRun:
    """Control one exploration run and update its model as new evidence arrives.

    Select actions through the configured ASM, switch ASMs, and enforce action
    limits. The exploration driver captures the GUI and performs the actions.
    """

    def __init__(self, configuration, sources=(), selected_runs=(), *, choose_index=None):
        """Check the ASM configuration and build the initial model from the selected trace runs."""
        self._lock = RLock()
        try:
            self._configuration = to_data(configuration)
            require(set(configuration).issubset({"abstraction", "asms", "total_action_limit"}),
                    "invalid_configuration", "Unknown configuration option.")
            self._rules = parse_abstraction_rules(dumps(configuration["abstraction"]))
            require("actions" in self._rules, "invalid_configuration", "Action abstraction is required.")
            asms = configuration["asms"]
            require(isinstance(asms, list) and bool(asms), "invalid_configuration", "Configure at least one ASM.")
            for asm in asms:
                require(asm.get("name") in ("unvisited", "random"), "invalid_configuration", "Unknown ASM.")
                require(type(asm.get("action_limit")) is int and asm["action_limit"] > 0,
                        "invalid_configuration", "An ASM action limit must be positive.")
                allowed = {"name", "action_limit", "unmatched_choices"} if asm["name"] == "unvisited" else {"name", "action_limit"}
                require(set(asm).issubset(allowed) and asm.get("unmatched_choices", "exclude") in ("exclude", "isolated"),
                        "invalid_configuration", "Unsupported ASM options.")
            if "total_action_limit" in configuration:
                require(type(configuration["total_action_limit"]) is int and configuration["total_action_limit"] > 0,
                        "invalid_configuration", "The overall action limit must be positive.")
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, AsmError):
                raise
            raise AsmError("invalid_configuration", str(error)) from error
        self._state_model_data = StateModelData(list(sources), list(selected_runs), self._rules)
        self.asm_data_revision = 0
        self._choose_index = choose_index or random.randrange
        self._context = None
        self._contexts = {}
        self._decision = None
        self._cancelled = False
        self._active_execution_reference = None
        # Keep each selected action and source observation while execution is outstanding.
        # Retain completed contexts too, so repeated reports do not count an action twice.
        self._execution_contexts = {}
        self._updates = {}
        self._asm_index = 0
        self._entry_count = 0
        self._total_count = 0
        self._counted = set()

    @property
    @_serialized
    def model(self):
        """Return a copy of the abstract model that callers can inspect without changing this run."""
        return deepcopy(self._state_model_data.model)

    def _result(self, kind, **fields):
        """Attach the current ASM data revision so callers know which accepted evidence a response uses."""
        return {"result_type": kind, "asm_data_revision": self.asm_data_revision, **deepcopy(fields)}

    def _retire_context(self):
        """Discard the old observation and selection when they can no longer guide the next action."""
        self._context = None
        self._decision = None

    @_serialized
    def set_context(self, context):
        """Accept the current observation and eligible available actions after checking their evidence."""
        require(self._active_execution_reference is None, "execution_in_progress", "Report the outstanding execution first.")
        candidate = deepcopy(context)
        identifier = candidate.get("context_id")
        require(isinstance(identifier, str) and bool(identifier.strip()), "invalid_context", "A context ID is required.")
        if identifier in self._contexts:
            require(same(self._contexts[identifier], candidate),
                    "context_conflict", "The context identifier has different content.")
            require(self._context is not None and identifier == self._context["context_id"],
                    "stale_context", "This context has been retired.")
            return self._result("context_ready", context_id=identifier)
        try:
            reference = ref_key(candidate["observation"], "observation")
            observation = self._state_model_data.observations.get(reference)
            require(observation is not None, "invalid_context", "Unknown observation.")
            sequence = self._state_model_data.sequences[(reference[0], observation["test_sequence"])]
            require(sequence["status"] == "running" and self._state_model_data.run_records[reference[:2]]["status"] == "running",
                    "invalid_context", "The observation belongs to a finished sequence or run.")
            latest = max(o["observation_index"] for key, o in self._state_model_data.observations.items()
                         if key[:2] == reference[:2] and o["test_sequence"] == observation["test_sequence"])
            require(observation["observation_index"] == latest, "invalid_context", "A newer observation exists.")
            require(not any(r["status"] in PENDING and r["execution"]["test_sequence"] == observation["test_sequence"]
                            for key, r in self._state_model_data.executions.items() if key[:2] == reference[:2]),
                    "execution_in_progress", "Resolve the sequence's pending execution.")
            choices = candidate["choices"]
            require(len({c["choice_id"] for c in choices}) == len(choices), "invalid_context", "Duplicate choice identifier.")
            for choice in choices:
                require(isinstance(choice["choice_id"], str) and bool(choice["choice_id"].strip()),
                        "invalid_context", "A choice identifier is required.")
                require({"action_description": choice["action_description"], "target_widget": choice["target_widget"]}
                        in observation["available_actions"], "invalid_context", "Choice was not reported at this observation.")
        except (KeyError, TypeError) as error:
            raise AsmError("invalid_context", str(error)) from error
        self._context = candidate
        self._contexts[identifier] = deepcopy(candidate)
        self._decision = None
        return self._result("context_ready", context_id=identifier)

    @_serialized
    def select(self):
        """Ask the active ASM for an action, switching ASMs after its limit or when it cannot select."""
        def no_action(reason):
            return self._result("no_action", reason=reason, rankings=[], asm_index=self._asm_index,
                                context_id=self._context["context_id"] if self._context else None, message=reason.replace("_", " "))
        if self._cancelled:
            return no_action("cancelled")
        if self._active_execution_reference is not None:
            return no_action("execution_in_progress")
        if self._context is None:
            return no_action("no_context")
        if self._total_count >= self._configuration.get("total_action_limit", float("inf")):
            return no_action("action_limit_reached")
        if not self._context["choices"]:
            return no_action("no_available_choices")
        # Repeated requests for the same observation return the same choice.
        if self._decision is not None:
            return deepcopy(self._decision)
        entries = self._configuration["asms"]
        start = self._asm_index
        index = start
        count = self._entry_count
        if count >= entries[index]["action_limit"]:
            index = (index + 1) % len(entries)
            count = 0
        fallback = None
        # Try each configured ASM once, so switching cannot continue indefinitely.
        for attempt in range(len(entries)):
            result = ActionSelector.for_asm(entries[index], self._state_model_data, self._context, self._choose_index).select(entries[index])
            if "choice_id" in result:
                self._asm_index = index
                self._entry_count = count
                self._decision = self._result("selected", context_id=self._context["context_id"],
                                              asm_index=index, message=result["reason"].replace("_", " "), **result)
                return deepcopy(self._decision)
            fallback = result if fallback is None else fallback
            index = (index + 1) % len(entries)
            count = 0
        result = fallback if len(entries) == 1 else {"reason": "no_applicable_asm", "rankings": fallback["rankings"]}
        return self._result("no_action", context_id=self._context["context_id"], asm_index=start,
                            message=result["reason"].replace("_", " "), **result)

    @_serialized
    def begin_execution(self, selection, execution):
        """Associate an execution reference with the last selection before the driver operates the GUI."""
        require(not self._cancelled, "cancelled", "Exploration was cancelled.")
        require(self._active_execution_reference is None, "execution_in_progress", "Another execution is outstanding.")
        require(self._context is not None and self._decision is not None and
                selection.get("context_id") == self._context["context_id"] and
                selection.get("asm_data_revision") == self.asm_data_revision,
                "stale_selection", "Select again using the current observation and state-model data.")
        require(selection.get("choice_id") == self._decision["choice_id"],
                "invalid_selection", "This is not the last selected choice.")
        key = ref_key(execution, "execution")
        require(key not in self._execution_contexts and key not in self._state_model_data.executions,
                "execution_reference_reused", "Execution identity has already been used.")
        require(key[:2] == ref_key(self._context["observation"], "observation")[:2],
                "execution_mismatch", "Execution must belong to the selected run.")
        choice = next(c for c in self._context["choices"] if c["choice_id"] == selection["choice_id"])
        self._execution_contexts[key] = {"reference": deepcopy(execution), "context": deepcopy(self._context),
                               "choice": deepcopy(choice), "invoked": None}
        self._active_execution_reference = key
        return self._result("execution_started", execution=execution, context_id=self._context["context_id"])

    def _prepare_update(self, update_id, sources, selected_runs):
        """Check incoming evidence without changing the run; repeated update IDs must retain their data."""
        require(isinstance(update_id, str) and bool(update_id.strip()), "invalid_evidence", "An update ID is required.")
        try:
            prepared = PreparedSnapshot(sources, selected_runs, self._state_model_data)
            previous = self._updates.get(update_id)
            require(previous is None or previous == prepared.fingerprint,
                    "conflicting_update", "Update identifier has different content.")
            candidate = self._state_model_data.extend_prepared(prepared, self._rules)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(error, AsmError):
                raise
            raise AsmError("invalid_evidence", str(error)) from error
        return candidate, candidate is not self._state_model_data, prepared.fingerprint

    def _accept_update(self, update_id, candidate, changed, fingerprint):
        """Install checked evidence and discard decisions or observations made outdated by the update."""
        self._updates[update_id] = fingerprint
        if changed:
            self._state_model_data = candidate
            self.asm_data_revision += 1
            self._decision = None
            if self._context is not None:
                reference = ref_key(self._context["observation"], "observation")
                current = candidate.observations[reference]
                sequence = current["test_sequence"]
                if (candidate.run_records[reference[:2]]["status"] != "running" or
                        candidate.sequences[(reference[0], sequence)]["status"] != "running"):
                    self._retire_context()
                    return
                if any(key[:2] == reference[:2] and o["test_sequence"] == sequence
                       and o["observation_index"] > current["observation_index"]
                       for key, o in candidate.observations.items()):
                    self._retire_context()

    @_serialized
    def update_evidence(self, update_id, sources, selected_runs):
        """Update the abstract model from recorded evidence, such as a newly captured state."""
        candidate, changed, fingerprint = self._prepare_update(update_id, sources, selected_runs)
        self._accept_update(update_id, candidate, changed, fingerprint)
        return self._result("evidence_updated", changed=changed)

    @_serialized
    def report_execution(self, execution, update_id, sources, selected_runs):
        """Match evidence to the selected action, update the model, and count that execution only once."""
        key = ref_key(execution, "execution")
        require(key in self._execution_contexts, "unknown_execution", "Execution was not begun through this ASM.")
        execution_context = self._execution_contexts[key]
        require(execution_context["invoked"] is not False, "unknown_execution", "This operation was never invoked.")
        candidate, changed, fingerprint = self._prepare_update(update_id, sources, selected_runs)
        record = candidate.executions.get(key)
        require(record is not None, "execution_mismatch", "The reported execution is missing.")
        choice = execution_context["choice"]
        original = execution_context["context"]["observation"]
        actual = record["execution"]
        require(actual["source_observation"] == original["observation"] and
                actual["action_description"] == choice["action_description"] and actual["target_widget"] == choice["target_widget"],
                "execution_mismatch", "Reported execution does not belong to the selected action.")
        self._accept_update(update_id, candidate, changed, fingerprint)
        execution_context["invoked"] = True
        # A later result report must not count the same execution a second time.
        if key not in self._counted:
            self._counted.add(key)
            self._entry_count += 1
            self._total_count += 1
        if record["status"] not in PENDING and self._active_execution_reference == key:
            self._active_execution_reference = None
            self._retire_context()
        return self._result("execution_updated", execution=execution, status=record["status"], changed=changed)

    @_serialized
    def report_not_invoked(self, execution):
        """Clear an action that never started without counting it; require a fresh observation next."""
        key = ref_key(execution, "execution")
        require(key in self._execution_contexts and self._active_execution_reference == key, "unknown_execution", "No such outstanding execution.")
        require(key not in self._state_model_data.executions, "execution_already_recorded", "Execution evidence already exists.")
        self._execution_contexts[key]["invoked"] = False
        self._active_execution_reference = None
        self._retire_context()
        return self._result("execution_not_invoked", execution=execution)

    @_serialized
    def _exploration_stop(self):
        """Check whether exploration must stop before asking the driver to capture the GUI again."""
        reason = ("cancelled" if self._cancelled else
                  "execution_in_progress" if self._active_execution_reference is not None else
                  "action_limit_reached" if self._total_count >= self._configuration.get("total_action_limit", float("inf"))
                  else None)
        if reason is None:
            return None
        return self._result("no_action", reason=reason, rankings=[], asm_index=self._asm_index,
                            context_id=self._context["context_id"] if self._context else None,
                            message=reason.replace("_", " "))

    def explore(self, driver, *, max_cycles=1000):
        """Repeat observation, action selection, execution, and model updates through the driver."""
        from .exploration import explore
        return explore(self, driver, max_cycles=max_cycles)

    @_serialized
    def cancel(self):
        """Prevent further selection; an action already started still needs its result reported."""
        self._cancelled = True
        return self._result("cancelled")
