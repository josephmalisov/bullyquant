"""QuantConnect cloud API client.

Ported from quantconnect/claude_bot/qc.py and extended with full-result fetch
(closed trades for the anti-cheat P&L-concentration check) and date patching for
the out-of-sample holdout re-run.

Auth matches the rest of the repo: HTTP Basic with user-id + sha256(token:ts).
The HTTP module is injectable (``http=`` / a fake with .post/.get) so the client
can be unit-tested without network access.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass

try:  # requests is a hard runtime dep, but keep import failures legible
    import requests as _requests
    from requests.auth import HTTPBasicAuth
except Exception:  # pragma: no cover
    _requests = None
    HTTPBasicAuth = None

QC_API_BASE = "https://www.quantconnect.com/api/v2"


class CompileError(RuntimeError):
    """A project's code failed to compile; carries the QC build logs."""


STAT_KEYS = [
    "Compounding Annual Return",
    "Net Profit",
    "Drawdown",
    "Sharpe Ratio",
    "Win Rate",
    "Total Orders",
    "Average Win",
    "Average Loss",
    "Total Fees",
]


@dataclass
class BacktestResult:
    project_id: int
    backtest_id: str
    name: str
    statistics: dict
    runtime_statistics: dict
    closed_trades: list
    url: str

    @property
    def stats(self) -> dict:
        return self.statistics


# ── Date patching (for the out-of-sample re-run) ──────────────────────────────

def patch_dates(code: str, start: str, end: str) -> str:
    """Rewrite set_start_date/set_end_date in a strategy's source.

    start/end are YYYY-MM-DD. Returns the code unchanged if the calls aren't
    found (with no error — the caller decides whether that matters).
    """
    def parts(d: str) -> tuple[int, int, int]:
        y, m, day = d.split("-")
        return int(y), int(m), int(day)

    sy, sm, sd = parts(start)
    ey, em, ed = parts(end)
    code = re.sub(
        r"self\.set_start_date\(\s*\d{4}\s*,\s*\d+\s*,\s*\d+\s*\)",
        f"self.set_start_date({sy}, {sm}, {sd})",
        code,
    )
    code = re.sub(
        r"self\.set_end_date\(\s*\d{4}\s*,\s*\d+\s*,\s*\d+\s*\)",
        f"self.set_end_date({ey}, {em}, {ed})",
        code,
    )
    return code


class QCClient:
    def __init__(self, user_id: str, api_token: str, *, http=None, timeout: int = 30):
        if not user_id or not api_token:
            raise RuntimeError("QCClient needs a user_id and api_token.")
        self.user_id = str(user_id)
        self.api_token = api_token
        self._http = http or _requests
        if self._http is None:  # pragma: no cover
            raise RuntimeError("The 'requests' package is required (pip install requests).")
        self.timeout = timeout

    # ── auth + transport ──────────────────────────────────────────────────────

    def _auth(self):
        ts = str(int(time.time()))
        hashed = hashlib.sha256(f"{self.api_token}:{ts}".encode()).hexdigest()
        auth = HTTPBasicAuth(self.user_id, hashed) if HTTPBasicAuth else (self.user_id, hashed)
        return auth, {"Timestamp": ts}

    def _post(self, endpoint: str, body: dict) -> dict:
        auth, headers = self._auth()
        r = self._http.post(f"{QC_API_BASE}/{endpoint}", json=body, auth=auth,
                            headers=headers, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"QC API error on POST /{endpoint}: {data.get('errors', data)}")
        return data

    def _get(self, endpoint: str, params: dict) -> dict:
        auth, headers = self._auth()
        r = self._http.get(f"{QC_API_BASE}/{endpoint}", params=params, auth=auth,
                           headers=headers, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if not data.get("success"):
            raise RuntimeError(f"QC API error on GET /{endpoint}: {data.get('errors', data)}")
        return data

    # ── projects + files ──────────────────────────────────────────────────────

    def create_project(self, name: str, language: str = "Py") -> int:
        data = self._post("projects/create", {"name": name, "language": language})
        return int(data["projects"][0]["projectId"])

    def write_file(self, project_id: int, name: str, content: str) -> None:
        try:
            self._post("files/update", {"projectId": project_id, "name": name, "content": content})
        except Exception:
            self._post("files/create", {"projectId": project_id, "name": name, "content": content})

    def read_file(self, project_id: int, name: str) -> str:
        data = self._get("files/read", {"projectId": project_id, "name": name})
        files = data.get("files", [])
        if not files:
            raise RuntimeError(f"File '{name}' not found in project {project_id}.")
        return files[0].get("content", "")

    # ── compile + backtest ────────────────────────────────────────────────────

    def compile(self, project_id: int, poll: int = 3, timeout: int = 120) -> str:
        data = self._post("compile/create", {"projectId": project_id})
        compile_id = data["compileId"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self._get("compile/read", {"projectId": project_id, "compileId": compile_id})
            state = d.get("state", "")
            if state == "BuildSuccess":
                return compile_id
            if state == "BuildError":
                logs = "\n".join(str(x) for x in d.get("logs", []))
                raise CompileError(f"Compile failed:\n{logs}")
            time.sleep(poll)
        raise TimeoutError(f"Compile timed out after {timeout}s")

    @staticmethod
    def _backtest_error(bt: dict) -> str | None:
        error = (bt.get("error") or "").strip()
        stacktrace = (bt.get("stacktrace") or "").strip()
        if not error and not stacktrace:
            return None
        parts = [p for p in (error, stacktrace) if p]
        if len(parts) == 2 and parts[0] in parts[1]:
            parts = [parts[1]]
        return "\n".join(parts)

    def run_backtest(self, project_id: int, name: str, *, poll: int = 10,
                     timeout: int = 900) -> BacktestResult:
        """Compile + run a fresh backtest; block until done. Raises CompileError
        on build failure or RuntimeError with the QC runtime error on a crash."""
        compile_id = self.compile(project_id)
        data = self._post("backtests/create", {
            "projectId": project_id, "compileId": compile_id, "backtestName": name,
        })
        backtest_id = data["backtest"]["backtestId"]

        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self._get("backtests/read", {"projectId": project_id, "backtestId": backtest_id})
            bt = d.get("backtest", d)
            err = self._backtest_error(bt)
            if err:
                raise RuntimeError(err)
            if bt.get("completed"):
                return self._to_result(bt, project_id, backtest_id)
            time.sleep(poll)
        raise TimeoutError(
            f"Backtest still running after {timeout}s. "
            f"See https://www.quantconnect.com/project/{project_id}/{backtest_id}"
        )

    def read_backtest(self, project_id: int, backtest_id: str) -> BacktestResult:
        d = self._get("backtests/read", {"projectId": project_id, "backtestId": backtest_id})
        bt = d.get("backtest", d)
        return self._to_result(bt, project_id, backtest_id)

    @staticmethod
    def _to_result(bt: dict, project_id: int, backtest_id: str) -> BacktestResult:
        return BacktestResult(
            project_id=int(project_id),
            backtest_id=str(backtest_id),
            name=bt.get("name", str(backtest_id)),
            statistics=bt.get("statistics", {}) or {},
            runtime_statistics=bt.get("runtimeStatistics", {}) or {},
            closed_trades=((bt.get("totalPerformance") or {}).get("closedTrades") or []),
            url=f"https://www.quantconnect.com/project/{project_id}/{backtest_id}",
        )
