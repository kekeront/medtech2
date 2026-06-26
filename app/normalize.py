"""Match a raw service name to the reference catalogue (TZ 4.3).

Exact name / synonym hits score 1.0; otherwise RapidFuzz token-set similarity.
Score >= MATCH_AUTO_THRESHOLD -> auto-link; lower -> left unmatched for the review queue.
The catalogue is loaded once into an in-memory index and reused across a batch.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import MATCH_AUTO_THRESHOLD, MATCH_SUGGEST_THRESHOLD
from .models import Service


def _key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


@dataclass
class MatchResult:
    service_id: uuid.UUID | None
    method: str | None  # exact / synonym / fuzzy / None
    confidence: float | None
    suggestion_id: uuid.UUID | None = (
        None  # below auto threshold but worth showing operators
    )


class ServiceMatcher:
    def __init__(self, session: Session):
        self.exact: dict[str, uuid.UUID] = {}
        self.choices: dict[str, uuid.UUID] = {}  # normalized name/synonym -> service_id
        for svc in session.scalars(select(Service).where(Service.is_active.is_(True))):
            self.exact[_key(svc.service_name)] = svc.service_id
            self.choices[_key(svc.service_name)] = svc.service_id
            for syn in svc.synonyms or []:
                self.choices[_key(syn)] = svc.service_id

    @property
    def empty(self) -> bool:
        return not self.choices

    def match(self, raw_name: str) -> MatchResult:
        if self.empty:
            return MatchResult(None, None, None)
        k = _key(raw_name)
        if k in self.exact:
            return MatchResult(self.exact[k], "exact", 1.0)
        if k in self.choices:
            return MatchResult(self.choices[k], "synonym", 1.0)

        best = process.extractOne(k, self.choices.keys(), scorer=fuzz.token_set_ratio)
        if not best:
            return MatchResult(None, None, None)
        label, score, _ = best
        conf = score / 100.0
        sid = self.choices[label]
        if conf >= MATCH_AUTO_THRESHOLD:
            return MatchResult(sid, "fuzzy", round(conf, 4))
        if conf >= MATCH_SUGGEST_THRESHOLD:
            return MatchResult(None, None, round(conf, 4), suggestion_id=sid)
        return MatchResult(None, None, round(conf, 4))
