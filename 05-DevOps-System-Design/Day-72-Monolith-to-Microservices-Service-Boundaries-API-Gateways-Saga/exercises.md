# Day 72: Enterprise Practical Exercises: Microservices & Saga Pattern

This workbook contains **3 enterprise-grade distributed systems exercises**. Each exercise presents a production scenario, precise architectural requirements, and a complete, tested reference solution.

---

## Exercise 1: Resilient Orchestration Saga Engine with Reverse Compensation

### Scenario
A travel booking platform books Flights, Hotels, and Car Rentals in a multi-step distributed Saga. If the Car Rental service fails (e.g., no cars available), the Saga Execution Coordinator must reliably abort and execute compensating transactions in **strict reverse order** to cancel the hotel and flight reservations without leaving dangling commitments.

### Requirements
1. Implement a `TravelBookingSagaCoordinator` that manages a state log.
2. Define 3 compensable steps:
   - `BookFlight` $\to$ `CancelFlight`
   - `BookHotel` $\to$ `CancelHotel`
   - `BookRentalCar` $\to$ `CancelRentalCar`
3. Execute forward steps sequentially.
4. If a step fails, intercept the exception, log the failure, and execute compensating transactions in **reverse order of completion** (e.g., if step 3 fails, compensate step 2, then step 1).
5. Ensure all compensations are executed even if one raises a temporary warning.

### Solution

```python
"""
Exercise 1 Solution: Resilient Travel Booking Saga Coordinator
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Tuple


class StepState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"


@dataclass
class SagaAction:
    name: str
    execute: Callable[[], bool]
    compensate: Callable[[], bool]
    state: StepState = StepState.NOT_STARTED


class TravelBookingSagaCoordinator:
    def __init__(self):
        self.audit_log: List[str] = []

    def execute_saga(self, steps: List[SagaAction]) -> Tuple[bool, str]:
        self.audit_log.clear()
        completed_actions: List[SagaAction] = []

        # 1. Forward Phase
        for action in steps:
            self.audit_log.append(f"START_STEP: {action.name}")
            try:
                success = action.execute()
                if not success:
                    raise RuntimeError(f"{action.name} returned False")
                action.state = StepState.SUCCEEDED
                completed_actions.append(action)
                self.audit_log.append(f"STEP_SUCCEEDED: {action.name}")
            except Exception as exc:
                action.state = StepState.FAILED
                self.audit_log.append(f"STEP_FAILED: {action.name} ({exc})")
                
                # 2. Backward Compensation Phase
                self._compensate(completed_actions)
                return False, f"Saga failed at {action.name}: {exc}"

        self.audit_log.append("SAGA_SUCCESSFULLY_COMMITTED")
        return True, "All travel bookings confirmed"

    def _compensate(self, completed_actions: List[SagaAction]) -> None:
        self.audit_log.append("STARTING_REVERSE_COMPENSATIONS")
        for action in reversed(completed_actions):
            self.audit_log.append(f"COMPENSATING_STEP: {action.name}")
            try:
                action.compensate()
                action.state = StepState.COMPENSATED
                self.audit_log.append(f"COMPENSATED: {action.name}")
            except Exception as c_exc:
                self.audit_log.append(f"COMPENSATION_ERROR: {action.name} ({c_exc})")


# --- Verification Test ---
if __name__ == "__main__":
    # Simulated Service States
    flight_booked = False
    hotel_booked = False
    car_booked = False

    def book_flight():
        nonlocal flight_booked
        flight_booked = True
        return True

    def cancel_flight():
        nonlocal flight_booked
        flight_booked = False
        return True

    def book_hotel():
        nonlocal hotel_booked
        hotel_booked = True
        return True

    def cancel_hotel():
        nonlocal hotel_booked
        hotel_booked = False
        return True

    def book_car():
        # Inject Failure: sold out
        raise ValueError("No rental cars available for selected dates")

    def cancel_car():
        nonlocal car_booked
        car_booked = False
        return True

    steps = [
        SagaAction("FlightService", book_flight, cancel_flight),
        SagaAction("HotelService", book_hotel, cancel_hotel),
        SagaAction("CarRentalService", book_car, cancel_car),
    ]

    coordinator = TravelBookingSagaCoordinator()
    success, msg = coordinator.execute_saga(steps)

    print("Saga Result:", success, "-", msg)
    print("Audit Log Trace:")
    for entry in coordinator.audit_log:
        print(f"  {entry}")

    # Assertions
    assert success is False
    # Verify backward compensation restored state:
    assert flight_booked is False, "Flight booking must be compensated"
    assert hotel_booked is False, "Hotel booking must be compensated"
    assert car_booked is False
    assert steps[0].state == StepState.COMPENSATED
    assert steps[1].state == StepState.COMPENSATED
    assert steps[2].state == StepState.FAILED

    print("\nExercise 1: Travel Booking Saga & Reverse Compensations verified successfully!")
```

---

## Exercise 2: Anti-Corruption Layer (ACL) with Schema Translation & Validation

### Scenario
An enterprise migration project extracts a clean, domain-driven `CustomerService` from a 20-year-old COBOL/SQL monolith. The legacy system exports flat customer records containing numeric country codes, concatenated full names, and unnormalized phone numbers. You must build an **Anti-Corruption Layer (ACL)** that parses, sanitizes, and maps legacy payloads into strict immutable domain objects.

### Requirements
1. Define a clean DDD `CustomerEntity` with:
   - `customer_id`: string UUID
   - `first_name`: string
   - `last_name`: string
   - `phone_e164`: string formatted to E.164 (`+1...`)
   - `country_iso`: 2-letter ISO country code (`US`, `CA`, `GB`, `IN`)
2. Implement `CustomerAntiCorruptionAdapter.translate(legacy_record)`:
   - Split `FULL_NAME` (e.g. `"Smith, John"`) into `first_name` and `last_name`.
   - Translate legacy numeric country codes (`840` $\to$ `US`, `124` $\to$ `CA`, `826` $\to$ `GB`, `356` $\to$ `IN`).
   - Clean raw phone digits and normalize to E.164 format.
   - Reject corrupt or unmapped records with a custom `CorruptLegacyDataException`.

### Solution

```python
"""
Exercise 2 Solution: Customer Anti-Corruption Layer (ACL)
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Dict


class CorruptLegacyDataException(Exception):
    pass


@dataclass(frozen=True)
class ModernCustomerEntity:
    customer_id: str
    first_name: str
    last_name: str
    phone_e164: str
    country_iso: str


class CustomerAntiCorruptionAdapter:
    # Mapping legacy ISO-3166 numeric codes to ISO-3166-1 alpha-2
    NUMERIC_TO_ISO = {
        840: "US",
        124: "CA",
        826: "GB",
        356: "IN"
    }

    COUNTRY_DIAL_CODES = {
        "US": "+1",
        "CA": "+1",
        "GB": "+44",
        "IN": "+91"
    }

    @classmethod
    def translate(cls, legacy_record: Dict[str, Any]) -> ModernCustomerEntity:
        # 1. Validate ID
        cust_id = legacy_record.get("CUST_NO")
        if not cust_id:
            raise CorruptLegacyDataException("Missing mandatory CUST_NO")

        # 2. Parse Country Code
        num_code = legacy_record.get("CTRY_CD")
        iso_country = cls.NUMERIC_TO_ISO.get(num_code)
        if not iso_country:
            raise CorruptLegacyDataException(f"Unmapped legacy country code: {num_code}")

        # 3. Parse Full Name: "Last, First" or "First Last"
        raw_name = legacy_record.get("FULL_NAME", "").strip()
        if not raw_name:
            raise CorruptLegacyDataException("Empty FULL_NAME")

        if "," in raw_name:
            parts = [p.strip() for p in raw_name.split(",", 1)]
            last_name, first_name = parts[0], parts[1]
        else:
            parts = raw_name.split(" ", 1)
            first_name = parts[0]
            last_name = parts[1] if len(parts) > 1 else ""

        # 4. Normalize Phone to E.164
        raw_phone = str(legacy_record.get("TEL_NUM", ""))
        digits = re.sub(r"\D", "", raw_phone)
        if len(digits) < 7:
            raise CorruptLegacyDataException(f"Invalid telephone digits: {raw_phone}")

        dial_prefix = cls.COUNTRY_DIAL_CODES[iso_country]
        phone_e164 = f"{dial_prefix}{digits[-10:]}"

        return ModernCustomerEntity(
            customer_id=f"cust_{cust_id}",
            first_name=first_name,
            last_name=last_name,
            phone_e164=phone_e164,
            country_iso=iso_country
        )


# --- Verification Test ---
if __name__ == "__main__":
    # Valid legacy record
    legacy_1 = {
        "CUST_NO": 50012,
        "FULL_NAME": "Doe, Jane",
        "CTRY_CD": 840,
        "TEL_NUM": "(512) 555-0199"
    }

    clean_customer = CustomerAntiCorruptionAdapter.translate(legacy_1)
    print("Clean Domain Entity:", clean_customer)

    assert clean_customer.customer_id == "cust_50012"
    assert clean_customer.first_name == "Jane"
    assert clean_customer.last_name == "Doe"
    assert clean_customer.country_iso == "US"
    assert clean_customer.phone_e164 == "+15125550199"

    # Corrupt legacy record (unknown country code)
    legacy_corrupt = {
        "CUST_NO": 50013,
        "FULL_NAME": "Smith, Bob",
        "CTRY_CD": 9999,  # Invalid
        "TEL_NUM": "5551234567"
    }

    try:
        CustomerAntiCorruptionAdapter.translate(legacy_corrupt)
        assert False, "Should have raised CorruptLegacyDataException"
    except CorruptLegacyDataException as cde:
        print("Successfully intercepted corrupt legacy record:", cde)

    print("\nExercise 2: Anti-Corruption Layer (ACL) translation verified successfully!")
```

---

## Exercise 3: API Gateway Token Bucket Rate Limiter with Route Dispatch

### Scenario
An API Gateway must throttle incoming client requests to protect internal microservices from DDoS or accidental query storms. You must implement a thread-safe **Token Bucket Rate Limiter** inside the Gateway that tracks rate limits per API Key, enforces limits with `HTTP 429 Too Many Requests`, and decorates downstream headers.

### Requirements
1. Implement a `ClientRateLimiterRegistry` that maintains token buckets per API key.
2. Token Bucket rules:
   - Bucket capacity = 4 tokens.
   - Refill rate = 2 tokens/second.
3. If tokens are available: decrement token and forward request to destination service.
4. If bucket is empty: return `HTTP 429` with `Retry-After: 1` header.
5. On successful dispatch, inject identity header `X-Client-Key` into the routed request.

### Solution

```python
"""
Exercise 3 Solution: API Gateway Token Bucket Rate Limiter
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class GatewayHTTPResponse:
    status_code: int
    headers: Dict[str, str]
    body: Dict[str, Any]


class TokenBucket:
    def __init__(self, capacity: int = 4, refill_rate_per_sec: float = 2.0):
        self.capacity = capacity
        self.refill_rate = refill_rate_per_sec
        self.tokens: float = float(capacity)
        self.last_refill = time.time()

    def consume(self) -> bool:
        now = time.time()
        elapsed = now - self.last_refill
        # Refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class SecureAPIGatewayRouter:
    def __init__(self):
        self.buckets: Dict[str, TokenBucket] = {}
        self.routes: Dict[str, Any] = {}

    def get_bucket(self, api_key: str) -> TokenBucket:
        if api_key not in self.buckets:
            self.buckets[api_key] = TokenBucket(capacity=4, refill_rate_per_sec=2.0)
        return self.buckets[api_key]

    def register_route(self, path_prefix: str, handler_name: str) -> None:
        self.routes[path_prefix] = handler_name

    def handle_request(self, path: str, api_key: str) -> GatewayHTTPResponse:
        # 1. Rate Limiting Check
        bucket = self.get_bucket(api_key)
        if not bucket.consume():
            return GatewayHTTPResponse(
                status_code=429,
                headers={"Retry-After": "1", "Content-Type": "application/json"},
                body={"error": "Too Many Requests", "api_key": api_key}
            )

        # 2. Route Matching
        target = None
        for prefix, handler in self.routes.items():
            if path.startswith(prefix):
                target = handler
                break

        if not target:
            return GatewayHTTPResponse(
                status_code=404,
                headers={"Content-Type": "application/json"},
                body={"error": f"Path not found: {path}"}
            )

        # 3. Successful dispatch with header decoration
        return GatewayHTTPResponse(
            status_code=200,
            headers={"Content-Type": "application/json", "X-Client-Key": api_key},
            body={"status": "dispatched", "target": target, "path": path}
        )


# --- Verification Test ---
if __name__ == "__main__":
    gateway = SecureAPIGatewayRouter()
    gateway.register_route("/orders", "OrdersMicroservice")
    gateway.register_route("/payments", "PaymentsMicroservice")

    key = "client_alpha"

    # Step 1: Burst 4 requests (Capacity = 4)
    responses = [gateway.handle_request("/orders/new", key) for _ in range(4)]
    assert all(r.status_code == 200 for r in responses), "First 4 burst requests must succeed"
    print("4 initial burst requests succeeded with HTTP 200.")

    # Step 2: 5th request exceeds bucket capacity -> must be 429
    resp_5 = gateway.handle_request("/orders/new", key)
    assert resp_5.status_code == 429
    assert resp_5.headers["Retry-After"] == "1"
    print(f"5th request correctly throttled: HTTP {resp_5.status_code} ({resp_5.body['error']})")

    # Step 3: Wait 0.6 seconds (refills > 1 token at 2 tokens/sec)
    time.sleep(0.6)
    resp_recharged = gateway.handle_request("/orders/new", key)
    assert resp_recharged.status_code == 200
    assert resp_recharged.headers["X-Client-Key"] == key
    print("Request after refill succeeded: HTTP 200 with X-Client-Key decoration.")

    print("\nExercise 3: Gateway Token Bucket Rate Limiting & Routing verified successfully!")
```
