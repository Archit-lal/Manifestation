"""
macOS Messages DB reader.

Reads ~/Library/Messages/chat.db (sqlite3, read-only).
Requires Full Disk Access for the terminal / Python process.
Contact name resolution tries AddressBook first, falls back to handle.
Results cached with a 5s TTL so rapid blinks don't hit sqlite repeatedly.
"""

import glob
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional

DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")
AB_GLOB = os.path.expanduser(
    "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
)

# iMessage epoch starts 2001-01-01, not unix epoch
APPLE_EPOCH_OFFSET = 978307200
NANOSECONDS = 1_000_000_000

CACHE_TTL = 5.0  # seconds


@dataclass
class Contact:
    id: str          # chat_identifier (phone / email / group)
    chat_id: int     # rowid in chat table
    name: str
    handle: str      # phone/email for AppleScript
    last_message: str
    last_ts: float   # unix timestamp
    unread: int = 0
    is_group: bool = False


@dataclass
class Message:
    id: int
    text: str
    from_me: bool
    ts: float        # unix timestamp


@dataclass
class _Cache:
    contacts: Optional[list] = None
    contacts_at: float = 0.0
    threads: dict = field(default_factory=dict)   # chat_id -> (list, float)


_cache = _Cache()


def _apple_date_to_unix(apple_date: int) -> float:
    """Convert iMessage nanosecond date to unix timestamp."""
    if apple_date > 1e12:
        return apple_date / NANOSECONDS + APPLE_EPOCH_OFFSET
    return apple_date + APPLE_EPOCH_OFFSET


def _open_db(path: str):
    uri = f"file:{path}?mode=ro"
    return sqlite3.connect(uri, uri=True, check_same_thread=False)


def _build_address_book() -> dict[str, str]:
    """Return {normalized_phone_or_email: display_name} from AddressBook."""
    names: dict[str, str] = {}
    # Try Sources subdirectories first (more complete), then root DB
    paths = glob.glob(AB_GLOB)
    root = os.path.expanduser("~/Library/Application Support/AddressBook/AddressBook-v22.abcddb")
    if root not in paths:
        paths.append(root)
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            con = _open_db(path)
            cur = con.cursor()
            # Z22_OWNER is the FK in newer schema; ZFIRSTNAME often holds full name
            cur.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, p.ZFULLNUMBER
                FROM ZABCDRECORD r
                JOIN ZABCDPHONENUMBER p ON (p.Z22_OWNER = r.Z_PK OR p.ZOWNER = r.Z_PK)
                WHERE p.ZFULLNUMBER IS NOT NULL
            """)
            for first, last, phone in cur.fetchall():
                name = " ".join(filter(None, [first, last])).strip() or phone
                # Store under multiple normalizations to maximize hit rate
                raw_norm = _normalize_phone(phone)
                stripped = re.sub(r"\D", "", phone)
                if raw_norm and name not in names.values():
                    names[raw_norm] = name
                # Also index by last 10 digits for fuzzy matching
                if len(stripped) >= 10:
                    names[stripped[-10:]] = name
            cur.execute("""
                SELECT r.ZFIRSTNAME, r.ZLASTNAME, e.ZADDRESS
                FROM ZABCDRECORD r
                JOIN ZABCDEMAILADDRESS e ON (e.Z22_OWNER = r.Z_PK OR e.ZOWNER = r.Z_PK)
                WHERE e.ZADDRESS IS NOT NULL
            """)
            for first, last, email in cur.fetchall():
                name = " ".join(filter(None, [first, last])).strip() or email
                names[email.lower()] = name
            con.close()
        except Exception:
            continue
    return names


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return f"+1{digits}"
    if len(digits) == 11 and digits[0] == "1":
        return f"+{digits}"
    return f"+{digits}" if digits else ""


_ab_cache: Optional[dict] = None
_ab_cache_at: float = 0.0
AB_TTL = 300.0  # address book rarely changes


def _address_book() -> dict[str, str]:
    global _ab_cache, _ab_cache_at
    if _ab_cache is None or time.time() - _ab_cache_at > AB_TTL:
        _ab_cache = _build_address_book()
        _ab_cache_at = time.time()
    return _ab_cache


def _resolve_name(handle_id: str) -> str:
    ab = _address_book()
    # try as-is (email)
    if handle_id.lower() in ab:
        return ab[handle_id.lower()]
    # try normalized phone
    norm = _normalize_phone(handle_id)
    if norm in ab:
        return ab[norm]
    # try last-10-digit fuzzy match
    digits = re.sub(r"\D", "", handle_id)
    if len(digits) >= 10 and digits[-10:] in ab:
        return ab[digits[-10:]]
    return handle_id  # fallback to raw handle


def get_contacts(limit: int = 50) -> list[Contact]:
    """Return contacts sorted by most-recent message timestamp, descending."""
    now = time.time()
    if _cache.contacts is not None and now - _cache.contacts_at < CACHE_TTL:
        return _cache.contacts

    try:
        con = _open_db(DB_PATH)
    except Exception as e:
        raise RuntimeError(
            f"Cannot open Messages DB: {e}\n"
            "Grant Full Disk Access to Terminal in System Settings → Privacy & Security."
        )

    cur = con.cursor()
    # One row per chat, with the most-recent message text + date
    cur.execute("""
        SELECT
            c.ROWID              AS chat_id,
            c.chat_identifier    AS chat_ident,
            c.display_name       AS group_name,
            c.style              AS style,
            m.text               AS last_text,
            m.date               AS last_date,
            m.is_from_me         AS is_from_me,
            h.id                 AS handle_id
        FROM chat c
        LEFT JOIN (
            SELECT cmj.chat_id, MAX(msg.date) AS max_date
            FROM chat_message_join cmj
            JOIN message msg ON msg.ROWID = cmj.message_id
            WHERE msg.text IS NOT NULL
            GROUP BY cmj.chat_id
        ) latest ON latest.chat_id = c.ROWID
        LEFT JOIN message m ON (
            m.ROWID = (
                SELECT cmj2.message_id
                FROM chat_message_join cmj2
                JOIN message msg2 ON msg2.ROWID = cmj2.message_id
                WHERE cmj2.chat_id = c.ROWID AND msg2.date = latest.max_date
                  AND msg2.text IS NOT NULL
                LIMIT 1
            )
        )
        LEFT JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
        LEFT JOIN handle h ON h.ROWID = chj.handle_id AND c.style = 45
        WHERE latest.max_date IS NOT NULL
        GROUP BY c.ROWID
        ORDER BY latest.max_date DESC
        LIMIT ?
    """, (limit,))

    rows = cur.fetchall()
    con.close()

    contacts = []
    for (chat_id, chat_ident, group_name, style,
         last_text, last_date, is_from_me, handle_id) in rows:
        is_group = style == 43  # group chat style
        # Resolve display name
        if is_group and group_name:
            name = group_name
        elif handle_id:
            name = _resolve_name(handle_id)
        else:
            name = _resolve_name(chat_ident)

        handle = handle_id or chat_ident
        ts = _apple_date_to_unix(last_date) if last_date else 0.0
        preview = (last_text or "").replace("\ufffc", "").strip()[:80]
        if is_from_me:
            preview = f"You: {preview}"

        contacts.append(Contact(
            id=chat_ident,
            chat_id=chat_id,
            name=name,
            handle=handle,
            last_message=preview,
            last_ts=ts,
            is_group=is_group,
        ))

    _cache.contacts = contacts
    _cache.contacts_at = now
    return contacts


def get_thread(chat_id: int, limit: int = 40) -> list[Message]:
    """Return the last `limit` messages for the chat, oldest first."""
    now = time.time()
    cached = _cache.threads.get(chat_id)
    if cached:
        msgs, at = cached
        if now - at < CACHE_TTL:
            return msgs

    try:
        con = _open_db(DB_PATH)
    except Exception as e:
        raise RuntimeError(f"Cannot open Messages DB: {e}")

    cur = con.cursor()
    cur.execute("""
        SELECT m.ROWID, m.text, m.is_from_me, m.date
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id = ?
          AND m.text IS NOT NULL
          AND m.text != ''
        ORDER BY m.date DESC
        LIMIT ?
    """, (chat_id, limit))

    rows = cur.fetchall()
    con.close()

    messages = [
        Message(
            id=row_id,
            text=(text or "").replace("\ufffc", "").strip(),
            from_me=bool(is_from_me),
            ts=_apple_date_to_unix(date),
        )
        for row_id, text, is_from_me, date in reversed(rows)
        if (text or "").strip()
    ]

    _cache.threads[chat_id] = (messages, now)
    return messages


def invalidate_thread(chat_id: int) -> None:
    """Bust the thread cache after sending a message."""
    _cache.threads.pop(chat_id, None)
