#!/usr/bin/env python3
"""Fail closed on unreviewed camera loss or stale-base publication. No write/push API."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys


def populated(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True  # zero-degree bearings and False are values, not absence


def camera_losses(before, after):
    old = {row["id"]: row for row in before}
    new = {row["id"]: row for row in after}
    losses = []
    if len(old) != len(before) or len(new) != len(after):
        losses.append({"id": "<duplicate>", "reason": "duplicate_camera_id"})
    for identifier, row in old.items():
        if identifier not in new:
            losses.append({"id": identifier, "reason": "unreviewed_source_withdrawal"})
            continue
        fields = [key for key, value in row.items()
                  if populated(value) and new[identifier].get(key) != value]
        if fields:
            losses.append({"id": identifier, "reason": "populated_value_changed_or_lost", "fields": sorted(fields)})
    return losses


def distance_meters(a, b):
    lat1, lat2 = math.radians(a["latitude"]), math.radians(b["latitude"])
    dlat = lat2 - lat1
    dlon = math.radians(b["longitude"] - a["longitude"])
    term = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(min(1.0, math.sqrt(term)))


class DirectoryReader:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def read(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Publication path escapes root")
        return path.read_bytes()


class GitReader:
    def __init__(self, root, commit):
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("A full immutable base commit is required")
        self.root, self.commit = Path(root).resolve(), commit
        subprocess.run(["git", "-C", str(self.root), "cat-file", "-e", commit + "^{commit}"], check=True, capture_output=True)

    def read(self, relative):
        return subprocess.check_output(["git", "-C", str(self.root), "show", self.commit + ":" + relative])


def global_camera_id_violations(reader, entries, publication):
    owners = {}
    errors = []
    for code in sorted(entries):
        if not re.fullmatch(r"[A-Z]{2}", code):
            continue  # invalid jurisdictions fail the publication checks below
        dataset = json.loads(reader.read("states/" + code + ".json"))
        for row in dataset["cameras"]:
            first_state = owners.setdefault(row["id"], code)
            if first_state != code:
                errors.append({"reason": "global_duplicate_camera_id", "publication": publication,
                               "id": row["id"], "firstState": first_state, "duplicateState": code})
    return errors


def publication_violations(baseline, candidate):
    old_manifest = json.loads(baseline.read("us_state_manifest.json"))
    new_manifest = json.loads(candidate.read("us_state_manifest.json"))
    old_entries = {e["stateCode"]: e for e in old_manifest["states"]}
    new_entries = {e["stateCode"]: e for e in new_manifest["states"]}
    errors = []
    additions = []
    if len(old_entries) != len(old_manifest["states"]) or len(new_entries) != len(new_manifest["states"]):
        errors.append({"reason": "duplicate_jurisdiction"})
    if set(old_entries) != set(new_entries):
        errors.append({"reason": "jurisdiction_set_changed"})
    # A retained identity in one pack cannot be added to another pack. Check the
    # immutable baseline too: pre-existing duplication needs review, not repair.
    errors.extend(global_camera_id_violations(baseline, old_entries, "baseline"))
    errors.extend(global_camera_id_violations(candidate, new_entries, "candidate"))
    for code, entry in old_entries.items():
        if not re.fullmatch(r"[A-Z]{2}", code) or code not in new_entries:
            errors.append({"state": code, "reason": "invalid_or_missing_jurisdiction"})
            continue
        path = "states/" + code + ".json"
        old_bytes, new_bytes = baseline.read(path), candidate.read(path)
        old, new = json.loads(old_bytes), json.loads(new_bytes)
        old_ids = {row["id"] for row in old["cameras"]}
        additions.extend((code, row) for row in new["cameras"] if row["id"] not in old_ids)
        for loss in camera_losses(old["cameras"], new["cameras"]):
            errors.append({"state": code, **loss})
        if old["cameras"] == new["cameras"]:
            if old_bytes != new_bytes or entry != new_entries[code]:
                errors.append({"state": code, "reason": "unchanged_state_payload_or_entry_mutated"})
            if baseline.read("changes/" + code + ".json") != candidate.read("changes/" + code + ".json"):
                errors.append({"state": code, "reason": "unchanged_state_change_feed_mutated"})
        elif int(new_entries[code]["version"]) <= int(entry["version"]):
            errors.append({"state": code, "reason": "changed_state_version_did_not_advance"})
    # Also catches a possible replacement entering an adjacent state's pack.
    # 500 m is a conservative HOLD screen, never an automatic identity match.
    try:
        pending = json.loads(candidate.read("quarantine/backbone-pending.json"))
    except FileNotFoundError:
        pending = {}
    for state in pending.get("heldStates", []):
        for old in state.get("withdrawnRecords", []):
            for code, new in additions:
                if old.get("vendor") == new.get("vendor") and distance_meters(old, new) <= 500:
                    errors.append({"state": code, "id": new["id"], "reason": "addition_near_quarantined_position",
                                   "retainedId": old["id"]})
    return errors


def require_remote_base(base, observed):
    if observed != base:
        raise ValueError("main advanced or could not be verified; recompute from new HEAD, never rebase generated files")


def remote_main(root):
    output = subprocess.check_output(["git", "-C", str(root), "ls-remote", "--exit-code", "origin", "refs/heads/main"], text=True)
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != "refs/heads/main" or not re.fullmatch(r"[0-9a-f]{40}", rows[0][0]):
        raise ValueError("Unexpected remote main response")
    return rows[0][0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--base-sha")
    source.add_argument("--baseline-dir", type=Path, help="Local fixture/replay only; forbidden in GitHub Actions")
    parser.add_argument("--check-remote", action="store_true")
    args = parser.parse_args()
    try:
        if args.baseline_dir and (os.environ.get("GITHUB_ACTIONS") == "true" or args.check_remote):
            raise ValueError("Production publication must use its immutable Git base")
        baseline = GitReader(args.root, args.base_sha) if args.base_sha else DirectoryReader(args.baseline_dir)
        errors = publication_violations(baseline, DirectoryReader(args.root))
        if errors:
            path = args.root / "quarantine/publication-blocked.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"status": "HOLD", "base": args.base_sha, "violations": errors}, indent=2) + "\n")
            print(json.dumps({"status": "HOLD", "base": args.base_sha, "violations": errors}), file=sys.stderr)
            raise ValueError(str(len(errors)) + " unreviewed semantic losses; candidate is quarantined, no publication permitted")
        if args.check_remote:
            require_remote_base(args.base_sha, remote_main(args.root))
        digest = hashlib.sha256((args.root / "us_state_manifest.json").read_bytes()).hexdigest()
        print(json.dumps({"semantic_gate": "PASS", "base": args.base_sha, "manifest_sha256": digest, "remote_base_checked": args.check_remote}))
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError, TypeError) as error:
        print("PUBLICATION HOLD: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
