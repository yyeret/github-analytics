"""Small Google Identity Services token verifier for Cloudflare's Python Worker."""

import base64
import hashlib
import json
import time

import httpx

_jwks = None
_jwks_expires_at = 0
_SHA256_DER_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


def _decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


async def verify_id_token(token: str, client_id: str, expected_nonce: str) -> dict:
    """Verify RS256 signature and required OIDC claims using Google's public JWKs."""
    global _jwks, _jwks_expires_at
    if len(token) > 16_000:
        raise ValueError("Google credential is too large.")
    pieces = token.split(".")
    if len(pieces) != 3:
        raise ValueError("Google credential is malformed.")
    header = json.loads(_decode(pieces[0]))
    claims = json.loads(_decode(pieces[1]))
    if header.get("alg") != "RS256" or not header.get("kid"):
        raise ValueError("Google credential uses an unsupported signature.")

    if not _jwks or time.time() >= _jwks_expires_at:
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get("https://www.googleapis.com/oauth2/v3/certs")
        response.raise_for_status()
        _jwks = response.json().get("keys", [])
        max_age = response.headers.get("cache-control", "").split("max-age=")
        _jwks_expires_at = time.time() + (int(max_age[1].split(",")[0]) if len(max_age) > 1 else 1800)

    jwk = next((item for item in _jwks if item.get("kid") == header["kid"] and item.get("kty") == "RSA"), None)
    if not jwk:
        _jwks = None
        raise ValueError("Google signing key is unavailable; try signing in again.")
    modulus = int.from_bytes(_decode(jwk["n"]), "big")
    exponent = int.from_bytes(_decode(jwk["e"]), "big")
    signature = _decode(pieces[2])
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size:
        raise ValueError("Google credential signature is invalid.")
    signed = f"{pieces[0]}.{pieces[1]}".encode()
    encoded_message = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    digest_info = _SHA256_DER_PREFIX + hashlib.sha256(signed).digest()
    padding_length = size - len(digest_info) - 3
    expected = b"\x00\x01" + b"\xff" * padding_length + b"\x00" + digest_info
    if padding_length < 8 or encoded_message != expected:
        raise ValueError("Google credential signature is invalid.")

    now = int(time.time())
    issuer = claims.get("iss")
    audience = claims.get("aud")
    if issuer not in ("accounts.google.com", "https://accounts.google.com"):
        raise ValueError("Google credential issuer is invalid.")
    if audience != client_id:
        raise ValueError("Google credential was issued for a different app.")
    if int(claims.get("exp", 0)) < now - 60 or int(claims.get("iat", now + 1)) > now + 60:
        raise ValueError("Google credential has expired.")
    if claims.get("nonce") != expected_nonce:
        raise ValueError("Google sign-in could not be verified. Please try again.")
    if claims.get("email_verified") is not True or not claims.get("email") or not claims.get("sub"):
        raise ValueError("Google did not verify an email address for this account.")
    if claims.get("azp") and claims["azp"] != client_id:
        raise ValueError("Google credential was issued for a different app.")
    return {"sub": claims["sub"], "email": claims["email"]}
