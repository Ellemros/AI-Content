"""Scheduler stub. Phase 1 does not run background schedules."""


class SchedulerStub:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def register(self, name: str, description: str) -> None:
        self.jobs.append({"name": name, "description": description, "enabled": False})


scheduler = SchedulerStub()


def register_future_jobs() -> None:
    if scheduler.jobs:
        return
    scheduler.register("topic-research", "Later phase: refresh research references.")
    scheduler.register("analytics-pull", "Later phase: pull platform metrics.")
