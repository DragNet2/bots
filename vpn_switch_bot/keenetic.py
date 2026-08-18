import asyncio
import hashlib
import httpx
import logging
import ipaddress
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
        self._interfaces_cache: Optional[list[dict]] = None

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

    async def get_interfaces(self, refresh: bool = False) -> list[dict]:
        if self._interfaces_cache is not None and not refresh:
            return self._interfaces_cache

        data = await self._request("GET", "/rci/show/interface")
        if isinstance(data, list):
            self._interfaces_cache = data
        elif isinstance(data, dict):
            self._interfaces_cache = [v for v in data.values() if isinstance(v, dict)]
        else:
            self._interfaces_cache = []

        return self._interfaces_cache

    def get_interface_internal_name(self, description: str) -> Optional[str]:
        if not self._interfaces_cache:
            return None
        for iface in self._interfaces_cache:
            if iface.get("description") == description:
                return iface.get("id") or iface.get("interface") or iface.get("name")
            for key in ("id", "interface", "name"):
                if iface.get(key) == description:
                    return iface.get("id") or iface.get("interface") or iface.get("name")
        return None

    async def get_static_routes(self) -> list[dict]:
        data = await self._request("GET", "/rci/show/ip/route")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("route", []) if "route" in data else [data]
        return []

    async def add_static_route(
        self,
        address: str,
        mask: str,
        gateway: str,
        interface_internal: str,
        description: str
    ) -> bool:
        try:
            net = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
            prefix = str(net.prefixlen)
        except Exception as e:
            logger.warning(f"Could not compute prefix for {address}/{mask}: {e}")
            prefix = None

        payload = {
            "host": address,
            "mask": mask,
            "gateway": gateway,
            "interface": interface_internal,
            "comment": description,
        }
        if prefix:
            payload["prefix"] = prefix

        logger.info(f"Adding route: {payload}")
        response = await self._request("POST", "/rci/ip/route", payload)

        if isinstance(response, dict):
            if any(k in response for k in ("host", "route", "status", "id")):
                return True
            if "error" in response or "message" in response:
                logger.warning(f"Route add reported: {response}")
                return False
        if isinstance(response, list) and len(response) > 0:
            return True

        logger.warning(f"Unexpected route add response: {response}")
        return False

    async def close(self):
        await self._client.aclose()


keenetic_client = KeeneticClient(
    host=config.KEENETIC_HOST,
    port=config.KEENETIC_PORT,
    username=config.KEENETIC_USER,
    password=config.KEENETIC_PASSWORD
)
