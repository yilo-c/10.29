/* 思忆集 PWA — 任务本地缓存 (IndexedDB) */
(function () {
  'use strict';

  class TasksCache {
    constructor() {
      this.dbName = 'siyiji-tasks';
      this.dbVersion = 1;
      this.storeName = 'tasks';
      this.db = null;
    }

    async open() {
      if (this.db) return;
      return new Promise((resolve, reject) => {
        const req = indexedDB.open(this.dbName, this.dbVersion);
        req.onupgradeneeded = (e) => {
          const db = e.target.result;
          if (!db.objectStoreNames.contains(this.storeName)) {
            const store = db.createObjectStore(this.storeName, { keyPath: 'id' });
            store.createIndex('updated_at', 'updated_at', { unique: false });
            store.createIndex('remote_id', 'remote_id', { unique: true });
          }
        };
        req.onsuccess = (e) => { this.db = e.target.result; resolve(); };
        req.onerror = () => reject(req.error);
      });
    }

    /** Get all cached tasks */
    async getAll() {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readonly');
        const req = tx.objectStore(this.storeName).getAll();
        req.onsuccess = () => {
          const tasks = req.result || [];
          tasks.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
          resolve(tasks);
        };
        req.onerror = () => reject(req.error);
      });
    }

    /** Get single task by remote_id */
    async getTask(remoteId) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readonly');
        const index = tx.objectStore(this.storeName).index('remote_id');
        const req = index.get(remoteId);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
      });
    }

    /** Replace entire cache */
    async setAll(tasks) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readwrite');
        const store = tx.objectStore(this.storeName);
        store.clear();
        tasks.forEach(t => {
          try { store.put(t); } catch (e) { /* skip */ }
        });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    /** Update or insert a single task */
    async updateTask(task) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readwrite');
        tx.objectStore(this.storeName).put({ ...task, remote_id: task.remote_id || task.id });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    /** Remove a task by remote_id */
    async removeTask(remoteId) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readwrite');
        const store = tx.objectStore(this.storeName);
        const index = store.index('remote_id');
        const req = index.getKey(remoteId);
        req.onsuccess = () => {
          if (req.result) store.delete(req.result);
        };
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    /** Get count */
    async count() {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readonly');
        const req = tx.objectStore(this.storeName).count();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      });
    }
  }

  window.tasksCache = new TasksCache();

})();
