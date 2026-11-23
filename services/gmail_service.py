import os
import base64
import html
import re
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]


def credentials_from_row(row):
    return Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri=row["token_uri"] or "https://oauth2.googleapis.com/token",
        client_id=row["client_id"],
        client_secret=row["client_secret"],
        scopes=(row["scopes"] or "").split(),
    )


def gmail_service(row):
    return build(
        "gmail",
        "v1",
        credentials=credentials_from_row(row),
        cache_discovery=False,
    )


def _decode(data):
    if not data:
        return ""
    try:
        padded = data + "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in ("script", "style", "noscript"):
            self.skip_depth += 1
        elif t in (
            "br", "p", "div", "tr", "li", "table", "section", "article",
            "h1", "h2", "h3", "h4", "h5", "h6"
        ):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in ("script", "style", "noscript"):
            self.skip_depth = max(0, self.skip_depth - 1)
        elif t in (
            "p", "div", "tr", "li", "table", "section", "article",
            "h1", "h2", "h3", "h4", "h5", "h6"
        ):
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skip_depth == 0 and data:
            self.parts.append(data)


def _html_to_text(value):
    if not value:
        return ""
    try:
        parser = _HTMLTextExtractor()
        parser.feed(value)
        text = "".join(parser.parts)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    lines = []
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            lines.append(line)
    return "\n".join(lines)


def _walk_parts(part, result):
    mime = (part.get("mimeType") or "").lower()
    body = part.get("body", {}) or {}
    filename = part.get("filename") or ""
    data = body.get("data")

    if mime == "text/plain" and data:
        decoded = _decode(data)
        if decoded:
            result["plain"].append(decoded)
    elif mime == "text/html" and data:
        decoded = _decode(data)
        if decoded:
            result["html"].append(decoded)

    if filename and body.get("attachmentId"):
        result["attachments"].append(
            {
                "filename": filename,
                "mimeType": mime,
                "attachmentId": body["attachmentId"],
            }
        )

    for child in part.get("parts", []) or []:
        _walk_parts(child, result)


def fetch_message(row, message_id):
    svc = gmail_service(row)
    msg = (
        svc.users()
        .messages()
        .get(userId="me", id=message_id, format="full", fields="id,threadId,snippet,internalDate,payload")
        .execute()
    )

    headers = {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }

    result = {"plain": [], "html": [], "attachments": []}
    _walk_parts(msg.get("payload", {}), result)

    plain_body = "\n\n".join(
        x for x in result["plain"] if x.strip()
    ).strip()
    html_body = "\n\n".join(
        _html_to_text(x) for x in result["html"] if x.strip()
    ).strip()
    snippet = html.unescape(msg.get("snippet") or "").strip()

    # Read the real message body whenever possible. HTML-only supplier emails
    # are converted to text. Gmail snippet is the final fallback.
    body_text = plain_body or html_body or snippet

    date = headers.get("date", "")
    try:
        date_iso = parsedate_to_datetime(date).isoformat()
    except Exception:
        date_iso = date

    return {
        "id": msg["id"],
        "threadId": msg.get("threadId"),
        "rfc_message_id": headers.get("message-id", "").strip(),
        "internal_date": msg.get("internalDate", ""),
        "received_at": date_iso,
        "sender": headers.get("from", ""),
        "recipients": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "subject": headers.get("subject", ""),
        "body": body_text[:50000],
        "attachments": result["attachments"],
    }


def download_attachment(row, message_id, attachment_id):
    svc = gmail_service(row)
    att = (
        svc.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    data = att.get("data") or ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def list_message_page(row, query="newer_than:7d", max_results=50, page_token=None):
    svc = gmail_service(row)
    params = {
        "userId": "me",
        "q": query,
        "maxResults": max_results,
    }
    if page_token:
        params["pageToken"] = page_token
    params["fields"] = "messages/id,nextPageToken,resultSizeEstimate"
    resp = svc.users().messages().list(**params).execute()
    return {
        "ids": [x["id"] for x in resp.get("messages", [])],
        "next_page_token": resp.get("nextPageToken"),
        "result_size_estimate": resp.get("resultSizeEstimate", 0),
    }


def list_message_ids(row, query="newer_than:7d", max_results=50):
    data = list_message_page(row, query=query, max_results=max_results)
    return data["ids"]


def get_inbox_total(row):
    """Return Gmail's exact current INBOX message count from the INBOX label."""
    svc = gmail_service(row)
    data = svc.users().labels().get(userId="me", id="INBOX").execute()
    return int(data.get("messagesTotal") or 0)


def list_all_message_ids(row, query="in:inbox", page_size=500):
    """Enumerate every message ID matching the query. Used for coverage verification."""
    svc = gmail_service(row)
    ids = []
    token = None
    page_size = max(1, min(500, int(page_size)))
    while True:
        params = {"userId": "me", "q": query, "maxResults": page_size}
        if token:
            params["pageToken"] = token
        params["fields"] = "messages/id,nextPageToken,resultSizeEstimate"
        data = svc.users().messages().list(**params).execute()
        ids.extend(x["id"] for x in data.get("messages", []) or [])
        token = data.get("nextPageToken")
        if not token:
            break
    # Gmail should not duplicate IDs, but preserve order while guarding anyway.
    return list(dict.fromkeys(ids))



def get_gmail_profile(row):
    """Small profile call used to seed/refresh Gmail History API tracking."""
    svc = gmail_service(row)
    data = svc.users().getProfile(userId="me").execute()
    return {
        "email": data.get("emailAddress") or "",
        "messages_total": int(data.get("messagesTotal") or 0),
        "threads_total": int(data.get("threadsTotal") or 0),
        "history_id": str(data.get("historyId") or ""),
    }


def list_history_message_ids(row, start_history_id, max_results=500):
    """Return incremental INBOX changes since Gmail historyId.

    The older engine tracked only messageAdded events. V20.2 also tracks INBOX
    label additions/removals so local ``in_inbox`` membership stays aligned with
    Gmail between periodic full reconciliation passes.
    """
    if not start_history_id:
        return {"ids": [], "inbox_added_ids": [], "inbox_removed_ids": [], "history_id": ""}
    svc = gmail_service(row)
    token = None
    added_ids = []
    removed_ids = []
    newest_history_id = str(start_history_id)
    while True:
        params = {
            "userId": "me",
            "startHistoryId": str(start_history_id),
            "labelId": "INBOX",
            "historyTypes": ["messageAdded", "labelAdded", "labelRemoved", "messageDeleted"],
            "maxResults": max(1, min(500, int(max_results))),
            "fields": (
                "history(id,messagesAdded(message(id,labelIds)),"
                "labelsAdded(message(id),labelIds),labelsRemoved(message(id),labelIds),"
                "messagesDeleted(message(id))),nextPageToken,historyId"
            ),
        }
        if token:
            params["pageToken"] = token
        data = svc.users().history().list(**params).execute()
        newest_history_id = str(data.get("historyId") or newest_history_id)
        for item in data.get("history", []) or []:
            for added in item.get("messagesAdded", []) or []:
                msg = (added or {}).get("message") or {}
                mid = msg.get("id")
                labels = set(msg.get("labelIds") or [])
                if mid and (not labels or "INBOX" in labels):
                    added_ids.append(mid)
            for changed in item.get("labelsAdded", []) or []:
                if "INBOX" in set((changed or {}).get("labelIds") or []):
                    mid = ((changed or {}).get("message") or {}).get("id")
                    if mid:
                        added_ids.append(mid)
            for changed in item.get("labelsRemoved", []) or []:
                if "INBOX" in set((changed or {}).get("labelIds") or []):
                    mid = ((changed or {}).get("message") or {}).get("id")
                    if mid:
                        removed_ids.append(mid)
            for deleted in item.get("messagesDeleted", []) or []:
                mid = ((deleted or {}).get("message") or {}).get("id")
                if mid:
                    removed_ids.append(mid)
        token = data.get("nextPageToken")
        if not token:
            break
    added_ids = list(dict.fromkeys(added_ids))
    removed_ids = list(dict.fromkeys(removed_ids))
    # If the same message was removed and re-added within the history window,
    # the final INBOX state is represented by the most recent label event. Gmail
    # history is ordered, but de-duplication loses ordering; treating it as added
    # is safer because full verification later reconciles exact membership.
    removed_only = [mid for mid in removed_ids if mid not in set(added_ids)]
    return {
        "ids": added_ids,
        "inbox_added_ids": added_ids,
        "inbox_removed_ids": removed_only,
        "history_id": newest_history_id,
    }


def fetch_thread_context(row, thread_id, max_messages=8):
    """Fetch a compact thread transcript for difficult revision/negotiation cases.

    Used only by the deep AI lane so normal direct extraction remains fast.
    """
    if not thread_id:
        return ""
    svc = gmail_service(row)
    data = svc.users().threads().get(
        userId="me", id=thread_id, format="full",
        fields="messages(id,threadId,snippet,internalDate,payload)"
    ).execute()
    messages = data.get("messages", []) or []
    messages = messages[-max(1, min(int(max_messages), 12)):]
    blocks = []
    for m in messages:
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        result = {"plain": [], "html": [], "attachments": []}
        _walk_parts(m.get("payload", {}), result)
        body = "\n\n".join(x for x in result["plain"] if x.strip()).strip()
        if not body:
            body = "\n\n".join(_html_to_text(x) for x in result["html"] if x.strip()).strip()
        if not body:
            body = html.unescape(m.get("snippet") or "").strip()
        blocks.append(
            "THREAD MESSAGE\n"
            f"DATE: {headers.get('date','')}\n"
            f"FROM: {headers.get('from','')}\n"
            f"TO: {headers.get('to','')}\n"
            f"SUBJECT: {headers.get('subject','')}\n"
            f"BODY:\n{body[:6000]}"
        )
    return "\n\n---\n\n".join(blocks)[-32000:]


def start_mailbox_watch(row, topic_name):
    """Register/renew Gmail users.watch for INBOX changes on a Pub/Sub topic."""
    if not topic_name:
        raise ValueError("Pub/Sub topic is empty")
    svc = gmail_service(row)
    return svc.users().watch(
        userId="me",
        body={
            "topicName": topic_name,
            "labelIds": ["INBOX"],
            "labelFilterBehavior": "INCLUDE",
        },
    ).execute()
