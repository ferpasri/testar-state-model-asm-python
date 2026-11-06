"""Unvisited ASM uses trace history to try new actions here or navigate toward them."""

from collections import deque
from copy import deepcopy

from .action_selector import ActionSelector
from .errors import AsmError
from .state_model_data import ref_key


class UnvisitedActionSelector(ActionSelector):
    """Prefer local unvisited actions, then the nearest state with known unvisited actions."""

    def select(self, asm):
        """Try an unvisited action here before navigating to another modeled state."""
        rankings = self.rankings(use_observation_local_counts=asm.get("unmatched_choices") == "isolated")
        eligible = [item for item in rankings if item["eligible"]]
        if not eligible:
            return {"reason": "no_comparable_choices", "rankings": rankings}
        # A matching execution means the action was tried, including an execution error.
        local = [item for item in eligible if item["recorded_executions"] == 0]
        if local:
            return {"choice_id": self.choose(local)["choice_id"], "reason": "local_unvisited_action", "rankings": rankings}
        return self.navigate(rankings)

    def navigate(self, rankings):
        """Find the nearest state with unvisited actions and select an available first step toward it."""
        unvisited = {}
        for action in self.actions:
            if action["sourceNode"] in self.representatives and action["availability"] and not action["executions"] and not action["ambiguous"]:
                unvisited[action["sourceNode"]] = unvisited.get(action["sourceNode"], 0) + 1
        if not unvisited:
            return {"reason": "no_unvisited_actions", "rankings": rankings}
        current = self.nodes.get(ref_key(self.context["observation"], "observation"))
        allowed = set(self.choice_actions.values())
        edges = []
        # Only a currently available action can supply the route's first step.
        for edge in self.state_model_data.model["edges"]:
            if edge["kind"] == "execution" and (edge["source"] != current or edge["abstractAction"] in allowed):
                copy = deepcopy(edge)
                copy["representative"] = min(edge["executions"], key=lambda r: ref_key(r, "execution"))
                edges.append(copy)
        queue = deque([(current, [], {current})])
        routes = []
        examined = 0
        shortest = None
        # Examine one transition at a time, so nearer states are considered first.
        while queue:
            node, path, visited = queue.popleft()
            examined += 1
            if examined > 10000:
                raise AsmError("processing_limit", "Shortest route search exceeded 10000 alternatives.")
            if shortest is not None and len(path) > shortest:
                break
            if node != current and unvisited.get(node, 0):
                shortest = len(path)
                routes.append((node, path))
                continue
            for edge in edges:
                if edge["source"] == node and edge["target"] not in visited:
                    queue.append((edge["target"], path + [edge], visited | {edge["target"]}))
        if not routes:
            return {"reason": "no_reachable_unvisited_actions", "rankings": rankings}
        # Distance is tied; prefer more distinct unvisited actions.
        most = max(unvisited[node] for node, path in routes)
        targets = sorted({node for node, path in routes if unvisited[node] == most},
                         key=lambda node: ref_key(self.representatives[node], "observation"))
        target = self.choose(targets)
        paths = sorted([path for node, path in routes if node == target],
                       key=lambda path: tuple(ref_key(edge["representative"], "execution") for edge in path))
        path = self.choose(paths)
        choices = sorted(choice for choice, action in self.choice_actions.items() if action == path[0]["abstractAction"])
        return {
            "choice_id": self.choose(choices), "reason": "navigate_to_unvisited_actions", "rankings": rankings,
            "navigation": {"target_state": self.representatives[target], "distance": len(path),
                           "unvisited_actions": most,
                           "supporting_executions": [edge["representative"] for edge in path]}
        }
