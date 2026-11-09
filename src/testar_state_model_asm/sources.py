"""Prepare selection-relevant source snapshots from validated evidence traces."""

from decimal import Decimal
from functools import lru_cache
import hashlib
import json

from testar_evidence import EvidenceReader
from testar_evidence.codec import dumps


def _source_fingerprint(data):
    """Hash the exact compact JSON bytes of an already validated source projection.

    Stream tokens into the digest instead of converting and serializing another
    complete copy. A bounded text cache reuses repeated property names and values.
    """
    digest = hashlib.sha256()

    @lru_cache(maxsize=512)
    def text_token(value):
        return json.dumps(value, ensure_ascii=True).encode("ascii")

    def write(value):
        if isinstance(value, str):
            digest.update(text_token(value))
        elif isinstance(value, dict):
            digest.update(b"{")
            for index, key in enumerate(sorted(value)):
                if index:
                    digest.update(b",")
                digest.update(text_token(key))
                digest.update(b":")
                write(value[key])
            digest.update(b"}")
        elif isinstance(value, list):
            digest.update(b"[")
            for index, item in enumerate(value):
                if index:
                    digest.update(b",")
                write(item)
            digest.update(b"]")
        elif type(value) is int or isinstance(value, Decimal):
            try:
                digest.update(str(value).encode("ascii"))
            except ValueError:
                digest.update(str(Decimal(value)).encode("ascii"))
        else:
            digest.update(dumps(value, sort_keys=True).encode("ascii"))

    write(data)
    return digest.hexdigest()


def evidence_source(evidence, source_id=None, *, label=None):
    """Project a validated ReconstructedEvidence; the original evidence remains unchanged."""
    source_id = source_id or evidence.manifest["evidence_id"]
    data = {
        "runs": [{**record["test_run"], "status": record["status"], "reason": record["reason"]}
                 for record in evidence.test_runs.values()],
        "sequences": [{**record["test_sequence"], "status": record["status"], "reason": record["reason"]}
                      for record in evidence.test_sequences.values()],
        "observations": list(evidence.observations.values()),
        "actions": list(evidence.action_descriptions.values()),
        "executions": [
            {"execution": record["action_execution"], "status": record["status"],
             "executionResult": record["execution_result"], "resultingObservation": record["resulting_observation"]}
            for record in evidence.action_executions.values()
        ]
    }
    fingerprint = _source_fingerprint(data)
    return {"id": source_id, "label": label or source_id, "fingerprint": fingerprint, "evidence": data}


def read_source(directory, source_id=None, *, label=None):
    """Read and validate a trace through the evidence library before preparing ASM input."""
    return evidence_source(EvidenceReader.read(directory), source_id, label=label)
