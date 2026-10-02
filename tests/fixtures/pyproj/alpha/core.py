"""Core loading logic."""
import json
import os

DEFAULT_PATH = "data.json"


class Base:
    """Base class."""

    def describe(self):
        return self.__class__.__name__


class Loader(Base):
    """Loads records from disk."""

    def __init__(self, path=DEFAULT_PATH):
        self.path = os.path.abspath(path)

    def load(self):
        """Read the JSON file."""
        with open(self.path) as fh:
            return json.load(fh)


def load(path=DEFAULT_PATH):
    """Convenience wrapper around Loader."""
    return Loader(path).load()
