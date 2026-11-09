"""Running the redaction job in a helper process for the window. Run from the repository folder:  uv run pytest"""
import multiprocessing
from pathlib import Path

from helpers import LIST, let_events_run, sample, wait_until
from pdfredact.gui.runner import JobRunner
from pdfredact.gui.session import Session


def request_for(tmp_path, out_name="out.pdf"):
    path = sample(tmp_path, "letter_fr.pdf")
    s = Session(str(path), path.read_bytes())
    s.add_items(LIST)
    request = s.job_request(str(tmp_path / out_name))
    s.close()
    return request


def listen(runner):
    progress, finished = [], []
    runner.progress.connect(lambda *a: progress.append(a))
    runner.finished.connect(lambda generation, outcome: finished.append((generation, outcome)))
    return progress, finished


def test_runner_finishes_with_a_result(qapp, tmp_path):
    runner = JobRunner()
    progress, finished = listen(runner)
    runner.start(7, request_for(tmp_path))
    assert runner.is_running() and runner.generation == 7
    wait_until(lambda: finished)
    generation, outcome = finished[0]
    assert generation == 7 and outcome.error == "" and outcome.result.fails == []
    assert progress[0] == ("redact", 1, 2) and progress[-1] == ("check", 2, 2)
    assert not runner.is_running() and runner.generation is None
    assert (tmp_path / "out.pdf").exists()                   # the window reads it, then deletes it


def test_cancel_stops_the_helper_and_removes_output(qapp, tmp_path):
    runner = JobRunner()
    progress, finished = listen(runner)
    request = request_for(tmp_path)
    runner.start(1, request)
    runner.cancel()
    assert not runner.is_running() and multiprocessing.active_children() == []
    let_events_run(1.0)
    assert finished == [] and not Path(request.out_path).exists()


def test_starting_again_cancels_the_previous_job(qapp, tmp_path):
    runner = JobRunner()
    progress, finished = listen(runner)
    first, second = request_for(tmp_path / "a"), request_for(tmp_path / "b")
    runner.start(1, first)
    runner.start(2, second)
    wait_until(lambda: finished)
    let_events_run(0.5)
    assert [g for g, _ in finished] == [2] and not Path(first.out_path).exists()


def test_a_helper_that_dies_is_reported(qapp, tmp_path):
    runner = JobRunner()
    progress, finished = listen(runner)
    request = request_for(tmp_path)
    runner.start(3, request)
    [helper] = multiprocessing.active_children()
    helper.kill()
    wait_until(lambda: finished)
    generation, outcome = finished[0]
    assert generation == 3 and outcome.result is None and "stopped unexpectedly" in outcome.error
    assert not runner.is_running() and not Path(request.out_path).exists()
