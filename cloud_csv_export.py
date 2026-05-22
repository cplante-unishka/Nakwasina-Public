#!/usr/bin/env python3
"""Standalone headless address transaction exporter for cloud execution."""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

import requests

# Enter your API keys here before deploying.
BLOCKCYPHER_API_KEY = "PASTE_BLOCKCYPHER_API_KEY_HERE"
BLOCKCHAIR_API_KEY = "PASTE_BLOCKCHAIR_API_KEY_HERE"

TIMEOUT_SECONDS = 20
USER_AGENT = "Crypto-Analyzer-Cloud-Exporter/0.1"
APP_ROOT = Path(__file__).resolve().parent
DEFAULT_EXPORT_DIR = APP_ROOT / "exports"

SUPPORTED_ASSETS: Dict[str, Dict[str, str]] = {
    "BTC": {"blockchair_chain": "bitcoin", "blockcypher_chain": "btc", "blockcypher_net": "main"},
    "ETH": {"blockchair_chain": "ethereum", "blockcypher_chain": "eth", "blockcypher_net": "main"},
    "LTC": {"blockchair_chain": "litecoin", "blockcypher_chain": "ltc", "blockcypher_net": "main"},
    "DOGE": {"blockchair_chain": "dogecoin", "blockcypher_chain": "doge", "blockcypher_net": "main"},
    "DASH": {"blockchair_chain": "dash", "blockcypher_chain": "dash", "blockcypher_net": "main"},
    "BCH": {"blockchair_chain": "bitcoin-cash"},
    "XRP": {"blockchair_chain": "ripple"},
    "XLM": {"blockchair_chain": "stellar"},
    "ZEC": {"blockchair_chain": "zcash"},
    "XMR": {"blockchair_chain": "monero"},
}


class ProviderError(RuntimeError):
    pass


@dataclass
class TxIO:
    address: str
    amount: float


@dataclass
class TransactionRecord:
    asset: str
    txid: str
    timestamp: Optional[str]
    inputs: List[TxIO]
    outputs: List[TxIO]
    fee: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class HttpClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        response = self.session.get(url, params=params or {}, timeout=TIMEOUT_SECONDS)
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code} from {url}: {response.text[:300]}")
        return response.json()


class BlockCypherProvider:
    def __init__(self, api_key: Optional[str], http: HttpClient) -> None:
        self.api_key = api_key
        self.http = http

    def supports(self, asset: str) -> bool:
        return "blockcypher_chain" in SUPPORTED_ASSETS.get(asset, {})

    def _base(self, asset: str) -> str:
        info = SUPPORTED_ASSETS[asset]
        return f"https://api.blockcypher.com/v1/{info['blockcypher_chain']}/{info['blockcypher_net']}"

    def fetch_transaction(self, asset: str, txid: str) -> TransactionRecord:
        if not self.supports(asset):
            raise ProviderError(f"BlockCypher does not support {asset}")
        params: Dict[str, Any] = {"limit": 2000}
        if self.api_key and "PASTE_" not in self.api_key:
            params["token"] = self.api_key
        payload = self.http.get_json(f"{self._base(asset)}/txs/{txid}", params=params)

        unit_divisor = 1e18 if asset == "ETH" else 1e8
        inputs: List[TxIO] = []
        for txin in payload.get("inputs", []):
            addresses = txin.get("addresses", [])
            value = float(txin.get("output_value", txin.get("value", 0))) / unit_divisor
            for addr in addresses:
                inputs.append(TxIO(address=str(addr), amount=value))

        outputs: List[TxIO] = []
        for txout in payload.get("outputs", []):
            addresses = txout.get("addresses", [])
            value = float(txout.get("value", 0)) / unit_divisor
            for addr in addresses:
                outputs.append(TxIO(address=str(addr), amount=value))

        if not inputs and not outputs:
            from_addr = payload.get("from")
            to_addr = payload.get("to")
            value = float(payload.get("value", 0)) / unit_divisor
            if from_addr:
                inputs.append(TxIO(address=str(from_addr), amount=value))
            if to_addr:
                outputs.append(TxIO(address=str(to_addr), amount=value))

        fee = None
        if payload.get("fees") is not None or payload.get("gas_used") is not None:
            fee = float(payload.get("fees", payload.get("gas_used", 0))) / unit_divisor

        return TransactionRecord(
            asset=asset,
            txid=str(payload.get("hash", txid)),
            timestamp=payload.get("confirmed"),
            inputs=inputs,
            outputs=outputs,
            fee=fee,
            raw=payload,
        )

    def fetch_address_transactions(self, asset: str, address: str, limit: int = 10) -> List[str]:
        if not self.supports(asset):
            return []
        params: Dict[str, Any] = {"limit": max(1, min(limit, 50)), "txlimit": max(1, min(limit, 50))}
        if self.api_key and "PASTE_" not in self.api_key:
            params["token"] = self.api_key
        payload = self.http.get_json(f"{self._base(asset)}/addrs/{address}", params=params)
        txrefs = payload.get("txrefs", []) + payload.get("unconfirmed_txrefs", [])
        return [str(item.get("tx_hash")) for item in txrefs if item.get("tx_hash")][:limit]


class BlockchairProvider:
    def __init__(self, http: HttpClient, api_key: Optional[str] = None) -> None:
        self.http = http
        self.api_key = api_key

    def supports(self, asset: str) -> bool:
        return "blockchair_chain" in SUPPORTED_ASSETS.get(asset, {})

    def _chain(self, asset: str) -> str:
        return SUPPORTED_ASSETS[asset]["blockchair_chain"]

    def fetch_transaction(self, asset: str, txid: str) -> TransactionRecord:
        if not self.supports(asset):
            raise ProviderError(f"Blockchair does not support {asset}")
        params: Dict[str, Any] = {}
        if self.api_key and "PASTE_" not in self.api_key:
            params["key"] = self.api_key
        payload = self.http.get_json(
            f"https://api.blockchair.com/{self._chain(asset)}/dashboards/transaction/{txid}",
            params=params,
        )
        data = payload.get("data", {})
        tx_data = data.get(txid, {}) if isinstance(data, dict) else {}
        raw_inputs = tx_data.get("inputs", []) if isinstance(tx_data, dict) else []
        raw_outputs = tx_data.get("outputs", []) if isinstance(tx_data, dict) else []
        tx_meta = tx_data.get("transaction", {}) if isinstance(tx_data, dict) else {}

        inputs: List[TxIO] = []
        for txin in raw_inputs:
            addr = str(txin.get("recipient") or txin.get("sender") or txin.get("address") or "unknown")
            inputs.append(TxIO(address=addr, amount=self._to_float(txin.get("value", txin.get("amount", 0)))))

        outputs: List[TxIO] = []
        for txout in raw_outputs:
            addr = str(txout.get("recipient") or txout.get("sender") or txout.get("address") or "unknown")
            outputs.append(TxIO(address=addr, amount=self._to_float(txout.get("value", txout.get("amount", 0)))))

        if not inputs and not outputs:
            raise ProviderError(f"Could not normalize transaction {txid} for {asset}; explorer schema may have changed.")

        return TransactionRecord(
            asset=asset,
            txid=txid,
            timestamp=tx_meta.get("time") or tx_meta.get("block_time"),
            inputs=inputs,
            outputs=outputs,
            fee=self._to_float(tx_meta.get("fee", 0)) if tx_meta else None,
            raw=payload,
        )

    def fetch_address_transactions(self, asset: str, address: str, limit: int = 10) -> List[str]:
        if not self.supports(asset):
            return []
        params: Dict[str, Any] = {}
        if self.api_key and "PASTE_" not in self.api_key:
            params["key"] = self.api_key
        payload = self.http.get_json(
            f"https://api.blockchair.com/{self._chain(asset)}/dashboards/address/{address}",
            params=params,
        )
        data = payload.get("data", {})
        addr_data = self._select_address_data(data, address)
        if not isinstance(addr_data, dict):
            return []

        txids: List[str] = []
        for key in ("transactions", "calls", "token_transfers"):
            txids.extend(self._normalize_txids(addr_data.get(key, [])))

        deduped: List[str] = []
        seen: Set[str] = set()
        for txid in txids:
            if txid not in seen:
                deduped.append(txid)
                seen.add(txid)
            if len(deduped) >= limit:
                break
        return deduped

    @staticmethod
    def _to_float(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, (int, float)):
            if value > 1_000_000_000:
                return float(value) / 1e8
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return 0.0
        return 0.0

    @staticmethod
    def _select_address_data(data: Any, address: str) -> Dict[str, Any]:
        if not isinstance(data, dict):
            return {}
        if address in data and isinstance(data[address], dict):
            return data[address]
        address_lc = address.lower()
        for key, value in data.items():
            if str(key).lower() == address_lc and isinstance(value, dict):
                return value
        for value in data.values():
            if isinstance(value, dict):
                return value
        return {}

    @staticmethod
    def _normalize_txids(raw_items: Any) -> List[str]:
        if not isinstance(raw_items, list):
            return []
        out: List[str] = []
        for item in raw_items:
            if isinstance(item, (str, int)):
                out.append(str(item))
                continue
            if isinstance(item, dict):
                for key in ("transaction_hash", "tx_hash", "hash", "id"):
                    value = item.get(key)
                    if value:
                        out.append(str(value))
                        break
        return out


class ProviderRouter:
    def __init__(self, blockcypher_key: Optional[str], blockchair_key: Optional[str], allow_blockchair_fallback: bool) -> None:
        http = HttpClient()
        self.blockcypher = BlockCypherProvider(api_key=blockcypher_key, http=http)
        self.blockchair = BlockchairProvider(http=http, api_key=blockchair_key)
        self.allow_blockchair_fallback = allow_blockchair_fallback

    def fetch_transaction(self, asset: str, txid: str) -> TransactionRecord:
        errors: List[str] = []
        blockcypher_supported = self.blockcypher.supports(asset)
        blockchair_supported = self.blockchair.supports(asset)

        if blockcypher_supported:
            try:
                return self.blockcypher.fetch_transaction(asset, txid)
            except Exception as exc:
                errors.append(f"BlockCypher: {exc}")
        if blockchair_supported and ((not blockcypher_supported) or (self.allow_blockchair_fallback and errors)):
            try:
                return self.blockchair.fetch_transaction(asset, txid)
            except Exception as exc:
                errors.append(f"Blockchair: {exc}")
        raise ProviderError("; ".join(errors) if errors else f"No provider available for {asset}")

    def fetch_address_transactions(self, asset: str, address: str, limit: int) -> List[str]:
        errors: List[str] = []
        blockcypher_supported = self.blockcypher.supports(asset)
        blockchair_supported = self.blockchair.supports(asset)

        if blockcypher_supported:
            try:
                txids = self.blockcypher.fetch_address_transactions(asset, address, limit=limit)
                if txids:
                    return txids[:limit]
                errors.append("BlockCypher: returned no transactions")
            except Exception as exc:
                errors.append(f"BlockCypher: {exc}")

        if blockchair_supported and ((not blockcypher_supported) or (self.allow_blockchair_fallback and errors)):
            try:
                txids = self.blockchair.fetch_address_transactions(asset, address, limit=limit)
                if txids:
                    return txids[:limit]
                errors.append("Blockchair: returned no transactions")
            except Exception as exc:
                errors.append(f"Blockchair: {exc}")

        if errors:
            raise ProviderError(f"Address lookup failed for {address} on {asset}: " + "; ".join(errors))
        raise ProviderError(f"No provider available for {asset} address lookup")


def _sum_amounts(items: Iterable[Dict[str, object]]) -> float:
    total = 0.0
    for item in items:
        total += float(item.get("amount", 0) or 0)
    return total


def _format_ios(items: Iterable[Dict[str, object]]) -> str:
    parts: List[str] = []
    for item in items:
        address = str(item.get("address", "unknown"))
        amount = float(item.get("amount", 0) or 0)
        parts.append(f"{address}:{amount}")
    return " | ".join(parts)


def export_address_transactions(asset: str, address: str, output_path: Path, max_transactions: int) -> Path:
    router = ProviderRouter(
        blockcypher_key=(BLOCKCYPHER_API_KEY or None),
        blockchair_key=(BLOCKCHAIR_API_KEY or None),
        allow_blockchair_fallback=True,
    )
    txids = router.fetch_address_transactions(asset=asset, address=address, limit=max_transactions)
    if not txids:
        raise ProviderError(f"No transactions found for {address} on {asset}")

    rows: List[Dict[str, object]] = []
    for index, txid in enumerate(txids, start=1):
        tx = router.fetch_transaction(asset=asset, txid=txid)
        inputs = [item.__dict__ for item in tx.inputs]
        outputs = [item.__dict__ for item in tx.outputs]
        rows.append(
            {
                "sequence": index,
                "asset": tx.asset,
                "address": address,
                "txid": tx.txid,
                "timestamp": tx.timestamp or "",
                "fee": tx.fee if tx.fee is not None else "",
                "input_count": len(inputs),
                "output_count": len(outputs),
                "input_total": _sum_amounts(inputs),
                "output_total": _sum_amounts(outputs),
                "inputs": _format_ios(inputs),
                "outputs": _format_ios(outputs),
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "sequence",
                "asset",
                "address",
                "txid",
                "timestamp",
                "fee",
                "input_count",
                "output_count",
                "input_total",
                "output_total",
                "inputs",
                "outputs",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    raw_args = list(argv if argv is not None else sys.argv[1:])
    if raw_args and not raw_args[0].startswith("-"):
        asset = raw_args[0] if len(raw_args) >= 1 else ""
        address = raw_args[1] if len(raw_args) >= 2 else ""
        output = raw_args[2] if len(raw_args) >= 3 and raw_args[2] else None
        max_transactions = raw_args[3] if len(raw_args) >= 4 and raw_args[3] else "200"
        return argparse.Namespace(
            asset=asset,
            address=address,
            output=output,
            max_transactions=int(max_transactions),
        )

    parser = argparse.ArgumentParser(description="Export address transactions to CSV without launching the GUI.")
    parser.add_argument("--asset", required=True, help="Cryptocurrency ticker, for example BTC or ETH.")
    parser.add_argument("--address", required=True, help="Wallet address to export transactions for.")
    parser.add_argument("--output", help="Optional CSV output path. Defaults to exports/<asset>_<address>_transactions.csv")
    parser.add_argument(
        "--max-transactions",
        type=int,
        default=200,
        help="Maximum number of transactions to request from the upstream provider.",
    )
    return parser.parse_args(raw_args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    asset = str(args.asset or "").strip().upper()
    address = str(args.address or "").strip()
    max_transactions = max(1, int(args.max_transactions))

    if asset not in SUPPORTED_ASSETS:
        supported = ", ".join(sorted(SUPPORTED_ASSETS))
        raise SystemExit(f"Unsupported asset '{asset}'. Supported assets: {supported}")
    if not address:
        raise SystemExit("Address is required.")

    safe_address = "".join(ch if ch.isalnum() else "_" for ch in address).strip("_")[:32] or "address"
    default_output = DEFAULT_EXPORT_DIR / f"{asset}_{safe_address}_transactions.csv"
    output_path = Path(args.output).expanduser().resolve() if args.output else default_output

    try:
        written = export_address_transactions(asset=asset, address=address, output_path=output_path, max_transactions=max_transactions)
    except ProviderError as exc:
        raise SystemExit(f"Export failed: {exc}") from exc
    except Exception as exc:
        raise SystemExit(f"Unexpected export failure: {exc}") from exc

    print(f"Exported {asset} transactions for {address} to {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
