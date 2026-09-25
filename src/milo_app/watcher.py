"""Keeps the graph in step with the drop folder, on a background thread.

Every few seconds: find the bundle folders (bundle.json is written last, so only finished runs
count), fingerprint each one (file count, total size, newest change), and ingest any that are new
or changed. The drop folder is the source of truth the other way too: a run whose folder is gone
from it (and still gone at the next look, so a move or copy in progress never counts) is taken out of
the graph. A run moved to another folder is taken in again from there, never removed.
A bundle in a folder directly inside the drop folder (drop/<campaign>/<bundle_id>/) belongs
to that campaign; moving it into or out of one takes it in again with its new campaign. A fingerprint has to hold still across two looks before it is ingested, so a bundle
still being copied in from the VM is never read half-way.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from neo4j import Driver
from PySide6.QtCore import QObject, Signal

from milo_app import blind, graph
from milo_app.bundle import find_bundles, read_bundle

log = logging.getLogger(__name__)


def fingerprint(folder: Path) -> str:
    """Files, total size and newest change under `folder`. os.walk + os.stat rather than pathlib: the same
    answer at a fraction of the Python work (this runs over every file every few seconds). Each file is
    stat'ed itself, not read from the folder listing, whose sizes can lag while a file is being written."""
    count = size = newest = 0
    for root, _dirs, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            if not os.path.isfile(path):
                continue
            st = os.stat(path)
            count, size, newest = count + 1, size + st.st_size, max(newest, st.st_mtime_ns)
    return f"{count}:{size}:{newest}"


class Watcher(QObject):
    ingested = Signal(str, str)  # bundle_id, title
    run_failed = Signal(str, str, str)  # bundle_id, title, error: a run that arrived failed
    removed = Signal(list)  # titles of runs taken out because their folders left the drop folder
    failed = Signal(str, str)  # folder, error
    scanned = Signal(int)  # bundles seen in the drop folder

    def __init__(self, driver: Driver, drop_dir: Path, every: float) -> None:
        super().__init__()
        self.driver, self.drop_dir, self.every = driver, drop_dir, every
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = threading.Thread(target=self._loop, name="milo-watcher", daemon=True)
        self._known: dict[str, str] = {}  # source_path -> fingerprint already in the graph
        self._pending: dict[str, str] = {}  # source_path -> fingerprint seen once, waiting to settle
        self._broken: dict[str, str] = {}  # source_path -> fingerprint that failed; retried when it changes
        self._gone: set[str] = set()  # bundle_ids whose folder was missing at the last look

    def start(self) -> None:
        self._known = graph.fingerprints(self.driver)
        self._thread.start()

    def scan_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan()
            except Exception:  # noqa: BLE001 - one bad pass must not end the watching
                log.exception("scan failed")
            self._wake.wait(self.every)
            self._wake.clear()

    def _predict_blind(self, bundle):
        try:
            return blind.predict_new_run(self.driver, bundle)
        except Exception:  # noqa: BLE001
            log.exception("could not predict %s blind", bundle.bundle_id)
            return None

    def _forget_missing(self, folders: list[Path]) -> None:
        """Runs whose folder has left the drop folder, two looks in a row, leave the graph."""
        present = {str(f) for f in folders}
        drop = str(self.drop_dir.resolve())
        missing = {bundle_id for bundle_id, path in graph.sources(self.driver).items()
                   if path not in present and path.startswith(drop) and not Path(path).exists()}
        gone, self._gone = missing & self._gone, missing - self._gone
        if not gone:
            return
        titles = {s["bundle_id"]: s.get("title") or s["bundle_id"] for s in graph.list_simulations(self.driver)}
        for bundle_id in sorted(gone):
            graph.remove(self.driver, bundle_id)
            log.info("removed %s: its folder left the drop folder", bundle_id)
        self._known = graph.fingerprints(self.driver)
        self.removed.emit([titles.get(b, b) for b in sorted(gone)])

    def _scan(self) -> None:
        if not self.drop_dir.is_dir():
            self.scanned.emit(0)
            return
        folders = find_bundles(self.drop_dir)
        self.scanned.emit(len(folders))
        self._forget_missing(folders)
        for folder in folders:
            if self._stop.is_set():
                return
            key, fp = str(folder), fingerprint(folder)
            if self._known.get(key) == fp or self._broken.get(key) == fp:
                continue
            if self._pending.get(key) != fp:
                self._pending[key] = fp  # first sighting of this state: look again next pass
                continue
            del self._pending[key]
            try:
                bundle = read_bundle(folder)
                # drop/<campaign>/<bundle_id>/: the folder names the campaign (bundle.json has the last word)
                graph.apply_campaign_folder(bundle, graph.folder_campaign(self.drop_dir, folder, bundle.bundle_id))
                prediction = self._predict_blind(bundle)  # before it joins the data (ghost.md 7.1)
                graph.ingest(self.driver, bundle, fp)
            except Exception as exc:  # noqa: BLE001
                log.exception("could not ingest %s", folder)
                self._broken[key] = fp
                self.failed.emit(key, str(exc))
                continue
            try:
                blind.record(self.driver, bundle, prediction)
            except Exception:  # noqa: BLE001 - a failed test must never lose the run
                log.exception("blind test of %s failed", bundle.bundle_id)
            self._known[key] = fp
            log.info("ingested %s", bundle.bundle_id)
            self.ingested.emit(bundle.bundle_id, graph.title_of(bundle))
            outputs = {item.name: item.value for item in bundle.items_in("OUTPUT")}
            failed, error = graph.failure(bundle.manifest.get("status"), outputs.get("error"))
            if failed:
                self.run_failed.emit(bundle.bundle_id, graph.title_of(bundle),
                                     error or f"status {bundle.manifest.get('status')!r}")
