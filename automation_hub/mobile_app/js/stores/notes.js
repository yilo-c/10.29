/* 思忆集 PWA — 笔记本地缓存 (IndexedDB) */
(function () {
  'use strict';

  class NotesCache {
    constructor() {
      this.dbName = 'siyiji-notes';
      this.dbVersion = 1;
      this.storeName = 'notes';
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

    /** Get all cached notes */
    async getAll() {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readonly');
        const req = tx.objectStore(this.storeName).getAll();
        req.onsuccess = () => {
          const notes = req.result || [];
          notes.sort((a, b) => (b.updated_at || '').localeCompare(a.updated_at || ''));
          resolve(notes);
        };
        req.onerror = () => reject(req.error);
      });
    }

    /** Get single note by remote_id */
    async getNote(remoteId) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readonly');
        const index = tx.objectStore(this.storeName).index('remote_id');
        const req = index.get(remoteId);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
      });
    }

    /** Replace entire cache with fresh data */
    async setAll(notes) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readwrite');
        const store = tx.objectStore(this.storeName);
        store.clear();
        notes.forEach(n => {
          try { store.put(n); } catch (e) { /* skip invalid */ }
        });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    /** Update or insert a single note */
    async updateNote(note) {
      await this.open();
      return new Promise((resolve, reject) => {
        const tx = this.db.transaction(this.storeName, 'readwrite');
        tx.objectStore(this.storeName).put({ ...note, remote_id: note.remote_id || note.id });
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
    }

    /** Remove a note by remote_id */
    async removeNote(remoteId) {
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

    /** Get count of cached notes */
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

  window.notesCache = new NotesCache();

})();
