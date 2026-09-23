import copy
from unittest.mock import Mock

import pytest

from fb_pipeline.browser.inbox.facebook_message_source import source_messages, reconcile_source, SOURCE_SCRIPT
from fb_pipeline.contracts.l1_fetch_integrity import check_snapshot, INCOMPLETE_REASONS
from fb_pipeline.contracts.l1_message_time import resolve_message_at


OBSERVED = '2026-09-21T12:00:00+00:00'
URL = 'https://business.facebook.com/latest/inbox/all?asset_id=123&selected_item_id=456'


def snapshot():
    return {'url': URL, 'rows': [{'dom_id': 'mid.1', 'model_id': 'mid.1',
        'sender_id': '456', 'viewer_id': '123', 'participants': ['456', '123'],
        'from_viewer': False, 'text': 'Đăng Ký Học Thiền', 'dom_text': 'Đăng Ký Học Thiền',
        'timestamp_ms': 1788629461502, 'has_model': True, 'unsupported': False}]}


def test_exact_source_identity_actor_and_seconds():
    messages = source_messages(snapshot(), OBSERVED)
    assert messages[0]['sender'] == 'Customer'
    assert messages[0]['timestamp'] == '2026-09-06 00:31:01'
    assert '1788629461502' in messages[0]['time_evidence']
    assert not check_snapshot(messages)
    assert resolve_message_at(messages[0]['timestamp']) == ('2026-09-06 00:31:01', False)
    assert resolve_message_at('2026-02-30 12:00:00') == (None, False)
    from fb_pipeline.browser.inbox.thread_list_parser import parse_sidebar_time_token
    assert parse_sidebar_time_token(messages[0]['timestamp'])['parsed_at'] == messages[0]['timestamp']
    assert parse_sidebar_time_token('2026-02-30 12:00:00')['kind'] == 'unknown'


def test_heading_verified_identity_reaches_message_source():
    from types import SimpleNamespace
    from unittest.mock import Mock
    from fb_pipeline.browser.inbox.thread_detail_parser import verify_thread_switch, extract_thread_messages
    page = Mock()
    page.url = 'https://business.facebook.com/latest/inbox/all?asset_id=123'
    data = snapshot()
    data['url'] = page.url
    page.evaluate.return_value = {'url': page.url, 'headings': ['Lan']}
    record = SimpleNamespace(page_id='123', selected_item_id='456', heading_identity_unique=True)
    recipient, verified = verify_thread_switch(page, Mock(), 'Lan', '', '', False, record)
    assert verified
    # Real extraction -> reconciliation -> source checks, with no legacy bubbles.
    page.evaluate.side_effect = [[], data]
    messages = extract_thread_messages(page, observed_at=OBSERVED, thread_name='Lan',
                                       verified_identity=('123', recipient))
    assert messages[0]['sender'] == 'Customer'
    assert not check_snapshot(messages)


@pytest.mark.parametrize('query,participants,accepted', [
    ('asset_id=123', ['123', '456'], True),
    ('asset_id=123', ['123', '999'], False),
    ('asset_id=999', ['123', '456'], False),
    ('asset_id=123&selected_item_id=999', ['123', '456'], False),
    ('asset_id=123&selected_item_id=', ['123', '456'], False),
])
def test_verified_identity_keeps_source_conflict_checks(query, participants, accepted):
    data = snapshot()
    data['url'] = 'https://business.facebook.com/latest/inbox/all?' + query
    data['rows'][0]['participants'] = participants
    messages = source_messages(data, OBSERVED, verified_identity=('123', '456'))
    assert (not check_snapshot(messages)) == accepted


def test_missing_url_recipient_still_requires_verified_identity():
    data = snapshot()
    data['url'] = 'https://business.facebook.com/latest/inbox/all?asset_id=123'
    assert check_snapshot(source_messages(data, OBSERVED))


@pytest.mark.parametrize('field,value,reason', [
    ('model_id', 'mid.other', 'source_message_id_conflict'),
    ('sender_id', '999', 'source_participant_conflict'),
    ('viewer_id', '456', 'source_participant_conflict'),
    ('participants', ['123', '999'], 'source_participant_conflict'),
    ('participants', ['123', '456', '789'], 'source_participant_conflict'),
    ('from_viewer', True, 'source_sender_direction_conflict'),
    ('has_model', False, 'missing_source_model'),
    ('timestamp_ms', None, 'missing_source_timestamp'),
    ('timestamp_ms', 999999999999999, 'missing_source_timestamp'),
    ('timestamp_ms', 1788629461, 'missing_source_timestamp'),
    ('text', None, 'unsupported_source_payload'),
    ('unsupported', True, 'unsupported_source_payload'),
    ('dom_text', 'Text from another thread', 'source_body_dom_conflict'),
])
def test_source_conflict_or_missing_evidence_never_becomes_verified(field, value, reason):
    data = snapshot()
    data['rows'][0][field] = value
    messages = source_messages(data, OBSERVED)
    assert messages[0]['sender'] == 'Unknown'
    assert reason in [i['reason'] for i in check_snapshot(messages)]


@pytest.mark.parametrize('url', [URL.replace('456', '789'), URL + '&asset_id=999',
                                  URL.replace('business.facebook.com', 'evil.test')])
def test_thread_binding_conflict_is_hard_failure(url):
    data = snapshot()
    data['url'] = url
    assert any(i['reason'] not in INCOMPLETE_REASONS for i in check_snapshot(source_messages(data, OBSERVED)))


def test_page_sender_and_missing_source_fallback():
    data = snapshot()
    data['rows'][0].update(sender_id='123', from_viewer=True)
    page = Mock(url=URL)
    page.evaluate.return_value = data
    legacy = [{'source_id': 'mid.1', 'text': 'wrong concatenated body'},
              {'source_id': 'mid.2', 'sender': 'Page', 'kind': 'message'},
              {'kind': 'system_banner', 'sender': 'System'}]
    rows = reconcile_source(page, copy.deepcopy(legacy), OBSERVED)
    assert rows[0]['sender'] == 'Page'
    assert rows[0]['text'] == 'Đăng Ký Học Thiền'
    assert rows[1]['sender'] == 'Unknown'
    assert rows[2]['sender'] == 'System'
    page.url = 'about:blank'
    assert reconcile_source(page, legacy, OBSERVED) == legacy


def test_source_epoch_refines_minute_label_but_never_overrides_a_conflict():
    from fb_pipeline.contracts.l1_fetch_integrity import compare_stored
    messages = source_messages(snapshot(), OBSERVED)
    row = dict(source_id='mid.1', thread_id='thread', sender='Customer',
        sender_confidence='explicit', time_precision='date_time',
        message_at='2026-09-06 00:31:00', message_timestamp='Sep 6, 2026, 12:31 AM',
        sender_evidence='aria-label=Customer sent a message')
    assert compare_stored(messages, [row], 'thread') == []
    # code:inbox-fetch-integrity-001:legacy-label-refinement
    # A DOM label dates the bubble cluster, so an epoch at/after it (up to a
    # day) is a refinement; an epoch before the label is a contradiction.
    assert compare_stored(messages, [{**row, 'message_at':'2026-09-06 00:30:00', 'message_timestamp':'Sep 6, 2026, 12:30 AM'}], 'thread') == []
    assert compare_stored(messages, [{**row, 'message_at':'2026-09-05 22:31:00', 'message_timestamp':'Sep 5, 2026, 10:31 PM'}], 'thread') == []
    assert compare_stored(messages, [{**row, 'message_at':'2026-09-06 00:32:00', 'message_timestamp':'Sep 6, 2026, 12:32 AM'}], 'thread')
    assert compare_stored(messages, [{**row, 'message_at':'2026-09-04 00:31:00', 'message_timestamp':'Sep 4, 2026, 12:31 AM'}], 'thread')
    assert compare_stored(messages, [{**row, 'sender':'Page'}], 'thread')
    assert compare_stored(messages, [{**row, 'thread_id':'other'}], 'thread')
    assert compare_stored(messages, [{**row, 'sender_evidence':messages[0]['sender_evidence']}], 'thread')
    assert compare_stored(messages, [{**row, 'message_timestamp':'2026-09-06 00:31:00'}], 'thread')
    assert compare_stored(messages, [{**row, 'message_at':'2026-09-06 00:31:02'}], 'thread')


def test_source_script_reads_only_node_bound_model():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page = browser.new_page()
        page.set_content('<div role="region" aria-label="message history"><div data-message-id="mid.1"><img alt="🌺">Hello</div></div><div data-message-id="outside">Other person</div>')
        page.evaluate('''() => {document.querySelector('[data-message-id]').__reactFiber$test = {return: {memoizedProps: {
          message: {messageID:'mid.1', sender:{userID:'456'}, textPayload:{text:'Hello'}, timestamp:1788629461502},
          viewerID:'123', participants:[{userID:'123'},{userID:'456'}], isFromViewer:false
        }}}}''')
        data = page.evaluate(SOURCE_SCRIPT)
        assert len(data['rows']) == 1
        assert data['rows'][0]['model_id'] == 'mid.1'
        assert data['rows'][0]['text'] == 'Hello'
        assert data['rows'][0]['dom_text'] == '🌺Hello'
        browser.close()


def test_body_dom_check_ignores_emoticon_rendering_but_catches_wrong_node():
    # Facebook paints ":)" as <img alt="🙂">; that is rendering, not a conflict.
    data = snapshot()
    data['rows'][0].update(text='hẹn bạn chiều chủ nhật nhé! :)', dom_text='hẹn bạn chiều chủ nhật nhé! 🙂')
    assert source_messages(data, OBSERVED)[0]['source_issues'] == []
    # "<3" keeps a digit, so the emoticon set itself must be stripped first.
    data['rows'][0].update(text='see you next week  <3', dom_text='see you next week  ❤')
    assert source_messages(data, OBSERVED)[0]['source_issues'] == []
    data['rows'][0].update(text='hẹn bạn chiều chủ nhật nhé! :)', dom_text='một tin nhắn khác hẳn')
    message = source_messages(data, OBSERVED)[0]
    assert 'source_body_dom_conflict' in message['source_issues']
    assert message['source_dom_text'] == 'một tin nhắn khác hẳn'


# code:test-validation-001:source-non-text-turns
def test_evidenced_non_text_turns_are_admitted_with_payload_evidence():
    import json
    data = snapshot()
    row = data['rows'][0]
    # Template quick-reply: no model text, DOM repeats the title in three spans.
    row.update(text='', templated=True, dom_text='Hỏi chi tiết\nHỏi chi tiết\nHỏi chi tiết')
    m = source_messages(data, OBSERVED)[0]
    assert m['source_issues'] == [] and m['text'] == 'Hỏi chi tiết' and m['sender'] == 'Customer'
    assert json.loads(m['sender_evidence'])['payload'] == {'attachments': 0, 'replied_to': False, 'templated': True, 'body_source': 'dom'}
    # Image with caption keeps its text; image only gets the placeholder.
    row.update(text='🌺 KHÓA HỌC THIỀN ONLINE', templated=False, attachments=1, dom_text='🌺 KHÓA HỌC THIỀN ONLINE')
    m = source_messages(data, OBSERVED)[0]
    assert m['source_issues'] == [] and m['text'] == '🌺 KHÓA HỌC THIỀN ONLINE'
    row.update(text='', dom_text='')
    m = source_messages(data, OBSERVED)[0]
    assert m['source_issues'] == [] and m['text'] == '[attachment]'
    assert json.loads(m['sender_evidence'])['payload']['body_source'] == 'attachment_placeholder'
    # Quoted reply with text is a normal turn.
    row.update(text='Dạ em đăng ký', attachments=0, replied_to=True, dom_text='Dạ em đăng ký')
    assert source_messages(data, OBSERVED)[0]['source_issues'] == []
    # Empty body with no attachment/template remains unsupported; log messages always are.
    row.update(text='', replied_to=False, dom_text='')
    assert 'unsupported_source_payload' in source_messages(data, OBSERVED)[0]['source_issues']
    row.update(text='joined', unsupported=True, log_message=True, dom_text='joined')
    assert 'unsupported_source_payload' in source_messages(data, OBSERVED)[0]['source_issues']


def test_unbound_legacy_duplicate_of_a_bound_bubble_is_dropped_but_unknown_rows_stay():
    page = Mock()
    page.url = URL
    data = snapshot()
    data['rows'][0].update(text='', templated=True, dom_text='Hỏi chi tiết\nHỏi chi tiết\nHỏi chi tiết')
    page.evaluate.return_value = data
    legacy = [
        {'source_id': None, 'kind': 'message', 'text': 'Hỏi chi tiết\nHỏi chi tiết\nHỏi chi tiết', 'sender': 'Customer', 'reactions': []},
        {'source_id': None, 'kind': 'message', 'text': 'Học phí ?', 'sender': 'Customer', 'reactions': []},
    ]
    out = reconcile_source(page, legacy, OBSERVED)
    texts = [m['text'] for m in out]
    assert texts == ['Hỏi chi tiết', 'Học phí ?']
    assert out[1]['source_issues'] == ['missing_source_model']


# code:test-validation-001:facebook-source-schema-drift (W6)
def test_schema_drift_is_one_hard_diagnosed_issue_not_a_wall_of_missing_models():
    from fb_pipeline.browser.inbox.facebook_message_source import diagnose_schema_drift, SCHEMA_DRIFT_ISSUE
    page = Mock(url=URL)
    # Region found, five bubbles rendered, none binds to a model.
    rows = [{'dom_id': f'mid.{i}', 'dom_text': f'Bubble {i}', 'has_model': False,
             'prop_keys': ['children', 'threadKey', 'messageNode']} for i in range(5)]
    data = {'url': URL, 'region_found': True, 'page_message_nodes': 5, 'rows': rows}
    page.evaluate.return_value = data
    legacy = [{'source_id': f'mid.{i}', 'kind': 'message', 'text': f'Bubble {i}', 'sender': 'Customer'} for i in range(5)]
    legacy.append({'kind': 'system_banner', 'sender': 'System', 'text': 'replied to an ad'})
    out = reconcile_source(page, copy.deepcopy(legacy), OBSERVED)
    issues = check_snapshot(out)
    drift = [i for i in issues if i['reason'] == SCHEMA_DRIFT_ISSUE]
    assert len(drift) == 5 and SCHEMA_DRIFT_ISSUE not in INCOMPLETE_REASONS  # hard, not quarantine
    assert drift[0]['detail']['sample_prop_keys'] == ['children', 'threadKey', 'messageNode']
    assert drift[0]['detail']['rows_bound'] == 0 and drift[0]['detail']['region_found'] is True
    assert all('detail' not in i for i in drift[1:])
    assert out[-1]['sender'] == 'System'  # banners untouched
    # Region selector broke: no rows at all, but the DOM still renders bubbles.
    data = {'url': URL, 'region_found': False, 'page_message_nodes': 7, 'rows': []}
    d = diagnose_schema_drift(data, legacy)
    assert d and d['region_found'] is False and d['page_message_nodes'] == 7 and d['legacy_messages'] == 5
    # One unbound bubble among many bound ones is quarantine, not drift.
    ok_rows = [dict(snapshot()['rows'][0], dom_id=f'mid.{i}', model_id=f'mid.{i}') for i in range(9)]
    ok_rows.append({'dom_id': 'mid.x', 'dom_text': 'sticker', 'has_model': False})
    assert diagnose_schema_drift({'url': URL, 'region_found': True, 'rows': ok_rows}, []) is None
    # Too small to judge.
    assert diagnose_schema_drift({'url': URL, 'rows': rows[:2]}, legacy[:2]) is None


def test_source_script_reports_region_and_prop_keys_for_unbound_nodes():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        page = browser.new_page()
        page.set_content('<div role="region" aria-label="message history"><div data-message-id="mid.1">Hello</div></div>')
        page.evaluate('''() => {document.querySelector('[data-message-id]').__reactFiber$test = {memoizedProps: {threadKey: 1, secretToken: 'SECRETVALUE'},
          return: {memoizedProps: {messageNode: {}, children: null}}}}''')
        data = page.evaluate(SOURCE_SCRIPT)
        assert data['region_found'] is True and data['page_message_nodes'] == 1
        assert data['rows'][0]['has_model'] is False
        assert sorted(data['rows'][0]['prop_keys']) == ['children', 'messageNode', 'secretToken', 'threadKey']
        assert 'SECRETVALUE' not in str(data['rows'][0])  # names only, never values
        page.set_content('<div><div data-message-id="mid.1">Hello</div></div>')
        data = page.evaluate(SOURCE_SCRIPT)
        assert data['region_found'] is False and data['page_message_nodes'] == 1 and data['rows'] == []
        browser.close()


# code:test-validation-001:automated-page-turn
def test_inbox_automation_is_auto_page_from_meta_creator_label_never_from_wording():
    from fb_pipeline.contracts.l1_fetch_integrity import compare_stored
    data = snapshot()
    data['rows'][0].update(sender_id='123', from_viewer=True, creator_type='automated_response',
                           creator_name='Automated Response',
                           text='Xin chào, bạn để lại Họ tên và số điện thoại để đăng ký học nhé',
                           dom_text='Xin chào, bạn để lại Họ tên và số điện thoại để đăng ký học nhé')
    # human admin with the same canned wording -> Page, with the admin's name kept as evidence
    human = dict(data['rows'][0], dom_id='mid.2', model_id='mid.2', creator_type='direct_admin', creator_name='Hung Bui')
    # plain Page turn without creator info -> Page
    plain = dict(data['rows'][0], dom_id='mid.3', model_id='mid.3', creator_type=None, creator_name=None)
    data['rows'] += [human, plain]
    out = source_messages(data, OBSERVED)
    assert [m['sender'] for m in out] == ['Auto_Page', 'Page', 'Page']
    assert all(m['sender_confidence'] == 'explicit' for m in out)
    assert not check_snapshot(out)  # Auto_Page is an admitted, verified sender
    import json
    assert json.loads(out[0]['sender_evidence'])['payload']['creator_type'] == 'automated_response'
    assert json.loads(out[1]['sender_evidence'])['payload'] == {
        'attachments': 0, 'body_source': 'model', 'creator_name': 'Hung Bui',
        'creator_type': 'direct_admin', 'replied_to': False, 'templated': False}
    # A row stored as Page before the creator label existed is refined, not contradicted.
    stored = [{'source_id': 'mid.1', 'thread_id': 't', 'sender': 'Page', 'sender_confidence': 'explicit',
               'sender_evidence': out[0]['sender_evidence'], 'message_timestamp': None,
               'message_at': out[0]['timestamp'], 'time_precision': 'date_time'}]
    assert compare_stored(out[:1], stored, 't') == []
    # ...but a side change still is.
    stored[0]['sender'] = 'Customer'
    assert [i['field'] for i in compare_stored(out[:1], stored, 't')] == ['sender']
