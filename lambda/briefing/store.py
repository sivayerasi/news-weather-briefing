"""DynamoDB access: user settings and audit log (single table, pk + sk)."""
import os
import time
from datetime import datetime, timezone

AUDIT_TTL_DAYS = 90


def _table():
    import boto3
    return boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def save_settings(email, city, interests, table=None):
    table = table or _table()
    table.put_item(Item={
        "pk": f"USER#{email}", "sk": "SETTINGS",
        "email": email, "city": city, "interests": interests,
    })


def list_settings(table=None):
    table = table or _table()
    items, kwargs = [], {"FilterExpression": "sk = :s", "ExpressionAttributeValues": {":s": "SETTINGS"}}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            return items
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]


def write_audit(user, trigger, city, interests, result, email_status, table=None):
    """Log sources queried, items kept, and the final briefing."""
    table = table or _table()
    now = datetime.now(timezone.utc)
    table.put_item(Item={
        "pk": f"USER#{user or 'anonymous'}",
        "sk": f"AUDIT#{now.isoformat()}",
        "trigger": trigger,
        "city": city,
        "interests": interests,
        "sources": result["sources"],
        "items_kept": result["items_kept"],
        "gaps": result["gaps"],
        "iterations": result["iterations"],
        "email_status": email_status,
        "briefing": result["briefing"],
        "expires_at": int(time.time()) + AUDIT_TTL_DAYS * 86400,
    })
