class ApplicationHandlerStop(Exception): pass
class _H:
    def __init__(self, *a, **k): self.a, self.k = a, k
class CallbackQueryHandler(_H): pass
class CommandHandler(_H): pass
class MessageHandler(_H): pass
class TypeHandler(_H): pass
class ConversationHandler(_H):
    END, TIMEOUT = -1, -2
class Defaults(_H): pass
class ContextTypes:
    DEFAULT_TYPE = object
class _Flt:
    def __and__(self, o): return self
    def __invert__(self): return self
class filters:
    TEXT = COMMAND = _Flt()
    class Document:
        ALL = _Flt()
class _JQ:
    def __init__(self): self.jobs = []
    def run_repeating(self, cb, interval, first=0, job_kwargs=None): self.jobs.append(("rep", cb.__name__, interval, job_kwargs))
    def run_daily(self, cb, time, job_kwargs=None): self.jobs.append(("daily", cb.__name__, time, job_kwargs))
class Application:
    def __init__(self):
        self.handlers, self.bot_data, self.job_queue = [], {}, _JQ()
        self.bot, self.errors = object(), []
    def add_handler(self, h, group=0): self.handlers.append((group, h))
    def add_error_handler(self, f): self.errors.append(f)
class ApplicationBuilder:
    def token(self, t): self._t = t; return self
    def defaults(self, d): return self
    def post_init(self, f): return self
    def connect_timeout(self, t): return self
    def read_timeout(self, t): return self
    def write_timeout(self, t): return self
    def connection_pool_size(self, n): return self
    def pool_timeout(self, t): return self
    def build(self): return Application()
