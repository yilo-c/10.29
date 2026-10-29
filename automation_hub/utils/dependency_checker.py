"""
工具依赖检查器

自动检测系统是否安装了必要的外部依赖（ripgrep、git、docker等）
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, List, Dict
from enum import Enum


class DependencyStatus(Enum):
    """依赖状态"""
    INSTALLED = "installed"
    MISSING = "missing"
    VERSION_MISMATCH = "version_mismatch"


@dataclass
class DependencyInfo:
    """依赖信息"""
    name: str
    command: str
    required: bool
    min_version: Optional[str] = None
    install_hint: Optional[str] = None
    status: DependencyStatus = DependencyStatus.MISSING
    installed_version: Optional[str] = None
    error: Optional[str] = None


class DependencyChecker:
    """依赖检查器"""
    
    # 预定义的依赖项
    DEPENDENCIES = [
        DependencyInfo(
            name="ripgrep",
            command="rg",
            required=False,
            install_hint="Windows: choco install ripgrep | Linux: apt install ripgrep | Mac: brew install ripgrep"
        ),
        DependencyInfo(
            name="git",
            command="git",
            required=True,
            min_version="2.0.0",
            install_hint="https://git-scm.com/downloads"
        ),
        DependencyInfo(
            name="docker",
            command="docker",
            required=False,
            min_version="20.0.0",
            install_hint="https://docs.docker.com/get-docker/"
        ),
        DependencyInfo(
            name="pytest",
            command="pytest",
            required=False,
            install_hint="pip install pytest"
        ),
        DependencyInfo(
            name="ruff",
            command="ruff",
            required=False,
            install_hint="pip install ruff"
        ),
    ]
    
    def __init__(self):
        self.results: Dict[str, DependencyInfo] = {}
    
    def check_command_exists(self, command: str) -> bool:
        """检查命令是否存在"""
        return shutil.which(command) is not None
    
    def get_command_version(self, command: str) -> Optional[str]:
        """获取命令版本"""
        try:
            # 常见的版本命令尝试顺序
            version_flags = ["--version", "-version", "-v", "version"]
            
            for flag in version_flags:
                try:
                    result = subprocess.run(
                        [command, flag],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    
                    if result.returncode == 0:
                        # 从输出中提取版本号（简单实现）
                        output = result.stdout or result.stderr
                        lines = output.split('\n')
                        if lines:
                            return lines[0].strip()
                    
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    continue
            
            return None
        
        except Exception as e:
            return None
    
    def compare_versions(self, installed: str, required: str) -> bool:
        """
        简单的版本比较（仅支持数字版本）
        
        Returns:
            True if installed >= required
        """
        try:
            # 提取数字版本号
            import re
            installed_match = re.search(r'(\d+)\.(\d+)\.(\d+)', installed)
            required_match = re.search(r'(\d+)\.(\d+)\.(\d+)', required)
            
            if not installed_match or not required_match:
                return True  # 无法比较，假设满足
            
            installed_parts = [int(x) for x in installed_match.groups()]
            required_parts = [int(x) for x in required_match.groups()]
            
            return installed_parts >= required_parts
        
        except Exception:
            return True  # 比较失败，假设满足
    
    def check_dependency(self, dep: DependencyInfo) -> DependencyInfo:
        """检查单个依赖"""
        # 检查命令是否存在
        if not self.check_command_exists(dep.command):
            dep.status = DependencyStatus.MISSING
            dep.error = f"Command '{dep.command}' not found"
            return dep
        
        # 获取版本
        version = self.get_command_version(dep.command)
        dep.installed_version = version
        
        # 检查版本要求
        if dep.min_version and version:
            if not self.compare_versions(version, dep.min_version):
                dep.status = DependencyStatus.VERSION_MISMATCH
                dep.error = f"Version {version} < {dep.min_version}"
                return dep
        
        dep.status = DependencyStatus.INSTALLED
        return dep
    
    def check_all(self) -> Dict[str, DependencyInfo]:
        """检查所有依赖"""
        for dep in self.DEPENDENCIES:
            checked = self.check_dependency(dep)
            self.results[dep.name] = checked
        
        return self.results
    
    def check_specific(self, names: List[str]) -> Dict[str, DependencyInfo]:
        """检查特定依赖"""
        for dep in self.DEPENDENCIES:
            if dep.name in names:
                checked = self.check_dependency(dep)
                self.results[dep.name] = checked
        
        return self.results
    
    def get_missing_required(self) -> List[DependencyInfo]:
        """获取缺失的必需依赖"""
        return [
            dep for dep in self.results.values()
            if dep.required and dep.status != DependencyStatus.INSTALLED
        ]
    
    def get_missing_optional(self) -> List[DependencyInfo]:
        """获取缺失的可选依赖"""
        return [
            dep for dep in self.results.values()
            if not dep.required and dep.status != DependencyStatus.INSTALLED
        ]
    
    def is_ready(self) -> bool:
        """检查系统是否准备就绪（所有必需依赖已安装）"""
        return len(self.get_missing_required()) == 0
    
    def print_report(self, verbose: bool = False):
        """打印依赖检查报告"""
        print("\n" + "="*60)
        print("  工具依赖检查报告")
        print("="*60)
        
        # 分类统计
        installed = [d for d in self.results.values() if d.status == DependencyStatus.INSTALLED]
        missing = [d for d in self.results.values() if d.status == DependencyStatus.MISSING]
        version_mismatch = [d for d in self.results.values() if d.status == DependencyStatus.VERSION_MISMATCH]
        
        print(f"\n✅ 已安装: {len(installed)}")
        print(f"❌ 缺失:   {len(missing)}")
        print(f"⚠️  版本不匹配: {len(version_mismatch)}\n")
        
        # 详细信息
        if verbose or missing or version_mismatch:
            print("\n详细信息:")
            print("-" * 60)
            
            for dep in self.results.values():
                status_icon = {
                    DependencyStatus.INSTALLED: "✅",
                    DependencyStatus.MISSING: "❌",
                    DependencyStatus.VERSION_MISMATCH: "⚠️"
                }[dep.status]
                
                required_mark = "🔴 必需" if dep.required else "⚪ 可选"
                
                print(f"\n{status_icon} {dep.name} [{required_mark}]")
                print(f"   命令: {dep.command}")
                
                if dep.status == DependencyStatus.INSTALLED:
                    print(f"   版本: {dep.installed_version or 'Unknown'}")
                    if dep.min_version:
                        print(f"   要求: >= {dep.min_version}")
                
                elif dep.status == DependencyStatus.MISSING:
                    print(f"   状态: 未安装")
                    if dep.install_hint:
                        print(f"   安装: {dep.install_hint}")
                
                elif dep.status == DependencyStatus.VERSION_MISMATCH:
                    print(f"   当前版本: {dep.installed_version}")
                    print(f"   要求版本: >= {dep.min_version}")
                    if dep.install_hint:
                        print(f"   升级提示: {dep.install_hint}")
        
        # 总结
        print("\n" + "="*60)
        if self.is_ready():
            print("✅ 系统准备就绪！所有必需依赖已安装")
        else:
            print("❌ 系统未就绪！请安装缺失的必需依赖：")
            for dep in self.get_missing_required():
                print(f"   - {dep.name}: {dep.install_hint}")
        
        if self.get_missing_optional():
            print("\n⚠️  以下可选依赖未安装（某些功能可能不可用）：")
            for dep in self.get_missing_optional():
                print(f"   - {dep.name}: {dep.install_hint}")
        
        print("="*60 + "\n")


def main():
    """命令行入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="检查工具依赖")
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示详细信息"
    )
    parser.add_argument(
        "--check",
        nargs="+",
        help="只检查特定依赖"
    )
    
    args = parser.parse_args()
    
    checker = DependencyChecker()
    
    if args.check:
        checker.check_specific(args.check)
    else:
        checker.check_all()
    
    checker.print_report(verbose=args.verbose)
    
    # 如果有必需依赖缺失，返回非零退出码
    if not checker.is_ready():
        sys.exit(1)


if __name__ == "__main__":
    main()
