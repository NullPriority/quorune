from __future__ import annotations

"""Validate that push-time checks apply to the actual outgoing source."""

import argparse
from collections.abc import Iterable
import re
import sys


GENERATION_SOURCE_PREFIX = "refs/heads/quorune-cloud-source/"
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")


def validate_push_refs(
    lines: Iterable[str], *, head: str, branch: str, source_checkpoint: bool,
) -> None:
    """Require one exact generation ref or check every outgoing branch tip."""
    if _OID.fullmatch(head) is None:
        raise ValueError("Cannot verify the checked-out commit for this push")
    updates = []
    for line in lines:
        fields = line.split()
        if not fields:
            continue
        if len(fields) != 4:
            raise ValueError("Malformed Git pre-push ref update")
        _, local_oid, remote_ref, _ = fields
        if _OID.fullmatch(local_oid) is None:
            raise ValueError("Malformed Git pre-push source object ID")
        updates.append((local_oid, remote_ref))

    if source_checkpoint:
        if not branch or branch == "main":
            raise ValueError("Cloud source checkpoints require a checked-out non-main branch")
        expected_ref = GENERATION_SOURCE_PREFIX + head
        if updates != [(head, expected_ref)]:
            raise ValueError(
                "Cloud source checkpoints may push only the checked-out commit "
                f"to {expected_ref}. Install its generated bundle before pushing a PR head."
            )
        return

    for local_oid, remote_ref in updates:
        if remote_ref.startswith("refs/heads/") and int(local_oid, 16) and local_oid != head:
            raise ValueError(
                f"Check out the exact commit destined for {remote_ref} before validating its push"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--source-checkpoint", action="store_true")
    args = parser.parse_args()
    try:
        validate_push_refs(
            sys.stdin, head=args.head, branch=args.branch, source_checkpoint=args.source_checkpoint,
        )
    except ValueError as error:
        print(f"Quorune pre-push: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
