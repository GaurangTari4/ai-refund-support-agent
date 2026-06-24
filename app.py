from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
import threading
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT / "public"
DATA_DIR = ROOT / "data"
PORT = int(os.getenv("PORT", "3000"))
USE_LLM = os.getenv("USE_LLM", "true").strip().lower() not in {"0", "false", "no", "off"}
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_VOICE = os.getenv("OPENAI_VOICE", "marin")
OPENAI_AUDIO_TIMEOUT_SECONDS = 60.0
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "phi3:mini")
LOCAL_LLM_BASE_URL = os.getenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
LOCAL_LLM_HEALTH_URL = os.getenv("LOCAL_LLM_HEALTH_URL", "http://127.0.0.1:11434/api/tags")

LOCAL_MODEL_CHOICES = [
    {"id": "phi3:mini", "label": "Phi-3 Mini", "description": "Lightest option; fast on modest hardware."},
    {"id": "mistral", "label": "Mistral 7B Instruct", "description": "A stronger small-model baseline."},
    {"id": "gemma2:2b", "label": "Gemma 2 2B", "description": "Tiny and quick for offline demos."},
]

try:
    from crewai import Agent as CrewAgent
    from crewai import Crew, LLM, Process, Task
    from crewai.tools import tool

    CREWAI_AVAILABLE = True
except Exception:
    CrewAgent = None
    Crew = None
    LLM = None
    Process = None
    Task = None
    tool = None
    CREWAI_AVAILABLE = False


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


customers: List[Dict[str, Any]] = load_json(DATA_DIR / "customers.json")
policy_text = (DATA_DIR / "refund-policy.md").read_text(encoding="utf-8")


def current_policy_date() -> str:
    return time.strftime("%Y-%m-%d")


def is_openai_voice_enabled() -> bool:
    return bool(OPENAI_API_KEY)


def safe_json(handler: "RefundRequestHandler", status: int, body: Any) -> None:
    payload = json.dumps(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def hash_for_case(input_text: str) -> str:
    return hashlib.sha1(input_text.encode("utf-8")).hexdigest()[:10].upper()


def normalize(text: str = "") -> str:
    return text.strip().lower()


def days_between(date_a: str, date_b: str) -> int:
    a = time.strptime(date_a, "%Y-%m-%d")
    b = time.strptime(date_b, "%Y-%m-%d")
    return int(
        (
            time.mktime((b.tm_year, b.tm_mon, b.tm_mday, 0, 0, 0, 0, 0, -1))
            - time.mktime((a.tm_year, a.tm_mon, a.tm_mday, 0, 0, 0, 0, 0, -1))
        )
        / 86400
    )


def mime_type_to_filename(mime_type: str) -> str:
    normalized = (mime_type or "audio/webm").split(";", 1)[0].strip().lower()
    extension = mimetypes.guess_extension(normalized) or ".webm"
    return f"voice{extension}"


def build_multipart_form_data(
    fields: Dict[str, str],
    file_field_name: str,
    file_name: str,
    file_bytes: bytes,
    mime_type: str,
) -> tuple[bytes, str]:
    boundary = f"----BajiMart{hashlib.sha1(f'{time.time()}:{file_name}'.encode('utf-8')).hexdigest()}"
    normalized_mime_type = (mime_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    parts: List[bytes] = []

    for field_name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data; name=\"{field_name}\"\r\n\r\n"
                f"{value}\r\n"
            ).encode("utf-8")
        )

    parts.append(
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{file_field_name}\"; filename=\"{file_name}\"\r\n"
            f"Content-Type: {normalized_mime_type}\r\n\r\n"
        ).encode("utf-8")
        + file_bytes
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def decode_voice_payload(audio_base64: str) -> bytes:
    try:
        return base64.b64decode(audio_base64, validate=True)
    except Exception as error:
        raise ValueError("The provided audio payload is not valid base64.") from error


def openai_error_message(error: Exception, fallback: str) -> str:
    if isinstance(error, HTTPError):
        try:
            payload = json.loads(error.read().decode("utf-8"))
        except Exception:
            return f"{fallback} (HTTP {error.code})."

        if isinstance(payload, dict):
            details = payload.get("error")
            if isinstance(details, dict):
                message = details.get("message") or details.get("type")
                if message:
                    return str(message)
            message = payload.get("message")
            if message:
                return str(message)
        return f"{fallback} (HTTP {error.code})."

    if isinstance(error, URLError):
        reason = getattr(error, "reason", None)
        return f"{fallback}: {reason or error}"

    return f"{fallback}: {error}"


def transcribe_openai_voice(audio_bytes: bytes, mime_type: str) -> str:
    request_body, content_type = build_multipart_form_data(
        {"model": OPENAI_TRANSCRIBE_MODEL},
        "file",
        mime_type_to_filename(mime_type),
        audio_bytes,
        mime_type,
    )
    request = Request(
        "https://api.openai.com/v1/audio/transcriptions",
        data=request_body,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": content_type,
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=OPENAI_AUDIO_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise RuntimeError(openai_error_message(error, "OpenAI transcription failed")) from error

    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        raise RuntimeError("OpenAI transcription returned an empty transcript.")
    return transcript


def synthesize_openai_voice(text: str) -> Dict[str, Any]:
    request = Request(
        "https://api.openai.com/v1/audio/speech",
        data=json.dumps(
            {
                "model": OPENAI_TTS_MODEL,
                "input": text,
                "voice": OPENAI_VOICE,
                "response_format": "mp3",
                "instructions": "Speak clearly, warmly, and concisely like a helpful customer support agent.",
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
    )

    try:
        with urlopen(request, timeout=OPENAI_AUDIO_TIMEOUT_SECONDS) as response:
            audio_bytes = response.read()
            mime_type = response.headers.get_content_type() or "audio/mpeg"
    except Exception as error:
        raise RuntimeError(openai_error_message(error, "OpenAI speech synthesis failed")) from error

    return {"audioBytes": audio_bytes, "audioMimeType": mime_type}


@dataclass
class Session:
    id: str
    created_at: str
    selected_customer_id: Optional[str] = None
    selected_order_id: Optional[str] = None
    selected_model: Optional[str] = None
    logs: List[Dict[str, Any]] = field(default_factory=list)
    messages: List[Dict[str, Any]] = field(default_factory=list)


sessions: Dict[str, Session] = {}
sse_clients: Dict[str, List[Any]] = {}
refund_cases: List[Dict[str, Any]] = []
lock = threading.Lock()


def get_session(session_id: Optional[str] = None) -> Session:
    session_id = session_id or hashlib.sha1(str(time.time()).encode()).hexdigest()[:12]
    with lock:
        if session_id not in sessions:
            sessions[session_id] = Session(id=session_id, created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return sessions[session_id]


def is_local_llm_available() -> bool:
    try:
        with urlopen(LOCAL_LLM_HEALTH_URL, timeout=1.5) as response:
            return int(getattr(response, "status", 0)) == 200
    except Exception:
        return False


def normalize_model_name(model_name: Optional[str]) -> str:
    value = (model_name or "").strip()
    return value or LOCAL_LLM_MODEL


def resolve_agent_backend(selected_model: Optional[str] = None) -> Dict[str, Any]:
    model_name = normalize_model_name(selected_model)
    local_available = is_local_llm_available()
    if USE_LLM and CREWAI_AVAILABLE and LLM_PROVIDER in {"ollama", "local", "auto"} and local_available:
        return {
            "mode": "crewai-local",
            "provider": "ollama",
            "available": True,
            "model": model_name,
            "baseUrl": LOCAL_LLM_BASE_URL,
            "label": "CrewAI + Ollama LLM",
        }
    if USE_LLM and CREWAI_AVAILABLE and LLM_PROVIDER in {"openai", "auto"} and OPENAI_API_KEY:
        return {
            "mode": "crewai-openai",
            "provider": "openai",
            "available": True,
            "model": OPENAI_MODEL,
            "baseUrl": "https://api.openai.com/v1",
            "label": "CrewAI + OpenAI LLM",
        }
    return {
        "mode": "deterministic-fallback",
        "provider": "none",
        "available": False,
        "model": model_name,
        "baseUrl": None,
        "label": "Deterministic fallback",
    }


def emit_log(session: Session, type_: str, title: str, detail: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    entry = {
        "id": hashlib.sha1(f"{session.id}:{time.time()}:{title}".encode()).hexdigest()[:16].upper(),
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "type": type_,
        "title": title,
        "detail": detail,
        "payload": payload or {},
    }
    session.logs.append(entry)
    for client in sse_clients.get(session.id, []):
        try:
            client.write(f"event: reasoning\ndata: {json.dumps(entry)}\n\n".encode("utf-8"))
            client.flush()
        except Exception:
            pass
    return entry


def extract_entities(message: str, session: Session) -> Dict[str, Any]:
    email = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", message, re.I)
    order_id = re.search(r"\bORD-\d{4}\b", message, re.I)
    amount_match = re.search(r"\$(\d+(?:\.\d{1,2})?)\b|\b(?:refund|amount)\s+(?:of\s+)?\$?(\d+(?:\.\d{1,2})?)\b", message, re.I)
    text = normalize(message)

    reason_hints: List[str] = []
    if re.search(r"(damaged|broken|defective|does not work|not working)", text):
        reason_hints.append("damaged")
    if re.search(r"(wrong|incorrect|different item|not what i ordered)", text):
        reason_hints.append("wrong_item")
    if re.search(r"(missing|never arrived|not received)", text):
        reason_hints.append("missing")
    if re.search(r"(duplicate|charged twice|double charged|billing error)", text):
        reason_hints.append("duplicate_charge")
    if re.search(r"(late|delay|still waiting|in transit)", text):
        reason_hints.append("delivery_delay")
    if re.search(r"(changed my mind|unwanted|return it|do not want)", text):
        reason_hints.append("unwanted")

    reason_priority = ["damaged", "wrong_item", "missing", "duplicate_charge", "delivery_delay", "unwanted"]
    reason = next((item for item in reason_priority if item in reason_hints), "unspecified")
    customer_statement_reason = "unspecified"
    if "unwanted" in reason_hints and reason != "unwanted":
        customer_statement_reason = "unwanted"
    elif reason != "unspecified":
        customer_statement_reason = reason

    has_refund_intent = bool(re.search(r"\b(refund|money back|chargeback)\b", text))
    has_conflict = "unwanted" in reason_hints and any(item in reason_hints for item in ("damaged", "wrong_item", "missing", "duplicate_charge"))

    return {
        "email": email.group(0).lower() if email else None,
        "orderId": order_id.group(0).upper() if order_id else None,
        "requestedAmount": float(amount_match.group(1) or amount_match.group(2)) if amount_match else None,
        "reason": reason,
        "policyReason": reason,
        "customerStatementReason": customer_statement_reason,
        "reasonHints": reason_hints,
        "hasReason": reason != "unspecified",
        "hasRefundIntent": has_refund_intent,
        "hasReasonConflict": has_conflict,
    }


def all_orders() -> List[Dict[str, Any]]:
    flattened = []
    for customer in customers:
        for order in customer["orders"]:
            flattened.append({**order, "customerId": customer["id"]})
    return flattened


def lookup_customer(email: Optional[str], order_id: Optional[str], selected_customer_id: Optional[str]) -> Optional[Dict[str, Any]]:
    owner_id = None
    if order_id:
        matching_order = next((order for order in all_orders() if order["id"] == order_id), None)
        owner_id = matching_order["customerId"] if matching_order else None
    return (
        next((item for item in customers if item["email"] == email), None)
        or next((item for item in customers if item["id"] == owner_id), None)
        or next((item for item in customers if item["id"] == selected_customer_id), None)
    )


def lookup_order(order_id: Optional[str], customer: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not order_id:
        if customer and len(customer.get("orders", [])) == 1:
            return {**customer["orders"][0], "customerId": customer["id"]}
        return None
    if customer:
        scoped = next((order for order in customer["orders"] if order["id"] == order_id), None)
        if scoped:
            return {**scoped, "customerId": customer["id"]}
    return next((order for order in all_orders() if order["id"] == order_id), None)


def verify_identity(customer: Optional[Dict[str, Any]], order: Optional[Dict[str, Any]], email: Optional[str]) -> Dict[str, Any]:
    if not customer and not order:
        return {"ok": False, "reason": "No CRM customer or order matched the request."}
    if customer and order and order["customerId"] != customer["id"]:
        return {"ok": False, "reason": "The order does not belong to the provided customer email."}
    if email and customer and customer["email"] != email:
        return {"ok": False, "reason": "The provided email does not match the CRM customer record."}
    return {"ok": True, "reason": "Customer identity and order ownership matched the CRM."}


def inspect_policy_rules(customer: Optional[Dict[str, Any]], order: Optional[Dict[str, Any]], entities: Dict[str, Any]) -> Dict[str, Any]:
    failures: List[str] = []
    review: List[str] = []
    approvals: List[str] = []
    checks: List[Dict[str, Any]] = []
    reason = entities["reason"]
    amount_available = round((order["total"] - order.get("previousRefunds", 0)) if order else 0, 2)
    today = current_policy_date()

    if not customer or not order:
        detail = "A valid customer and order are required."
        failures.append(detail)
        checks.append({"rule": "identity", "status": "fail", "detail": detail})
        return {"failures": failures, "review": review, "approvals": approvals, "checks": checks, "amountAvailable": amount_available}

    if order["paymentStatus"] not in {"captured", "settled"}:
        detail = f"Payment status is {order['paymentStatus']}; only captured or settled payments are refundable."
        failures.append(detail)
        checks.append({"rule": "payment_status", "status": "fail", "detail": detail})
    else:
        detail = "Payment is captured or settled."
        approvals.append(detail)
        checks.append({"rule": "payment_status", "status": "pass", "detail": detail})

    if order.get("chargebackOpen"):
        detail = "An open chargeback blocks automated refunds."
        failures.append(detail)
        checks.append({"rule": "chargeback", "status": "fail", "detail": detail})

    if amount_available <= 0:
        detail = "No refundable balance remains after prior refunds."
        failures.append(detail)
        checks.append({"rule": "refundable_balance", "status": "fail", "detail": detail})

    if customer["refundsLast180Days"] > 2:
        detail = "More than two refunds in the last 180 days requires manual review."
        review.append(detail)
        checks.append({"rule": "refund_velocity", "status": "review", "detail": detail})

    if customer["fraudScore"] >= 70:
        detail = "Fraud score is 70 or higher and requires manual review."
        review.append(detail)
        checks.append({"rule": "fraud_score", "status": "review", "detail": detail})

    if order["status"] in {"in_transit", "processing"}:
        detail = "The order is not marked delivered, so it is not eligible for automated refund."
        failures.append(detail)
        checks.append({"rule": "delivery_status", "status": "fail", "detail": detail})

    if order["status"] == "cancelled":
        if order.get("cancellationReason") in {"stockout", "merchant_cancelled", "duplicate_charge"}:
            detail = "Merchant-side cancellation is refundable."
            approvals.append(detail)
            checks.append({"rule": "cancellation_reason", "status": "pass", "detail": detail})
        else:
            detail = "Cancelled orders are refundable only for merchant-side cancellation, stockout, or duplicate charge."
            failures.append(detail)
            checks.append({"rule": "cancellation_reason", "status": "fail", "detail": detail})

    if order.get("deliveredAt"):
        days_since_delivery = days_between(order["deliveredAt"], today)
        incident_reasons = {"damaged", "wrong_item", "missing"}
        window_days = 14 if reason in incident_reasons else 90
        if days_since_delivery > window_days:
            detail = f"The request is {days_since_delivery} days after delivery, beyond the {window_days}-day policy window."
            failures.append(detail)
            checks.append({"rule": "delivery_window", "status": "fail", "detail": detail, "windowDays": window_days, "daysSinceDelivery": days_since_delivery})
        else:
            detail = f"Request is within the {window_days}-day policy window."
            approvals.append(detail)
            checks.append({"rule": "delivery_window", "status": "pass", "detail": detail, "windowDays": window_days, "daysSinceDelivery": days_since_delivery})

    if order["category"] == "final_sale":
        detail = "Final-sale items are never eligible for automated refunds."
        failures.append(detail)
        checks.append({"rule": "category_final_sale", "status": "fail", "detail": detail})
    if order["category"] == "gift_card":
        detail = "Gift cards are never eligible for cash refunds."
        failures.append(detail)
        checks.append({"rule": "category_gift_card", "status": "fail", "detail": detail})
    if order["category"] == "opened_hygiene" and reason != "damaged":
        detail = "Opened hygiene items are refundable only when verified damaged on arrival."
        failures.append(detail)
        checks.append({"rule": "category_opened_hygiene", "status": "fail", "detail": detail})
    if order["category"] == "perishable" and reason != "damaged":
        detail = "Perishables are refundable only when verified damaged on arrival."
        failures.append(detail)
        checks.append({"rule": "category_perishable", "status": "fail", "detail": detail})
    if order["category"] == "digital_download":
        days_since_purchase = days_between(order["date"], today)
        if days_since_purchase >= 0 or order.get("digitalUsagePercent", 0) > 0:
            detail = "Digital downloads are not returnable once delivered or used."
            failures.append(detail)
            checks.append({"rule": "category_digital_download", "status": "fail", "detail": detail, "daysSincePurchase": days_since_purchase, "usage": order.get("digitalUsagePercent", 0)})
    if order["category"] == "subscription" and reason != "duplicate_charge":
        detail = "Subscriptions are refundable only for duplicate charges or merchant billing errors."
        failures.append(detail)
        checks.append({"rule": "category_subscription", "status": "fail", "detail": detail})
    if order["category"] == "bundle" and not order.get("bundleItemized") and reason != "damaged":
        detail = "Bundles without item-level prices must be refunded as a full bundle only."
        failures.append(detail)
        checks.append({"rule": "category_bundle", "status": "fail", "detail": detail})

    return {"failures": failures, "review": review, "approvals": approvals, "checks": checks, "amountAvailable": amount_available}


def calculate_refund_eligibility(identity: Dict[str, Any], policy: Dict[str, Any], entities: Dict[str, Any], order: Dict[str, Any]) -> Dict[str, Any]:
    if not identity["ok"]:
        return {"outcome": "DENIED", "reason": identity["reason"], "refundAmount": 0}
    if policy["failures"]:
        return {"outcome": "DENIED", "reason": policy["failures"][0], "refundAmount": 0}
    if policy["review"]:
        return {"outcome": "MANUAL_REVIEW", "reason": policy["review"][0], "refundAmount": 0}
    requested = entities.get("requestedAmount") or order["total"]
    refund_amount = min(float(requested), float(policy["amountAvailable"]))
    return {"outcome": "APPROVED", "reason": "All required strict refund policy checks passed.", "refundAmount": round(refund_amount, 2)}


def finalize_refund_case(session: Session, customer: Dict[str, Any], order: Dict[str, Any], eligibility: Dict[str, Any], entities: Dict[str, Any]) -> Dict[str, Any]:
    case_source = f"{session.id}:{order.get('id', 'unknown')}:{time.time()}"
    case_id = f"RF-{hash_for_case(case_source)}"
    refund_case = {
        "caseId": case_id,
        "sessionId": session.id,
        "customerId": customer["id"] if customer else None,
        "orderId": order["id"] if order else None,
        "reason": entities["reason"],
        "outcome": eligibility["outcome"],
        "refundAmount": eligibility["refundAmount"],
        "policyReason": eligibility["reason"],
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    refund_cases.insert(0, refund_case)
    return {"refundCase": refund_case}


def plan_tool_sequence(state: Dict[str, Any]) -> List[str]:
    sequence = ["extract_entities"]
    if state["needsCustomer"]:
        sequence.append("lookup_customer")
    sequence.extend(["lookup_order", "verify_identity", "inspect_policy_rules", "calculate_refund_eligibility", "finalize_refund_case"])
    return sequence


def describe_tool_result(name: str, result: Dict[str, Any]) -> str:
    if name == "extract_entities":
        entities = result["entities"]
        found = ", ".join(
            f"{key}: {value}"
            for key, value in entities.items()
            if key != "rawMessage" and value not in (None, "unspecified", "")
        )
        return found or "No customer identifiers were found."
    if name == "lookup_customer":
        return f"Matched {result['customer']['name']}." if result.get("customer") else "No customer matched."
    if name == "lookup_order":
        return f"Matched {result['order']['id']} ({result['order']['status']})." if result.get("order") else "No order matched."
    if name == "verify_identity":
        return result["reason"]
    if name == "inspect_policy_rules":
        if result["failures"]:
            return f"{len(result['failures'])} denial rule(s) matched."
        if result["review"]:
            return f"{len(result['review'])} manual review rule(s) matched."
        return "All automated policy checks passed."
    if name == "calculate_refund_eligibility":
        return f"{result['outcome']}: {result['reason']}"
    if name == "finalize_refund_case":
        return f"Case {result['refundCase']['caseId']} recorded."
    return "Completed."


def compose_agent_reply(state: Dict[str, Any]) -> str:
    eligibility = state["eligibility"]
    refund_case = state["refundCase"]
    customer = state["customer"]
    order = state["order"]
    customer_name = customer["name"] if customer else "there"
    item_text = ", ".join(order["items"]) if order and order.get("items") else "the order"

    if eligibility["outcome"] == "APPROVED":
        return f"Approved. I processed a ${eligibility['refundAmount']:.2f} refund for {item_text}. Case {refund_case['caseId']} is recorded."
    if eligibility["outcome"] == "MANUAL_REVIEW":
        return f"Thanks, {customer_name}. I cannot approve this automatically. Case {refund_case['caseId']} has been routed to manual review because {eligibility['reason']}"
    return f"I have to deny this refund request. {eligibility['reason']} Case {refund_case['caseId']} is recorded for audit history."


def build_crewai_refund_crew(message: str, session: Session, selected_customer_id: Optional[str], selected_model: Optional[str]) -> Optional[Dict[str, Any]]:
    backend = resolve_agent_backend(selected_model)
    if backend["mode"] == "deterministic-fallback":
        return None

    # CrewAI acts as the LLM orchestration layer; deterministic policy tools stay authoritative.
    # The deterministic policy functions below remain the source of truth for decisions.
    if CrewAgent is None or Crew is None or LLM is None or Process is None or Task is None or tool is None:
        return None

    crew_state: Dict[str, Any] = {
        "message": message,
        "session": session,
        "selectedCustomerId": selected_customer_id or session.selected_customer_id,
        "selectedModel": backend["model"],
        "entities": None,
        "customer": None,
        "order": None,
        "identity": None,
        "policy": None,
        "eligibility": None,
        "refundCase": None,
    }

    @tool("extract_entities")
    def crew_extract_entities(message: str) -> str:
        """Extract refund entities like order ID, email, amount, and reason from the customer message."""
        entities = extract_entities(message, session)
        crew_state["entities"] = entities
        return json.dumps({"entities": entities}, ensure_ascii=False)

    @tool("lookup_customer")
    def crew_lookup_customer(email: str = "", orderId: str = "", selectedCustomerId: str = "") -> str:
        """Look up the most likely CRM customer record using email, order ID, or the selected customer."""
        customer = lookup_customer(email or None, orderId or None, selectedCustomerId or crew_state["selectedCustomerId"])
        crew_state["customer"] = customer
        return json.dumps({"customer": customer}, ensure_ascii=False, default=str)

    @tool("lookup_order")
    def crew_lookup_order(orderId: str = "", customerId: str = "") -> str:
        """Find the order in the CRM and scope it to the matched customer when possible."""
        customer = next((item for item in customers if item["id"] == customerId), None) if customerId else crew_state["customer"]
        order = lookup_order(orderId or None, customer)
        crew_state["order"] = order
        return json.dumps({"order": order}, ensure_ascii=False, default=str)

    @tool("verify_identity")
    def crew_verify_identity() -> str:
        """Verify that the customer identity and order ownership match the CRM."""
        result = verify_identity(
            crew_state["customer"],
            crew_state["order"],
            crew_state["entities"].get("email") if crew_state["entities"] else None,
        )
        crew_state["identity"] = result
        return json.dumps(result, ensure_ascii=False)

    @tool("inspect_policy_rules")
    def crew_inspect_policy_rules() -> str:
        """Evaluate the strict refund policy and return the pass, review, and fail checks."""
        result = inspect_policy_rules(crew_state["customer"], crew_state["order"], crew_state["entities"] or {})
        crew_state["policy"] = result
        return json.dumps(result, ensure_ascii=False, default=str)

    @tool("calculate_refund_eligibility")
    def crew_calculate_refund_eligibility() -> str:
        """Compute the final refund outcome from identity and policy results."""
        result = calculate_refund_eligibility(
            crew_state["identity"] or {"ok": False, "reason": "Identity not checked."},
            crew_state["policy"] or {"failures": ["Policy not checked."], "review": [], "amountAvailable": 0},
            crew_state["entities"] or {},
            crew_state["order"] or {},
        )
        crew_state["eligibility"] = result
        return json.dumps(result, ensure_ascii=False)

    @tool("finalize_refund_case")
    def crew_finalize_refund_case() -> str:
        """Record the refund case for audit history after a final decision is made."""
        if not crew_state["customer"] or not crew_state["order"] or not crew_state["eligibility"] or not crew_state["entities"]:
            return json.dumps({"error": "Missing state for refund case creation."}, ensure_ascii=False)
        result = finalize_refund_case(session, crew_state["customer"], crew_state["order"], crew_state["eligibility"], crew_state["entities"])
        crew_state["refundCase"] = result["refundCase"]
        return json.dumps(result, ensure_ascii=False, default=str)

    if backend["mode"] == "crewai-local":
        llm = LLM(
            model=backend["model"],
            base_url=backend["baseUrl"],
            provider="openai",
            api_key="ollama",
            temperature=0,
        )
    else:
        llm = LLM(model=OPENAI_MODEL, api_key=OPENAI_API_KEY, provider="openai", temperature=0)
    agent = CrewAgent(
        role="Refund Policy Analyst",
        goal="Evaluate refund requests against the strict BajiMart policy and only approve valid cases.",
        backstory="You are an operations agent who must validate identity, payment status, order age, category rules, and risk signals before deciding a refund.",
        verbose=False,
        allow_delegation=False,
        tools=[
            crew_extract_entities,
            crew_lookup_customer,
            crew_lookup_order,
            crew_verify_identity,
            crew_inspect_policy_rules,
            crew_calculate_refund_eligibility,
            crew_finalize_refund_case,
        ],
        llm=llm,
        function_calling_llm=llm,
        max_iter=12,
        reasoning=True,
    )

    task = Task(
        description=(
            "Process this refund request using the tools in a strict sequence: "
            "1) extract_entities, 2) lookup_customer, 3) lookup_order, 4) verify_identity, "
            "5) inspect_policy_rules, 6) calculate_refund_eligibility, 7) finalize_refund_case. "
            "If both the order ID and email are missing, ask for one and stop. "
            "If only the email is present, continue and infer the single matching order when possible. "
            "If the customer has multiple orders and no order ID is available, ask for the order ID and stop. "
            "If identity, payment, window, category, or fraud checks fail, deny the refund. "
            "If risk checks require human review, classify it as manual review. "
            f"Customer message: {message}"
        ),
        expected_output="A short, policy-backed summary of the refund outcome and what happened at each tool step.",
        agent=agent,
        tools=agent.tools,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        process=Process.sequential,
        verbose=False,
        memory=False,
    )

    try:
        output = crew.kickoff(
            inputs={
                "message": message,
                "session_id": session.id,
                "selected_customer_id": selected_customer_id or session.selected_customer_id or "",
            }
        )
        raw = getattr(output, "raw", None) or str(output)
        if raw:
            emit_log(session, "plan", "CrewAI orchestration complete", "CrewAI executed the refund workflow and returned a summary.", {"raw": raw[:2000], "backend": backend["mode"], "model": backend["model"]})
        return crew_state
    except Exception as error:
        emit_log(session, "stop", "CrewAI fallback", f"CrewAI orchestration failed, so the deterministic policy engine will handle the request: {error}")
        return None


def run_agent(session: Session, message: str, selected_customer_id: Optional[str], selected_model: Optional[str] = None) -> Dict[str, Any]:
    session.selected_customer_id = selected_customer_id or session.selected_customer_id
    session.selected_model = normalize_model_name(selected_model or session.selected_model)
    initial_entities = extract_entities(message, session)
    state: Dict[str, Any] = {
        "message": message,
        "session": session,
        "selectedCustomerId": session.selected_customer_id,
        "selectedModel": session.selected_model,
        "needsCustomer": True,
        "entities": initial_entities,
        "customer": None,
        "order": None,
        "identity": None,
        "policy": None,
        "eligibility": None,
        "refundCase": None,
    }

    if not initial_entities["orderId"] and not initial_entities["email"]:
        emit_log(session, "stop", "More information needed", "The agent needs an order ID or email address before it can evaluate the refund.")
        return {
            "text": "I can help with that. Please send either the order ID, such as ORD-7001, or the email address on the order, along with a short reason for the refund request.",
            "state": state,
        }

    if not initial_entities["hasRefundIntent"]:
        emit_log(session, "stop", "Refund intent needed", "The agent needs an explicit refund request before it can continue.")
        target_label = f"order {initial_entities['orderId']}" if initial_entities["orderId"] else "your account"
        return {
            "text": f"I found {target_label}, but I still need you to explicitly say you want a refund. For example: 'I want a refund because it arrived damaged.'",
            "state": state,
        }

    if not initial_entities["hasReason"]:
        emit_log(session, "stop", "More information needed", "The agent needs a short refund reason before it can evaluate the order.")
        target_label = f"order {initial_entities['orderId']}" if initial_entities["orderId"] else "your account"
        return {
            "text": f"I found {target_label}, but I still need a short reason for the refund, such as damaged, missing, wrong item, or duplicate charge.",
            "state": state,
        }

    crew_state = build_crewai_refund_crew(message, session, selected_customer_id, session.selected_model)
    if crew_state and crew_state.get("eligibility") and crew_state.get("refundCase"):
        if crew_state.get("customer") and not session.selected_customer_id:
            session.selected_customer_id = crew_state["customer"]["id"]
        session.selected_order_id = crew_state["order"]["id"] if crew_state.get("order") else session.selected_order_id
        final_text = compose_agent_reply(crew_state)
        emit_log(session, "decision", crew_state["eligibility"]["outcome"], final_text, {"refundCase": crew_state["refundCase"], "policyTextLength": len(policy_text), "crewai": True, "model": session.selected_model})
        return {"text": final_text, "state": crew_state}
    if crew_state:
        emit_log(session, "plan", "CrewAI agent engaged", "CrewAI was used to bootstrap the refund analysis before the deterministic policy checks ran.", {"crewai": True, "model": session.selected_model})
    else:
        emit_log(session, "plan", "Agent received request", "Planning the next tool calls for identity, order, policy, and outcome checks.", {"crewai": False})

    for tool_name in plan_tool_sequence(state):
        emit_log(session, "tool", f"Calling {tool_name}", "The agent selected this tool based on the current refund state.")
        if tool_name == "extract_entities":
            result = {"entities": initial_entities}
        elif tool_name == "lookup_customer":
            result = {"customer": lookup_customer(state["entities"].get("email"), state["entities"].get("orderId"), session.selected_customer_id)}
        elif tool_name == "lookup_order":
            result = {"order": lookup_order(state["entities"].get("orderId"), state["customer"])}
        elif tool_name == "verify_identity":
            result = verify_identity(state["customer"], state["order"], state["entities"].get("email"))
        elif tool_name == "inspect_policy_rules":
            result = inspect_policy_rules(state["customer"], state["order"], state["entities"])
        elif tool_name == "calculate_refund_eligibility":
            result = calculate_refund_eligibility(state["identity"], state["policy"], state["entities"], state["order"])
        elif tool_name == "finalize_refund_case":
            result = finalize_refund_case(session, state["customer"], state["order"], state["eligibility"], state["entities"])
        else:
            result = {}

        if tool_name == "extract_entities":
            state["entities"] = result["entities"]
            state["needsCustomer"] = bool(result["entities"].get("email") or session.selected_customer_id)
        if tool_name == "lookup_customer":
            state["customer"] = result["customer"]
        if tool_name == "lookup_order":
            state["order"] = result["order"]
            if state["order"]:
                session.selected_order_id = state["order"]["id"]
            elif not state["entities"].get("orderId") and state["customer"] and len(state["customer"].get("orders", [])) > 1:
                emit_log(session, "stop", "More information needed", "The customer has multiple orders, so the agent needs an order ID to continue.")
                return {
                    "text": "I found your account, but I need the order ID to continue because there are multiple orders on that account.",
                    "state": state,
                }
        if tool_name == "verify_identity":
            state["identity"] = result
        if tool_name == "inspect_policy_rules":
            state["policy"] = result
        if tool_name == "calculate_refund_eligibility":
            state["eligibility"] = result
        if tool_name == "finalize_refund_case":
            state["refundCase"] = result["refundCase"]

        emit_log(session, "observation", f"{tool_name} result", describe_tool_result(tool_name, result), result)

    text = compose_agent_reply(state)

    emit_log(session, "decision", state["eligibility"]["outcome"], text, {"refundCase": state["refundCase"], "policyTextLength": len(policy_text), "model": session.selected_model})
    return {"text": text, "state": state}


def handle_voice_turn(session: Session, body: Dict[str, Any]) -> Dict[str, Any]:
    if not is_openai_voice_enabled():
        return {
            "error": "OpenAI voice pipeline is not configured.",
            "detail": "Set OPENAI_API_KEY to enable voice transcription and speech generation.",
        }

    audio_payload = str(body.get("audioBase64") or "").strip()
    if not audio_payload:
        return {"error": "Audio payload is required."}

    try:
        audio_bytes = decode_voice_payload(audio_payload)
    except ValueError as error:
        return {"error": "Invalid audio payload.", "detail": str(error)}

    mime_type = str(body.get("mimeType") or "audio/webm").strip() or "audio/webm"

    try:
        transcript = transcribe_openai_voice(audio_bytes, mime_type)
        session.messages.append({"role": "user", "content": transcript, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        reply = run_agent(session, transcript, body.get("customerId"), body.get("modelName"))
        session.messages.append({"role": "assistant", "content": reply["text"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        speech = synthesize_openai_voice(reply["text"])
    except RuntimeError as error:
        return {"error": "OpenAI voice processing failed.", "detail": str(error)}

    state = reply["state"]
    return {
        "sessionId": session.id,
        "transcript": transcript,
        "message": reply["text"],
        "decision": state["eligibility"],
        "customer": summarize_customer(state["customer"]) if state["customer"] else None,
        "order": state["order"],
        "case": state["refundCase"],
        "policyChecks": state["policy"]["checks"] if state["policy"] else [],
        "audioBase64": base64.b64encode(speech["audioBytes"]).decode("ascii"),
        "audioMimeType": speech["audioMimeType"],
        "logs": session.logs,
    }


def summarize_customer(customer: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": customer["id"],
        "name": customer["name"],
        "email": customer["email"],
        "tier": customer["tier"],
        "fraudScore": customer["fraudScore"],
        "refundsLast180Days": customer["refundsLast180Days"],
        "notes": customer["notes"],
        "orders": customer["orders"],
    }


def get_runtime_config() -> Dict[str, Any]:
    openai_voice_enabled = is_openai_voice_enabled()
    local_llm_available = is_local_llm_available()
    backend = resolve_agent_backend(LOCAL_LLM_MODEL)
    return {
        "agent": {
            "llmEnabled": USE_LLM,
            "llmProvider": LLM_PROVIDER,
            "crewAIInstalled": CREWAI_AVAILABLE,
            "crewAIConfigured": bool(CREWAI_AVAILABLE and (OPENAI_API_KEY or local_llm_available)),
            "localLLMAvailable": local_llm_available,
            "localToolCallingAvailable": bool(USE_LLM and CREWAI_AVAILABLE and local_llm_available),
            "mode": backend["mode"],
            "label": backend["label"],
            "selectedModel": LOCAL_LLM_MODEL,
            "localModels": LOCAL_MODEL_CHOICES,
            "localBaseUrl": LOCAL_LLM_BASE_URL,
        },
        "voice": {
            "browserSpeechRecognition": True,
            "browserSpeechSynthesis": True,
            "openAIRealtimeEnabled": False,
            "openAIVoicePipelineEnabled": openai_voice_enabled,
            "openAITranscribeModel": OPENAI_TRANSCRIBE_MODEL,
            "openAITTSModel": OPENAI_TTS_MODEL,
            "mode": "openai-audio-pipeline" if openai_voice_enabled else "browser-fallback",
        },
        "serverTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": {"effectiveDate": current_policy_date(), "profiles": len(customers)},
    }


class RefundRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        ext = path.suffix.lower()
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".md": "text/markdown; charset=utf-8",
        }.get(ext, "application/octet-stream")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/customers":
            return safe_json(self, 200, customers)
        if parsed.path == "/api/policy":
            return self._serve_file(DATA_DIR / "refund-policy.md")
        if parsed.path == "/api/config":
            return safe_json(self, 200, get_runtime_config())
        if parsed.path == "/api/cases":
            return safe_json(self, 200, refund_cases)
        if parsed.path.startswith("/api/sessions/") and parsed.path.endswith("/events"):
            session_id = parsed.path.split("/")[3]
            session = get_session(session_id)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(f"event: snapshot\ndata: {json.dumps(session.logs)}\n\n".encode("utf-8"))
            self.wfile.flush()
            sse_clients.setdefault(session_id, []).append(self.wfile)
            try:
                while True:
                    time.sleep(15)
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except Exception:
                try:
                    sse_clients.get(session_id, []).remove(self.wfile)
                except Exception:
                    pass
            return

        static_path = (PUBLIC_DIR / parsed.path.lstrip("/")).resolve()
        if parsed.path == "/" or static_path.is_dir():
            return self._serve_file(PUBLIC_DIR / "index.html")
        if static_path.exists() and str(static_path).startswith(str(PUBLIC_DIR.resolve())):
            return self._serve_file(static_path)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/chat":
            body = self._read_json()
            session = get_session(body.get("sessionId"))
            message = str(body.get("message") or "").strip()
            if not message:
                return safe_json(self, 400, {"error": "Message is required."})
            session.messages.append({"role": "user", "content": message, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            reply = run_agent(session, message, body.get("customerId"), body.get("modelName"))
            session.messages.append({"role": "assistant", "content": reply["text"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
            state = reply["state"]
            return safe_json(self, 200, {
                "sessionId": session.id,
                "message": reply["text"],
                "decision": state["eligibility"],
                "customer": summarize_customer(state["customer"]) if state["customer"] else None,
                "order": state["order"],
                "case": state["refundCase"],
                "policyChecks": state["policy"]["checks"] if state["policy"] else [],
                "logs": session.logs,
            })
        if parsed.path == "/api/voice/turn":
            body = self._read_json()
            session = get_session(body.get("sessionId"))
            payload = handle_voice_turn(session, body)
            if "error" in payload:
                status = 400 if payload["error"] in {"OpenAI voice pipeline is not configured.", "Audio payload is required.", "Invalid audio payload."} else 502
                return safe_json(self, status, payload)
            return safe_json(self, 200, payload)
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RefundRequestHandler)
    print(f"BajiMart Customer Support Agent running at http://localhost:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
