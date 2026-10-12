"""HTTP boundary for reviewed, explicitly approved Zalo inquiries."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Database, now
from .settings import Settings
from .social_browser import social_login_at
from .zalo_browser import lookup_zalo_recipient, send_zalo_inquiry
from .zalo_contacts import DEFAULT_ZALO_TEMPLATE, validate_zalo_template
from .zalo_inquiry import (InquiryConflict, claim_inquiry_send, clear_recipient, create_inquiry, finish_inquiry_send,
                           get_inquiry, inquiry_context, record_recipient, regenerate_inquiry,
                           resolve_inquiry_attempt, update_inquiry)


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TemplateInput(Input):
    template: str


class CreateInput(Input):
    observation_id: str
    phone: str | None = None


class EditInput(Input):
    phone: str | None = None
    position: str | None = None
    message: str | None = None


class ApprovalInput(Input):
    package_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ResolveInput(Input):
    sent: bool


def _service(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except InquiryConflict as error:
        raise HTTPException(409, str(error)) from error
    except KeyError as error:
        raise HTTPException(404, str(error.args[0])) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


def create_zalo_router(db: Database, settings: Settings, browser_lock: asyncio.Lock) -> APIRouter:
    router = APIRouter()

    @router.get("/api/setup/zalo-template")
    def template():
        return {"template": db.get_setting("zalo_inquiry_template", DEFAULT_ZALO_TEMPLATE)}

    @router.put("/api/setup/zalo-template")
    def save_template(payload: TemplateInput):
        value = _service(validate_zalo_template, payload.template)
        db.set_setting("zalo_inquiry_template", value)
        return {"template": value}

    @router.get("/api/jobs/{vacancy_id}/zalo")
    def context(vacancy_id: str):
        result = _service(inquiry_context, db, vacancy_id)
        result["connected"] = bool(social_login_at(db, "zalo") and not db.get_setting("social_reauth_required_zalo"))
        return result

    @router.post("/api/jobs/{vacancy_id}/zalo")
    def create(vacancy_id: str, payload: CreateInput):
        return _service(create_inquiry, db, vacancy_id, payload.observation_id, payload.phone)

    @router.get("/api/zalo-inquiries/{inquiry_id}")
    def read(inquiry_id: str):
        return _service(get_inquiry, db, inquiry_id)

    @router.patch("/api/zalo-inquiries/{inquiry_id}")
    def edit(inquiry_id: str, payload: EditInput):
        return _service(update_inquiry, db, inquiry_id, payload.model_dump(exclude_unset=True))

    @router.post("/api/zalo-inquiries/{inquiry_id}/regenerate")
    def regenerate(inquiry_id: str):
        return _service(regenerate_inquiry, db, inquiry_id)

    @router.post("/api/zalo-inquiries/{inquiry_id}/lookup")
    async def lookup(inquiry_id: str):
        async with browser_lock:
            draft = _service(get_inquiry, db, inquiry_id)
            if not draft["editable"]:
                raise HTTPException(409, "This inquiry has an active or completed send attempt")
            result = await lookup_zalo_recipient(settings, draft["phone"])
            if result["status"] == "ready":
                draft = _service(record_recipient, db, inquiry_id, result["recipient"])
                db.set_setting("social_login_completed_at_zalo", now())
                db.set_setting("social_reauth_required_zalo", None)
            else:
                draft = _service(clear_recipient, db, inquiry_id)
                if result.get("code") == "auth_required":
                    db.set_setting("social_reauth_required_zalo", True)
            return {"lookup": result, "draft": draft}

    @router.post("/api/zalo-inquiries/{inquiry_id}/approve")
    async def approve(inquiry_id: str, payload: ApprovalInput):
        async with browser_lock:
            attempt = _service(claim_inquiry_send, db, inquiry_id, payload.package_hash)
            package = attempt["package_data"]
            try:
                result = await send_zalo_inquiry(settings, package["recipient"], package["message"])
            except asyncio.CancelledError:
                finish_inquiry_send(db, attempt["id"], {"status": "send_uncertain", "error": "Send was interrupted. Check the Zalo conversation."})
                raise
            except Exception as error:
                result = {"status": "send_uncertain", "error": str(error)[:600]}
            attempt = finish_inquiry_send(db, attempt["id"], result)
            return {"attempt": attempt, "draft": get_inquiry(db, inquiry_id)}

    @router.post("/api/zalo-inquiries/{inquiry_id}/attempts/{attempt_id}/resolve")
    def resolve(inquiry_id: str, attempt_id: str, payload: ResolveInput):
        _service(get_inquiry, db, inquiry_id)
        if not db.one("SELECT id FROM zalo_inquiry_attempts WHERE id=? AND draft_id=?", (attempt_id, inquiry_id)):
            raise HTTPException(404, "Attempt does not belong to this inquiry")
        attempt = _service(resolve_inquiry_attempt, db, attempt_id, payload.sent)
        return {"attempt": attempt, "draft": get_inquiry(db, inquiry_id)}

    return router
