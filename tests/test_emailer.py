from lab.config import Email
from lab import emailer


def test_send_noop_when_unconfigured():
    assert emailer.send(Email(), "subj", "<p>hi</p>") is False


def test_build_message_has_html_alternative():
    email = Email(host="smtp.example.com", user="me@example.com", password="pw",
                  to="you@example.com", from_="me@example.com")
    msg = emailer.build_message(email, "Subject", "<h1>Body</h1>", "plain")
    assert msg["Subject"] == "Subject"
    assert msg["To"] == "you@example.com"
    payloads = [p.get_content_type() for p in msg.walk()]
    assert "text/html" in payloads


class _FakeSMTP:
    def __init__(self):
        self.logged_in = False
        self.sent = None
    def starttls(self): pass
    def login(self, u, p): self.logged_in = True
    def send_message(self, msg): self.sent = msg
    def quit(self): pass


def test_send_uses_injected_smtp():
    email = Email(host="smtp.example.com", user="me@example.com", password="pw",
                  to="you@example.com", from_="me@example.com")
    fake = _FakeSMTP()
    ok = emailer.send(email, "Subject", "<p>hi</p>", smtp=fake)
    assert ok is True
    assert fake.logged_in is True
    assert fake.sent is not None
