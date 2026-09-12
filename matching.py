"""
Shared, pure (no API calls) helpers for comparing detected action sequences.

Lives outside detect.py / storage.py so both can import it without a
circular dependency (storage.tool_already_saved needs it; detect.py already
imports from storage).
"""


def sequence_similarity(a: list[str], b: list[str]) -> float:
    """
    Jaccard similarity between two action sequences, ignoring order and repeats.

    Two occurrences of a routine rarely fire in exactly the same order or with
    exactly the same step count (a step gets skipped, or an extra Slack message
    sneaks in) — comparing sets instead of exact tuples tolerates that.
    """
    sa, sb = set(a or []), set(b or [])
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def group_similar_sequences(
    interpreted: list[dict],
    threshold: float = 0.6,
) -> list[list[dict]]:
    """
    Greedy single-pass clustering of interpreted routine-candidates whose
    `sequence` overlaps enough (>= threshold) to count as "the same routine".

    Replaces exact-tuple equality (Counter(tuple(seq))) so a ritual missing
    one step on a given occurrence still gets recognised as a repeat.
    """
    groups: list[list[dict]] = []
    for r in interpreted:
        placed = False
        for group in groups:
            if sequence_similarity(r.get("sequence"), group[0].get("sequence")) >= threshold:
                group.append(r)
                placed = True
                break
        if not placed:
            groups.append([r])
    return groups
