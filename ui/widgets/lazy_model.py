from typing import List, Dict
from PyQt6.QtCore import QAbstractListModel, Qt, QModelIndex, pyqtSignal, QMimeData, QUrl
from PyQt6.QtGui import QPixmap, QColor

from database.models import Asset

class AssetListModel(QAbstractListModel):
    # Signals
    loadRequested = pyqtSignal(int, str) # asset_id, path

    def __init__(self, parent=None):
        super().__init__(parent)
        self.assets: List[Asset] = []
        self.thumbnails: Dict[int, QPixmap] = {} # asset_id -> QPixmap mapping
        self.loading: Dict[int, bool] = {}       # Track loading state to prevent redundant disk ops
        
        # Placeholder for thumbnails that are currently loading
        self._placeholder = QPixmap(200, 200)
        self._placeholder.fill(QColor("#2d2d2d")) # Modern dark grey UI element

    def setAssets(self, new_assets: List[Asset]):
        self.beginResetModel()
        self.assets = new_assets
        self.thumbnails.clear()
        self.loading.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
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
                return self.thumbnails[asset.id]
            
            if asset.id not in self.loading and asset.thumbnail_path:
                # Trigger background loading
                self.loading[asset.id] = True
                self.loadRequested.emit(asset.id, asset.thumbnail_path)
            
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

    def setImage(self, asset_id: int, pixmap: QPixmap):
        """Called by the main thread when QImage has been decoded and passed back."""
        self.thumbnails[asset_id] = pixmap
        if asset_id in self.loading:
            del self.loading[asset_id]
            
        # Notify the view that data has changed
        for row, asset in enumerate(self.assets):
            if asset.id == asset_id:
                idx = self.index(row, 0)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
                break
