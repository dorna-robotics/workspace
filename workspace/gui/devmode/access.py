"""Developer mode's view of the unit's account — a safe wrapper around
``gui.account`` (the account client). When that module is missing or
fails, the unit counts as logged out: Developer mode stays closed and
the scene builder's catalog is not filtered for it.
"""


def _account():
    try:
        from gui.account import account
        return account
    except Exception:
        return None


def logged_in() -> bool:
    acc = _account()
    try:
        return bool(acc and acc.logged_in())
    except Exception:
        return False


def allowed_types():
    """None = every component type (an admin); else the set this account
    was given. Logged out → an empty set."""
    acc = _account()
    if not acc:
        return set()
    try:
        return acc.allowed_types()
    except Exception:
        return set()


# Not sold, always there: keep-out walls are part of laying out any bench.
ALWAYS = {"collision_box"}


def filter_types(types, keep=()):
    """``types`` narrowed to what the account may add (the add-component
    catalog only — a part already in a project loads whatever this says).
    Types in ``keep`` (the project's own components) are never hidden."""
    allowed = allowed_types()
    if allowed is None:
        return list(types)
    keep = set(keep) | ALWAYS
    return [t for t in types if t in allowed or t in keep]
