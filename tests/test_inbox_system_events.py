import json
import sqlite3
from types import SimpleNamespace

import pytest

from fb_pipeline.persistence.l4_inbox_events import save_system_events, source_target
from fb_pipeline.persistence.l4_sqlite_store import setup_database


@pytest.mark.parametrize('url,expected', [
    ('https://www.facebook.com/123/posts/456', ('post', '456')),
    ('https://www.facebook.com/permalink.php?story_fbid=456&id=123', ('post', '456')),
    ('https://www.facebook.com/ads/?ad_id=789', ('ad', '789')),
    ('https://evil.example/posts/456', (None, None)),
    ('https://www.facebook.com/123', (None, None)),
])
def test_source_target_namespaces(url, expected):
    assert source_target(url) == expected


def test_system_events_preserve_post_and_ad_links_idempotently():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    setup_database(conn)
    record = SimpleNamespace(page_id='123', thread_id='thread')
    event = dict(kind='system_banner', sender='System', text='Lan replied to an ad.',
                 source_links=[{'url': 'https://www.facebook.com/123/posts/456'},
                               {'url': 'https://www.facebook.com/ads/?ad_id=789'}])
    save_system_events(conn, record, [event])
    save_system_events(conn, record, [event])
    assert conn.execute('SELECT COUNT(*) FROM inbox_system_events').fetchone()[0] == 2
    assert [r[0] for r in conn.execute('SELECT ad_id FROM user_ad_ids')] == ['789']
    assert conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0] == 0
    payload = json.loads(conn.execute('SELECT payload_json FROM inbox_system_events LIMIT 1').fetchone()[0])
    assert payload['text'] == event['text']
    conn.close()


def test_banner_with_view_ad_label_is_an_event():
    from fb_pipeline.contracts.l1_message_kind import classify_message_kind
    assert classify_message_kind('Lan replied to an ad.\nView ad') == 'system_banner'
