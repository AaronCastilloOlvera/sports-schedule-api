import requests

_BASE = "https://sb2bethistory-gateway-altenar2.biahosted.com/api/WidgetReports"

# Fixed params from the Playdoit widget (Altenar/BIA Sportsbook), copied
# straight from a real browser payload -- not secrets, just integration
# config. `timezoneOffset: 360` = UTC-6, matches the America/Mexico_City
# convention used elsewhere in this project.
_STATIC_PARAMS = {
    "culture": "es-ES",
    "integration": "playdoit2",
    "deviceType": 2,
    "numFormat": "en-GB",
    "countryCode": "MX",
    "liveOnly": False,
    "timezoneOffset": 360,
}

PAGE_SIZE = 50

# `statuses` is REQUIRED -- Altenar returns an empty 400 if it's left out.
# The widget itself defaults to [1, 8], but that's a PARTIAL filter --
# confirmed with real data: over ~2 months, [1,8] returned 171 bets vs 290
# with every known code (0=open, 1=won, 2=lost, 8=push, 18=closed). So
# [1,8] skips losses and open bets entirely -- never use it for importing,
# it's only a reference for what the site's own UI sends.
DEFAULT_STATUSES = [0, 1, 2, 8, 18]


class PlaydoitClient:
    """
    Raw client for the Altenar gateway Playdoit uses. The token is a
    short-lived session JWT (hours) -- passed fresh on every run, never
    stored or auto-refreshed.
    """
    def __init__(self, token: str):
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Origin": "https://www.playdoit.mx",
            "Referer": "https://www.playdoit.mx/",
        }

    def get_bet_history_page(self, date_from: str, date_to: str, page: int,
                             statuses: list | None = None) -> dict:
        body = {
            **_STATIC_PARAMS,
            "dateFrom": date_from,
            "dateTo": date_to,
            "pageNumber": page,
            "pageSize": PAGE_SIZE,
            "statuses": statuses if statuses is not None else DEFAULT_STATUSES,
        }
        try:
            r = requests.post(f"{_BASE}/widgetBetHistory", headers=self.headers, json=body, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            status = getattr(e.response, 'status_code', None)
            body_txt = getattr(e.response, 'text', '')
            print(f"[PLAYDOIT] bet history error (page={page}, status={status}): {e} — {body_txt[:300]}")
            return {"bets": [], "isLastPage": True}

    def get_all_bets(self, date_from: str, date_to: str, statuses: list | None = None) -> list:
        """Pages through widgetBetHistory until isLastPage."""
        page, out = 1, []
        while True:
            data = self.get_bet_history_page(date_from, date_to, page, statuses)
            bets = data.get("bets") or []
            out.extend(bets)
            if data.get("isLastPage", True) or not bets:
                break
            page += 1
        return out

    def get_bet_detail(self, bet_id: int) -> dict | None:
        # Confirmed against the real API: needs the same static params as
        # widgetBetHistory, not just the id (with only {"id": ..} Altenar
        # returned an empty 400).
        body = {**_STATIC_PARAMS, "id": bet_id}
        try:
            r = requests.post(f"{_BASE}/WidgetGetBetDetails", headers=self.headers,
                              json=body, timeout=15)
            r.raise_for_status()
            data = r.json()
            # Altenar sometimes returns 200 with {"error": {...}} instead of
            # a bet -- raise_for_status() won't catch it since the HTTP
            # status is 200, so it needs an explicit check.
            if data.get("error"):
                print(f"[PLAYDOIT] bet detail soft-error (id={bet_id}): {data['error']}")
                return None
            return data.get("bet")
        except requests.RequestException as e:
            status = getattr(e.response, 'status_code', None)
            body_txt = getattr(e.response, 'text', '')
            print(f"[PLAYDOIT] bet detail error (id={bet_id}, status={status}): {e} — {body_txt[:300]}")
            return None
