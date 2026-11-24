"""Unit tests for screen capture privacy exclusion and display affinity management."""
import sys
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QSettings

from ui import privacy
from ui.privacy import (
    STATUS_DISABLED,
    STATUS_ENABLED,
    STATUS_FAILURE,
    STATUS_UNSUPPORTED,
    WDA_EXCLUDEFROMCAPTURE,
    WDA_NONE,
    get_window_display_affinity,
    is_display_affinity_supported,
    set_window_capture_exclusion,
)


class TestScreenCapturePrivacy(unittest.TestCase):
    def setUp(self):
        # Save original _user32
        self.orig_user32 = privacy._user32

    def tearDown(self):
        privacy._user32 = self.orig_user32

    def test_enable_capture_exclusion_success(self):
        """Test enabling exclusion successfully calls SetWindowDisplayAffinity with 0x11 and verifies."""
        mock_user32 = MagicMock()
        mock_user32.SetWindowDisplayAffinity.return_value = 1

        def mock_get(hwnd, ptr):
            ptr.contents.value = WDA_EXCLUDEFROMCAPTURE
            return 1

        mock_user32.GetWindowDisplayAffinity.side_effect = mock_get
        privacy._user32 = mock_user32

        with patch.object(privacy, "is_display_affinity_supported", return_value=True):
            ok, status, err = set_window_capture_exclusion(12345, True)

            self.assertTrue(ok)
            self.assertEqual(status, STATUS_ENABLED)
            self.assertEqual(err, 0)
            mock_user32.SetWindowDisplayAffinity.assert_called_once()
            args, _ = mock_user32.SetWindowDisplayAffinity.call_args
            self.assertEqual(args[1], WDA_EXCLUDEFROMCAPTURE)

    def test_disable_capture_exclusion_success(self):
        """Test disabling exclusion calls SetWindowDisplayAffinity with 0x00 and verifies."""
        mock_user32 = MagicMock()
        mock_user32.SetWindowDisplayAffinity.return_value = 1

        def mock_get(hwnd, ptr):
            ptr.contents.value = WDA_NONE
            return 1

        mock_user32.GetWindowDisplayAffinity.side_effect = mock_get
        privacy._user32 = mock_user32

        with patch.object(privacy, "is_display_affinity_supported", return_value=True):
            ok, status, err = set_window_capture_exclusion(12345, False)

            self.assertTrue(ok)
            self.assertEqual(status, STATUS_DISABLED)
            self.assertEqual(err, 0)
            mock_user32.SetWindowDisplayAffinity.assert_called_once()
            args, _ = mock_user32.SetWindowDisplayAffinity.call_args
            self.assertEqual(args[1], WDA_NONE)

    def test_api_failure_set_returns_zero(self):
        """Test API failure when SetWindowDisplayAffinity returns 0."""
        mock_user32 = MagicMock()
        mock_user32.SetWindowDisplayAffinity.return_value = 0
        privacy._user32 = mock_user32

        with patch.object(privacy, "is_display_affinity_supported", return_value=True):
            with patch("ctypes.get_last_error", return_value=5):
                ok, status, err = set_window_capture_exclusion(12345, True)

                self.assertFalse(ok)
                self.assertEqual(status, STATUS_FAILURE)
                self.assertEqual(err, 5)

    def test_api_failure_verification_mismatch(self):
        """Test failure when GetWindowDisplayAffinity reports a mismatch (never claim success if unverified)."""
        mock_user32 = MagicMock()
        mock_user32.SetWindowDisplayAffinity.return_value = 1

        # Simulate read-back returning WDA_NONE instead of WDA_EXCLUDEFROMCAPTURE
        def mock_get(hwnd, ptr):
            ptr.contents.value = WDA_NONE
            return 1

        mock_user32.GetWindowDisplayAffinity.side_effect = mock_get
        privacy._user32 = mock_user32

        with patch.object(privacy, "is_display_affinity_supported", return_value=True):
            ok, status, err = set_window_capture_exclusion(12345, True)

            self.assertFalse(ok)
            self.assertEqual(status, STATUS_FAILURE)

    def test_unsupported_system(self):
        """Test behavior when system does not support display affinity."""
        with patch.object(privacy, "is_display_affinity_supported", return_value=False):
            ok, status, err = set_window_capture_exclusion(12345, True)
            self.assertFalse(ok)
            self.assertEqual(status, STATUS_UNSUPPORTED)
            self.assertEqual(err, -1)

    def test_non_windows_safety(self):
        """Ensure functions do not crash if platform is simulated as non-Windows."""
        with patch("sys.platform", "linux"):
            self.assertFalse(privacy.is_display_affinity_supported())
            ok, status, err = privacy.set_window_capture_exclusion(12345, True)
            self.assertFalse(ok)
            self.assertEqual(status, STATUS_UNSUPPORTED)

    def test_preference_persistence(self):
        """Verify QSettings persistence for privacy and window settings."""
        settings = QSettings("MeetingAssistantTest", "DesktopApp")
        settings.clear()

        # Initial should be default
        self.assertFalse(settings.value("privacy/hide_from_screen_capture", False, type=bool))
        self.assertFalse(settings.value("window/always_on_top", False, type=bool))

        # Save preferences
        settings.setValue("privacy/hide_from_screen_capture", True)
        settings.setValue("window/always_on_top", True)

        # Reload and check
        reloaded = QSettings("MeetingAssistantTest", "DesktopApp")
        self.assertTrue(reloaded.value("privacy/hide_from_screen_capture", False, type=bool))
        self.assertTrue(reloaded.value("window/always_on_top", False, type=bool))

        settings.clear()


if __name__ == "__main__":
    unittest.main()
