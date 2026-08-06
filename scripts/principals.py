"""Mint a bearer token and print the principals-file entry for it — RFC-043.

The token is printed **once**, here, because nothing stores it: the file
keeps the SHA-256 and the engine never sees the plaintext again. Losing it
means minting another one, which is the property that makes the file safe to
commit to a configuration repository::

    python scripts/principals.py alice --role viewer --role runner

Append the printed entry to the deployment's principals file and start the
API with ``create_app(principals="principals.json")``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.api.auth import Principals, Role, mint_token, token_digest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="the principal's name")
    parser.add_argument("--role", action="append", required=True,
                        choices=[role.value for role in Role],
                        help="repeatable; no role implies another")
    parser.add_argument("--file", default=None,
                        help="principals file to append the entry to "
                             "(created if absent); printed to stdout if not "
                             "given")
    args = parser.parse_args()

    token = mint_token()
    entry = {"name": args.name, "token_sha256": token_digest(token),
             "roles": sorted(set(args.role))}

    if args.file:
        path = Path(args.file)
        payload = (json.loads(path.read_text(encoding="utf-8"))
                   if path.is_file() else {"principals": []})
        payload.setdefault("principals", []).append(entry)
        # Validated before it is written: a principals file that will not
        # load is an API that will not start, and finding that out here is
        # better than finding it out at boot.
        Principals.from_dict(payload)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"appended {args.name} to {path}")
    else:
        print(json.dumps(entry, indent=2))

    print()
    print(f"token for {args.name} (shown once, stored nowhere):")
    print(f"  {token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
