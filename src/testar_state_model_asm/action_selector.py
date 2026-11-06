"""Shared matching and counting for concrete action selection mechanisms."""

from abc import ABC, abstractmethod

from .errors import require
from .state_model_data import STATUSES, ref_key


class ActionSelector(ABC):
    """Connect current actions to matching trace records before an ASM makes its choice."""

    def __init__(self, state_model_data, context, choose_index):
        self.state_model_data = state_model_data
        self.context = context
        self.choose_index = choose_index
        self.actions = state_model_data.model["actionEvidence"]["actions"]
        self.nodes = {ref_key(member, "observation"): node["id"]
                      for node in state_model_data.model["nodes"] if node.get("groupId") for member in node["members"]}
        self.representatives = {node["id"]: min(node["members"], key=lambda r: ref_key(r, "observation"))
                                for node in state_model_data.model["nodes"]}
        self.choice_actions = {}

    def choose(self, items):
        """Choose equally among suitable alternatives, skipping a random draw when only one remains."""
        require(bool(items), "processing_limit", "No selection alternatives.")
        index = 0 if len(items) == 1 else self.choose_index(len(items))
        require(type(index) is int and 0 <= index < len(items), "invalid_configuration",
                "The random provider returned an invalid index.")
        return items[index]

    def rankings(self, *, include_unmatched_choices=False, use_observation_local_counts=False):
        """Explain prior executions; an unknown match retains an unknown count."""
        rankings = []
        for choice in sorted(self.context["choices"], key=lambda item: item["choice_id"]):
            reference = self.context["observation"]
            candidates = []
            for action in self.actions:
                for available in action["availability"]:
                    binding = available["reference"]
                    if (ref_key(binding, "observation") == ref_key(reference, "observation")
                            and binding["actionDescription"] == choice["action_description"]
                            and binding["targetWidget"] == choice["target_widget"]):
                        candidates.append(action)
                        break
            action = candidates[0] if len(candidates) == 1 else None
            match = ("unmatched_state" if ref_key(reference, "observation") not in self.nodes else
                     "unmatched_action" if action is None else
                     "ambiguous_action" if action["ambiguous"] else "matched")
            references = []
            scope = None
            if match == "matched":
                self.choice_actions[choice["choice_id"]] = action["id"]
                scope = "abstract_action"
                references = [record["reference"] for record in action["executions"]]
            elif use_observation_local_counts:
                scope = "recorded_binding"
                for key, record in self.state_model_data.executions.items():
                    execution = record["execution"]
                    if (key[:2] == ref_key(reference, "observation")[:2]
                            and execution["source_observation"] == reference["observation"]
                            and execution["action_description"] == choice["action_description"]
                            and execution["target_widget"] == choice["target_widget"]):
                        references.append({"source": key[0], "run": key[1], "execution": key[2]})
            references = sorted(references, key=lambda r: ref_key(r, "execution"))
            counts = None
            if scope is not None:
                counts = dict.fromkeys(STATUSES, 0)
                for item in references:
                    counts[self.state_model_data.executions[ref_key(item, "execution")]["status"]] += 1
            rankings.append({
                "choice_id": choice["choice_id"], "match": match,
                "eligible": scope is not None or include_unmatched_choices, "count_scope": scope,
                "recorded_executions": len(references) if scope else None,
                "status_counts": counts, "supporting_executions": references
            })
        return rankings

    @staticmethod
    def for_asm(asm, state_model_data, context, choose_index):
        """Create the selector named in the run configuration."""
        # Import here because each concrete selector extends this common base.
        from .random_action_selector import RandomActionSelector
        from .unvisited_action_selector import UnvisitedActionSelector

        selectors = {"random": RandomActionSelector, "unvisited": UnvisitedActionSelector}
        selector = selectors.get(asm["name"])
        require(selector is not None, "invalid_configuration", "Unknown ASM.")
        return selector(state_model_data, context, choose_index)

    @abstractmethod
    def select(self, asm):
        """Apply this ASM's action selection rule."""
