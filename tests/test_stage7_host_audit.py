import unittest

from tools.stage7_host_audit import inbound_rules


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


if __name__ == "__main__":
    unittest.main()
