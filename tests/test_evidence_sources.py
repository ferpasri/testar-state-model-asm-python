"""Source fingerprints retain exact JSON content while preparation avoids extra copies."""

from decimal import Decimal
from hashlib import sha256
import unittest

from testar_evidence import EvidenceEvent, Manifest, ReconstructedEvidence
from testar_evidence.codec import dumps
from testar_state_model_asm import evidence_source
from testar_state_model_asm.sources import _source_fingerprint
from tests.support import case


class EvidenceSourceTests(unittest.TestCase):
    def test_streamed_fingerprint_matches_the_existing_json_digest(self):
        # Keep exact decimal values, strings that resemble numbers, Unicode,
        # and object order. Decimal values must never be rounded through floats.
        values = {"decimal": Decimal("12345678901234567890.12345678901234567890"),
                  "numeric_text": "1.00", "number": Decimal("1.00"),
                  "flags": [True, False, None], "text": "?quipe\n\"\\",
                  "nested": [{"z": -4, "a": Decimal("-0.00")}],
                  "empty": [], "large": 10 ** 5000}
        expected = sha256(dumps(values, sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(expected, _source_fingerprint(values))
        self.assertEqual(expected, _source_fingerprint(dict(reversed(list(values.items())))))
        self.assertNotEqual(_source_fingerprint({"value": 1}), _source_fingerprint({"value": "1"}))
        self.assertNotEqual(_source_fingerprint({"value": True}), _source_fingerprint({"value": 1}))

    def test_returned_source_and_history_remain_independent(self):
        data, _ = case("empty-start")
        original = data["evidence_sets"]["observed"]["sources"][0]["evidence"]
        history = ReconstructedEvidence(Manifest("trace"))
        events = [
            ("test_run_started", {k: v for k, v in original["runs"][0].items() if k not in ("status", "reason")}),
            ("test_sequence_started", {k: v for k, v in original["sequences"][0].items() if k not in ("status", "reason")}),
            *[("action_described", action) for action in original["actions"]],
            ("observation_recorded", original["observations"][0]),
        ]
        for index, (event_type, payload) in enumerate(events):
            history.apply(EvidenceEvent(str(index), event_type, payload))
        source = evidence_source(history)
        expected = sha256(dumps(source["evidence"], sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(expected, source["fingerprint"])
        source["evidence"]["observations"][0]["recorded_state"]["widgets"].clear()
        again = evidence_source(history)
        self.assertEqual(expected, again["fingerprint"])
        self.assertTrue(again["evidence"]["observations"][0]["recorded_state"]["widgets"])
