from pathlib import Path
import os


from app.storage import MapStore
from app.models import NoGoZone


def test_store_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    store = MapStore()
    doc = store.load()
    doc.no_go_zones.append(NoGoZone(id="z", name="test", x=1, y=1, width=1, height=1))
    saved = store.save(doc)
    loaded = store.load()
    assert saved.revision == loaded.revision
    assert any(z.id == "z" for z in loaded.no_go_zones)


def test_stale_map_cannot_overwrite_newer_revision(tmp_path, monkeypatch):
    import pytest
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    store = MapStore()
    first, second = store.load(), store.load()
    first.name = 'Nowsza mapa'
    store.save(first)
    with pytest.raises(ValueError):
        store.save(second)
    assert store.load().name == 'Nowsza mapa'


def test_invalid_map_geometry():
    import pytest
    from pydantic import ValidationError
    from app.models import Vec2, MapDocument
    with pytest.raises(ValidationError):
        Vec2(x=float('inf'), y=0)
    with pytest.raises(ValidationError):
        MapDocument(no_go_zones=[NoGoZone(id='a',x=0,y=0,width=1,height=1)]*2)
