"""
数据库备份与恢复系统

自动备份数据库，支持恢复和归档
"""

import os
import shutil
import sqlite3
import tarfile
import json
from pathlib import Path
from datetime import datetime, timedelta
import re
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class BackupInfo:
    """备份信息"""
    filepath: str
    timestamp: str
    size_bytes: int
    compressed: bool = False
    metadata: Optional[Dict[str, Any]] = None


class DatabaseBackupService:
    """数据库备份服务"""
    
    def __init__(
        self,
        db_path: str,
        backup_dir: str = "data/backups",
        retention_days: int = 30
    ):
        """
        初始化备份服务
        
        Args:
            db_path: 数据库文件路径
            backup_dir: 备份目录
            retention_days: 保留天数
        """
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.retention_days = retention_days
        
        # 确保备份目录存在
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_table_name(name: str) -> bool:
        """Validate table name against a whitelist pattern. Only allows alphanumeric and underscores."""
        return bool(re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name))

    @staticmethod
    def _validate_column_name(name: str) -> bool:
        """Validate column name against a whitelist pattern."""
        return bool(re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_]*', name))
    
    def create_backup(
        self,
        compressed: bool = True,
        metadata: Optional[Dict[str, Any]] = None
    ) -> BackupInfo:
        """
        创建数据库备份
        
        Args:
            compressed: 是否压缩
            metadata: 额外的元数据
            
        Returns:
            BackupInfo对象
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        if compressed:
            backup_filename = f"backup_{timestamp}.tar.gz"
            backup_path = self.backup_dir / backup_filename
            
            # 创建压缩备份
            with tarfile.open(backup_path, "w:gz") as tar:
                tar.add(self.db_path, arcname=self.db_path.name)
                
                # 添加元数据
                if metadata:
                    metadata_path = self.backup_dir / f"metadata_{timestamp}.json"
                    with open(metadata_path, 'w') as f:
                        json.dump(metadata, f, indent=2)
                    tar.add(metadata_path, arcname="metadata.json")
                    metadata_path.unlink()  # 删除临时文件
        
        else:
            backup_filename = f"backup_{timestamp}.sqlite3"
            backup_path = self.backup_dir / backup_filename
            
            # 使用SQLite的备份API（在线备份）
            self._online_backup(backup_path)
            
            # 保存元数据
            if metadata:
                metadata_path = backup_path.with_suffix('.json')
                with open(metadata_path, 'w') as f:
                    json.dump(metadata, f, indent=2)
        
        size = backup_path.stat().st_size
        
        logger.info(f"备份已创建: {backup_path} ({size / 1024:.1f} KB)")
        
        return BackupInfo(
            filepath=str(backup_path),
            timestamp=timestamp,
            size_bytes=size,
            compressed=compressed,
            metadata=metadata
        )
    
    def _online_backup(self, backup_path: Path):
        """在线备份数据库"""
        # 源数据库
        src_conn = sqlite3.connect(self.db_path)
        
        # 目标数据库
        dst_conn = sqlite3.connect(backup_path)
        
        try:
            # 使用备份API
            src_conn.backup(dst_conn)
        finally:
            src_conn.close()
            dst_conn.close()
    
    def restore_backup(
        self,
        backup_path: str,
        target_path: Optional[str] = None
    ):
        """
        恢复备份
        
        Args:
            backup_path: 备份文件路径
            target_path: 目标路径，如果为None则恢复到原位置
        """
        backup_file = Path(backup_path)
        
        if not backup_file.exists():
            raise FileNotFoundError(f"备份文件不存在: {backup_path}")
        
        target = Path(target_path) if target_path else self.db_path
        
        # 备份当前数据库（安全措施）
        if target.exists():
            safety_backup = target.with_suffix(
                f".before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
            )
            shutil.copy2(target, safety_backup)
            logger.info(f"安全备份已创建: {safety_backup}")
        
        # 恢复
        if backup_file.suffix == '.gz':
            # 解压缩备份
            with tarfile.open(backup_file, "r:gz") as tar:
                tar.extractall(path=target.parent)
        else:
            # 直接复制
            shutil.copy2(backup_file, target)
        
        logger.info(f"备份已恢复: {backup_path} -> {target}")
    
    def list_backups(self) -> List[BackupInfo]:
        """列出所有备份"""
        backups = []
        
        for filepath in sorted(self.backup_dir.glob("backup_*")):
            if filepath.suffix in ['.sqlite3', '.gz']:
                # 提取时间戳
                timestamp = filepath.stem.replace('backup_', '')
                
                # 检查元数据
                metadata_path = filepath.with_suffix('.json')
                metadata = None
                if metadata_path.exists():
                    with open(metadata_path) as f:
                        metadata = json.load(f)
                
                backups.append(BackupInfo(
                    filepath=str(filepath),
                    timestamp=timestamp,
                    size_bytes=filepath.stat().st_size,
                    compressed=filepath.suffix == '.gz',
                    metadata=metadata
                ))
        
        return backups
    
    def cleanup_old_backups(self):
        """清理过期备份"""
        cutoff_date = datetime.now() - timedelta(days=self.retention_days)
        deleted_count = 0
        
        for backup in self.list_backups():
            try:
                # 解析时间戳
                backup_time = datetime.strptime(backup.timestamp, "%Y%m%d_%H%M%S")
                
                if backup_time < cutoff_date:
                    # 删除备份文件
                    Path(backup.filepath).unlink()
                    
                    # 删除元数据文件（如果存在）
                    metadata_path = Path(backup.filepath).with_suffix('.json')
                    if metadata_path.exists():
                        metadata_path.unlink()
                    
                    deleted_count += 1
                    logger.info(f"已删除过期备份: {backup.filepath}")
            
            except Exception as e:
                logger.warning(f"清理备份失败 {backup.filepath}: {e}")
        
        logger.info(f"清理完成，删除 {deleted_count} 个过期备份")
        
        return deleted_count
    
    def export_data(
        self,
        output_path: str,
        tables: Optional[List[str]] = None,
        format: str = "json"
    ):
        """
        导出数据到JSON/CSV
        
        Args:
            output_path: 输出文件路径
            tables: 要导出的表，如果为None则导出所有表
            format: 导出格式 (json, csv)
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 获取所有表名
        if tables is None:
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """)
            tables = [row[0] for row in cursor.fetchall()]
        
        export_data = {}
        
        for table in tables:
            if not self._validate_table_name(table):
                logger.warning("skipping export: invalid table name %r", table)
                continue
            cursor.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()

            # 获取列名
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [col[1] for col in cursor.fetchall()]
            
            # 转换为字典列表
            export_data[table] = [
                dict(zip(columns, row)) for row in rows
            ]
        
        conn.close()
        
        # 保存
        if format == "json":
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False, default=str)
        
        elif format == "csv":
            import csv
            
            # 为每个表创建一个CSV文件
            output_dir = Path(output_path).parent
            output_stem = Path(output_path).stem
            
            for table, data in export_data.items():
                csv_path = output_dir / f"{output_stem}_{table}.csv"
                
                if data:
                    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
                        writer = csv.DictWriter(f, fieldnames=data[0].keys())
                        writer.writeheader()
                        writer.writerows(data)
        
        logger.info(f"数据已导出: {output_path}")
    
    def import_data(self, import_path: str):
        """
        从JSON导入数据
        
        Args:
            import_path: 导入文件路径
        """
        with open(import_path, 'r', encoding='utf-8') as f:
            import_data = json.load(f)
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for table, rows in import_data.items():
            if not rows:
                continue
            if not self._validate_table_name(table):
                logger.warning("skipping import: invalid table name %r", table)
                continue

            # 插入数据
            columns = rows[0].keys()
            # Validate column names
            if not all(self._validate_column_name(c) for c in columns):
                logger.warning("skipping import: invalid column names in table %r", table)
                continue
            placeholders = ', '.join(['?' for _ in columns])
            
            for row in rows:
                values = [row[col] for col in columns]
                cursor.execute(
                    f"INSERT OR REPLACE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    values
                )
        
        conn.commit()
        conn.close()
        
        logger.info(f"数据已导入: {import_path}")


def schedule_auto_backup(db_path: str, backup_dir: str = "data/backups"):
    """
    设置自动备份（配合定时任务使用）
    
    Args:
        db_path: 数据库路径
        backup_dir: 备份目录
    """
    service = DatabaseBackupService(db_path, backup_dir)
    
    # 创建备份
    backup_info = service.create_backup(
        compressed=True,
        metadata={
            "type": "scheduled",
            "timestamp": datetime.now().isoformat()
        }
    )
    
    # 清理过期备份
    service.cleanup_old_backups()
    
    logger.info(f"自动备份完成: {backup_info.filepath}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="数据库备份管理")
    parser.add_argument("--db", default="data/automation_hub.sqlite3", help="数据库路径")
    parser.add_argument("--backup-dir", default="data/backups", help="备份目录")
    
    subparsers = parser.add_subparsers(dest="command")
    
    # 创建备份
    backup_parser = subparsers.add_parser("backup", help="创建备份")
    backup_parser.add_argument("--no-compress", action="store_true", help="不压缩")
    
    # 列出备份
    subparsers.add_parser("list", help="列出备份")
    
    # 恢复备份
    restore_parser = subparsers.add_parser("restore", help="恢复备份")
    restore_parser.add_argument("backup_file", help="备份文件路径")
    
    # 清理过期备份
    subparsers.add_parser("cleanup", help="清理过期备份")
    
    # 导出数据
    export_parser = subparsers.add_parser("export", help="导出数据")
    export_parser.add_argument("output", help="输出文件路径")
    export_parser.add_argument("--format", choices=["json", "csv"], default="json")
    
    args = parser.parse_args()
    
    service = DatabaseBackupService(args.db, args.backup_dir)
    
    if args.command == "backup":
        info = service.create_backup(compressed=not args.no_compress)
        print(f"✅ 备份已创建: {info.filepath} ({info.size_bytes / 1024:.1f} KB)")
    
    elif args.command == "list":
        backups = service.list_backups()
        print(f"\n找到 {len(backups)} 个备份:\n")
        for backup in backups:
            size_mb = backup.size_bytes / 1024 / 1024
            compressed = "压缩" if backup.compressed else "未压缩"
            print(f"  - {backup.timestamp}: {size_mb:.2f} MB ({compressed})")
            print(f"    {backup.filepath}")
        print()
    
    elif args.command == "restore":
        service.restore_backup(args.backup_file)
        print(f"✅ 备份已恢复: {args.backup_file}")
    
    elif args.command == "cleanup":
        count = service.cleanup_old_backups()
        print(f"✅ 已删除 {count} 个过期备份")
    
    elif args.command == "export":
        service.export_data(args.output, format=args.format)
        print(f"✅ 数据已导出: {args.output}")
    
    else:
        parser.print_help()
