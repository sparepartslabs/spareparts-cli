import pytest

from spareparts.modules.lgtm import prompts
from spareparts.modules.lgtm.generator import OVERSHOOT


def test_the_file_matches_its_pinned_hash():
    """
    The drift guard.

    This file is byte-identical in sparepartslabs/spareparts-lgtm, which pins
    the same hash. If this test fails you edited the prompts — which is fine,
    and the fix is two steps, not one:

      1. update PROMPTS_SHA256 in src/spareparts/modules/lgtm/prompts.py
      2. copy the file to the other repo and update its pinned hash too

    Doing only the first is how the two tools end up asking differently-worded
    questions about the same diff, which is the failure this whole arrangement
    exists to prevent.
    """
    assert prompts.file_sha256() == prompts.PROMPTS_SHA256, (
        "The shared prompts file changed. Update PROMPTS_SHA256 *and* copy the "
        "file to sparepartslabs/spareparts-lgtm. See PROMPTS.md."
    )


def test_no_repo_specific_vocabulary_leaks_into_the_shared_file():
    # The Action addresses a reviewer on a pull request; the CLI addresses
    # someone about to merge a branch they just read. Wording true of only one
    # of those is how the file stops being shareable.
    text = " ".join(prompts.load()["propose"] + prompts.load()["verify"]).lower()
    for word in ("pull request", " pr ", "reviewer", "approved", "merge button"):
        assert word not in text, f"{word!r} is repo-specific — see PROMPTS.md"


def test_propose_substitutes_every_placeholder():
    out = prompts.propose(diff="DIFF-HERE", difficulty="hard", want=4)
    assert "{{" not in out
    assert "DIFF-HERE" in out
    assert "up to 4 multiple-choice" in out
    assert "plausible misreading" in out  # the hard guidance, not medium


def test_propose_picks_the_right_difficulty():
    easy = prompts.propose("d", "easy", 2)
    medium = prompts.propose("d", "medium", 2)
    assert "rule them out immediately" in easy
    assert "true statements about this change" in medium
    assert easy != medium


def test_an_unknown_difficulty_raises_rather_than_defaulting():
    # config has already clamped this; reaching here means the shared file and
    # the config disagree about the vocabulary, which is a drift bug.
    with pytest.raises(prompts.PromptsError):
        prompts.propose("d", "brutal", 2)


def test_verify_substitutes_every_placeholder_and_marks_the_answer():
    out = prompts.verify(
        question="What does it do?",
        options=["first", "second", "third"],
        correct=1,
        cited="src/a.ts @@ -1,2 +1,3 @@",
        rationale="because of line 4",
        diff="DIFF-HERE",
    )
    assert "{{" not in out
    assert "What does it do?" in out
    assert "  * second" in out
    assert "    first" in out
    assert "src/a.ts @@ -1,2 +1,3 @@" in out
    assert "because of line 4" in out
    assert "DIFF-HERE" in out


def test_the_verifier_is_never_told_which_vendor_proposed():
    # The verifier's independence is the point of the stage. It sees the
    # question and the diff, and nothing about who wrote it.
    out = prompts.verify("q", ["a", "b", "c"], 0, "f h", "r", "d").lower()
    for vendor in ("anthropic", "claude", "openai", "gpt", "gemini", "google"):
        assert vendor not in out


def test_every_difficulty_the_config_allows_has_guidance():
    from spareparts.modules.lgtm.config import DIFFICULTIES

    guidance = prompts.load()["difficultyGuidance"]
    assert set(DIFFICULTIES) == set(guidance)


def test_the_overshoot_reaches_the_prompt():
    # What the generator asks for and what the prompt says must agree.
    want = min(3 * OVERSHOOT, 8)
    assert f"up to {want} multiple-choice" in prompts.propose("d", "medium", want)
