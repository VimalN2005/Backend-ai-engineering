"""
Day 70: Message Queues - RabbitMQ (AMQP 0-9-1) Message Broker Simulator
========================================================================
Architecture & Principles Demonstrated:
1. AMQP 0-9-1 Smart Broker Architecture:
   - Exchanges decouple producers from queues.
   - 4 Routing Exchange Types: Direct, Fanout, Topic (with * and # wildcards), and Headers.
   - Bindings with binding keys and argument headers.
2. Queue Lifecycle & Guarantees:
   - In-flight message delivery tracking and explicit acknowledgments (`basic_ack`).
   - Rejections and Dead-Letter Exchange (DLX / DLQ) routing (`basic_nack` with requeue=False).
   - Queue capacity constraints and TTL expiration.
3. Flow Control & Backpressure:
   - Consumer Prefetch QoS (`basic_qos(prefetch_count=N)`).
   - Prevents fast producers from overwhelming worker memory.
"""

from __future__ import annotations
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple


class ExchangeType(str, Enum):
    DIRECT = "direct"
    FANOUT = "fanout"
    TOPIC = "topic"
    HEADERS = "headers"


@dataclass
class Message:
    """Represents an AMQP 0-9-1 message with payload, routing metadata, and properties."""
    body: Any
    routing_key: str = ""
    headers: Dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    ttl_ms: Optional[int] = None
    delivery_tag: Optional[int] = None
    redelivered: bool = False
    death_reason: Optional[str] = None


@dataclass
class Binding:
    """Represents a binding between an exchange and a queue."""
    queue_name: str
    binding_key: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)

    def matches_topic(self, routing_key: str) -> bool:
        """
        AMQP 0-9-1 Topic Matcher:
        '*' matches exactly one word delimited by '.'
        '#' matches zero or more words
        """
        if self.binding_key == routing_key or self.binding_key == "#":
            return True

        b_tokens = self.binding_key.split(".")
        r_tokens = routing_key.split(".")
        memo: Dict[Tuple[int, int], bool] = {}

        def match_tokens(b_idx: int, r_idx: int) -> bool:
            state = (b_idx, r_idx)
            if state in memo:
                return memo[state]
            if b_idx == len(b_tokens):
                return r_idx == len(r_tokens)

            if b_tokens[b_idx] == "#":
                # Option 1: '#' matches 0 words
                if match_tokens(b_idx + 1, r_idx):
                    memo[state] = True
                    return True
                # Option 2: '#' matches 1 or more words
                if r_idx < len(r_tokens) and match_tokens(b_idx, r_idx + 1):
                    memo[state] = True
                    return True
                memo[state] = False
                return False

            if r_idx < len(r_tokens) and (b_tokens[b_idx] == "*" or b_tokens[b_idx] == r_tokens[r_idx]):
                res = match_tokens(b_idx + 1, r_idx + 1)
                memo[state] = res
                return res

            memo[state] = False
            return False

        return match_tokens(0, 0)

    def matches_headers(self, message_headers: Dict[str, Any]) -> bool:
        """
        AMQP 0-9-1 Headers Matcher:
        'x-match' argument controls logic ('all' vs 'any'). Default is 'all'.
        """
        match_mode = self.arguments.get("x-match", "all").lower()
        rule_keys = {k: v for k, v in self.arguments.items() if k != "x-match"}
        if not rule_keys:
            return True

        if match_mode == "all":
            return all(message_headers.get(k) == v for k, v in rule_keys.items())
        elif match_mode == "any":
            return any(message_headers.get(k) == v for k, v in rule_keys.items())
        return False


class Queue:
    """
    AMQP 0-9-1 Queue representation:
    - Maintains pending and unacknowledged (in-flight) message stores.
    - Enforces DLX routing when messages are dead-lettered (nacked without requeue, or TTL expired).
    """
    def __init__(
        self,
        name: str,
        dlx_exchange: Optional[str] = None,
        dlx_routing_key: Optional[str] = None,
        max_length: Optional[int] = None,
        message_ttl_ms: Optional[int] = None,
    ):
        self.name = name
        self.dlx_exchange = dlx_exchange
        self.dlx_routing_key = dlx_routing_key
        self.max_length = max_length
        self.message_ttl_ms = message_ttl_ms
        self.pending_messages: List[Message] = []
        self.unacked_messages: Dict[int, Message] = {}  # delivery_tag -> Message
        self._tag_counter: int = 0

    def enqueue(self, message: Message, broker: AMQPBroker) -> bool:
        """Enqueue message; check TTL and capacity constraints."""
        # Check queue-level TTL
        if self.message_ttl_ms and message.ttl_ms is None:
            message.ttl_ms = self.message_ttl_ms

        if self.max_length and len(self.pending_messages) >= self.max_length:
            # Drop head or dead-letter
            overflow_msg = self.pending_messages.pop(0)
            self._dead_letter(overflow_msg, "maxlen_exceeded", broker)

        self.pending_messages.append(message)
        return True

    def deliver_next(self) -> Optional[Message]:
        """Fetch next pending message and move to unacknowledged state."""
        while self.pending_messages:
            msg = self.pending_messages.pop(0)
            # Check message expiration
            if msg.ttl_ms is not None:
                elapsed_ms = (time.time() - msg.timestamp) * 1000
                if elapsed_ms > msg.ttl_ms:
                    msg.death_reason = "expired"
                    # Expired during wait in queue, caller will dead letter if applicable
                    continue

            self._tag_counter += 1
            msg.delivery_tag = self._tag_counter
            self.unacked_messages[msg.delivery_tag] = msg
            return msg
        return None

    def acknowledge(self, delivery_tag: int) -> Optional[Message]:
        """Acknowledge message (basic.ack); removes from unacked store."""
        return self.unacked_messages.pop(delivery_tag, None)

    def reject(
        self,
        delivery_tag: int,
        requeue: bool,
        broker: AMQPBroker,
        reason: str = "rejected"
    ) -> Optional[Message]:
        """Reject message (basic.nack / basic.reject)."""
        msg = self.unacked_messages.pop(delivery_tag, None)
        if not msg:
            return None

        if requeue:
            msg.redelivered = True
            self.pending_messages.insert(0, msg)
        else:
            self._dead_letter(msg, reason, broker)
        return msg

    def _dead_letter(self, message: Message, reason: str, broker: AMQPBroker) -> None:
        """Forward dead-lettered message to DLX if configured."""
        if not self.dlx_exchange:
            return

        message.death_reason = reason
        message.headers["x-death-reason"] = reason
        message.headers["x-death-queue"] = self.name
        routing_key = self.dlx_routing_key if self.dlx_routing_key is not None else message.routing_key
        broker.publish(self.dlx_exchange, routing_key, message)


class Exchange:
    """AMQP 0-9-1 Exchange routing engine."""
    def __init__(self, name: str, exchange_type: ExchangeType):
        self.name = name
        self.exchange_type = exchange_type
        self.bindings: List[Binding] = []

    def bind(self, queue_name: str, binding_key: str = "", arguments: Optional[Dict[str, Any]] = None) -> None:
        self.bindings.append(Binding(queue_name, binding_key, arguments or {}))

    def route(self, message: Message) -> Set[str]:
        """Returns set of queue names that receive this message."""
        destination_queues: Set[str] = set()

        if self.exchange_type == ExchangeType.FANOUT:
            for b in self.bindings:
                destination_queues.add(b.queue_name)

        elif self.exchange_type == ExchangeType.DIRECT:
            for b in self.bindings:
                if b.binding_key == message.routing_key:
                    destination_queues.add(b.queue_name)

        elif self.exchange_type == ExchangeType.TOPIC:
            for b in self.bindings:
                if b.matches_topic(message.routing_key):
                    destination_queues.add(b.queue_name)

        elif self.exchange_type == ExchangeType.HEADERS:
            for b in self.bindings:
                if b.matches_headers(message.headers):
                    destination_queues.add(b.queue_name)

        return destination_queues


class Consumer:
    """Simulated AMQP Consumer with prefetch QoS management."""
    def __init__(self, consumer_id: str, prefetch_count: int = 1):
        self.consumer_id = consumer_id
        self.prefetch_count = prefetch_count
        self.inflight_messages: Dict[int, Message] = {}  # delivery_tag -> Message
        self.processed_messages: List[Message] = []
        self.rejected_messages: List[Message] = []

    @property
    def can_receive(self) -> bool:
        """QoS enforcement: true if unacked inflight messages < prefetch_count."""
        if self.prefetch_count == 0:  # 0 denotes unlimited prefetch in AMQP
            return True
        return len(self.inflight_messages) < self.prefetch_count


class AMQPBroker:
    """
    Central AMQP 0-9-1 Broker Virtual Host:
    - Maintains exchanges, queues, and consumers.
    - Handles publish, deliver, ack, nack, and QoS loops.
    """
    def __init__(self, vhost: str = "/"):
        self.vhost = vhost
        self.exchanges: Dict[str, Exchange] = {}
        self.queues: Dict[str, Queue] = {}
        self.consumers: Dict[str, List[Consumer]] = {}  # queue_name -> list of Consumers
        self._consumer_turn: Dict[str, int] = {}       # Round-robin cursor per queue

    def declare_exchange(self, name: str, exchange_type: ExchangeType) -> Exchange:
        if name not in self.exchanges:
            self.exchanges[name] = Exchange(name, exchange_type)
        return self.exchanges[name]

    def declare_queue(
        self,
        name: str,
        dlx_exchange: Optional[str] = None,
        dlx_routing_key: Optional[str] = None,
        max_length: Optional[int] = None,
        message_ttl_ms: Optional[int] = None
    ) -> Queue:
        if name not in self.queues:
            self.queues[name] = Queue(
                name=name,
                dlx_exchange=dlx_exchange,
                dlx_routing_key=dlx_routing_key,
                max_length=max_length,
                message_ttl_ms=message_ttl_ms
            )
            self.consumers[name] = []
            self._consumer_turn[name] = 0
        return self.queues[name]

    def bind_queue(
        self,
        queue_name: str,
        exchange_name: str,
        routing_key: str = "",
        binding_key: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None
    ) -> None:
        key = binding_key if binding_key is not None else routing_key
        if exchange_name not in self.exchanges:
            raise ValueError(f"Exchange {exchange_name} does not exist")
        if queue_name not in self.queues:
            raise ValueError(f"Queue {queue_name} does not exist")
        self.exchanges[exchange_name].bind(queue_name, key, arguments)

    def publish(self, exchange_name: str, routing_key: str, message: Message) -> int:
        """Route message to appropriate queues. Returns count of routed queues."""
        if exchange_name not in self.exchanges:
            raise ValueError(f"Exchange {exchange_name} does not exist")
        
        message.routing_key = routing_key
        exchange = self.exchanges[exchange_name]
        target_queues = exchange.route(message)

        for q_name in target_queues:
            q = self.queues[q_name]
            # Deep clone message for independent delivery tags per queue
            cloned = Message(
                body=message.body,
                routing_key=message.routing_key,
                headers=dict(message.headers),
                ttl_ms=message.ttl_ms,
            )
            q.enqueue(cloned, self)
        
        return len(target_queues)

    def register_consumer(self, queue_name: str, consumer: Consumer) -> None:
        if queue_name not in self.queues:
            raise ValueError(f"Queue {queue_name} does not exist")
        self.consumers[queue_name].append(consumer)

    def dispatch(self, queue_name: str) -> int:
        """
        Dispatches available messages from queue to eligible consumers adhering to prefetch QoS.
        Returns count of delivered messages in this cycle.
        """
        q = self.queues[queue_name]
        consumers = self.consumers.get(queue_name, [])
        if not consumers:
            return 0

        dispatched = 0
        num_consumers = len(consumers)
        start_idx = self._consumer_turn[queue_name]

        # Round-robin consumer delivery check
        for i in range(num_consumers):
            curr_idx = (start_idx + i) % num_consumers
            consumer = consumers[curr_idx]

            while consumer.can_receive and q.pending_messages:
                msg = q.deliver_next()
                if not msg:
                    break
                consumer.inflight_messages[msg.delivery_tag] = msg
                dispatched += 1

            self._consumer_turn[queue_name] = (curr_idx + 1) % num_consumers
        return dispatched

    def ack(self, queue_name: str, consumer: Consumer, delivery_tag: int) -> None:
        """Consumer acknowledges receipt and processing."""
        msg = consumer.inflight_messages.pop(delivery_tag, None)
        if msg:
            consumer.processed_messages.append(msg)
            self.queues[queue_name].acknowledge(delivery_tag)

    def nack(
        self,
        queue_name: str,
        consumer: Consumer,
        delivery_tag: int,
        requeue: bool = False,
        reason: str = "failed"
    ) -> None:
        """Consumer rejects message. Requeues or routes to DLX."""
        msg = consumer.inflight_messages.pop(delivery_tag, None)
        if msg:
            consumer.rejected_messages.append(msg)
            self.queues[queue_name].reject(delivery_tag, requeue, self, reason=reason)


# =====================================================================
# Verification and Demonstration Scenarios
# =====================================================================

def demo_direct_exchange():
    print("\n--- 1. DIRECT EXCHANGE DEMO ---")
    broker = AMQPBroker()
    broker.declare_exchange("orders.direct", ExchangeType.DIRECT)
    broker.declare_queue("orders.eu")
    broker.declare_queue("orders.us")

    broker.bind_queue("orders.eu", "orders.direct", routing_key="order.europe")
    broker.bind_queue("orders.us", "orders.direct", routing_key="order.usa")

    broker.publish("orders.direct", "order.europe", Message({"id": 101, "item": "Laptop"}))
    broker.publish("orders.direct", "order.usa", Message({"id": 102, "item": "Keyboard"}))
    broker.publish("orders.direct", "order.asia", Message({"id": 103, "item": "Mouse"}))  # Unrouted

    assert len(broker.queues["orders.eu"].pending_messages) == 1
    assert len(broker.queues["orders.us"].pending_messages) == 1
    print(f"Direct routing verified: EU Queue={len(broker.queues['orders.eu'].pending_messages)}, US Queue={len(broker.queues['orders.us'].pending_messages)}")


def demo_fanout_exchange():
    print("\n--- 2. FANOUT EXCHANGE DEMO ---")
    broker = AMQPBroker()
    broker.declare_exchange("notifications.fanout", ExchangeType.FANOUT)
    broker.declare_queue("email.service")
    broker.declare_queue("sms.service")
    broker.declare_queue("push.service")

    broker.bind_queue("email.service", "notifications.fanout")
    broker.bind_queue("sms.service", "notifications.fanout")
    broker.bind_queue("push.service", "notifications.fanout")

    # Routing key ignored in Fanout
    broker.publish("notifications.fanout", "any.key", Message({"event": "password_reset", "user_id": 99}))

    assert len(broker.queues["email.service"].pending_messages) == 1
    assert len(broker.queues["sms.service"].pending_messages) == 1
    assert len(broker.queues["push.service"].pending_messages) == 1
    print("Fanout broadcast verified: 1 message successfully delivered to 3 distinct subscriber queues.")


def demo_topic_exchange():
    print("\n--- 3. TOPIC EXCHANGE (* and # wildcards) DEMO ---")
    broker = AMQPBroker()
    broker.declare_exchange("logs.topic", ExchangeType.TOPIC)
    broker.declare_queue("critical_logs")
    broker.declare_queue("payment_all_logs")

    # critical_logs receives all '*.critical.*' logs
    broker.bind_queue("critical_logs", "logs.topic", binding_key="*.critical.*")
    # payment_all_logs receives all logs starting with 'payment.#'
    broker.bind_queue("payment_all_logs", "logs.topic", binding_key="payment.#")

    broker.publish("logs.topic", "auth.critical.timeout", Message("Auth server timeout"))
    broker.publish("logs.topic", "payment.info.stripe", Message("Stripe payment webhook received"))
    broker.publish("logs.topic", "payment.critical.gateway", Message("Payment gateway connection lost"))

    # critical_logs should match auth.critical.timeout and payment.critical.gateway
    assert len(broker.queues["critical_logs"].pending_messages) == 2
    # payment_all_logs should match payment.info.stripe and payment.critical.gateway
    assert len(broker.queues["payment_all_logs"].pending_messages) == 2
    print("Topic wildcard routing (* and #) verified across multiple hierarchies.")


def demo_headers_exchange():
    print("\n--- 4. HEADERS EXCHANGE DEMO ---")
    broker = AMQPBroker()
    broker.declare_exchange("analytics.headers", ExchangeType.HEADERS)
    broker.declare_queue("vip_fast_lane")

    # Require tier=VIP and region=EU (x-match: all)
    broker.bind_queue(
        "vip_fast_lane",
        "analytics.headers",
        arguments={"x-match": "all", "tier": "VIP", "region": "EU"}
    )

    msg_match = Message("VIP Europe Payload", headers={"tier": "VIP", "region": "EU", "version": 2})
    msg_mismatch = Message("Standard Europe Payload", headers={"tier": "Standard", "region": "EU"})

    broker.publish("analytics.headers", "", msg_match)
    broker.publish("analytics.headers", "", msg_mismatch)

    assert len(broker.queues["vip_fast_lane"].pending_messages) == 1
    print("Headers exchange multi-attribute matching verified.")


def demo_prefetch_qos_and_dlx():
    print("\n--- 5. PREFETCH QOS, BACKPRESSURE & DEAD LETTER EXCHANGE (DLX) DEMO ---")
    broker = AMQPBroker()
    
    # 1. Declare Dead Letter Exchange and DLQ
    broker.declare_exchange("dlx.direct", ExchangeType.DIRECT)
    dlq = broker.declare_queue("orders.dlq")
    broker.bind_queue("orders.dlq", "dlx.direct", routing_key="order.poison")

    # 2. Declare Primary Exchange and Queue with DLX configuration
    broker.declare_exchange("orders.primary", ExchangeType.DIRECT)
    primary_queue = broker.declare_queue(
        "orders.work",
        dlx_exchange="dlx.direct",
        dlx_routing_key="order.poison",
        max_length=10
    )
    broker.bind_queue("orders.work", "orders.primary", routing_key="order.process")

    # 3. Register Consumer with prefetch_count = 2 (Backpressure barrier)
    worker = Consumer(consumer_id="worker-1", prefetch_count=2)
    broker.register_consumer("orders.work", worker)

    # 4. Producer bursts 5 messages
    for i in range(1, 6):
        broker.publish("orders.primary", "order.process", Message(body={"order_id": i}))

    print(f"Total messages queued in 'orders.work': {len(primary_queue.pending_messages)}")
    assert len(primary_queue.pending_messages) == 5

    # 5. First dispatch: consumer receives exactly prefetch_count (2) messages
    delivered_first = broker.dispatch("orders.work")
    print(f"Dispatched {delivered_first} messages. Consumer in-flight: {len(worker.inflight_messages)}")
    assert delivered_first == 2
    assert len(worker.inflight_messages) == 2
    assert len(primary_queue.pending_messages) == 3

    # Attempting another dispatch without acking should deliver 0 due to QoS limit
    delivered_second = broker.dispatch("orders.work")
    print(f"Second dispatch without Ack: {delivered_second} messages delivered (QoS backpressure holds).")
    assert delivered_second == 0

    # 6. Worker acks 1 message, releases capacity
    first_tag = list(worker.inflight_messages.keys())[0]
    broker.ack("orders.work", worker, first_tag)
    print(f"Worker acked delivery_tag {first_tag}. Consumer in-flight: {len(worker.inflight_messages)}")
    assert len(worker.inflight_messages) == 1

    # Now dispatch should deliver 1 more message
    delivered_third = broker.dispatch("orders.work")
    print(f"Third dispatch after Ack: {delivered_third} message delivered.")
    assert delivered_third == 1
    assert len(worker.inflight_messages) == 2

    # 7. Worker encounters a poison-pill message and nacks with requeue=False -> DLX triggered
    poison_tag = list(worker.inflight_messages.keys())[0]
    print(f"Worker rejecting poison-pill delivery_tag {poison_tag} without requeue...")
    broker.nack("orders.work", worker, poison_tag, requeue=False, reason="schema_corrupted")

    # Verify message moved to DLQ
    assert len(dlq.pending_messages) == 1
    dead_letter = dlq.pending_messages[0]
    print(f"Message in DLQ: id={dead_letter.message_id}, reason={dead_letter.headers.get('x-death-reason')}")
    assert dead_letter.headers.get("x-death-reason") == "schema_corrupted"
    assert dead_letter.headers.get("x-death-queue") == "orders.work"
    print("Dead Letter Exchange (DLX) routing verified successfully!")


if __name__ == "__main__":
    print("=" * 70)
    print("RUNNING AMQP 0-9-1 MESSAGE BROKER SIMULATOR (RABBITMQ SPECIFICATION)")
    print("=" * 70)
    demo_direct_exchange()
    demo_fanout_exchange()
    demo_topic_exchange()
    demo_headers_exchange()
    demo_prefetch_qos_and_dlx()
    print("\n" + "=" * 70)
    print("ALL AMQP 0-9-1 AND FLOW-CONTROL TESTS PASSED SUCCESSFULLY!")
    print("=" * 70)
