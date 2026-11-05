"""Flights - OpenSky Network live state vectors over Australia.

Docs:     https://openskynetwork.github.io/opensky-api/rest.html
Endpoint: GET https://opensky-network.org/api/states/all?lamin=..&lomin=..&lamax=..&lomax=..
Auth:     OAuth2 client credentials (basic auth was switched off on 18 Mar 2026).
          Without credentials the API still works anonymously, with fewer credits.
Limits:   credits per day - 400 anonymous, 4,000 with an account.
          A call costs 1-4 credits depending on box area; Australia is ~1,400 sq
          degrees so every call costs 4.
            authenticated: 4,000 / 4 = 1,000 calls/day -> poll every 120 s (720 calls)
            anonymous:       400 / 4 =   100 calls/day -> poll every 900 s (96 calls)
Licence:  research and non-commercial use; cite The OpenSky Network.

Each state vector is a positional array (documented order), e.g.
  ["7c6b2d", "QFA401  ", "Australia", 1762300000, 1762300001, 151.17, -33.94,
   1234.4, false, 140.2, 182.5, -5.2, null, 1300.1, "3021", false, 0, 6]
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx

from app.connectors.base import Connector, LayerInfo
from app.schema import Feature

log = logging.getLogger(__name__)

API_URL = "https://opensky-network.org/api/states/all"
TOKEN_URL = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"

# lat -44..-10, lon 112..154 covers the mainland, Tasmania and nearby waters.
AUS_QUERY = {"lamin": -44.0, "lomin": 112.0, "lamax": -10.0, "lomax": 154.0, "extended": 1}

POLL_AUTHENTICATED = 120
POLL_ANONYMOUS = 900
MAX_POSITION_AGE_S = 300  # ignore positions older than 5 min at query time

# Index positions in a state vector
ICAO24, CALLSIGN, ORIGIN_COUNTRY, TIME_POSITION, LAST_CONTACT = 0, 1, 2, 3, 4
LON, LAT, BARO_ALT, ON_GROUND, VELOCITY, TRUE_TRACK, VERTICAL_RATE = 5, 6, 7, 8, 9, 10, 11
GEO_ALT, SQUAWK, POSITION_SOURCE, CATEGORY = 13, 14, 16, 17

CATEGORY_NAMES = {
    2: "Light", 3: "Small", 4: "Large", 5: "High vortex large", 6: "Heavy",
    7: "High performance", 8: "Rotorcraft", 9: "Glider", 10: "Lighter-than-air",
    11: "Parachutist", 12: "Ultralight", 14: "Drone", 15: "Space vehicle",
}  # fmt: skip

M_TO_FT = 3.28084
MS_TO_KT = 1.943844

INFO = LayerInfo(
    key="flights",
    title="Flights (ADS-B)",
    attribution="The OpenSky Network",
    attribution_url="https://opensky-network.org",
    licence="Non-commercial use",
    poll_seconds=POLL_AUTHENTICATED,
    keep_history=True,
    stale_after_seconds=300,
)


class TokenManager:
    """Fetches an OAuth2 access token and reuses it until shortly before it expires."""

    REFRESH_MARGIN_S = 60

    def __init__(
        self,
        client: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.client_id = client_id
        self.client_secret = client_secret
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0

    async def get(self) -> str:
        if self._token is None or self._clock() >= self._expires_at - self.REFRESH_MARGIN_S:
            await self._refresh()
        return self._token  # type: ignore[return-value]

    def invalidate(self) -> None:
        self._token = None

    async def _refresh(self) -> None:
        resp = await self.client.post(
            TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        self._expires_at = self._clock() + float(body.get("expires_in", 1800))
        log.info("opensky: obtained access token (expires in %ss)", body.get("expires_in"))


class OpenSkyConnector(Connector):
    def __init__(
        self,
        client: httpx.AsyncClient,
        client_id: str = "",
        client_secret: str = "",
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        token_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(client)
        self._now = now
        self.tokens = (
            TokenManager(client, client_id, client_secret, clock=token_clock)
            if client_id and client_secret
            else None
        )
        self.info = replace(INFO, poll_seconds=POLL_AUTHENTICATED if self.authenticated else POLL_ANONYMOUS)
        self.meta = {"auth": "oauth2" if self.authenticated else "anonymous"}

    @property
    def authenticated(self) -> bool:
        return self.tokens is not None

    async def fetch(self) -> dict[str, Any]:
        resp = await self._get_states()
        if resp.status_code == 401 and self.tokens:
            # Token revoked or expired early: get a fresh one and retry once.
            self.tokens.invalidate()
            resp = await self._get_states()
        remaining = resp.headers.get("X-Rate-Limit-Remaining")
        if remaining is not None:
            self.meta["credits_remaining"] = int(remaining) if remaining.isdigit() else remaining
        resp.raise_for_status()
        return resp.json()

    async def _get_states(self) -> httpx.Response:
        headers = {"Authorization": f"Bearer {await self.tokens.get()}"} if self.tokens else {}
        return await self.client.get(API_URL, params=AUS_QUERY, headers=headers)

    def normalise(self, raw: dict[str, Any]) -> list[Feature]:
        api_time = raw.get("time") or int(self._now().timestamp())
        features: list[Feature] = []
        skipped = 0
        for state in raw.get("states") or []:
            try:
                feat = self._to_feature(state, api_time)
            except (IndexError, TypeError, ValueError, AttributeError) as exc:
                log.debug("skipping state vector: %s", exc)
                feat = None
            if feat is None:
                skipped += 1
            else:
                features.append(feat)
        if skipped:
            log.debug("opensky: skipped %d states (no position, stale or malformed)", skipped)
        return features

    def _to_feature(self, s: list[Any], api_time: int) -> Feature | None:
        lon, lat = s[LON], s[LAT]
        if lon is None or lat is None:
            return None
        icao24 = str(s[ICAO24]).strip().lower()
        if not icao24:
            return None
        seen = s[TIME_POSITION] or s[LAST_CONTACT] or api_time
        if api_time - seen > MAX_POSITION_AGE_S:
            return None

        alt_m = s[BARO_ALT] if s[BARO_ALT] is not None else s[GEO_ALT]
        category = s[CATEGORY] if len(s) > CATEGORY else None
        callsign = (s[CALLSIGN] or "").strip() or None

        feat = Feature(
            id=icao24,
            layer=INFO.key,
            lat=float(lat),
            lon=float(lon),
            timestamp=datetime.fromtimestamp(seen, UTC),
            properties={
                "title": callsign or icao24.upper(),
                "callsign": callsign,
                "icao24": icao24,
                "origin_country": s[ORIGIN_COUNTRY] or None,
                "on_ground": bool(s[ON_GROUND]),
                "altitude_ft": _round(alt_m, M_TO_FT),
                "speed_kt": _round(s[VELOCITY], MS_TO_KT),
                "heading": _round(s[TRUE_TRACK], 1),
                "vertical_rate_fpm": _round(s[VERTICAL_RATE], M_TO_FT * 60),
                "squawk": s[SQUAWK] or None,
                "category": CATEGORY_NAMES.get(category),
            },
        )
        return feat if feat.in_australia() else None


def _round(value: float | None, factor: float) -> int | None:
    return None if value is None else round(value * factor)
