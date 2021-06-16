class SaaSEvent:
    """Signalling events coming from the SaaS worker.

    The purpose is to communicate with the thread that writes to stdout.
    """

    @property
    def name(self) -> str:
        return self.__class__.__name__.upper()


class Success(SaaSEvent):
    pass


class Error(SaaSEvent):
    pass


class Timeout(SaaSEvent):
    pass
