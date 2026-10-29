package com.siyi.notepad.miui

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteException
import java.io.File

/**
 * 读取 MIUI/HyperOS 系统便签数据库（需 root）。
 *
 * 移植自旧项目 com.siyi.app.miui.RedmiNoteReader。
 * 改动：去掉 Hilt @Inject @Singleton @ApplicationContext，改为普通构造函数。
 *
 * 数据库路径：/data/data/com.miui.notes/databases/notes.db
 *
 * 表结构在不同 MIUI/HyperOS 版本间变化，本读取器探测已知布局并优雅降级。
 */
class RedmiNoteReader(private val context: Context) {

    private val dbPath = "/data/data/com.miui.notes/databases/notes.db"
    private val cacheFile: File get() = File(context.cacheDir, "miui_notes_cache.db")

    data class ReadResult(
        val notes: List<MiuiNote>,
        val rootAvailable: Boolean,
        val dbFound: Boolean,
        val noteCount: Int,
        val error: String? = null
    )

    /** 尝试读取所有 MIUI 便签。 */
    fun readAllNotes(): ReadResult {
        if (!RootShell.isRootAvailable()) {
            return ReadResult(emptyList(), rootAvailable = false, dbFound = false, noteCount = 0, error = "设备未 Root")
        }

        // 拷贝数据库到 app 缓存目录（绕过 sandbox）
        val copy = RootShell.copyWithRoot(dbPath, cacheFile.absolutePath)
        if (!copy.isSuccess || !cacheFile.exists()) {
            return ReadResult(emptyList(), rootAvailable = true, dbFound = false, noteCount = 0,
                error = "小米笔记数据库未找到：${copy.stderr}")
        }

        return try {
            val db = SQLiteDatabase.openDatabase(cacheFile.absolutePath, null, SQLiteDatabase.OPEN_READONLY)
            try {
                val schema = detectSchemaVersion(db)
                val folders = loadFolders(db)
                val notes = loadNotes(db, schema, folders)
                ReadResult(notes, rootAvailable = true, dbFound = true, noteCount = notes.size)
            } finally {
                db.close()
            }
        } catch (e: SQLiteException) {
            ReadResult(emptyList(), rootAvailable = true, dbFound = true, noteCount = 0,
                error = "读取数据库失败：${e.message}")
        } finally {
            cacheFile.delete()
        }
    }

    private enum class SchemaVersion { MIUI_LEGACY, HYPER_OS }

    private fun detectSchemaVersion(db: SQLiteDatabase): SchemaVersion {
        val tables = queryTables(db)
        return if ("note_content" in tables) SchemaVersion.HYPER_OS else SchemaVersion.MIUI_LEGACY
    }

    private fun queryTables(db: SQLiteDatabase): Set<String> {
        val tables = mutableSetOf<String>()
        try {
            val cursor = db.rawQuery("SELECT name FROM sqlite_master WHERE type='table'", null)
            cursor.use { while (it.moveToNext()) tables.add(it.getString(0)) }
        } catch (_: Exception) {}
        return tables
    }

    private fun loadFolders(db: SQLiteDatabase): Map<Long, String> {
        val folders = mutableMapOf<Long, String>()
        try {
            val cursor = db.rawQuery("SELECT _id, title FROM folder", null)
            cursor.use { while (it.moveToNext()) folders[it.getLong(0)] = it.getString(1) ?: "默认" }
        } catch (_: Exception) {}
        return folders
    }

    private fun loadNotes(db: SQLiteDatabase, schema: SchemaVersion, folders: Map<Long, String>): List<MiuiNote> {
        val notes = mutableListOf<MiuiNote>()
        val columns = queryColumns(db, "note")
        val hasContent = "content" in columns
        val hasSnippet = "snippet" in columns

        val sql = buildString {
            append("SELECT _id, type, folder_id, created_date, modified_date")
            if (hasSnippet) append(", snippet")
            if (hasContent) append(", content, title") else append(", title")
            append(" FROM note WHERE type IN (0, 1, 2)")
        }

        try {
            val cursor = db.rawQuery(sql, null)
            cursor.use {
                while (it.moveToNext()) {
                    val noteId = it.getLong(0)
                    val type = it.getInt(1)
                    val folderId = it.getLong(2)
                    val created = it.getLong(3)
                    val modified = it.getLong(4)
                    val snippet = if (hasSnippet) it.getString(5) else null
                    val title: String?
                    val content: String?
                    if (hasContent) {
                        title = it.getString(6); content = it.getString(7)
                    } else {
                        title = it.getString(5); content = null
                    }
                    val fullContent = if (content == null && schema == SchemaVersion.HYPER_OS) {
                        loadNoteContent(db, noteId) ?: snippet
                    } else content ?: snippet
                    notes.add(MiuiNote(
                        id = noteId, title = title, snippet = snippet, content = fullContent,
                        folderId = folderId, folderName = folders[folderId], type = type,
                        createdDate = created, modifiedDate = modified
                    ))
                }
            }
        } catch (_: Exception) {}
        return notes
    }

    private fun loadNoteContent(db: SQLiteDatabase, noteId: Long): String? {
        return try {
            val cursor = db.rawQuery("SELECT content FROM note_content WHERE note_id = ? LIMIT 1",
                arrayOf(noteId.toString()))
            cursor.use { if (it.moveToFirst()) it.getString(0) else null }
        } catch (_: Exception) { null }
    }

    private fun queryColumns(db: SQLiteDatabase, table: String): Set<String> {
        val cols = mutableSetOf<String>()
        try {
            val cursor = db.rawQuery("PRAGMA table_info($table)", null)
            cursor.use { while (it.moveToNext()) cols.add(it.getString(1)) }
        } catch (_: Exception) {}
        return cols
    }
}
