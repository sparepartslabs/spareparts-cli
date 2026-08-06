from spareparts.modules.lgtm.generator import Candidate, _as_candidate, well_formed


def candidate(**overrides) -> Candidate:
    base = dict(
        prompt="What does the new guard prevent?",
        options=["A charge above MAX", "A charge below zero", "A duplicate charge"],
        correct=0,
        file="src/charge.ts",
        hunk="@@ -10,6 +10,9 @@",
        rationale="because",
    )
    base.update(overrides)
    return Candidate(**base)


def test_accepts_a_normal_candidate():
    assert well_formed(candidate())


def test_rejects_fewer_than_three_options():
    assert not well_formed(candidate(options=["yes", "no"], correct=0))


def test_rejects_an_out_of_range_answer():
    assert not well_formed(candidate(correct=3))
    assert not well_formed(candidate(correct=-1))


def test_rejects_duplicate_options():
    assert not well_formed(
        candidate(options=["same", "Same ", "different"], correct=0)
    )


def test_rejects_the_conspicuously_long_answer():
    # The oldest tell in multiple choice: the careful correct answer beside two
    # throwaway wrong ones.
    assert not well_formed(
        candidate(
            options=[
                "It rejects any charge above MAX by raising before the post call",
                "Nothing",
                "It logs",
            ],
            correct=0,
        )
    )


def test_a_long_distractor_is_fine():
    assert well_formed(
        candidate(
            options=[
                "It rejects a charge above MAX",
                "It rejects any charge above MAX by raising before the post call",
                "It logs the charge",
            ],
            correct=0,
        )
    )


def test_candidate_parsing_rejects_junk():
    assert _as_candidate(None) is None
    assert _as_candidate({"prompt": "  ", "file": "a", "hunk": "b", "rationale": "c"}) is None
    assert _as_candidate({"prompt": "p", "file": "f", "hunk": "h", "rationale": "r"}) is None


def test_candidate_parsing_rejects_a_boolean_index():
    raw = {
        "prompt": "p",
        "options": ["a", "b", "c"],
        "correct": True,
        "file": "f",
        "hunk": "h",
        "rationale": "r",
    }
    assert _as_candidate(raw) is None


def test_candidate_parsing_accepts_a_good_one():
    raw = {
        "prompt": "p",
        "options": ["a", "b", "c"],
        "correct": 1,
        "file": "f",
        "hunk": "h",
        "rationale": "r",
    }
    parsed = _as_candidate(raw)
    assert parsed is not None and parsed.correct == 1
