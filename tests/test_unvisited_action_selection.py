"""Small examples explain local unvisited selection and navigation using existing traces."""

import unittest

from testar_state_model_asm import AsmRun
from tests.support import case


def select_from(name):
    """Load a shared case, build its model, set the current observation, and select.

    The returned dictionary identifies an eligible action and explains the matching
    counts and any navigation route. This helper never executes browser actions.
    Ties use index zero so these examples have repeatable results.
    """
    data, _ = case(name)
    run = AsmRun(data["configuration"], **data["evidence_sets"][data["initial_evidence"]],
                 choose_index=lambda size: 0)
    run.set_context(data["steps"][0]["context"])
    return run.select()


class UnvisitedActionSelectionTests(unittest.TestCase):
    def test_try_a_new_action_here_before_navigating_elsewhere(self):
        # This shared case starts at state A with three eligible actions: to-b, to-c, and new-a.
        # Recorded executions already connect A to B and C. Those states have unvisited actions,
        # but new-a has never been executed in A. The configured ASM is Unvisited.
        # The helper builds the model from these records and asks for one action; it returns
        # a JSON decision, not an execution result.
        selected = select_from("local-unvisited-before-navigation")

        # The decision must identify new-a: try an unvisited action here before navigating away.
        # The reason confirms that this is local selection rather than a navigation step.
        self.assertEqual("new-a", selected["choice_id"])
        self.assertEqual("local_unvisited_action", selected["reason"])

    def test_nearer_state_wins_even_when_a_farther_state_has_more_new_actions(self):
        # This shared case starts at A, where all current actions have already been tried.
        # The trace contains A -> B and A -> C -> D. B has one unvisited action; D has three.
        # The helper builds that model and asks the Unvisited ASM for its next action.
        selected = select_from("navigation-nearest-state")

        # Choose to-b, the first step toward B. Distance 1 counts the recorded transition
        # needed to reach B; unvisited_actions 1 describes the opportunity waiting there.
        self.assertEqual("to-b", selected["choice_id"])
        self.assertEqual(1, selected["navigation"]["distance"])
        self.assertEqual(1, selected["navigation"]["unvisited_actions"])

    def test_equally_near_states_prefer_more_unvisited_abstract_actions(self):
        # This shared case starts at A with no local unvisited action.
        # Recorded transitions reach B and C in one step each. C has two distinct unvisited
        # abstract actions, more than B. The helper asks the Unvisited ASM to choose a route.
        selected = select_from("navigation-most-unvisited-actions")

        # Choose to-c: when destinations are equally near, prefer the one with more
        # unvisited abstract actions. The returned count describes C, not actions at A.
        self.assertEqual("to-c", selected["choice_id"])
        self.assertEqual(2, selected["navigation"]["unvisited_actions"])
