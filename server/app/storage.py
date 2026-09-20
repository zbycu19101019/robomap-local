from __future__ import annotations

import json
import os
import threading
import sqlite3
from pathlib import Path

from .models import MapDocument, Vec2, Segment, Furniture, Dock


class MapStore:
    def __init__(self) -> None:
        data_dir = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        self.path = data_dir / "map.json"
        self._lock = threading.RLock()
        if not self.path.exists():
            self.save(self._demo_map(), bump_revision=False)

    def _demo_map(self) -> MapDocument:
        walls = [
            Segment(id="demo-w1", start=Vec2(x=0, y=0), end=Vec2(x=5.8, y=0), height=2.5),
            Segment(id="demo-w2", start=Vec2(x=5.8, y=0), end=Vec2(x=5.8, y=4.2), height=2.5),
            Segment(id="demo-w3", start=Vec2(x=5.8, y=4.2), end=Vec2(x=0, y=4.2), height=2.5),
            Segment(id="demo-w4", start=Vec2(x=0, y=4.2), end=Vec2(x=0, y=0), height=2.5),
            Segment(id="demo-w5", start=Vec2(x=3.45, y=0), end=Vec2(x=3.45, y=2.45), height=2.5),
        ]
        furniture = [
            Furniture(id="demo-sofa", category="sofa-demo", center=Vec2(x=1.4, y=1.0), width=2.0, depth=0.85),
            Furniture(id="demo-table", category="table-demo", center=Vec2(x=4.45, y=3.15), width=1.1, depth=0.75),
        ]
        return MapDocument(
            name="Mapa demonstracyjna",
            source="demo",
            walls=walls,
            furniture=furniture,
            dock=Dock(x=0.45, y=3.7, rotation=0),
        )

    def load(self) -> MapDocument:
        with self._lock:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return MapDocument.model_validate(raw)

    def archive(self, doc):
        with sqlite3.connect(self.path.parent / 'map-history.db') as db:
            db.execute('CREATE TABLE IF NOT EXISTS versions(revision INTEGER PRIMARY KEY, created TEXT DEFAULT CURRENT_TIMESTAMP, document TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO versions(revision, document) VALUES (?, ?)', (doc.revision, doc.model_dump_json()))

    def versions(self):
        with self._lock:
            self.archive(self.load())
            with sqlite3.connect(self.path.parent / 'map-history.db') as db:
                return [{'revision': r, 'created': t} for r,t in db.execute('SELECT revision,created FROM versions ORDER BY revision DESC LIMIT 100')]

    def historical(self, revision):
        self.versions()
        with sqlite3.connect(self.path.parent / 'map-history.db') as db:
            row=db.execute('SELECT document FROM versions WHERE revision=?',(revision,)).fetchone()
        if row is None:
            raise KeyError(revision)
        return MapDocument.model_validate_json(row[0])

    def save(self, doc: MapDocument, *, bump_revision: bool = True) -> MapDocument:
        with self._lock:
            current_revision = 0
            if self.path.exists():
                try:
                    current_revision = json.loads(self.path.read_text(encoding="utf-8")).get("revision", 0)
                except Exception:
                    current_revision = 0
            if bump_revision:
                if doc.revision != current_revision:
                    raise ValueError('Mapa została zmieniona na innym urządzeniu. Odśwież ją przed ponownym zapisem.')
                self.archive(self.load())
                doc = doc.model_copy(update={"revision": current_revision + 1})
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(
                json.dumps(doc.model_dump(mode="json"), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.path)
            return doc
