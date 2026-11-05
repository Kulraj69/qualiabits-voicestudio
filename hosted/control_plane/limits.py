"""Limits that hold: a monthly character ceiling and a per-key request rate.

The plane meters what a customer did. Metering is a record; it is not a brake, and
the difference matters the moment one customer hands a key to a script. This
module is the brake, and it is deliberately small and boring:

**Two limits, two meanings.**

* ``character_ceiling`` — characters this workspace may ask the engine to speak
  in one UTC calendar month. ``0`` means no ceiling, which is the default: a
  limit nobody asked for is not a policy, it is a surprise. The dashboard sets
  it, and the operator may set a house default for new workspaces.
* ``requests_per_minute`` — requests one *API key* may make in a fixed
  one-minute window. Fixed window, not sliding: a client that waits out the
  ``Retry-After`` we send is never refused, but two bursts either side of a
  minute boundary can together total twice the limit. That is stated in the docs
  rather than papered over, because the alternative (a sliding window with a
  per-request timestamp row per key) is a lot of rows to buy a fairness nobody
  has asked for yet.

**Why a reservation and not just a sum.** The ledger is the truth about what was
spent, but reading it and then acting on it is not atomic: two concurrent
requests can both read "12,000 of 12,000 remaining" and both proceed. So a
request *reserves* its characters with a single conditional UPDATE

    UPDATE quota_periods SET reserved = reserved + n
     WHERE id = ? AND committed + reserved + n <= ceiling

and believes only a rowcount of 1 — the same compare-and-swap idiom
``jobs.py`` already uses to claim work, and for the same reason: SQLite has no
``SELECT ... FOR UPDATE``. The reservation is released when the request settles
(its usage event lands in the ledger in the same transaction) or when it is
refused, cancelled or failed, so the two never double-count.

**Both halves live in the counter row, and that is not decoration.** The first
version of this module compared the new reservation against the *ledger* it had
just read, and the thread-race test caught what that costs: a settle commit
between the read and the UPDATE moves spend from ``reserved`` to the ledger, so
the reader's stale ledger figure is too small and it lets the request through.
The gate is now one statement comparing two columns of one row, so the figure it
compares against cannot move underneath it. ``committed_characters`` stays a
projection — the ledger is still the record — but it is seeded from the ledger
when the row is created and advanced only beside the usage event it mirrors.

**What a refusal costs.** Nothing. The text never reached the engine, so the
event is written with zero characters; what it does cost is its rate slot,
because the limiter counts attempts. Both refusal codes say which is which:
``402`` for the ceiling — retrying now cannot help, and the body carries the
moment the period resets — and ``429`` for the rate, with a real
``Retry-After``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import ApiKey, QuotaPeriod, RateWindow, UsageEvent, Workspace, utcnow

#: Status codes, named so a caller reads intent rather than a number.
CEILING_STATUS = 402  # Payment Required: retrying does not help until the period resets
RATE_STATUS = 429  # Too Many Requests: retrying after Retry-After does help

CEILING_ERROR = "character_ceiling_exceeded"
RATE_ERROR = "request_rate_exceeded"

WINDOW_SECONDS = 60


def utc_month_start(moment: datetime) -> datetime:
    """The first instant of ``moment``'s UTC calendar month."""
    return moment.astimezone(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )


def utc_next_month_start(moment: datetime) -> datetime:
    start = utc_month_start(moment)
    return (start.replace(day=28) + timedelta(days=4)).replace(day=1)


def utc_minute_start(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(second=0, microsecond=0)


@dataclass(frozen=True)
class Refusal:
    """A decided refusal, ready to be raised by a route or printed by a test.

    ``retry_after`` is seconds a client should wait, and it is only set where
    waiting actually helps: a rate refusal. A ceiling refusal carries the reset
    moment instead, because telling a client to retry in 60 seconds against a
    monthly ceiling would be a worse answer than the truth.
    """

    status_code: int
    error: str
    detail: str
    limit: int
    used: int
    remaining: int
    retry_after: int | None = None
    resets_at: str | None = None

    def http_headers(self) -> dict[str, str]:
        """What a refusing response carries besides its body.

        ``Retry-After`` only where waiting helps (the rate), and the limit
        numbers on every refusal so a client can log why it was told no without
        having to parse prose. There is deliberately no ``Retry-After`` on a
        ceiling refusal: it would be a number that only becomes true at the
        start of next month.
        """
        headers = {
            "X-Qualiabits-Limit": str(self.limit),
            "X-Qualiabits-Limit-Used": str(self.used),
            "X-Qualiabits-Limit-Remaining": str(self.remaining),
        }
        if self.retry_after is not None:
            headers["Retry-After"] = str(self.retry_after)
        return headers

    def as_dict(self) -> dict:
        body = {
            "error": self.error,
            "detail": self.detail,
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
        }
        if self.retry_after is not None:
            body["retry_after"] = self.retry_after
        if self.resets_at is not None:
            body["resets_at"] = self.resets_at
        return body


def _iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(timezone.utc).isoformat()


# ── the ceiling ───────────────────────────────────────────────────────────────


def _period_row(session: Session, workspace_id: int, start: datetime) -> QuotaPeriod:
    """The counter row for one workspace and one period, created if absent.

    The insert races with itself: two requests in the same second on a fresh
    month both find no row. One wins and the other gets an IntegrityError, which
    a savepoint turns into a rollback of *that statement only* — the surrounding
    request transaction keeps going and re-reads the winner's row.

    A new row is seeded with what the ledger already says for this period. That
    matters for a workspace whose ceiling is set for the first time in the middle
    of a month, and for a database upgraded from a slice that had no counters:
    without the seed, its earlier spend would be invisible to the gate and the
    ceiling would be that much larger than the owner asked for.
    """
    def find() -> QuotaPeriod | None:
        return session.execute(
            select(QuotaPeriod).where(
                QuotaPeriod.workspace_id == workspace_id,
                QuotaPeriod.period_start == start,
            )
        ).scalar_one_or_none()

    row = find()
    if row is not None:
        return row
    seeded = settled_characters(session, workspace_id, start)
    try:
        with session.begin_nested():
            row = QuotaPeriod(
                workspace_id=workspace_id,
                period_start=start,
                committed_characters=seeded,
                reserved_characters=0,
            )
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        row = find()
        if row is None:  # pragma: no cover - the row vanished between two reads
            raise
        return row


def settled_characters(session: Session, workspace_id: int, start: datetime) -> int:
    """Characters this workspace has actually been charged in this period.

    Read straight off the ledger, which is the only place a character is
    counted: a request that was refused before the engine saw it is a row with
    zero characters, so it does not appear here as spend.
    """
    return int(
        session.execute(
            select(func.coalesce(func.sum(UsageEvent.characters), 0)).where(
                UsageEvent.workspace_id == workspace_id,
                UsageEvent.created_at >= start,
            )
        ).scalar_one()
        or 0
    )


def reserved_characters(session: Session, workspace_id: int, start: datetime) -> int:
    row = session.execute(
        select(QuotaPeriod).where(
            QuotaPeriod.workspace_id == workspace_id,
            QuotaPeriod.period_start == start,
        )
    ).scalar_one_or_none()
    return 0 if row is None else int(row.reserved_characters or 0)


def used_characters(session: Session, workspace_id: int, start: datetime) -> int:
    """Spend the ledger records, plus the reservations still standing.

    Read from the ledger rather than from the counter row, because the ledger is
    the record and a number shown to a customer should not be a projection of one.
    :func:`counter_drift` exists so a test can insist the two agree; if they ever
    disagree, the row is what is wrong, not the ledger.
    """
    return settled_characters(session, workspace_id, start) + reserved_characters(
        session, workspace_id, start
    )


def counter_drift(session: Session, workspace_id: int, start: datetime) -> int:
    """``counter.committed - ledger`` for this period. Zero is the invariant."""
    row = session.execute(
        select(QuotaPeriod).where(
            QuotaPeriod.workspace_id == workspace_id,
            QuotaPeriod.period_start == start,
        )
    ).scalar_one_or_none()
    committed = 0 if row is None else int(row.committed_characters or 0)
    return committed - settled_characters(session, workspace_id, start)


def reserve(
    session: Session,
    *,
    workspace: Workspace,
    characters: int,
    now: datetime | None = None,
) -> Refusal | None:
    """Try to reserve ``characters`` against this month. ``None`` means allowed.

    One conditional UPDATE decides it, so this is safe under concurrency without
    holding a lock the engine's slow call would be inside.
    """
    moment = now or utcnow()
    ceiling = int(workspace.character_ceiling or 0)
    if ceiling <= 0 or characters <= 0:
        return None
    start = utc_month_start(moment)
    row = _period_row(session, workspace.id, start)
    outcome = session.execute(
        update(QuotaPeriod)
        .where(
            QuotaPeriod.id == row.id,
            QuotaPeriod.committed_characters
            + QuotaPeriod.reserved_characters
            + characters
            <= ceiling,
        )
        .values(reserved_characters=QuotaPeriod.reserved_characters + characters)
    )
    if outcome.rowcount == 1:
        return None
    live = used_characters(session, workspace.id, start)
    return Refusal(
        status_code=CEILING_STATUS,
        error=CEILING_ERROR,
        detail=(
            f"This request needs {characters} characters and this workspace has "
            f"{max(ceiling - live, 0)} left in the ceiling of {ceiling} for "
            f"{start.date().isoformat()}. The ceiling resets on "
            f"{utc_next_month_start(moment).date().isoformat()} (UTC); the "
            "dashboard's Usage page shows it, and a workspace can change its own."
        ),
        limit=ceiling,
        used=live,
        remaining=max(ceiling - live, 0),
        resets_at=_iso(utc_next_month_start(moment)),
    )


def settle(
    session: Session,
    *,
    workspace_id: int,
    characters: int,
    now: datetime | None = None,
) -> None:
    """Give a reservation back because it is about to become real spend.

    Called in the same transaction as the usage event that charges those
    characters, so the ledger, the counter and the reservation move together and a
    crash between them is impossible. ``max(..., 0)`` because a workspace whose
    ceiling was lowered, or whose reservation was already released by a
    cancellation, must not end up with a negative reservation.

    The projection is advanced here and nowhere else. A path that wrote characters
    to the ledger without calling this one would make the counter read low and the
    ceiling generous, which is why the tests assert the two agree after every kind
    of request rather than trusting the symmetry.
    """
    if characters <= 0:
        return
    start = utc_month_start(now or utcnow())
    session.execute(
        update(QuotaPeriod)
        .where(QuotaPeriod.workspace_id == workspace_id, QuotaPeriod.period_start == start)
        .values(
            reserved_characters=func.max(QuotaPeriod.reserved_characters - characters, 0),
            committed_characters=QuotaPeriod.committed_characters + characters,
        )
    )


def release(
    session: Session,
    *,
    workspace_id: int,
    characters: int,
    now: datetime | None = None,
) -> None:
    """Give a reservation back because nothing was spent.

    The difference from :func:`settle` is the whole reason both exist: settling
    means the characters *were* asked for, so they move from reserved to
    committed; releasing means they never were (a queued job the customer
    cancelled before any worker claimed it), so they leave the row entirely. Using
    the first where the second belongs charges the ceiling for work nobody did —
    which is what a test in this slice caught the first version doing.
    """
    if characters <= 0:
        return
    start = utc_month_start(now or utcnow())
    session.execute(
        update(QuotaPeriod)
        .where(QuotaPeriod.workspace_id == workspace_id, QuotaPeriod.period_start == start)
        .values(
            reserved_characters=func.max(QuotaPeriod.reserved_characters - characters, 0)
        )
    )


# ── the rate ──────────────────────────────────────────────────────────────────


def _rate_row(session: Session, api_key_id: int, start: datetime) -> RateWindow:
    def find() -> RateWindow | None:
        return session.execute(
            select(RateWindow).where(
                RateWindow.api_key_id == api_key_id, RateWindow.window_start == start
            )
        ).scalar_one_or_none()

    row = find()
    if row is not None:
        return row
    try:
        with session.begin_nested():
            row = RateWindow(api_key_id=api_key_id, window_start=start, count=0)
            session.add(row)
            session.flush()
        return row
    except IntegrityError:
        row = find()
        if row is None:  # pragma: no cover
            raise
        return row


def check_rate(
    session: Session,
    *,
    workspace: Workspace,
    api_key: ApiKey | None,
    now: datetime | None = None,
) -> Refusal | None:
    """Count this request against the key's window. ``None`` means allowed.

    ``api_key=None`` is a dashboard session rather than a key, and the rate is a
    property of a key: a person clicking in the dashboard is not a script, and
    inventing a synthetic key to rate-limit them would put a number in the meter
    that belongs to no key. A session caller is still bound by the ceiling.
    """
    moment = now or utcnow()
    limit = int(workspace.requests_per_minute or 0)
    if limit <= 0 or api_key is None:
        return None
    start = utc_minute_start(moment)
    row = _rate_row(session, api_key.id, start)
    outcome = session.execute(
        update(RateWindow)
        .where(RateWindow.id == row.id, RateWindow.count < limit)
        .values(count=RateWindow.count + 1)
    )
    if outcome.rowcount == 1:
        return None
    ends = start + timedelta(seconds=WINDOW_SECONDS)
    retry_after = max(int((ends - moment).total_seconds()), 1)
    live = int(
        session.execute(
            select(RateWindow.count).where(RateWindow.id == row.id)
        ).scalar_one()
    )
    return Refusal(
        status_code=RATE_STATUS,
        error=RATE_ERROR,
        detail=(
            f"This key has made {live} requests in the minute beginning "
            f"{start.isoformat()}, and the workspace allows {limit}. The window "
            "is fixed, not sliding: waiting out Retry-After always clears it."
        ),
        limit=limit,
        used=live,
        remaining=max(limit - live, 0),
        retry_after=retry_after,
    )


# ── what a route asks for, and what it tells the customer ─────────────────────


def guard(
    session: Session,
    *,
    workspace: Workspace,
    api_key: ApiKey | None,
    characters: int,
    now: datetime | None = None,
) -> Refusal | None:
    """The one call a metered route makes before it touches the engine.

    Rate first, then the ceiling: the rate check is one small conditional UPDATE
    and the cheapest way to shed a storm, and a request refused for its rate
    should not have moved the month's spend.
    """
    refusal = check_rate(session, workspace=workspace, api_key=api_key, now=now)
    if refusal is not None:
        return refusal
    return reserve(session, workspace=workspace, characters=characters, now=now)


def state(session: Session, workspace: Workspace, *, now: datetime | None = None) -> dict:
    """The limits as a customer (or an alarm) reads them."""
    moment = now or utcnow()
    start = utc_month_start(moment)
    ceiling = int(workspace.character_ceiling or 0)
    used = used_characters(session, workspace.id, start)
    return {
        "character_ceiling": ceiling or None,
        "characters_used": used,
        "characters_remaining": None if ceiling <= 0 else max(ceiling - used, 0),
        "characters_settled": settled_characters(session, workspace.id, start),
        "characters_reserved": reserved_characters(session, workspace.id, start),
        "counter_committed": int(
            (
                session.execute(
                    select(QuotaPeriod.committed_characters).where(
                        QuotaPeriod.workspace_id == workspace.id,
                        QuotaPeriod.period_start == start,
                    )
                ).scalar_one_or_none()
            )
            or 0
        ),
        "period_start": _iso(start),
        "period_resets_at": _iso(utc_next_month_start(moment)),
        "requests_per_minute": int(workspace.requests_per_minute or 0) or None,
        "rate_window_seconds": WINDOW_SECONDS,
        "rate_window_kind": "fixed",
        "ceiling_note": (
            "Characters you asked the engine to speak this UTC calendar month. "
            "Transcriptions count here as zero characters: they add none to the "
            "ceiling and are bounded by the rate instead."
        ),
        "rate_note": (
            "Requests per API key per fixed one-minute window. Refused requests "
            "still spend their slot; a refused request costs no characters."
        ),
    }