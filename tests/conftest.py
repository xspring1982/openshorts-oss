import os
import sys

# Make the repo root importable so tests can import the app modules directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests always run the app in self-host (BYOK) mode. app.py freezes
# BILLING_ENABLED at import time and load_dotenv never overrides an existing
# variable, so this must be set here — before any test module imports app — or
# the suite's behavior would depend on the developer's personal .env.
os.environ["BILLING_ENABLED"] = "0"

# The shared YouTube-account state (yt_account) lives in files: give the suite
# its own directory and no pacing delay, so a test never waits on, or leaves a
# cooldown for, another test or the developer's own output/ directory.
import tempfile  # noqa: E402

os.environ["YT_ACCOUNT_STATE_DIR"] = tempfile.mkdtemp(prefix="yt-account-tests-")
os.environ.setdefault("YT_ACCOUNT_MIN_INTERVAL", "0")


# Probes run in a thread in the suite: tests replace metering.probe_url_minutes,
# which a pool process would not see.
os.environ.setdefault("PROBE_PROCESSES", "0")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_owner_cache():
    """The owner check remembers callers per credential (cloud/owner_cache.py);
    a caller one test remembered must not answer for the next."""
    try:
        from cloud import owner_cache
    except ImportError:  # this fork ships without cloud/
        yield
        return
    owner_cache.clear()
    yield
    owner_cache.clear()


# ---- Fork: the suite without cloud/ ------------------------------------------
# This fork does not ship upstream's cloud/ package (it is not under the MIT
# license). Test files that import it at module level are not collected, and
# a test that reaches for it at run time is reported as skipped, not failed.
import importlib.util  # noqa: E402
import re  # noqa: E402

HAVE_CLOUD = importlib.util.find_spec("cloud") is not None
_TOP_LEVEL_CLOUD = re.compile(r"^(from cloud\b|import cloud\b)", re.M)

collect_ignore = []
if not HAVE_CLOUD:
    _here = os.path.dirname(os.path.abspath(__file__))
    for _name in sorted(os.listdir(_here)):
        if _name.startswith("test_") and _name.endswith(".py"):
            with open(os.path.join(_here, _name), encoding="utf-8") as _f:
                if _TOP_LEVEL_CLOUD.search(_f.read()):
                    collect_ignore.append(_name)


def _needs_cloud(excinfo) -> bool:
    exc = excinfo.value if excinfo else None
    while exc is not None:
        if isinstance(exc, ModuleNotFoundError) and (exc.name or "").split(".")[0] == "cloud":
            return True
        exc = exc.__cause__ or exc.__context__
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not HAVE_CLOUD and report.failed and _needs_cloud(call.excinfo):
        report.outcome = "skipped"
        report.longrepr = (str(item.path), item.location[1] or 0,
                           "Skipped: needs upstream's cloud/ package (not in this fork)")
