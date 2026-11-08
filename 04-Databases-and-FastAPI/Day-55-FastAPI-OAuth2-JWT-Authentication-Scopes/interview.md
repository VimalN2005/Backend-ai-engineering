# Senior & Staff Engineering Interview Q&A: OAuth2, JWT Authentication & Scopes

This document compiles the top 5 advanced architectural and security interview questions regarding OAuth2, JWTs, scopes, and distributed authentication systems, formulated for Senior and Staff Backend & AI Systems Engineers.

---

### Question 1: What are the mechanical and architectural differences between OAuth2 Password Grant (ROPC) and Authorization Code Flow with PKCE, and why is Password Grant deprecated in OAuth 2.1?

#### Conceptual Answer
The **Resource Owner Password Credentials (ROPC / Password Grant)** flow allows a client application to collect the end-user's raw credentials (username and password) directly and exchange them with the authorization server for access and refresh tokens.

In contrast, the **Authorization Code Flow with Proof Key for Code Exchange (PKCE)** (RFC 7636) completely decouples the client application from the user's credentials. The client initiates authentication by redirecting the user's browser to the Identity Provider (IdP). The user logs in directly with the IdP (which can enforce MFA, biometric WebAuthn/Passkeys, or SSO). The IdP redirects back to the client with an authorization code cryptographically bound to a runtime client secret (`code_verifier` / `code_challenge`), which the client exchanges for tokens.

OAuth 2.1 officially deprecates Password Grant because:
1. **Direct Credential Exposure**: The client application sees, buffers, and handles raw passwords. This makes users vulnerable to credential harvesting, memory inspection attacks, and third-party dependency compromise.
2. **Impediment to Modern Authentication**: Password Grant cannot support Multi-Factor Authentication (MFA), FIDO2 WebAuthn passkeys, or corporate federated Identity Providers (Okta, Azure AD, Google Workspace) without clumsy proprietary hacks.
3. **Overprivileged Delegation**: When an external or third-party client has user passwords, it bypasses the principle of least privilege, as it can authenticate as the user directly across any system.

Password Grant remains acceptable only for legacy systems, first-party mobile/native apps where redirect handling is constrained, or high-throughput trusted automated harnesses. Modern enterprise architectures mandate Authorization Code Flow with PKCE for all user-facing clients (SPAs, native mobile, web apps) and Client Credentials Grant for machine-to-machine (M2M) services.

#### Architecture & Flow Diagram
```
ROPC / Password Grant (Deprecated):
Client App ---> [Receives Plaintext Password] ---> Auth Server ---> Returns JWT
(Extreme risk: Client compromise leaks user passwords)

Authorization Code Flow with PKCE (OAuth 2.1 Standard):
Client App ---> Browser Redirect ---> IdP Login Page (MFA, WebAuthn)
                                           |
Client App <--- Auth Code + State <------- Browser Redirect
    |
    +---------> POST /token (Code + code_verifier) ---> Auth Server ---> Returns JWT
```

---

### Question 2: When should an enterprise architecture choose symmetric signing (HS256) versus asymmetric signing (RS256/ES256), and what are the operational tradeoffs?

#### Conceptual Answer
The decision between symmetric (`HS256`) and asymmetric (`RS256`, `ES256`, `EdDSA`) signing hinges on **trust domains and system topology**:

| Dimension | Symmetric (`HS256`) | Asymmetric (`RS256` / `ES256`) |
| :--- | :--- | :--- |
| **Cryptographic Primitive** | Shared secret HMAC (`HMAC-SHA256`) | Public/Private Keypair (RSA / Elliptic Curve) |
| **Signing Entity** | Holds symmetric secret key | Holds private key only |
| **Verifying Entity** | Must hold the exact same secret key | Holds public key only (distributable via JWKS) |
| **Trust Boundary** | Single service or monolithic perimeter | Heterogeneous multi-service / multi-cloud architectures |
| **CPU Signing Latency** | Ultra-fast (~20–40 µs per signature) | Moderate to High (~1–50 ms for RSA; ~0.2 ms for ECDSA) |
| **CPU Verification Latency**| Ultra-fast (~30–50 µs per verification) | Fast (~70–120 µs for RSA verification) |
| **Security Blast Radius** | **Critical**: Compromise of any verifier allows forging tokens for all users | **Contained**: Public keys are public; private key is quarantined in IdP/HSM |

#### Engineering Deep Dive
In a microservices ecosystem with 50 downstream services:
- If using **HS256**, all 50 services must have access to the secret key to verify incoming tokens. If an attacker exploits a remote code execution (RCE) or read vulnerability on a low-security service (e.g., an internal PDF generator), they recover the HMAC secret and can immediately issue forged root admin tokens across the entire enterprise.
- If using **RS256/ES256**, the private key is held exclusively by the central Authentication Service (often backed by AWS KMS or HashiCorp Vault). The other 49 services only fetch the public key (via `/.well-known/jwks.json`). A compromise of any downstream service reveals only public keys, preventing token forgery.

To eliminate the CPU overhead of repetitive asymmetric verification, downstream services implement an **in-memory LRU signature cache**: the SHA-256 hash of the token signature is mapped to the validated payload. Subsequent requests within the token's TTL hit memory in `< 1 µs` (100x+ speedup).

---

### Question 3: How do you solve the "Stateless JWT Revocation Problem" in a distributed system without degrading database throughput to stateful session levels?

#### Conceptual Answer
Pure stateless JWTs present an inherent architectural paradox: **you cannot immediately revoke a token without maintaining state.** If an employee is terminated, an account is hijacked, or a user changes their password, a purely stateless verifier will continue accepting the token until its `exp` claim elapses.

To solve this without reverting to stateful database lookups on every single request, enterprise systems deploy a **Three-Tier Hybrid Revocation Engine**:

1. **Short Token Lifetimes (Tier 1)**:
   Access tokens are configured with aggressive lifetimes (e.g., 10 to 15 minutes). This strictly bounds the maximum vulnerability window under catastrophic network partition or total cache failure.
2. **Distributed Redis Blocklist with Automatic TTL (Tier 2)**:
   Every access token contains a unique `jti` (JWT ID). When a user clicks "Log Out", the auth server writes `SET token_blacklist:{jti} 1 EX {remaining_ttl}` to a distributed Redis cluster. 
   - Because the Redis key expires automatically when the token itself expires, the blocklist remains small and bounded.
   - Resource servers check Redis with an $O(1)$ memory lookup taking `< 0.5 ms`.
3. **User-Level Revocation Epoch / Security Stamp (Tier 3)**:
   For events like "Change Password" or "Log Out All Devices", storing millions of individual `jti`s is inefficient. Instead, store a single `revocation_epoch` integer (or timestamp) per user in Redis:
   `user_security_epoch:{user_id} = 1718000000`
   During token verification, if `token.iat < user_security_epoch`, the token is rejected immediately. This invalidates all tokens issued prior to the security event with a single cache key.

#### Gateway Optimization: In-Memory Bloom Filter
High-scale API Gateways (Envoy, Kong) layer an in-memory Bloom filter or Cuckoo filter in front of Redis. If the Bloom filter returns `False` (the `jti` is definitely not revoked), the request proceeds to upstream services without making a network round-trip to Redis, achieving over 100,000 requests per second per node.

---

### Question 4: What is the mechanical difference between OAuth2 Scopes and Application Roles (RBAC), and how should they be designed together in FastAPI?

#### Conceptual Answer
The core distinction lies in **Identity vs. Delegation**:
- **Roles (RBAC)** model **User Identity Entitlements**: What can *this specific person* do based on their organizational responsibility? (e.g., Alice is a `FinancialController`, Bob is an `Auditor`).
- **Scopes (OAuth2)** model **Delegated Authorization**: What permissions has the user *delegated* to a specific client application or API integration? (e.g., Alice authorizes a receipt-scanning mobile app with scope `invoices:create`, but explicitly denies it `invoices:delete` or `reports:export`).

#### The Scope Creep Trap
A common antipattern is setting `scope = role` (e.g., granting a token `scope: "admin"`). If an Admin user connects a third-party Slack notification bot to their account, and the bot receives `admin` scope, an attacker who compromises the Slack bot can delete databases, reconfigure DNS, and provision root accounts.

#### FastAPI Enterprise Pattern: Effective Permissions
In production, access control is governed by the mathematical intersection:
$$\text{Effective Permission} = \text{User Role Permissions} \cap \text{Client Delegated Scopes}$$

In FastAPI, this is cleanly expressed using `SecurityScopes` combined with a role dependency:

```python
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import SecurityScopes, OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="auth/token",
    scopes={
        "invoices:read": "Read financial invoices",
        "invoices:write": "Create or update invoices",
    }
)

async def require_permissions(
    security_scopes: SecurityScopes,
    token_payload: dict = Depends(verify_jwt_token),
    current_user: User = Depends(get_current_active_user)
):
    # 1. Enforce Client Delegated Scopes (OAuth2)
    token_scopes = set(token_payload.get("scopes", []))
    for req_scope in security_scopes.scopes:
        if req_scope not in token_scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing delegated scope: {req_scope}",
                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{req_scope}"'}
            )
            
    # 2. Enforce User Role Entitlements (RBAC)
    if "admin" not in current_user.roles and "billing_manager" not in current_user.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account lacks administrative role entitlements"
        )
    return current_user
```

---

### Question 5: Describe the "Algorithm Confusion Attack" against JWTs. How does it work mechanically, and how do modern security standards prevent it?

#### Conceptual Answer
The **Algorithm Confusion Attack** (specifically the RS256-to-HS256 Key Confusion Attack) exploits implementations where a cryptographic library allows dynamic algorithm selection and uses the same key variable for both symmetric and asymmetric verification.

#### Mechanical Breakdown
1. **Target Architecture**: An enterprise system uses asymmetric encryption (`RS256`). The Identity Provider signs tokens with an RSA private key. Downstream services verify tokens using the corresponding RSA public key, which is publicly accessible via `/.well-known/jwks.json`.
2. **The Flaw**: The downstream service verifies tokens using a generic decoder:
   ```python
   # Vulnerable verification code
   jwt.decode(token, key=SERVER_PUBLIC_KEY, algorithms=["RS256", "HS256"])
   ```
3. **The Exploit**:
   - An attacker downloads the server's public key (a standard PEM string: `-----BEGIN PUBLIC KEY...-----`).
   - The attacker crafts a malicious JWT payload containing `{"sub": "superadmin", "scopes": ["*"]}`.
   - The attacker modifies the header to specify symmetric HMAC: `{"alg": "HS256", "typ": "JWT"}`.
   - The attacker signs the token using standard HMAC-SHA256, **using the server's public key PEM string as the HMAC secret key**!
4. **The Bypass**:
   - When the token arrives at the server, the verifier reads `alg: "HS256"` from the header.
   - The verifier executes `HMAC_SHA256(header.payload, SERVER_PUBLIC_KEY)`.
   - Because the server's `key` parameter happens to be the identical `SERVER_PUBLIC_KEY` that the attacker used to sign the token, the HMAC calculation matches the token's signature perfectly!
   - The attacker achieves complete authentication bypass and privilege escalation without possessing the private key.

#### Prevention Protocols
1. **Strict Algorithm Whitelisting**: Never allow multiple cryptographic families in the `algorithms` parameter. Verifiers must strictly specify `algorithms=["RS256"]`.
2. **Explicit Key Typing**: Modern cryptographic libraries (such as `PyJWT` 2.x and `cryptography`) inspect the key object type. If `algorithms=["HS256"]` is supplied with an RSA public key object, the library rejects the operation with a type mismatch error (`TypeError: Expected bytes/str secret for HMAC, got RSAPublicKey`).
3. **Adherence to RFC 8725**: RFC 8725 (JSON Web Token Best Current Practices) explicitly mandates:
   - Reject tokens with `alg: "none"`.
   - Never use the same key for multiple algorithms.
   - Ensure the algorithm declared in the header matches the verifier's expected algorithm before processing.
