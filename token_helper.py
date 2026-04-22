from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv

from dhan_token_manager import (
    ENV_FILE,
    generate_token_via_totp,
    load_config,
    renew_token,
    save_token_to_env,
    verify_token,
)


def ensure_dhan_token() -> Tuple[str, str]:
    """Return (client_id, access_token).

    Priority order:
      1. Shared file from dhan-token-generator.exe (C:/balfund_shared/dhan_token.json)
      2. Existing valid token in .env
      3. Renew existing token
      4. Generate fresh token via TOTP
    """
    from dhan_token_manager import read_shared_token

    # 1. Try shared token file first
    shared = read_shared_token()
    if shared.get("access_token") and shared.get("client_id"):
        cid = shared["client_id"]
        tok = shared["access_token"]
        if verify_token(cid, tok):
            return cid, tok

    # 2. Fall back to .env / TOTP flow
    load_dotenv(Path(ENV_FILE))
    cfg = load_config()
    client_id = cfg['client_id']
    access_token = cfg['access_token']

    if access_token and verify_token(client_id, access_token):
        return client_id, access_token

    if access_token:
        renewed = renew_token(client_id, access_token)
        if renewed.get('success'):
            save_token_to_env(renewed['access_token'], renewed.get('expiry', ''))
            os.environ['DHAN_ACCESS_TOKEN'] = renewed['access_token']
            return client_id, renewed['access_token']

    generated = generate_token_via_totp(client_id, cfg['pin'], cfg['totp_secret'])
    if not generated.get('success'):
        raise RuntimeError(f"Dhan token generation failed: {generated.get('error', 'unknown error')}")

    save_token_to_env(generated['access_token'], generated.get('expiry', ''))
    os.environ['DHAN_ACCESS_TOKEN'] = generated['access_token']
    return client_id, generated['access_token']
