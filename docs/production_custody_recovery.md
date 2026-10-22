# Production custody and database recovery

This procedure is prepared for the consolidated owner checkpoint. It has been
rehearsed only with fictional bootstrap-confined data. It is not authorization
to access production data, migrate the production volume or enable SMTP.

## Recovery dependencies

Keep these separately provisioned and inventoried:

1. The existing application SQLite volume, including its current WAL state.
2. An owner-only encrypted custody vault on a persistent custody mount.
3. The independent durable checkpoint SQLite file, on the custody recovery
   volume. Never replace it with a checkpoint from an application backup.
4. Raw 32-byte wrapping keys, one file per generation, injected from separate
   secret custody. The image and repository contain no keys. Unix key files
   must have no group/other permission bits; provision a restrictive Windows
   ACL when operating on Windows. Keep the directories owner-only and do not
   expose them through HTTP or application logs.
5. The approved deployment/mount identity, recovery manifests and authorization
   references. A supplied reference records trusted operator admission; it is
   not a signature or a substitute for the owner checkpoint.

The provider encrypts both the key vault and per-payload data keys. Its OS lock
serializes mutations, while the independent compare-and-swap checkpoint rejects
an older vault, including erased keys restored from a stale backup. Every
database unit records its starting fingerprint, pending state and intended
commit fingerprint. Payload grants are revalidated under that durable fence.
An unresolved unit blocks ordinary access and key revocation/rotation.

The S1 SQL checksum and representation strings remain unchanged. Those strings
do not authorize production operation. Production uses a separately scoped
exact database authority, protected-payload binding, verified account schema
and durable `production:binding` enrollment. Only connections registered by
that exact authority are admitted; attached databases and replaced files fail.
The fictional connection guard and `connect_disposable` remain confined to
the pre-import test isolation root.

## Approved migration order

After owner approval, independently verify the current instance, image,
container, volume/mount, database path, schema/prerequisites and all sender
processes. Stop the legacy writer and all legacy and new physical senders.
Preserve unresolved transport journals; stopping a controller is not proof that
a physical sender ended. Record quiescence and current file/mount identity.

Use the final release image's module entry point, with explicit absolute paths
and reviewed values. `--database-sha256` is the operator-verified current main
file hash; SQLite online backup also captures committed WAL content. Supply
`--wrapping-key GENERATION=ABSOLUTE_SECRET_FILE` once per required generation.

```text
python -m app.database.production_maintenance migrate
  --database <verified-existing-database>
  --database-sha256 <verified-current-main-file-sha256>
  --vault <persistent-custody-vault>
  --checkpoint <independent-current-checkpoint-file>
  --wrapping-key <generation>=<mounted-secret-file>
  --active-generation <generation>
  --initialize-vault
  --recovery-directory <new-owner-only-recovery-directory>
  --policy-reference <approved-no-send-policy-reference>
  --quiescence-reference <verified-stopped-writers-and-senders-reference>
  --approval-reference <owner-release-approval-reference>
```

Use `--initialize-vault` only when explicitly provisioning new custody. An
existing vault is opened and authenticated instead of replaced. A missing vault
with an existing checkpoint is rejected. Repeated migration verifies the
installed schema and preserves identities; it never reseeds obligations.

The command performs SQLite backup through its backup API while holding an
exclusive writer reservation, verifies integrity/FKs and logical content,
records mount/file identity and hashes, exports encrypted custody bytes, then
applies additive S1 schema and account policy under protected units. Legacy
subscribers remain unreviewed, without granted consent or a verified
notification destination. `SENT`, `ACK`, `UNKNOWN` and `ACK_FAILED` remain
historical-only. Pending legacy obligations remain held for review. No source
event, notification intent, SMTP invocation or physical sending is enabled.

Success returns `READY_NO_SEND`. Run the `verify` operation with the same explicit
database/custody arguments and current hash before starting the reviewed
runtime. A successful migration is not proof of production source access,
ARM64 acceptance, SMTP delivery or permission to activate a pilot.

## Recovery and shutdown

The `backup` operation uses a new recovery directory and requires stopped-writer
evidence. Every manifest identifies its encrypted custody export and current
checkpoint. Preserve the current independent checkpoint and the required
wrapping-key generations through separate recovery custody.

`restore_to_new_path(manifest, target)` verifies the SQLite artifact and creates
only a new inspection file. It rejects an existing file, WAL, SHM or rollback
journal. It never replaces the live database. The restored database cannot be
adopted as an active namespace automatically. Compare with current consent and
delivery journals and arrange reviewed forward recovery before activation.
Never copy an older SQLite file over newer consent or transport facts.

After an interrupted vault write whose checkpoint acknowledgement was lost,
`ProductionKeyProvider.recover_checkpoint` can advance only one authenticated
vault revision whose predecessor exactly matches the current checkpoint. It
cannot accept an older revision or reset the anchor. Then use:

- `reconcile-migration` for an interrupted initial S1 installation. It verifies
  the original legacy fingerprint, full schema, protected baseline contents,
  immutable migration manifest and unactivated runtime before acknowledgement.
- `reconcile-unit` for a later pending unit. It accepts only the exact durable
  starting or staged fingerprint and never writes the SQLite data. Any extra
  consent/delivery change is rejected and stays held for investigation.

Both operations require an exact maintenance authority and a fresh reviewed
approval reference. They do not release blocked physical senders, manufacture
SMTP acceptance or restore old consent. Run `verify` after recovery. Runtime
physical shutdown must remain held until current transport evidence proves the
sender cannot still act. Rolling back an image does not reverse migration.

Wrapping-key rotation requires a newly injected generation. Rewrap each live
payload reference with an idempotent operation, retain old generations until
all live references move, and retire only after recovery artifacts and erasure
journals have been acknowledged. A controller configured with an older active
generation cannot downgrade custody. Key retirement cannot make external old
backups safe by itself; the current independent checkpoint must survive.

## Local evidence and remaining limits

The focused guarded tests exercise durable key recovery, revocation, rotation,
checkpoint loss, snapshot integrity, identity rejection, idempotence, historical
uncertainty, account installation and consent-preserving forward recovery.
All data, keys and addresses are fictional. Production provisioning, real data
preflight, native ARM64 testing and owner-approved cutover remain separate
release gates. Fingerprinting currently scans the database per protected unit;
measure production volume/latency before granting a pilot. No benchmark on real
student data has been performed.
