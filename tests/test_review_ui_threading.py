"""Regression test for a real bug: the confidence filter crashed with
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that
same thread.

Root cause: review_ui.py cached its DB connection across Streamlit reruns via
@st.cache_resource. That decorator caches globally even outside a live app (confirmed
directly — no Streamlit server or session needed to reproduce this), so a connection
created on whichever thread handled the first script run got reused on whichever
thread handled a later rerun (e.g. after clicking the filter dropdown), which sqlite3
never permits regardless of caching.

Fixed by not caching the connection at all — review_ui.get_conn() now opens a fresh
one on every call, so it's always created and used on the same thread by construction.
Deliberately not check_same_thread=False, which would silence the error without making
concurrent access to one shared connection actually safe.

This calls the real review_ui.get_conn(), the function main() actually uses to get its
connection for a script run, from two different threads — the same way two different
Streamlit reruns can land on different threads.
"""

import threading

from src.review_ui import get_conn
from src.store import db


def test_get_conn_from_two_different_threads_are_each_usable_in_their_own_thread():
    """Simulates two Streamlit reruns landing on different threads, the way they
    really can. Threads A and B must overlap in time — if A is fully joined before
    B starts, the OS can recycle A's thread id for B, which would make a *cached*
    connection appear to pass this check for the wrong reason (B "looks like" the
    thread that created it, because it received a and the OS handed the same id
    back). Keeping A alive on a barrier until B is done rules that out.
    """
    results = {}
    errors = []
    a_started = threading.Event()
    release_a = threading.Event()

    def run_on_thread_a():
        try:
            conn = get_conn()
            conn.execute("SELECT 1").fetchone()
            results["a"] = True
        except Exception as exc:  # noqa: BLE001 — capturing to assert on below
            errors.append(("a", exc))
        finally:
            a_started.set()
            release_a.wait(timeout=5)

    def run_on_thread_b():
        try:
            conn = get_conn()
            conn.execute("SELECT 1").fetchone()
            results["b"] = True
        except Exception as exc:  # noqa: BLE001
            errors.append(("b", exc))

    thread_a = threading.Thread(target=run_on_thread_a)
    thread_a.start()
    a_started.wait(timeout=5)

    thread_b = threading.Thread(target=run_on_thread_b)
    thread_b.start()
    thread_b.join()

    release_a.set()
    thread_a.join()

    assert not errors, errors
    assert results == {"a": True, "b": True}


def test_a_connection_reused_across_threads_reproduces_the_original_crash():
    """Documents the actual failure mode this project hit: a single connection
    object, created on one thread, used from another. This must keep raising
    ProgrammingError forever — that's the reason review_ui.get_conn() must never be
    cached across reruns, not a bug to eventually "fix" by suppressing it.

    Thread A must still be alive when thread B uses the connection — OS thread ids
    can be recycled once a thread exits, and joining A before starting B let a
    first draft of this test silently pass for the wrong reason (B got A's now-freed
    thread id back, so sqlite3's check_same_thread saw a "match").
    """
    conn_from_thread_a = {}
    created = threading.Event()
    release_a = threading.Event()

    def hold_open_on_thread_a():
        conn_from_thread_a["conn"] = db.connect()
        created.set()
        release_a.wait(timeout=5)

    thread_a = threading.Thread(target=hold_open_on_thread_a)
    thread_a.start()
    created.wait(timeout=5)

    errors = []

    def use_from_thread_b():
        try:
            conn_from_thread_a["conn"].execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    thread_b = threading.Thread(target=use_from_thread_b)
    thread_b.start()
    thread_b.join()
    release_a.set()
    thread_a.join()

    assert len(errors) == 1
    assert "SQLite objects created in a thread" in str(errors[0])
