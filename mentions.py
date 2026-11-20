"""Normalize leading bot mentions in MaiBot 1.3/1.4 serialized messages."""

import re


def strip_bot_mention(text: str, message: dict, nickname: str = "") -> tuple[str, bool]:
    """Remove only a leading mention; never remove mentions inside arguments.

    MaiBot renders an AtComponent as @cardname, @nickname or @user_id and joins
    components with spaces. Raw segment metadata preserves names containing spaces.
    Plain textual mentions are supported only for the explicitly configured nickname.
    """
    text = text.strip()
    account_id = str(message.get("account_id") or "")
    marked = message.get("is_mentioned") is True or message.get("is_at") is True
    segments = message.get("raw_message")
    removed = False
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                break
            if (
                segment.get("type") == "text"
                and not str(segment.get("data") or "").strip()
            ):
                continue
            if segment.get("type") != "at" or not isinstance(segment.get("data"), dict):
                break
            data = segment["data"]
            # MaiBot 1.4 passes the receiving account ID. A mention of another
            # account must never be removed, even if a later segment mentions us.
            if account_id:
                if str(data.get("target_user_id") or "") != account_id:
                    return text, removed
            elif not marked:
                break
            name = (
                data.get("target_user_cardname")
                or data.get("target_user_nickname")
                or data.get("target_user_id")
            )
            if not name:
                break
            remaining = _remove_prefix(text, str(name))
            if remaining is None:
                break
            text = remaining
            removed = True
            # Legacy payloads without account_id only provide a message-level
            # flag; keep the original single-mention behavior in that case.
            if not account_id:
                break
    if removed:
        return text, True
    if nickname.strip():
        remaining = _remove_prefix(text, nickname.strip())
        if remaining is not None:
            return remaining, True
    # A marked message may omit raw segments; support its visible single-token @.
    if marked and not segments:
        match = re.match(r"^@[^\s@]+[\s\u200b\ufeff]+", text)
        if match:
            return text[match.end() :].strip(), True
    return text, False


def _remove_prefix(text: str, name: str) -> str | None:
    match = re.match(r"^@" + re.escape(name) + r"(?:[\s\u200b\ufeff]+|(?=[/／]))", text)
    return text[match.end() :].strip() if match else None
