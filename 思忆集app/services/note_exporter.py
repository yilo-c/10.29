"""笔记导出服务 — 支持多种格式导出。

支持格式：
- Markdown (.md)
- HTML (.html)
- PDF (.pdf) — 需要额外依赖
- JSON (.json) — 结构化数据
"""
from __future__ import annotations

import base64
import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("siyi.services.note_exporter")


class ExportFormat(str, Enum):
    """导出格式"""
    MARKDOWN = "markdown"
    HTML = "html"
    PDF = "pdf"
    JSON = "json"
    PLAIN_TEXT = "text"


@dataclass
class NoteData:
    """笔记数据"""
    id: str
    title: str
    content: str
    folder: str | None = None
    tags: list[str] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class ExportResult:
    """导出结果"""
    content: bytes | str
    mime_type: str
    file_extension: str
    filename: str
    size_bytes: int


class MarkdownExporter:
    """Markdown 导出器"""
    
    def export(self, note: NoteData) -> ExportResult:
        """导出为 Markdown"""
        lines = []
        
        # 标题
        lines.append(f"# {note.title}")
        lines.append("")
        
        # 元信息
        meta_parts = []
        if note.folder:
            meta_parts.append(f"**文件夹**: {note.folder}")
        if note.tags:
            meta_parts.append(f"**标签**: {', '.join(f'#{t}' for t in note.tags)}")
        if note.created_at:
            meta_parts.append(f"**创建时间**: {note.created_at}")
        if note.updated_at:
            meta_parts.append(f"**更新时间**: {note.updated_at}")
        
        if meta_parts:
            lines.append(" | ".join(meta_parts))
            lines.append("")
            lines.append("---")
            lines.append("")
        
        # 内容
        lines.append(note.content)
        
        content = "\n".join(lines)
        content_bytes = content.encode("utf-8")
        
        return ExportResult(
            content=content,
            mime_type="text/markdown",
            file_extension=".md",
            filename=self._sanitize_filename(note.title) + ".md",
            size_bytes=len(content_bytes),
        )
    
    def _sanitize_filename(self, name: str) -> str:
        """清理文件名"""
        # 移除或替换非法字符
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        # 截断过长文件名
        if len(name) > 100:
            name = name[:100]
        return name.strip()


class HTMLExporter:
    """HTML 导出器"""
    
    def __init__(self, include_styles: bool = True):
        self.include_styles = include_styles
    
    def export(self, note: NoteData) -> ExportResult:
        """导出为 HTML"""
        # 简单的 Markdown 到 HTML 转换
        html_content = self._markdown_to_html(note.content)
        
        styles = ""
        if self.include_styles:
            styles = """
<style>
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
        line-height: 1.6;
        max-width: 800px;
        margin: 0 auto;
        padding: 20px;
        color: #333;
    }
    h1 { color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }
    .meta { color: #7f8c8d; font-size: 0.9em; margin-bottom: 20px; }
    .tags { margin-top: 20px; }
    .tag { display: inline-block; background: #3498db; color: white; padding: 2px 8px; border-radius: 3px; margin-right: 5px; font-size: 0.8em; }
    pre { background: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; }
    code { background: #f5f5f5; padding: 2px 5px; border-radius: 3px; }
    blockquote { border-left: 4px solid #3498db; margin: 0; padding-left: 15px; color: #555; }
</style>
"""
        
        # 构建完整 HTML
        full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(note.title)}</title>
    {styles}
</head>
<body>
    <h1>{html.escape(note.title)}</h1>
    <div class="meta">
"""
        
        if note.folder:
            full_html += f'        <span>馃搧 {html.escape(note.folder)}</span><br>\n'
        if note.tags:
            full_html += '        <div class="tags">'
            full_html += ''.join(f'<span class="tag">{html.escape(t)}</span>' for t in note.tags)
            full_html += '</div><br>\n'
        if note.created_at:
            full_html += f'        <span>馃搮 创建: {html.escape(note.created_at)}</span><br>\n'
        if note.updated_at:
            full_html += f'        <span>馃攧 更新: {html.escape(note.updated_at)}</span><br>\n'
        
        full_html += f"""    </div>
    <hr>
    <div class="content">
{html_content}
    </div>
</body>
</html>"""
        
        content_bytes = full_html.encode("utf-8")
        
        return ExportResult(
            content=full_html,
            mime_type="text/html",
            file_extension=".html",
            filename=self._sanitize_filename(note.title) + ".html",
            size_bytes=len(content_bytes),
        )
    
    def _markdown_to_html(self, content: str) -> str:
        """简单的 Markdown 到 HTML 转换。
        
        支持：标题、粗体、斜体、代码块、引用、列表
        """
        lines = content.split('\n')
        html_lines = []
        in_code_block = False
        in_list = False
        
        for line in lines:
            # 代码块
            if line.strip().startswith('```'):
                if in_code_block:
                    html_lines.append('</code></pre>')
                    in_code_block = False
                else:
                    lang = line.strip()[3:].strip()
                    html_lines.append(f'<pre><code class="language-{lang}">')
                    in_code_block = True
                continue
            
            if in_code_block:
                html_lines.append(html.escape(line))
                continue
            
            # 标题
            if line.startswith('# '):
                html_lines.append(f'<h1>{html.escape(line[2:])}</h1>')
            elif line.startswith('## '):
                html_lines.append(f'<h2>{html.escape(line[3:])}</h2>')
            elif line.startswith('### '):
                html_lines.append(f'<h3>{html.escape(line[4:])}</h3>')
            # 引用
            elif line.startswith('> '):
                html_lines.append(f'<blockquote>{html.escape(line[2:])}</blockquote>')
            # 无序列表
            elif line.startswith('- ') or line.startswith('* '):
                if not in_list:
                    html_lines.append('<ul>')
                    in_list = True
                html_lines.append(f'<li>{html.escape(line[2:])}</li>')
            # 有序列表
            elif re.match(r'^\d+\. ', line):
                if not in_list:
                    html_lines.append('<ol>')
                    in_list = True
                content = re.sub(r'^\d+\. ', '', line)
                html_lines.append(f'<li>{html.escape(content)}</li>')
            # 空行
            elif not line.strip():
                if in_list:
                    html_lines.append('</ul>' if '</ul>' not in ''.join(html_lines[-5:]) else '</ol>')
                    in_list = False
                html_lines.append('')
            # 普通段落
            else:
                # 处理行内格式
                processed = html.escape(line)
                # 粗体
                processed = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', processed)
                # 斜体
                processed = re.sub(r'\*(.+?)\*', r'<em>\1</em>', processed)
                # 行内代码
                processed = re.sub(r'`(.+?)`', r'<code>\1</code>', processed)
                # 链接
                processed = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', processed)
                
                html_lines.append(f'<p>{processed}</p>')
        
        if in_list:
            html_lines.append('</ul>')
        
        return '\n'.join(html_lines)
    
    def _sanitize_filename(self, name: str) -> str:
        """清理文件名"""
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        if len(name) > 100:
            name = name[:100]
        return name.strip()


class JSONExporter:
    """JSON 导出器"""
    
    def export(self, note: NoteData) -> ExportResult:
        """导出为 JSON"""
        data = {
            "id": note.id,
            "title": note.title,
            "content": note.content,
            "folder": note.folder,
            "tags": note.tags or [],
            "created_at": note.created_at,
            "updated_at": note.updated_at,
            "metadata": note.metadata or {},
            "exported_at": datetime.now().isoformat(),
        }
        
        content = json.dumps(data, ensure_ascii=False, indent=2)
        content_bytes = content.encode("utf-8")
        
        return ExportResult(
            content=content,
            mime_type="application/json",
            file_extension=".json",
            filename=f"note_{note.id}.json",
            size_bytes=len(content_bytes),
        )


class PlainTextExporter:
    """纯文本导出器"""
    
    def export(self, note: NoteData) -> ExportResult:
        """导出为纯文本"""
        lines = []
        lines.append(f"标题: {note.title}")
        lines.append("=" * 40)
        
        if note.folder:
            lines.append(f"文件夹: {note.folder}")
        if note.tags:
            lines.append(f"标签: {', '.join(note.tags)}")
        if note.created_at:
            lines.append(f"创建时间: {note.created_at}")
        
        lines.append("")
        lines.append("-" * 40)
        lines.append("")
        lines.append(note.content)
        
        content = "\n".join(lines)
        content_bytes = content.encode("utf-8")
        
        return ExportResult(
            content=content,
            mime_type="text/plain",
            file_extension=".txt",
            filename=f"{note.title}.txt",
            size_bytes=len(content_bytes),
        )


class NoteExporter:
    """笔记导出服务 — 统一入口。
    
    使用示例：
        exporter = NoteExporter()
        note = NoteData(
            id="abc123",
            title="我的笔记",
            content="# Hello\\n\\n这是一条笔记。",
            tags=["测试", "示例"],
        )
        result = exporter.export(note, ExportFormat.MARKDOWN)
        
        # 保存文件
        with open(result.filename, 'wb') as f:
            if isinstance(result.content, str):
                f.write(result.content.encode('utf-8'))
            else:
                f.write(result.content)
    """
    
    def __init__(self):
        self._exporters: dict[ExportFormat, Any] = {
            ExportFormat.MARKDOWN: MarkdownExporter(),
            ExportFormat.HTML: HTMLExporter(),
            ExportFormat.JSON: JSONExporter(),
            ExportFormat.PLAIN_TEXT: PlainTextExporter(),
        }
        # PDF 导出器需要额外依赖，延迟加载
        self._pdf_exporter: Any | None = None
    
    def export(self, note: NoteData, format: ExportFormat | str) -> ExportResult:
        """导出笔记。
        
        Args:
            note: 笔记数据
            format: 导出格式
        
        Returns:
            ExportResult: 导出结果
        """
        if isinstance(format, str):
            format = ExportFormat(format)
        
        if format == ExportFormat.PDF:
            return self._export_pdf(note)
        
        exporter = self._exporters.get(format)
        if exporter is None:
            raise ValueError(f"不支持的导出格式: {format}")
        
        return exporter.export(note)
    
    def _export_pdf(self, note: NoteData) -> ExportResult:
        """导出为 PDF。
        
        需要安装 weasyprint 或 reportlab。
        如果未安装，回退到 HTML 格式。
        """
        try:
            # 尝试使用 weasyprint
            from weasyprint import HTML as WeasyHTML  # type: ignore[import-untyped]
            
            # 先生成 HTML
            html_exporter = HTMLExporter(include_styles=True)
            html_result = html_exporter.export(note)
            
            # 转换为 PDF
            pdf_bytes = WeasyHTML(string=html_result.content).write_pdf()
            
            return ExportResult(
                content=pdf_bytes,
                mime_type="application/pdf",
                file_extension=".pdf",
                filename=self._sanitize_filename(note.title) + ".pdf",
                size_bytes=len(pdf_bytes),
            )
        except ImportError:
            logger.warning("weasyprint 未安装，PDF 导出回退到 HTML")
            # 回退到 HTML
            html_exporter = HTMLExporter(include_styles=True)
            result = html_exporter.export(note)
            result.file_extension = ".html"
            result.mime_type = "text/html"
            return result
    
    def _sanitize_filename(self, name: str) -> str:
        """清理文件名"""
        name = re.sub(r'[<>:"/\\|?*]', '_', name)
        if len(name) > 100:
            name = name[:100]
        return name.strip()
    
    def get_supported_formats(self) -> list[str]:
        """获取支持的导出格式列表"""
        formats = [f.value for f in ExportFormat]
        # 检查 PDF 支持
        try:
            import weasyprint  # type: ignore[import-untyped]
            formats.append("pdf")
        except ImportError:
            pass
        return formats
    
    def export_to_base64(self, note: NoteData, format: ExportFormat | str) -> dict[str, str]:
        """导出并返回 base64 编码内容（用于 JSON-RPC 响应）。
        
        Args:
            note: 笔记数据
            format: 导出格式
        
        Returns:
            包含 content (base64), mime_type, filename 的字典
        """
        result = self.export(note, format)
        
        if isinstance(result.content, str):
            content_bytes = result.content.encode("utf-8")
        else:
            content_bytes = result.content
        
        return {
            "content": base64.b64encode(content_bytes).decode("ascii"),
            "mime_type": result.mime_type,
            "filename": result.filename,
            "size_bytes": result.size_bytes,
        }


# 便捷函数
def export_note(
    note_data: dict[str, Any],
    format: str = "markdown",
) -> dict[str, str]:
    """导出笔记的便捷函数。
    
    Args:
        note_data: 笔记数据字典
        format: 导出格式
    
    Returns:
        包含导出结果的字典
    """
    note = NoteData(
        id=note_data.get("id", "unknown"),
        title=note_data.get("title", "未命名笔记"),
        content=note_data.get("content", ""),
        folder=note_data.get("folder"),
        tags=note_data.get("tags"),
        created_at=note_data.get("created_at"),
        updated_at=note_data.get("updated_at"),
        metadata=note_data.get("metadata"),
    )
    
    exporter = NoteExporter()
    return exporter.export_to_base64(note, format)