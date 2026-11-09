"""Prepare owned update data and compact, content-based retry fingerprints."""

from decimal import Decimal
from hashlib import sha256

from testar_evidence.codec import to_data


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


def same(first, second):
    """Check JSON equality without allocating a second tree; True is distinct from 1."""
    if first is second:
        return True
    if isinstance(first, bool) or isinstance(second, bool):
        return type(first) is type(second) and first == second
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(same(first[key], second[key]) for key in first)
    if isinstance(first, list) and isinstance(second, list):
        return len(first) == len(second) and all(same(left, right) for left, right in zip(first, second))
    numbers = (int, float, Decimal)
    if isinstance(first, numbers) and isinstance(second, numbers):
        return canonical(first) == canonical(second) if isinstance(first, float) or isinstance(second, float) else first == second
    return type(first) is type(second) and first == second


def record_id(collection, record):
    return record["execution"]["id"] if collection == "executions" else record["id"]


def fingerprint(value):
    return sha256(repr(canonical(value)).encode("utf-8")).digest()


def _copy_snapshot(sources, selected_runs, previous_records):
    """Convert new data at its original JSON depth, retaining checked old records.

    Placeholders keep unchanged widgets out of the conversion walk. Only records
    equal to our owned accepted data are restored after conversion succeeds.
    """
    if not previous_records or type(sources) not in (list, tuple):
        return to_data({"sources": sources, "selected_runs": selected_runs})
    copied_sources = []
    reused = []
    for source_index, source in enumerate(sources):
        if type(source) is not dict or type(source.get("evidence")) is not dict:
            return to_data({"sources": sources, "selected_runs": selected_runs})
        collections = {}
        for name, records in source["evidence"].items():
            if type(records) not in (list, tuple):
                return to_data({"sources": sources, "selected_runs": selected_runs})
            copied_records = []
            for index, record in enumerate(records):
                if type(record) is not dict:
                    return to_data({"sources": sources, "selected_runs": selected_runs})
                accepted = previous_records.get((source["id"], name, record_id(name, record)))
                if accepted is not None and same(accepted[0], record):
                    copied_records.append(None)
                    reused.append((source_index, name, index, accepted[0]))
                else:
                    copied_records.append(record)
            collections[name] = copied_records
        copied_sources.append({**source, "evidence": collections})
    snapshot = to_data({"sources": copied_sources, "selected_runs": selected_runs})
    for source_index, name, index, record in reused:
        snapshot["sources"][source_index]["evidence"][name][index] = record
    return snapshot


class PreparedSnapshot:
    """Copy new caller data and reuse accepted records only after checking their actual contents.

    Supplied source fingerprints never replace those checks. Cached record digests
    make retry tracking small without hashing every old widget again.
    """

    def __init__(self, sources, selected_runs, previous=None):
        previous_records = previous.record_fingerprints if previous is not None else {}
        self.snapshot = _copy_snapshot(sources, selected_runs, previous_records)
        self.record_fingerprints = {}
        source_signatures = []
        self.snapshot["sources"].sort(key=lambda item: item["id"])
        self.snapshot["selected_runs"].sort(key=lambda item: (item["source"], item["run"]))
        for source in self.snapshot["sources"]:
            collection_signatures = {}
            for name, records in source["evidence"].items():
                records.sort(key=lambda item: record_id(name, item))
                digests = []
                for index, record in enumerate(records):
                    key = source["id"], name, record_id(name, record)
                    accepted = previous_records.get(key)
                    if accepted is not None and same(accepted[0], record):
                        record, digest = accepted
                        records[index] = record
                    else:
                        digest = fingerprint(record)
                    self.record_fingerprints[key] = record, digest
                    digests.append(digest.hex())
                collection_signatures[name] = digests
            metadata = {key: value for key, value in source.items() if key != "evidence"}
            source_signatures.append({"metadata": metadata, "evidence": collection_signatures})
        self.fingerprint = fingerprint({"sources": source_signatures, "selected_runs": self.snapshot["selected_runs"]})
