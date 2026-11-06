"""Trace identities remain distinct even when abstraction groups their states and actions."""

import unittest

from testar_state_model_asm import AsmRun
from tests.support import case


class StateAndActionMatchingTests(unittest.TestCase):
    def setUp(self):
        # Load recorded form-page observations and rules that lowercase app.page
        # and match action targets by widget name. Build the model from the initial
        # traces; the first context identifies the current observation and its choices.
        self.data, _ = case("configured-abstraction")
        self.initial = self.data["evidence_sets"]["initial"]
        self.run = AsmRun(self.data["configuration"], **self.initial, choose_index=lambda size: 0)
        self.context = self.data["steps"][0]["context"]

    def test_form_and_uppercase_form_become_one_abstract_state(self):
        # The current trace captured "form"; the historical trace captured "FORM".
        # The lowercase rule makes them comparable without changing either trace.
        sources = {source["id"]: source for source in self.initial["sources"]}
        self.assertEqual("form", sources["live"]["evidence"]["observations"][0]["recorded_state"]["properties"]["app.page"])
        self.assertEqual("FORM", sources["history"]["evidence"]["observations"][0]["recorded_state"]["properties"]["app.page"])
        node = next(node for node in self.run.model["nodes"] if self.context["observation"] in node["members"])
        # Three observations share one abstract state; their recorded values stay intact.
        self.assertEqual(3, len(node["members"]))

    def test_widget_names_link_actions_despite_different_widget_ids(self):
        # The current form offers First and Second. A historical trace clicked First
        # under a different widget ID; matching by name lets that execution count here.
        self.run.set_context(self.context)
        # Selection returns a decision with a choice ID and per-choice execution counts.
        # It does not click either widget.
        decision = self.run.select()
        first, second = decision["rankings"]
        # "one" and "old:one" both describe the First widget. Its historical click counts.
        self.assertEqual("choice-1", first["choice_id"])
        self.assertEqual(1, first["recorded_executions"])
        self.assertEqual(0, second["recorded_executions"])
        # Second has no matching execution, so the ASM selects its current choice ID.
        self.assertEqual("choice-2", decision["choice_id"])
