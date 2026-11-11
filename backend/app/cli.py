from __future__ import annotations

import argparse
import hashlib
import secrets
import uuid

from .storage import ApiKey, SessionLocal, init_db


def main():
    parser = argparse.ArgumentParser(description="NEXORA administration utilities")
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create-key", help="Create an API key; the secret is shown once")
    create.add_argument("--name", required=True)
    create.add_argument("--permissions", default="read")
    args = parser.parse_args()
    init_db()
    token = "nxr_" + secrets.token_urlsafe(36)
    permissions = list(dict.fromkeys(p.strip() for p in args.permissions.split(",") if p.strip()))
    if not permissions or set(permissions) - {"read", "write", "admin"}:
        parser.error("permissions must be read, write, and/or admin")
    with SessionLocal() as db:
        db.add(ApiKey(id=str(uuid.uuid4()), name=args.name, key_hash=hashlib.sha256(token.encode()).hexdigest(), permissions=permissions))
        db.commit()
    print("Save this key now; it cannot be shown again:\n" + token)


if __name__ == "__main__":
    main()
