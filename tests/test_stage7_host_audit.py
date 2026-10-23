import unittest
from unittest.mock import patch

from tools.stage7_host_audit import audit, inbound_rules


class UfwOutputTests(unittest.TestCase):
    def test_exact_ssh_rule_and_extra_rules_are_visible(self):
        status = """Status: active
Default: deny (incoming), allow (outgoing), deny (routed)
To                         Action      From
--                         ------      ----
22/tcp                     ALLOW IN    192.168.45.1
80/tcp                     ALLOW IN    Anywhere
"""
        self.assertEqual(inbound_rules(status),
                         [("22/tcp", "192.168.45.1"), ("80/tcp", "Anywhere")])

    def test_docker_list_failure_cannot_pass_as_zero_published_ports(self):
        def fake_command(*args):
            if args[:2] == ("docker", "info"):
                return 0, "json-file", ""
            if args == ("docker", "ps", "-q"):
                return 1, "", "permission denied"
            if args[:3] == ("sudo", "-n", "ufw"):
                return 0, ("Status: active\nLogging: on (low)\n"
                           "Default: deny (incoming), allow (outgoing), deny (routed)\n"
                           "22/tcp ALLOW IN 192.168.45.1"), ""
            if args[0] == "timedatectl":
                return 0, "yes", ""
            self.fail(f"unexpected command: {args}")

        with patch("tools.stage7_host_audit.command", side_effect=fake_command):
            report = audit("192.168.45.1")
        self.assertTrue(report["docker_access"])
        self.assertFalse(report["docker_inventory_complete"])
        self.assertEqual(report["published_ports"], [])
        self.assertIn("docker ps: permission denied", report["errors"])


if __name__ == "__main__":
    unittest.main()
