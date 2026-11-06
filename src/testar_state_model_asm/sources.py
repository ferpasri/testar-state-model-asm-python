"""Prepare selection-relevant source snapshots from validated evidence traces."""

import hashlib

from testar_evidence import EvidenceReader
from testar_evidence.codec import dumps


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
    fingerprint = hashlib.sha256(dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    return {"id": source_id, "label": label or source_id, "fingerprint": fingerprint, "evidence": data}


def read_source(directory, source_id=None, *, label=None):
    """Read and validate a trace through the evidence library before preparing ASM input."""
    return evidence_source(EvidenceReader.read(directory), source_id, label=label)
