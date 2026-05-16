"""定时任务调度 - 账号有效性检测、trial 到期提醒、定时注册任务执行"""
from datetime import datetime, timezone
from sqlmodel import Session, select
from .db import engine, AccountModel
from .registry import get, load_all
from .base_platform import Account, AccountStatus, RegisterConfig
import threading
import time
import json


class Scheduler:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        # 从数据库加载定时任务
        self._load_tasks_from_db()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        print(f"[Scheduler] started, {len(_scheduled_register_tasks)} tasks loaded")
    
    def _load_tasks_from_db(self):
        """从数据库加载定时任务"""
        try:
            from core.db import ScheduledTaskModel, engine
            from sqlmodel import Session, select
            with Session(engine) as s:
                tasks = s.exec(select(ScheduledTaskModel).where(ScheduledTaskModel.paused == False)).all()
                for task in tasks:
                    config = {
                        'task_id': task.task_id,
                        'platform': task.platform,
                        'count': task.count,
                        'executor_type': task.executor_type,
                        'captcha_solver': task.captcha_solver,
                        'extra': task.get_extra(),
                        'interval_type': task.interval_type,
                        'interval_value': task.interval_value,
                        'paused': task.paused,
                    }
                    _scheduled_register_tasks[task.task_id] = config
        except Exception as e:
            print(f"[Scheduler] 加载任务失败：{e}")

    def stop(self):
        self._running = False

    def _loop(self):
        # Wait 5s for tasks to load before starting checks
        time.sleep(5)
        while self._running:
            try:
                self.check_trial_expiry()
                self.check_and_run_scheduled_tasks()
                self.cleanup_zombie_processes()  # 自动巡检僵尸进程
            except Exception as e:
                print(f"[Scheduler] error: {e}")
            # 每分钟检查一次
            time.sleep(60)

    def cleanup_zombie_processes(self):
        """十二路自愈环：自动清理超时死锁的 Playwright / Camoufox 残留进程及无用缓存"""
        import psutil
        import shutil
        import os
        from datetime import datetime
        
        now = time.time()
        killed_count = 0
        try:
            for proc in psutil.process_iter(['pid', 'name', 'create_time', 'cmdline']):
                try:
                    name = proc.info.get('name', '').lower()
                    cmdline = proc.info.get('cmdline', [])
                    # 匹配 chromium / firefox 等无头浏览器进程
                    if name in ('chrome', 'chrome.exe', 'firefox', 'firefox.exe', 'camoufox.exe'):
                        # 检查进程已存活时间 (秒)
                        create_time = proc.info.get('create_time', now)
                        age = now - create_time
                        
                        # 如果存活超过 15 分钟（一般注册不会卡这么久），认为是死锁残留
                        if age > 900:
                            proc.kill()
                            killed_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                    
            if killed_count > 0:
                print(f"[Daemon] cleaned {killed_count} zombie browser processes")
                
            # 清理 /tmp 下 Playwright 残留的无用临时目录 (超过 1 小时)
            tmp_dir = "/tmp" if os.name != 'nt' else os.environ.get('TEMP')
            if tmp_dir and os.path.exists(tmp_dir):
                cleaned_dirs = 0
                for item in os.listdir(tmp_dir):
                    if item.startswith("playwright_chromiumdir") or item.startswith("playwright_firefoxdir"):
                        path = os.path.join(tmp_dir, item)
                        if os.path.isdir(path):
                            age = now - os.path.getmtime(path)
                            if age > 3600:
                                shutil.rmtree(path, ignore_errors=True)
                                cleaned_dirs += 1
                if cleaned_dirs > 0:
                    print(f"[Daemon] cleaned {cleaned_dirs} Playwright leftover tmpfs dirs")
                    
        except ImportError:
            pass  # 如果没安装 psutil 则跳过
        except Exception as e:
            print(f"[Daemon 自愈] 清理失败: {e}")

    def check_trial_expiry(self):
        """检查 trial 到期账号，更新状态"""
        now = int(datetime.now(timezone.utc).timestamp())
        with Session(engine) as s:
            accounts = s.exec(
                select(AccountModel).where(AccountModel.status == "trial")
            ).all()
            updated = 0
            for acc in accounts:
                if acc.trial_end_time and acc.trial_end_time < now:
                    acc.status = AccountStatus.EXPIRED.value
                    acc.updated_at = datetime.now(timezone.utc)
                    s.add(acc)
                    updated += 1
            s.commit()
            if updated:
                print(f"[Scheduler] {updated} trial accounts expired")

    def check_and_run_scheduled_tasks(self):
        """检查并执行到期的定时任务"""
        # 延迟导入，避免循环导入
        global _scheduled_register_tasks, _task_run_status, _scheduled_tasks_lock, _task_status_lock
        from api.tasks import _run_register, RegisterTaskRequest
        
        with _scheduled_tasks_lock:
            tasks = dict(_scheduled_register_tasks)
        now = datetime.now(timezone.utc)
        
        for task_id, task_config in tasks.items():
            # 检查是否暂停
            if task_config.get('paused', False):
                continue
            
            # 获取上次运行状态
            with _task_status_lock:
                run_status = _task_run_status.get(task_id)
            last_run_at = None
            if run_status and run_status.get('last_run_at'):
                try:
                    last_run_at = datetime.fromisoformat(run_status['last_run_at'].replace('+00:00', '+00:00'))
                except:
                    pass
            
            # 计算下次运行时间
            interval_type = task_config.get('interval_type', 'minutes')
            interval_value = task_config.get('interval_value', 30)
            
            if interval_type == 'hours':
                interval_minutes = interval_value * 60
            else:
                interval_minutes = interval_value
            
            # 检查是否到期
            should_run = False
            if last_run_at is None:
                # 从未运行过，立即运行
                should_run = True
            else:
                # 检查是否超过间隔时间
                elapsed = (now - last_run_at).total_seconds() / 60
                if elapsed >= interval_minutes:
                    should_run = True
            
            if should_run:
                print(f"[Scheduler] executing task {task_id}")
                run_task_id = f"scheduled_{task_id}_{int(time.time())}"
                def run_task():
                    error_msg = None
                    success = False
                    try:
                        # 初始化 _tasks 记录
                        from api.tasks import _create_task_record, _tasks, _tasks_lock
                        req = RegisterTaskRequest(**task_config)
                        _create_task_record(run_task_id, req, "scheduled", req.extra)
                        _run_register(run_task_id, req)
                        with _tasks_lock:
                            task_state = dict(_tasks.get(run_task_id, {}))
                        success = task_state.get("status") == "done" and not task_state.get("errors")
                        if not success:
                            error_msg = task_state.get("error") or "; ".join(task_state.get("errors") or [])
                        print(f"[Scheduler] task {task_id} done")
                    except Exception as e:
                        error_msg = str(e)
                        print(f"[Scheduler] task {task_id} failed: {e}")
                    finally:
                        # 更新运行状态
                        from core.scheduler import update_task_run_status
                        update_task_run_status(task_id, success, error_msg)
                threading.Thread(target=run_task, daemon=True).start()


scheduler = Scheduler()


# 定时注册任务管理
_scheduled_register_tasks = {}
_scheduled_tasks_lock = threading.Lock()

# 任务运行状态跟踪
_task_run_status = {}
_task_status_lock = threading.Lock()


def add_scheduled_register_task(task_id: str, config: dict):
    """添加定时注册任务"""
    with _scheduled_tasks_lock:
        _scheduled_register_tasks[task_id] = config


def remove_scheduled_register_task(task_id: str):
    """移除定时注册任务"""
    with _scheduled_tasks_lock:
        if task_id in _scheduled_register_tasks:
            del _scheduled_register_tasks[task_id]


def get_scheduled_register_tasks():
    """获取所有定时任务"""
    with _scheduled_tasks_lock:
        return dict(_scheduled_register_tasks)


def update_task_run_status(task_id: str, success: bool, error: str = None):
    """更新任务运行状态"""
    with _task_status_lock:
        _task_run_status[task_id] = {
            'last_run_at': datetime.now(timezone.utc).isoformat(),
            'last_run_success': success,
            'last_error': error,
        }


def get_task_run_status(task_id: str):
    """获取任务运行状态"""
    with _task_status_lock:
        return _task_run_status.get(task_id)


def get_all_task_run_status():
    """获取所有任务运行状态"""
    with _task_status_lock:
        return dict(_task_run_status)
