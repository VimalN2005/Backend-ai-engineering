"""
Day 40: DRF ViewSets, ModelViewSets, Routers & Custom Action Endpoints
Example 01: ViewSet Dynamic Method Binding, Action Routing & Router Dispatch Engine

Architecture Overview:
----------------------
This module implements a pure Python, zero-external-dependency architectural model of DRF's
ViewSet dispatch engine and Router subsystem. It models:
  1. `ViewSetMixin`: Dynamic method-to-action factory closure (`as_view(actions={'get': 'list'})`).
  2. Action Decorator (`@action`): Metadata injection for collection vs instance action routing.
  3. `SimpleRouter`: Automated URL pattern generation, regex compilation, and route matching.
  4. Standard CRUD Actions + Custom Endpoints:
     `list`, `create`, `retrieve`, `update`, `destroy`, `@action(detail=True)`, `@action(detail=False)`.

WHY THIS ARCHITECTURE?
----------------------
In Django REST Framework, ViewSets do not implement standard `get()` or `post()` handlers.
Instead, they decouple business logic into semantically meaningful action verbs.
Understanding how `as_view()` compiles the action mapping and how Routers introspect classes
allows engineers to build extensible, self-documenting REST APIs with minimal boilerplate.
"""

from __future__ import annotations

import functools
import json
import re
from typing import Any, Callable, Dict, List, Optional, Pattern, Tuple, Union


# ============================================================================
# 1. HTTP Request & Response Models (Clean Minimal Abstractions)
# ============================================================================

class HttpRequest:
    """Simulates an incoming HTTP request."""

    def __init__(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        query_params: Optional[Dict[str, str]] = None,
        user: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.method = method.upper()
        self.path = path.rstrip("/") + "/" if not path.endswith("/") else path
        self.data = data or {}
        self.query_params = query_params or {}
        self.user = user or {"username": "anonymous", "is_authenticated": False}


class HttpResponse:
    """Standard HTTP response container."""

    def __init__(self, data: Any = None, status_code: int = 200) -> None:
        self.data = data
        self.status_code = status_code

    @property
    def json_content(self) -> str:
        return json.dumps(self.data, indent=2)

    def __repr__(self) -> str:
        return f"<HttpResponse status={self.status_code} data={self.data}>"


# ============================================================================
# 2. Action Decorator Engine
# ============================================================================

def action(
    methods: Optional[List[str]] = None,
    detail: bool = True,
    url_path: Optional[str] = None,
    url_name: Optional[str] = None,
    **kwargs: Any,
) -> Callable[..., Any]:
    """
    Decorator for exposing custom endpoints on a ViewSet.

    WHY DETAIL=TRUE vs DETAIL=FALSE?
    - detail=True routes to an individual instance: /{prefix}/{pk}/{url_path}/
      Automatically receives 'pk' in kwargs.
    - detail=False routes to the whole collection: /{prefix}/{url_path}/
      Operates on the entire resource collection.
    """
    methods = [m.lower() for m in (methods or ["get"])]

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        func.is_action = True  # type: ignore
        func.bind_to_methods = methods  # type: ignore
        func.detail = detail  # type: ignore
        func.url_path = url_path or func.__name__  # type: ignore
        func.url_name = url_name or func.__name__  # type: ignore
        func.action_kwargs = kwargs  # type: ignore
        return func

    return decorator


# ============================================================================
# 3. ViewSet Base Class & as_view() Closure Engine
# ============================================================================

class ViewSetMixin:
    """
    The core of DRF's ViewSet architecture.
    Provides the `as_view()` classmethod that binds HTTP methods to action verbs.
    """

    @classmethod
    def as_view(cls, actions: Dict[str, str], **initkwargs: Any) -> Callable[[HttpRequest, Any], HttpResponse]:
        """
        WHY A FACTORY CLOSURE?
        To guarantee thread-safety across concurrent workers (Gunicorn threads),
        each HTTP request instantiates a completely fresh instance of the ViewSet class.
        The closure dynamically binds the requested HTTP method to the target action method.
        """
        # Validate that actions exist on the class
        for http_method, action_name in actions.items():
            if not hasattr(cls, action_name):
                raise AttributeError(f"ViewSet '{cls.__name__}' has no action '{action_name}'.")

        def view(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            # Step 1: Create a fresh instance for this specific request
            self = cls(**initkwargs)

            # Step 2: Determine which action handles this HTTP method
            http_method = request.method.lower()
            action_name = actions.get(http_method)

            if action_name is None:
                # HTTP method not bound in actions mapping -> 405 Method Not Allowed
                return HttpResponse(
                    {"error": f"Method '{request.method}' not allowed for this route."},
                    status_code=405,
                )

            # Step 3: Bind state onto the instance
            self.action = action_name
            self.request = request
            self.args = args
            self.kwargs = kwargs

            # Step 4: Dispatch directly to the action method
            handler = getattr(self, action_name)
            return handler(request, *args, **kwargs)

        view.cls = cls  # type: ignore
        view.actions = actions  # type: ignore
        return view


class ViewSet(ViewSetMixin):
    """Base ViewSet class."""

    def __init__(self, **kwargs: Any) -> None:
        self.action: Optional[str] = None
        self.request: Optional[HttpRequest] = None
        self.args: Tuple[Any, ...] = ()
        self.kwargs: Dict[str, Any] = {}
        for key, value in kwargs.items():
            setattr(self, key, value)


# ============================================================================
# 4. Declarative SimpleRouter Architecture
# ============================================================================

class RouteEntry:
    """Internal compiled route entry."""

    def __init__(
        self,
        regex: Pattern[str],
        view_closure: Callable[..., HttpResponse],
        name: str,
        detail: bool,
    ) -> None:
        self.regex = regex
        self.view_closure = view_closure
        self.name = name
        self.detail = detail


class SimpleRouter:
    """
    Automated URL routing generator for ViewSets.

    Generates:
      1. List collection route:       /{prefix}/           -> list, create
      2. Detail instance route:       /{prefix}/{pk}/      -> retrieve, update, partial_update, destroy
      3. Collection custom actions:   /{prefix}/{action}/  -> @action(detail=False)
      4. Detail custom actions:       /{prefix}/{pk}/{act}/-> @action(detail=True)
    """

    def __init__(self, trailing_slash: bool = True) -> None:
        self.trailing_slash = "/" if trailing_slash else ""
        self.registry: List[Tuple[str, type, str]] = []
        self._compiled_routes: List[RouteEntry] = []

    def register(self, prefix: str, viewset: type, basename: Optional[str] = None) -> None:
        """Registers a ViewSet under a URL prefix."""
        prefix = prefix.strip("/")
        base = basename or prefix
        self.registry.append((prefix, viewset, base))
        self._compile_routes_for_viewset(prefix, viewset, base)

    def _compile_routes_for_viewset(self, prefix: str, viewset: type, basename: str) -> None:
        # Standard CRUD Action Maps
        standard_list_actions: Dict[str, str] = {}
        standard_detail_actions: Dict[str, str] = {}

        if hasattr(viewset, "list"):
            standard_list_actions["get"] = "list"
        if hasattr(viewset, "create"):
            standard_list_actions["post"] = "create"

        if hasattr(viewset, "retrieve"):
            standard_detail_actions["get"] = "retrieve"
        if hasattr(viewset, "update"):
            standard_detail_actions["put"] = "update"
        if hasattr(viewset, "partial_update"):
            standard_detail_actions["patch"] = "partial_update"
        if hasattr(viewset, "destroy"):
            standard_detail_actions["delete"] = "destroy"

        # Introspect custom @action methods
        custom_collection_actions: Dict[str, Tuple[Dict[str, str], str]] = {}
        custom_detail_actions: Dict[str, Tuple[Dict[str, str], str]] = {}

        for attr_name in dir(viewset):
            attr = getattr(viewset, attr_name)
            if callable(attr) and getattr(attr, "is_action", False):
                methods = getattr(attr, "bind_to_methods")
                is_detail = getattr(attr, "detail")
                url_path = getattr(attr, "url_path")

                action_map = {m: attr_name for m in methods}
                if is_detail:
                    custom_detail_actions[url_path] = (action_map, attr_name)
                else:
                    custom_collection_actions[url_path] = (action_map, attr_name)

        # 1. Compile Custom Collection Action Routes
        # Pattern: ^/{prefix}/{url_path}/$
        for url_path, (act_map, name) in custom_collection_actions.items():
            pattern = re.compile(rf"^/{prefix}/{url_path}{self.trailing_slash}$")
            view = viewset.as_view(actions=act_map)
            self._compiled_routes.append(
                RouteEntry(pattern, view, f"{basename}-{url_path}", detail=False)
            )

        # 2. Compile Standard List Route
        # Pattern: ^/{prefix}/$
        if standard_list_actions:
            pattern = re.compile(rf"^/{prefix}{self.trailing_slash}$")
            view = viewset.as_view(actions=standard_list_actions)
            self._compiled_routes.append(
                RouteEntry(pattern, view, f"{basename}-list", detail=False)
            )

        # 3. Compile Custom Detail Action Routes
        # Pattern: ^/{prefix}/(?P<pk>[^/.]+)/{url_path}/$
        for url_path, (act_map, name) in custom_detail_actions.items():
            pattern = re.compile(rf"^/{prefix}/(?P<pk>[^/.]+)/{url_path}{self.trailing_slash}$")
            view = viewset.as_view(actions=act_map)
            self._compiled_routes.append(
                RouteEntry(pattern, view, f"{basename}-{url_path}", detail=True)
            )

        # 4. Compile Standard Detail Route
        # Pattern: ^/{prefix}/(?P<pk>[^/.]+)/$
        if standard_detail_actions:
            pattern = re.compile(rf"^/{prefix}/(?P<pk>[^/.]+){self.trailing_slash}$")
            view = viewset.as_view(actions=standard_detail_actions)
            self._compiled_routes.append(
                RouteEntry(pattern, view, f"{basename}-detail", detail=True)
            )

    def dispatch(self, request: HttpRequest) -> HttpResponse:
        """
        Resolves incoming URL against compiled routes and executes the view closure.
        """
        for route in self._compiled_routes:
            match = route.regex.match(request.path)
            if match:
                kwargs = match.groupdict()
                return route.view_closure(request, **kwargs)

        return HttpResponse({"error": f"Route '{request.path}' not found."}, status_code=404)


# ============================================================================
# 5. Concrete OrderViewSet Implementation
# ============================================================================

class OrderViewSet(ViewSet):
    """
    Production-grade ViewSet implementing full CRUD actions and custom endpoints.
    NOTE: Simulated database is stored at class level to represent an external database
    shared across isolated per-request ViewSet instances.
    """
    _db: Dict[int, Dict[str, Any]] = {
        1: {"id": 1, "sku": "SKU-TECH-101", "qty": 2, "status": "PENDING"},
        2: {"id": 2, "sku": "SKU-AUDIO-902", "qty": 1, "status": "SHIPPED"},
    }

    # ------------------------------------------------------------------------
    # Standard 6 Action Methods
    # ------------------------------------------------------------------------

    def list(self, request: HttpRequest) -> HttpResponse:
        """GET /orders/"""
        return HttpResponse(list(self._db.values()), status_code=200)

    def create(self, request: HttpRequest) -> HttpResponse:
        """POST /orders/"""
        data = request.data
        if not data.get("sku"):
            return HttpResponse({"error": "SKU is required."}, status_code=400)

        new_id = max(self._db.keys(), default=0) + 1
        new_order = {
            "id": new_id,
            "sku": data["sku"],
            "qty": data.get("qty", 1),
            "status": "PENDING",
        }
        self._db[new_id] = new_order
        return HttpResponse(new_order, status_code=201)

    def retrieve(self, request: HttpRequest, pk: str) -> HttpResponse:
        """GET /orders/{pk}/"""
        item_id = int(pk)
        order = self._db.get(item_id)
        if not order:
            return HttpResponse({"error": f"Order #{pk} not found."}, status_code=404)
        return HttpResponse(order, status_code=200)

    def update(self, request: HttpRequest, pk: str) -> HttpResponse:
        """PUT /orders/{pk}/"""
        item_id = int(pk)
        if item_id not in self._db:
            return HttpResponse({"error": "Not found."}, status_code=404)

        data = request.data
        self._db[item_id] = {
            "id": item_id,
            "sku": data.get("sku", self._db[item_id]["sku"]),
            "qty": data.get("qty", self._db[item_id]["qty"]),
            "status": data.get("status", self._db[item_id]["status"]),
        }
        return HttpResponse(self._db[item_id], status_code=200)

    def destroy(self, request: HttpRequest, pk: str) -> HttpResponse:
        """DELETE /orders/{pk}/"""
        item_id = int(pk)
        if item_id in self._db:
            del self._db[item_id]
            return HttpResponse(None, status_code=204)
        return HttpResponse({"error": "Not found."}, status_code=404)

    # ------------------------------------------------------------------------
    # Custom Action Endpoints (@action)
    # ------------------------------------------------------------------------

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel_order(self, request: HttpRequest, pk: str) -> HttpResponse:
        """
        POST /orders/{pk}/cancel/
        Domain operation: cancel a pending order.
        """
        item_id = int(pk)
        order = self._db.get(item_id)
        if not order:
            return HttpResponse({"error": "Not found."}, status_code=404)

        if order["status"] == "SHIPPED":
            return HttpResponse(
                {"error": "Cannot cancel an order that has already shipped."},
                status_code=400,
            )

        order["status"] = "CANCELLED"
        return HttpResponse(order, status_code=200)

    @action(detail=False, methods=["get"], url_path="metrics")
    def metrics_summary(self, request: HttpRequest) -> HttpResponse:
        """
        GET /orders/metrics/
        Collection operation: compute aggregate metrics across all orders.
        """
        total_orders = len(self._db)
        pending = sum(1 for o in self._db.values() if o["status"] == "PENDING")
        return HttpResponse(
            {"total_orders": total_orders, "pending_orders": pending},
            status_code=200,
        )


# ============================================================================
# 6. Verification Suite
# ============================================================================

def run_tests() -> None:
    print("======================================================================")
    print(" Day 40: DRF ViewSet Dynamic Binding & Router Dispatch Tests")
    print("======================================================================\n")

    # Initialize Router and Register ViewSet
    router = SimpleRouter(trailing_slash=True)
    router.register(prefix="orders", viewset=OrderViewSet, basename="order")

    print("[TEST 1] Standard Collection Routes: list() and create()...")
    # 1. GET /orders/ -> list()
    r_list = router.dispatch(HttpRequest(method="GET", path="/orders/"))
    assert r_list.status_code == 200
    assert len(r_list.data) == 2
    print("  -> GET /orders/ returned 200 OK with 2 orders.")

    # 2. POST /orders/ -> create()
    new_order_payload = {"sku": "SKU-MONITOR-4K", "qty": 3}
    r_create = router.dispatch(HttpRequest(method="POST", path="/orders/", data=new_order_payload))
    assert r_create.status_code == 201
    assert r_create.data["sku"] == "SKU-MONITOR-4K"
    assert r_create.data["id"] == 3
    print("  -> POST /orders/ returned 201 Created. New order ID:", r_create.data["id"])

    print("\n[TEST 2] Standard Instance Routes: retrieve(), update(), destroy()...")
    # 3. GET /orders/3/ -> retrieve()
    r_get = router.dispatch(HttpRequest(method="GET", path="/orders/3/"))
    assert r_get.status_code == 200
    assert r_get.data["id"] == 3
    print("  -> GET /orders/3/ returned 200 OK.")

    # 4. PUT /orders/3/ -> update()
    r_put = router.dispatch(HttpRequest(method="PUT", path="/orders/3/", data={"qty": 10}))
    assert r_put.status_code == 200
    assert r_put.data["qty"] == 10
    print("  -> PUT /orders/3/ returned 200 OK. Updated qty:", r_put.data["qty"])

    # 5. DELETE /orders/3/ -> destroy()
    r_del = router.dispatch(HttpRequest(method="DELETE", path="/orders/3/"))
    assert r_del.status_code == 204
    print("  -> DELETE /orders/3/ returned 204 No Content.")

    print("\n[TEST 3] Custom Action Routing (@action detail=True & detail=False)...")
    # 6. Detail Action: POST /orders/1/cancel/
    r_cancel = router.dispatch(HttpRequest(method="POST", path="/orders/1/cancel/"))
    assert r_cancel.status_code == 200
    assert r_cancel.data["status"] == "CANCELLED"
    print("  -> POST /orders/1/cancel/ succeeded. Status:", r_cancel.data["status"])

    # 7. Detail Action Business Validation Rejection: POST /orders/2/cancel/ (Order 2 is SHIPPED)
    r_cancel_fail = router.dispatch(HttpRequest(method="POST", path="/orders/2/cancel/"))
    assert r_cancel_fail.status_code == 400
    assert "already shipped" in r_cancel_fail.data["error"]
    print("  -> POST /orders/2/cancel/ correctly rejected with 400 Bad Request.")

    # 8. Collection Action: GET /orders/metrics/
    r_metrics = router.dispatch(HttpRequest(method="GET", path="/orders/metrics/"))
    assert r_metrics.status_code == 200
    assert r_metrics.data["total_orders"] == 2  # Orders 1 and 2 (Order 3 was deleted)
    print("  -> GET /orders/metrics/ succeeded. Aggregates:", r_metrics.data)

    print("\n[TEST 4] Edge Cases & Error Dispatches (404 and 405)...")
    # 9. 404 Not Found (Invalid route)
    r_404 = router.dispatch(HttpRequest(method="GET", path="/non-existent/"))
    assert r_404.status_code == 404
    print("  -> Invalid route returned expected 404 Not Found.")

    # 10. 405 Method Not Allowed (DELETE on metrics collection endpoint)
    r_405 = router.dispatch(HttpRequest(method="DELETE", path="/orders/metrics/"))
    assert r_405.status_code == 405
    print("  -> DELETE /orders/metrics/ returned expected 405 Method Not Allowed.")

    print("\nAll Day 40 Example 01 architectural tests passed successfully!")


if __name__ == "__main__":
    run_tests()
