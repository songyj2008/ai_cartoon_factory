"""Process-local wakeups; durable database state remains authoritative."""
import threading

class ChangeSignal:
    def __init__(self):
        self._condition = threading.Condition()
        self._revision = 0

    def revision(self):
        with self._condition:
            return self._revision

    def publish(self):
        with self._condition:
            self._revision += 1
            self._condition.notify_all()

    def wait(self, revision, timeout):
        with self._condition:
            self._condition.wait_for(lambda: self._revision != revision, timeout)
            return self._revision

web_results = ChangeSignal()
operations = ChangeSignal()
