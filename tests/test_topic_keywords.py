"""Keyword-based project auto-linking (``topic_keywords``).

The rule is deliberately dull: a capture's text is checked against each
project's configured keyword list as a case-insensitive **substring**,
and the first group with a hit gets the note filed under it. No
stemming, no fuzzy distance, no semantics, no network — the whole
feature is `keyword in text.lower()` in a loop, which is why it can be
described to a user in one sentence and why its failure mode is a
missed link rather than a surprising one.

Three properties matter enough to pin:

* a configured keyword links the capture, creating the project if it
  does not exist yet;
* an explicit "… to the X project" phrase always wins — that is the
  user saying it outright, and it outranks a substring guess;
* an empty ``topic_keywords`` is a true no-op, so the feature is off
  until it is configured.

The settings are read through a *callable* rather than captured as a
dict, because the settings dialog rebinds ``MainWindow.settings`` on
save; ``test_rules_are_re_read_on_every_capture`` is the test that
pins it.
"""
from __future__ import annotations

import pytest

from core.memory import get_memory
from core.projects import get_memory_project, list_projects
from core.settings import DEFAULT_SETTINGS, load_settings, save_settings
from ui.assistant_controller import AssistantController, SessionContext


EMBEDDED = {"Embedded": ["esp32", "sensor", "arduino", "mpu-6050", "i2c"]}


@pytest.fixture
def make_ctrl(conn, qapp):
    """Build a controller over a mutable settings dict.

    Returns ``(controller, settings)``; mutate ``settings`` in place to
    change the rules mid-test, which is how the real app behaves when
    the user saves the Topics page.
    """
    made = []

    def _make(topic_keywords=None):
        settings = dict(DEFAULT_SETTINGS)
        # A fresh dict, never the shared default: ``dict()`` is a shallow
        # copy, so mutating ``settings["topic_keywords"]`` in place would
        # scribble on DEFAULT_SETTINGS and leak into every later test.
        settings["topic_keywords"] = dict(topic_keywords or {})
        controller = AssistantController(
            conn, SessionContext(), settings_provider=lambda: settings
        )
        made.append(controller)
        return controller, settings

    yield _make
    for controller in made:
        controller.deleteLater()


def _capture(controller, text: str) -> int:
    ids: list[int] = []
    controller.captured.connect(ids.append)
    controller.submit(text)
    return ids[-1]


# ---------- the happy path ----------


class TestKeywordLinking:
    def test_a_keyword_files_the_capture_and_creates_the_project(
        self, make_ctrl, conn
    ):
        ctrl, _settings = make_ctrl(EMBEDDED)
        assert list_projects(conn) == []
        new_id = _capture(ctrl, "remember that the ESP32 has two I2C buses")
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"
        assert [p.name for p in list_projects(conn)] == ["Embedded"]

    def test_matching_is_case_insensitive_both_ways(self, make_ctrl, conn):
        ctrl, _settings = make_ctrl({"Embedded": ["ESP32"]})
        new_id = _capture(ctrl, "the esp32 board arrived")
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"

    def test_a_keyword_inside_a_longer_word_still_counts(
        self, make_ctrl, conn
    ):
        """Substring, not word-boundary — and that is on purpose.

        "sensors" has to match the keyword "sensor" for the feature to be
        useful without the user configuring every plural. The cost is
        that a keyword like "i2c" would also fire inside a longer token;
        keywords are short and technical, so that trade is the right way
        round here.
        """
        ctrl, _settings = make_ctrl({"Embedded": ["sensor"]})
        new_id = _capture(ctrl, "wire up both sensors tonight")
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"

    def test_an_existing_project_is_reused_not_duplicated(
        self, make_ctrl, conn
    ):
        ctrl, _settings = make_ctrl(EMBEDDED)
        first = _capture(ctrl, "the esp32 needs a pull-up")
        second = _capture(ctrl, "the arduino sketch is flashed")
        assert (
            get_memory_project(conn, first).id
            == get_memory_project(conn, second).id
        )
        assert [p.name for p in list_projects(conn)] == ["Embedded"]

    def test_the_first_configured_group_wins(self, make_ctrl, conn):
        """A capture that mentions two groups lands in exactly one.

        Insertion order decides, so the outcome is predictable and the
        note is never duplicated across projects.
        """
        ctrl, _settings = make_ctrl(
            {"Embedded": ["sensor"], "Robotics": ["arduino"]}
        )
        new_id = _capture(ctrl, "the sensor is on the arduino")
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"
        assert [p.name for p in list_projects(conn)] == ["Embedded"]

    def test_linking_does_not_change_the_stored_text_or_type(
        self, make_ctrl, conn
    ):
        ctrl, _settings = make_ctrl(EMBEDDED)
        new_id = _capture(ctrl, "the esp32 has two I2C buses")
        stored = get_memory(conn, new_id)
        assert stored.title == "the esp32 has two I2C buses"
        assert stored.type == "inbox"

    def test_a_dated_capture_is_both_a_task_and_linked(
        self, make_ctrl, conn
    ):
        """The two capture-path hooks are independent, not exclusive."""
        ctrl, _settings = make_ctrl(EMBEDDED)
        new_id = _capture(ctrl, "test the sensor tomorrow")
        assert get_memory(conn, new_id).type == "task"
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"

    def test_rules_are_re_read_on_every_capture(self, make_ctrl, conn):
        """Saving the Topics page takes effect without a restart.

        The controller holds a callable, not a snapshot, so a rule added
        after construction applies to the next capture.
        """
        ctrl, settings = make_ctrl({})
        first = _capture(ctrl, "the esp32 needs a pull-up")
        assert get_memory_project(conn, first) is None
        settings["topic_keywords"] = {"Embedded": ["esp32"]}
        second = _capture(ctrl, "the esp32 needs a decoupling cap")
        linked = get_memory_project(conn, second)
        assert linked is not None and linked.name == "Embedded"


# ---------- precedence: the spoken phrase wins ----------


class TestExplicitPhraseWins:
    def test_an_explicit_project_outranks_a_keyword_match(
        self, make_ctrl, conn
    ):
        """"… to the Batman project" is the user saying it outright.

        The text also contains "esp32", which the rules map to Embedded.
        The phrase wins, and Embedded is not even created — a keyword
        guess must not quietly fork the note's home.
        """
        ctrl, _settings = make_ctrl(EMBEDDED)
        new_id = _capture(
            ctrl, "remember to solder the esp32 to the Batman project"
        )
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Batman"
        assert [p.name for p in list_projects(conn)] == ["Batman"]

    def test_the_keyword_rule_still_applies_without_a_phrase(
        self, make_ctrl, conn
    ):
        # The control for the test above: same text, no phrase.
        ctrl, _settings = make_ctrl(EMBEDDED)
        new_id = _capture(ctrl, "remember to solder the esp32")
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"


# ---------- off by default ----------


class TestNoOpWhenUnconfigured:
    def test_empty_topic_keywords_links_nothing(self, make_ctrl, conn):
        ctrl, _settings = make_ctrl({})
        new_id = _capture(ctrl, "remember that the ESP32 has two I2C buses")
        assert get_memory_project(conn, new_id) is None
        assert list_projects(conn) == []

    def test_the_default_settings_are_empty(self):
        assert DEFAULT_SETTINGS["topic_keywords"] == {}

    def test_no_settings_provider_at_all_is_also_a_no_op(self, conn, qapp):
        """Headless construction (and every older test) keeps working."""
        ctrl = AssistantController(conn, SessionContext())
        try:
            new_id = _capture(ctrl, "remember that the ESP32 is a two-core MCU")
            assert get_memory_project(conn, new_id) is None
            assert list_projects(conn) == []
        finally:
            ctrl.deleteLater()

    def test_a_non_matching_capture_creates_no_project(self, make_ctrl, conn):
        ctrl, _settings = make_ctrl(EMBEDDED)
        new_id = _capture(ctrl, "remember to buy milk")
        assert get_memory_project(conn, new_id) is None
        assert list_projects(conn) == []


# ---------- malformed settings on disk ----------


class TestMalformedRulesAreTolerated:
    """``topic_keywords`` is user-editable JSON; bad shapes must not raise.

    A hand-edited settings file is a thing that happens. Anything that
    isn't a name mapped to a list of strings is skipped, and the capture
    is still filed — the same trade as everywhere else on this path.
    """

    @pytest.mark.parametrize(
        "rules",
        [
            {"": ["esp32"]},
            {"   ": ["esp32"]},
            {"Embedded": []},
            {"Embedded": [""]},
            {"Embedded": None},
            {"Embedded": 42},
            {"Embedded": [None, 7]},
        ],
    )
    def test_unusable_rules_are_skipped(self, make_ctrl, conn, rules):
        ctrl, _settings = make_ctrl(rules)
        new_id = _capture(ctrl, "the esp32 needs a pull-up")
        assert new_id > 0
        assert get_memory_project(conn, new_id) is None
        assert list_projects(conn) == []

    def test_a_bare_string_is_read_as_a_single_keyword(self, make_ctrl, conn):
        ctrl, _settings = make_ctrl({"Embedded": "esp32"})
        new_id = _capture(ctrl, "the esp32 needs a pull-up")
        linked = get_memory_project(conn, new_id)
        assert linked is not None and linked.name == "Embedded"

    def test_a_non_dict_value_is_ignored(self, conn, qapp):
        ctrl = AssistantController(
            conn,
            SessionContext(),
            settings_provider=lambda: {"topic_keywords": ["esp32"]},
        )
        try:
            new_id = _capture(ctrl, "the esp32 needs a pull-up")
            assert get_memory_project(conn, new_id) is None
        finally:
            ctrl.deleteLater()

    def test_a_raising_provider_does_not_lose_the_capture(self, conn, qapp):
        def _boom():
            raise RuntimeError("settings unavailable")

        ctrl = AssistantController(
            conn, SessionContext(), settings_provider=_boom
        )
        try:
            new_id = _capture(ctrl, "the esp32 needs a pull-up")
            assert new_id > 0
            assert get_memory(conn, new_id).title == "the esp32 needs a pull-up"
        finally:
            ctrl.deleteLater()


# ---------- persistence ----------


def test_topic_keywords_round_trip_through_the_settings_file(tmp_path):
    """The generic merge carries the new key with no extra plumbing."""
    path = str(tmp_path / "settings.json")
    settings = dict(DEFAULT_SETTINGS)
    settings["topic_keywords"] = {"Embedded": ["esp32", "sensor"]}
    save_settings(settings, path)
    assert load_settings(path)["topic_keywords"] == {
        "Embedded": ["esp32", "sensor"]
    }


def test_an_old_settings_file_without_the_key_loads_at_the_default(tmp_path):
    path = str(tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text(
        '{"hotkey": "ctrl+space"}', encoding="utf-8"
    )
    assert load_settings(path)["topic_keywords"] == {}
