"""Lambda entry point.

Routes (API Gateway HTTP API v2):
  GET  /          -> index.html
  POST /settings  -> save city/interests/email for the daily email
  POST /preview   -> run the agent now and return the briefing
Scheduled (EventBridge): run the agent for every saved user and email it.
"""
import base64
import html
import json
import logging
import os
from pathlib import Path

import agent
import guardrails
import store

log = logging.getLogger()
log.setLevel(logging.INFO)

_bedrock = None
_ses = None


def bedrock():
    global _bedrock
    if _bedrock is None:
        import boto3
        _bedrock = boto3.client("bedrock-runtime")
    return _bedrock


def ses():
    global _ses
    if _ses is None:
        import boto3
        _ses = boto3.client("ses")
    return _ses


def _response(status, body, content_type="application/json"):
    payload = body if isinstance(body, str) else json.dumps(body)
    return {"statusCode": status, "headers": {"Content-Type": content_type}, "body": payload}


def _safe_audit(*args):
    try:
        store.write_audit(*args)
    except Exception:
        log.exception("audit write failed")


def to_html(briefing):
    """Plain-text briefing -> simple HTML (bullets become a real list)."""
    out, in_list = [], False
    for line in briefing.splitlines():
        text = line.strip()
        if text.startswith("- "):
            if not in_list:
                out.append('<ul style="margin:4px 0 12px 0;padding-left:20px">')
                in_list = True
            out.append(f'<li style="margin:0 0 6px 0">{html.escape(text[2:])}</li>')
            continue
        if not text:
            continue  # blank lines never split a list
        if in_list:
            out.append("</ul>")
            in_list = False
        if text.endswith(":") and len(text) <= 40:
            out.append(f'<p style="margin:14px 0 4px 0"><b>{html.escape(text)}</b></p>')
        elif ":" in text[:12]:  # "Weather: ..." style lead-in
            head, _, rest = text.partition(":")
            out.append(f'<p style="margin:10px 0"><b>{html.escape(head)}:</b>{html.escape(rest)}</p>')
        else:
            out.append(f'<p style="margin:10px 0">{html.escape(text)}</p>')
    if in_list:
        out.append("</ul>")
    return '<div style="font-family:Arial,sans-serif;font-size:15px;line-height:1.5;max-width:600px">' + "".join(out) + "</div>"


def send_email(to_addr, briefing):
    sender = os.environ.get("SENDER_EMAIL", "")
    if not sender:
        return "skipped: SENDER_EMAIL not configured"
    try:
        ses().send_email(
            Source=sender,
            Destination={"ToAddresses": [to_addr]},
            Message={
                "Subject": {"Data": "Your daily briefing"},
                "Body": {"Text": {"Data": briefing}, "Html": {"Data": to_html(briefing)}},
            },
        )
        return "sent"
    except Exception as exc:
        log.exception("SES send failed")
        return f"failed: {type(exc).__name__}"


def run_for(settings, trigger, fail_sources=()):
    result = agent.run_agent(bedrock(), settings["city"], settings["interests"], fail_sources)
    return result


def handle_preview(body):
    req = guardrails.validate_request(body)
    result = run_for(req, "preview", req["fail_sources"])
    _safe_audit(req["email"], "preview", req["city"], req["interests"], result, "n/a")
    return {
        "briefing": result["briefing"],
        "gaps": result["gaps"],
        "iterations": result["iterations"],
        "sources": result["sources"],
        "items_kept": result["items_kept"],
    }


def handle_settings(body):
    req = guardrails.validate_request(body, require_email=True)
    store.save_settings(req["email"], req["city"], req["interests"])
    return {"saved": True}


def handle_daily():
    sent = failed = 0
    for user in store.list_settings():
        try:
            result = run_for(user, "scheduled")
            status = send_email(user["email"], result["briefing"])
            _safe_audit(user["email"], "scheduled", user["city"], user["interests"], result, status)
            sent += status == "sent"
            failed += status != "sent"
        except Exception:
            failed += 1
            log.exception("daily briefing failed for one user")  # one user must not stop the rest
    return {"sent": sent, "failed": failed}


def lambda_handler(event, context):
    if event.get("source") == "aws.events":
        return handle_daily()

    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", ""), http.get("path", "")

    if method == "GET" and path == "/":
        return _response(200, (Path(__file__).parent / "index.html").read_text(), "text/html")

    if method == "POST" and path in ("/preview", "/settings"):
        try:
            raw = event.get("body") or "{}"
            if event.get("isBase64Encoded"):
                raw = base64.b64decode(raw).decode("utf-8")
            body = json.loads(raw)
            out = handle_preview(body) if path == "/preview" else handle_settings(body)
            return _response(200, out)
        except (guardrails.ValidationError, json.JSONDecodeError) as exc:
            return _response(400, {"error": str(exc)})
        except Exception:
            log.exception("request failed")
            return _response(500, {"error": "internal error"})

    return _response(404, {"error": "not found"})
