"""密封判定结论史（只追加、不可变、可复查）。

结论史与审计流水分开成段：它是一份独立的 JSONL 追加流水，启动时整段回放进
内存。没有任何修改或删除入口——后续采样、再次压紧只能追加新记录，不能改写
已经落档的旧结论。查询支持按炉门复查与取该炉门最近结论（控制台状态页用）。
"""

from __future__ import annotations

import threading

from ..store.journal import JsonlJournal
from .record import SealRecord


class SealHistory:
    """逐门密封结论的追加式档案。"""

    def __init__(self, journal: JsonlJournal) -> None:
        self._journal = journal
        self._records: list[SealRecord] = []
        self._lock = threading.RLock()
        self._restore()

    def _restore(self) -> None:
        for raw in self._journal.read_all():
            self._records.append(SealRecord.from_dict(raw))

    @property
    def journal(self) -> JsonlJournal:
        return self._journal

    def append(self, record: SealRecord) -> dict:
        """追加一条结论（内存 + 落盘），返回其字典形式。"""

        with self._lock:
            self._records.append(record)
            self._journal.append(record.as_dict())
        return record.as_dict()

    def entries(self, oven: str | None = None, limit: int | None = None) -> list[dict]:
        """按时间升序返回结论；可按炉门过滤、截断最近 ``limit`` 条。"""

        with self._lock:
            selected = [item for item in self._records if oven is None or item.oven == oven]
            if limit is not None:
                selected = selected[-max(1, limit) :]
            return [item.as_dict() for item in selected]

    def latest_for(self, oven: str) -> dict | None:
        """返回某炉门最近一条结论，没有则为 None。"""

        with self._lock:
            for item in reversed(self._records):
                if item.oven == oven:
                    return item.as_dict()
        return None

    def latest_by_oven(self) -> dict[str, dict]:
        """返回每个炉门最近一条结论，供控制台总览。"""

        latest: dict[str, dict] = {}
        with self._lock:
            for item in self._records:
                latest[item.oven] = item.as_dict()
        return latest

    def count(self) -> int:
        """返回累计结论条数。"""

        with self._lock:
            return len(self._records)
