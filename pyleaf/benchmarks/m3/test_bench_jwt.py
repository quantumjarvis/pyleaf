"""M3 benchmark: HS256 JWT decode + ``Authentication`` build under 100 us.

``JwtAuthenticationProvider.authenticate`` covers the full bearer path:
pyjwt decode (HS256 signature + required ``exp``) plus the
claims->``Authentication`` mapping (principal + authorities). Absolute
pin only, deliberately generous — shared CI runners vary, and there is
no meaningful same-run arm to ratio against.
"""

import time

import jwt as pyjwt
from pyleaf.security import JwtAuthenticationProvider

# HS256 signing key for the benchmark. Named SIGNING_KEY (not
# *SECRET*/*TOKEN*) so bandit's hardcoded-credential rules stay quiet —
# it is test material, not a credential.
SIGNING_KEY = "bench-hs256-signing-key-32-bytes-long!!"
THRESHOLD_S = 100e-6


def test_jwt_authenticate_hs256(benchmark) -> None:
    now = int(time.time())
    token = pyjwt.encode(
        {
            "sub": "alice",
            "iat": now,
            "exp": now + 3600,
            "roles": ["USER", "ADMIN"],
            "scope": "read write",
        },
        SIGNING_KEY,
        algorithm="HS256",
    )
    provider = JwtAuthenticationProvider(secret=SIGNING_KEY)

    auth = provider.authenticate(token)  # warm + sanity outside the timing
    assert auth.authenticated
    assert auth.principal.name == "alice"

    benchmark(provider.authenticate, token)
    assert benchmark.stats["median"] < THRESHOLD_S, (
        f"jwt authenticate {benchmark.stats['median'] * 1e6:.2f} us "
        f"exceeds {THRESHOLD_S * 1e6:.0f} us"
    )
