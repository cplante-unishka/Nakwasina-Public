#!/usr/bin/env python3
"""Synchronize sanctioned addresses and mixer watchlists from public OFAC-linked sources."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import stat
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import requests
from requests import RequestException

GITHUB_OWNER = "0xB10C"
GITHUB_REPO = "ofac-sanctioned-digital-currency-addresses"
GITHUB_BRANCH = "lists"
GITHUB_API_ROOT = "https://api.github.com"
RAW_ROOT = "https://raw.githubusercontent.com"
SDN_XML_URL = "https://www.treasury.gov/ofac/downloads/sanctions/1.0/sdn_advanced.xml"
USER_AGENT = "AML-Crypto-Analyzer-SanctionsSync/0.2"
TIMEOUT_SECONDS = 30

PUBLIC_MIXER_SOURCES = [
    {
        "name": "TORNADO CASH",
        "url": "https://ofac.treasury.gov/recent-actions/20220808",
        "eth_only": True,
    },
    {
        "name": "TORNADO CASH",
        "url": "https://ofac.treasury.gov/recent-actions/20221108",
        "eth_only": True,
    },
    {
        "name": "BLENDER.IO",
        "url": "https://ofac.treasury.gov/recent-actions/20220506",
        "eth_only": False,
    },
]

MIXER_KEYWORDS = [
    "TORNADO CASH",
    "BLENDER.IO",
    "BLENDER",
    "SINBAD",
    "MIXER",
    "MIXING",
    "CRYPTOCURRENCY MIXER",
]

ETH_RE = re.compile(r"0x[a-fA-F0-9]{40}")
BTC_LEGACY_RE = re.compile(r"\b[13][a-km-zA-HJ-NP-Z1-9]{25,34}\b")
BTC_SEGWIT_RE = re.compile(r"\bbc1[ac-hj-np-z02-9]{11,71}\b", re.IGNORECASE)
XRP_RE = re.compile(r"\br[1-9A-HJ-NP-Za-km-z]{24,34}\b")


@dataclass
class SyncReport:
    updated: bool
    address_count: int
    file_count: int
    mixer_count: int
    mixer_source_count: int
    output_path: Path
    mixer_output_path: Path
    metadata_path: Path
    detail: str


class SanctionsSyncError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})

    def get_json(self, url: str) -> Dict[str, Any]:
        try:
            response = self.session.get(url, timeout=TIMEOUT_SECONDS)
        except RequestException as exc:
            raise SanctionsSyncError(f"Network error while requesting {url}: {exc}") from exc
        if response.status_code >= 400:
            raise SanctionsSyncError(f"HTTP {response.status_code} for {url}: {response.text[:240]}")
        return response.json()

    def get_text(self, url: str) -> str:
        try:
            response = self.session.get(url, timeout=TIMEOUT_SECONDS)
        except RequestException as exc:
            raise SanctionsSyncError(f"Network error while requesting {url}: {exc}") from exc
        if response.status_code >= 400:
            raise SanctionsSyncError(f"HTTP {response.status_code} for {url}: {response.text[:240]}")
        return response.text


def _is_likely_address(value: str) -> bool:
    candidate = (value or "").strip()
    if len(candidate) < 16:
        return False
    if ETH_RE.fullmatch(candidate):
        return True
    if BTC_LEGACY_RE.fullmatch(candidate):
        return True
    if BTC_SEGWIT_RE.fullmatch(candidate):
        return True
    if XRP_RE.fullmatch(candidate):
        return True
    if candidate.lower().startswith(("bitcoincash:", "bchtest:")) and len(candidate) > 24:
        return True
    return False


def _extract_from_json_obj(payload: Any) -> Set[str]:
    found: Set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if _is_likely_address(node):
                found.add(node.strip())
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and _is_likely_address(key):
                    found.add(key.strip())
                walk(value)

    walk(payload)
    return found


def _extract_from_csv_text(text: str) -> Set[str]:
    found: Set[str] = set()
    reader = csv.reader(text.splitlines())
    for row in reader:
        for value in row:
            val = value.strip()
            if _is_likely_address(val):
                found.add(val)
    return found


def _extract_from_text_lines(text: str) -> Set[str]:
    found: Set[str] = set()
    for line in text.splitlines():
        candidate = line.strip()
        if candidate.startswith("#") or not candidate:
            continue
        if _is_likely_address(candidate):
            found.add(candidate)
    return found


def _list_candidate_files(client: GitHubClient) -> List[Tuple[str, str]]:
    url = f"{GITHUB_API_ROOT}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/trees/{GITHUB_BRANCH}?recursive=1"
    payload = client.get_json(url)
    tree = payload.get("tree", [])
    if not isinstance(tree, list):
        raise SanctionsSyncError("Unexpected GitHub tree response")

    candidates: List[Tuple[str, str]] = []
    for item in tree:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", ""))
        sha = str(item.get("sha", ""))
        if item.get("type") != "blob":
            continue
        lowered = path.lower()
        if not lowered.endswith((".json", ".txt", ".csv")):
            continue
        if "sanction" in lowered or "ofac" in lowered:
            candidates.append((path, sha))

    if not candidates:
        raise SanctionsSyncError("No candidate sanctions files found in repository tree")
    return candidates


def _fetch_addresses(client: GitHubClient, path: str) -> Set[str]:
    raw_url = f"{RAW_ROOT}/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{path}"
    lowered = path.lower()
    text = client.get_text(raw_url)

    if lowered.endswith(".json"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SanctionsSyncError(f"Invalid JSON in {path}: {exc}")
        return _extract_from_json_obj(payload)
    if lowered.endswith(".csv"):
        return _extract_from_csv_text(text)
    return _extract_from_text_lines(text)


def _write_csv(rows: List[Tuple[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _prepare_writable_path(output_path)
    tmp_path = _write_csv_temp(
        output_path.parent,
        ["address", "source"],
        rows,
    )
    _replace_file_atomically(tmp_path, output_path)


def _read_meta(meta_path: Path) -> Dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_meta(meta_path: Path, payload: Dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _entry_name(entry: ET.Element) -> str:
    fields = {"firstName", "lastName", "title", "sdnType"}
    parts: List[str] = []
    for child in entry.iter():
        tag = _strip_ns(child.tag)
        if tag in fields and child.text and child.text.strip():
            parts.append(child.text.strip())
    name = " ".join(parts).strip()
    return name if name else "UNKNOWN_MIXER_ENTITY"


def _entry_all_text(entry: ET.Element) -> str:
    chunks: List[str] = []
    for child in entry.iter():
        if child.text and child.text.strip():
            chunks.append(child.text.strip())
    return " ".join(chunks).upper()


def _extract_mixer_addresses_from_sdn_xml(client: GitHubClient) -> Dict[str, Tuple[str, str]]:
    xml_text = client.get_text(SDN_XML_URL)
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise SanctionsSyncError(f"Failed to parse OFAC SDN XML: {exc}")

    mixers: Dict[str, Tuple[str, str]] = {}
    for entry in root.iter():
        if _strip_ns(entry.tag) != "sdnEntry":
            continue
        content = _entry_all_text(entry)
        if not any(keyword in content for keyword in MIXER_KEYWORDS):
            continue

        mixer_name = _entry_name(entry)
        for node in entry.iter():
            if _strip_ns(node.tag) != "id":
                continue
            id_type = ""
            id_number = ""
            for sub in node:
                key = _strip_ns(sub.tag)
                txt = (sub.text or "").strip()
                if key == "idType":
                    id_type = txt
                elif key == "idNumber":
                    id_number = txt
            if "Digital Currency Address" in id_type and _is_likely_address(id_number):
                mixers[id_number] = (
                    mixer_name,
                    f"OFAC SDN XML ({id_type})",
                )
    return mixers


def _extract_addresses_from_public_page(text: str, eth_only: bool) -> Set[str]:
    matches: Set[str] = set()
    for value in ETH_RE.findall(text):
        matches.add(value)

    if not eth_only:
        for regex in (BTC_LEGACY_RE, BTC_SEGWIT_RE, XRP_RE):
            for value in regex.findall(text):
                matches.add(value)

    return {m for m in matches if _is_likely_address(m)}


def _extract_public_mixer_lists(client: GitHubClient) -> Dict[str, Tuple[str, str]]:
    rows: Dict[str, Tuple[str, str]] = {}
    for item in PUBLIC_MIXER_SOURCES:
        name = str(item["name"])
        url = str(item["url"])
        eth_only = bool(item["eth_only"])
        text = client.get_text(url)
        addresses = _extract_addresses_from_public_page(text, eth_only=eth_only)
        for address in addresses:
            if address not in rows:
                rows[address] = (name, f"OFAC recent-actions {url}")
    return rows


def _read_existing_mixer_rows(path: Path) -> Dict[str, Tuple[str, str]]:
    if not path.exists():
        return {}
    out: Dict[str, Tuple[str, str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            return out
        for row in reader:
            address = (row.get("address") or "").strip()
            mixer_name = (row.get("mixer_name") or "").strip()
            source = (row.get("source") or "manual-existing").strip()
            if address and mixer_name:
                out[address] = (mixer_name, source)
    return out


def _write_mixer_csv(path: Path, rows: Dict[str, Tuple[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _prepare_writable_path(path)
    ordered_rows: List[Tuple[str, str, str]] = []
    for address in sorted(rows.keys()):
        mixer_name, source = rows[address]
        ordered_rows.append((address, mixer_name, source))
    tmp_path = _write_csv_temp(path.parent, ["address", "mixer_name", "source"], ordered_rows)
    _replace_file_atomically(tmp_path, path)


def _prepare_writable_path(path: Path) -> None:
    if not path.exists():
        return
    try:
        mode = path.stat().st_mode
        if not (mode & stat.S_IWRITE):
            path.chmod(mode | stat.S_IWRITE)
    except Exception:
        # Keep going; replace step will provide detailed error if still blocked.
        pass


def _write_csv_temp(parent: Path, header: List[str], rows: Iterable[Iterable[Any]]) -> Path:
    fd, raw_path = tempfile.mkstemp(prefix=".tmp_sync_", suffix=".csv", dir=str(parent))
    tmp_path = Path(raw_path)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(header)
            for row in rows:
                writer.writerow(list(row))
        return tmp_path
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _replace_file_atomically(tmp_path: Path, final_path: Path) -> None:
    try:
        tmp_path.replace(final_path)
    except PermissionError as exc:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise SanctionsSyncError(
            f"Permission denied writing {final_path}. "
            f"The file may be open/locked by another program. Close it and retry."
        ) from exc


def sync_sanctions(
    output_path: Path,
    metadata_path: Path,
    mixer_output_path: Optional[Path] = None,
    max_age_hours: int = 24,
    force: bool = False,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> SyncReport:
    def emit_progress(percent: int, message: str) -> None:
        if progress_callback is None:
            return
        bounded = max(0, min(100, int(percent)))
        progress_callback(bounded, message)

    emit_progress(0, "Starting update")
    mixer_output = mixer_output_path or (output_path.parent / "known_mixers.csv")

    now = datetime.now(timezone.utc)
    meta = _read_meta(metadata_path)

    if not force and output_path.exists() and mixer_output.exists() and meta.get("last_sync_utc"):
        try:
            last_sync = datetime.fromisoformat(str(meta["last_sync_utc"]))
            if now - last_sync < timedelta(hours=max_age_hours):
                emit_progress(100, "Already up to date")
                return SyncReport(
                    updated=False,
                    address_count=int(meta.get("address_count", 0)),
                    file_count=int(meta.get("file_count", 0)),
                    mixer_count=int(meta.get("mixer_count", 0)),
                    mixer_source_count=int(meta.get("mixer_source_count", 0)),
                    output_path=output_path,
                    mixer_output_path=mixer_output,
                    metadata_path=metadata_path,
                    detail=f"Sanctions/mixer lists are fresh (last sync: {meta['last_sync_utc']})",
                )
        except Exception:
            pass

    client = GitHubClient()
    emit_progress(10, "Fetching source file list")

    files = _list_candidate_files(client)
    sanctions_rows: List[Tuple[str, str]] = []
    seen_addresses: Set[str] = set()
    total_files = max(1, len(files))
    for idx, (path, sha) in enumerate(files, start=1):
        addresses = _fetch_addresses(client, path)
        source = f"{GITHUB_OWNER}/{GITHUB_REPO}:{path}@{sha[:12]}"
        for address in sorted(addresses):
            if address in seen_addresses:
                continue
            seen_addresses.add(address)
            sanctions_rows.append((address, source))
        emit_progress(10 + int((idx / total_files) * 50), f"Processed sanctions source {idx}/{total_files}")

    if not sanctions_rows:
        raise SanctionsSyncError("Sanctions sync succeeded but no addresses were extracted")

    sanctions_rows.sort(key=lambda item: item[0])
    _write_csv(sanctions_rows, output_path)
    emit_progress(65, "Wrote sanctions CSV")

    # Build mixer list from: existing manual entries + SDN mixer filter + OFAC Tornado/Blender public pages.
    combined_mixers: Dict[str, Tuple[str, str]] = _read_existing_mixer_rows(mixer_output)
    emit_progress(72, "Loaded existing mixer list")

    sdn_mixers = _extract_mixer_addresses_from_sdn_xml(client)
    emit_progress(82, "Loaded OFAC SDN mixer entries")
    public_mixers = _extract_public_mixer_lists(client)
    emit_progress(92, "Loaded OFAC public mixer pages")

    combined_mixers.update(sdn_mixers)
    combined_mixers.update(public_mixers)

    if not combined_mixers:
        raise SanctionsSyncError("Mixer sync produced no entries")

    _write_mixer_csv(mixer_output, combined_mixers)
    emit_progress(96, "Wrote mixer CSV")

    mixer_sources = set()
    for _address, (_name, source) in combined_mixers.items():
        mixer_sources.add(source)

    _write_meta(
        metadata_path,
        {
            "last_sync_utc": now.isoformat(),
            "address_count": len(sanctions_rows),
            "file_count": len(files),
            "mixer_count": len(combined_mixers),
            "mixer_source_count": len(mixer_sources),
            "github_owner": GITHUB_OWNER,
            "github_repo": GITHUB_REPO,
            "github_branch": GITHUB_BRANCH,
            "sdn_xml_url": SDN_XML_URL,
        },
    )
    emit_progress(100, "Update completed")

    return SyncReport(
        updated=True,
        address_count=len(sanctions_rows),
        file_count=len(files),
        mixer_count=len(combined_mixers),
        mixer_source_count=len(mixer_sources),
        output_path=output_path,
        mixer_output_path=mixer_output,
        metadata_path=metadata_path,
        detail="Sanctions and mixer lists refreshed from public sources",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update sanctions and mixer address lists")
    parser.add_argument("--output", default="intel/sanctioned_addresses.csv", help="Sanctions output CSV path")
    parser.add_argument("--mixer-output", default="intel/known_mixers.csv", help="Mixer output CSV path")
    parser.add_argument("--meta", default="intel/sanctions_sync_meta.json", help="Sync metadata path")
    parser.add_argument("--max-age-hours", default=24, type=int, help="Skip refresh if data is newer than this")
    parser.add_argument("--force", action="store_true", help="Force refresh even if data is fresh")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = sync_sanctions(
            output_path=Path(args.output),
            mixer_output_path=Path(args.mixer_output),
            metadata_path=Path(args.meta),
            max_age_hours=max(1, args.max_age_hours),
            force=args.force,
        )
    except SanctionsSyncError as exc:
        print(f"Sanctions sync failed: {exc}")
        return 2

    action = "Updated" if report.updated else "Skipped"
    print(f"{action}: {report.detail}")
    print(f"Sanctions addresses: {report.address_count}")
    print(f"Sanctions source files: {report.file_count}")
    print(f"Mixer addresses: {report.mixer_count}")
    print(f"Mixer sources: {report.mixer_source_count}")
    print(f"Sanctions CSV: {report.output_path}")
    print(f"Mixer CSV: {report.mixer_output_path}")
    print(f"Meta: {report.metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
