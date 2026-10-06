import threading
import time

from pixel_rpg_studio.core.errors import StudioError
from pixel_rpg_studio.core.jobs import JobQueue, JobState


def test_jobs_run_sequentially():
    q = JobQueue()
    order = []
    active = []

    def make(n):
        def f(ctx):
            active.append(n)
            assert len(active) == 1
            time.sleep(0.05)
            order.append(n)
            ctx.progress(1.0, "done")
            active.remove(n)
            return n

        return f

    jobs = [q.submit(f"j{n}", make(n)) for n in range(3)]
    for j in jobs:
        assert j.wait(5)
    assert order == [0, 1, 2]
    assert all(j.state == JobState.DONE for j in jobs)
    assert jobs[2].result == 2
    q.shutdown()


def test_failure_is_reported_not_raised():
    q = JobQueue()
    j = q.submit("bad", lambda ctx: (_ for _ in ()).throw(StudioError("nope", hint="fix it")))
    j.wait(5)
    assert j.state == JobState.FAILED
    assert j.error.message == "nope" and j.error.hint == "fix it"
    q.shutdown()


def test_cancel():
    q = JobQueue()
    started = threading.Event()

    def long(ctx):
        started.set()
        for _ in range(200):
            ctx.check_cancelled()
            time.sleep(0.01)

    j = q.submit("long", long)
    started.wait(2)
    q.cancel(j.id)
    j.wait(5)
    assert j.state == JobState.CANCELLED
    q.shutdown()


def test_sub_progress_mapping():
    q = JobQueue()
    seen = []
    q.add_listener(lambda job: seen.append(job.progress))

    def f(ctx):
        sub = ctx.sub(0.5, 1.0)
        sub.progress(0.5, "half of second half")

    j = q.submit("p", f)
    j.wait(5)
    assert 0.75 in seen
    q.shutdown()
