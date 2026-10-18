"""A single budget shared by retries and new messages in one process."""
import time


class SendBudget:
    def __init__(self, max_messages=12, seconds=240):
        self.max_messages = max_messages
        self.deadline = time.monotonic() + seconds
        self.attempts = 0
        self.last_attempt = None

    def reserve(self, gap, required_seconds=110):
        current = time.monotonic()
        delay = max(0, gap - (current - self.last_attempt)) if self.last_attempt is not None else 0
        # Leave time for network calls and two remote checkpoints.
        if self.attempts >= self.max_messages or current + delay + required_seconds >= self.deadline:
            return False
        if delay:
            time.sleep(delay)
        self.attempts += 1
        self.last_attempt = time.monotonic()
        return True

    def mark_attempt(self):
        self.last_attempt = time.monotonic()
