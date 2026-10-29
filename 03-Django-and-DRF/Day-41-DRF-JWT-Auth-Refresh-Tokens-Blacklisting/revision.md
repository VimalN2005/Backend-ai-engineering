# 5-Minute Rapid Revision: DRF JWT, Refresh Tokens & Blacklisting

A high-yield, quick-reference cheat sheet for senior backend engineers reviewing Django REST Framework (DRF) JWT authentication, refresh token rotation, and blacklisting.

---

## 1. Anatomy of a JWT (RFC 7519)

```
eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoxMDEsImV4cCI6MTgwMH0.4dK3...
├───────────────────┤ ├──────────────────────────────┤ ├──────┤
1. Header             2. Payload (Claims)              3. Signature
   Base64URL(JSON)       Base64URL(JSON)                  HMAC-SHA256(H + "." + P, secret)
```

- **Header**: `{"alg": "HS256", "typ": "JWT"}`
- **Payload**: Standard claims (`exp`, `iat`, `jti`, `sub`) + Custom claims (`tenant_id`, `role`).
- **Signature**: Verifies integrity. **Base64URL is NOT encryption!** Never put passwords or secrets in payload.

---

## 2. Access Token vs Refresh Token Matrix

```
PROPERTY               Access Token                    Refresh Token
─────────────────────────────────────────────────────────────────────────────────────────────
Lifetime               5 to 15 minutes                 1 to 7 days
Verification           Stateless (0 database queries)  Stateful (Checked against blacklist)
Header / Transport     Authorization: Bearer <token>   POST /api/token/refresh/ or HttpOnly Cookie
Revocation             Expires naturally               Revoked via Blacklist on rotation/logout
Security Role          Authorizes individual API calls Refreshes expired access tokens
─────────────────────────────────────────────────────────────────────────────────────────────
```

---

## 3. Production SimpleJWT Settings Boilerplate

```python
# settings.py
from datetime import timedelta

INSTALLED_APPS = [
    # ...
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,       # Rotates refresh token on every refresh
    'BLACKLIST_AFTER_ROTATION': True,   # Blacklists old refresh token immediately
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': SECRET_KEY,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
    'TOKEN_TYPE_CLAIM': 'token_type',
    'JTI_CLAIM': 'jti',
}
```

---

## 4. Token Rotation & Reuse Detection Flow

1. **Rotation**: Calling `/api/token/refresh/` invalidates the submitted refresh token and issues a new pair.
2. **Family Tracking**: Parent and child tokens share a `family_id`.
3. **Reuse Detection (RFC 6749 Section 10.4)**: If an already-blacklisted refresh token is submitted again, the server assumes token theft and **revokes all tokens in that family**, locking out the attacker and the victim.

---

## 5. Custom Claims Injection Boilerplate

```python
# serializers.py
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    @classmethod
    def get_token(cls, user):
        token = super().get_token(user)
        # Custom claims embedded into JWT access token
        token['email'] = user.email
        token['tenant_id'] = str(user.tenant_id)
        token['role'] = user.role
        return token
```

---

## 6. The 5 Security Commandments of JWT

1. **Short Access Token Expiration**: Keep access token lifetime between 5 and 15 minutes.
2. **Strict Algorithm Whitelisting**: Never allow client headers to dictate the algorithm; strictly defend against `alg: "none"` and RS256/HS256 key confusion.
3. **No Sensitive PII in Payload**: Base64URL is readable by anyone who possesses the token.
4. **Use HttpOnly Cookies for Refresh Tokens**: Defends against credential theft via Cross-Site Scripting (XSS).
5. **Schedule `flushexpiredtokens`**: Run daily via Celery Beat to prevent unbounded database bloat in blacklist tables.

---

## 7. High-Frequency Production Debugging Checklist

- [ ] **Are users reporting unexpected logouts after 15 minutes?** Check if the frontend client is properly handling silent token refresh on 401 responses.
- [ ] **Are blacklisted tokens still allowing API requests?** Remember: Access tokens are stateless and valid until `exp`! Blacklisting only stops the refresh token.
- [ ] **Are blacklist database tables growing into millions of rows?** Check if `manage.py flushexpiredtokens` is configured in Celery Beat.
- [ ] **Are tokens rejected with "Algorithm mismatch"?** Ensure both signer and verifier use identical algorithms (`HS256`).
- [ ] **Are refresh tokens failing with "Token is blacklisted"?** Check if your frontend is firing duplicate concurrent refresh requests (race condition consuming the single-use token).
