"""User-facing strings for Alfred.

Centralized so the butler's voice stays consistent and easy to tune. Every
entry is short (under ~8 words), dry, and never gushing. No exclamation
marks -- Alfred is unflappable.
"""

# Window
APP_TITLE = "Alfred"

# Toolbar / buttons
NEW_MEMORY = "New entry"
NEW_INBOX = "+ Inbox"
SAVE = "Save"
DELETE = "Delete"
PIN_LABEL = "Pin to top"
PREVIEW_TOGGLE_PREVIEW = "Preview"
PREVIEW_TOGGLE_EDIT = "Edit"

# Sidebar / views
SIDEBAR_ALL_MEMORIES = "All Memories"
SIDEBAR_INBOX = "Inbox"
SIDEBAR_TASKS = "Tasks"
SIDEBAR_TRASH = "Trash"
SIDEBAR_DASHBOARD = "Dashboard"
# Not a view: the row opens the Settings dialog and hands the
# selection straight back to whichever view the user was on.
SIDEBAR_SETTINGS = "Settings"
INBOX_EMPTY = "The inbox is clear."
TASKS_EMPTY = "No tasks on file."

# Field labels and placeholders
TITLE_LABEL = "Title"
TAGS_LABEL = "Tags"
TAGS_PLACEHOLDER = "comma, separated, tags"
SEARCH_PLACEHOLDER = "Search your files"

# Status / feedback messages
EMPTY_STATE = "Nothing on file yet. Shall we begin?"
SEARCH_NO_RESULTS = "I find no match for that, I'm afraid."
SAVED_NEW = "Noted and filed."
SAVED_UPDATED = "Updated."
TITLE_REQUIRED = "A title is required, I'm afraid."
DELETED = "Removed from the file."

# Confirmation dialog
DELETE_CONFIRM_TITLE = "Confirm removal"
DELETE_CONFIRM_BODY = "Remove this entry from your files?"

# History / version restore
HISTORY = "History"
HISTORY_DIALOG_TITLE = "Version history"
HISTORY_EMPTY = "No previous versions on file."
RESTORE_VERSION = "Restore this version"
RESTORE_CONFIRM_TITLE = "Confirm restoration"
RESTORE_CONFIRM_BODY = "Replace the current entry with an earlier version?"
VERSION_RESTORED = "Restored to an earlier version."
CLOSE = "Close"

# Inbox actions
ORGANIZE_AS_NOTE = "Organize as note"
CONVERT_TO_TASK = "Convert to task"
INBOX_ORGANIZED = "Promoted to a note."
INBOX_CONVERTED = "Promoted to a task."
QUICK_CAPTURE_TITLE = "Quick capture"
QUICK_CAPTURE_LABEL = "Capture:"

# Task actions
MARK_COMPLETE = "Mark complete"
MARK_INCOMPLETE = "Mark incomplete"
DUE_DATE_LABEL = "Due date"
NO_DUE_DATE = "No due date"
TASK_COMPLETED = "Marked complete."
TASK_UNCOMPLETED = "Marked incomplete."
DUE_DATE_SET = "Due date updated."
DUE_DATE_CLEARED = "Due date cleared."

# Task buckets (QTreeWidget top-level items). Three, not four:
# overdue work belongs in Today and undated work in Upcoming, so a
# separate "Pending" bucket had nothing left of its own to hold.
BUCKET_TODAY = "Today"
BUCKET_UPCOMING = "Upcoming"
BUCKET_COMPLETED = "Completed"

# Trash
TRASH_EMPTY_STATE = "Nothing in the bin."
TRASH_EMPTY_BTN = "Empty Trash"
TRASH_EMPTY_CONFIRM_TITLE = "Empty the bin"
TRASH_EMPTY_CONFIRM_BODY = "Permanently remove every item in the bin?"
TRASH_EMPTIED = "The bin is empty."
RESTORE = "Restore"
RESTORED = "Restored."
DELETE_PERMANENTLY = "Delete permanently"
DELETE_PERMANENT_CONFIRM_TITLE = "Confirm permanent removal"
DELETE_PERMANENT_CONFIRM_BODY = "Remove this entry forever? This cannot be undone."
PERMANENTLY_DELETED = "Removed forever."
TRASH_PREVIEW_PLACEHOLDER = "Select an entry to preview it."
TRASH_TYPE_LABEL = "Type"
TRASH_DELETED_LABEL = "Removed"

# Projects
SIDEBAR_PROJECTS = "Projects"
PROJECTS_EMPTY = "No projects on file."
NEW_PROJECT = "New project"
PROJECT_LABEL = "Project"
PROJECT_NAME_LABEL = "Name"
PROJECT_DESCRIPTION_LABEL = "Description"
PROJECT_NAME_PLACEHOLDER = "Project name"
PROJECT_DESCRIPTION_PLACEHOLDER = "What is this for?"
PROJECT_CREATED = "Project opened."
PROJECT_RENAMED = "Project updated."
PROJECT_DELETED = "Project closed."
RENAME_PROJECT = "Rename"
PROJECT_DIALOG_TITLE_NEW = "New project"
PROJECT_DIALOG_TITLE_RENAME = "Rename project"
DELETE_PROJECT_CONFIRM_TITLE = "Confirm removal"
DELETE_PROJECT_CONFIRM_BODY = "Close this project? Its entries will remain, unfiled."
PROJECT_NAME_REQUIRED = "A project needs a name."
PROJECT_NO_PROJECT = "— No Project —"
PROJECT_PREVIEW_PLACEHOLDER = "Select a project to view its entries."

# Related (memory links)
RELATED_LABEL = "Related"
RELATED_EMPTY = "Nothing linked yet."
ADD_RELATED = "Add related"
RELATED_DIALOG_TITLE = "Add related entry"
RELATED_SEARCH_PLACEHOLDER = "Filter entries…"
RELATED_REMOVE_TOOLTIP = "Remove link"
RELATED_NO_MATCHES = "No matching entries."

# Quick capture popup
CAPTURE_PLACEHOLDER = "Remember something, or ask me…"
CAPTURE_CONFIRMATION = "Noted, sir."
CAPTURE_WINDOW_TITLE = "Quick capture"
CAPTURE_CONFIRMATION_MS = 600

# Ask-or-remember extension.
# The popup is two-state: it starts in INPUT mode (the existing
# single-line entry field) and, when the user submits a
# question-like text, transitions to ANSWER mode with up to 3
# matching memories listed below the input. Strings are short
# and butler-toned to match the rest of the app.
CAPTURE_ANSWER_HEADER_PREFIX = "You asked"
CAPTURE_ANSWER_HEADER_SUFFIX = ""
CAPTURE_ANSWER_NO_RESULTS = "Nothing on file about that."
CAPTURE_ANSWER_NO_RESULTS_HINT = (
    "Try “remember that …” to file a new note."
)
CAPTURE_ANSWER_RESULTS_HEADER = "Matches on file"
CAPTURE_ANSWER_RESULT_SNIPPET_FALLBACK = "(no preview)"
CAPTURE_ANSWER_RESULT_OPEN_TOOLTIP = "Open this entry"

# Dashboard -- the default landing view.
# Greeting is time-of-day based; the dashboard picks one
# of the three and stamps it into the header. The
# explicit noun-phrase ("Good morning, sir") matches
# the rest of Alfred's voice -- third-person reference
# rather than first-person cheerfulness.
DASHBOARD_GREETING_MORNING = "Good morning."
DASHBOARD_GREETING_AFTERNOON = "Good afternoon."
DASHBOARD_GREETING_EVENING = "Good evening."
DASHBOARD_GREETING_NIGHT = "A late evening, I see."

DASHBOARD_SUBHEAD = "What shall we attend to today?"

DASHBOARD_STATS_HEADER = "On file"
DASHBOARD_STAT_MEMORIES = "Memories"
DASHBOARD_STAT_TASKS = "Open tasks"
DASHBOARD_STAT_PROJECTS = "Projects"

DASHBOARD_TASKS_TODAY_HEADER = "Due today"
DASHBOARD_TASKS_TODAY_EMPTY = "Nothing due today."

DASHBOARD_OPEN_TOOLTIP = "Open this entry"

# System tray
TRAY_TOOLTIP = "Alfred"
TRAY_OPEN = "Open Alfred"
TRAY_QUICK_CAPTURE = "Quick Capture"
TRAY_SEARCH = "Search Memories"
TRAY_OPEN_INBOX = "Open Inbox"
TRAY_PINNED = "Pinned Memories"
TRAY_SETTINGS = "Settings"
TRAY_EXIT = "Exit Alfred"
TRAY_MINIMIZE_NOTIFICATION_TITLE = "Alfred"
TRAY_MINIMIZE_NOTIFICATION_BODY = (
    "Alfred is still here — right-click the tray icon to reopen."
)

# File menu (export / import / backup)
MENU_FILE = "&File"
MENU_EXPORT = "&Export all…"
MENU_IMPORT = "&Import…"
MENU_BACKUP = "&Backup now"

EXPORT_DIALOG_TITLE = "Export all"
EXPORT_DIALOG_FILTER = "JSON files (*.json)"
EXPORT_DEFAULT_FILENAME_PREFIX = "alfred-export-"
EXPORT_DONE = "Filed {count} entries to {path}."

IMPORT_DIALOG_TITLE = "Import"
IMPORT_DIALOG_FILTER = "JSON files (*.json)"
IMPORT_CONFIRM_TITLE = "Confirm import"
IMPORT_CONFIRM_BODY = (
    "This will add {count} new entries (and {links} links) from the "
    "selected file. Existing entries will not be touched, merged, or "
    "deduplicated. Proceed?"
)
IMPORT_DONE = (
    "Imported {count} entries and {links} links. "
    "{projects} new projects opened."
)

BACKUP_DONE = "Backup filed at {path}."

# Settings dialog -- window
SETTINGS_TITLE = "Settings"
SETTINGS_CATEGORY_GENERAL = "General"
SETTINGS_CATEGORY_HOTKEY = "Hotkey"
SETTINGS_CATEGORY_BACKUPS = "Backups"
SETTINGS_CATEGORY_NOTIFICATIONS = "Notifications"
SETTINGS_CATEGORY_TOPICS = "Topics"
SETTINGS_CATEGORY_DATA = "Data"
SETTINGS_CATEGORY_ABOUT = "About"

# General page
SETTINGS_GENERAL_STORAGE_LABEL = "Storage location"
SETTINGS_GENERAL_STORAGE_CHANGE = "Change…"
SETTINGS_GENERAL_RESTART_REQUIRED = (
    "A restart is required for this change to take effect."
)
SETTINGS_GENERAL_STORAGE_DIALOG_TITLE = "Choose database file"
SETTINGS_GENERAL_STORAGE_DIALOG_FILTER = "Alfred database (*.alfred.db *.db)"

# Hotkey page
SETTINGS_HOTKEY_LABEL = "Global hotkey"
SETTINGS_HOTKEY_PLACEHOLDER = "ctrl+space"
SETTINGS_HOTKEY_INVALID = "That hotkey is not valid."
SETTINGS_HOTKEY_NOTE = (
    "Changes take effect immediately, no restart required."
)
SETTINGS_HOTKEY_UNAVAILABLE = (
    "The new hotkey could not be registered. "
    "The previous hotkey is still active."
)

# Backups page
SETTINGS_BACKUPS_FOLDER_LABEL = "Backup folder"
SETTINGS_BACKUPS_CHANGE = "Change…"
SETTINGS_BACKUPS_BACKUP_NOW = "Backup now"
SETTINGS_BACKUPS_NO_BACKUPS = "No backups on file yet."
SETTINGS_BACKUPS_LIST_HEADER = "Existing backups"
SETTINGS_BACKUPS_RESTORE = "Restore…"
SETTINGS_BACKUPS_CONFIRM_LABEL = (
    "I understand this will overwrite all my current data."
)
SETTINGS_BACKUPS_CONFIRM_TITLE = "Confirm restore"
SETTINGS_BACKUPS_CONFIRM_BODY = (
    "Restore from {name}? Every entry in your current database "
    "will be replaced with the contents of that backup. This "
    "cannot be undone."
)
SETTINGS_BACKUPS_RESTORED = "Restored from {name}."
SETTINGS_BACKUPS_FOLDER_DIALOG_TITLE = "Choose backup folder"

# Notifications page
SETTINGS_NOTIFICATIONS_LABEL = "Notifications"
SETTINGS_NOTIFICATIONS_ENABLE = "Show tray notifications"
SETTINGS_NOTIFICATIONS_REMINDERS_ENABLE = "Periodic task reminders"
SETTINGS_NOTIFICATIONS_INTERVAL_LABEL = "Reminder interval (minutes)"
SETTINGS_NOTIFICATIONS_INTERVAL_SUFFIX = " min"

# Topics page -- keyword -> project auto-linking rules
SETTINGS_TOPICS_HEADER = "Topic keywords"
SETTINGS_TOPICS_NOTE = (
    "When a capture mentions one of a project's keywords, Alfred files "
    "it under that project. Plain case-insensitive substring matching, "
    "nothing clever. Saying “… to the X project” outright always wins."
)
SETTINGS_TOPICS_COL_PROJECT = "Project"
SETTINGS_TOPICS_COL_KEYWORDS = "Keywords (comma-separated)"
SETTINGS_TOPICS_ADD = "Add group"
SETTINGS_TOPICS_REMOVE = "Remove"
SETTINGS_TOPICS_EMPTY = "No keyword groups. Captures are never auto-filed."
SETTINGS_TOPICS_NEW_PROJECT = "New project"
SETTINGS_TOPICS_KEYWORDS_PLACEHOLDER = "esp32, sensor, arduino"

# Data page
SETTINGS_DATA_STATS_HEADER = "Database statistics"
SETTINGS_DATA_STAT_MEMORIES = "Total memories"
SETTINGS_DATA_STAT_NOTES = "Notes"
SETTINGS_DATA_STAT_INBOX = "Inbox items"
SETTINGS_DATA_STAT_TASKS = "Tasks"
SETTINGS_DATA_STAT_PROJECTS = "Projects"
SETTINGS_DATA_STAT_DB_SIZE = "Database file size"
SETTINGS_DATA_STAT_BACKUP_COUNT = "Backups on file"
SETTINGS_DATA_STAT_BACKUP_SIZE = "Total backup size"
SETTINGS_DATA_REFRESH = "Refresh"
SETTINGS_DATA_VACUUM = "Vacuum database"
SETTINGS_DATA_VACUUM_TITLE = "Vacuum database"
SETTINGS_DATA_VACUUM_CONFIRM_BODY = (
    "Vacuum the database? This rebuilds the file and may take a "
    "moment on large databases. Current size: {size_before}. "
    "Proceed?"
)
SETTINGS_DATA_VACUUM_DONE_BEFORE = "{size_before} → {size_after}"
SETTINGS_DATA_DANGER_HEADER = "Danger zone"
SETTINGS_DATA_DANGER_NOTE = (
    "These actions are permanent. Please be certain."
)
SETTINGS_DATA_RESET_LABEL = (
    "Type DELETE below to enable the reset button."
)
SETTINGS_DATA_RESET_PLACEHOLDER = "DELETE"
SETTINGS_DATA_RESET_BUTTON = "Reset all data"
SETTINGS_DATA_RESET_TITLE = "Reset all data"
SETTINGS_DATA_RESET_CONFIRM_BODY = (
    "Permanently delete every memory, tag, project, task, and "
    "link in this database? This cannot be undone."
)
SETTINGS_DATA_RESET_DONE = "All data has been cleared."

# About page
SETTINGS_ABOUT_NAME = "Alfred"
SETTINGS_ABOUT_TAGLINE = (
    "Your local-first personal knowledge base, kept on file "
    "without ceremony."
)
SETTINGS_ABOUT_VERSION_LABEL = "Version"
SETTINGS_ABOUT_VERSION = "0.1.0"
SETTINGS_ABOUT_CHALLENGE = (
    "Built as part of the 30 Days / 30 Projects challenge."
)

# Restore / storage restart notice
SETTINGS_RESTART_REQUIRED_TITLE = "Restart required"
SETTINGS_RESTART_REQUIRED_BODY = (
    "The storage location has been changed. Please restart "
    "Alfred for the new location to take effect."
)

# Reminder balloon
REMINDER_BALLOON_TITLE = "Alfred"
REMINDER_BALLOON_BODY = "{count} tasks need your attention."

# ---------------------------------------------------------------------------
# Butler interaction layer
# ---------------------------------------------------------------------------
# Templated output for the curated question shapes the assistant
# recognizes. These are *templates*, not generated language: every
# sentence below is written by hand and filled with real data. Same
# house style as the rest of this file -- short, dry, "sir" at the end,
# no exclamation marks.

# Transient states
ASSISTANT_THINKING = "Lemme check, sir."
ASSISTANT_ACTION_CONFIRM = "Certainly, sir."
ASSISTANT_ERROR = "I couldn't look that up, sir."

# Fallbacks / declines
ASSISTANT_UNKNOWN_FIELD = "I don't have that on file, sir."
ASSISTANT_DECLINE = "I'm not sure what you're after, sir."
ASSISTANT_NO_MATCH = "Nothing on file about {topic}, sir."
ASSISTANT_CONTEXT_NONE = "I'm not sure what you're referring to, sir."

# Capability list (no lookup -- this is a fixed answer)
ASSISTANT_CAPABILITIES = (
    "I keep your notes, tasks, and projects on file, sir. Say "
    "\u201cremember\u2026\u201d and I'll file it, or \u201cstart a new project "
    "called\u2026\u201d and I'll open one. Ask what's on file about a "
    "topic, what your tasks are for today, what's left on a project, "
    "when something was created, or what you worked on yesterday. "
    "\u201cOpen my tasks\u201d and the like will take you straight there."
)

# Single-fact profile answers
ASSISTANT_PROFILE_FACT = "Your {field} is {value}, sir."

# Dates
ASSISTANT_CREATED_AT = "You filed \u201c{title}\u201d on {when}, sir."
ASSISTANT_CREATED_UNKNOWN = "I have no record of that entry, sir."
ASSISTANT_LAST_WORKED = "You last touched \u201c{title}\u201d on {when}, sir."
ASSISTANT_YESTERDAY = "Yesterday you worked on {titles}, sir."
ASSISTANT_YESTERDAY_NONE = "Nothing was filed yesterday, sir."

# Topic search
ASSISTANT_SEARCH_SUMMARY = "{count} on {topic}, sir: {titles}."

# Tasks
ASSISTANT_TASKS_NONE = "Nothing {bucket_label}, sir."
ASSISTANT_TASKS_SUMMARY = "{count} {bucket_label}, sir: {titles}."
ASSISTANT_TASK_BUCKET_TODAY = "due today"
ASSISTANT_TASK_BUCKET_TOMORROW = "due tomorrow"
ASSISTANT_TASK_BUCKET_OPEN = "still open"

# Conversational task completion ("I finished the meeting")
ASSISTANT_TASK_DONE = "Marked “{title}” complete, sir."
# Two or more open tasks match: name them and ask, rather than picking
# one. Completing the wrong task is a silent data change the user has
# no reason to go looking for.
ASSISTANT_TASK_DONE_AMBIGUOUS = (
    "Several open tasks match that, sir: {titles}. Which one?"
)
ASSISTANT_TASK_DONE_NONE = "I don't see an open task matching that, sir."

# "What's left on X"
ASSISTANT_PENDING_NONE = "Nothing outstanding on {topic}, sir."
ASSISTANT_PENDING_SUMMARY = "{count} left on {topic}, sir: {titles}."

# Project status
ASSISTANT_PROJECT_STATUS = "{project} holds {memories} and {tasks}, sir."
ASSISTANT_PROJECT_STATUS_EMPTY = "{project} is empty, sir."
ASSISTANT_PROJECT_OUTSTANDING = " Still outstanding: {titles}."
ASSISTANT_PROJECT_ALL_DONE = " Nothing outstanding."
ASSISTANT_PROJECT_UNKNOWN = "I have no project by that name, sir."
ASSISTANT_PROJECT_UNKNOWN_NAMED = "I have no {project} project on file, sir."

# Project writes ("add a task to my ESP32 project: test the sensor")
ASSISTANT_PROJECT_ADDED = "Added to {project}, sir: “{title}”."
ASSISTANT_PROJECT_ADDED_NEW = (
    "Started the {project} project and added “{title}”, sir."
)

# Project creation ("start a new project called Batcave"). Saying the
# name back is the whole confirmation: it is the name the project was
# filed under, which is also the name every later question has to use.
ASSISTANT_PROJECT_STARTED = "Started the {project} project, sir."
# The name resolved to a project already on file. Not an error, and
# deliberately not a second project -- the existing one is named back
# so the user can see which one was matched.
ASSISTANT_PROJECT_EXISTS = "The {project} project is already on file, sir."

# Context follow-up ("what about it?")
ASSISTANT_CONTEXT_MEMORY = "That was \u201c{title}\u201d, last touched {when}, sir."

# Grammatical helpers
ASSISTANT_LIST_AND = "and"
ASSISTANT_LIST_MORE = "{count} more"
ASSISTANT_NOUN_MEMORY = "memory"
ASSISTANT_NOUN_MEMORIES = "memories"
ASSISTANT_NOUN_TASK = "task"
ASSISTANT_NOUN_TASKS = "tasks"
ASSISTANT_NOUN_ENTRY = "entry"
ASSISTANT_NOUN_ENTRIES = "entries"
ASSISTANT_NOUN_OPEN_TASK = "open task"
ASSISTANT_NOUN_OPEN_TASKS = "open tasks"

# Bubble chrome
ASSISTANT_ANSWER_AUTO_CLOSE_MS = 2200
ASSISTANT_ACTION_CLOSE_MS = 700
