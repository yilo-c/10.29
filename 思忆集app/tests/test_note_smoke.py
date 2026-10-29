"""Note smoke tests: create, update, delete, close/reopen preserves data."""
from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from data.local_storage import LocalDataStore
from repositories._note import LocalPersonalNoteRepository


def test_note_create_and_reopen(tmp_path):
    """Create a note, close/reopen '"note and content survive."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalNoteRepository(store)

    note_id = repo.add_note({
        "title": "测试绗旇",
        "content": "这是涓€条测试内瀹",
        "category": "涓汉",
    })
    notes = repo.list_notes()
    assert any(n["id"] == note_id for n in notes)

    # Simulate close
    saved_id = note_id
    del repo
    del store

    # Reopen
    store2 = LocalDataStore(base_dir=str(tmp_path))
    repo2 = LocalPersonalNoteRepository(store2)

    reloaded = repo2.list_notes()
    assert len(reloaded) >= 1
    match = [n for n in reloaded if n["id"] == saved_id]
    assert len(match) == 1
    assert match[0]["title"] == "测试绗旇"
    assert match[0]["content"] == "这是涓€条测试内瀹"


def test_note_update_and_delete(tmp_path):
    """Update content survives; soft delete hides note; hard delete removes."""
    store = LocalDataStore(base_dir=str(tmp_path))
    repo = LocalPersonalNoteRepository(store)

    note_id = repo.add_note({"title": "原标棰", "content": "原内瀹"})

    repo.update_note(note_id, {"title": "新标棰", "content": "新内瀹"})
    reloaded = repo.get_note(note_id) if hasattr(repo, 'get_note') else None
    if reloaded is None:
        notes = repo.list_notes()
        reloaded = next(n for n in notes if n["id"] == note_id)
    assert reloaded["title"] == "新标棰"
    assert reloaded["content"] == "新内瀹"

    # Soft delete
    repo.delete_note(note_id, soft=True)
    assert note_id not in [n["id"] for n in repo.list_notes(include_deleted=False)]
    # Hard delete
    repo.delete_note(note_id, soft=False)
    assert note_id not in [n["id"] for n in repo.list_notes(include_deleted=True)]
