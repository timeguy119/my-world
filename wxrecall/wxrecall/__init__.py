"""wxrecall — a local, read-only archive of your WeChat conversations.

Keeps a copy of every message rendered in your own chat window, so a message
that is withdrawn is still in your archive. It reads the accessibility tree of
the running client; it does not inject code, patch memory, decrypt the client
database, or send anything anywhere.
"""

__version__ = "0.1.0"

from .models import Message, MsgType  # noqa: F401
from .watcher import ChatWatcher, Changes, Recall, diff_snapshots  # noqa: F401
