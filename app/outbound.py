import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

import httpx


class OutboundPolicyError(ValueError):
    pass


METADATA_HOSTS = {"metadata", "metadata.google.internal", "instance-data", "instance-data.ec2.internal"}
METADATA_ADDRESSES = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}


@dataclass(frozen=True)
class OutboundDecision:
    url: str
    normalized_url: str
    pinned_url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]
    classification: str


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _host_rule(value: str) -> tuple[str, int | None]:
    parsed = urlsplit(value if "://" in value else f"//{value}")
    return (parsed.hostname or "").lower().rstrip("."), parsed.port


def _forbidden_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if address in METADATA_ADDRESSES:
        return True
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return _forbidden_address(address.ipv4_mapped)
    return any((address.is_loopback, address.is_link_local, address.is_multicast, address.is_unspecified, address.is_reserved))


class OutboundPolicy:
    def __init__(self, *, disallowed_hosts: str = "", disallowed_cidrs: str = "", disallowed_ports: str = ""):
        self.disallowed_hosts = {_host_rule(item) for item in _split_csv(disallowed_hosts)}
        try:
            self.disallowed_cidrs = tuple(ipaddress.ip_network(item, strict=False) for item in _split_csv(disallowed_cidrs))
            self.disallowed_ports = {int(item) for item in _split_csv(disallowed_ports)}
        except ValueError as exc:
            raise OutboundPolicyError(f"Invalid outbound denylist configuration: {exc}") from exc

    @staticmethod
    def normalize(url: str) -> SplitResult:
        parsed = urlsplit(url.strip())
        if parsed.scheme not in {"http", "https"}:
            raise OutboundPolicyError("Outbound URLs must use http or https")
        if not parsed.hostname:
            raise OutboundPolicyError("Outbound URL must include a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise OutboundPolicyError("Credentials embedded in outbound URLs are not allowed")
        if parsed.fragment:
            raise OutboundPolicyError("Outbound URLs cannot contain fragments")
        host = parsed.hostname.lower().rstrip(".")
        if host in METADATA_HOSTS or host.endswith(".metadata.google.internal"):
            raise OutboundPolicyError("Cloud metadata destinations are permanently blocked")
        try:
            port = parsed.port
        except ValueError as exc:
            raise OutboundPolicyError("Outbound URL contains an invalid port") from exc
        default_port = 443 if parsed.scheme == "https" else 80
        netloc_host = f"[{host}]" if ":" in host else host
        netloc = netloc_host if port in {None, default_port} else f"{netloc_host}:{port}"
        return SplitResult(parsed.scheme, netloc, parsed.path or "/", parsed.query, "")

    def _host_is_disallowed(self, host: str, port: int) -> bool:
        return (host, None) in self.disallowed_hosts or (host, port) in self.disallowed_hosts

    def _address_is_disallowed(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(address in network for network in self.disallowed_cidrs)

    async def inspect(self, url: str) -> OutboundDecision:
        parsed = self.normalize(url)
        hostname = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if self._host_is_disallowed(hostname, port):
            raise OutboundPolicyError(f"Provider host '{hostname}:{port}' is blocked by OUTBOUND_DISALLOWED_HOSTS")
        if port in self.disallowed_ports:
            raise OutboundPolicyError(f"Outbound port {port} is blocked by OUTBOUND_DISALLOWED_PORTS")

        try:
            literal = ipaddress.ip_address(hostname)
            raw_addresses: Iterable[str] = [str(literal)]
        except ValueError:
            try:
                records = await asyncio.get_running_loop().getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
            except socket.gaierror as exc:
                raise OutboundPolicyError(f"Could not resolve provider hostname '{hostname}'") from exc
            raw_addresses = [record[4][0] for record in records]

        addresses = tuple(dict.fromkeys(raw_addresses))
        if not addresses:
            raise OutboundPolicyError("Provider hostname resolved to no addresses")
        classification = "public"
        for raw in addresses:
            address = ipaddress.ip_address(raw)
            if address in METADATA_ADDRESSES:
                raise OutboundPolicyError("Cloud metadata destinations are permanently blocked")
            if self._address_is_disallowed(address):
                raise OutboundPolicyError(
                    f"Provider destination {address} is blocked by OUTBOUND_DISALLOWED_CIDRS"
                )
            private = _forbidden_address(address) or address.is_private or not address.is_global
            if private:
                classification = "private"

        chosen = ipaddress.ip_address(addresses[0])
        pinned_host = f"[{chosen}]" if isinstance(chosen, ipaddress.IPv6Address) else str(chosen)
        default_port = 443 if parsed.scheme == "https" else 80
        pinned_netloc = pinned_host if port == default_port else f"{pinned_host}:{port}"
        normalized = urlunsplit(parsed)
        pinned = urlunsplit((parsed.scheme, pinned_netloc, parsed.path, parsed.query, ""))
        return OutboundDecision(url, normalized, pinned, hostname, port, addresses, classification)


class ProtectedAsyncClient:
    """HTTPX facade that validates and DNS-pins every upstream request."""

    def __init__(self, policy: OutboundPolicy):
        self.policy = policy
        self._client = httpx.AsyncClient(follow_redirects=False, limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))

    async def _prepare(self, url: str, headers: dict[str, str] | None) -> tuple[OutboundDecision, dict[str, str], dict[str, Any]]:
        decision = await self.policy.inspect(url)
        outgoing_headers = dict(headers or {})
        outgoing_headers["Host"] = decision.hostname if decision.port in {80, 443} else f"{decision.hostname}:{decision.port}"
        # HTTPX/httpcore forwards this extension to AnyIO as the TLS
        # server_hostname. AnyIO expects text and performs its own IDNA/ASCII
        # encoding; passing bytes causes TLS setup to fail before the provider
        # receives the request.
        return decision, outgoing_headers, {"sni_hostname": decision.hostname}

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        decision, headers, extensions = await self._prepare(url, kwargs.pop("headers", None))
        return await self._client.request(method, decision.pinned_url, headers=headers, extensions=extensions, **kwargs)

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    def stream(self, method: str, url: str, **kwargs: Any):
        @asynccontextmanager
        async def open_stream() -> AsyncIterator[httpx.Response]:
            decision, headers, extensions = await self._prepare(url, kwargs.pop("headers", None))
            async with self._client.stream(method, decision.pinned_url, headers=headers, extensions=extensions, **kwargs) as response:
                yield response
        return open_stream()

    async def aclose(self) -> None:
        await self._client.aclose()
