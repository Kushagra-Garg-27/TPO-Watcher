"""Bounded STARTTLS transport. Construction never connects to a provider."""
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import parseaddr
import smtplib
import ssl
import time

from app.monitoring.notification_pipeline import TransportResult


def mailbox(value):
    if (not isinstance(value,str) or len(value)>254 or '\r' in value or '\n' in value
            or parseaddr(value)[1] != value or value.count('@') != 1 or ' ' in value):
        raise ValueError('single verified mailbox required')
    return value


@dataclass(frozen=True)
class SMTPSettings:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    sender: str
    timeout_seconds: int = 15

    def __post_init__(self):
        if (not isinstance(self.host,str) or not self.host or any(c.isspace() for c in self.host)
                or type(self.port) is not int or not 1 <= self.port <= 65535
                or not self.username or not self.password
                or type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 30):
            raise ValueError('explicit injected SMTP settings and bounded timeout required')
        mailbox(self.sender)


class SMTPTransport:
    def __init__(self, settings, *, connector=None):
        if not isinstance(settings,SMTPSettings): raise ValueError('typed SMTP settings required')
        self.settings, self.connector = settings, connector

    def send(self, destination, subject, html, headers, *, deadline_monotonic=None,
             authority_expires_ms=None, clock=None, cancelled=None, authorize_data=None):
        mailbox(destination)
        if (not isinstance(subject,str) or not subject or len(subject)>500 or '\r' in subject or '\n' in subject
                or not isinstance(html,str) or len(html.encode())>1048576 or not isinstance(headers,dict)
                or set(headers)-{'List-Unsubscribe','List-Unsubscribe-Post'}
                or any(not isinstance(v,str) or '\r' in v or '\n' in v for v in headers.values())):
            raise ValueError('bounded mail content and safe headers required')
        message=EmailMessage()
        message['From'],message['To'],message['Subject']=self.settings.sender,destination,subject
        for key,value in headers.items(): message[key]=value
        message.set_content('Please view the HTML alternative for this notification.')
        message.add_alternative(html,subtype='html')
        server=None; data_entered=False; ended=True; kind='NOT_ACCEPTED'
        deadline = time.monotonic() + self.settings.timeout_seconds if deadline_monotonic is None else deadline_monotonic
        wall_clock = clock or (lambda: int(time.time() * 1000))
        def remaining():
            value = deadline - time.monotonic()
            if (value <= 0 or cancelled is not None and cancelled.is_set()
                    or authority_expires_ms is not None and authority_expires_ms <= wall_clock()):
                raise TimeoutError('physical dispatch authority expired')
            return min(self.settings.timeout_seconds, value)
        def operation(method, *args, **kwargs):
            timeout = remaining()
            sock = getattr(server, 'sock', None)
            if sock is not None:
                sock.settimeout(timeout)
            result = method(*args, **kwargs)
            remaining()
            return result
        try:
            server=(self.connector or smtplib.SMTP)(self.settings.host,self.settings.port,timeout=remaining())
            remaining()
            operation(server.ehlo)
            operation(server.starttls, context=ssl.create_default_context())
            operation(server.ehlo)
            operation(server.login,self.settings.username,self.settings.password)
            code,_=operation(server.mail,self.settings.sender)
            if code==250:
                code,_=operation(server.rcpt,destination)
                if code in (250,251):
                    if authorize_data is not None and not authorize_data():
                        raise TimeoutError('current DATA authorization rejected')
                    remaining()
                    data_entered=True
                    code,_=operation(server.data,message.as_bytes())
                    kind='ACCEPTED' if code==250 else 'NOT_ACCEPTED'
        except smtplib.SMTPDataError:
            kind='NOT_ACCEPTED'  # A received final negative DATA reply is definitive.
        except Exception:
            kind='UNKNOWN' if data_entered else 'NOT_ACCEPTED'
        finally:
            if server is not None:
                try: server.close()
                except Exception: ended=False
        return TransportResult(kind,ended)
