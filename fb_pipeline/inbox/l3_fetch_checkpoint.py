"""Fsync'd append-only fetch journal, exclusively owned for the whole run.

Tasks are written before dispatch, outcomes after the message transaction commits.
A crash between those writes can replay one task (message persistence is idempotent).
"""
import dataclasses
import fcntl
import json
import os
from pathlib import Path
import threading
import uuid

from fb_pipeline.contracts.l1_inbox import ThreadRecord
from fb_pipeline.contracts.l1_inbox_tasks import ThreadTask


class FetchCheckpoint:
    def __init__(self, directory, metadata, resume=None):
        self.path = Path(resume) if resume else Path(directory) / f"run_{uuid.uuid4().hex}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('r+' if resume else 'x+', encoding='utf-8')
        self.lock = threading.Lock()
        self.tasks = {}
        self.results = {}
        self.discovery = None
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if resume:
                lines = self.file.readlines()
                for i, line in enumerate(lines):
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        if i != len(lines) - 1:
                            raise
                        # A killed writer may leave only the final record torn.
                        self.file.seek(sum(len(s.encode('utf-8')) for s in lines[:i]))
                        self.file.truncate()
                        break
                    self._apply(event)
                if self.metadata != metadata:
                    raise ValueError('Resume parameters differ from checkpoint: use the original page/range/options.')
            else:
                self.append({'kind': 'metadata', 'value': metadata})
        except BaseException:
            self.file.close()
            raise

    def _apply(self, event):
        kind, value = event['kind'], event['value']
        if kind == 'metadata':
            self.metadata = value
        elif kind == 'task':
            self.tasks[value['ordinal']] = value
        elif kind == 'result':
            old = self.results.get(value['ordinal'])
            if not old or old['status'] != 'persisted':
                self.results[value['ordinal']] = value
        elif kind == 'discovery':
            self.discovery = value

    def append(self, event):
        with self.lock:
            self.file.seek(0, os.SEEK_END)
            self.file.write(json.dumps(event, ensure_ascii=False) + '\n')
            self.file.flush()
            os.fsync(self.file.fileno())
            self._apply(event)

    def add_task(self, task):
        self.append({'kind': 'task', 'value': dataclasses.asdict(task)})

    def add_result(self, result):
        self.append({'kind': 'result', 'value': dataclasses.asdict(result)})

    def pending(self):
        for ordinal, value in self.tasks.items():
            result = self.results.get(ordinal)
            if result and (result['status'] in ('persisted', 'needs_review', 'no_messages', 'skipped')
                           or 'fetch_integrity_failed' in result['error']
                           or 'fetch_evidence_needs_review' in result['error']):
                continue
            value = dict(value)
            value['record'] = ThreadRecord(**value['record'])
            # Each explicit resume receives a fresh bounded retry budget.
            value.update(attempt=1, failed_by='')
            yield ThreadTask(**value)

    def close(self):
        self.file.close()
