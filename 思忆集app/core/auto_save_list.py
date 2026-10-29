"""A list subclass that auto-persists to a callback on every mutation.

``AutoSaveList`` wraps a plain Python list and calls a user-provided
save callback after every mutating operation (append, pop, clear,
extend, __setitem__, __delitem__, sort, reverse).  This eliminates
the "write to memory, remember to save" fragility pattern.

Usage::

    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: print(f"saved {len(items)} items"))
    data.append(4)          # -> saved 4 items
    data.pop()              # -> saved 3 items
    data.clear()            # -> saved 0 items

The callback receives a **copy** of the internal list so the callee
can do whatever it wants (serialise, pass to a repository, …) without
holding a reference back into the live list.

``replace(items)`` is provided as an explicit single-shot replacement
alternative to ``copy.clear(); copy.extend(items)`` (which would fire
*two* save events).
"""

from __future__ import annotations

import collections.abc
import typing
from collections.abc import Callable

if typing.TYPE_CHECKING:
    pass

_SaveCallback = Callable[[list], None] | None


class AutoSaveList(collections.abc.MutableSequence):
    """A mutable sequence that auto-saves after every mutation.

    Thread-safety is *not* guaranteed — the caller is expected to
    mutate from a single event loop / thread (which is already the
    convention in PyQt applications).
    """

    def __init__(self, iterable: typing.Iterable = ()) -> None:
        self._data: list = list(iterable)
        self._save_callback: _SaveCallback = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_save_callback(self, callback: _SaveCallback) -> None:
        """Register a callable invoked after every mutation.

        The callback receives a **shallow copy** of the internal list::

            callback(list(self))

        Pass ``None`` to disable auto-saving.
        """
        self._save_callback = callback

    def replace(self, items: typing.Iterable) -> None:
        """Atomically replace all items (single save event)."""
        self._data[:] = list(items)
        self._save()

    # ------------------------------------------------------------------
    # MutableSequence abstract methods  (all auto-save)
    # ------------------------------------------------------------------

    def __getitem__(self, index: int | slice) -> typing.Any:
        return self._data[index]

    def __setitem__(self, index: int | slice, value: typing.Any) -> None:
        self._data[index] = value
        self._save()

    def __delitem__(self, index: int | slice) -> None:
        del self._data[index]
        self._save()

    def __len__(self) -> int:
        return len(self._data)

    def insert(self, index: int, value: typing.Any) -> None:
        self._data.insert(index, value)
        self._save()

    # ------------------------------------------------------------------
    # Override mutating methods for explicit save semantics
    # ------------------------------------------------------------------

    def append(self, value: typing.Any) -> None:
        self._data.append(value)
        self._save()

    def extend(self, values: typing.Iterable) -> None:
        self._data.extend(values)
        self._save()

    def pop(self, index: int = -1) -> typing.Any:
        value = self._data.pop(index)
        self._save()
        return value

    def clear(self) -> None:
        self._data.clear()
        self._save()

    def sort(self, *, key: typing.Callable | None = None,
             reverse: bool = False) -> None:
        self._data.sort(key=key, reverse=reverse)
        self._save()

    def reverse(self) -> None:
        self._data.reverse()
        self._save()

    def remove(self, value: typing.Any) -> None:
        self._data.remove(value)
        self._save()

    # ------------------------------------------------------------------
    # sequence passthrough (non-mutating, no save)
    # ------------------------------------------------------------------

    def __contains__(self, value: typing.Any) -> bool:
        return value in self._data

    def __iter__(self) -> typing.Iterator:
        return iter(self._data)

    def __reversed__(self) -> typing.Iterator:
        return reversed(self._data)

    def __repr__(self) -> str:
        return f"AutoSaveList({self._data!r})"

    def count(self, value: typing.Any) -> int:
        return self._data.count(value)

    def index(self, value: typing.Any, *args: typing.Any) -> int:
        return self._data.index(value, *args)

    def copy(self) -> list:
        return self._data.copy()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _save(self) -> None:
        if self._save_callback is not None:
            self._save_callback(list(self._data))
