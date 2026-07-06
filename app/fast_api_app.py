"""
CuroCadence AI — FastAPI Application
Custom web UI backend serving the medication concierge interface.
"""

import asyncio
import json
import uuid
import datetime
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

from app.agent import (
    runner,
    root_agent,
    security_checkpoint,
    get_medication_schedule,
    add_medication,
    check_interaction,
    log_dose_taken,
    export_schedule_ics,
    get_adherence_report,
    emergency_escalation,
    _audit,
    _store,
)
from app.config import config

# Google ADK imports
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

app = FastAPI(
    title="CuroCadence AI",
    description="Personal Medication Concierge Agent",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Request/Response Models
# ─────────────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    user_id: str = "default_user"
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    security_blocked: bool = False
    agent_name: str = "orchestrator"
    timestamp: str = ""


class MedRequest(BaseModel):
    user_id: str = "default_user"
    drug_name: str
    dose: str
    times: str


class DoseLogRequest(BaseModel):
    user_id: str = "default_user"
    drug_name: str
    timestamp: Optional[str] = None


class ApprovalRequest(BaseModel):
    approved: bool
    user_id: str = "default_user"
    drug_name: str
    dose: str
    times: str


class EmergencyRequest(BaseModel):
    user_id: str = "default_user"
    message: str = "Emergency escalation triggered by user"


# ─────────────────────────────────────────────────────────────────────────────
# In-memory pending approvals store
# ─────────────────────────────────────────────────────────────────────────────
_pending_approvals = _store.setdefault("pending_approvals", {})
_audit_log = _store.setdefault("audit_log", [])


def log_to_audit(level: str, event: str, detail: dict):
    """Append to in-memory audit log (also calls _audit for structured logging)."""
    _audit(level, event, detail)


# ─────────────────────────────────────────────────────────────────────────────
# Chat Endpoint (Main agent interaction)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Process a user message through the CuroCadence AI agent pipeline."""

    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    # 1. Security checkpoint
    sec = security_checkpoint(req.message, req.user_id)
    if not sec["safe"]:
        event_name = sec.get("event", "SECURITY_EVENT")
        log_to_audit("CRITICAL", event_name, {
            "user_id": req.user_id,
            "matched_pattern": sec.get("matched_pattern", "unknown"),
            "note": "Caught by security_checkpoint"
        })
        return ChatResponse(
            response=sec["message"],
            session_id=req.session_id or str(uuid.uuid4()),
            security_blocked=True,
            agent_name="security_checkpoint",
            timestamp=timestamp,
        )

    # Use scrubbed input
    safe_input = sec.get("scrubbed_input", req.message)

    # 2. Create or reuse session
    session_id = req.session_id or str(uuid.uuid4())

    try:
        session = await runner.session_service.get_session(
            app_name="curocadence_ai",
            user_id=req.user_id,
            session_id=session_id,
        )
    except Exception:
        session = None

    if session is None:
        session = await runner.session_service.create_session(
            app_name="curocadence_ai",
            user_id=req.user_id,
            session_id=session_id,
            state={
                "user_id": req.user_id,
                "medication_list": _store["schedules"].get(req.user_id, []),
            },
        )

    # 3. Run the agent
    user_content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=safe_input)],
    )

    response_text = ""
    agent_name = "orchestrator"

    try:
        async for event in runner.run_async(
            user_id=req.user_id,
            session_id=session_id,
            new_message=user_content,
        ):
            if hasattr(event, "content") and event.content:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        response_text += part.text
            if hasattr(event, "author") and event.author:
                agent_name = event.author

        if not response_text:
            response_text = "I processed your request. How can I help further with your medication schedule?"

    except Exception as e:
        log_to_audit("CRITICAL", "agent_error", {"error": str(e), "user_id": req.user_id})
        response_text = f"I encountered an error processing your request: {str(e)}"

    is_injection = "Security alert" in response_text
    is_interaction = "interaction" in response_text.lower() and ("high" in response_text.lower() or "medium" in response_text.lower() or "severity" in response_text.lower() or "conflict" in response_text.lower() or "risk" in response_text.lower())

    if is_injection:
        log_to_audit("CRITICAL", "injection_detected", {
            "user_id": req.user_id,
            "session_id": session_id,
            "note": "Caught by agent during chat"
        })
    elif is_interaction:
        log_to_audit("WARNING", "interaction_flagged", {
            "user_id": req.user_id,
            "session_id": session_id,
            "note": "Interaction flagged during chat"
        })
    else:
        log_to_audit("INFO", "chat_processed", {
            "user_id": req.user_id,
            "session_id": session_id,
            "had_pii": sec.get("original_had_pii", False),
        })

    return ChatResponse(
        response=response_text,
        session_id=session_id,
        security_blocked=False,
        agent_name=agent_name,
        timestamp=timestamp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Direct MCP Tool Endpoints (for UI panels)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/schedule/{user_id}")
async def get_schedule(user_id: str = "default_user"):
    """Get medication schedule for the UI schedule panel."""
    result = get_medication_schedule(user_id)
    return JSONResponse(json.loads(result))


@app.post("/medication/add")
async def add_med(req: MedRequest):
    """Add a medication — checks interactions first."""
    user_id = req.user_id
    drug_name = req.drug_name

    # Check interactions with all existing meds
    existing = _store["schedules"].get(user_id, [])
    interactions = []
    max_severity = "LOW"

    for med in existing:
        result = json.loads(check_interaction(drug_name, med["drug_name"], create_approval=False))
        if result["severity"] != "LOW":
            interactions.append(result)
            if result["severity"] == "HIGH":
                max_severity = "HIGH"
            elif result["severity"] == "MEDIUM" and max_severity != "HIGH":
                max_severity = "MEDIUM"

    if max_severity == "HIGH":
        # Create pending approval
        approval_id = str(uuid.uuid4())
        _pending_approvals[approval_id] = {
            "user_id": user_id,
            "drug_name": drug_name,
            "dose": req.dose,
            "times": req.times,
            "interactions": interactions,
            "severity": "HIGH",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        log_to_audit("WARNING", "HIGH_SEVERITY_PENDING_APPROVAL", {
            "user_id": user_id,
            "drug": drug_name,
            "approval_id": approval_id,
        })
        return JSONResponse({
            "status": "pending_approval",
            "severity": "HIGH",
            "approval_id": approval_id,
            "interactions": interactions,
            "message": f"⛔ HIGH severity interaction detected. Human approval required before adding {drug_name}.",
        })

    elif max_severity == "MEDIUM":
        approval_id = str(uuid.uuid4())
        _pending_approvals[approval_id] = {
            "user_id": user_id,
            "drug_name": drug_name,
            "dose": req.dose,
            "times": req.times,
            "interactions": interactions,
            "severity": "MEDIUM",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }
        log_to_audit("WARNING", "MEDIUM_SEVERITY_PENDING_APPROVAL", {
            "user_id": user_id,
            "drug": drug_name,
            "approval_id": approval_id,
        })
        return JSONResponse({
            "status": "pending_approval",
            "severity": "MEDIUM",
            "approval_id": approval_id,
            "interactions": interactions,
            "message": f"⚠️ MEDIUM severity interaction detected. Caregiver will be notified. Approve to proceed.",
        })

    # No significant interaction — add directly
    result = json.loads(add_medication(user_id, drug_name, req.dose, req.times))
    log_to_audit("INFO", "medication_auto_approved", {"user_id": user_id, "drug": drug_name})
    return JSONResponse({**result, "interactions": interactions, "severity": "LOW"})


@app.post("/medication/approve")
async def approve_medication(req: ApprovalRequest):
    """Handle human-in-the-loop approval for MEDIUM/HIGH interactions."""
    # Find pending approval
    pending = None
    approval_id = None
    for aid, p in _pending_approvals.items():
        if p["user_id"] == req.user_id and p["drug_name"] == req.drug_name:
            pending = p
            approval_id = aid
            break

    if not pending:
        # Try to add directly with provided info
        pass

    if req.approved:
        result = json.loads(add_medication(req.user_id, req.drug_name, req.dose, req.times))
        if approval_id:
            del _pending_approvals[approval_id]
        log_to_audit("INFO", "human_approved_medication", {
            "user_id": req.user_id,
            "drug": req.drug_name,
            "severity": pending.get("severity", "UNKNOWN") if pending else "UNKNOWN",
        })
        return JSONResponse({**result, "approved": True, "human_reviewed": True})
    else:
        if approval_id:
            del _pending_approvals[approval_id]
        log_to_audit("WARNING", "human_rejected_medication", {
            "user_id": req.user_id,
            "drug": req.drug_name,
        })
        return JSONResponse({
            "status": "rejected",
            "message": f"Addition of {req.drug_name} was cancelled by user.",
            "approved": False,
        })


@app.post("/dose/log")
async def log_dose(req: DoseLogRequest):
    """Log a dose taken."""
    timestamp = req.timestamp or datetime.datetime.utcnow().isoformat() + "Z"
    result = json.loads(log_dose_taken(req.user_id, req.drug_name, timestamp))
    return JSONResponse(result)


@app.get("/schedule/{user_id}/export-ics")
async def export_ics(user_id: str):
    """Export schedule as ICS."""
    result = json.loads(export_schedule_ics(user_id))
    return JSONResponse(result)


@app.get("/report/{user_id}")
async def adherence_report(user_id: str, date_range: str = "last 7 days"):
    """Get adherence report."""
    result = json.loads(get_adherence_report(user_id, date_range))
    return JSONResponse(result)


@app.post("/emergency")
async def emergency(req: EmergencyRequest):
    """Trigger emergency escalation."""
    result = json.loads(emergency_escalation(req.user_id, req.message))
    log_to_audit("CRITICAL", "EMERGENCY_ESCALATION_UI", {
        "user_id": req.user_id,
        "message": req.message[:100],
    })
    return JSONResponse(result)


@app.get("/audit-log")
async def get_audit_log():
    """Return the in-memory audit log."""
    return JSONResponse({"entries": _audit_log[-50:]})  # Last 50 entries


@app.get("/pending-approvals/{user_id}")
async def get_pending_approvals(user_id: str):
    """Get pending approval requests for a user."""
    user_pending = {k: v for k, v in _pending_approvals.items() if v["user_id"] == user_id}
    return JSONResponse({"pending": list(user_pending.values()), "count": len(user_pending)})


import os
from fastapi.responses import FileResponse

# ─────────────────────────────────────────────────────────────────────────────
# Custom Web UI (served at root)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=FileResponse)
async def serve_ui():
    """Serve the CuroCadence AI custom web UI."""
    # Serve the static HTML file from the app/static directory
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    index_path = os.path.join(static_dir, "index.html")
    return FileResponse(index_path)


@app.get("/health")
async def health():
    return {"status": "ok", "model": config.model, "version": "1.0.0"}
