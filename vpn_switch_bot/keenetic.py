import asyncio
import hashlib
import httpx
import logging
from typing import Optional

from config import config

logger = logging.getLogger(__name__)


class KeeneticClient:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.base_url = f"https://{host}:{port}"
        self.username = username
        self.password = password
        self._client = httpx.AsyncClient(timeout=30.0, verify=False, follow_redirects=True)
        self._authenticated = False
        self._policies_cache: Optional[dict] = None

    async def authenticate(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/auth")

            challenge = response.headers.get("x-ndm-challenge", "")
            realm = response.headers.get("x-ndm-realm", "")

            if not challenge:
                logger.error(f"No challenge received. Status: {response.status_code}")
                return False

            md5_hash = hashlib.md5(
                f"{self.username}:{realm}:{self.password}".encode()
            ).hexdigest()
            auth_hash = hashlib.sha256(
                f"{challenge}{md5_hash}".encode()
            ).hexdigest()

            response = await self._client.post(
                f"{self.base_url}/auth",
                json={"login": self.username, "password": auth_hash}
            )

            self._authenticated = response.status_code == 200
            return self._authenticated
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False

    async def _request(self, method: str, path: str, data=None):
        if not self._authenticated:
            await self.authenticate()

        headers = {"Content-Type": "application/json"}
        url = f"{self.base_url}{path}"

        if method.upper() == "GET":
            response = await self._client.get(url, headers=headers)
        else:
            response = await self._client.post(url, json=data, headers=headers)

        try:
            return response.json()
        except:
            return {}

    async def get_policies(self) -> dict:
        data = await self._request("GET", "/rci/show/ip/policy")
        self._policies_cache = data
        return data

    def get_policy_internal_name(self, description: str) -> Optional[str]:
        if not self._policies_cache:
            return None
        for key, value in self._policies_cache.items():
            if value.get("description") == description:
                return key
        return None

    async def get_all_hosts(self) -> list[dict]:
        data = await self._request("GET", "/rci/ip/hotspot/host")
        if not isinstance(data, list):
            return []

        hosts_with_policies = data

        data_names = await self._request("GET", "/rci/show/ip/hotspot")
        hosts_with_names = data_names.get("host", [])

        name_by_mac = {}
        for h in hosts_with_names:
            mac = h.get("mac", "").lower()
            name = h.get("name") or h.get("hostname") or ""
            if mac:
                name_by_mac[mac] = name

        for h in hosts_with_policies:
            mac = h.get("mac", "").lower()
            if mac in name_by_mac and not h.get("name"):
                h["name"] = name_by_mac[mac]

        return hosts_with_policies

    async def set_host_policy(self, mac: str, policy_description: str) -> bool:
        if not self._policies_cache:
            await self.get_policies()

        internal_name = self.get_policy_internal_name(policy_description)
        if not internal_name:
            logger.error(f"Policy not found: {policy_description}")
            return False

        mac_lower = mac.lower()
        response = await self._request(
            "POST",
            "/rci/ip/hotspot/host",
            {"mac": mac_lower, "policy": internal_name}
        )

        if isinstance(response, dict) and "policy" in response:
            status = response["policy"].get("status", [])
            for s in status:
                if s.get("status") == "message":
                    logger.info(f"Policy set: {s.get('message')}")
                    return True

        logger.warning(f"Unexpected response: {response}")
        return False

    async def close(self):
        await self._client.aclose()


keenetic_client = KeeneticClient(
    host=config.KEENETIC_HOST,
    port=config.KEENETIC_PORT,
    username=config.KEENETIC_USER,
    password=config.KEENETIC_PASSWORD
)
