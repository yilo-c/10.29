"""
Automation Hub CLI 工具

提供命令行接口来管理和执行工具
"""

import click
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import requests
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()

# 配置
DB_PATH = "data/automation_hub.sqlite3"
API_BASE = "http://localhost:8000"


def get_db():
    """获取数据库连接"""
    return sqlite3.connect(DB_PATH)


@click.group()
@click.version_option(version="1.0.0")
def cli():
    """Automation Hub - 自动化运维平台 CLI"""
    pass


# ==================== 工具管理 ====================

@cli.group()
def tools():
    """工具管理命令"""
    pass


@tools.command("list")
@click.option("--enabled-only", is_flag=True, help="只显示已启用的工具")
@click.option("--risk", type=click.Choice(["read", "exec_low", "exec_high", "write"]), help="按风险级别过滤")
def tools_list(enabled_only, risk):
    """列出所有工具"""
    conn = get_db()
    cursor = conn.cursor()
    
    query = "SELECT id, name, description, risk_level, enabled FROM tools WHERE 1=1"
    params = []
    
    if enabled_only:
        query += " AND enabled = 1"
    
    if risk:
        query += " AND risk_level = ?"
        params.append(risk)
    
    query += " ORDER BY name"
    
    cursor.execute(query, params)
    tools_data = cursor.fetchall()
    
    if not tools_data:
        console.print("[yellow]没有找到工具[/yellow]")
        return
    
    table = Table(title="工具列表", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("名称", style="green")
    table.add_column("描述", style="white")
    table.add_column("风险级别", style="yellow")
    table.add_column("状态", style="magenta")
    
    for tool in tools_data:
        tool_id, name, desc, risk_level, enabled = tool
        status = "✅ 已启用" if enabled else "❌ 已禁用"
        risk_emoji = {
            "read": "📖",
            "exec_low": "⚡",
            "exec_high": "⚠️",
            "write": "✏️"
        }.get(risk_level, "❓")
        
        table.add_row(
            tool_id,
            name,
            desc[:50] + "..." if desc and len(desc) > 50 else (desc or ""),
            f"{risk_emoji} {risk_level}",
            status
        )
    
    console.print(table)
    console.print(f"\n总计: {len(tools_data)} 个工具")
    conn.close()


@tools.command("show")
@click.argument("tool_id")
def tools_show(tool_id):
    """查看工具详情"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, name, description, risk_level, executor, 
               command_json, args_schema_json, timeout_seconds, enabled, created_at
        FROM tools WHERE id = ?
    """, (tool_id,))
    
    tool = cursor.fetchone()
    
    if not tool:
        console.print(f"[red]工具不存在: {tool_id}[/red]")
        return
    
    tool_id, name, desc, risk, executor, cmd_json, schema_json, timeout, enabled, created_at = tool
    
    # 显示详情
    console.print(Panel(f"[bold green]{name}[/bold green]", title="工具详情"))
    console.print(f"[cyan]ID:[/cyan] {tool_id}")
    console.print(f"[cyan]描述:[/cyan] {desc or '无'}")
    console.print(f"[cyan]风险级别:[/cyan] {risk}")
    console.print(f"[cyan]执行器:[/cyan] {executor}")
    console.print(f"[cyan]超时时间:[/cyan] {timeout}秒")
    console.print(f"[cyan]状态:[/cyan] {'✅ 已启用' if enabled else '❌ 已禁用'}")
    console.print(f"[cyan]创建时间:[/cyan] {created_at}")
    
    if cmd_json:
        console.print(f"\n[bold]命令模板:[/bold]")
        console.print(json.dumps(json.loads(cmd_json), indent=2))
    
    if schema_json:
        console.print(f"\n[bold]参数定义:[/bold]")
        console.print(json.dumps(json.loads(schema_json), indent=2))
    
    conn.close()


@tools.command("enable")
@click.argument("tool_id")
def tools_enable(tool_id):
    """启用工具"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE tools SET enabled = 1 WHERE id = ?", (tool_id,))
    
    if cursor.rowcount == 0:
        console.print(f"[red]工具不存在: {tool_id}[/red]")
    else:
        conn.commit()
        console.print(f"[green]✅ 工具已启用: {tool_id}[/green]")
    
    conn.close()


@tools.command("disable")
@click.argument("tool_id")
def tools_disable(tool_id):
    """禁用工具"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE tools SET enabled = 0 WHERE id = ?", (tool_id,))
    
    if cursor.rowcount == 0:
        console.print(f"[red]工具不存在: {tool_id}[/red]")
    else:
        conn.commit()
        console.print(f"[yellow]⚠️  工具已禁用: {tool_id}[/yellow]")
    
    conn.close()


# ==================== 任务执行 ====================

@cli.command("run")
@click.argument("tool_id")
@click.option("--args", help="工具参数（JSON格式）")
@click.option("--wait/--no-wait", default=True, help="是否等待执行完成")
def run(tool_id, args, wait):
    """执行工具"""
    import uuid
    
    # 解析参数
    args_dict = {}
    if args:
        try:
            args_dict = json.loads(args)
        except json.JSONDecodeError:
            console.print("[red]参数格式错误，必须是有效的JSON[/red]")
            return
    
    # 创建run记录
    conn = get_db()
    cursor = conn.cursor()
    
    # 检查工具是否存在且已启用
    cursor.execute("SELECT name, enabled FROM tools WHERE id = ?", (tool_id,))
    tool = cursor.fetchone()
    
    if not tool:
        console.print(f"[red]工具不存在: {tool_id}[/red]")
        return
    
    if not tool[1]:
        console.print(f"[yellow]工具未启用: {tool_id}[/yellow]")
        return
    
    run_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    
    cursor.execute("""
        INSERT INTO runs (id, tool_id, args_json, status, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (run_id, tool_id, json.dumps(args_dict), "queued", now))
    
    conn.commit()
    
    console.print(f"[green]✅ 任务已创建[/green]")
    console.print(f"[cyan]Run ID:[/cyan] {run_id}")
    console.print(f"[cyan]工具:[/cyan] {tool[0]}")
    console.print(f"[cyan]参数:[/cyan] {json.dumps(args_dict, ensure_ascii=False)}")
    
    if wait:
        console.print("\n⏳ 等待执行完成...")
        # TODO: 实际等待执行（需要Worker运行）
        console.print("[yellow]提示: 需要启动Worker才能执行任务[/yellow]")
    
    conn.close()


# ==================== 任务管理 ====================

@cli.group()
def runs():
    """任务管理命令"""
    pass


@runs.command("list")
@click.option("--limit", default=20, help="显示数量")
@click.option("--status", type=click.Choice(["queued", "running", "succeeded", "failed"]), help="按状态过滤")
def runs_list(limit, status):
    """列出最近的任务"""
    conn = get_db()
    cursor = conn.cursor()
    
    query = """
        SELECT r.id, r.tool_id, t.name, r.status, r.created_at, r.started_at, r.completed_at
        FROM runs r
        LEFT JOIN tools t ON r.tool_id = t.id
        WHERE 1=1
    """
    params = []
    
    if status:
        query += " AND r.status = ?"
        params.append(status)
    
    query += " ORDER BY r.created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    runs_data = cursor.fetchall()
    
    if not runs_data:
        console.print("[yellow]没有找到任务[/yellow]")
        return
    
    table = Table(title="任务列表", box=box.ROUNDED)
    table.add_column("Run ID", style="cyan")
    table.add_column("工具", style="green")
    table.add_column("状态", style="yellow")
    table.add_column("创建时间", style="white")
    
    for run in runs_data:
        run_id, tool_id, tool_name, run_status, created_at, started_at, completed_at = run
        
        status_emoji = {
            "queued": "⏸️",
            "running": "▶️",
            "succeeded": "✅",
            "failed": "❌"
        }.get(run_status, "❓")
        
        table.add_row(
            run_id[:8],
            tool_name or tool_id,
            f"{status_emoji} {run_status}",
            created_at[:19] if created_at else ""
        )
    
    console.print(table)
    conn.close()


@runs.command("status")
@click.argument("run_id")
def runs_status(run_id):
    """查看任务状态"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT r.id, r.tool_id, t.name, r.args_json, r.status, 
               r.created_at, r.started_at, r.completed_at, r.exit_code
        FROM runs r
        LEFT JOIN tools t ON r.tool_id = t.id
        WHERE r.id LIKE ? OR r.id = ?
    """, (f"{run_id}%", run_id))
    
    run = cursor.fetchone()
    
    if not run:
        console.print(f"[red]任务不存在: {run_id}[/red]")
        return
    
    run_id, tool_id, tool_name, args_json, status, created_at, started_at, completed_at, exit_code = run
    
    console.print(Panel(f"[bold]任务状态[/bold]", box=box.ROUNDED))
    console.print(f"[cyan]Run ID:[/cyan] {run_id}")
    console.print(f"[cyan]工具:[/cyan] {tool_name or tool_id}")
    console.print(f"[cyan]参数:[/cyan] {args_json}")
    console.print(f"[cyan]状态:[/cyan] {status}")
    console.print(f"[cyan]创建时间:[/cyan] {created_at}")
    if started_at:
        console.print(f"[cyan]开始时间:[/cyan] {started_at}")
    if completed_at:
        console.print(f"[cyan]完成时间:[/cyan] {completed_at}")
    if exit_code is not None:
        console.print(f"[cyan]退出码:[/cyan] {exit_code}")
    
    conn.close()


@runs.command("logs")
@click.argument("run_id")
def runs_logs(run_id):
    """查看任务日志"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT stdout, stderr FROM runs 
        WHERE id LIKE ? OR id = ?
    """, (f"{run_id}%", run_id))
    
    result = cursor.fetchone()
    
    if not result:
        console.print(f"[red]任务不存在: {run_id}[/red]")
        return
    
    stdout, stderr = result
    
    if stdout:
        console.print(Panel("[bold green]标准输出[/bold green]", box=box.ROUNDED))
        console.print(stdout)
    
    if stderr:
        console.print(Panel("[bold red]标准错误[/bold red]", box=box.ROUNDED))
        console.print(stderr)
    
    if not stdout and not stderr:
        console.print("[yellow]暂无日志输出[/yellow]")
    
    conn.close()


# ==================== 审批管理 ====================

@cli.group()
def approvals():
    """审批管理命令"""
    pass


@approvals.command("list")
@click.option("--status", type=click.Choice(["pending", "approved", "denied"]), default="pending")
def approvals_list(status):
    """列出审批请求"""
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, resource_type, resource_id, requested_by, status, created_at
        FROM approval_requests
        WHERE status = ?
        ORDER BY created_at DESC
    """, (status,))
    
    approvals_data = cursor.fetchall()
    
    if not approvals_data:
        console.print(f"[yellow]没有{status}状态的审批请求[/yellow]")
        return
    
    table = Table(title=f"审批请求 ({status})", box=box.ROUNDED)
    table.add_column("ID", style="cyan")
    table.add_column("资源类型", style="green")
    table.add_column("资源ID", style="white")
    table.add_column("请求人", style="yellow")
    table.add_column("创建时间", style="magenta")
    
    for approval in approvals_data:
        approval_id, res_type, res_id, requested_by, ap_status, created_at = approval
        table.add_row(
            approval_id[:8],
            res_type,
            res_id[:8] if res_id else "",
            requested_by or "未知",
            created_at[:19] if created_at else ""
        )
    
    console.print(table)
    conn.close()


@approvals.command("approve")
@click.argument("approval_id")
@click.option("--comment", help="批准意见")
def approvals_approve(approval_id, comment):
    """批准请求"""
    conn = get_db()
    cursor = conn.cursor()

    now = datetime.utcnow().isoformat()

    # P0-5：修复 schema 错误（原用 decided_by / decision_comment 不存在）
    cursor.execute("""
        UPDATE approval_requests
        SET status = 'approved', decided_by_user_id = 'cli_user', decided_at = ?, decision_note = ?
        WHERE (id LIKE ? OR id = ?) AND status = 'pending'
    """, (now, comment, f"{approval_id}%", approval_id))

    if cursor.rowcount == 0:
        console.print(f"[red]审批请求不存在或已处理: {approval_id}[/red]")
    else:
        conn.commit()
        console.print(f"[green]✅ 已批准: {approval_id}[/green]")

    conn.close()


@approvals.command("deny")
@click.argument("approval_id")
@click.option("--reason", required=True, help="拒绝原因")
def approvals_deny(approval_id, reason):
    """拒绝请求"""
    conn = get_db()
    cursor = conn.cursor()

    now = datetime.utcnow().isoformat()

    # P0-5：修复 schema 错误（原用 decided_by / decision_comment 不存在）
    cursor.execute("""
        UPDATE approval_requests
        SET status = 'denied', decided_by_user_id = 'cli_user', decided_at = ?, decision_note = ?
        WHERE (id LIKE ? OR id = ?) AND status = 'pending'
    """, (now, reason, f"{approval_id}%", approval_id))

    if cursor.rowcount == 0:
        console.print(f"[red]审批请求不存在或已处理: {approval_id}[/red]")
    else:
        conn.commit()
        console.print(f"[yellow]❌ 已拒绝: {approval_id}[/yellow]")

    conn.close()


# ==================== 审计日志 ====================

@cli.group()
def audit():
    """审计日志命令"""
    pass


@audit.command("list")
@click.option("--limit", default=20, help="显示数量")
@click.option("--event-type", help="事件类型过滤")
@click.option("--last", help="最近时间（如: 1h, 24h, 7d）")
def audit_list(limit, event_type, last):
    """列出审计日志"""
    conn = get_db()
    cursor = conn.cursor()
    
    query = """
        SELECT event_type, actor_user_id, resource_type, resource_id, 
               status, timestamp
        FROM audit_events
        WHERE 1=1
    """
    params = []
    
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    
    if last:
        # 解析时间
        import re
        match = re.match(r"(\d+)([hd])", last)
        if match:
            value, unit = match.groups()
            hours = int(value) if unit == 'h' else int(value) * 24
            cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
            query += " AND timestamp >= ?"
            params.append(cutoff)
    
    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    events = cursor.fetchall()
    
    if not events:
        console.print("[yellow]没有找到审计日志[/yellow]")
        return
    
    table = Table(title="审计日志", box=box.ROUNDED)
    table.add_column("事件类型", style="cyan")
    table.add_column("操作人", style="green")
    table.add_column("资源", style="white")
    table.add_column("状态", style="yellow")
    table.add_column("时间", style="magenta")
    
    for event in events:
        event_type, actor, res_type, res_id, status, timestamp = event
        
        status_emoji = "✅" if status == "success" else "❌"
        
        table.add_row(
            event_type,
            actor or "系统",
            f"{res_type}:{res_id[:8]}" if res_id else res_type or "",
            f"{status_emoji} {status or 'unknown'}",
            timestamp[:19] if timestamp else ""
        )
    
    console.print(table)
    conn.close()


# ==================== 系统状态 ====================

@cli.command("status")
def system_status():
    """查看系统状态"""
    conn = get_db()
    cursor = conn.cursor()
    
    # 统计信息
    cursor.execute("SELECT COUNT(*) FROM tools WHERE enabled = 1")
    enabled_tools = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM runs WHERE status = 'queued'")
    queued_runs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM runs WHERE status = 'running'")
    running_runs = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM approval_requests WHERE status = 'pending'")
    pending_approvals = cursor.fetchone()[0]
    
    console.print(Panel("[bold]系统状态[/bold]", box=box.ROUNDED))
    console.print(f"[cyan]已启用工具:[/cyan] {enabled_tools}")
    console.print(f"[cyan]排队任务:[/cyan] {queued_runs}")
    console.print(f"[cyan]运行中任务:[/cyan] {running_runs}")
    console.print(f"[cyan]待审批请求:[/cyan] {pending_approvals}")
    
    # 数据库路径
    console.print(f"\n[cyan]数据库:[/cyan] {DB_PATH}")
    
    # 检查数据库文件大小
    db_path = Path(DB_PATH)
    if db_path.exists():
        size_mb = db_path.stat().st_size / 1024 / 1024
        console.print(f"[cyan]数据库大小:[/cyan] {size_mb:.2f} MB")
    
    conn.close()


# ==================== 依赖检查 ====================

@cli.command("check-deps")
@click.option('--verbose', '-v', is_flag=True, help='显示详细信息')
@click.option('--check', multiple=True, help='只检查特定依赖')
def check_deps(verbose, check):
    """检查系统依赖"""
    try:
        from automation_hub.utils.dependency_checker import DependencyChecker
        
        checker = DependencyChecker()
        
        if check:
            checker.check_specific(list(check))
        else:
            checker.check_all()
        
        checker.print_report(verbose=verbose)
        
        if not checker.is_ready():
            sys.exit(1)
    
    except ImportError as e:
        console.print(f"[red]错误: 无法导入依赖检查器: {e}[/red]")
        sys.exit(1)


# ==================== 定时任务管理 ====================

@cli.group()
def schedule():
    """管理定时任务"""
    pass


@schedule.command('list')
@click.option('--enabled-only', is_flag=True, help='只显示启用的任务')
def schedule_list(enabled_only):
    """列出定时任务"""
    try:
        from automation_hub.scheduler import SchedulerService
        
        scheduler = SchedulerService(DB_PATH)
        jobs = scheduler.list_jobs(enabled_only=enabled_only)
        
        if not jobs:
            console.print("[yellow]暂无定时任务[/yellow]")
            return
        
        table = Table(title="定时任务列表", box=box.ROUNDED)
        table.add_column("名称", style="cyan")
        table.add_column("工具ID", style="yellow")
        table.add_column("触发器", style="green")
        table.add_column("状态", style="magenta")
        table.add_column("执行次数", justify="right")
        table.add_column("最后执行", style="dim")
        
        for job in jobs:
            status = "✅ 启用" if job.enabled else "⏸️ 禁用"
            last_run = job.last_run_at[:19] if job.last_run_at else "N/A"
            
            table.add_row(
                job.name,
                job.tool_id,
                job.trigger_type,
                status,
                str(job.run_count),
                last_run
            )
        
        console.print(table)
    
    except ImportError:
        console.print("[red]定时任务功能需要安装: pip install apscheduler[/red]")
        sys.exit(1)


@schedule.command('create')
@click.option('--name', required=True, help='任务名称')
@click.option('--tool', required=True, help='工具ID')
@click.option('--cron', help='Cron表达式 (例如: 0 2 * * *)')
@click.option('--interval', help='间隔时间 (例如: 1h, 30m)')
@click.option('--args', default='{}', help='工具参数 (JSON)')
def schedule_create(name, tool, cron, interval, args):
    """创建定时任务"""
    try:
        from automation_hub.scheduler import SchedulerService
        import json
        
        if not cron and not interval:
            console.print("[red]必须指定 --cron 或 --interval[/red]")
            sys.exit(1)
        
        scheduler = SchedulerService(DB_PATH)
        
        # 解析触发器配置
        if cron:
            # 简单的cron解析 (分 时 日 月 周)
            parts = cron.split()
            trigger_type = "cron"
            trigger_config = {
                "minute": parts[0] if len(parts) > 0 else "*",
                "hour": parts[1] if len(parts) > 1 else "*",
            }
        else:
            # 解析间隔时间
            trigger_type = "interval"
            if interval.endswith('h'):
                trigger_config = {"hours": int(interval[:-1])}
            elif interval.endswith('m'):
                trigger_config = {"minutes": int(interval[:-1])}
            elif interval.endswith('s'):
                trigger_config = {"seconds": int(interval[:-1])}
            else:
                console.print("[red]间隔格式错误，应为: 1h, 30m, 60s[/red]")
                sys.exit(1)
        
        tool_args = json.loads(args)
        
        job_id = scheduler.create_job(
            name=name,
            tool_id=tool,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            args=tool_args,
            created_by="cli"
        )
        
        console.print(f"[green]✅ 定时任务创建成功: {name}[/green]")
        console.print(f"[dim]任务ID: {job_id}[/dim]")
    
    except ImportError:
        console.print("[red]定时任务功能需要安装: pip install apscheduler[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]创建失败: {e}[/red]")
        sys.exit(1)


@schedule.command('delete')
@click.argument('job_id')
def schedule_delete(job_id):
    """删除定时任务"""
    try:
        from automation_hub.scheduler import SchedulerService
        
        scheduler = SchedulerService(DB_PATH)
        scheduler.delete_job(job_id)
        
        console.print(f"[green]✅ 任务已删除: {job_id}[/green]")
    
    except ImportError:
        console.print("[red]定时任务功能需要安装: pip install apscheduler[/red]")
        sys.exit(1)


@schedule.command('enable')
@click.argument('job_id')
def schedule_enable(job_id):
    """启用定时任务"""
    try:
        from automation_hub.scheduler import SchedulerService
        
        scheduler = SchedulerService(DB_PATH)
        scheduler.enable_job(job_id)
        
        console.print(f"[green]✅ 任务已启用: {job_id}[/green]")
    
    except ImportError:
        console.print("[red]定时任务功能需要安装: pip install apscheduler[/red]")
        sys.exit(1)


@schedule.command('disable')
@click.argument('job_id')
def schedule_disable(job_id):
    """禁用定时任务"""
    try:
        from automation_hub.scheduler import SchedulerService
        
        scheduler = SchedulerService(DB_PATH)
        scheduler.disable_job(job_id)
        
        console.print(f"[yellow]⏸️ 任务已禁用: {job_id}[/yellow]")
    
    except ImportError:
        console.print("[red]定时任务功能需要安装: pip install apscheduler[/red]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
