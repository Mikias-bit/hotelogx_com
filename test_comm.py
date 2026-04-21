import os
import json
import hmac
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()
app = Flask(__name__)

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "change-me")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_APP_SECRET = os.getenv("WHATSAPP_APP_SECRET", "")

MEWS_BASE_URL = os.getenv("MEWS_BASE_URL", "https://api.mews-demo.com")
MEWS_CLIENT_TOKEN = os.getenv("MEWS_CLIENT_TOKEN", "")
MEWS_ACCESS_TOKEN = os.getenv("MEWS_ACCESS_TOKEN", "")
MEWS_ENTERPRISE_ID = os.getenv("MEWS_ENTERPRISE_ID", "Hotelogx")

# For MVP simplicity we keep state in memory.
# Replace with Redis/Firestore/Postgres for production.
SESSION_STATE: Dict[str, Dict[str, Any]] = {}
GUEST_DIRECTORY: Dict[str, Dict[str, str]] = {}

# Example seed data: map a WhatsApp number to a reservation/customer/room.
# In production, fetch this from Mews using reservation/customer lookups.
# Format: phone -> {room_number, last_name, reservation_id, customer_id, service_order_id(optional)}
GUEST_DIRECTORY.update(
    {
        "31612345678": {
            "room_number": "204",
            "last_name": "Smith",
            "reservation_id": "reservation-demo-204",
            "customer_id": "customer-demo-204",
            "service_order_id": "service-order-demo-204",
        },
        "31684325333": {
            "room_number": "504",
            "last_name": "Mikias",
            "reservation_id": "reservation-demo-204",
            "customer_id": "customer-demo-204",
            "service_order_id": "service-order-demo-204",
        },
        "0684325333": {
            "room_number": "304",
            "last_name": "Alemayehu",
            "reservation_id": "reservation-demo-204",
            "customer_id": "customer-demo-204",
            "service_order_id": "service-order-demo-204",
        },
        "31687031003": {
            "room_number": "604",
            "last_name": "Nathali",
            "reservation_id": "reservation-demo-604",
            "customer_id": "customer-demo-604",
            "service_order_id": "service-order-demo-604",
        }
    }
)


# -----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
class MewsClient:
    def __init__(self, base_url: str, client_token: str, access_token: str, enterprise_id: str):
        self.base_url = base_url.rstrip("/")
        self.client_token = client_token
        self.access_token = access_token
        self.enterprise_id = enterprise_id

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
        }

    def _payload(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "ClientToken": self.client_token,
            "AccessToken": self.access_token,
            "EnterpriseId": self.enterprise_id,
        }
        payload.update(extra)
        return payload

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        response = requests.post(url, headers=self._headers(), json=payload, timeout=30)
        response.raise_for_status()
        return response.json() if response.content else {}

    def get_all_reservations(self, phone_number: str) -> Dict[str, Any]:
        # Example placeholder. Exact filters depend on your Mews data model and available guest data.
        payload = self._payload(
            {
                "CustomerQuery": {
                    "PhoneNumber": phone_number,
                },
            }
        )
        return self._post("/api/connector/v1/reservations/getAll", payload)

    def add_message_thread(self, customer_id: str, subject: str) -> Dict[str, Any]:
        payload = self._payload(
            {
                "MessageThreads": [
                    {
                        "CustomerId": customer_id,
                        "Subject": subject,
                    }
                ]
            }
        )
        return self._post("/api/connector/v1/customerMessaging/addMessageThread", payload)

    def add_messages(self, message_thread_id: str, text: str) -> Dict[str, Any]:
        payload = self._payload(
            {
                "Messages": [
                    {
                        "MessageThreadId": message_thread_id,
                        "Text": text,
                    }
                ]
            }
        )
        return self._post("/api/connector/v1/customerMessaging/addMessages", payload)

    def add_service_order_note(self, service_order_id: str, text: str) -> Dict[str, Any]:
        payload = self._payload(
            {
                "ServiceOrderNotes": [
                    {
                        "ServiceOrderId": service_order_id,
                        "Text": text,
                    }
                ]
            }
        )
        return self._post("/api/connector/v1/serviceOrders/addServiceOrderNotes", payload)

    def update_reservation_labels_or_notes(self, reservation_id: str, text: str) -> Dict[str, Any]:
        # This is an adapter placeholder. Depending on your Mews setup, you may prefer:
        # - reservation updates
        # - message threads
        # - service order notes
        # - custom operational tags/notes handled in your own DB
        payload = self._payload(
            {
                "Reservations": [
                    {
                        "Id": reservation_id,
                        "Notes": text,
                    }
                ]
            }
        )
        return self._post("/api/connector/v1/reservations/update", payload)


mews = MewsClient(
    base_url=MEWS_BASE_URL,
    client_token=MEWS_CLIENT_TOKEN,
    access_token=MEWS_ACCESS_TOKEN,
    enterprise_id=MEWS_ENTERPRISE_ID,
)


def verify_meta_signature(raw_body: bytes, signature_header: Optional[str]) -> bool:
    if not WHATSAPP_APP_SECRET:
        app.logger.warning("WHATSAPP_APP_SECRET not set - accepting request for testing")
        return True  # MVP shortcut; set the secret in real deployments.
    if not signature_header:
        app.logger.warning("Missing X-Hub-Signature-256 header")
        return False
    if not signature_header.startswith("sha256="):
        app.logger.warning(f"Invalid signature header format: {signature_header[:20]}...")
        return False

    their_sig = signature_header.split("=", 1)[1]
    our_sig = hmac.new(WHATSAPP_APP_SECRET.encode(), raw_body, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(our_sig, their_sig):
        app.logger.warning(f"Signature mismatch. Expected: {our_sig[:10]}..., Got: {their_sig[:10]}...")
        return False

    return True


def normalize_phone(wa_id: str) -> str:
    return "".join(ch for ch in wa_id if ch.isdigit())


def upsert_session(phone: str) -> Dict[str, Any]:
    state = SESSION_STATE.setdefault(
        phone,
        {
            "step": "idle",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return state


def send_whatsapp_text(to: str, body: str) -> Dict[str, Any]:
    if not WHATSAPP_PHONE_NUMBER_ID or not WHATSAPP_TOKEN:
        app.logger.warning("WhatsApp credentials missing. Pretending message was sent.")
        return {"mock": True, "to": to, "body": body}

    url = f"https://graph.facebook.com/v23.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def send_whatsapp_buttons(to: str, body: str, buttons: list[dict[str, str]]) -> Dict[str, Any]:
    if not WHATSAPP_PHONE_NUMBER_ID or not WHATSAPP_TOKEN:
        app.logger.warning("WhatsApp credentials missing. Pretending button message was sent.")
        return {"mock": True, "to": to, "body": body, "buttons": buttons}

    url = f"https://graph.facebook.com/v23.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {
                            "id": b["id"],
                            "title": b["title"],
                        },
                    }
                    for b in buttons[:3]
                ]
            },
        },
    }
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_incoming_message(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        entry = payload["entry"][0]
        change = entry["changes"][0]
        value = change["value"]
        messages = value.get("messages", [])
        if not messages:
            return None

        msg = messages[0]
        contacts = value.get("contacts", [])
        wa_id = msg.get("from") or (contacts[0].get("wa_id") if contacts else None)
        if not wa_id:
            return None

        phone = normalize_phone(wa_id)
        msg_type = msg.get("type")
        text = ""

        if msg_type == "text":
            text = msg["text"].get("body", "").strip()
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            if interactive.get("type") == "button_reply":
                text = interactive["button_reply"].get("id", "").strip()
            elif interactive.get("type") == "list_reply":
                text = interactive["list_reply"].get("id", "").strip()
        else:
            text = ""

        return {
            "phone": phone,
            "message_type": msg_type,
            "text": text.lower(),
            "raw": msg,
        }
    except (KeyError, IndexError, TypeError):
        return None


def build_dnd_note(room_number: str, phone: str, until_text: Optional[str] = None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    base = f"WhatsApp DND request for room {room_number} from phone {phone} at {stamp}."
    if until_text:
        return f"{base} Requested until {until_text}."
    return base


def apply_dnd(phone: str, until_text: Optional[str] = None) -> Dict[str, Any]:
    guest = GUEST_DIRECTORY.get(phone)
    if not guest:
        return {"ok": False, "reason": "guest_not_found"}

    room_number = guest["room_number"]
    note = build_dnd_note(room_number, phone, until_text)
    results = {"reservation": None, "message_thread": None, "service_order": None}

    # 1) Reservation note/update adapter
    try:
        if guest.get("reservation_id"):
            results["reservation"] = mews.update_reservation_labels_or_notes(guest["reservation_id"], note)
    except Exception as exc:
        results["reservation"] = {"error": str(exc)}

    # 2) Customer messaging thread, useful for traceability
    try:
        if guest.get("customer_id"):
            thread_resp = mews.add_message_thread(guest["customer_id"], "DND request")
            results["message_thread"] = thread_resp
    except Exception as exc:
        results["message_thread"] = {"error": str(exc)}

    # 3) Optional housekeeping note on a service order
    try:
        if guest.get("service_order_id"):
            results["service_order"] = mews.add_service_order_note(guest["service_order_id"], note)
    except Exception as exc:
        results["service_order"] = {"error": str(exc)}

    return {
        "ok": True,
        "room_number": room_number,
        "note": note,
        "results": results,
    }


def handle_user_message(phone: str, text: str) -> None:
    state = upsert_session(phone)
    guest = GUEST_DIRECTORY.get(phone)

    if not guest:
        send_whatsapp_text(
            phone,
            "I could not match your WhatsApp number to an in-house reservation. Please reply with your room number and last name.",
        )
        state["step"] = "identify_guest"
        return

    if text in {"dnd", "do not disturb", "donotdisturb", "privacy"}:
        state["step"] = "confirm_dnd"
        send_whatsapp_buttons(
            phone,
            f"Room {guest['room_number']}: would you like to activate Do Not Disturb now?",
            [
                {"id": "dnd_yes", "title": "Activate DND"},
                {"id": "dnd_until", "title": "Set end time"},
                {"id": "dnd_cancel", "title": "Cancel"},
            ],
        )
        return

    if state.get("step") == "confirm_dnd":
        if text == "dnd_yes":
            result = apply_dnd(phone)
            if result["ok"]:
                send_whatsapp_text(
                    phone,
                    f"Done — Do Not Disturb is recorded for room {result['room_number']}. Housekeeping can now see the request in the workflow.",
                )
            else:
                send_whatsapp_text(phone, "Sorry, I could not record your DND request right now.")
            state["step"] = "idle"
            return

        if text == "dnd_until":
            state["step"] = "await_dnd_time"
            send_whatsapp_text(phone, "Please reply with an end time, for example: 14:00")
            return

        if text == "dnd_cancel":
            state["step"] = "idle"
            send_whatsapp_text(phone, "No problem — I cancelled the request.")
            return

    if state.get("step") == "await_dnd_time":
        until_text = text.strip()
        result = apply_dnd(phone, until_text=until_text)
        if result["ok"]:
            send_whatsapp_text(
                phone,
                f"Done — Do Not Disturb is recorded for room {result['room_number']} until {until_text}.",
            )
        else:
            send_whatsapp_text(phone, "Sorry, I could not record your DND request right now.")
        state["step"] = "idle"
        return

    if text in {"help", "menu", "start"}:
        send_whatsapp_buttons(
            phone,
            "How can I help you today?",
            [
                {"id": "dnd", "title": "Do Not Disturb"},
                {"id": "towels", "title": "Need towels"},
                {"id": "clean", "title": "Clean room"},
            ],
        )
        state["step"] = "idle"
        return

    send_whatsapp_text(
        phone,
        "Reply with DND to activate Do Not Disturb, or HELP to see more options.",
    )


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------
@app.get("/")
def root() -> Any:
    return jsonify({"message": "WhatsApp Mews DND Service is running", "health": "/health", "webhook": "/webhook"})


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "service": "whatsapp-mews-dnd-mvp"})


@app.route("/webhook", methods=["GET", "POST"])
def webhook() -> Any:
    if request.method == "GET":
        # Webhook verification
        mode = request.args.get("hub.mode")
        challenge = request.args.get("hub.challenge")
        verify_token = request.args.get("hub.verify_token")

        if mode == "subscribe" and verify_token == VERIFY_TOKEN:
            return challenge, 200
        return jsonify({"error": "verification_failed"}), 403

    elif request.method == "POST":
        # Webhook message handling
        raw_body = request.get_data()
        signature = request.headers.get("X-Hub-Signature-256")

        if not verify_meta_signature(raw_body, signature):
            return jsonify({"error": "invalid_signature"}), 403

        payload = request.get_json(silent=True) or {}
        incoming = parse_incoming_message(payload)
        if not incoming:
            return jsonify({"ok": True, "ignored": True})

        try:
            handle_user_message(incoming["phone"], incoming["text"])
            return jsonify({"ok": True})
        except Exception as exc:
            app.logger.exception("Failed handling message")
            return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/debug/test-message")
def debug_test_message() -> Any:
    payload = request.get_json(force=True)
    phone = normalize_phone(payload["phone"])
    text = payload["text"].strip().lower()
    handle_user_message(phone, text)
    return jsonify({"ok": True, "session": SESSION_STATE.get(phone)})


@app.get("/debug/guests")
def debug_guests() -> Any:
    """Display all registered guests for testing purposes"""
    guests_list = []
    for phone, guest_info in GUEST_DIRECTORY.items():
        guests_list.append({
            "phone": phone,
            "room_number": guest_info.get("room_number"),
            "last_name": guest_info.get("last_name"),
            "customer_id": guest_info.get("customer_id"),
        })
    return jsonify({
        "ok": True,
        "guests": guests_list,
        "instruction": "Use the phone number to send messages. When asked for room number and last name, provide those values."
    })


@app.post("/admin/guest-directory")
def admin_upsert_guest() -> Any:
    payload = request.get_json(force=True)
    phone = normalize_phone(payload["phone"])
    GUEST_DIRECTORY[phone] = {
        "room_number": payload["room_number"],
        "last_name": payload.get("last_name", ""),
        "reservation_id": payload.get("reservation_id", ""),
        "customer_id": payload.get("customer_id", ""),
        "service_order_id": payload.get("service_order_id", ""),
    }
    return jsonify({"ok": True, "guest": GUEST_DIRECTORY[phone]})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), debug=True)
