import pytest


def test_transport_identity_rejects_ambiguous_or_extra_wire_fields():
    from app.notifications.transport_protocol import InvocationIdentity
    data=dict(account="account-1",invocation="1"*32,submission="2"*32,authority="3"*32,
              gate_generation=1,epoch=1,delivery_id=None,attempt_generation=None)
    identity=InvocationIdentity.from_wire(data)
    assert identity.to_wire()==data
    for changes in ({"epoch":True},{"authority":""},{"gate_generation":0},{"delivery_id":1},
                    {"unknown":"value"}):
        with pytest.raises(ValueError):InvocationIdentity.from_wire(dict(data,**changes))


def test_ephemeral_mail_is_fictional_bounded_and_finite():
    from app.notifications.transport_protocol import EphemeralMail
    data=dict(message_class="ACCOUNT",destination="fictional@example.invalid",subject="Example",
              html="<p>finite example</p>",expires_ms=1001,headers={})
    message=EphemeralMail.from_wire(data)
    assert message.to_wire()==data
    for changes in ({"destination":"not-fictional@example.com"},{"expires_ms":True},
                    {"subject":"bad\nheader"},{"headers":{"Injected":"value"}},
                    {"html":"x"*65537},{"message_class":"OTHER"}):
        with pytest.raises(ValueError):EphemeralMail.from_wire(dict(data,**changes))


@pytest.mark.parametrize("changes",[{"kind":"ACCEPTED"},{"kind":"OTHER"},{"kind":"BUSY","ended":True},
                                  {"kind":"EXPIRED","invocation":"1"*32},{"kind":"UNKNOWN","invocation":"bad"},
                                  {"kind":"NOT_ACCEPTED","invocation":"1"*32,"ended":1},{"extra":1}])
def test_outcome_requires_exact_invocation_and_end_contract(changes):
    from app.notifications.transport_protocol import MailOutcome
    data=dict(kind="BUSY",invocation=None,ended=False)
    with pytest.raises(ValueError):MailOutcome.from_wire(dict(data,**changes))


def test_one_click_headers_are_https_paired_and_immutable():
    from app.notifications.transport_protocol import EphemeralMail
    headers={"List-Unsubscribe":"<https://watcher.example.invalid/api/v1/unsubscribe/one-click?token=fictional>",
             "List-Unsubscribe-Post":"List-Unsubscribe=One-Click"}
    data=dict(message_class="SUBSCRIBER",destination="fictional@example.invalid",subject="Example",
              html="<p>finite</p>",expires_ms=1001,headers=headers)
    mail=EphemeralMail.from_wire(data)
    headers.clear()
    assert len(mail.headers)==2
    with pytest.raises(TypeError):mail.headers["Injected"]="bad"
    for bad in ({},{"List-Unsubscribe-Post":"List-Unsubscribe=One-Click"},
                {"List-Unsubscribe":"<http://watcher.example.invalid/unsubscribe>","List-Unsubscribe-Post":"List-Unsubscribe=One-Click"}):
        with pytest.raises(ValueError):EphemeralMail.from_wire(dict(data,headers=bad))
