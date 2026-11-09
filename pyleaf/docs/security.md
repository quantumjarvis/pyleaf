# pyleaf-security

`pyleaf.security` provides the M3 security model: bearer-token
authentication through ordered `AuthenticationProvider` beans, ordered
Ant-style path rules enforced by the `SecurityMiddleware`, and
`@Secured`/`@PreAuthorize`/`@PostAuthorize` method security through an
AOP interceptor — plus the Spring-style `{id}hash` password encoders.

Installing the package registers `SecurityAutoConfiguration`
(`pyleaf.autoconfigure` entry point `security`). Every integration is
optional and `OnClass`-gated: the middleware appears only when
`pyleaf-web` is installed, the JWT provider only when `pyjwt` is
installed *and* a key property is set, and the delegating password
encoder only when `argon2-cffi` or `bcrypt` is installed.

```bash
pip install "pyleaf-security[jwt,argon2]"   # or [bcrypt], [web]
```

## Method security

```python
from pyleaf.security import PostAuthorize, PreAuthorize, Secured


@PostMapping("/transfer")
@Secured("TELLER")                       # ANY of the listed roles
def transfer(self, payload: TransferRequest) -> Receipt: ...

@PreAuthorize("has_role('ADMIN') or #owner == authentication.principal.name")
def close_account(self, owner: str) -> None: ...

@PostAuthorize("return_value.owner == authentication.principal.name")
def get_account(self, account_id: int) -> Account: ...
```

The `MethodSecurityInterceptor` advice is `@Order(50)` — *outside* the
tx interceptor's `100` — so a denied call never opens a transaction.
Deny semantics: no usable authentication → `AuthenticationError` (401);
authenticated but unauthorized → `AccessDeniedError` (403). Method-level
marks override class-level marks of the same kind; different kinds
combine.

### Expression grammar

Expressions are parsed once at decoration/refresh into a whitelist
interpreter — there is no dynamic-code evaluation anywhere, and
anything outside the grammar fails at parse time, not at request time:

- calls: `has_role('X')`, `has_authority('X')`, `is_authenticated()`,
  `permit_all()`, `deny_all()`
- names: `authentication`, `principal`; `#param` or bare `param` for
  bound method arguments; `return_value`, `result`, `#result` for the
  return value (post-authorize only)
- public attribute reads (`authentication.principal.name`)
- `and` / `or` / `not`, a single `==`/`!=` comparison, parentheses,
  string/int/bool/`None` literals

Missing data (no auth, absent argument) evaluates as *denied*.

## JWT bearer authentication

```yaml
pyleaf:
  security:
    jwt:
      secret: "shared-hs256-key"           # HS256
      # public-key: "-----BEGIN PUBLIC..." # RS256 alternative
      issuer: "https://auth.example.com"   # optional iss verification
      audience: "bank-api"                 # optional aud verification
```

Exactly one of `pyleaf.security.jwt.secret` / `public-key` configures
the `JwtAuthenticationProvider`. `exp` is **required** on tokens;
`iss`/`aud` are verified only when configured. Claims map to the
`Authentication` as:

- `sub` (then `preferred_username`, else `"jwt-user"`) → principal name;
  all claims are exposed verbatim as `principal.details`
- `roles` (list or separated string) → `ROLE_<value>` authorities
- `authorities` → verbatim `GrantedAuthority`
- `scope`/`scp` → `SCOPE_<value>` authorities

Any verification failure — bad signature, expired, wrong iss/aud,
malformed — is a 401 (`AuthenticationError`), never a silent downgrade
to anonymous and never a 500.

Custom providers are plain beans implementing the
`AuthenticationProvider` protocol (`authenticate(credentials: str)
-> Authentication | None`): return `None` to pass to the next provider,
raise `AuthenticationError` to reject. `ctx.get_all` order (@Order,
then name) is the chain order.

## Path rules

```yaml
pyleaf:
  security:
    default-access: authenticated   # or permit_all
    rules:
      - path: /health
        access: permit_all
      - path: /api/admin/**
        roles: [ADMIN]
      - path: /api/single/*         # * stays within one segment
        access: authenticated
```

- `pyleaf.security.rules` is an **ordered** list; the first matching
  rule wins — put specific rules first. Patterns are Ant-style: `**`
  crosses path segments (a trailing `/**` also matches the bare
  prefix), `*` and `?` stay within one segment.
- `roles` entries need no `ROLE_` prefix. A rule with only `path`
  requires authentication; `access` is `permit_all` | `authenticated` |
  `deny_all`. A rule setting both `roles` and `access`, or a malformed
  entry, fails startup with `PyleafError`.
- After the user rules comes the built-in actuator rule, then
  `pyleaf.security.default-access` — `authenticated` by default, so a
  security-enabled app is **secure-all**: every unmatched path
  (including `/health`) requires a credential unless a rule opens it.

### Actuator protection

When `pyleaf.actuator` is installed, a default rule
`<pyleaf.actuator.base-path>/**` → role `ACTUATOR` is appended after
the user rules — anonymous actuator access returns 401 and a
non-ACTUATOR caller 403. A user rule covering an actuator path wins
(first match). `pyleaf.security.actuator.enabled=false` removes the
rule entirely, falling through to `default-access`.

## Error responses

The middleware short-circuits 401/403 with a `{"detail": ...}` JSON
body (the advice shape); 401s carry `WWW-Authenticate: Bearer`. It also
maps `AuthenticationError`/`AccessDeniedError` raised downstream —
e.g. by the method-security advice — to the same statuses, so a
`@Secured` denial and a rule denial look identical to clients. A user
`@ControllerAdvice` handler for those types would run first and win.

## Password encoders

```python
from pyleaf.security import PasswordEncoder  # protocol

# Injected bean: DelegatingPasswordEncoder when a hasher is installed —
# encode() produces "{argon2}$argon2id$..." (preferred) or
# "{bcrypt}$2b$..."; matches() dispatches on the stored {id} prefix.
hash_ = encoder.encode("s3cret")
encoder.matches("s3cret", hash_)
```

`build_default_encoder()` registers every installed hasher; `matches`
decodes any `{id}` it knows — the Spring format, so hashes migrate
between algorithms. `{noop}` decoding is deliberately not enabled;
`NoOpPasswordEncoder` exists only for explicit construction. Provide
your own `PasswordEncoder` bean to replace the auto-configured one
(`@ConditionalOnMissingBean`).

## SecurityContextHolder

```python
from pyleaf.security import SecurityContextHolder

auth = SecurityContextHolder.get_authentication()  # bound per request
```

The middleware binds the resolved `Authentication` to a contextvar
before calling downstream — sync and async handlers alike see it —
and resets it afterwards. Outside the middleware (no web layer, or
before authentication), `get_authentication()` returns `None`.

## Properties

| Property | Default | Meaning |
|---|---|---|
| `pyleaf.security.rules` | `[]` | ordered path-rule list (see above) |
| `pyleaf.security.default-access` | `authenticated` | fallback when no rule matches — secure-all |
| `pyleaf.security.actuator.enabled` | `true` | append the `/actuator/**` → `ACTUATOR` rule |
| `pyleaf.security.jwt.secret` / `.public-key` / `.issuer` / `.audience` | unset | JWT provider configuration |
| `pyleaf.security.order` | `50` | `@Order` of the method-security advice (outside tx's 100) |
