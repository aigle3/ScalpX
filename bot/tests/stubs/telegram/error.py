class TelegramError(Exception): pass
class BadRequest(TelegramError): pass
class Forbidden(TelegramError): pass
class Conflict(TelegramError): pass
class RetryAfter(TelegramError):
    def __init__(self, retry_after):
        super().__init__(f"Flood control exceeded. Retry in {retry_after} seconds")
        self.retry_after = retry_after
