"""S0-D2 fictional SQLite experiment. Explicit guard-confined calls only.

No startup integration, real encryption, private data or transport.
Importing this module performs no I/O.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import uuid

SOURCE_SQL_SHA256 = "D99A1EA79BFBD5A47D135A34602BBBE06B5DBA276D910562AE12A188D370081D"
VERSION = 1
SOURCE_SQL = (
    "-- DESIGN ONLY: never executed in S0. Requires SQLite >=3.37 (STRICT),\n"
    "-- foreign_keys=ON on EACH connection before BEGIN; verify WAL/busy_timeout.\n"
    "-- Existing legacy companies/users/preferences/tokens/deliveries stay intact.\n"
    "CREATE TABLE notification_schema_migrations(\n"
    " version INTEGER PRIMARY KEY CHECK(version>0), checksum TEXT NOT NULL,\n"
    " phase TEXT NOT NULL, applied_ms INTEGER NOT NULL CHECK(applied_ms>=0)\n"
    ") STRICT;\n"
    "CREATE TABLE notification_runtime(\n"
    " singleton INTEGER PRIMARY KEY CHECK(singleton=1),\n"
    " epoch INTEGER NOT NULL CHECK(epoch>=0), config_version TEXT NOT NULL,\n"
    " policy_version TEXT NOT NULL, writer_mode TEXT NOT NULL CHECK(writer_mode IN('OFF','V2')),\n"
    " fanout_mode TEXT NOT NULL CHECK(fanout_mode IN('OFF','V2')),\n"
    " send_mode TEXT NOT NULL CHECK(send_mode IN('OFF','V2')),\n"
    " intake_generation INTEGER NOT NULL CHECK(intake_generation>=0),\n"
    " intake_token TEXT, intake_lease_ms INTEGER, active_batch_id TEXT,\n"
    " CHECK((intake_token IS NULL AND intake_lease_ms IS NULL) OR\n"
    "       (intake_token IS NOT NULL AND length(intake_token)>0 AND intake_lease_ms IS NOT NULL)),\n"
    " FOREIGN KEY(active_batch_id) REFERENCES source_batches(batch_id)\n"
    ") STRICT;\n"
    "CREATE TABLE payload_tombstones(\n"
    " payload_id TEXT PRIMARY KEY NOT NULL CHECK(length(payload_id)>0),\n"
    " purpose TEXT NOT NULL CHECK(purpose IN('SOURCE','SNAPSHOT','APPROVAL')),\n"
    " created_ms INTEGER NOT NULL CHECK(created_ms>=0)\n"
    ") STRICT;\n"
    "CREATE TABLE protected_payloads(\n"
    " payload_id TEXT PRIMARY KEY NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " encrypted_bytes BLOB NOT NULL, key_reference TEXT NOT NULL\n"
    ") STRICT;\n"
    "CREATE TABLE privacy_actions(\n"
    " action_id TEXT PRIMARY KEY NOT NULL, authorization_id TEXT NOT NULL UNIQUE,\n"
    " target_kind TEXT NOT NULL CHECK(target_kind IN('PAYLOAD','RECIPIENT','LEGACY')),\n"
    " target_key TEXT NOT NULL, action TEXT NOT NULL CHECK(action IN('ERASE','DEIDENTIFY')),\n"
    " recorded_ms INTEGER NOT NULL CHECK(recorded_ms>=0), policy_version TEXT NOT NULL\n"
    ") STRICT;\n"
    "CREATE TABLE recipient_handles(\n"
    " recipient_key TEXT PRIMARY KEY NOT NULL CHECK(length(recipient_key)>0),\n"
    " role TEXT NOT NULL CHECK(role IN('ADMIN','SUBSCRIBER')),\n"
    " created_ms INTEGER NOT NULL CHECK(created_ms>=0), UNIQUE(recipient_key,role)\n"
    ") STRICT;\n"
    "CREATE TABLE recipient_associations(\n"
    " recipient_key TEXT PRIMARY KEY NOT NULL REFERENCES recipient_handles(recipient_key),\n"
    " user_id INTEGER UNIQUE REFERENCES users(id) ON DELETE RESTRICT,\n"
    " protected_destination BLOB, key_reference TEXT,\n"
    " CHECK((user_id IS NOT NULL AND protected_destination IS NULL AND key_reference IS NULL) OR\n"
    "       (user_id IS NULL AND protected_destination IS NOT NULL AND key_reference IS NOT NULL))\n"
    ") STRICT;\n"
    "CREATE TABLE subscriber_versions(\n"
    " user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE RESTRICT,\n"
    " consent_epoch INTEGER NOT NULL CHECK(consent_epoch>=0),\n"
    " profile_epoch INTEGER NOT NULL CHECK(profile_epoch>=0), updated_ms INTEGER NOT NULL CHECK(updated_ms>=0)\n"
    ") STRICT;\n"
    "CREATE TABLE source_batches(\n"
    " batch_id TEXT PRIMARY KEY NOT NULL, source_key TEXT NOT NULL,\n"
    " intake_generation INTEGER NOT NULL CHECK(intake_generation>=0), admission_key TEXT NOT NULL UNIQUE,\n"
    " payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " observed_ms INTEGER NOT NULL CHECK(observed_ms>=0), admitted_ms INTEGER NOT NULL CHECK(admitted_ms>=0),\n"
    " source_contract TEXT NOT NULL, normalizer_version TEXT NOT NULL,\n"
    " config_version TEXT NOT NULL, policy_version TEXT NOT NULL,\n"
    " record_count INTEGER NOT NULL CHECK(record_count>=0), seen_digest TEXT NOT NULL,\n"
    " completeness TEXT NOT NULL CHECK(completeness IN('VERIFIED_COMPLETE','PARTIAL','UNKNOWN')),\n"
    " reconciliation_allowed INTEGER NOT NULL CHECK(reconciliation_allowed IN(0,1)),\n"
    " status TEXT NOT NULL CHECK(status IN('ACCEPTED','INGESTING','COMPLETE','COMPLETE_WITH_HOLDS','HELD_GLOBAL')),\n"
    " positive_cursor INTEGER NOT NULL DEFAULT 0 CHECK(positive_cursor BETWEEN 0 AND record_count),\n"
    " reconcile_cursor INTEGER NOT NULL DEFAULT 0 CHECK(reconcile_cursor>=0),\n"
    " completed_ms INTEGER, hold_reason TEXT, resolution_authorization TEXT,\n"
    " UNIQUE(source_key,intake_generation),\n"
    " CHECK(reconciliation_allowed=0 OR completeness='VERIFIED_COMPLETE'),\n"
    " CHECK((status IN('COMPLETE','COMPLETE_WITH_HOLDS') AND completed_ms IS NOT NULL)\n"
    "       OR (status NOT IN('COMPLETE','COMPLETE_WITH_HOLDS') AND completed_ms IS NULL)),\n"
    " CHECK(status<>'HELD_GLOBAL' OR hold_reason IS NOT NULL)\n"
    ") STRICT;\n"
    "CREATE UNIQUE INDEX source_one_active ON source_batches(source_key)\n"
    " WHERE status IN('ACCEPTED','INGESTING','HELD_GLOBAL');\n"
    "CREATE TABLE source_batch_items(\n"
    " batch_id TEXT NOT NULL REFERENCES source_batches(batch_id), item_index INTEGER NOT NULL CHECK(item_index>=0),\n"
    " trusted_company_id TEXT, payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " status TEXT NOT NULL CHECK(status IN('PENDING','APPLIED','UNCHANGED','QUARANTINED')),\n"
    " company_key TEXT, event_id TEXT, error_category TEXT, completed_ms INTEGER,\n"
    " PRIMARY KEY(batch_id,item_index),\n"
    " FOREIGN KEY(company_key) REFERENCES opportunity_state(company_key),\n"
    " FOREIGN KEY(company_key,event_id) REFERENCES opportunity_events(company_key,event_id),\n"
    " CHECK((status='PENDING' AND completed_ms IS NULL AND event_id IS NULL AND company_key IS NULL)\n"
    " OR (status='APPLIED' AND completed_ms IS NOT NULL AND event_id IS NOT NULL AND company_key IS NOT NULL)\n"
    " OR (status='UNCHANGED' AND completed_ms IS NOT NULL AND event_id IS NULL)\n"
    " OR (status='QUARANTINED' AND completed_ms IS NOT NULL AND event_id IS NULL AND error_category IS NOT NULL))\n"
    ") STRICT;\n"
    "CREATE TABLE source_reconciliation_work(\n"
    " batch_id TEXT NOT NULL REFERENCES source_batches(batch_id),\n"
    " company_key TEXT NOT NULL REFERENCES opportunity_state(company_key),\n"
    " status TEXT NOT NULL CHECK(status IN('PENDING','APPLIED','UNCHANGED','HELD')),\n"
    " event_id TEXT, completed_ms INTEGER, reason TEXT,\n"
    " PRIMARY KEY(batch_id,company_key),\n"
    " FOREIGN KEY(company_key,event_id) REFERENCES opportunity_events(company_key,event_id),\n"
    " CHECK((status='PENDING' AND event_id IS NULL AND completed_ms IS NULL)\n"
    " OR (status='APPLIED' AND event_id IS NOT NULL AND completed_ms IS NOT NULL)\n"
    " OR (status IN('UNCHANGED','HELD') AND event_id IS NULL AND completed_ms IS NOT NULL)),\n"
    " CHECK(status<>'HELD' OR reason IS NOT NULL)\n"
    ") STRICT;\n"
    "CREATE TABLE opportunity_state(\n"
    " company_key TEXT PRIMARY KEY NOT NULL, source_key TEXT NOT NULL,\n"
    " legacy_company_id TEXT NOT NULL UNIQUE REFERENCES companies(id) ON DELETE RESTRICT,\n"
    " revision INTEGER NOT NULL CHECK(revision>=0), head_event_id TEXT,\n"
    " snapshot_payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " material_digest TEXT NOT NULL, last_batch_id TEXT REFERENCES source_batches(batch_id),\n"
    " normalizer_version TEXT NOT NULL, matcher_version TEXT NOT NULL, updated_ms INTEGER NOT NULL CHECK(updated_ms>=0),\n"
    " CHECK((revision=0 AND head_event_id IS NULL) OR (revision>0 AND head_event_id IS NOT NULL)),\n"
    " FOREIGN KEY(company_key,revision,head_event_id)\n"
    "   REFERENCES opportunity_events(company_key,revision,event_id) DEFERRABLE INITIALLY DEFERRED\n"
    ") STRICT;\n"
    "CREATE TABLE opportunity_events(\n"
    " event_id TEXT PRIMARY KEY NOT NULL, company_key TEXT NOT NULL REFERENCES opportunity_state(company_key),\n"
    " revision INTEGER NOT NULL CHECK(revision>0), previous_revision INTEGER, previous_event_id TEXT,\n"
    " batch_id TEXT NOT NULL REFERENCES source_batches(batch_id), item_index INTEGER,\n"
    " origin TEXT NOT NULL CHECK(origin IN('FETCH','RECONCILE')),\n"
    " before_payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " after_payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " reasons_json TEXT NOT NULL, observed_ms INTEGER NOT NULL CHECK(observed_ms>=0), persisted_ms INTEGER NOT NULL CHECK(persisted_ms>=0),\n"
    " normalizer_version TEXT NOT NULL, matcher_version TEXT NOT NULL,\n"
    " window_quality TEXT NOT NULL CHECK(window_quality IN('VERIFIED_INSTANT','SOURCE_LOCAL','UNKNOWN')),\n"
    " deadline_ms INTEGER, zone_evidence_reference TEXT,\n"
    " UNIQUE(company_key,revision), UNIQUE(company_key,event_id), UNIQUE(company_key,revision,event_id),\n"
    " FOREIGN KEY(company_key,previous_revision,previous_event_id)\n"
    " REFERENCES opportunity_events(company_key,revision,event_id),\n"
    " FOREIGN KEY(batch_id,item_index) REFERENCES source_batch_items(batch_id,item_index),\n"
    " CHECK((revision=1 AND previous_revision IS NULL AND previous_event_id IS NULL)\n"
    " OR (revision>1 AND previous_revision=revision-1 AND previous_revision IS NOT NULL AND previous_event_id IS NOT NULL)),\n"
    " CHECK((origin='FETCH' AND item_index IS NOT NULL) OR (origin='RECONCILE' AND item_index IS NULL)),\n"
    " CHECK((window_quality='VERIFIED_INSTANT' AND deadline_ms IS NOT NULL AND zone_evidence_reference IS NOT NULL)\n"
    " OR (window_quality<>'VERIFIED_INSTANT' AND deadline_ms IS NULL))\n"
    ") STRICT;\n"
    "CREATE UNIQUE INDEX event_one_item ON opportunity_events(batch_id,item_index) WHERE origin='FETCH';\n"
    "CREATE TABLE event_routes(\n"
    " event_id TEXT NOT NULL REFERENCES opportunity_events(event_id),\n"
    " side TEXT NOT NULL CHECK(side IN('BEFORE','AFTER')),\n"
    " institution TEXT NOT NULL, branch TEXT NOT NULL, qualification TEXT NOT NULL,\n"
    " PRIMARY KEY(event_id,side,institution,branch)\n"
    ") STRICT;\n"
    "CREATE TABLE event_relations(\n"
    " company_key TEXT NOT NULL, earlier_event_id TEXT NOT NULL, later_event_id TEXT NOT NULL,\n"
    " relation TEXT NOT NULL CHECK(relation IN('SUPERSEDES','CORRECTS')),\n"
    " policy_version TEXT NOT NULL, recorded_ms INTEGER NOT NULL CHECK(recorded_ms>=0),\n"
    " PRIMARY KEY(earlier_event_id,later_event_id,relation),\n"
    " FOREIGN KEY(company_key,earlier_event_id) REFERENCES opportunity_events(company_key,event_id),\n"
    " FOREIGN KEY(company_key,later_event_id) REFERENCES opportunity_events(company_key,event_id)\n"
    ") STRICT;\n"
    "CREATE TABLE notification_authorizations(\n"
    " authorization_id TEXT PRIMARY KEY NOT NULL, company_key TEXT NOT NULL REFERENCES opportunity_state(company_key),\n"
    " purpose TEXT NOT NULL CHECK(purpose IN('REDRIVE','LEGACY_CATCHUP','RESOLVE')),\n"
    " scope_payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " duplicate_risk_ack INTEGER NOT NULL CHECK(duplicate_risk_ack IN(0,1)),\n"
    " policy_version TEXT NOT NULL, authorized_ms INTEGER NOT NULL CHECK(authorized_ms>=0)\n"
    ") STRICT;\n"
    "CREATE TABLE notification_intents(\n"
    " intent_id TEXT PRIMARY KEY NOT NULL, company_key TEXT NOT NULL REFERENCES opportunity_state(company_key),\n"
    " kind TEXT NOT NULL CHECK(kind IN('SOURCE_EVENT','MANUAL_REDRIVE','LEGACY_CATCHUP')),\n"
    " source_event_id TEXT, parent_intent_id TEXT, redrive_delivery_id INTEGER,\n"
    " authorization_id TEXT UNIQUE REFERENCES notification_authorizations(authorization_id),\n"
    " legacy_disposition_id TEXT UNIQUE,\n"
    " snapshot_payload_id TEXT NOT NULL REFERENCES payload_tombstones(payload_id),\n"
    " policy_version TEXT NOT NULL, config_version TEXT NOT NULL, created_ms INTEGER NOT NULL CHECK(created_ms>=0),\n"
    " UNIQUE(company_key,intent_id),\n"
    " FOREIGN KEY(company_key,source_event_id) REFERENCES opportunity_events(company_key,event_id),\n"
    " FOREIGN KEY(company_key,parent_intent_id) REFERENCES notification_intents(company_key,intent_id),\n"
    " FOREIGN KEY(redrive_delivery_id,parent_intent_id,company_key)\n"
    " REFERENCES notification_deliveries_v2(delivery_id,intent_id,company_key),\n"
    " FOREIGN KEY(legacy_disposition_id) REFERENCES legacy_notification_dispositions(disposition_id),\n"
    " CHECK((kind='SOURCE_EVENT' AND source_event_id IS NOT NULL AND parent_intent_id IS NULL\n"
    "   AND redrive_delivery_id IS NULL AND authorization_id IS NULL AND legacy_disposition_id IS NULL)\n"
    " OR (kind='MANUAL_REDRIVE' AND source_event_id IS NOT NULL AND parent_intent_id IS NOT NULL\n"
    "   AND redrive_delivery_id IS NOT NULL AND authorization_id IS NOT NULL AND legacy_disposition_id IS NULL)\n"
    " OR (kind='LEGACY_CATCHUP' AND source_event_id IS NULL AND parent_intent_id IS NULL\n"
    "   AND redrive_delivery_id IS NULL AND authorization_id IS NOT NULL AND legacy_disposition_id IS NOT NULL))\n"
    ") STRICT;\n"
    "CREATE UNIQUE INDEX ordinary_intent ON notification_intents(source_event_id) WHERE kind='SOURCE_EVENT';\n"
    "CREATE TABLE event_outbox(\n"
    " intent_id TEXT NOT NULL REFERENCES notification_intents(intent_id),\n"
    " channel TEXT NOT NULL CHECK(channel IN('ADMIN','SUBSCRIBER')),\n"
    " status TEXT NOT NULL CHECK(status IN('PENDING','PROCESSING','COMPLETE','HELD')),\n"
    " runtime_epoch INTEGER NOT NULL CHECK(runtime_epoch>=0), claim_token TEXT,\n"
    " generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0), lease_ms INTEGER,\n"
    " next_ms INTEGER NOT NULL CHECK(next_ms>=0), failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count>=0),\n"
    " max_failures INTEGER NOT NULL CHECK(max_failures>0),\n"
    " sealed_ms INTEGER, audience_count INTEGER CHECK(audience_count>=0),\n"
    " last_audience_id INTEGER NOT NULL DEFAULT 0 CHECK(last_audience_id>=0),\n"
    " processed_count INTEGER NOT NULL DEFAULT 0 CHECK(processed_count>=0),\n"
    " created_ms INTEGER NOT NULL CHECK(created_ms>=0), completed_ms INTEGER, hold_reason TEXT,\n"
    " PRIMARY KEY(intent_id,channel),\n"
    " CHECK((status='PROCESSING' AND claim_token IS NOT NULL AND length(claim_token)>0 AND lease_ms IS NOT NULL)\n"
    " OR (status<>'PROCESSING' AND claim_token IS NULL AND lease_ms IS NULL)),\n"
    " CHECK((sealed_ms IS NULL AND audience_count IS NULL AND last_audience_id=0 AND processed_count=0)\n"
    " OR (sealed_ms IS NOT NULL AND audience_count IS NOT NULL AND processed_count<=audience_count)),\n"
    " CHECK((status='COMPLETE' AND sealed_ms IS NOT NULL AND completed_ms IS NOT NULL AND processed_count=audience_count)\n"
    " OR (status<>'COMPLETE' AND completed_ms IS NULL)),\n"
    " CHECK(status<>'HELD' OR hold_reason IS NOT NULL)\n"
    ") STRICT;\n"
    "CREATE TABLE event_audience(\n"
    " audience_id INTEGER PRIMARY KEY, intent_id TEXT NOT NULL, channel TEXT NOT NULL,\n"
    " recipient_key TEXT NOT NULL REFERENCES recipient_handles(recipient_key),\n"
    " role TEXT NOT NULL CHECK(role IN('ACTION','INFO')),\n"
    " selected_branch TEXT, consent_epoch INTEGER, profile_epoch INTEGER,\n"
    " sealed_policy_version TEXT NOT NULL, created_ms INTEGER NOT NULL CHECK(created_ms>=0),\n"
    " FOREIGN KEY(intent_id,channel) REFERENCES event_outbox(intent_id,channel),\n"
    " UNIQUE(intent_id,channel,recipient_key), UNIQUE(audience_id,intent_id,channel,recipient_key),\n"
    " CHECK((channel='SUBSCRIBER' AND selected_branch IS NOT NULL AND consent_epoch IS NOT NULL\n"
    "     AND consent_epoch>=0 AND profile_epoch IS NOT NULL AND profile_epoch>=0)\n"
    " OR (channel='ADMIN' AND selected_branch IS NULL AND consent_epoch IS NULL AND profile_epoch IS NULL))\n"
    ") STRICT;\n"
    "CREATE TABLE notification_deliveries_v2(\n"
    " delivery_id INTEGER PRIMARY KEY, company_key TEXT NOT NULL,\n"
    " audience_id INTEGER NOT NULL, intent_id TEXT NOT NULL, channel TEXT NOT NULL, recipient_key TEXT NOT NULL,\n"
    " account_key TEXT NOT NULL,\n"
    " status TEXT NOT NULL CHECK(status IN('PENDING','PROCESSING','SENDING','SENT','FAILED','SUPPRESSED','UNCERTAIN')),\n"
    " attempt_count INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL CHECK(max_attempts>0),\n"
    " generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0), claim_token TEXT,\n"
    " runtime_epoch INTEGER NOT NULL CHECK(runtime_epoch>=0), lease_ms INTEGER,\n"
    " next_ms INTEGER NOT NULL CHECK(next_ms>=0), expires_ms INTEGER, priority INTEGER NOT NULL, created_ms INTEGER NOT NULL CHECK(created_ms>=0),\n"
    " send_started_ms INTEGER, accepted_ms INTEGER, ack_ms INTEGER, finished_ms INTEGER,\n"
    " invocation_id TEXT, resolution_id TEXT, cancel_requested_ms INTEGER, reason_code TEXT,\n"
    " FOREIGN KEY(company_key,intent_id) REFERENCES notification_intents(company_key,intent_id),\n"
    " FOREIGN KEY(audience_id,intent_id,channel,recipient_key)\n"
    " REFERENCES event_audience(audience_id,intent_id,channel,recipient_key),\n"
    " FOREIGN KEY(account_key) REFERENCES smtp_account_gate(account_key),\n"
    " FOREIGN KEY(invocation_id,delivery_id,generation) REFERENCES transport_invocations(invocation_id,delivery_id,delivery_generation),\n"
    " FOREIGN KEY(resolution_id,delivery_id,invocation_id)\n"
    " REFERENCES delivery_resolutions(resolution_id,delivery_id,invocation_id),\n"
    " UNIQUE(intent_id,channel,recipient_key), UNIQUE(delivery_id,intent_id,company_key),\n"
    " CHECK(attempt_count BETWEEN 0 AND max_attempts),\n"
    " CHECK((status IN('PROCESSING','SENDING') AND claim_token IS NOT NULL AND length(claim_token)>0 AND lease_ms IS NOT NULL)\n"
    " OR (status NOT IN('PROCESSING','SENDING') AND claim_token IS NULL AND lease_ms IS NULL)),\n"
    " CHECK((status='SENT' AND accepted_ms IS NOT NULL AND ack_ms IS NOT NULL)\n"
    " OR (status<>'SENT' AND accepted_ms IS NULL AND ack_ms IS NULL)),\n"
    " CHECK((status IN('SENT','FAILED','SUPPRESSED','UNCERTAIN') AND finished_ms IS NOT NULL)\n"
    " OR (status IN('PENDING','PROCESSING','SENDING') AND finished_ms IS NULL)),\n"
    " CHECK(status NOT IN('PENDING','PROCESSING') OR (send_started_ms IS NULL AND invocation_id IS NULL)),\n"
    " CHECK(status NOT IN('SENDING','UNCERTAIN','SENT') OR (send_started_ms IS NOT NULL AND invocation_id IS NOT NULL)),\n"
    " CHECK(status<>'PENDING' OR attempt_count<max_attempts),\n"
    " CHECK(status NOT IN('PROCESSING','SENDING','UNCERTAIN','SENT') OR attempt_count>0),\n"
    " CHECK(resolution_id IS NULL OR (invocation_id IS NOT NULL AND status IN('UNCERTAIN','SENT')))\n"
    ") STRICT;\n"
    "CREATE TABLE delivery_attempts(\n"
    " delivery_id INTEGER NOT NULL REFERENCES notification_deliveries_v2(delivery_id),\n"
    " generation INTEGER NOT NULL CHECK(generation>0), attempt_number INTEGER NOT NULL CHECK(attempt_number>0),\n"
    " claim_token TEXT NOT NULL, runtime_epoch INTEGER NOT NULL, owner_instance TEXT NOT NULL,\n"
    " claimed_ms INTEGER NOT NULL CHECK(claimed_ms>=0), lease_ms INTEGER NOT NULL CHECK(lease_ms>=0), account_key TEXT NOT NULL,\n"
    " gate_generation INTEGER NOT NULL, submission_key TEXT NOT NULL UNIQUE,\n"
    " PRIMARY KEY(delivery_id,generation), UNIQUE(delivery_id,attempt_number),\n"
    " FOREIGN KEY(account_key) REFERENCES smtp_account_gate(account_key)\n"
    ") STRICT;\n"
    "CREATE TABLE delivery_attempt_facts(\n"
    " delivery_id INTEGER NOT NULL, generation INTEGER NOT NULL, fact_kind TEXT NOT NULL\n"
    " CHECK(fact_kind IN('PREP_EXPIRED','SUPPRESSED','NOT_ACCEPTED','UNKNOWN','ACK_FAILED','ACCEPTED')),\n"
    " fact_key TEXT NOT NULL, observed_ms INTEGER NOT NULL CHECK(observed_ms>=0), reason_code TEXT NOT NULL,\n"
    " PRIMARY KEY(delivery_id,generation,fact_kind,fact_key),\n"
    " FOREIGN KEY(delivery_id,generation) REFERENCES delivery_attempts(delivery_id,generation)\n"
    ") STRICT;\n"
    "CREATE TABLE transport_invocations(\n"
    " invocation_id TEXT PRIMARY KEY NOT NULL, account_key TEXT NOT NULL REFERENCES smtp_account_gate(account_key),\n"
    " gate_generation INTEGER NOT NULL, submission_key TEXT NOT NULL UNIQUE, owner_instance TEXT NOT NULL,\n"
    " owner_epoch INTEGER NOT NULL, delivery_id INTEGER, delivery_generation INTEGER,\n"
    " message_class TEXT NOT NULL CHECK(message_class IN('ADMIN','SUBSCRIBER','ACCOUNT')),\n"
    " intent_ms INTEGER NOT NULL CHECK(intent_ms>=0),\n"
    " FOREIGN KEY(delivery_id,delivery_generation) REFERENCES delivery_attempts(delivery_id,generation),\n"
    " UNIQUE(delivery_id,delivery_generation), UNIQUE(invocation_id,delivery_id,delivery_generation),\n"
    " UNIQUE(invocation_id,account_key,gate_generation,submission_key),\n"
    " CHECK((message_class='ACCOUNT' AND delivery_id IS NULL AND delivery_generation IS NULL)\n"
    " OR (message_class IN('ADMIN','SUBSCRIBER') AND delivery_id IS NOT NULL AND delivery_generation IS NOT NULL))\n"
    ") STRICT;\n"
    "CREATE TABLE transport_facts(\n"
    " invocation_id TEXT NOT NULL REFERENCES transport_invocations(invocation_id),\n"
    " fact_kind TEXT NOT NULL CHECK(fact_kind IN('ACCEPTED','NOT_ACCEPTED','UNKNOWN','ENDED','PROCESS_EXIT_CONFIRMED')),\n"
    " fact_key TEXT NOT NULL, observed_ms INTEGER NOT NULL CHECK(observed_ms>=0), evidence_type TEXT NOT NULL,\n"
    " evidence_reference TEXT NOT NULL, authorization_id TEXT,\n"
    " PRIMARY KEY(invocation_id,fact_kind,fact_key)\n"
    ") STRICT;\n"
    "CREATE TABLE delivery_resolutions(\n"
    " resolution_id TEXT PRIMARY KEY NOT NULL, delivery_id INTEGER NOT NULL,\n"
    " invocation_id TEXT NOT NULL, delivery_generation INTEGER NOT NULL,\n"
    " authorization_id TEXT NOT NULL UNIQUE REFERENCES notification_authorizations(authorization_id),\n"
    " action TEXT NOT NULL CHECK(action IN('CONFIRM_ACCEPTED','KEEP_UNCERTAIN')),\n"
    " recorded_ms INTEGER NOT NULL CHECK(recorded_ms>=0), accepted_ms INTEGER, evidence_reference TEXT NOT NULL,\n"
    " UNIQUE(resolution_id,delivery_id,invocation_id),\n"
    " FOREIGN KEY(delivery_id) REFERENCES notification_deliveries_v2(delivery_id),\n"
    " FOREIGN KEY(invocation_id,delivery_id,delivery_generation)\n"
    " REFERENCES transport_invocations(invocation_id,delivery_id,delivery_generation),\n"
    " CHECK((action='CONFIRM_ACCEPTED' AND accepted_ms IS NOT NULL) OR (action='KEEP_UNCERTAIN' AND accepted_ms IS NULL))\n"
    ") STRICT;\n"
    "CREATE TABLE smtp_account_gate(\n"
    " account_key TEXT PRIMARY KEY NOT NULL,\n"
    " state TEXT NOT NULL CHECK(state IN('FREE','RESERVED','INFLIGHT','BLOCKED')),\n"
    " generation INTEGER NOT NULL CHECK(generation>=0), owner_token TEXT, runtime_epoch INTEGER,\n"
    " submission_key TEXT, delivery_id INTEGER REFERENCES notification_deliveries_v2(delivery_id),\n"
    " invocation_id TEXT, lease_ms INTEGER, next_allowed_ms INTEGER NOT NULL CHECK(next_allowed_ms>=0),\n"
    " updated_ms INTEGER NOT NULL CHECK(updated_ms>=0),\n"
    " FOREIGN KEY(invocation_id,account_key,generation,submission_key)\n"
    " REFERENCES transport_invocations(invocation_id,account_key,gate_generation,submission_key),\n"
    " CHECK((state='FREE' AND owner_token IS NULL AND runtime_epoch IS NULL AND submission_key IS NULL\n"
    "      AND delivery_id IS NULL AND invocation_id IS NULL AND lease_ms IS NULL)\n"
    " OR (state='RESERVED' AND owner_token IS NOT NULL AND runtime_epoch IS NOT NULL\n"
    "      AND submission_key IS NOT NULL AND invocation_id IS NULL AND lease_ms IS NOT NULL)\n"
    " OR (state IN('INFLIGHT','BLOCKED') AND owner_token IS NOT NULL AND runtime_epoch IS NOT NULL\n"
    "      AND submission_key IS NOT NULL AND invocation_id IS NOT NULL AND lease_ms IS NOT NULL))\n"
    ") STRICT;\n"
    "CREATE TABLE legacy_notification_dispositions(\n"
    " disposition_id TEXT PRIMARY KEY NOT NULL, legacy_kind TEXT NOT NULL CHECK(legacy_kind IN('ADMIN','SUBSCRIBER')),\n"
    " legacy_row_id INTEGER NOT NULL, company_key TEXT NOT NULL REFERENCES opportunity_state(company_key),\n"
    " original_status TEXT NOT NULL, row_digest TEXT NOT NULL,\n"
    " decision TEXT NOT NULL CHECK(decision IN('HISTORICAL_ONLY','HOLD_REVIEW')),\n"
    " recorded_ms INTEGER NOT NULL CHECK(recorded_ms>=0), UNIQUE(legacy_kind,legacy_row_id)\n"
    ") STRICT;\n"
    "\n"
    "CREATE INDEX item_work ON source_batch_items(batch_id,status,item_index);\n"
    "CREATE INDEX reconcile_work ON source_reconciliation_work(batch_id,status,company_key);\n"
    "CREATE INDEX event_previous ON opportunity_events(company_key,previous_revision,previous_event_id);\n"
    "CREATE INDEX event_batch ON opportunity_events(batch_id);\n"
    "CREATE INDEX event_clock ON opportunity_events(observed_ms,event_id);\n"
    "CREATE INDEX intent_parent ON notification_intents(company_key,parent_intent_id);\n"
    "CREATE INDEX intent_redrive ON notification_intents(redrive_delivery_id);\n"
    "CREATE INDEX routes_match ON event_routes(institution,branch,event_id,side);\n"
    "CREATE INDEX outbox_ready ON event_outbox(channel,next_ms,created_ms) WHERE status='PENDING';\n"
    "CREATE INDEX outbox_lease ON event_outbox(lease_ms) WHERE status='PROCESSING';\n"
    "CREATE INDEX audience_page ON event_audience(intent_id,channel,audience_id);\n"
    "CREATE INDEX audience_recipient ON event_audience(recipient_key,intent_id);\n"
    "CREATE INDEX delivery_ready ON notification_deliveries_v2(account_key,priority,next_ms,created_ms,delivery_id) WHERE status='PENDING';\n"
    "CREATE INDEX delivery_lease ON notification_deliveries_v2(lease_ms) WHERE status IN('PROCESSING','SENDING');\n"
    "CREATE INDEX delivery_recipient ON notification_deliveries_v2(recipient_key,status);\n"
    "CREATE INDEX delivery_expiry ON notification_deliveries_v2(expires_ms,status);\n"
    "CREATE INDEX invocation_account ON transport_invocations(account_key,gate_generation);\n"
    "CREATE INDEX gate_invocation ON smtp_account_gate(invocation_id);\n"
    "CREATE INDEX resolution_invocation ON delivery_resolutions(invocation_id);\n"
    "\n"
    "-- Append-only metadata is enforced; private vault erasure is separately authorized.\n"
    "CREATE TRIGGER events_order BEFORE INSERT ON opportunity_events BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(\n"
    " SELECT 1 FROM opportunity_state s WHERE s.company_key=NEW.company_key\n"
    " AND NEW.revision=s.revision+1 AND NEW.previous_event_id IS s.head_event_id)\n"
    " THEN RAISE(ABORT,'event is not next committed company revision') END;\n"
    "END;\n"
    "CREATE TRIGGER state_identity_guard BEFORE UPDATE OF company_key,source_key,legacy_company_id ON opportunity_state\n"
    "BEGIN SELECT RAISE(ABORT,'immutable source identity'); END;\n"
    "CREATE TRIGGER state_head_guard BEFORE UPDATE OF revision,head_event_id ON opportunity_state\n"
    "WHEN NOT (NEW.revision=OLD.revision AND NEW.head_event_id IS OLD.head_event_id)\n"
    "BEGIN\n"
    " SELECT CASE WHEN NEW.revision<>OLD.revision+1 OR NOT EXISTS(\n"
    " SELECT 1 FROM notification_intents i WHERE i.company_key=NEW.company_key\n"
    " AND i.source_event_id=NEW.head_event_id AND i.kind='SOURCE_EVENT'\n"
    " AND (SELECT count(*) FROM event_outbox o WHERE o.intent_id=i.intent_id)=2)\n"
    " THEN RAISE(ABORT,'head requires next event and two durable channel obligations') END;\n"
    "END;\n"
    "CREATE TRIGGER relation_order BEFORE INSERT ON event_relations BEGIN\n"
    " SELECT CASE WHEN (SELECT revision FROM opportunity_events WHERE event_id=NEW.earlier_event_id)\n"
    " >=(SELECT revision FROM opportunity_events WHERE event_id=NEW.later_event_id)\n"
    " THEN RAISE(ABORT,'relation must point forward') END;\n"
    "END;\n"
    "CREATE TRIGGER intent_scope BEFORE INSERT ON notification_intents BEGIN\n"
    " SELECT CASE WHEN NEW.kind='MANUAL_REDRIVE' AND NOT EXISTS(\n"
    " SELECT 1 FROM notification_intents p WHERE p.intent_id=NEW.parent_intent_id\n"
    " AND p.company_key=NEW.company_key AND p.source_event_id=NEW.source_event_id)\n"
    " THEN RAISE(ABORT,'redrive must retain original source context') END;\n"
    " SELECT CASE WHEN NEW.authorization_id IS NOT NULL AND NOT EXISTS(\n"
    " SELECT 1 FROM notification_authorizations a WHERE a.authorization_id=NEW.authorization_id\n"
    " AND a.company_key=NEW.company_key AND\n"
    " ((NEW.kind='MANUAL_REDRIVE' AND a.purpose='REDRIVE' AND a.duplicate_risk_ack=1)\n"
    " OR (NEW.kind='LEGACY_CATCHUP' AND a.purpose='LEGACY_CATCHUP')))\n"
    " THEN RAISE(ABORT,'intent authorization mismatch') END;\n"
    " SELECT CASE WHEN NEW.kind='LEGACY_CATCHUP' AND NOT EXISTS(\n"
    " SELECT 1 FROM legacy_notification_dispositions l WHERE l.disposition_id=NEW.legacy_disposition_id\n"
    " AND l.company_key=NEW.company_key)\n"
    " THEN RAISE(ABORT,'legacy company mismatch') END;\n"
    "END;\n"
    "CREATE TRIGGER routes_before_outbox BEFORE INSERT ON event_routes WHEN EXISTS(\n"
    " SELECT 1 FROM notification_intents i JOIN event_outbox o ON o.intent_id=i.intent_id\n"
    " WHERE i.source_event_id=NEW.event_id AND i.kind='SOURCE_EVENT')\n"
    "BEGIN SELECT RAISE(ABORT,'routes already sealed for event'); END;\n"
    "CREATE TRIGGER outbox_insert_guard BEFORE INSERT ON event_outbox BEGIN\n"
    " SELECT CASE WHEN NEW.status NOT IN('PENDING','HELD') OR NEW.generation<>0\n"
    " OR NEW.sealed_ms IS NOT NULL OR NEW.audience_count IS NOT NULL\n"
    " OR NEW.last_audience_id<>0 OR NEW.processed_count<>0 OR NEW.completed_ms IS NOT NULL\n"
    " THEN RAISE(ABORT,'initial outbox must be unsealed pending or held') END;\n"
    "END;\n"
    "CREATE TRIGGER audience_before_seal BEFORE INSERT ON event_audience BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM event_outbox o WHERE o.intent_id=NEW.intent_id\n"
    " AND o.channel=NEW.channel AND o.status='PROCESSING' AND o.sealed_ms IS NULL)\n"
    " THEN RAISE(ABORT,'audience requires owned unsealed outbox') END;\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM recipient_handles h WHERE h.recipient_key=NEW.recipient_key\n"
    " AND h.role=NEW.channel) THEN RAISE(ABORT,'recipient channel mismatch') END;\n"
    "END;\n"
    "CREATE TRIGGER outbox_seal BEFORE UPDATE OF sealed_ms,audience_count ON event_outbox BEGIN\n"
    " SELECT CASE WHEN OLD.sealed_ms IS NOT NULL AND\n"
    " (NEW.sealed_ms IS NOT OLD.sealed_ms OR NEW.audience_count IS NOT OLD.audience_count)\n"
    " THEN RAISE(ABORT,'immutable audience seal') END;\n"
    " SELECT CASE WHEN NEW.sealed_ms IS NOT NULL AND NEW.audience_count<>\n"
    " (SELECT count(*) FROM event_audience a WHERE a.intent_id=NEW.intent_id AND a.channel=NEW.channel)\n"
    " THEN RAISE(ABORT,'seal count mismatch') END;\n"
    "END;\n"
    "CREATE TRIGGER outbox_progress BEFORE UPDATE ON event_outbox BEGIN\n"
    " SELECT CASE WHEN NEW.last_audience_id<OLD.last_audience_id OR NEW.processed_count<OLD.processed_count\n"
    " OR NEW.generation<OLD.generation OR NEW.max_failures<>OLD.max_failures\n"
    " OR NEW.intent_id<>OLD.intent_id OR NEW.channel<>OLD.channel\n"
    " THEN RAISE(ABORT,'outbox identity/progress regression') END;\n"
    " SELECT CASE WHEN OLD.status='COMPLETE' THEN RAISE(ABORT,'completed outbox immutable') END;\n"
    " SELECT CASE WHEN NOT(\n"
    " OLD.status=NEW.status\n"
    " OR (OLD.status='PENDING' AND NEW.status IN('PROCESSING','HELD'))\n"
    " OR (OLD.status='PROCESSING' AND NEW.status IN('PENDING','COMPLETE','HELD'))\n"
    " OR (OLD.status='HELD' AND NEW.status='PENDING'))\n"
    " THEN RAISE(ABORT,'illegal outbox transition') END;\n"
    " SELECT CASE WHEN OLD.status='PENDING' AND NEW.status='PROCESSING'\n"
    " AND NEW.generation<>OLD.generation+1 THEN RAISE(ABORT,'outbox claim increments generation') END;\n"
    "END;\n"
    "CREATE TRIGGER delivery_insert_guard BEFORE INSERT ON notification_deliveries_v2 BEGIN\n"
    " SELECT CASE WHEN NEW.status NOT IN('PENDING','SUPPRESSED') OR NEW.attempt_count<>0 OR NEW.generation<>0\n"
    " OR NEW.invocation_id IS NOT NULL OR NEW.resolution_id IS NOT NULL\n"
    " THEN RAISE(ABORT,'invalid initial obligation') END;\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM event_outbox o WHERE o.intent_id=NEW.intent_id\n"
    " AND o.channel=NEW.channel AND o.sealed_ms IS NOT NULL)\n"
    " THEN RAISE(ABORT,'delivery requires sealed audience') END;\n"
    "END;\n"
    "CREATE TRIGGER delivery_update_guard BEFORE UPDATE ON notification_deliveries_v2 BEGIN\n"
    " SELECT CASE WHEN NEW.company_key<>OLD.company_key OR NEW.audience_id<>OLD.audience_id\n"
    " OR NEW.intent_id<>OLD.intent_id OR NEW.channel<>OLD.channel OR NEW.recipient_key<>OLD.recipient_key\n"
    " OR NEW.account_key<>OLD.account_key OR NEW.max_attempts<>OLD.max_attempts\n"
    " OR NEW.created_ms<>OLD.created_ms OR NEW.generation<OLD.generation\n"
    " OR NEW.attempt_count<OLD.attempt_count THEN RAISE(ABORT,'delivery identity/counter regression') END;\n"
    " SELECT CASE WHEN OLD.status IN('SENT','FAILED','SUPPRESSED')\n"
    " THEN RAISE(ABORT,'terminal delivery immutable') END;\n"
    " SELECT CASE WHEN NOT(\n"
    " OLD.status=NEW.status\n"
    " OR (OLD.status='PENDING' AND NEW.status IN('PROCESSING','FAILED','SUPPRESSED'))\n"
    " OR (OLD.status='PROCESSING' AND NEW.status IN('SENDING','PENDING','FAILED','SUPPRESSED'))\n"
    " OR (OLD.status='SENDING' AND NEW.status IN('SENT','PENDING','FAILED','SUPPRESSED','UNCERTAIN'))\n"
    " OR (OLD.status='UNCERTAIN' AND NEW.status='SENT' AND EXISTS(\n"
    " SELECT 1 FROM delivery_resolutions r WHERE r.resolution_id=NEW.resolution_id\n"
    " AND r.delivery_id=OLD.delivery_id AND r.invocation_id=OLD.invocation_id AND r.action='CONFIRM_ACCEPTED')))\n"
    " THEN RAISE(ABORT,'illegal delivery transition') END;\n"
    " SELECT CASE WHEN OLD.status='PENDING' AND NEW.status='PROCESSING' AND\n"
    " (NEW.attempt_count<>OLD.attempt_count+1 OR NEW.generation<>OLD.generation+1)\n"
    " THEN RAISE(ABORT,'claim must increment attempt and generation once') END;\n"
    " SELECT CASE WHEN NOT(OLD.status='PENDING' AND NEW.status='PROCESSING')\n"
    " AND NEW.attempt_count<>OLD.attempt_count THEN RAISE(ABORT,'attempt changes only on claim') END;\n"
    " SELECT CASE WHEN OLD.status='PROCESSING' AND NEW.status='SENDING' AND NOT EXISTS(\n"
    " SELECT 1 FROM transport_invocations v JOIN delivery_attempts a\n"
    " ON a.delivery_id=v.delivery_id AND a.generation=v.delivery_generation\n"
    " WHERE v.invocation_id=NEW.invocation_id AND v.delivery_id=NEW.delivery_id\n"
    " AND v.delivery_generation=NEW.generation AND a.claim_token=NEW.claim_token\n"
    " AND a.attempt_number=NEW.attempt_count)\n"
    " THEN RAISE(ABORT,'send requires matching durable invocation/attempt') END;\n"
    " SELECT CASE WHEN OLD.status='SENDING' AND NEW.status IN('PENDING','FAILED','SUPPRESSED') AND NOT(\n"
    " EXISTS(SELECT 1 FROM transport_facts f WHERE f.invocation_id=OLD.invocation_id AND f.fact_kind='NOT_ACCEPTED')\n"
    " AND EXISTS(SELECT 1 FROM transport_facts f WHERE f.invocation_id=OLD.invocation_id\n"
    " AND f.fact_kind IN('ENDED','PROCESS_EXIT_CONFIRMED'))\n"
    " AND NOT EXISTS(SELECT 1 FROM transport_facts f WHERE f.invocation_id=OLD.invocation_id\n"
    " AND f.fact_kind IN('ACCEPTED','UNKNOWN')))\n"
    " THEN RAISE(ABORT,'retry/suppression requires definitive nonacceptance and end') END;\n"
    " SELECT CASE WHEN OLD.status='SENDING' AND NEW.status='SENT' AND NOT(\n"
    " EXISTS(SELECT 1 FROM transport_facts f WHERE f.invocation_id=NEW.invocation_id AND f.fact_kind='ACCEPTED')\n"
    " AND NOT EXISTS(SELECT 1 FROM transport_facts f WHERE f.invocation_id=NEW.invocation_id\n"
    " AND f.fact_kind IN('UNKNOWN','NOT_ACCEPTED')))\n"
    " THEN RAISE(ABORT,'ordinary SENT requires unconflicted acceptance') END;\n"
    "END;\n"
    "CREATE TRIGGER attempt_matches_claim BEFORE INSERT ON delivery_attempts BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notification_deliveries_v2 d\n"
    " WHERE d.delivery_id=NEW.delivery_id AND d.status='PROCESSING' AND d.generation=NEW.generation\n"
    " AND d.attempt_count=NEW.attempt_number AND d.claim_token=NEW.claim_token\n"
    " AND d.runtime_epoch=NEW.runtime_epoch AND d.account_key=NEW.account_key)\n"
    " THEN RAISE(ABORT,'attempt must match current claim') END;\n"
    "END;\n"
    "CREATE TRIGGER invocation_matches_attempt BEFORE INSERT ON transport_invocations BEGIN\n"
    " SELECT CASE WHEN NEW.delivery_id IS NOT NULL AND NOT EXISTS(\n"
    " SELECT 1 FROM delivery_attempts a JOIN notification_deliveries_v2 d ON d.delivery_id=a.delivery_id\n"
    " WHERE a.delivery_id=NEW.delivery_id AND a.generation=NEW.delivery_generation\n"
    " AND a.account_key=NEW.account_key AND a.gate_generation=NEW.gate_generation\n"
    " AND a.submission_key=NEW.submission_key AND d.channel=NEW.message_class)\n"
    " THEN RAISE(ABORT,'invocation attempt/account mismatch') END;\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM smtp_account_gate g\n"
    " WHERE g.account_key=NEW.account_key AND g.state='RESERVED' AND g.generation=NEW.gate_generation\n"
    " AND g.submission_key=NEW.submission_key AND g.delivery_id IS NEW.delivery_id)\n"
    " THEN RAISE(ABORT,'invocation requires exact reservation') END;\n"
    "END;\n"
    "CREATE TRIGGER gate_release_proof BEFORE UPDATE ON smtp_account_gate BEGIN\n"
    " SELECT CASE WHEN NEW.generation<OLD.generation OR NEW.account_key<>OLD.account_key\n"
    " THEN RAISE(ABORT,'gate identity regression') END;\n"
    " SELECT CASE WHEN NOT(OLD.state=NEW.state\n"
    " OR (OLD.state='FREE' AND NEW.state='RESERVED')\n"
    " OR (OLD.state='RESERVED' AND NEW.state IN('INFLIGHT','FREE'))\n"
    " OR (OLD.state='INFLIGHT' AND NEW.state IN('BLOCKED','FREE'))\n"
    " OR (OLD.state='BLOCKED' AND NEW.state='FREE'))\n"
    " THEN RAISE(ABORT,'illegal gate transition') END;\n"
    " SELECT CASE WHEN OLD.state='FREE' AND NEW.state='RESERVED' AND NEW.generation<>OLD.generation+1\n"
    " THEN RAISE(ABORT,'reservation increments gate generation') END;\n"
    " SELECT CASE WHEN NOT(OLD.state='FREE' AND NEW.state='RESERVED') AND NEW.generation<>OLD.generation\n"
    " THEN RAISE(ABORT,'gate generation changes only on reservation') END;\n"
    " SELECT CASE WHEN OLD.state='RESERVED' AND NEW.state='FREE' AND EXISTS(\n"
    " SELECT 1 FROM transport_invocations v WHERE v.account_key=OLD.account_key\n"
    " AND v.gate_generation=OLD.generation AND v.submission_key=OLD.submission_key)\n"
    " THEN RAISE(ABORT,'reservation with invocation requires end pathway') END;\n"
    " SELECT CASE WHEN OLD.state IN('INFLIGHT','BLOCKED') AND NEW.state='FREE' AND NOT EXISTS(\n"
    " SELECT 1 FROM transport_facts f WHERE f.invocation_id=OLD.invocation_id\n"
    " AND f.fact_kind IN('ENDED','PROCESS_EXIT_CONFIRMED'))\n"
    " THEN RAISE(ABORT,'transport end proof required') END;\n"
    " SELECT CASE WHEN OLD.state IN('INFLIGHT','BLOCKED') AND NEW.state NOT IN('FREE','BLOCKED',OLD.state)\n"
    " THEN RAISE(ABORT,'cannot reuse active gate') END;\n"
    " SELECT CASE WHEN OLD.state IN('INFLIGHT','BLOCKED') AND NEW.state<>'FREE' AND\n"
    " (NEW.invocation_id IS NOT OLD.invocation_id OR NEW.submission_key IS NOT OLD.submission_key\n"
    " OR NEW.generation<>OLD.generation OR NEW.owner_token IS NOT OLD.owner_token\n"
    " OR NEW.runtime_epoch IS NOT OLD.runtime_epoch OR NEW.delivery_id IS NOT OLD.delivery_id)\n"
    " THEN RAISE(ABORT,'active invocation identity locked') END;\n"
    "END;\n"
    "CREATE TRIGGER resolution_scope BEFORE INSERT ON delivery_resolutions BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM notification_authorizations a\n"
    " JOIN notification_deliveries_v2 d ON d.company_key=a.company_key\n"
    " WHERE a.authorization_id=NEW.authorization_id AND a.purpose='RESOLVE'\n"
    " AND d.delivery_id=NEW.delivery_id AND d.status='UNCERTAIN' AND d.invocation_id=NEW.invocation_id)\n"
    " THEN RAISE(ABORT,'resolution scope mismatch') END;\n"
    "END;\n"
    "CREATE TRIGGER batch_header_immutable BEFORE UPDATE OF source_key,intake_generation,admission_key,\n"
    " payload_id,observed_ms,admitted_ms,source_contract,normalizer_version,config_version,policy_version,\n"
    " record_count,seen_digest,completeness ON source_batches\n"
    "BEGIN SELECT RAISE(ABORT,'immutable accepted observation'); END;\n"
    "CREATE TRIGGER item_payload_immutable BEFORE UPDATE OF batch_id,item_index,trusted_company_id,payload_id ON source_batch_items\n"
    "BEGIN SELECT RAISE(ABORT,'immutable accepted item'); END;\n"
    "CREATE TRIGGER item_terminal BEFORE UPDATE ON source_batch_items WHEN OLD.status<>'PENDING'\n"
    "BEGIN SELECT RAISE(ABORT,'terminal item; correction requires new batch'); END;\n"
    "CREATE TRIGGER batch_cursor_guard BEFORE UPDATE ON source_batches BEGIN\n"
    " SELECT CASE WHEN NEW.positive_cursor<OLD.positive_cursor OR NEW.reconcile_cursor<OLD.reconcile_cursor\n"
    " OR (OLD.reconciliation_allowed=0 AND NEW.reconciliation_allowed=1)\n"
    " THEN RAISE(ABORT,'batch progress/authority regression') END;\n"
    " SELECT CASE WHEN OLD.status IN('COMPLETE','COMPLETE_WITH_HOLDS') THEN RAISE(ABORT,'terminal batch') END;\n"
    "END;\n"
    "CREATE TRIGGER protected_payload_insert_guard BEFORE INSERT ON protected_payloads\n"
    "WHEN EXISTS(SELECT 1 FROM privacy_actions p WHERE p.target_kind='PAYLOAD'\n"
    " AND p.target_key=NEW.payload_id AND p.action='ERASE')\n"
    "BEGIN SELECT RAISE(ABORT,'erased payload identity cannot be rebound'); END;\n"
    "CREATE TRIGGER association_insert_guard BEFORE INSERT ON recipient_associations\n"
    "WHEN EXISTS(SELECT 1 FROM privacy_actions p WHERE p.target_kind='RECIPIENT'\n"
    " AND p.target_key=NEW.recipient_key AND p.action='DEIDENTIFY')\n"
    "BEGIN SELECT RAISE(ABORT,'deidentified recipient cannot be rebound'); END;\n"
    "CREATE TRIGGER protected_payload_erase BEFORE DELETE ON protected_payloads\n"
    "WHEN NOT EXISTS(SELECT 1 FROM privacy_actions p WHERE p.target_kind='PAYLOAD'\n"
    " AND p.target_key=OLD.payload_id AND p.action='ERASE')\n"
    "BEGIN SELECT RAISE(ABORT,'authorized privacy action required'); END;\n"
    "CREATE TRIGGER association_erase BEFORE DELETE ON recipient_associations\n"
    "WHEN NOT EXISTS(SELECT 1 FROM privacy_actions p WHERE p.target_kind='RECIPIENT'\n"
    " AND p.target_key=OLD.recipient_key AND p.action='DEIDENTIFY')\n"
    "BEGIN SELECT RAISE(ABORT,'authorized association erasure required'); END;\n"
    "-- Installed only after M3 sidecar seeding; current old APIs must be excluded.\n"
    "CREATE TRIGGER subscriber_create AFTER INSERT ON users BEGIN\n"
    " INSERT INTO subscriber_versions VALUES(NEW.id,0,0,CAST(strftime('%s','now') AS INTEGER)*1000);\n"
    "END;\n"
    "CREATE TRIGGER subscriber_mutation_guard BEFORE UPDATE ON users WHEN\n"
    " NEW.is_active IS NOT OLD.is_active OR NEW.is_verified IS NOT OLD.is_verified OR\n"
    " NEW.email IS NOT OLD.email OR NEW.branch_canonical IS NOT OLD.branch_canonical OR\n"
    " NEW.graduation_year IS NOT OLD.graduation_year\n"
    "BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM subscriber_versions WHERE user_id=OLD.id)\n"
    " THEN RAISE(ABORT,'missing subscriber versions') END;\n"
    "END;\n"
    "CREATE TRIGGER subscriber_epochs AFTER UPDATE ON users BEGIN\n"
    " UPDATE subscriber_versions SET\n"
    " consent_epoch=consent_epoch+CASE WHEN NEW.is_active IS NOT OLD.is_active OR NEW.is_verified IS NOT OLD.is_verified THEN 1 ELSE 0 END,\n"
    " profile_epoch=profile_epoch+CASE WHEN NEW.email IS NOT OLD.email OR NEW.branch_canonical IS NOT OLD.branch_canonical\n"
    " OR NEW.graduation_year IS NOT OLD.graduation_year THEN 1 ELSE 0 END\n"
    " WHERE user_id=NEW.id;\n"
    "END;\n"
    "CREATE TRIGGER prefs_insert_guard BEFORE INSERT ON user_preferences BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM subscriber_versions WHERE user_id=NEW.user_id)\n"
    " THEN RAISE(ABORT,'missing preferences version') END;\n"
    "END;\n"
    "CREATE TRIGGER prefs_insert_epoch AFTER INSERT ON user_preferences BEGIN\n"
    " UPDATE subscriber_versions SET profile_epoch=profile_epoch+1 WHERE user_id=NEW.user_id;\n"
    "END;\n"
    "CREATE TRIGGER prefs_update_guard BEFORE UPDATE ON user_preferences BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM subscriber_versions WHERE user_id=NEW.user_id)\n"
    " THEN RAISE(ABORT,'missing preferences version') END;\n"
    "END;\n"
    "CREATE TRIGGER prefs_update_epoch AFTER UPDATE ON user_preferences WHEN NEW.pref_internship IS NOT OLD.pref_internship OR NEW.pref_placement IS NOT OLD.pref_placement OR NEW.pref_ppo IS NOT OLD.pref_ppo BEGIN\n"
    " UPDATE subscriber_versions SET profile_epoch=profile_epoch+1 WHERE user_id=NEW.user_id;\n"
    "END;\n"
    "CREATE TRIGGER prefs_delete_guard BEFORE DELETE ON user_preferences BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM subscriber_versions WHERE user_id=OLD.user_id)\n"
    " THEN RAISE(ABORT,'missing preferences version') END;\n"
    "END;\n"
    "CREATE TRIGGER prefs_delete_epoch AFTER DELETE ON user_preferences BEGIN\n"
    " UPDATE subscriber_versions SET profile_epoch=profile_epoch+1 WHERE user_id=OLD.user_id;\n"
    "END;\n"
    "CREATE TRIGGER notification_schema_migrations_no_update BEFORE UPDATE ON notification_schema_migrations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only notification_schema_migrations'); END;\n"
    "CREATE TRIGGER notification_schema_migrations_no_delete BEFORE DELETE ON notification_schema_migrations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only notification_schema_migrations'); END;\n"
    "CREATE TRIGGER payload_tombstones_no_update BEFORE UPDATE ON payload_tombstones\n"
    "BEGIN SELECT RAISE(ABORT,'append-only payload_tombstones'); END;\n"
    "CREATE TRIGGER payload_tombstones_no_delete BEFORE DELETE ON payload_tombstones\n"
    "BEGIN SELECT RAISE(ABORT,'append-only payload_tombstones'); END;\n"
    "CREATE TRIGGER privacy_actions_no_update BEFORE UPDATE ON privacy_actions\n"
    "BEGIN SELECT RAISE(ABORT,'append-only privacy_actions'); END;\n"
    "CREATE TRIGGER privacy_actions_no_delete BEFORE DELETE ON privacy_actions\n"
    "BEGIN SELECT RAISE(ABORT,'append-only privacy_actions'); END;\n"
    "CREATE TRIGGER recipient_handles_no_update BEFORE UPDATE ON recipient_handles\n"
    "BEGIN SELECT RAISE(ABORT,'append-only recipient_handles'); END;\n"
    "CREATE TRIGGER recipient_handles_no_delete BEFORE DELETE ON recipient_handles\n"
    "BEGIN SELECT RAISE(ABORT,'append-only recipient_handles'); END;\n"
    "CREATE TRIGGER opportunity_events_no_update BEFORE UPDATE ON opportunity_events\n"
    "BEGIN SELECT RAISE(ABORT,'append-only opportunity_events'); END;\n"
    "CREATE TRIGGER opportunity_events_no_delete BEFORE DELETE ON opportunity_events\n"
    "BEGIN SELECT RAISE(ABORT,'append-only opportunity_events'); END;\n"
    "CREATE TRIGGER event_routes_no_update BEFORE UPDATE ON event_routes\n"
    "BEGIN SELECT RAISE(ABORT,'append-only event_routes'); END;\n"
    "CREATE TRIGGER event_routes_no_delete BEFORE DELETE ON event_routes\n"
    "BEGIN SELECT RAISE(ABORT,'append-only event_routes'); END;\n"
    "CREATE TRIGGER event_relations_no_update BEFORE UPDATE ON event_relations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only event_relations'); END;\n"
    "CREATE TRIGGER event_relations_no_delete BEFORE DELETE ON event_relations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only event_relations'); END;\n"
    "CREATE TRIGGER notification_authorizations_no_update BEFORE UPDATE ON notification_authorizations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only notification_authorizations'); END;\n"
    "CREATE TRIGGER notification_authorizations_no_delete BEFORE DELETE ON notification_authorizations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only notification_authorizations'); END;\n"
    "CREATE TRIGGER notification_intents_no_update BEFORE UPDATE ON notification_intents\n"
    "BEGIN SELECT RAISE(ABORT,'append-only notification_intents'); END;\n"
    "CREATE TRIGGER notification_intents_no_delete BEFORE DELETE ON notification_intents\n"
    "BEGIN SELECT RAISE(ABORT,'append-only notification_intents'); END;\n"
    "CREATE TRIGGER event_audience_no_update BEFORE UPDATE ON event_audience\n"
    "BEGIN SELECT RAISE(ABORT,'append-only event_audience'); END;\n"
    "CREATE TRIGGER event_audience_no_delete BEFORE DELETE ON event_audience\n"
    "BEGIN SELECT RAISE(ABORT,'append-only event_audience'); END;\n"
    "CREATE TRIGGER delivery_attempts_no_update BEFORE UPDATE ON delivery_attempts\n"
    "BEGIN SELECT RAISE(ABORT,'append-only delivery_attempts'); END;\n"
    "CREATE TRIGGER delivery_attempts_no_delete BEFORE DELETE ON delivery_attempts\n"
    "BEGIN SELECT RAISE(ABORT,'append-only delivery_attempts'); END;\n"
    "CREATE TRIGGER delivery_attempt_facts_no_update BEFORE UPDATE ON delivery_attempt_facts\n"
    "BEGIN SELECT RAISE(ABORT,'append-only delivery_attempt_facts'); END;\n"
    "CREATE TRIGGER delivery_attempt_facts_no_delete BEFORE DELETE ON delivery_attempt_facts\n"
    "BEGIN SELECT RAISE(ABORT,'append-only delivery_attempt_facts'); END;\n"
    "CREATE TRIGGER transport_invocations_no_update BEFORE UPDATE ON transport_invocations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only transport_invocations'); END;\n"
    "CREATE TRIGGER transport_invocations_no_delete BEFORE DELETE ON transport_invocations\n"
    "BEGIN SELECT RAISE(ABORT,'append-only transport_invocations'); END;\n"
    "CREATE TRIGGER transport_facts_no_update BEFORE UPDATE ON transport_facts\n"
    "BEGIN SELECT RAISE(ABORT,'append-only transport_facts'); END;\n"
    "CREATE TRIGGER transport_facts_no_delete BEFORE DELETE ON transport_facts\n"
    "BEGIN SELECT RAISE(ABORT,'append-only transport_facts'); END;\n"
    "CREATE TRIGGER delivery_resolutions_no_update BEFORE UPDATE ON delivery_resolutions\n"
    "BEGIN SELECT RAISE(ABORT,'append-only delivery_resolutions'); END;\n"
    "CREATE TRIGGER delivery_resolutions_no_delete BEFORE DELETE ON delivery_resolutions\n"
    "BEGIN SELECT RAISE(ABORT,'append-only delivery_resolutions'); END;\n"
    "CREATE TRIGGER legacy_notification_dispositions_no_update BEFORE UPDATE ON legacy_notification_dispositions\n"
    "BEGIN SELECT RAISE(ABORT,'append-only legacy_notification_dispositions'); END;\n"
    "CREATE TRIGGER legacy_notification_dispositions_no_delete BEFORE DELETE ON legacy_notification_dispositions\n"
    "BEGIN SELECT RAISE(ABORT,'append-only legacy_notification_dispositions'); END;\n"
    "CREATE TRIGGER source_batches_no_delete BEFORE DELETE ON source_batches\n"
    "BEGIN SELECT RAISE(ABORT,'preserve source_batches'); END;\n"
    "CREATE TRIGGER source_batch_items_no_delete BEFORE DELETE ON source_batch_items\n"
    "BEGIN SELECT RAISE(ABORT,'preserve source_batch_items'); END;\n"
    "CREATE TRIGGER source_reconciliation_work_no_delete BEFORE DELETE ON source_reconciliation_work\n"
    "BEGIN SELECT RAISE(ABORT,'preserve source_reconciliation_work'); END;\n"
    "CREATE TRIGGER opportunity_state_no_delete BEFORE DELETE ON opportunity_state\n"
    "BEGIN SELECT RAISE(ABORT,'preserve opportunity_state'); END;\n"
    "CREATE TRIGGER event_outbox_no_delete BEFORE DELETE ON event_outbox\n"
    "BEGIN SELECT RAISE(ABORT,'preserve event_outbox'); END;\n"
    "CREATE TRIGGER notification_deliveries_v2_no_delete BEFORE DELETE ON notification_deliveries_v2\n"
    "BEGIN SELECT RAISE(ABORT,'preserve notification_deliveries_v2'); END;\n"
    "CREATE TRIGGER subscriber_versions_no_delete BEFORE DELETE ON subscriber_versions\n"
    "BEGIN SELECT RAISE(ABORT,'preserve subscriber_versions'); END;\n"
    "CREATE TRIGGER smtp_account_gate_no_delete BEFORE DELETE ON smtp_account_gate\n"
    "BEGIN SELECT RAISE(ABORT,'preserve smtp_account_gate'); END;\n"
    "CREATE TRIGGER protected_payloads_no_update BEFORE UPDATE ON protected_payloads\n"
    "BEGIN SELECT RAISE(ABORT,'replace by new identity or authorized erasure'); END;\n"
    "CREATE TRIGGER recipient_associations_no_update BEFORE UPDATE ON recipient_associations\n"
    "BEGIN SELECT RAISE(ABORT,'replace by new identity or authorized erasure'); END;\n"
    "\n"
    "CREATE TRIGGER item_insert_guard BEFORE INSERT ON source_batch_items BEGIN\n"
    " SELECT CASE WHEN NEW.status<>'PENDING' OR NOT EXISTS(\n"
    " SELECT 1 FROM source_batches b WHERE b.batch_id=NEW.batch_id\n"
    " AND b.status IN('ACCEPTED','HELD_GLOBAL') AND b.positive_cursor=0\n"
    " AND NEW.item_index<b.record_count)\n"
    " THEN RAISE(ABORT,'initial indexed item must be pending in admission') END;\n"
    "END;\n"
    "CREATE TRIGGER reconcile_insert_guard BEFORE INSERT ON source_reconciliation_work BEGIN\n"
    " SELECT CASE WHEN NEW.status<>'PENDING' OR NOT EXISTS(\n"
    " SELECT 1 FROM source_batches b WHERE b.batch_id=NEW.batch_id\n"
    " AND b.status='ACCEPTED' AND b.positive_cursor=0 AND b.reconciliation_allowed=1\n"
    " AND b.completeness='VERIFIED_COMPLETE')\n"
    " THEN RAISE(ABORT,'initial frozen reconciliation must be pending') END;\n"
    "END;\n"
    "CREATE TRIGGER reconcile_identity_guard BEFORE UPDATE OF batch_id,company_key ON source_reconciliation_work\n"
    "BEGIN SELECT RAISE(ABORT,'immutable frozen reconciliation identity'); END;\n"
    "CREATE TRIGGER reconcile_authority BEFORE INSERT ON opportunity_events WHEN NEW.origin='RECONCILE' BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM source_batches b\n"
    " WHERE b.batch_id=NEW.batch_id AND b.status='INGESTING'\n"
    " AND b.reconciliation_allowed=1 AND b.completeness='VERIFIED_COMPLETE'\n"
    " AND b.positive_cursor=b.record_count\n"
    " AND (SELECT count(*) FROM source_batch_items i WHERE i.batch_id=b.batch_id)=b.record_count\n"
    " AND NOT EXISTS(SELECT 1 FROM source_batch_items i WHERE i.batch_id=b.batch_id\n"
    " AND i.status IN('PENDING','QUARANTINED'))\n"
    " AND EXISTS(SELECT 1 FROM source_reconciliation_work w WHERE w.batch_id=b.batch_id\n"
    " AND w.company_key=NEW.company_key AND w.status='PENDING'))\n"
    " THEN RAISE(ABORT,'absence requires completed trustworthy positive accounting') END;\n"
    "END;\n"
    "CREATE TRIGGER batch_initial_guard BEFORE INSERT ON source_batches BEGIN\n"
    " SELECT CASE WHEN NEW.status NOT IN('ACCEPTED','HELD_GLOBAL') OR NEW.positive_cursor<>0\n"
    " OR NEW.reconcile_cursor<>0 OR NEW.completed_ms IS NOT NULL\n"
    " THEN RAISE(ABORT,'invalid initial admission state') END;\n"
    "END;\n"
    "CREATE TRIGGER batch_finalize_guard BEFORE UPDATE OF status ON source_batches\n"
    "WHEN NEW.status IN('COMPLETE','COMPLETE_WITH_HOLDS') BEGIN\n"
    " SELECT CASE WHEN OLD.status<>'INGESTING' OR NEW.positive_cursor<>NEW.record_count\n"
    " OR (SELECT count(*) FROM source_batch_items i WHERE i.batch_id=NEW.batch_id)<>NEW.record_count\n"
    " OR EXISTS(SELECT 1 FROM source_batch_items i WHERE i.batch_id=NEW.batch_id AND i.status='PENDING')\n"
    " OR EXISTS(SELECT 1 FROM source_reconciliation_work w WHERE w.batch_id=NEW.batch_id AND w.status='PENDING')\n"
    " OR NEW.reconcile_cursor<>(SELECT count(*) FROM source_reconciliation_work w WHERE w.batch_id=NEW.batch_id)\n"
    " THEN RAISE(ABORT,'cannot complete unfinished or unaccounted batch') END;\n"
    " SELECT CASE WHEN (NEW.status='COMPLETE_WITH_HOLDS')<>(\n"
    " EXISTS(SELECT 1 FROM source_batch_items i WHERE i.batch_id=NEW.batch_id AND i.status='QUARANTINED')\n"
    " OR EXISTS(SELECT 1 FROM source_reconciliation_work w WHERE w.batch_id=NEW.batch_id AND w.status='HELD'))\n"
    " THEN RAISE(ABORT,'batch completion must reflect held dispositions') END;\n"
    "END;\n"
    "CREATE TRIGGER batch_recovery_guard BEFORE UPDATE OF status ON source_batches BEGIN\n"
    " SELECT CASE WHEN OLD.status='HELD_GLOBAL' AND NEW.status<>'HELD_GLOBAL'\n"
    " AND (NEW.status<>'INGESTING' OR NEW.resolution_authorization IS NULL\n"
    " OR NEW.resolution_authorization IS OLD.resolution_authorization)\n"
    " THEN RAISE(ABORT,'global hold requires fresh scoped recovery authorization') END;\n"
    "END;\n"
    "CREATE TRIGGER item_event_scope BEFORE UPDATE OF event_id ON source_batch_items WHEN NEW.event_id IS NOT NULL BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM opportunity_events e WHERE e.event_id=NEW.event_id\n"
    " AND e.company_key=NEW.company_key AND e.batch_id=NEW.batch_id AND e.item_index=NEW.item_index AND e.origin='FETCH')\n"
    " THEN RAISE(ABORT,'item event mismatch') END;\n"
    "END;\n"
    "CREATE TRIGGER reconcile_event_scope BEFORE UPDATE OF event_id ON source_reconciliation_work WHEN NEW.event_id IS NOT NULL BEGIN\n"
    " SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM opportunity_events e WHERE e.event_id=NEW.event_id\n"
    " AND e.company_key=NEW.company_key AND e.batch_id=NEW.batch_id AND e.origin='RECONCILE')\n"
    " THEN RAISE(ABORT,'reconciliation event mismatch') END;\n"
    "END;\n"
    "CREATE TRIGGER reconcile_terminal BEFORE UPDATE ON source_reconciliation_work WHEN OLD.status<>'PENDING'\n"
    "BEGIN SELECT RAISE(ABORT,'terminal reconciliation disposition'); END;\n"
    "\n"
)

SCHEMA_SQL = SOURCE_SQL
SCHEMA_SHA256 = hashlib.sha256(SCHEMA_SQL.encode()).hexdigest()


class SchemaError(RuntimeError):
    """Mismatch or invalid fictional legacy input; never repair destructively."""


def _root():
    guard = sys.modules.get("isolation_bootstrap")
    root = getattr(guard, "ROOT", None)
    if root is None or not getattr(guard, "_installed", False):
        raise SchemaError("installed disposable isolation guard required")
    return Path(root).resolve()


def _inside(path):
    candidate = Path(path)
    if not candidate.is_absolute() or not candidate.resolve().is_relative_to(_root()):
        raise SchemaError("explicit disposable database path required")
    return candidate.resolve()


@contextmanager
def connect_disposable(path, *, busy_timeout_ms=100):
    """Own and close a configured test connection; no implicit/default path."""
    path = _inside(path)
    if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 1000:
        raise SchemaError("bounded busy timeout required")
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=" + str(busy_timeout_ms))
        if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
            raise SchemaError("WAL unavailable")
        require_connection(conn)
        yield conn
    finally:
        conn.close()


def require_connection(conn):
    _root()
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise SchemaError("SQLite STRICT >=3.37 required")
    databases = conn.execute("PRAGMA database_list").fetchall()
    for row in databases:
        if row[1] != "temp":
            _inside(row[2])
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise SchemaError("foreign_keys must be enabled before BEGIN")


@contextmanager
def atomic(conn):
    require_connection(conn)
    if conn.in_transaction:
        raise SchemaError("unit owns transaction; nesting/hidden commits forbidden")
    from app.database.protected_payloads import require_binding
    codec=require_binding(conn)
    if codec:codec.begin_unit(conn)
    try:conn.execute("BEGIN IMMEDIATE")
    except BaseException:
        if codec:codec.finish_unit(conn)
        raise
    try:
        yield
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
            if codec:codec.finish_unit(conn)
        raise
    else:
        if codec:codec.finish_unit(conn)


def statements(sql=SCHEMA_SQL):
    """Use SQLite's statement boundary recognizer, including trigger bodies."""
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            yield buffer.strip()
            buffer = ""
    if buffer.strip():
        raise SchemaError("incomplete schema statement")


def _definition(statement):
    match = re.search(r"CREATE\s+(?:UNIQUE\s+)?(TABLE|INDEX|TRIGGER)\s+(\w+)", statement, re.I)
    if match is None:
        raise SchemaError("unknown definition")
    start = statement.index("CREATE", match.start())
    return match[1].lower(), match[2], statement[start:]


def _canonical(sql):
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).strip()


def payload(conn, purpose, data, now_ms, *, key=None, owner=None):
    from app.database.protected_payloads import binding, require_binding
    codec = binding(conn)
    if codec:
        return codec.insert(conn, purpose, data, now_ms, key, owner=owner)
    require_binding(conn)
    key = key or uuid.uuid4().hex
    conn.execute("INSERT INTO payload_tombstones VALUES(?,?,?)", (key, purpose, now_ms))
    # Opaque fixture storage exercises shape, NOT cryptography.
    opaque = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    conn.execute("INSERT INTO protected_payloads VALUES(?,?,?)", (key, opaque, "fictional-key"))
    return key


def _legacy_preflight(conn):
    required = {
        "companies": {"id": "TEXT", "raw_data_json": "TEXT"},
        "users": {"id": "INTEGER", "email": "TEXT", "graduation_year": "INTEGER", "branch_canonical": "TEXT"},
        "user_preferences": {"user_id": "INTEGER", "pref_internship": "INTEGER", "pref_placement": "INTEGER", "pref_ppo": "INTEGER"},
        "notification_deliveries": {"id": "INTEGER", "user_id": "INTEGER", "company_id": "TEXT", "status": "TEXT", "attempt_count": "INTEGER", "max_attempts": "INTEGER"},
        "pending_notifications": {"id": "INTEGER", "company_id": "TEXT"},
        "action_tokens": {"user_id": "INTEGER", "token_hash": "TEXT"},
        "system_state": {"key": "TEXT", "value": "TEXT"},
    }
    for table, columns in required.items():
        actual = {r[1]: r[2].upper() for r in conn.execute("PRAGMA table_info(" + table + ")")}
        if any(actual.get(k) != v for k, v in columns.items()):
            raise SchemaError("legacy shape mismatch: " + table)
    if conn.execute("SELECT 1 FROM companies WHERE id IS NULL OR typeof(id)<>'text' LIMIT 1").fetchone():
        raise SchemaError("legacy company identity invalid")
    if conn.execute("""SELECT 1 FROM notification_deliveries d
            LEFT JOIN companies c ON c.id=d.company_id LEFT JOIN users u ON u.id=d.user_id
            WHERE c.id IS NULL OR u.id IS NULL OR d.status NOT IN('SENT','FAILED','PENDING','PROCESSING')
            OR typeof(d.attempt_count)<>'integer' OR typeof(d.max_attempts)<>'integer'
            OR d.attempt_count<0 OR d.max_attempts<1 OR d.attempt_count>d.max_attempts LIMIT 1""").fetchone():
        raise SchemaError("legacy delivery status/attempt/reference requires explicit disposition")
    if conn.execute("""SELECT 1 FROM pending_notifications p LEFT JOIN companies c ON c.id=p.company_id
            WHERE c.id IS NULL LIMIT 1""").fetchone() or conn.execute("PRAGMA foreign_key_check").fetchall():
        raise SchemaError("legacy orphan/null reference requires explicit disposition")


def _seed_legacy(conn, now_ms):
    company_keys = {}
    for old in conn.execute("SELECT * FROM companies ORDER BY id").fetchall():
        key = uuid.uuid4().hex
        company_keys[old["id"]] = key
        snapshot = payload(conn, "SNAPSHOT", dict(old), now_ms, owner={'company':old['id']})
        material = hashlib.sha256((old["raw_data_json"] or "").encode()).hexdigest()
        conn.execute("""INSERT INTO opportunity_state
            (company_key,source_key,legacy_company_id,revision,head_event_id,snapshot_payload_id,
             material_digest,normalizer_version,matcher_version,updated_ms)
            VALUES(?,?,?,0,NULL,?,?,?, ?,?)""",
            (key, "fictional", old["id"], snapshot, material, "F-S0", "F-S0", now_ms))
    for old in conn.execute("SELECT id FROM users").fetchall():
        conn.execute("INSERT INTO subscriber_versions VALUES(?,0,0,?)", (old[0], now_ms))
        handle = uuid.uuid4().hex
        conn.execute("INSERT INTO recipient_handles VALUES(?,'SUBSCRIBER',?)", (handle, now_ms))
        conn.execute("INSERT INTO recipient_associations(recipient_key,user_id) VALUES(?,?)", (handle, old[0]))
    for table, kind in (("notification_deliveries", "SUBSCRIBER"), ("pending_notifications", "ADMIN")):
        for row in conn.execute("SELECT * FROM " + table + " ORDER BY id").fetchall():
            status = row["status"] if kind == "SUBSCRIBER" else "PENDING"
            decision = "HISTORICAL_ONLY" if status in ("SENT", "FAILED") else "HOLD_REVIEW"
            digest = hashlib.sha256(json.dumps(dict(row), sort_keys=True).encode()).hexdigest()
            conn.execute("INSERT INTO legacy_notification_dispositions VALUES(?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, kind, row["id"], company_keys[row["company_id"]],
                          status, digest, decision, now_ms))


def create_schema(conn, *, now_ms, fault=None):
    """Atomic explicit additive migration, OFF; safe idempotent verification."""
    require_connection(conn)
    if type(now_ms) is not int or now_ms < 0:
        raise SchemaError("supplied nonnegative clock required")
    from app.database.protected_payloads import binding, require_binding, MARKER
    codec = binding(conn)
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    installed = 'notification_schema_migrations' in present
    if codec and not installed:
        if present & {'notification_runtime','subscriber_versions','protected_payloads'}:
            raise SchemaError("partial notification schema requires maintenance")
        _legacy_preflight(conn)
        _protected_preflight(conn)
        fingerprint = _legacy_fingerprint(conn)
        for old in conn.execute("SELECT * FROM companies ORDER BY id").fetchall():
            codec.prepare('SNAPSHOT', dict(old), {'company':old['id']})
        if fault: fault('prepared')
    else:
        fingerprint = None
        require_binding(conn)
    with atomic(conn):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='notification_schema_migrations'").fetchone():
            verify_schema(conn)
            if codec:_verify_protected_manifest(conn)
            return
        _legacy_preflight(conn)
        if codec:
            _protected_preflight(conn)
            if fingerprint != _legacy_fingerprint(conn):
                raise SchemaError("legacy changed after protected preparation")
        definitions = list(statements())
        seeded = False
        for statement in definitions:
            kind, name, _ = _definition(statement)
            if kind == "trigger" and not seeded:
                _seed_legacy(conn, now_ms)
                seeded = True
                if fault:
                    fault("seeded")
            conn.execute(statement)
            if fault:
                fault(name)
        conn.execute("""INSERT INTO notification_runtime
            (singleton,epoch,config_version,policy_version,writer_mode,fanout_mode,send_mode,intake_generation)
            VALUES(1,0,'F-S0','F-S0','OFF','OFF','OFF',0)""")
        conn.execute("""INSERT INTO smtp_account_gate
            (account_key,state,generation,next_allowed_ms,updated_ms) VALUES('account-1','FREE',0,0,?)""", (now_ms,))
        conn.execute("INSERT INTO notification_schema_migrations VALUES(?,?,?,?)",
                     (VERSION, SCHEMA_SHA256, "FICTIONAL_ONLY", now_ms))
        if codec:
            conn.execute("INSERT INTO system_state(key,value,updated_at) VALUES(?,?,?)",(MARKER,codec.namespace,str(now_ms)))
            manifest=dict(companies=[tuple(r) for r in conn.execute('SELECT company_key,legacy_company_id,snapshot_payload_id FROM opportunity_state ORDER BY company_key')],
                recipients=[r[0] for r in conn.execute('SELECT recipient_key FROM recipient_handles ORDER BY recipient_key')],
                dispositions=[tuple(r) for r in conn.execute('SELECT disposition_id,row_digest FROM legacy_notification_dispositions ORDER BY disposition_id')])
            conn.execute("INSERT INTO system_state(key,value,updated_at) VALUES('r2bc:migration_manifest',?,?)",(json.dumps(manifest,sort_keys=True),str(now_ms)))
        verify_schema(conn)
        if codec:
            _verify_protected_manifest(conn)
            if fault:fault('before_commit')
    if codec and fault: fault('committed')


def _legacy_fingerprint(conn):
    tables = ('companies','users','user_preferences','action_tokens','notification_deliveries','pending_notifications','system_state')
    return hashlib.sha256(json.dumps({t:[tuple(r) for r in conn.execute('SELECT * FROM '+t+' ORDER BY 1')]
        for t in tables},sort_keys=True).encode()).hexdigest()


def _verify_protected_manifest(conn):
    row=conn.execute("SELECT value FROM system_state WHERE key='r2bc:migration_manifest'").fetchone()
    try:
        manifest=json.loads(row[0])
        if set(manifest)!= {'companies','recipients','dispositions'}:raise ValueError()
        for key,legacy,payload_id in manifest['companies']:
            state=conn.execute('SELECT legacy_company_id FROM opportunity_state WHERE company_key=?',(key,)).fetchone()
            if not state or state[0]!=legacy or not conn.execute('SELECT 1 FROM payload_tombstones WHERE payload_id=?',(payload_id,)).fetchone():raise ValueError()
        for key in manifest['recipients']:
            if not conn.execute('SELECT 1 FROM recipient_handles WHERE recipient_key=?',(key,)).fetchone():raise ValueError()
        for key,digest in manifest['dispositions']:
            state=conn.execute('SELECT row_digest FROM legacy_notification_dispositions WHERE disposition_id=?',(key,)).fetchone()
            if not state or state[0]!=digest:raise ValueError()
    except (ValueError,TypeError,KeyError,IndexError):raise SchemaError('protected migration manifest mismatch') from None


def _protected_preflight(conn):
    """Strict opt-in experiment; frozen F-S0 fixtures retain their old contract."""
    from datetime import datetime
    def reject(): raise SchemaError('legacy protected preflight requires explicit disposition')
    def stamp(value, required=False, projection=False):
        if value is None and not required:return
        if not isinstance(value,str):reject()
        try:
            parsed=datetime.fromisoformat(value)
            if parsed.tzinfo is None:reject()
            if projection and (parsed.utcoffset().total_seconds()!=0 or parsed.isoformat()!=value):reject()
        except (ValueError,TypeError):reject()
    def text(value):
        if not isinstance(value,str) or not value.strip():reject()
    for row in conn.execute('SELECT * FROM companies'):
        text(row['id']);text(row['company'])
        if row['id'] != row['id'].strip() or row['is_active'] not in ('True','False'):reject()
        for field in ('first_seen_at','last_seen_at'):stamp(row[field],True,True)
        stamp(row['last_notified_at'])
        try:raw=json.loads(row['raw_data_json'])
        except (ValueError,TypeError):reject()
        if not isinstance(raw,dict) or type(raw.get('id')) is not str or raw['id']!=row['id'] or raw.get('isactive')!=row['is_active']:reject()
        from app.tpo.models import CompanyRecord
        try:CompanyRecord(**raw)
        except (ValueError,TypeError):reject()
    for row in conn.execute('SELECT * FROM users'):
        text(row['email']);text(row['branch_canonical'])
        if any(type(row[k]) is not int or row[k] not in (0,1) for k in ('is_verified','is_active')):reject()
        if row['graduation_year']!=2028:reject()
        stamp(row['created_at'],True);stamp(row['verified_at']);stamp(row['updated_at'])
        if conn.execute('SELECT count(*) FROM user_preferences WHERE user_id=?',(row['id'],)).fetchone()[0]!=1:reject()
    for row in conn.execute('SELECT * FROM user_preferences'):
        if any(type(row[k]) is not int or row[k] not in (0,1) for k in ('pref_internship','pref_placement','pref_ppo')):reject()
        stamp(row['updated_at'],True)
    for row in conn.execute('SELECT * FROM action_tokens'):
        if row['token_type'] not in ('SIGNUP_VERIFY','MANAGE_PREFS','UNSUBSCRIBE'):reject()
        if not isinstance(row['token_hash'],str) or len(row['token_hash'])!=64 or any(c not in '0123456789abcdef' for c in row['token_hash']):reject()
        stamp(row['created_at'],True);stamp(row['expires_at'],True);stamp(row['used_at'])
    for row in conn.execute('SELECT * FROM notification_deliveries'):
        if row['status'] not in ('SENT','FAILED','PENDING','PROCESSING') or row['notification_type'] not in ('NEW','UPDATE','INFO'):reject()
        for k in ('created_at',):stamp(row[k],True)
        for k in ('lease_expires_at','last_attempt_at','sent_at'):stamp(row[k])
    for row in conn.execute('SELECT * FROM pending_notifications'):
        if row['notification_type'] not in ('NEW','UPDATE','INFO'):reject()
        stamp(row['created_at'],True)
        if row['changes_json'] is not None:
            try:json.loads(row['changes_json'])
            except (ValueError,TypeError):reject()


def verify_schema(conn):
    require_connection(conn)
    metadata = conn.execute("SELECT version,checksum,phase FROM notification_schema_migrations").fetchall()
    if len(metadata) != 1 or tuple(metadata[0]) != (VERSION, SCHEMA_SHA256, "FICTIONAL_ONLY"):
        raise SchemaError("version/checksum mismatch")
    counts = {"table": 0, "index": 0, "trigger": 0}
    expected_names = set()
    new_tables = []
    for statement in statements():
        kind, name, definition = _definition(statement)
        expected_names.add(name)
        if kind == "table":
            new_tables.append(name)
        row = conn.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if row is None or row[0] != kind or _canonical(row[1]) != _canonical(definition):
            raise SchemaError("schema shape mismatch: " + name)
        counts[kind] += 1
    placeholders = ",".join("?" for _ in new_tables)
    actual = conn.execute("SELECT type,name FROM sqlite_master WHERE sql IS NOT NULL AND "
        "(tbl_name IN (" + placeholders + ") OR (type='trigger' AND tbl_name IN ('users','user_preferences')))", new_tables).fetchall()
    if {row[1] for row in actual} != expected_names:
        raise SchemaError("schema shape has unexpected or missing objects")
    counts = {kind: sum(row[0] == kind for row in actual) for kind in counts}
    if conn.execute("PRAGMA foreign_key_check").fetchall() or conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SchemaError("integrity/FK check failed")
    return {"tables": counts["table"], "indexes": counts["index"], "triggers": counts["trigger"],
            "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "busy_timeout": conn.execute("PRAGMA busy_timeout").fetchone()[0],
            "sqlite_version": sqlite3.sqlite_version, "python_version": sys.version.split()[0],
            "compile_options": [r[0] for r in conn.execute("PRAGMA compile_options")],
            "schema_sha256": SCHEMA_SHA256, "source_sha256": hashlib.sha256(SOURCE_SQL.encode()).hexdigest()}
