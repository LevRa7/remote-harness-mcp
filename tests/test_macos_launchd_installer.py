"""macOS launchd logging and health-check diagnostics in install.sh.

On macOS, launchd swallows stdout/stderr unless StandardOutPath and
StandardErrorPath are declared in the LaunchAgent plist, and systemctl /
journalctl do not exist when the standalone health gate fails.
"""

import os
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL_SH = os.path.join(REPO, "install.sh")


def _read_install_sh() -> str:
    with open(INSTALL_SH, "r", encoding="utf-8") as handle:
        return handle.read()


class TestMacOSLaunchdInstaller(unittest.TestCase):
    def test_install_sh_syntax_is_valid(self):
        result = subprocess.run(
            ["bash", "-n", INSTALL_SH],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_standalone_plist_declares_log_paths(self):
        text = _read_install_sh()
        start = text.index("com.remoteharness.mcp.standalone.plist")
        end = text.index("</plist>", start)
        block = text[start:end]
        self.assertIn("<key>StandardOutPath</key>", block)
        self.assertIn("<key>StandardErrorPath</key>", block)
        self.assertIn("<string>$CONFIG_DIR/standalone.log</string>", block)

    def test_agent_plist_declares_log_paths(self):
        text = _read_install_sh()
        start = text.index("com.remoteharness.mcp.agent.plist")
        end = text.index("</plist>", start)
        block = text[start:end]
        self.assertIn("<key>StandardOutPath</key>", block)
        self.assertIn("<key>StandardErrorPath</key>", block)
        self.assertIn("<string>$CONFIG_DIR/agent.log</string>", block)

    def test_standalone_health_error_handles_darwin_in_ru_and_en(self):
        text = _read_install_sh()
        start = text.index('if [ "$HEALTH_OK" != true ]; then')
        end = text.index("exit 1", start)
        block = text[start:end]
        self.assertEqual(block.count('elif [ "$(uname -s)" = "Darwin" ]; then'), 2)
        self.assertEqual(block.count("launchctl list | grep remoteharness"), 2)
        self.assertEqual(block.count("$CONFIG_DIR/standalone.log"), 2)


if __name__ == "__main__":
    unittest.main()
