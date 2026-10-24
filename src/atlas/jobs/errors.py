"""What can go wrong with routines and delegations."""


class JobsError(Exception):
    """A routine cannot be made, changed or run; the message says how to fix it."""


class ScheduleError(JobsError):
    """A schedule Atlas cannot read."""


class RoutineNotFoundError(JobsError):
    """There is no routine with that id or name."""


class DelegationError(JobsError):
    """A delegation that cannot start as asked; the message is for the model."""
