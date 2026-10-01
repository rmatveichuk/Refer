from collections import OrderedDict
from threading import Event
from math import isqrt
from typing import List, Dict
from PyQt6.QtCore import QAbstractListModel, Qt, QModelIndex, pyqtSignal, QMimeData, QUrl
from PyQt6.QtGui import QPixmap, QColor

from database.models import Asset

class AssetListModel(QAbstractListModel):
    # Signals
    loadRequested = pyqtSignal(int, str, int) # asset_id, path, generation
    loadCapacityAvailable = pyqtSignal()

    def __init__(self, parent=None, *, cache_budget_bytes=64 * 1024 * 1024,
                 max_active_loads=4, max_pending_loads=64):
        super().__init__(parent)
        self.assets: List[Asset] = []
        self.thumbnails: Dict[int, QPixmap] = OrderedDict()
        self.loading: Dict[int, bool] = {}       # Track loading state to prevent redundant disk ops
        self.cache_budget_bytes = max(0, int(cache_budget_bytes))
        self.cache_bytes = 0
        self.max_active_loads = max(1, int(max_active_loads))
        self.max_pending_loads = max(1, int(max_pending_loads))
        self.generation = 0
        self._cancellation = Event()
        self._active = set()
        self._pending = OrderedDict()
        self._queue_overflow = False
        self._rows = {}
        self._failed = set()
        self._evicted = set()
        self._visible = set()
        self._thumbnail_extent = 320
        
        # Placeholder for thumbnails that are currently loading
        self._placeholder = QPixmap(200, 200)
        self._placeholder.fill(QColor("#2d2d2d")) # Modern dark grey UI element

    def setAssets(self, new_assets: List[Asset]):
        old_paths = {asset.id: asset.thumbnail_path for asset in self.assets}
        self._cancellation.set()
        self._cancellation = Event()
        self.generation += 1
        self.beginResetModel()
        self.assets = list(new_assets)
        self._rows = {asset.id: row for row, asset in enumerate(self.assets)}
        # Keep unchanged thumbnails when another results page is added.
        new_paths = {asset.id: asset.thumbnail_path for asset in self.assets}
        for asset_id in list(self.thumbnails):
            if asset_id not in new_paths or old_paths.get(asset_id) != new_paths[asset_id]:
                self._remove_thumbnail(asset_id)
        self._pending.clear()
        self._queue_overflow = False
        self.loading.clear()
        self._failed.clear()
        self._evicted.clear()
        self._visible.clear()
        self._thumbnail_extent = 320
        self.endResetModel()

    def set_visible_assets(self, asset_ids):
        """Keep the viewport resident while retaining the fixed memory budget."""
        visible = set(asset_ids)
        if visible == self._visible:
            return
        self._visible = visible
        # Four bytes per pixel is the conservative square-image cost.
        self._thumbnail_extent = min(320, max(1, isqrt(self.cache_budget_bytes // (4 * max(1, len(visible))))))
        for aid, pixmap in list(self.thumbnails.items()):
            if max(pixmap.width(), pixmap.height()) > self._thumbnail_extent:
                self._remove_thumbnail(aid)
                scaled = pixmap.scaled(self._thumbnail_extent, self._thumbnail_extent,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
                self.thumbnails[aid] = scaled
                self.cache_bytes += self._pixmap_bytes(scaled)
        for aid in list(self._pending):
            if aid not in visible:
                self._pending.pop(aid)
                self.loading.pop(aid, None)
        self._evicted.difference_update(visible)

    def cancellation_for(self, generation):
        if generation == self.generation:
            return self._cancellation
        cancelled = Event()
        cancelled.set()
        return cancelled

    def retry_evicted(self):
        """Allow reload after scrolling/resizing, never from a load completion repaint."""
        self._evicted.clear()

    def cancel_loads(self):
        """Stop queued work on close or when this model is detached from its view."""
        self._cancellation.set()
        self.generation += 1
        self._pending.clear()
        self._queue_overflow = False
        self.loading.clear()

    @staticmethod
    def _pixmap_bytes(pixmap):
        return pixmap.width() * pixmap.height() * max(1, (pixmap.depth() + 7) // 8)

    def _remove_thumbnail(self, asset_id):
        pixmap = self.thumbnails.pop(asset_id)
        self.cache_bytes -= self._pixmap_bytes(pixmap)

    def _drain_pending(self):
        while self._pending and len(self._active) < self.max_active_loads:
            # Recent paints represent the current viewport after a fast scroll.
            asset_id, path = self._pending.popitem(last=True)
            self._active.add((self.generation, asset_id))
            self.loadRequested.emit(asset_id, path, self.generation)

    def rowCount(self, parent=None) -> int:
        if parent is not None and parent.isValid():
            return 0
        return len(self.assets)

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        default_flags = super().flags(index)
        if index.isValid():
            return default_flags | Qt.ItemFlag.ItemIsDragEnabled
        return default_flags

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < self.rowCount()):
            return None

        asset = self.assets[index.row()]

        # Icon/Image role
        if role == Qt.ItemDataRole.DecorationRole:
            if asset.id in self.thumbnails:
                self.thumbnails.move_to_end(asset.id)
                return self.thumbnails[asset.id]
            
            if (asset.id not in self.loading and asset.id not in self._failed
                    and asset.id not in self._evicted and asset.thumbnail_path
                    and not self._cancellation.is_set()):
                # Trigger background loading
                self.loading[asset.id] = True
                self._pending[asset.id] = asset.thumbnail_path
                while len(self._pending) > self.max_pending_loads:
                    discarded, _ = self._pending.popitem(last=False)
                    self.loading.pop(discarded, None)
                    self._queue_overflow = True
                self._drain_pending()
            
            return self._placeholder
        
        # We can implement DisplayRole to show text beneath images, or omit if we want purely image grids.
        # elif role == Qt.ItemDataRole.DisplayRole:
        #    return asset.original_url or f"Asset #{asset.id}"

        # Return full object for custom item delegates
        elif role == Qt.ItemDataRole.UserRole:
            return asset

        return None

    def mimeTypes(self) -> List[str]:
        return ["text/uri-list"]

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes: List[QModelIndex]) -> QMimeData:
        mime_data = QMimeData()
        urls = []
        for index in indexes:
            if index.isValid() and 0 <= index.row() < self.rowCount():
                asset = self.assets[index.row()]
                # Prefer local_path for full resolution, fallback to thumbnail
                path = asset.local_path if asset.local_path else asset.thumbnail_path
                if path:
                    import os
                    if os.path.exists(path):
                        urls.append(QUrl.fromLocalFile(os.path.abspath(path)))
        
        mime_data.setUrls(urls)
        return mime_data

    def completeImage(self, asset_id: int, generation: int, pixmap: QPixmap | None):
        """Finish a request on the GUI thread, ignoring replaced result sets."""
        request = (generation, asset_id)
        if request not in self._active:
            return
        self._active.remove(request)
        if generation == self.generation and asset_id in self._rows:
            self.loading.pop(asset_id, None)
            if pixmap is None or pixmap.isNull():
                self._failed.add(asset_id)
            else:
                if max(pixmap.width(), pixmap.height()) > self._thumbnail_extent:
                    pixmap = pixmap.scaled(self._thumbnail_extent, self._thumbnail_extent,
                                           Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)
                size = self._pixmap_bytes(pixmap)
                if size <= self.cache_budget_bytes:
                    if asset_id in self.thumbnails:
                        self._remove_thumbnail(asset_id)
                    while self.thumbnails and self.cache_bytes + size > self.cache_budget_bytes:
                        evicted_id = next((aid for aid in self.thumbnails if aid not in self._visible), None)
                        if evicted_id is None:
                            break
                        self._remove_thumbnail(evicted_id)
                        self._evicted.add(evicted_id)
                    if self.cache_bytes + size <= self.cache_budget_bytes:
                        self.thumbnails[asset_id] = pixmap
                        self.cache_bytes += size
                        idx = self.index(self._rows[asset_id], 0)
                        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                    else:
                        self._evicted.add(asset_id)
                else:
                    self._evicted.add(asset_id)
        self._drain_pending()
        if self._queue_overflow and len(self._pending) < self.max_pending_loads:
            self._queue_overflow = False
            # The view repaints its viewport, which retries discarded visible rows.
            self.loadCapacityAvailable.emit()

    def setImage(self, asset_id: int, pixmap: QPixmap):
        """Compatibility for current-generation GUI consumers."""
        self.completeImage(asset_id, self.generation, pixmap)
