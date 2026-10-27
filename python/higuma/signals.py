"""Dependency-free, synchronous lifecycle notifications, not Blinker interoperability."""

from __future__ import annotations

import inspect
import weakref
from contextlib import contextmanager
from threading import RLock

ANY = object()
_NONE_SENDER = object()


def _identity(receiver):
    if inspect.ismethod(receiver):
        return (id(receiver.__self__), id(receiver.__func__))
    return (id(receiver),)


class Signal:
    def __init__(self, name: str):
        self.name = name
        self._subscriptions = []
        self._lock = RLock()

    def connect(self, receiver, sender=ANY, weak=True):
        if not callable(receiver) or inspect.iscoroutinefunction(receiver):
            raise TypeError("signal receivers must be synchronous callables")
        identity = _identity(receiver)
        if weak:
            reference = (
                weakref.WeakMethod(receiver)
                if inspect.ismethod(receiver)
                else weakref.ref(receiver)
            )
        else:
            reference = lambda: receiver
        if sender is None:
            owner = lambda: _NONE_SENDER
        elif sender is ANY:
            owner = lambda: ANY
        else:
            try:
                owner = weakref.ref(sender)
            except TypeError:
                owner = lambda: sender
        with self._lock:
            self._remove(receiver, sender)
            self._subscriptions.append((identity, reference, owner))
        return receiver

    def disconnect(self, receiver, sender=ANY):
        return self._remove(receiver, sender, all_senders=sender is ANY)

    def _remove(self, receiver, sender, *, all_senders=False):
        identity = _identity(receiver)
        sender = _NONE_SENDER if sender is None else sender
        with self._lock:
            previous = len(self._subscriptions)
            self._subscriptions[:] = [
                entry
                for entry in self._subscriptions
                if not (entry[0] == identity and (all_senders or entry[2]() is sender))
            ]
            return len(self._subscriptions) != previous

    def receivers_for(self, sender):
        sender = _NONE_SENDER if sender is None else sender
        with self._lock:
            self._subscriptions[:] = [
                entry
                for entry in self._subscriptions
                if entry[1]() is not None and entry[2]() is not None
            ]
            receivers = [
                entry[1]()
                for entry in self._subscriptions
                if entry[2]() is ANY or entry[2]() is sender
            ]
        seen = set()
        for receiver in receivers:
            if receiver is not None and _identity(receiver) not in seen:
                seen.add(_identity(receiver))
                yield receiver

    def has_receivers_for(self, sender):
        return next(self.receivers_for(sender), None) is not None

    def send(self, sender, **values):
        results = []
        for receiver in self.receivers_for(sender):
            result = receiver(sender, **values)
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):
                    result.close()
                raise TypeError("signal receivers must not return awaitables")
            results.append((receiver, result))
        return results

    def connect_via(self, sender, weak=False):
        return lambda receiver: self.connect(receiver, sender, weak)

    @contextmanager
    def connected_to(self, receiver, sender=ANY):
        existing = any(
            _identity(item) == _identity(receiver) for item in self.receivers_for(sender)
        )
        if not existing:
            self.connect(receiver, sender, weak=False)
        try:
            yield
        finally:
            if not existing:
                self._remove(receiver, sender)


request_started = Signal("request-started")
request_finished = Signal("request-finished")
request_tearing_down = Signal("request-tearing-down")
got_request_exception = Signal("got-request-exception")
appcontext_pushed = Signal("appcontext-pushed")
appcontext_tearing_down = Signal("appcontext-tearing-down")
appcontext_popped = Signal("appcontext-popped")
before_render_template = Signal("before-render-template")
template_rendered = Signal("template-rendered")
message_flashed = Signal("message-flashed")


def has_lifecycle_receivers(app):
    return any(
        signal.has_receivers_for(app)
        for signal in (
            request_started,
            request_finished,
            request_tearing_down,
            got_request_exception,
            appcontext_pushed,
            appcontext_tearing_down,
            appcontext_popped,
            before_render_template,
            template_rendered,
            message_flashed,
        )
    )
