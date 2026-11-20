"""Persisted state machines for CollectionJob and artifact status."""

from __future__ import annotations

from .entities import CollectionJob
from .enums import ArtifactStatus, JobState
from .errors import InvalidTransition
from .ids import utcnow

J = JobState
JOB_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    J.CREATED: frozenset({J.NOTIFIED, J.IN_PROGRESS, J.OVERDUE}),
    J.NOTIFIED: frozenset({J.IN_PROGRESS, J.OVERDUE, J.SUBMITTED}),
    J.IN_PROGRESS: frozenset({J.SUBMITTED, J.OVERDUE}),
    J.OVERDUE: frozenset({J.ESCALATED, J.IN_PROGRESS, J.SUBMITTED}),
    J.ESCALATED: frozenset({J.IN_PROGRESS, J.SUBMITTED}),
    J.SUBMITTED: frozenset({J.IN_PROGRESS, J.CLOSED}),
    J.CLOSED: frozenset(),
}

ACTIVE_JOB_STATES = frozenset({J.CREATED, J.NOTIFIED, J.IN_PROGRESS, J.OVERDUE, J.ESCALATED})


def can_transition(current: JobState, target: JobState) -> bool:
    return target in JOB_TRANSITIONS[current]


def transition_job(job: CollectionJob, target: JobState) -> CollectionJob:
    if job.state == target:
        return job
    if not can_transition(job.state, target):
        raise InvalidTransition(
            f"job {job.job_id}: {job.state} -> {target} not allowed",
            current=str(job.state),
            target=str(target),
        )
    job.state = target
    job.state_changed_at = utcnow()
    return job


A = ArtifactStatus
ARTIFACT_TRANSITIONS: dict[ArtifactStatus | None, frozenset[ArtifactStatus]] = {
    None: frozenset({A.DRAFT, A.SUBMITTED}),
    A.DRAFT: frozenset({A.DRAFT, A.SUBMITTED}),
    A.SUBMITTED: frozenset({A.VALIDATED, A.DRAFT, A.SUBMITTED}),
    A.VALIDATED: frozenset({A.APPROVED, A.DRAFT, A.SUBMITTED}),
    A.APPROVED: frozenset({A.DRAFT, A.SUBMITTED}),
}


def check_artifact_transition(previous: ArtifactStatus | None, new: ArtifactStatus) -> None:
    if new not in ARTIFACT_TRANSITIONS[previous]:
        raise InvalidTransition(f"artifact status {previous} -> {new} not allowed")
