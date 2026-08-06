from spareparts.modules.lgtm.config import DEFAULTS, matches_any, parse_config


def test_absent_config_is_the_defaults_without_complaint():
    loaded = parse_config(None)
    assert loaded.config == DEFAULTS
    assert loaded.problems == []


def test_clamps_rather_than_rejects_an_out_of_range_count():
    loaded = parse_config({"questions": 10})
    assert loaded.config.questions == 5
    assert loaded.problems


def test_true_is_not_one_question():
    # `bool` is an `int` in Python; `questions: true` is a mistake, not a 1.
    loaded = parse_config({"questions": True})
    assert loaded.config.questions == DEFAULTS.questions
    assert loaded.problems


def test_bad_difficulty_falls_back_and_says_so():
    loaded = parse_config({"difficulty": "brutal"})
    assert loaded.config.difficulty == "medium"
    assert loaded.problems


def test_action_only_keys_are_accepted_in_silence():
    loaded = parse_config({"enforce": True, "webConcepts": False, "exemptReviewers": []})
    assert loaded.problems == []


def test_unknown_keys_are_reported():
    loaded = parse_config({"quesitons": 3})
    assert loaded.problems


def test_non_mapping_is_not_a_crash():
    assert parse_config([1, 2, 3]).config == DEFAULTS


def test_double_star_slash_matches_zero_directories():
    # The bug that once quizzed someone about a 900-line lockfile.
    assert matches_any("package-lock.json", ["**/package-lock.json"])
    assert matches_any("web/package-lock.json", ["**/package-lock.json"])
    assert matches_any("a/b/c/package-lock.json", ["**/package-lock.json"])


def test_single_star_does_not_cross_directories():
    assert matches_any("src/a.ts", ["src/*.ts"])
    assert not matches_any("src/deep/a.ts", ["src/*.ts"])


def test_dots_are_literal():
    assert not matches_any("srcXa.ts", ["src.a.ts"])


def test_provider_and_verifier_are_read():
    loaded = parse_config({"provider": "openai:gpt-5", "verifier": "gemini"})
    assert loaded.config.provider == "openai:gpt-5"
    assert loaded.config.verifier == "gemini"
    assert loaded.problems == []


def test_provider_is_not_validated_here():
    # The vendor list lives in `spareparts.providers`; duplicating it here would
    # mean two places to update, and they would drift. An unknown name is
    # reported by `resolve`, with the known names in the message.
    loaded = parse_config({"provider": "definitely-not-a-vendor"})
    assert loaded.config.provider == "definitely-not-a-vendor"
    assert loaded.problems == []


def test_a_non_string_provider_is_ignored():
    loaded = parse_config({"provider": 3})
    assert loaded.config.provider is None
    assert loaded.problems


def test_provider_defaults_to_none():
    assert DEFAULTS.provider is None
    assert DEFAULTS.verifier is None
