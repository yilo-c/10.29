/* 思忆集 PWA — IndexedDB 离线队列 */
class OfflineQueue {
  constructor() {
    this.dbName = 'siyiji-offline';
    this.dbVersion = 1;
    this.storeName = 'pending';
    this.db = null;
  }

  async open() {
    if (this.db) return;
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(this.dbName, this.dbVersion);
      req.onupgradeneeded = (e) => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains(this.storeName)) {
          db.createObjectStore(this.storeName, { keyPath: 'id', autoIncrement: true });
        }
      };
      req.onsuccess = (e) => { this.db = e.target.result; resolve(); };
      req.onerror = () => reject(req.error);
    });
  }

  async enqueue(method, path, body) {
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(this.storeName, 'readwrite');
      tx.objectStore(this.storeName).add({ method, path, body, created_at: new Date().toISOString() });
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async dequeueAll() {
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(this.storeName, 'readonly');
      const req = tx.objectStore(this.storeName).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async clear() {
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(this.storeName, 'readwrite');
      tx.objectStore(this.storeName).clear();
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async remove(id) {
    await this.open();
    return new Promise((resolve, reject) => {
      const tx = this.db.transaction(this.storeName, 'readwrite');
      tx.objectStore(this.storeName).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async replay(apiClient) {
    const items = await this.dequeueAll();
    if (!items.length) return { replayed: 0, failed: 0 };

    let replayed = 0, failed = 0;
    for (const item of items) {
      try {
        await apiClient.request(item.method, item.path, item.body);
        await this.remove(item.id);
        replayed++;
      } catch {
        failed++;
      }
    }
    return { replayed, failed };
  }

  get pendingCount() {
    return this.open().then(() => {
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readonly');
        const req = tx.objectStore(this.storeName).count();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    });
  }
}

window.offlineQueue = new OfflineQueue();
