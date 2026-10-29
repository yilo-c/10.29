"""Unit tests for AutoSaveList."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from core.auto_save_list import AutoSaveList


def test_append_triggers_save():
    saved = []
    data = AutoSaveList()
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.append(1)
    assert saved == [[1]]


def test_extend_triggers_save():
    saved = []
    data = AutoSaveList([1])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.extend([2, 3])
    assert saved == [[1, 2, 3]]


def test_pop_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    value = data.pop()
    assert value == 3
    assert saved == [[1, 2]]


def test_pop_index_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    value = data.pop(0)
    assert value == 1
    assert saved == [[2, 3]]


def test_clear_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.clear()
    assert saved == [[]]


def test_replace_triggers_one_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.replace([4, 5])
    assert saved == [[4, 5]]


def test_setitem_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data[1] = 99
    assert saved == [[1, 99, 3]]


def test_delitem_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    del data[1]
    assert saved == [[1, 3]]


def test_insert_triggers_save():
    saved = []
    data = AutoSaveList([1, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.insert(1, 2)
    assert saved == [[1, 2, 3]]


def test_sort_triggers_save():
    saved = []
    data = AutoSaveList([3, 1, 2])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.sort()
    assert saved == [[1, 2, 3]]


def test_reverse_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.reverse()
    assert saved == [[3, 2, 1]]


def test_remove_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3, 2])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.remove(2)
    assert saved == [[1, 3, 2]]


def test_callback_receives_copy():
    """Callback should receive a copy, not a reference to internal data."""
    internal_ref = []
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(lambda items: internal_ref.append(items))
    data.append(4)
    # Mutating the callback argument should NOT affect the AutoSaveList
    internal_ref[0].append(999)
    assert list(data) == [1, 2, 3, 4]


def test_no_callback_no_error():
    """Operations should work even without a callback set."""
    data = AutoSaveList([1, 2, 3])
    data.append(4)
    data.pop()
    data.clear()
    assert list(data) == []


def test_disable_callback():
    saved = []
    data = AutoSaveList([1])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data.append(2)
    data.set_save_callback(None)
    data.append(3)  # should NOT trigger callback
    assert saved == [[1, 2]]


def test_none_callback_does_not_crash():
    data = AutoSaveList([1, 2, 3])
    data.set_save_callback(None)
    data.append(4)
    data.pop()
    assert list(data) == [1, 2, 3]


def test_getitem():
    data = AutoSaveList([1, 2, 3])
    assert data[0] == 1
    assert data[-1] == 3
    assert data[1:3] == [2, 3]


def test_len():
    data = AutoSaveList([1, 2, 3])
    assert len(data) == 3
    data.append(4)
    assert len(data) == 4


def test_contains():
    data = AutoSaveList([1, 2, 3])
    assert 2 in data
    assert 99 not in data


def test_iter():
    data = AutoSaveList([1, 2, 3])
    assert list(iter(data)) == [1, 2, 3]


def test_reversed():
    data = AutoSaveList([1, 2, 3])
    assert list(reversed(data)) == [3, 2, 1]


def test_repr():
    data = AutoSaveList([1, 2, 3])
    assert repr(data) == "AutoSaveList([1, 2, 3])"


def test_count():
    data = AutoSaveList([1, 2, 2, 3])
    assert data.count(2) == 2
    assert data.count(99) == 0


def test_index():
    data = AutoSaveList([1, 2, 3])
    assert data.index(2) == 1


def test_copy():
    data = AutoSaveList([1, 2, 3])
    c = data.copy()
    assert c == [1, 2, 3]
    assert type(c) == list


def test_slice_setitem_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3, 4])
    data.set_save_callback(lambda items: saved.append(list(items)))
    data[1:3] = [99, 100]
    assert saved == [[1, 99, 100, 4]]


def test_slice_delitem_triggers_save():
    saved = []
    data = AutoSaveList([1, 2, 3, 4])
    data.set_save_callback(lambda items: saved.append(list(items)))
    del data[1:3]
    assert saved == [[1, 4]]


def test_init_from_iterable():
    data = AutoSaveList(range(3))
    assert list(data) == [0, 1, 2]


def test_sequence_interface():
    """Must satisfy MutableSequence ABC."""
    from collections.abc import MutableSequence
    assert isinstance(AutoSaveList(), MutableSequence)
