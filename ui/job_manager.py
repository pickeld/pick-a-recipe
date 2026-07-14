"""
Job Manager for Pick-a-Recipe
Dynamic job queue with configurable concurrency and progress tracking.
"""

import os
import sys
import base64
import queue
import threading
from typing import Dict, Optional, Callable, TYPE_CHECKING

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config
from database import (
    create_job as db_create_job,
    get_job,
    get_active_jobs,
    get_queued_jobs,
    update_job_progress,
    fail_job as db_fail_job,
    cancel_job as db_cancel_job,
    complete_job as db_complete_job,
    create_history_entry,
    get_queue_position,
    update_job_tokens,
)

if TYPE_CHECKING:
    from flask_socketio import SocketIO


def resolve_max_concurrent() -> int:
    env_val = os.environ.get('MAX_CONCURRENT_JOBS')
    if env_val:
        try:
            return max(1, min(16, int(env_val)))
        except ValueError:
            pass
    config.reload()
    return config.MAX_CONCURRENT_JOBS


class JobManager:
    """Manages a FIFO job queue with a fixed worker pool."""

    def __init__(self, socketio):
        self.socketio = socketio
        self.max_concurrent = resolve_max_concurrent()
        self.active_jobs: Dict[str, dict] = {}
        self.job_threads: Dict[str, threading.Thread] = {}
        self.cancellation_flags: Dict[str, threading.Event] = {}
        self._work_queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._workers: list[threading.Thread] = []

        for _ in range(self.max_concurrent):
            worker = threading.Thread(target=self._worker_loop, daemon=True)
            worker.start()
            self._workers.append(worker)

        self._pending_restore = True

    def set_process_func(self, func: Callable) -> None:
        self._process_func = func
        if self._pending_restore:
            self._restore_active_jobs()
            self._pending_restore = False

    def refresh_concurrency(self) -> None:
        """Update max concurrent from config/env (applies to new worker spawns only)."""
        self.max_concurrent = resolve_max_concurrent()

    def _worker_loop(self) -> None:
        while True:
            job_id, process_func = self._work_queue.get()
            try:
                if self.is_cancelled(job_id):
                    continue
                self.update_progress(job_id, 'pending', 'Starting...', 1)
                process_func(job_id, self)
            except Exception as exc:
                self.fail_job(job_id, f'Worker error: {exc}')
            finally:
                self._work_queue.task_done()
                self._cleanup_job(job_id)
                self._broadcast_queue_positions()

    def _restore_active_jobs(self) -> None:
        try:
            active = get_active_jobs()
            for job in active:
                job_id = job['id']
                status = job['status']
                if status in (
                    'downloading', 'transcribing', 'extracting', 'creating',
                    'uploading', 'processing', 'awaiting_confirmation',
                ):
                    db_fail_job(job_id, 'Server was restarted during processing. Please retry.')
                elif status in ('queued', 'pending'):
                    with self._lock:
                        self.active_jobs[job_id] = {
                            'url': job['url'],
                            'status': 'queued',
                            'progress': job.get('progress', 0),
                        }
                        self.cancellation_flags[job_id] = threading.Event()
                    self._work_queue.put((job_id, self._process_func))
        except Exception as exc:
            print(f"Error restoring active jobs: {exc}")

    _process_func: Callable | None = None
    _pending_restore: bool = True

    def create_new_job(
        self,
        url: str,
        *,
        retry_from_history_id: int | None = None,
        priority: int = 0,
    ) -> str:
        job_id = db_create_job(url, retry_from_history_id=retry_from_history_id, priority=priority)
        with self._lock:
            self.active_jobs[job_id] = {
                'url': url,
                'status': 'queued',
                'progress': 0,
            }
            self.cancellation_flags[job_id] = threading.Event()
        return job_id

    def start_job(self, job_id: str, process_func: Callable) -> None:
        self.set_process_func(process_func)
        position = get_queue_position(job_id)
        msg = f'Queued — position {position}' if position > 1 else 'Queued — starting soon...'
        self.update_progress(job_id, 'queued', msg, 0)
        self._work_queue.put((job_id, process_func))
        self._broadcast_queue_positions()

    def is_cancelled(self, job_id: str) -> bool:
        flag = self.cancellation_flags.get(job_id)
        return flag.is_set() if flag else False

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            if job_id in self.cancellation_flags:
                self.cancellation_flags[job_id].set()
        result = db_cancel_job(job_id)
        if result:
            self.socketio.emit('job_cancelled', {'job_id': job_id}, room=f'job_{job_id}')
            self.socketio.emit('job_cancelled', {'job_id': job_id})
            self._broadcast_queue_positions()
        return result

    def update_progress(
        self,
        job_id: str,
        stage: str,
        message: str,
        percent: int,
        video_title: Optional[str] = None,
    ) -> None:
        if self.is_cancelled(job_id):
            return

        status_map = {
            'queued': 'queued',
            'pending': 'pending',
            'info': 'downloading',
            'download': 'downloading',
            'transcribe': 'transcribing',
            'visual': 'extracting',
            'image': 'extracting',
            'evaluate': 'creating',
            'preview': 'awaiting_confirmation',
            'upload': 'uploading',
            'complete': 'completed',
            'error': 'failed',
            'cancelled': 'cancelled',
        }
        status = status_map.get(stage, 'processing')

        update_job_progress(job_id, status, percent, stage, message, video_title)

        with self._lock:
            if job_id in self.active_jobs:
                self.active_jobs[job_id]['status'] = status
                self.active_jobs[job_id]['progress'] = percent
                if video_title:
                    self.active_jobs[job_id]['video_title'] = video_title

        payload = {
            'job_id': job_id,
            'stage': stage,
            'message': message,
            'percent': percent,
            'video_title': video_title,
            'queue_position': get_queue_position(job_id) if status == 'queued' else 0,
        }
        self.socketio.emit('job_progress', payload, room=f'job_{job_id}')
        self.socketio.emit('job_progress', payload)

    def complete_job(
        self,
        job_id: str,
        recipe_data: dict,
        image_path: Optional[str],
        output_target: str,
        llm_tokens: int = 0,
    ) -> None:
        job = get_job(job_id)
        if not job:
            return

        if llm_tokens:
            update_job_tokens(job_id, llm_tokens)

        thumbnail_data = None
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, 'rb') as f:
                    thumbnail_data = base64.b64encode(f.read()).decode('utf-8')
            except Exception:
                pass

        create_history_entry(
            job_id=job_id,
            url=job['url'],
            video_title=job.get('video_title'),
            recipe_name=recipe_data.get('name'),
            recipe_data=recipe_data,
            thumbnail_path=image_path,
            thumbnail_data=thumbnail_data,
            status='success',
            output_target=output_target,
        )
        db_complete_job(job_id)

        payload = {
            'job_id': job_id,
            'recipe': recipe_data,
            'llm_tokens_used': llm_tokens,
        }
        self.socketio.emit('job_complete', payload, room=f'job_{job_id}')
        self.socketio.emit('job_complete', payload)

    def fail_job(self, job_id: str, error_message: str, llm_tokens: int = 0) -> None:
        job = get_job(job_id)
        if not job:
            return

        if llm_tokens:
            update_job_tokens(job_id, llm_tokens)

        create_history_entry(
            job_id=job_id,
            url=job['url'],
            video_title=job.get('video_title'),
            recipe_name=None,
            recipe_data=None,
            thumbnail_path=None,
            thumbnail_data=None,
            status='failed',
            error_message=error_message,
        )
        db_fail_job(job_id, error_message)

        payload = {'job_id': job_id, 'error': error_message}
        self.socketio.emit('job_failed', payload, room=f'job_{job_id}')
        self.socketio.emit('job_failed', payload)

    def get_all_active_jobs(self) -> list:
        return get_active_jobs()

    def get_job_status(self, job_id: str) -> Optional[dict]:
        job = get_job(job_id)
        if job and job.get('status') == 'queued':
            job = dict(job)
            job['queue_position'] = get_queue_position(job_id)
        return job

    def get_queue_stats(self) -> dict:
        queued = get_queued_jobs()
        return {
            'max_concurrent': self.max_concurrent,
            'queued_count': len(queued),
            'active_count': len(self.active_jobs),
        }

    def _broadcast_queue_positions(self) -> None:
        for job in get_queued_jobs():
            pos = get_queue_position(job['id'])
            self.update_progress(
                job['id'], 'queued', f'Queued — position {pos}', 0,
                video_title=job.get('video_title'),
            )

    def _cleanup_job(self, job_id: str) -> None:
        with self._lock:
            self.job_threads.pop(job_id, None)
            self.cancellation_flags.pop(job_id, None)


job_manager: Optional[JobManager] = None


def init_job_manager(socketio) -> JobManager:
    global job_manager
    job_manager = JobManager(socketio)
    return job_manager


def get_job_manager() -> Optional[JobManager]:
    return job_manager
