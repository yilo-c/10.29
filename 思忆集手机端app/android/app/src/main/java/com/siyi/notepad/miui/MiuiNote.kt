package com.siyi.notepad.miui

data class MiuiNote(
    val id: Long,
    val title: String?,
    val snippet: String?,
    val content: String?,
    val folderId: Long = 0,
    val folderName: String? = null,
    val type: Int = 0,       // 0=note, 1=todo, 2=checklist
    val createdDate: Long = 0L,
    val modifiedDate: Long = 0L
) {
    val isTodo: Boolean get() = type == 1
    val isChecklist: Boolean get() = type == 2
    val displayTitle: String get() = (title ?: snippet ?: content?.take(80) ?: "无标题").trim()
    val fullText: String get() = content ?: snippet ?: ""
}
