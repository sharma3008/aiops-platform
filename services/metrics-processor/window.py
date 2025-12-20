# window.py

from collections import deque

class SlidingWindow:
    def __init__(self, max_size=10):
        self.max_size = max_size
        self.window = deque(maxlen=max_size)

    def add(self, value):
        self.window.append(value)

    def avg(self):
        if len(self.window) == 0:
            return 0
        return sum(self.window) / len(self.window)

    def max(self):
        if len(self.window) == 0:
            return 0
        return max(self.window)

    def min(self):
        if len(self.window) == 0:
            return 0
        return min(self.window)

    def size(self):
        return len(self.window)
