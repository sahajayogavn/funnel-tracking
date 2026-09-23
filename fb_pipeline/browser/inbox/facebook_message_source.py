"""Read only the Facebook message model bound to a rendered message node.

This is an undocumented Facebook surface, not a stable API. Schema drift must
quarantine evidence, never silently fall back to CSS actor/date guesses.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo


# Facebook's documented emoticon shortcuts, which the Inbox renders as emoji
# images while the message model keeps the ASCII form.
_EMOTICONS = [
    ':)', ':-)', ':(', ':-(', ':P', ':-P', ':p', ':D', ':-D', ':o', ':O', ':-o', ';)', ';-)',
    '8-)', '8)', 'B-)', '8-|', '8|', 'B|', '>:(', '>:-(', ':/', ':-/', ":'(", '3:)', 'O:)', 'O:-)',
    ':*', ':-*', '<3', '^_^', '-_-', '(^^^)', ':v', ':3', ':|]', '<(")', '>:o', '>:O', ':putnam:', ':poop:',
]
_EMOTICON_RE = re.compile(
    r'(?<!\S)(?:' + '|'.join(re.escape(e) for e in sorted(_EMOTICONS, key=len, reverse=True)) + r')(?!\S)'
)

ATTACHMENT_PLACEHOLDER = '[attachment]'
# Meta's creatorInfo.creatorType for an Inbox automation; the stored sender
# for such a turn (the value the MAS/state layer already reserves for "the
# Page's bot, not a person").
AUTOMATED_CREATOR_TYPE = 'automated_response'
AUTOMATED_PAGE_SENDER = 'Auto_Page'


def _dedupe_lines(rendered: str) -> str:
    """Collapse the rendered lines of a template bubble (title repeated in
    visible, aria and hidden spans) into one body; consecutive duplicates only."""
    lines = []
    for line in rendered.replace('\u200b', '').split('\n'):
        line = ' '.join(line.split())
        if line and (not lines or lines[-1] != line):
            lines.append(line)
    return '\n'.join(lines)


def _alnum(s: str) -> str:
    return ''.join(ch for ch in s if ch.isalnum()).casefold()


SOURCE_SCRIPT = r"""() => {
 const region = document.querySelector('div[aria-label*="Message list container"], div[role="region"][aria-label*="message"]');
 const url = location.href;
 function renderedText(el) {
   // Facebook paints some emoji as images; innerText omits their alt text.
   // Walk the live node read-only, in document order, emitting alt text for
   // images and a line break at block boundaries so repeated template lines
   // stay separable.
   const BLOCK = new Set(['DIV', 'P', 'LI', 'BR', 'TR', 'SECTION', 'ARTICLE']);
   const parts = [];
   const walker = document.createTreeWalker(el, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
   for (let n = walker.nextNode(); n; n = walker.nextNode()) {
     if (n.nodeType === Node.TEXT_NODE) { parts.push(n.nodeValue || ''); continue; }
     if (n.tagName === 'IMG') { parts.push(n.getAttribute('alt') || ''); continue; }
     if (BLOCK.has(n.tagName)) parts.push('\n');
   }
   return parts.join('');
 }
 // Schema-drift diagnostics: when the fiber shape changes, Python must be
 // able to say *what* changed from the report alone.  Only prop *names* of
 // the nearest fibers are collected, never values.
 function propKeys(el) {
   const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
   const names = new Set();
   for (let f = key ? el[key] : null, depth = 0; f && depth < 8 && names.size < 40; f = f.return, depth++) {
     for (const k of Object.keys(f.memoizedProps || {})) names.add(k);
   }
   return [...names].slice(0, 40);
 }
 return {url, region_found: Boolean(region),
   page_message_nodes: document.querySelectorAll('[data-message-id]').length,
   rows: [...(region?.querySelectorAll('[data-message-id]') || [])].map(el => {
   const id = el.getAttribute('data-message-id');
   const key = Object.keys(el).find(k => k.startsWith('__reactFiber$'));
   let props = null;
   for (let f = key ? el[key] : null, depth = 0; f && depth < 30; f = f.return, depth++) {
     if (f.memoizedProps?.message) { props = f.memoizedProps; break; }
   }
   const m = props?.message;
   // Allowlist scalar evidence: never serialize a fiber, token, photo URL or app store.
   return {dom_id: id, dom_text: renderedText(el), model_id: m?.messageID,
     prop_keys: m ? undefined : propKeys(el),
     sender_id: m?.sender?.userID, viewer_id: props?.viewerID,
     participants: props?.participants?.map(p => p.userID),
     from_viewer: props?.isFromViewer, text: m?.textPayload?.text,
     timestamp_ms: m?.timestamp, has_model: Boolean(m),
     // Payload shape, so Python can admit evidenced non-text turns.  A log
     // message is a platform event, never a human turn.
     log_message: Boolean(m?.logMessage), templated: Boolean(m?.templatedMessagePayload),
     attachments: (m?.attachments?.length) || 0, replied_to: Boolean(m?.repliedToMessage),
     // Who on the Page side produced the turn.  Meta labels an Inbox
     // automation `automated_response` (rendered as the "Automated response.
     // Manage Automations" footer) and a human admin `direct_admin` with the
     // admin's display name; a plain Page turn carries neither.
     creator_type: m?.creatorInfo?.creatorType, creator_name: m?.creatorInfo?.creatorName,
     unsupported: Boolean(m?.logMessage),
   };
 })};
}"""


def source_messages(snapshot: dict, observed_at: str, *, verified_identity: tuple[str, str] | None = None) -> list[dict]:
    """Validate node ID, URL identity, participant set, actor and epoch together."""
    url = urlparse(snapshot.get('url') or '')
    qs = parse_qs(url.query, keep_blank_values=True)
    page_ids, recipients = qs.get('asset_id', []), qs.get('selected_item_id', [])
    valid_url = (url.hostname == 'business.facebook.com' and len(page_ids) == 1
                 and len(recipients) == 1 and page_ids[0].isdigit() and recipients[0].isdigit()
                 and page_ids[0] != recipients[0])
    page_id = page_ids[0] if page_ids else ''
    recipient = recipients[0] if recipients else ''
    if verified_identity is not None:
        expected_page, expected_recipient = verified_identity
        # A successful panel verification supplies the missing URL recipient.
        # Explicit conflicting or malformed URL identities must never be replaced.
        if (url.hostname == 'business.facebook.com'
                and page_ids == [expected_page]
                and expected_page.isdigit() and expected_recipient.isdigit()
                and expected_page != expected_recipient
                and 'selected_item_id' not in qs):
            recipient = expected_recipient
            valid_url = True
        else:
            valid_url = valid_url and (page_id, recipient) == verified_identity
    observed = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    result = []
    for raw in snapshot.get('rows', []):
        problems = []
        if not valid_url:
            problems.append('source_thread_identity_conflict')
        if not raw.get('has_model'):
            problems.append('missing_source_model')
        elif not raw.get('dom_id') or raw.get('dom_id') != raw.get('model_id'):
            problems.append('source_message_id_conflict')
        participants = raw.get('participants') or []
        if raw.get('has_model') and (raw.get('viewer_id') != page_id
                or len(participants) != 2 or set(participants) != {page_id, recipient}
                or raw.get('sender_id') not in {page_id, recipient}):
            problems.append('source_participant_conflict')
        actor = 'Page' if raw.get('sender_id') == page_id else 'Customer'
        if raw.get('has_model') and raw.get('from_viewer') is not (actor == 'Page'):
            problems.append('source_sender_direction_conflict')
        # code:inbox-fetch-source-001:automated-page-turn
        # An Inbox automation is not a human reply: it must never close a
        # seeker's wait (l1_conversation_state treats only ``Page`` as a
        # person) and readers must see it as "Page (automated message)".
        # The sender value is taken from Meta's own creator label, never
        # inferred from canned wording.
        creator_type = raw.get('creator_type') if isinstance(raw.get('creator_type'), str) else None
        if actor == 'Page' and creator_type == AUTOMATED_CREATOR_TYPE:
            actor = AUTOMATED_PAGE_SENDER
        ms = raw.get('timestamp_ms')
        stamp = None
        if (isinstance(ms, (int, float)) and not isinstance(ms, bool)
                and 946684800000 <= ms <= (observed.timestamp() + 300) * 1000):
            stamp = datetime.fromtimestamp(ms / 1000, ZoneInfo('Asia/Ho_Chi_Minh'))
        else:
            problems.append('missing_source_timestamp')
        text = raw.get('text')
        # code:inbox-fetch-source-001:non-text-turns
        # Actor and time come from the bound model, not from the body, so a
        # turn with attachments, a quoted reply or a template button is fully
        # evidenced.  Only the body needs a fallback: rendered DOM text for a
        # template (a quick-reply such as "Hỏi chi tiết"), or a placeholder
        # for a media-only turn.  A log message is a platform event, not a
        # human turn, and stays unsupported.
        payload = {'attachments': int(raw.get('attachments') or 0),
                   'replied_to': bool(raw.get('replied_to')), 'templated': bool(raw.get('templated'))}
        if creator_type:
            payload['creator_type'] = creator_type
            if isinstance(raw.get('creator_name'), str) and raw['creator_name'].strip():
                payload['creator_name'] = raw['creator_name'].strip()
        body_source = 'model'
        if raw.get('unsupported'):
            problems.append('unsupported_source_payload')
        elif not isinstance(text, str) or not text.strip():
            if payload['templated'] and _dedupe_lines(raw.get('dom_text') or ''):
                text, body_source = _dedupe_lines(raw.get('dom_text') or ''), 'dom'
            elif payload['attachments']:
                text, body_source = ATTACHMENT_PLACEHOLDER, 'attachment_placeholder'
            else:
                problems.append('unsupported_source_payload')
        payload['body_source'] = body_source
        if body_source == 'model' and isinstance(text, str) and text.strip() and raw.get('dom_text'):
            # This guards against a model bound to the wrong node, so compare
            # only letters/digits after removing Facebook's fixed emoticon set:
            # the DOM paints ":)" / "<3" as emoji images (alt "🙂" / "❤"), and
            # rewrites punctuation/whitespace, none of which is a body conflict.
            normal = lambda s: ''.join(ch for ch in _EMOTICON_RE.sub(' ', s) if ch.isalnum()).casefold()
            if normal(text) and normal(text) not in normal(raw['dom_text']):
                problems.append('source_body_dom_conflict')
                dom_text_for_report = raw['dom_text']
            else:
                dom_text_for_report = None
        else:
            dom_text_for_report = None
        evidence = json.dumps({'source': 'facebook_bound_message_v1', 'message_id': raw.get('model_id'),
                               'page_id': page_id, 'recipient_id': recipient,
                               'sender_id': raw.get('sender_id'), 'participants': participants,
                               'timestamp_ms': ms, 'timezone': 'Asia/Ho_Chi_Minh',
                               'payload': payload}, sort_keys=True)
        result.append({
            'source_id': raw.get('dom_id'), 'sender': actor if not problems else 'Unknown',
            'sender_confidence': 'explicit' if not problems else 'unknown',
            'sender_evidence': evidence, 'kind': 'message',
            'text': text if isinstance(text, str) and text.strip() else raw.get('dom_text', ''),
            'body': text if isinstance(text, str) and text.strip() else raw.get('dom_text', ''),
            'timestamp': stamp.strftime('%Y-%m-%d %H:%M:%S') if stamp else '',
            'day_context': stamp.date().isoformat() if stamp else None,
            'raw_timestamp': str(ms) if ms is not None else '',
            'time_precision': 'date_time' if stamp else 'unknown', 'time_evidence': evidence,
            'observed_at': observed_at, 'source_issues': problems,
            'source_links': [], 'reactions': [],
            **({'source_dom_text': dom_text_for_report} if dom_text_for_report is not None else {}),
        })
    return result


SCHEMA_DRIFT_ISSUE = 'facebook_source_schema_drift'
SCHEMA_DRIFT_MIN_BUBBLES = 3
SCHEMA_DRIFT_MAX_BOUND_RATIO = 0.1


# code:inbox-fetch-source-001:schema-drift
def diagnose_schema_drift(snapshot: dict, legacy: list[dict]) -> dict | None:
    """Run-level signal that the undocumented Facebook surface changed shape.

    One unbound bubble is missing evidence (quarantined); a thread where
    (almost) no bubble binds to a model while the DOM clearly renders a
    conversation is not "missing evidence", it is our reader being wrong
    about Facebook's structure.  Return the diagnosis (region present?, how
    many message nodes on the page, prop names of one unbound node) so the
    integrity report says *schema drift* instead of a wall of
    ``missing_source_model``.  ``None`` when binding works or the thread is
    too small to judge.
    """
    rows = snapshot.get('rows') or []
    legacy_messages = [r for r in legacy if r.get('kind') != 'system_banner'
                       and (r.get('text') or r.get('body'))]
    total = max(len(rows), len(legacy_messages))
    if total < SCHEMA_DRIFT_MIN_BUBBLES:
        return None
    bound = sum(1 for r in rows if r.get('has_model'))
    if bound > SCHEMA_DRIFT_MAX_BOUND_RATIO * total:
        return None
    sample = next((r for r in rows if not r.get('has_model')), None)
    return {
        'reason': SCHEMA_DRIFT_ISSUE,
        'region_found': bool(snapshot.get('region_found', bool(rows))),
        'page_message_nodes': snapshot.get('page_message_nodes'),
        'rows_in_region': len(rows), 'rows_bound': bound, 'legacy_messages': len(legacy_messages),
        'sample_dom_id': sample.get('dom_id') if sample else None,
        'sample_prop_keys': list(sample.get('prop_keys') or [])[:40] if sample else [],
    }


def reconcile_source(page, legacy: list[dict], observed_at: str, *, verified_identity: tuple[str, str] | None = None) -> list[dict]:
    # Test doubles/offline HTML retain the DOM-only contract; production
    # Facebook pages always require source evidence for human turns.
    if not isinstance(getattr(page, 'url', None), str) or urlparse(page.url).hostname != 'business.facebook.com':
        return legacy
    snapshot = page.evaluate(SOURCE_SCRIPT)
    messages = source_messages(snapshot, observed_at, verified_identity=verified_identity)
    drift = diagnose_schema_drift(snapshot, legacy)
    ids = {m['source_id'] for m in messages}
    for message in messages:
        # Preserve independently parsed reaction observations only when their
        # enclosing Facebook message ID matches; never transfer body/actor guesses.
        message['reactions'] = [reaction for row in legacy
            if row.get('source_id') == message['source_id']
            for reaction in row.get('reactions', [])]
    # code:inbox-fetch-source-001:unbound-duplicate
    # The legacy selector sometimes yields the same bubble a second time with
    # no message id (a template's inner span).  When its text is contained in
    # a bound bubble's rendered text it is the same observation, not a
    # missing message; keeping it would leave the thread incomplete forever.
    rendered = [_alnum(r.get('dom_text') or '') for r in snapshot.get('rows', [])]
    for row in legacy:
        if row.get('source_id') in ids:
            continue
        if (not row.get('source_id') and row.get('kind') != 'system_banner'
                and _alnum(row.get('text') or '')
                and any(_alnum(row.get('text') or '') in r for r in rendered if r)):
            continue
        if row.get('kind') != 'system_banner':
            row = {**row, 'sender': 'Unknown', 'sender_confidence': 'unknown',
                   'day_context': None, 'time_precision': 'unknown',
                   'source_issues': ['missing_source_model']}
        messages.append(row)
    if drift:
        # A contradiction of the reader's assumptions, not missing evidence on
        # one bubble: every conversational row carries the hard issue so the
        # thread is rejected (not quarantined) and the run's stop gate fires;
        # the diagnosis rides on the first such row for the report.
        first = True
        for message in messages:
            if message.get('kind') == 'system_banner':
                continue
            message['source_issues'] = [*message.get('source_issues', []), SCHEMA_DRIFT_ISSUE]
            message['sender'], message['sender_confidence'] = 'Unknown', 'unknown'
            if first:
                message['source_diagnostics'] = drift
                first = False
    return messages
