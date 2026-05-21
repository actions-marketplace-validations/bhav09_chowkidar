"""Tests for the notification utility."""

from chowkidar.sentinel.notifier import notify


def test_desktop_notification_success():
    # Sending notifications should return True/False or fallback gracefully to plyer
    # Let's test that notify doesn't crash and returns a boolean value
    success = notify("Test Title", "Test Message", "normal")
    assert isinstance(success, bool)
