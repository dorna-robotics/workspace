"""This unit's Dorna account (device-code login). Other modules use:

    from gui.account import account
    account.logged_in()      # a token is stored (no network)
    account.status()         # {logged_in, email, usage: {percent}, ...}, cached
    account.allowed_types()  # None = every component type, else a set

    from gui.account import login_required   # 401 when offline
"""
from .client import account  # noqa: F401
from .handlers import login_required, routes  # noqa: F401
