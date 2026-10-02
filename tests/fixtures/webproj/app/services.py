"""Service objects used by the API."""


class Service:
    """Does the real work for a request."""

    def run(self):
        """Run the service once."""
        return self.helper()

    def helper(self):
        return 1


class Other:
    def run(self):
        return 2
