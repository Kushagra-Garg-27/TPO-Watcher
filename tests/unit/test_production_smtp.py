import smtplib
import time
import pytest


class Server:
    def __init__(self, outcome='accepted', close_failure=False):
        self.outcome, self.close_failure, self.data_calls = outcome, close_failure, 0
    def ehlo(self): return (250, b'ok')
    def starttls(self, *, context): return (220, b'tls')
    def login(self, username, password): return (235, b'ok')
    def mail(self, sender): return (550 if self.outcome=='sender-rejected' else 250, b'ok')
    def rcpt(self, recipient): return (550 if self.outcome=='recipient-rejected' else 250, b'result')
    def data(self, value):
        self.data_calls += 1
        if self.outcome=='disconnected': raise smtplib.SMTPServerDisconnected('private provider text')
        if self.outcome=='refused': raise smtplib.SMTPDataError(550,b'private provider text')
        return (250,b'queued')
    def close(self):
        if self.close_failure: raise OSError('cannot confirm close')


def adapter(server):
    from app.notifications.production_smtp import SMTPTransport, SMTPSettings
    return SMTPTransport(SMTPSettings('smtp.example.invalid',587,'operator','external-secret','sender@example.invalid'),
        connector=lambda host,port,timeout:server)


@pytest.mark.parametrize('outcome,want',[('accepted','ACCEPTED'),('recipient-rejected','NOT_ACCEPTED'),
    ('refused','NOT_ACCEPTED'),('disconnected','UNKNOWN')])
def test_smtp_classifies_protocol_outcomes_without_unsafe_negative_on_lost_data_ack(outcome,want):
    server=Server(outcome)
    result=adapter(server).send('recipient@example.invalid','Subject','<p>Truthful</p>',{})
    assert result.kind==want and result.ended is True
    assert server.data_calls==int(outcome!='recipient-rejected')


def test_socket_close_failure_does_not_assert_physical_end():
    result=adapter(Server(close_failure=True)).send('recipient@example.invalid','Subject','<p>Truthful</p>',{})
    assert result.kind=='ACCEPTED' and result.ended is False


@pytest.mark.parametrize('outcome',['sender-rejected','recipient-rejected'])
def test_rejection_with_close_failure_never_fabricates_physical_end(outcome):
    server=Server(outcome,close_failure=True)
    result=adapter(server).send('recipient@example.invalid','Subject','<p>Truthful</p>',{})
    assert result.kind=='NOT_ACCEPTED' and result.ended is False
    assert server.data_calls==0


def test_invalid_headers_are_rejected_before_connecting():
    server=Server()
    with pytest.raises(ValueError):adapter(server).send('recipient@example.invalid','bad\nInjected','html',{})
    assert server.data_calls==0


def test_expired_authority_during_setup_prevents_data():
    clock = [1000]
    class ExpiringServer(Server):
        def rcpt(self, recipient):
            clock[0] = 2000
            return super().rcpt(recipient)
    server = ExpiringServer()
    result = adapter(server).send('recipient@example.invalid', 'Subject', 'html', {},
        deadline_monotonic=time.monotonic() + 1, authority_expires_ms=1500, clock=lambda: clock[0])
    assert result.kind == 'NOT_ACCEPTED' and result.ended is True
    assert server.data_calls == 0
