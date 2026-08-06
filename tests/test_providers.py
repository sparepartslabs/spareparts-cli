import pytest

from spareparts.providers import (
    VENDORS,
    ProviderError,
    available,
    default_vendor,
    resolve,
)
from spareparts.providers._gemini import _plain
from spareparts.providers._openai import _strictify

ALL_KEYS = [k for v in VENDORS for k in v.env_keys]


@pytest.fixture
def no_keys(monkeypatch):
    for key in ALL_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def keys(monkeypatch, no_keys):
    def set_them(*names: str):
        for name in names:
            monkeypatch.setenv(name, "test-key")

    return set_them


def test_unknown_provider_lists_the_known_ones(keys):
    keys("ANTHROPIC_API_KEY")
    with pytest.raises(ProviderError) as caught:
        resolve("claude")
    message = str(caught.value)
    assert "Unknown provider" in message
    for vendor in VENDORS:
        assert vendor.name in message


def test_a_missing_key_names_the_variable(no_keys):
    with pytest.raises(ProviderError) as caught:
        resolve("openai")
    assert "OPENAI_API_KEY" in str(caught.value)


def test_a_missing_key_points_at_the_ones_you_do_have(keys):
    keys("ANTHROPIC_API_KEY")
    with pytest.raises(ProviderError) as caught:
        resolve("gemini")
    assert "You have a key for: anthropic" in str(caught.value)


def test_gemini_accepts_either_google_variable(keys):
    keys("GOOGLE_API_KEY")
    assert "gemini" in available()
    keys("GEMINI_API_KEY")
    assert "gemini" in available()


def test_available_reports_only_what_is_set(keys):
    keys("ANTHROPIC_API_KEY", "OPENAI_API_KEY")
    assert set(available()) == {"anthropic", "openai"}


def test_none_means_whichever_key_is_set(keys):
    # Not a hardcoded vendor: the one you actually have credentials for.
    # `keys` is additive within a test, so each vendor gets its own.
    keys("OPENAI_API_KEY")
    assert resolve(None).label.startswith("openai:")


def test_none_with_only_a_google_key(keys):
    keys("GEMINI_API_KEY")
    assert resolve(None).label.startswith("gemini:")


def test_more_than_one_key_picks_the_same_one_every_time(keys):
    # Which one matters less than that it does not vary between runs.
    keys("OPENAI_API_KEY", "ANTHROPIC_API_KEY")
    first = resolve(None).label
    assert resolve(None).label == first
    assert first.startswith("anthropic:")


def test_no_key_at_all_names_every_variable_that_would_work(no_keys):
    with pytest.raises(ProviderError) as caught:
        default_vendor()
    message = str(caught.value)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        assert key in message


def test_the_vendor_colon_model_form(keys):
    keys("ANTHROPIC_API_KEY")
    assert resolve("anthropic:claude-sonnet-5").label == "anthropic:claude-sonnet-5"


def test_the_model_argument_beats_the_spec(keys):
    # A flag is typed now; the spec may have come out of a config file.
    keys("ANTHROPIC_API_KEY")
    assert resolve("anthropic:from-config", "from-flag").label == "anthropic:from-flag"


def test_each_vendor_has_a_default_model(keys):
    keys(*ALL_KEYS)
    for vendor in VENDORS:
        assert resolve(vendor.name).label == f"{vendor.name}:{vendor.default_model}"


# --- schema transforms -----------------------------------------------------

NESTED = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}, "correct": {"type": "integer"}},
                "required": ["prompt", "correct"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def test_strictify_requires_every_property_at_every_depth():
    out = _strictify(NESTED)
    assert out["required"] == ["questions"]
    item = out["properties"]["questions"]["items"]
    assert sorted(item["required"]) == ["correct", "prompt"]
    assert item["additionalProperties"] is False


def test_strictify_adds_required_where_it_was_missing():
    out = _strictify({"type": "object", "properties": {"a": {"type": "string"}}})
    assert out["required"] == ["a"]
    assert out["additionalProperties"] is False


def test_strictify_leaves_the_original_alone():
    before = repr(NESTED)
    _strictify(NESTED)
    assert repr(NESTED) == before


def test_plain_strips_additional_properties_at_every_depth():
    out = _plain(NESTED)
    assert "additionalProperties" not in out
    assert "additionalProperties" not in out["properties"]["questions"]["items"]
    # Everything else survives.
    assert out["required"] == ["questions"]
    assert out["properties"]["questions"]["items"]["properties"]["prompt"] == {
        "type": "string"
    }


def test_plain_leaves_the_original_alone():
    before = repr(NESTED)
    _plain(NESTED)
    assert repr(NESTED) == before
