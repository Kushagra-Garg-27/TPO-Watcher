"""Bounded typed contracts for the guard-confined R2a experiment, never SMTP."""
from dataclasses import asdict, dataclass
import json
import re
from types import MappingProxyType


def opaque(value, length=32):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{"+str(length)+r"}",value):
        raise ValueError("exact opaque identity required")
    return value


def integer(value, minimum=0):
    if type(value) is not int or not minimum<=value<=2**63-1:
        raise ValueError("bounded integer required")
    return value


def wire(data, fields):
    if type(data) is not dict or set(data)!=set(fields):raise ValueError("exact wire fields required")
    encoded=json.dumps(data,allow_nan=False,separators=(",",":"))
    if len(encoded.encode())>131072:raise ValueError("bounded IPC frame required")
    return data


@dataclass(frozen=True)
class InvocationIdentity:
    account: str
    invocation: str
    submission: str
    authority: str
    gate_generation: int
    epoch: int
    delivery_id: int | None
    attempt_generation: int | None

    def __post_init__(self):
        if type(self.account) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",self.account):raise ValueError("account identity required")
        for key in ("invocation","submission","authority"):opaque(getattr(self,key))
        integer(self.gate_generation,1)
        integer(self.epoch)
        if (self.delivery_id is None)!=(self.attempt_generation is None):raise ValueError("paired delivery/attempt required")
        if self.delivery_id is not None:
            integer(self.delivery_id,1)
            integer(self.attempt_generation,1)

    def to_wire(self):return asdict(self)
    @classmethod
    def from_wire(cls,data):return cls(**wire(data,cls.__dataclass_fields__))


@dataclass(frozen=True)
class EphemeralMail:
    message_class: str
    destination: str
    subject: str
    html: str
    expires_ms: int
    headers: dict

    def __post_init__(self):
        if self.message_class not in ("ADMIN","SUBSCRIBER","ACCOUNT"):raise ValueError("mail class required")
        if type(self.destination) is not str or not re.fullmatch(r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.invalid",self.destination) or len(self.destination)>254:
            raise ValueError("fictional destination required")
        if type(self.subject) is not str or not 0<len(self.subject)<=256 or any(c in self.subject for c in "\r\n\x00"):
            raise ValueError("safe bounded subject required")
        if type(self.html) is not str or len(self.html.encode())>65536:raise ValueError("bounded ephemeral HTML required")
        integer(self.expires_ms,1)
        if type(self.headers) is not dict or set(self.headers)-{"List-Unsubscribe","List-Unsubscribe-Post"}:raise ValueError("supported headers required")
        if any(type(k) is not str or type(v) is not str or len(v)>2048 or any(c in v for c in "\r\n\x00") for k,v in self.headers.items()):
            raise ValueError("safe header values required")
        if "List-Unsubscribe-Post" in self.headers and (self.headers["List-Unsubscribe-Post"]!="List-Unsubscribe=One-Click" or "List-Unsubscribe" not in self.headers):raise ValueError("one-click header contract")
        if "List-Unsubscribe" in self.headers and not re.fullmatch(r"<https://[^<>\s]+>",self.headers["List-Unsubscribe"]):raise ValueError("HTTPS unsubscribe header URL required")
        if self.message_class=="SUBSCRIBER" and set(self.headers)!={"List-Unsubscribe","List-Unsubscribe-Post"}:raise ValueError("subscriber one-click headers required")
        object.__setattr__(self,"headers",MappingProxyType(dict(self.headers)))

    def to_wire(self):return dict(message_class=self.message_class,destination=self.destination,subject=self.subject,
                                html=self.html,expires_ms=self.expires_ms,headers=dict(self.headers))
    @classmethod
    def from_wire(cls,data):return cls(**wire(data,cls.__dataclass_fields__))


@dataclass(frozen=True)
class MailOutcome:
    kind: str
    invocation: str | None = None
    ended: bool = False

    def __post_init__(self):
        if self.kind not in ("ACCEPTED","NOT_ACCEPTED","UNKNOWN","BUSY","EXPIRED"):raise ValueError("typed outcome required")
        if type(self.ended) is not bool:raise ValueError("explicit END required")
        if self.invocation is not None:opaque(self.invocation)
        if self.kind in ("BUSY","EXPIRED") and self.invocation is not None:raise ValueError("unadmitted result has no invocation")
        if self.kind in ("BUSY","EXPIRED") and self.ended:raise ValueError("unadmitted result has no physical END")
        if self.kind in ("ACCEPTED","NOT_ACCEPTED","UNKNOWN") and self.invocation is None:raise ValueError("exact outcome invocation required")

    def __bool__(self):return self.kind=="ACCEPTED"
    def to_wire(self):return asdict(self)
    @classmethod
    def from_wire(cls,data):return cls(**wire(data,cls.__dataclass_fields__))
