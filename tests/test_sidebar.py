"""The sidebar: five view rows and one action row.

The rows are a navigation surface and nothing else -- the widget owns no
data -- so what is worth pinning is the contract the main window relies
on: which id each row emits, and that the Settings row does *not* behave
like a view. Settings is a modal dialog owned by the window; a sidebar
that left its highlight on "Settings" would be claiming the middle pane
had changed to a page that does not exist.

Inbox deliberately has no row (All Memories already lists every type),
and :meth:`Sidebar.select_view` no-ops on an id with no row, which is
what keeps the tray's "Open Inbox" shortcut working.
"""
from __future__ import annotations

import pytest

from ui.sidebar import (
    VIEW_ALL_MEMORIES,
    VIEW_DASHBOARD,
    VIEW_INBOX,
    VIEW_PROJECTS,
    VIEW_TASKS,
    VIEW_TRASH,
    Sidebar,
)
from ui.strings import (
    SIDEBAR_ALL_MEMORIES,
    SIDEBAR_DASHBOARD,
    SIDEBAR_PROJECTS,
    SIDEBAR_SETTINGS,
    SIDEBAR_TASKS,
    SIDEBAR_TRASH,
)


@pytest.fixture
def sidebar(qapp):
    widget = Sidebar()
    yield widget
    widget.deleteLater()


def _labels(sidebar) -> list[str]:
    return [sidebar.list.item(i).text() for i in range(sidebar.list.count())]


class TestRows:
    def test_the_rows_are_in_the_documented_order(self, sidebar):
        assert _labels(sidebar) == [
            SIDEBAR_DASHBOARD,
            SIDEBAR_ALL_MEMORIES,
            SIDEBAR_TASKS,
            SIDEBAR_TRASH,
            SIDEBAR_PROJECTS,
            SIDEBAR_SETTINGS,
        ]

    def test_settings_sits_directly_under_projects(self, sidebar):
        """The position that was asked for, pinned on its own.

        Asserted as a relationship rather than an index so re-ordering
        the views above them does not silently move Settings elsewhere.
        """
        labels = _labels(sidebar)
        assert labels.index(SIDEBAR_SETTINGS) == labels.index(
            SIDEBAR_PROJECTS
        ) + 1

    def test_the_first_row_is_selected_on_construction(self, sidebar):
        assert sidebar.list.currentRow() == 0

    def test_inbox_has_no_row_of_its_own(self, sidebar):
        from ui.strings import SIDEBAR_INBOX

        assert SIDEBAR_INBOX not in _labels(sidebar)


class TestViewRows:
    @pytest.mark.parametrize(
        "view_id",
        [
            VIEW_ALL_MEMORIES,
            VIEW_TASKS,
            VIEW_TRASH,
            VIEW_PROJECTS,
            VIEW_DASHBOARD,
        ],
    )
    def test_selecting_a_row_emits_its_id(self, sidebar, view_id):
        # Parked on a different row first: Qt only reports a *change* of
        # current item, so re-selecting the row that is already current
        # (Dashboard, at construction) emits nothing -- which is the
        # documented behaviour of ``select_view``, not a bug.
        sidebar.select_view(
            VIEW_TRASH if view_id != VIEW_TRASH else VIEW_TASKS
        )
        seen: list[str] = []
        sidebar.view_selected.connect(seen.append)
        sidebar.select_view(view_id)
        assert seen == [view_id]

    def test_re_selecting_the_current_row_emits_nothing(self, sidebar):
        seen: list[str] = []
        sidebar.view_selected.connect(seen.append)
        sidebar.select_view(VIEW_DASHBOARD)  # already current
        assert seen == []

    def test_selecting_a_view_with_no_row_does_nothing(self, sidebar):
        seen: list[str] = []
        sidebar.view_selected.connect(seen.append)
        sidebar.select_view(VIEW_INBOX)
        assert seen == []
        assert sidebar.list.currentRow() == 0


class TestSettingsRow:
    """The action row: a dialog request, not a view switch."""

    def _click_settings(self, sidebar) -> None:
        labels = _labels(sidebar)
        sidebar.list.setCurrentRow(labels.index(SIDEBAR_SETTINGS))

    def test_it_asks_for_the_dialog(self, sidebar):
        asked: list[int] = []
        sidebar.settings_requested.connect(lambda: asked.append(1))
        self._click_settings(sidebar)
        assert asked == [1]

    def test_it_emits_no_view(self, sidebar):
        """The signal the main window swaps panes on must stay silent.

        Otherwise ``_on_view_changed`` would be handed "settings" as a
        view id, and ``_refresh_list`` would fall back to All Memories --
        the middle pane would change behind the dialog for no reason.
        """
        seen: list[str] = []
        sidebar.view_selected.connect(seen.append)
        self._click_settings(sidebar)
        assert seen == []

    def test_the_highlight_goes_back_to_the_view_underneath(self, sidebar):
        sidebar.select_view(VIEW_TASKS)
        self._click_settings(sidebar)
        assert sidebar.list.currentItem().text() == SIDEBAR_TASKS

    def test_the_row_can_be_used_twice_running(self, sidebar):
        """The restore guard must not stay armed after restoring.

        A flag left set would swallow every later selection change, so
        the second open is the test that matters.
        """
        asked: list[int] = []
        sidebar.settings_requested.connect(lambda: asked.append(1))
        sidebar.select_view(VIEW_PROJECTS)
        self._click_settings(sidebar)
        self._click_settings(sidebar)
        assert asked == [1, 1]
        assert sidebar.list.currentItem().text() == SIDEBAR_PROJECTS

    def test_a_view_still_switches_after_a_visit_to_settings(self, sidebar):
        seen: list[str] = []
        sidebar.view_selected.connect(seen.append)
        self._click_settings(sidebar)
        sidebar.select_view(VIEW_TRASH)
        assert seen == [VIEW_TRASH]
