"""Guarded fictional key custody; never production key infrastructure."""
import pytest
import importlib.util
import json
import secrets
from dataclasses import replace
from pathlib import Path


class Authority:
    """Fictional external custody, recoverable only in a guarded test directory."""
    def __init__(self, path):
        self.path = Path(path)
        self.failure = None
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            "keys": {}, "actions": {}, "keks": {"1": secrets.token_hex(32)}, "generation": 1,"databases":{}}
        anchor=self.path.with_suffix('.anchor')
        if self.path.exists() and (not anchor.exists() or anchor.read_text()!=__import__('hashlib').sha256(self.path.read_bytes()).hexdigest()):
            from app.database.key_provider import KeyUnavailable
            raise KeyUnavailable('fictional external authority rollback or interrupted save')

    def save(self):
        target = self.path.with_suffix(".next")
        target.write_text(json.dumps(self.state))
        target.replace(self.path)
        self.path.with_suffix('.anchor').write_text(__import__('hashlib').sha256(self.path.read_bytes()).hexdigest())

    def schema_required(self,namespace):
        return self.state['databases'].get(namespace,{}).get('schema',False)

    def database_state(self,namespace):return dict(self.state['databases'][namespace])

    def resolve_migration(self,namespace,original,current,authorization,*,committed=True):
        from app.database.key_provider import KeyUnavailable
        row=self.state['databases'][namespace]
        if not row['pending'] and row.get('resolution')==authorization and row['fingerprint']==current:return
        if not row['pending'] or row['schema'] or row['fingerprint']!=original or not authorization:
            raise KeyUnavailable('migration recovery authorization or state rejected')
        self.state['databases'][namespace]=dict(fingerprint=current,pending=False,schema=committed,resolution=authorization)
        self.save()

    def check_database(self,namespace,fingerprint):
        from app.database.key_provider import KeyUnavailable
        row=self.state['databases'].get(namespace)
        if row and (row['pending'] or row['fingerprint']!=fingerprint):
            raise KeyUnavailable('fictional recovery freshness or commit unresolved')

    def begin_database(self,namespace,fingerprint):
        self.check_database(namespace,fingerprint)
        old=self.state['databases'].get(namespace,{})
        self.state['databases'][namespace]=dict(fingerprint=fingerprint,pending=True,schema=old.get('schema',False))
        self.save()

    def finish_database(self,namespace,fingerprint,schema):
        if self.failure=='finish_database':
            from app.database.key_provider import KeyUnavailable
            raise KeyUnavailable('fictional database acknowledgment lost')
        self.state['databases'][namespace]=dict(fingerprint=fingerprint,pending=False,schema=schema)
        self.save()

    def retire_generation(self,generation,*,recovery_acknowledged=False):
        from app.database.key_provider import KeyUnavailable
        if (not recovery_acknowledged or any(k['generation']==generation and not k['revoked'] for k in self.state['keys'].values())
                or any(a['generation']==generation and not a['acknowledged'] for a in self.state['actions'].values())):
            raise KeyUnavailable('fictional retirement evidence incomplete')
        self.state['keks'].pop(str(generation),None);self.save()

    def allocate(self, context, operation):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from app.database.key_provider import KeyGrant, KeyUnavailable
        if self.failure == "allocate": raise KeyUnavailable("allocation unavailable")
        ref = secrets.token_hex(16)
        key, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
        generation = self.state["generation"]
        wrapped = AESGCM(bytes.fromhex(self.state["keks"][str(generation)])).encrypt(nonce, key, ref.encode())
        self.state["keys"][ref] = dict(context=context.as_dict(), operation=operation,
            wrapped=wrapped.hex(), nonce=nonce.hex(), generation=generation, revoked=False)
        self.save()
        return KeyGrant(ref, key, generation)

    def unwrap(self, reference, context):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from app.database.key_provider import KeyGrant, KeyUnavailable
        row = self.state["keys"].get(reference)
        if self.failure == "unwrap" or not row or row["revoked"] or row["context"] != context.as_dict():
            raise KeyUnavailable("key unavailable or unauthorized")
        key = AESGCM(bytes.fromhex(self.state["keks"][str(row["generation"])])).decrypt(
            bytes.fromhex(row["nonce"]), bytes.fromhex(row["wrapped"]), reference.encode())
        return KeyGrant(reference, key, row["generation"])

    def expected(self, reference):
        from app.database.key_provider import PayloadContext, KeyUnavailable
        row = self.state["keys"].get(reference)
        if not row: raise KeyUnavailable("key unavailable")
        return PayloadContext(**row["context"])

    def prepare_erasure(self, action, context, reference):
        from app.database.key_provider import KeyUnavailable
        if self.failure == "prepare": raise KeyUnavailable("preparation unavailable")
        row = dict(context=context.as_dict(), reference=reference,
            generation=self.state["keys"][reference]["generation"], acknowledged=False)
        old = self.state["actions"].get(action)
        if old and {k:v for k,v in old.items() if k!="acknowledged"} != {k:v for k,v in row.items() if k!="acknowledged"}:
            raise KeyUnavailable("action conflict")
        if old is None: self.state["actions"][action] = row; self.save()

    def erasure(self, action):
        from app.database.key_provider import KeyUnavailable
        if action not in self.state["actions"]: raise KeyUnavailable("action unavailable")
        return dict(self.state["actions"][action])

    def revoke(self, action):
        from app.database.key_provider import KeyUnavailable
        row = self.state["actions"][action]
        if self.failure == "revoke": raise KeyUnavailable("revocation uncertain")
        self.state["keys"][row["reference"]]["revoked"] = True
        self.save()
        if self.failure == "ack": raise KeyUnavailable("revocation acknowledgment uncertain")
        row["acknowledged"] = True; self.save()
        return True

    def rewrap(self, reference, operation):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from app.database.key_provider import KeyUnavailable
        row = self.state["keys"][reference]
        if row.get("rotation") == operation: return row["generation"]
        grant = self.unwrap(reference, self.expected(reference))
        generation = self.state["generation"] + 1
        kek, nonce = secrets.token_bytes(32), secrets.token_bytes(12)
        wrapped = AESGCM(kek).encrypt(nonce, grant.key, reference.encode())
        if self.failure == "rewrap": raise KeyUnavailable("rotation interrupted")
        self.state["keks"][str(generation)] = kek.hex()
        row.update(generation=generation, nonce=nonce.hex(), wrapped=wrapped.hex(), rotation=operation)
        self.state["generation"] = generation; self.save()
        if self.failure == "rewrap_ack": raise KeyUnavailable("rotation acknowledgment uncertain")
        return generation


def implementation():
    assert importlib.util.find_spec("app.database.protected_payloads") is not None, "protected codec missing"
    from app.database.protected_payloads import ProtectedPayloads
    return ProtectedPayloads


def test_codec_authenticated_context_and_no_plaintext(tmp_path):
    Codec = implementation()
    from app.database.key_provider import PayloadContext, KeyUnavailable
    authority = Authority(tmp_path / "keys.json")
    context = PayloadContext(str(tmp_path / "db.sqlite"), "payload-a", "SOURCE", "a"*64)
    codec = Codec(authority, context.namespace)
    sealed = codec.encrypt(context, {"fictional": "private"})
    assert b"private" not in sealed.blob
    assert codec.decrypt(context, sealed.blob, sealed.reference) == {"fictional": "private"}
    for field, value in (("namespace", str(tmp_path / "other.sqlite")), ("payload_id", "payload-b"),
                         ("purpose", "SNAPSHOT"), ("owner_digest", "b"*64)):
        with pytest.raises(KeyUnavailable): codec.decrypt(replace(context, **{field:value}), sealed.blob, sealed.reference)
    with pytest.raises(KeyUnavailable): codec.decrypt(context, b'{"plaintext":true}', sealed.reference)


def test_installed_aesgcm_roundtrip_and_tamper_rejection():
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = AESGCM.generate_key(bit_length=256)
    cipher = AESGCM(key)
    nonce, body, aad = b"fictional-12", b"fictional source", b"fictional binding"
    ciphertext = cipher.encrypt(nonce, body, aad)
    assert ciphertext != body
    assert cipher.decrypt(nonce, ciphertext, aad) == body
    corrupted = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
    with pytest.raises(InvalidTag):
        cipher.decrypt(nonce, corrupted, aad)


@pytest.mark.parametrize('part',['ciphertext','tag','nonce','version','algorithm','reference','namespace','payload_id','purpose','owner_digest','truncated','plaintext','extra'])
def test_v7_envelope_tamper_and_plaintext_are_rejected(tmp_path,part):
    Codec=implementation()
    from app.database.key_provider import PayloadContext,KeyUnavailable
    from app.database.protected_payloads import PREFIX,canonical
    authority=Authority(tmp_path/'keys.json');codec=Codec(authority,str(tmp_path/'db.sqlite'))
    ctx=PayloadContext(codec.namespace,'payload','SNAPSHOT','a'*64)
    sealed=codec.encrypt(ctx,{'private':'fictional'})
    value=json.loads(sealed.blob[len(PREFIX):])
    if part in ('ciphertext','tag','nonce'):
        field='nonce' if part=='nonce' else 'ciphertext'
        raw=bytearray.fromhex(value[field]);raw[-1 if part=='tag' else 0]^=1;value[field]=raw.hex()
    elif part=='extra':value['ignored']='not allowed'
    elif part not in ('plaintext','truncated'):value['header'][part]=99 if part=='version' else 'swapped'
    blob=b'{}' if part=='plaintext' else sealed.blob[:20] if part=='truncated' else PREFIX+canonical(value)
    with pytest.raises(KeyUnavailable):codec.decrypt(ctx,blob,sealed.reference)


@pytest.mark.parametrize('failure',['missing','revoked','unauthorized','bad_size'])
def test_v7_missing_revoked_unauthorized_and_bad_keys_fail_closed(tmp_path,failure):
    Codec=implementation()
    from app.database.key_provider import PayloadContext,KeyUnavailable
    authority=Authority(tmp_path/'keys.json');codec=Codec(authority,str(tmp_path/'db.sqlite'))
    ctx=PayloadContext(codec.namespace,'payload','SOURCE','a'*64)
    sealed=codec.encrypt(ctx,{'fictional':True})
    if failure=='missing':del authority.state['keys'][sealed.reference]
    elif failure=='revoked':authority.state['keys'][sealed.reference]['revoked']=True
    elif failure=='unauthorized':authority.failure='unwrap'
    else:
        original=authority.unwrap
        authority.unwrap=lambda ref,context:replace(original(ref,context),key=b'bad')
    with pytest.raises(KeyUnavailable):codec.decrypt(ctx,sealed.blob,sealed.reference)


@pytest.mark.parametrize('failure',[None,'rewrap','rewrap_ack'])
def test_v8_external_rewrap_recovery_preserves_payload_ciphertext(tmp_path,failure):
    Codec=implementation()
    from app.database.key_provider import PayloadContext,KeyUnavailable
    authority=Authority(tmp_path/'keys.json');codec=Codec(authority,str(tmp_path/'db.sqlite'))
    ctx=PayloadContext(codec.namespace,'payload','SOURCE','a'*64)
    sealed=codec.encrypt(ctx,{'fictional':'source'})
    authority.failure=failure
    if failure:
        with pytest.raises(KeyUnavailable):authority.rewrap(sealed.reference,'rotation-a')
    else:authority.rewrap(sealed.reference,'rotation-a')
    recovered=Authority(authority.path)
    generation=recovered.rewrap(sealed.reference,'rotation-a')
    assert recovered.rewrap(sealed.reference,'rotation-a')==generation
    assert Codec(recovered,ctx.namespace).decrypt(ctx,sealed.blob,sealed.reference)=={'fictional':'source'}
    with pytest.raises(KeyUnavailable,match='schema'):codec.rotate_data_key(ctx.payload_id)


def test_v7_each_encryption_allocates_new_record_key_and_nonce(tmp_path):
    Codec=implementation()
    from app.database.key_provider import PayloadContext
    from app.database.protected_payloads import PREFIX
    authority=Authority(tmp_path/'keys.json');codec=Codec(authority,str(tmp_path/'db.sqlite'))
    references=set();nonces=set();keys=set()
    for index in range(20):
        ctx=PayloadContext(codec.namespace,str(index),'SOURCE','a'*64)
        sealed=codec.encrypt(ctx,{'same':'fictional content'})
        references.add(sealed.reference);nonces.add(json.loads(sealed.blob[len(PREFIX):])['nonce'])
        keys.add(authority.unwrap(sealed.reference,ctx).key)
    assert len(references)==len(nonces)==len(keys)==20


def test_v7_duplicate_envelope_header_fields_are_rejected(tmp_path):
    Codec=implementation()
    from app.database.key_provider import PayloadContext,KeyUnavailable
    authority=Authority(tmp_path/'keys.json');codec=Codec(authority,str(tmp_path/'db.sqlite'))
    ctx=PayloadContext(codec.namespace,'payload','SOURCE','a'*64)
    sealed=codec.encrypt(ctx,{'fictional':True})
    ambiguous=sealed.blob.replace(b'"algorithm":"AES-256-GCM"',b'"algorithm":"UNKNOWN","algorithm":"AES-256-GCM"')
    with pytest.raises(KeyUnavailable):codec.decrypt(ctx,ambiguous,sealed.reference)


def test_v8_old_wrapping_key_retirement_requires_declared_recovery_evidence(tmp_path):
    Codec=implementation()
    from app.database.key_provider import PayloadContext,KeyUnavailable
    authority=Authority(tmp_path/'keys.json');codec=Codec(authority,str(tmp_path/'db.sqlite'))
    ctx=PayloadContext(codec.namespace,'payload','SOURCE','a'*64)
    sealed=codec.encrypt(ctx,{'fictional':True})
    with pytest.raises(KeyUnavailable):authority.retire_generation(1,recovery_acknowledged=True)
    authority.rewrap(sealed.reference,'rotation')
    with pytest.raises(KeyUnavailable):authority.retire_generation(1)
    authority.retire_generation(1,recovery_acknowledged=True)
    assert '1' not in Authority(authority.path).state['keks']
    assert codec.decrypt(ctx,sealed.blob,sealed.reference)=={'fictional':True}
