"""Reviewed Zalo inquiries have their own lifecycle, separate from applications."""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .db import Database, new_id, now
from .zalo_contacts import (DEFAULT_ZALO_TEMPLATE, contact_candidates, facebook_observations,
                            normalize_zalo_phone, render_zalo_message)

BLOCKING = ("sending", "sent_confirmed", "send_uncertain")


class InquiryConflict(ValueError):
    pass


def _decode(row: dict) -> dict:
    row = dict(row)
    for key in ("recipient", "receipt", "package_data"):
        if key in row:
            row[key] = json.loads(row[key] or "{}")
    if "group_name" in row:
        row["group"] = row.pop("group_name")
    return row


def _blocking(db: Database, draft: dict, *, own: bool = False) -> dict | None:
    condition = "draft_id=?" if own else "vacancy_id=? AND phone=?"
    params = (draft["id"],) if own else (draft["vacancy_id"], draft["phone"])
    return db.one(f"SELECT * FROM zalo_inquiry_attempts WHERE {condition} "
                  "AND status IN ('sending','sent_confirmed','send_uncertain') LIMIT 1", params)


def _blockers(draft: dict, active: dict | None) -> list[str]:
    reasons = []
    if not draft["position"].strip():
        reasons.append("Enter the position as it should appear in your message")
    if not draft["message"].strip():
        reasons.append("Enter the message to send")
    recipient = draft.get("recipient") or {}
    if recipient.get("phone") != draft["phone"] or not recipient.get("identity_key") or not recipient.get("display_name"):
        reasons.append("Look up and review the Zalo recipient before sending")
    if active:
        reasons.append({"sending": "A Zalo send is already in progress", "sent_confirmed": "This Zalo contact has already been messaged",
                        "send_uncertain": "Check the Zalo chat before resolving the uncertain send"}[active["status"]])
    return reasons


def inquiry_hash(inquiry: dict) -> str:
    recipient = inquiry.get("recipient") or {}
    payload = {key: inquiry.get(key) for key in ("observation_id", "phone", "position", "message")}
    payload["recipient"] = {key: recipient.get(key) for key in ("identity_key", "display_name")}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def get_inquiry(db: Database, inquiry_id: str) -> dict:
    row = db.one("SELECT * FROM zalo_inquiry_drafts WHERE id=?", (inquiry_id,))
    if not row:
        raise KeyError("Zalo inquiry not found")
    draft = _decode(row)
    latest = db.one("SELECT * FROM zalo_inquiry_attempts WHERE draft_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (inquiry_id,))
    draft["latest_attempt"] = _decode(latest) if latest else None
    draft["blockers"] = _blockers(draft, _blocking(db, draft))
    draft["send_ready"] = not draft["blockers"]
    draft["package_hash"] = inquiry_hash(draft)
    draft["editable"] = not _blocking(db, draft, own=True)
    return draft


def create_inquiry(db: Database, vacancy_id: str, observation_id: str, phone: str | None = None) -> dict:
    observation = next((row for row in facebook_observations(db, vacancy_id) if row["observation_id"] == observation_id), None)
    if observation is None:
        raise KeyError("Facebook post does not belong to this job")
    candidates = [row for row in contact_candidates(db, vacancy_id) if row["observation_id"] == observation_id]
    if phone is None:
        if len(candidates) != 1:
            raise ValueError("Choose or enter the Zalo phone number from this post")
        phone = candidates[0]["phone"]
    phone = normalize_zalo_phone(phone)
    evidence = next((row["evidence"] for row in candidates if row["phone"] == phone), observation["raw_text"][:3000])
    template = db.get_setting("zalo_inquiry_template", DEFAULT_ZALO_TEMPLATE)
    message = render_zalo_message(template, observation["position"], observation["group"])
    identifier, timestamp = new_id(), now()
    db.execute("INSERT INTO zalo_inquiry_drafts(id,vacancy_id,observation_id,phone,position,group_name,post_url,evidence,template,message,created_at,updated_at) "
               "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, vacancy_id, observation_id, phone, observation["position"], observation["group"],
                                                  observation["post_url"], evidence, template, message, timestamp, timestamp))
    return get_inquiry(db, identifier)


def _editable(db: Database, draft: dict) -> None:
    if _blocking(db, draft, own=True):
        raise InquiryConflict("This inquiry has an active or completed send attempt")


def update_inquiry(db: Database, inquiry_id: str, updates: dict) -> dict:
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = get_inquiry(db, inquiry_id)
        _editable(db, draft)
        if set(updates) - {"phone", "position", "message"}:
            raise ValueError("Edit only the phone, position, or message")
        merged = {**draft, **updates}
        merged["phone"] = normalize_zalo_phone(merged["phone"])
        for key in ("position", "message"):
            merged[key] = str(merged[key] or "").strip()
            if not merged[key]:
                raise ValueError(f"Enter the {key} before saving")
        if merged["phone"] != draft["phone"]:
            merged["recipient"] = {}
        conn.execute("UPDATE zalo_inquiry_drafts SET phone=?,position=?,message=?,recipient=?,updated_at=? WHERE id=?",
                     (merged["phone"], merged["position"], merged["message"], json.dumps(merged["recipient"], ensure_ascii=False), now(), inquiry_id))
    return get_inquiry(db, inquiry_id)


def regenerate_inquiry(db: Database, inquiry_id: str) -> dict:
    draft = get_inquiry(db, inquiry_id)
    return update_inquiry(db, inquiry_id, {"message": render_zalo_message(draft["template"], draft["position"], draft["group"])})


def record_recipient(db: Database, inquiry_id: str, recipient: dict) -> dict:
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        draft = get_inquiry(db, inquiry_id)
        _editable(db, draft)
        if (normalize_zalo_phone(recipient.get("phone", "")) != draft["phone"]
                or not recipient.get("identity_key") or not recipient.get("display_name")):
            raise ValueError("The Zalo lookup does not match this phone number")
        recipient = {**recipient, "phone": draft["phone"]}
        conn.execute("UPDATE zalo_inquiry_drafts SET recipient=?,updated_at=? WHERE id=?",
                     (json.dumps(recipient, ensure_ascii=False), now(), inquiry_id))
    return get_inquiry(db, inquiry_id)


def claim_inquiry_send(db: Database, inquiry_id: str, expected_hash: str) -> dict:
    try:
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            draft = get_inquiry(db, inquiry_id)
            if not expected_hash or expected_hash != draft["package_hash"]:
                raise InquiryConflict("The inquiry changed. Review the current recipient and message before sending")
            if _blocking(db, draft):
                raise InquiryConflict("This contact already has a send attempt. Check its outcome before sending again")
            if draft["blockers"]:
                raise ValueError("; ".join(draft["blockers"]))
            identifier, timestamp = new_id(), now()
            package = {key: draft[key] for key in ("observation_id", "phone", "position", "message", "recipient")}
            conn.execute("INSERT INTO zalo_inquiry_attempts(id,draft_id,vacancy_id,phone,package_hash,package_data,status,created_at,updated_at) "
                         "VALUES(?,?,?,?,?,?,'sending',?,?)", (identifier, inquiry_id, draft["vacancy_id"], draft["phone"], expected_hash,
                                                              json.dumps(package, ensure_ascii=False), timestamp, timestamp))
    except sqlite3.IntegrityError as error:
        raise InquiryConflict("A send attempt already exists for this contact") from error
    return _decode(db.one("SELECT * FROM zalo_inquiry_attempts WHERE id=?", (identifier,)))


def finish_inquiry_send(db: Database, attempt_id: str, result: dict) -> dict:
    status = result.get("status", "send_uncertain")
    if status not in {"sent_confirmed", "send_uncertain", "needs_attention", "failed"}:
        status = "send_uncertain"
    db.execute("UPDATE zalo_inquiry_attempts SET status=?,receipt=?,error=?,updated_at=? WHERE id=? AND status='sending'",
               (status, json.dumps(result.get("receipt") or {}, ensure_ascii=False), result.get("error") or result.get("detail"), now(), attempt_id))
    row = db.one("SELECT * FROM zalo_inquiry_attempts WHERE id=?", (attempt_id,))
    if row is None:
        raise KeyError("Zalo attempt not found")
    return _decode(row)


def resolve_inquiry_attempt(db: Database, attempt_id: str, sent: bool) -> dict:
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        attempt = conn.execute("SELECT * FROM zalo_inquiry_attempts WHERE id=?", (attempt_id,)).fetchone()
        if not attempt:
            raise KeyError("Zalo attempt not found")
        if attempt["status"] != "send_uncertain":
            raise ValueError("Only an uncertain send can be resolved after checking Zalo")
        receipt = {"source": "user_confirmation", "sent": sent, "resolved_at": now()}
        conn.execute("UPDATE zalo_inquiry_attempts SET status=?,receipt=?,updated_at=? WHERE id=?",
                     ("sent_confirmed" if sent else "confirmed_not_sent", json.dumps(receipt), now(), attempt_id))
        if not sent:
            conn.execute("UPDATE zalo_inquiry_drafts SET recipient='{}',updated_at=? WHERE id=?", (now(), attempt["draft_id"]))
    return _decode(db.one("SELECT * FROM zalo_inquiry_attempts WHERE id=?", (attempt_id,)))


def recover_inquiry_sends(db: Database) -> int:
    with db.connection() as conn:
        return conn.execute("UPDATE zalo_inquiry_attempts SET status='send_uncertain',error=?,updated_at=? WHERE status='sending'",
                            ("Job Radar restarted before send confirmation. Check the Zalo chat.", now())).rowcount


def inquiry_context(db: Database, vacancy_id: str) -> dict:
    observations = facebook_observations(db, vacancy_id)
    drafts = [get_inquiry(db, row["id"]) for row in db.all("SELECT id FROM zalo_inquiry_drafts WHERE vacancy_id=? ORDER BY created_at DESC,rowid DESC", (vacancy_id,))]
    attempts = [_decode(row) for row in db.all("SELECT * FROM zalo_inquiry_attempts WHERE vacancy_id=? ORDER BY created_at DESC,rowid DESC", (vacancy_id,))]
    status = "awaiting_reply" if any(row["status"] == "sent_confirmed" for row in attempts) else "attention" if any(row["status"] in ("sending", "send_uncertain") for row in attempts) else "not_contacted"
    return {"observations": observations, "candidates": contact_candidates(db, vacancy_id), "drafts": drafts, "attempts": attempts, "contact_status": status}
