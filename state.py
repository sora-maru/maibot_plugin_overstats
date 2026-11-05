"""Persist bindings and settings atomically, without blocking the event loop."""

from copy import deepcopy
from pathlib import Path

import asyncio
import json
import os


class StateStore:
    def __init__(self, directory: Path):
        self.path = directory / "state.json"
        self.lock = asyncio.Lock()
        self.data = {
            "bindings": {},
            "chats": {},
            "cooldowns": {},
            "last_targets": {},
            "maintenance": "",
            "review_usage": {},
        }

    async def load(self):
        if not await asyncio.to_thread(self.path.exists):
            return
        # A corrupt state file must be reported, never silently replaced with empty bindings.
        data = json.loads(
            await asyncio.to_thread(self.path.read_text, encoding="utf-8")
        )
        if not isinstance(data, dict):
            raise ValueError("Overstats state.json 必须为对象")
        self.data.update(data)
        for section in (
            "bindings",
            "chats",
            "cooldowns",
            "last_targets",
            "review_usage",
        ):
            if not isinstance(self.data[section], dict):
                raise ValueError(f"Overstats state.json 的 {section} 无效")

    def get(self, section: str, key: str, default=None):
        return self.data[section].get(key, default)

    async def put(self, section: str, key: str | None, value):
        async with self.lock:
            updated = deepcopy(self.data)
            if key is None:
                updated[section] = value
            elif value is None:
                updated[section].pop(key, None)
            else:
                updated[section][key] = value
            await self._commit(updated)

    async def reserve_review(self, key: str, period: str, limit: int) -> bool:
        """Reserve before sending an AI request; concurrent requests cannot exceed quota."""
        async with self.lock:
            record = self.get("review_usage", key, {})
            count = record.get("count", 0) if record.get("period") == period else 0
            if count >= limit:
                return False
            updated = deepcopy(self.data)
            updated["review_usage"][key] = {"period": period, "count": count + 1}
            await self._commit(updated)
            return True

    async def _commit(self, updated):
        task = asyncio.create_task(asyncio.to_thread(self._write, updated))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            self.data = updated
            raise
        self.data = updated

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, self.path)
