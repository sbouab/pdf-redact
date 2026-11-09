"""Runs the redaction job in a helper process for the window: start it, cancel it, and pass on what it says.

The helper is a fresh Python process ("spawn"), never a thread: PyMuPDF must not be used from two threads at
once, and the window keeps drawing pages while a job runs. It talks back through a one-way pipe that the
window checks every few milliseconds, so the window never waits on it.
"""
import multiprocessing
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from .job import JobOutcome, child_main

STOPPED = "the redaction stopped unexpectedly. Try again; if it happens again, the PDF may be damaged."


class JobRunner(QObject):
    """Runs one job at a time. Emits progress(stage, done, total) while it runs, then finished(generation,
    outcome), unless the job is cancelled first (by cancel(), or by starting another one)."""

    progress = Signal(str, int, int)
    finished = Signal(int, object)

    def __init__(self, parent=None, poll_ms=40):
        super().__init__(parent)
        self._context = multiprocessing.get_context("spawn")
        self._timer = QTimer(self)
        self._timer.setInterval(poll_ms)
        self._timer.timeout.connect(self._poll)
        self._process = self._pipe = self._request = None
        self._generation = None

    @property
    def generation(self):
        """The generation of the running job, None if no job runs."""
        return self._generation

    def is_running(self):
        return self._process is not None

    def start(self, generation, request):
        """Run `request` (a JobRequest) for edit `generation`, cancelling the job that runs, if any."""
        self.cancel()
        reader, writer = self._context.Pipe(duplex=False)
        process = self._context.Process(target=child_main, args=(writer, request), daemon=True,
                                        name="pdf-redact job")
        process.start()
        writer.close()                 # the helper now holds the only writing end: reading ends if it dies
        self._process, self._pipe, self._request, self._generation = process, reader, request, generation
        self._timer.start()

    def cancel(self):
        """Stop the running job, if any, and delete what it may have written. Nothing is emitted for it."""
        if self._process is not None:
            self._end(stop=True)

    def _poll(self):
        # Whether the helper has ended is read before the pipe, so that nothing it sent before ending is missed.
        # The pipe alone can't tell: on Windows, a helper stopped while starting never takes its end of the pipe
        # from the copy multiprocessing keeps for it, so the pipe never reports that it closed.
        ended = not self._process.is_alive()
        try:
            while self._pipe.poll():
                message = self._pipe.recv()
                if message[0] == "progress":
                    self.progress.emit(*message[1:])
                else:
                    generation = self._generation
                    self._end(stop=False)
                    self.finished.emit(generation, message[1])
                    return
        except (EOFError, OSError):
            ended = True
        if ended:                        # the helper ended without sending its outcome
            generation = self._generation
            self._end(stop=True)
            self.finished.emit(generation, JobOutcome(error=STOPPED))

    def _end(self, stop):
        """Forget the job: stop listening, wait for the helper to end (stopping it first if `stop`), and when it
        was stopped, delete its output."""
        process, request = self._process, self._request
        self._timer.stop()
        self._pipe.close()
        self._process = self._pipe = self._request = self._generation = None
        if stop and process.is_alive():
            process.terminate()
        process.join(3)
        if process.is_alive():
            process.kill()
            process.join(3)
        if not process.is_alive():
            process.close()
        if stop:
            try:
                Path(request.out_path).unlink(missing_ok=True)
            except OSError:
                pass                   # still busy: the workspace is deleted when the app closes anyway
