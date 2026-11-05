"""Normalize leading mentions using MaiBot 1.3.16's serialized message data."""

import re


def strip_bot_mention(text: str, message: dict, nickname: str = "") -> tuple[str, bool]:
    """Remove only a leading mention; never remove mentions inside arguments.

    MaiBot renders an AtComponent as @cardname, @nickname or @user_id and joins
    components with spaces. Raw segment metadata preserves names containing spaces.
    Plain textual mentions are supported only for the explicitly configured nickname.
    """
    text = text.strip()
    names = set()
    if nickname.strip():
        names.add(nickname.strip())
    marked = message.get("is_mentioned") is True or message.get("is_at") is True
    if marked:
        segments = message.get("raw_message")
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    break
                if (
                    segment.get("type") == "text"
                    and not str(segment.get("data") or "").strip()
                ):
                    continue
                if segment.get("type") == "at":
                    data = segment.get("data")
                    if isinstance(data, dict):
                        name = (
                            data.get("target_user_cardname")
                            or data.get("target_user_nickname")
                            or data.get("target_user_id")
                        )
                        if name:
                            names.add(str(name))
                break
    for name in sorted(names, key=len, reverse=True):
        match = re.match(
            r"^@" + re.escape(name) + r"(?:[\s\u200b\ufeff]+|(?=[/／]))", text
        )
        if match:
            return text[match.end() :].strip(), True
    # A marked message may omit raw segments; support its visible single-token @.
    if marked and not message.get("raw_message"):
        match = re.match(r"^@[^\s@]+[\s\u200b\ufeff]+", text)
        if match:
            return text[match.end() :].strip(), True
    return text, False
