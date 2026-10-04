"""Server setup tests: cron lines, token hand-off from the Mac, email alerts."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import autorun  # noqa: E402
import install_linux  # noqa: E402


class CronTests(unittest.TestCase):
    def test_adds_our_jobs_and_keeps_other_bots(self):
        existing = "0 7 * * * /other/bot.sh # other-bot\n1 2 * * * old safe-algo line # safe-algo\n"
        text = install_linux.merged_crontab(existing)
        self.assertIn("/other/bot.sh # other-bot", text)
        self.assertNotIn("old safe-algo line", text)
        ours = [l for l in text.splitlines() if l.endswith("# safe-algo")]
        self.assertEqual([l.split()[:5] for l in ours],
                         [["5", "9", "*", "*", "1-5"], ["50", "14", "*", "*", "1-5"],
                          ["57", "14", "*", "*", "1-5"], ["25", "18", "*", "*", "1-5"]])
        self.assertTrue(all("autorun.py" in l for l in ours))

    def test_uninstall_removes_only_ours(self):
        existing = "0 7 * * * /other/bot.sh # other-bot\n" + "\n".join(install_linux.cron_lines())
        self.assertEqual(install_linux.merged_crontab(existing, add=False), "0 7 * * * /other/bot.sh # other-bot\n")


class TokenHandoffTests(unittest.TestCase):
    def test_push_does_nothing_without_server(self):
        with mock.patch.dict(os.environ, {"SAFEALGO_SERVER": ""}), mock.patch.object(autorun.subprocess, "run") as run:
            self.assertFalse(autorun.push_token())
        run.assert_not_called()

    def test_push_copies_token_with_key(self):
        done = mock.Mock(returncode=0, stderr="")
        with mock.patch.dict(os.environ, {"SAFEALGO_SERVER": "ubuntu@65.0.244.16", "SAFEALGO_SSH_KEY": "/k.pem"}), \
                mock.patch.object(autorun.subprocess, "run", return_value=done) as run, \
                mock.patch.object(autorun, "notify"):
            self.assertTrue(autorun.push_token())
        cmd = run.call_args[0][0]
        self.assertEqual(cmd[0], "scp")
        self.assertIn("/k.pem", cmd)
        self.assertEqual(cmd[-1], "ubuntu@65.0.244.16:safe-algo/token.txt")

    def test_server_waits_for_token_instead_of_opening_a_login(self):
        tokens = iter(["", "", "fresh"])
        with mock.patch.object(autorun.sys, "platform", "linux"), \
                mock.patch.object(autorun.get_token, "read_token", lambda: next(tokens)), \
                mock.patch.object(autorun.get_token, "token_is_valid", lambda t: t == "fresh"), \
                mock.patch.object(autorun.get_token, "auto_login") as login, \
                mock.patch("time.sleep"), mock.patch.object(autorun, "notify") as note:
            self.assertEqual(autorun.ensure_token(10), "fresh")
        login.assert_not_called()
        self.assertIn("On your Mac", note.call_args[0][0])


class EmailTests(unittest.TestCase):
    def test_no_email_without_settings(self):
        with mock.patch.dict(os.environ, {"SMTP_USER": "", "SMTP_PASSWORD": ""}):
            self.assertFalse(autorun.send_email("hi"))

    def test_email_sent_with_app_password(self):
        env = {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "abcd efgh ijkl mnop", "NOTIFY_EMAIL": ""}
        with mock.patch.dict(os.environ, env), mock.patch.object(autorun.smtplib, "SMTP_SSL") as smtp:
            self.assertTrue(autorun.send_email("Bought 1 lot", "Safe-Algo Commodity"))
        session = smtp.return_value.__enter__.return_value
        session.login.assert_called_once_with("me@gmail.com", "abcdefghijklmnop")
        sent = session.send_message.call_args[0][0]
        self.assertEqual(sent["To"], "me@gmail.com")
        self.assertEqual(sent["Subject"], "Safe-Algo Commodity: Bought 1 lot")


class EmailFallbackTests(unittest.TestCase):
    def test_falls_back_to_starttls_when_ssl_is_cut(self):
        env = {"SMTP_USER": "me@gmail.com", "SMTP_PASSWORD": "abcdefghijklmnop"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(autorun.smtplib, "SMTP_SSL",
                                  side_effect=autorun.smtplib.SMTPServerDisconnected("closed")), \
                mock.patch.object(autorun.smtplib, "SMTP") as plain:
            self.assertTrue(autorun.send_email("hi"))
        plain.return_value.starttls.assert_called_once()
        plain.return_value.__enter__.return_value.send_message.assert_called_once()


class SesTests(unittest.TestCase):
    def test_server_alerts_go_through_ses(self):
        fake = mock.MagicMock()
        env = {"ALERT_VIA": "ses", "NOTIFY_EMAIL": "me@gmail.com", "SMTP_USER": "", "SMTP_PASSWORD": ""}
        with mock.patch.dict(os.environ, env), mock.patch.dict(sys.modules, {"boto3": fake}):
            self.assertTrue(autorun.send_email("Bought 1 lot", "Safe-Algo Commodity"))
        kw = fake.client.return_value.send_email.call_args.kwargs
        self.assertEqual(kw["Destination"], {"ToAddresses": ["me@gmail.com"]})
        self.assertEqual(kw["Content"]["Simple"]["Subject"]["Data"], "Safe-Algo Commodity: Bought 1 lot")

    def test_ses_failure_never_raises(self):
        fake = mock.MagicMock()
        fake.client.return_value.send_email.side_effect = RuntimeError("not verified")
        with mock.patch.dict(os.environ, {"ALERT_VIA": "ses", "NOTIFY_EMAIL": "me@gmail.com"}), \
                mock.patch.dict(sys.modules, {"boto3": fake}):
            self.assertFalse(autorun.send_email("hi"))


if __name__ == "__main__":
    unittest.main()
