"""Cloudflare API-token hosting; all mutations run in retryable jobs.

Provider responses, tokens and tunnel output must never enter job errors or logs.
"""
from __future__ import annotations

import base64
import json
import secrets

import requests
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from flask import current_app
from sqlalchemy import select

from copydesk import audit, domains, jobs, settings
from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import HostingConnection, ManagedRecord
from copydesk.utils import utcnow

API = 'https://api.cloudflare.com/client/v4'


def _cipher():
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b'copydesk.hosting.credentials.v1').derive(state().config.secret_key.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt(data):
    return _cipher().encrypt(json.dumps(data).encode()).decode()


def connection():
    row = db.session.get(HostingConnection, 1, populate_existing=True)
    if row is None:
        raise domains.DomainProblem('Connect Cloudflare first.')
    try:
        return row, json.loads(_cipher().decrypt(row.payload.encode()))
    except (InvalidToken, ValueError):
        raise domains.DomainProblem('Reconnect Cloudflare or restore the instance secret key.') from None


def save(data, status=None):
    row = db.session.get(HostingConnection, 1)
    if row is None:
        row = HostingConnection(id=1, payload=encrypt(data), status=status or 'connected')
        db.session.add(row)
    else:
        row.payload, row.updated_at = encrypt(data), utcnow()
        row.version += 1
        if status:
            row.status = status
    return row


class Client:
    def __init__(self, token):
        self.credentials = token

    def call(self, method, path, *, raw=False, **kwargs):
        try:
            if isinstance(self.credentials, dict):
                from copydesk.hosting_oauth import access_token

                token = access_token(self.credentials) if self.credentials.get('oauth') else self.credentials['token']
            else:
                token = self.credentials
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(method, API + path, headers={'Authorization': 'Bearer ' + token},
                    timeout=(3, 10), allow_redirects=False, **kwargs)
            if response.status_code in (401, 403):
                row = db.session.get(HostingConnection, 1)
                if row:
                    row.status = 'authorization_required'
                    db.session.commit()
                    runtime = current_app.extensions.get('copydesk_desktop')
                    if runtime:
                        runtime.hosting_status = 'Authorization required'
                raise domains.DomainProblem('Cloudflare authorization needs attention. Check token permissions or reconnect.')
            if method == 'DELETE' and response.status_code == 404:
                return None
            if raw and response.ok:
                return response.content
            data = response.json()
            if not response.ok or not data.get('success'):
                raise domains.DomainProblem('Cloudflare could not complete this step. Check permissions and retry.')
            return data.get('result')
        except (requests.RequestException, ValueError) as exc:
            if isinstance(exc, domains.DomainProblem):
                raise
            raise domains.DomainProblem('Cloudflare could not be reached. Try again later.') from None

    def pages(self, path, **params):
        result = []
        for page in range(1, 101):
            batch = self.call('GET', path, params={**params, 'page': page, 'per_page': 50})
            result.extend(batch)
            if len(batch) < 50:
                return result
        raise domains.DomainProblem('Too many Cloudflare results. Use a more narrowly scoped token.')


def connect(token):
    token = str(token or '').strip()
    if not token or len(token) > 4096 or any(c.isspace() for c in token):
        raise domains.DomainProblem('Paste a Cloudflare API token.')
    existing = db.session.get(HostingConnection, 1)
    if existing:
        raise domains.DomainProblem('Disconnect the existing hosting connection before connecting another token.')
    # Verification is a job too: credentials never go into the job payload.
    save({'token': token}, 'checking')
    jobs.enqueue('domain.connect')
    audit.record('domain.connected', label='Cloudflare')


@jobs.handler('domain.connect')
def verify_token(payload):
    row, data = connection()
    client = Client(data)
    result = {'status': 'active'} if data.get('oauth') else client.call('GET', '/user/tokens/verify')
    if result.get('status') != 'active':
        raise domains.DomainProblem('This Cloudflare token is not active.')
    zones = client.pages('/zones')
    # Store only display-safe domain metadata, never an unfiltered response.
    settings.set_value('hosting.zones', [{k: z.get(k) for k in ('id', 'name', 'status', 'type', 'name_servers')} for z in zones])
    row.status = 'connected'


def choose(zone_id, subdomain):
    row, data = connection()
    zones = settings.get('hosting.zones', [])
    zone = next((z for z in zones if z['id'] == zone_id), None)
    if not zone or zone.get('status') != 'active' or zone.get('type') != 'full':
        raise domains.DomainProblem('Choose an active domain using full Cloudflare nameservers.')
    if data.get('zone') or row.status not in {'connected'}:
        raise domains.DomainProblem('Finish or disconnect the current domain first.')
    name = domains.hostname((subdomain.strip() + '.' if subdomain.strip() else '') + zone['name'])
    data.update(zone=zone_id, hostname=name)
    save(data, 'provisioning')
    jobs.enqueue('domain.provision')
    audit.record('domain.provision', label=name)


@jobs.handler('domain.provision')
def provision(payload):
    row, data = connection()
    if row.status in {'disconnecting', 'authorization_required'}:
        return
    client = Client(data)
    zone_id, host = data['zone'], data['hostname']
    if not data.get('account'):
        zone = client.call('GET', '/zones/' + zone_id)
        data['account'] = zone['account']['id']
        save(data)
        db.session.commit()
    account = '/accounts/' + data['account']
    tunnel_name = 'quillsail-' + domains.instance_id()
    db.session.commit()
    # Reserve the hostname before making a tunnel; unrelated records always block.
    marker = 'Managed by QuillSail ' + domains.instance_id()
    existing = client.pages('/zones/' + zone_id + '/dns_records', name=host)
    owned = [r for r in existing if r.get('comment') == marker]
    if any(r not in owned for r in existing):
        raise domains.DomainProblem('A DNS record already uses this hostname. Choose another name or remove it yourself.')
    if not data.get('tunnel'):
        tunnels = client.pages(account + '/cfd_tunnel', name=tunnel_name, is_deleted='false')
        tunnel = next((t for t in tunnels if t['name'] == tunnel_name), None)
        if tunnel is None:
            tunnel = client.call('POST', account + '/cfd_tunnel', json={
                'name': tunnel_name, 'config_src': 'cloudflare',
                'tunnel_secret': base64.b64encode(secrets.token_bytes(32)).decode()})
        data['tunnel'] = tunnel['id']
        save(data)
        db.session.commit()
    runtime = current_app.extensions.get('copydesk_desktop')
    if runtime:
        settings.set_value('hosting.provider', 'cloudflare')
        db.session.commit()
        if runtime.public_url != 'https://' + host:
            runtime.set_public_url('https://' + host)
        service = f'http://127.0.0.1:{runtime.public_server.server_port}'
    else:
        service = settings.get('hosting.origin', 'http://127.0.0.1:8000')
    tunnel_path = account + '/cfd_tunnel/' + data['tunnel']
    client.call('PUT', tunnel_path + '/configurations', json={'config': {'ingress': [
        {'hostname': host, 'service': service}, {'service': 'http_status:404'}]}})
    target = data['tunnel'] + '.cfargotunnel.com'
    if owned:
        if len(owned) != 1 or owned[0]['type'] != 'CNAME' or owned[0]['content'] != target:
            raise domains.DomainProblem('The managed DNS record changed. Review it before retrying.')
        record = owned[0]
    else:
        record = client.call('POST', '/zones/' + zone_id + '/dns_records', json={
            'type': 'CNAME', 'name': host, 'content': target, 'proxied': True, 'ttl': 1, 'comment': marker})
    journal = db.session.scalar(select(ManagedRecord).where(ManagedRecord.zone_id == zone_id, ManagedRecord.provider_id == record['id']))
    if journal is None:
        db.session.add(ManagedRecord(zone_id=zone_id, provider_id=record['id'], name=host, type='CNAME', value=target))
    save(data)
    db.session.commit()
    if not data.get('tunnel_token'):
        data['tunnel_token'] = client.call('GET', tunnel_path + '/token')
        save(data, 'connecting')
        db.session.commit()
    settings.set_value('hosting.provider', 'cloudflare')
    if runtime:
        runtime.ensure_tunnel()
    jobs.enqueue('domain.verify')
    audit.record('domain.provisioned', label=host)


def request_disconnect():
    row, _ = connection()
    row.status = 'disconnecting'
    jobs.enqueue('domain.disconnect', max_attempts=100)
    audit.record('domain.disconnect', label='Cloudflare')


@jobs.handler('domain.disconnect')
def disconnect(payload):
    row, data = connection()
    if row.status != 'disconnecting':
        return
    client = Client(data)
    runtime = current_app.extensions.get('copydesk_desktop')
    if runtime:
        runtime.stop_tunnel()
    if data.get("worker"):
        from copydesk import edge

        edge.disable({})
        row, data = connection()
    # Conditional undo refuses records changed outside QuillSail.
    for record in db.session.scalars(select(ManagedRecord)).all():
        undo_record(client, record)
        db.session.commit()
    if data.get('tunnel'):
        client.call('DELETE', '/accounts/' + data['account'] + '/cfd_tunnel/' + data['tunnel'])
    if runtime:
        runtime.set_public_url('')
    from copydesk.hosting_oauth import revoke

    revoke(data)
    db.session.delete(row)
    settings.set_value('hosting.provider', 'manual')
    settings.set_value('hosting.zones', [])
    settings.set_value('hosting.checks', {})
    audit.record('domain.disconnected', label='Cloudflare')


def record_value(record):
    value = record['content']
    if record['type'] == 'TXT' and value.startswith('"'):
        import dns.exception
        import dns.rdata

        try:
            return b''.join(dns.rdata.from_text('IN', 'TXT', value).strings).decode()
        except (dns.exception.DNSException, UnicodeError):
            raise domains.DomainProblem('The provider returned an unreadable TXT record. Review DNS manually.') from None
    return value.rstrip('.').lower() if record['type'] == 'CNAME' else value


def undo_record(client, record):
    path = '/zones/' + record.zone_id + '/dns_records'
    found = client.pages(path, name=record.name, type=record.type)
    current = next((r for r in found if r['id'] == record.provider_id), None)
    if current is not None:
        if record.previous is not None and record_value(current) == record_value({'type': record.type, 'content': record.previous}):
            db.session.delete(record)
            return
        if record_value(current) != record.value:
            raise domains.DomainProblem('A managed DNS record was changed elsewhere. Restore its managed value before disconnecting or undoing.')
        if record.previous is None:
            client.call('DELETE', path + '/' + record.provider_id)
        else:
            client.call('PATCH', path + '/' + record.provider_id, json={'content': record.previous})
    elif record.previous is not None:
        raise domains.DomainProblem('A modified record was removed elsewhere. Review DNS before undoing.')
    expected = record_value({'type': record.type, 'content': record.previous}) if record.previous is not None else None
    values = domains.authoritative_values(record.name, record.type)
    if (expected is not None and expected not in values) or (expected is None and record.value in values):
        raise jobs.RetryLater('Waiting for authoritative DNS to confirm undo.', delay=60)
    audit.record('domain.record_undone', label=record.name)
    db.session.delete(record)


def apply_record(client, zone, spec, previous=None):
    """Checkpoint intent before a write, and recover an uncertain response by marker.

    Never adopt an unmarked pre-existing record. SPF is the one allowed update.
    """
    marker = 'Managed by QuillSail ' + domains.instance_id()
    path = '/zones/' + zone + '/dns_records'
    found = client.pages(path, name=spec.name)
    journal = db.session.scalar(select(ManagedRecord).where(
        ManagedRecord.zone_id == zone, ManagedRecord.name == spec.name, ManagedRecord.type == spec.type))
    if journal and journal.value != spec.value:
        raise domains.DomainProblem('Undo the previous managed change before changing this record again.')
    if previous is not None:
        candidates = [r for r in found if r['type'] == spec.type and (record_value(r) == record_value({'type': spec.type, 'content': previous}) or (journal and r['id'] == journal.provider_id and record_value(r) == spec.value))]
        if len(candidates) != 1:
            raise domains.DomainProblem('The SPF record changed. Check DNS and start again.')
        record = candidates[0]
        if journal is None:
            journal = ManagedRecord(zone_id=zone, provider_id=record['id'], name=spec.name,
                type=spec.type, value=spec.value, previous=record['content'], purpose=spec.purpose)
            db.session.add(journal)
            db.session.commit()
        if record_value(record) != spec.value:
            client.call('PATCH', path + '/' + record['id'], json={'content': spec.value})
    else:
        owned = [r for r in found if r.get('comment') == marker and r['type'] == spec.type and record_value(r) == spec.value]
        # Other TXT records coexist; CNAME and DKIM names must be empty.
        conflict = [r for r in found if r not in owned and (
            spec.purpose == 'DKIM' or spec.type == 'CNAME' or r['type'] == 'CNAME' or
            (r['type'] == 'TXT' and (record_value(r).lower().startswith('v=spf1') if spec.purpose == 'SPF' else record_value(r).lower().startswith('v=dmarc1'))))]
        if conflict:
            raise domains.DomainProblem('An existing DNS value conflicts. No record was replaced.')
        record = owned[0] if owned else client.call('POST', path, json={
            'name': spec.name, 'type': spec.type, 'content': spec.value, 'ttl': 300, 'comment': marker})
        if journal is None:
            db.session.add(ManagedRecord(zone_id=zone, provider_id=record['id'], name=spec.name,
                type=spec.type, value=spec.value, purpose=spec.purpose))
    db.session.commit()
    result = domains.check_record(spec)
    if result['status'] != 'Found':
        raise jobs.RetryLater('The DNS write is saved; waiting for all authoritative nameservers.', delay=60)
    audit.record('domain.record_verified', label=spec.name)


@jobs.handler('domain.email')
def email_dns(payload):
    row, data = connection()
    if row.status in {'disconnecting', 'authorization_required'}:
        return
    domain = payload['domain']
    zones = settings.get('hosting.zones', [])
    zone = next((z for z in zones if z['id'] == data.get('zone')), None)
    if not zone or not (domain == zone['name'] or domain.endswith('.' + zone['name'])):
        raise domains.DomainProblem('The sending domain is outside the connected zone. Use the manual records.')
    client = Client(data)
    include = domains.EMAIL_PRESETS[payload['preset']][1]
    # Recover a checkpointed SPF merge after a lost response.
    journal = db.session.scalar(select(ManagedRecord).where(ManagedRecord.name == domain, ManagedRecord.purpose == 'SPF'))
    values = domains.authoritative_values(domain, 'TXT')
    value = journal.value if journal else domains.merge_spf(values, include)
    previous = journal.previous if journal else next((v for v in values if v.lower().startswith('v=spf1')), None)
    if journal or value not in values:
        apply_record(client, zone['id'], domains.RecordSpec(domain, 'TXT', value, 'SPF'), previous)
    dmarc_name = '_dmarc.' + domain
    existing = domains.authoritative_values(dmarc_name, 'TXT')
    if not existing:
        apply_record(client, zone['id'], domains.RecordSpec(dmarc_name, 'TXT', 'v=DMARC1; p=none;', 'DMARC'))
    for spec in domains.parse_dkim(payload.get('dkim', ''), domain):
        apply_record(client, zone['id'], spec)
    settings.set_value('hosting.email_checks', {
        'SPF': domains.check_record(domains.RecordSpec(domain, 'TXT', value, 'SPF'))['status'],
        'DMARC': 'Found' if domains.authoritative_values(dmarc_name, 'TXT') else 'Missing',
        'DKIM': 'Found' if payload.get('dkim', '').strip() else 'Not supplied'})


@jobs.handler('domain.undo')
def undo_job(payload):
    _, data = connection()
    record = db.session.get(ManagedRecord, payload['record'])
    if record:
        undo_record(Client(data), record)


def reauthorize(token):
    token = str(token or '').strip()
    if not token or len(token) > 4096 or any(c.isspace() for c in token):
        raise domains.DomainProblem('Paste a Cloudflare API token.')
    row, data = connection()
    if row.status == 'disconnecting':
        raise domains.DomainProblem('Finish disconnecting before changing credentials.')
    data['pending_token'] = token
    save(data, 'checking')
    jobs.enqueue('domain.reauthorize')
    audit.record('domain.reauthorize', label='Cloudflare')


@jobs.handler('domain.reauthorize')
def verify_reauthorization(payload):
    row, data = connection()
    candidate = data.get('pending_token')
    if not candidate or row.status == 'disconnecting':
        return
    client = Client(candidate)
    result = client.call('GET', '/user/tokens/verify')
    if result.get('status') != 'active':
        raise domains.DomainProblem('This Cloudflare token is not active.')
    if data.get('zone'):
        zone = client.call('GET', '/zones/' + data['zone'])
        if data.get('account') and zone['account']['id'] != data['account']:
            raise domains.DomainProblem('Use a token for the connected domain and account.')
    from copydesk.hosting_oauth import revoke

    revoke(data)
    data['token'] = data.pop('pending_token')
    data.pop('oauth', None)
    data.pop('client', None)
    save(data, 'connected')
    runtime = current_app.extensions.get('copydesk_desktop')
    if runtime:
        runtime.hosting_status = ''
    jobs.enqueue('domain.provision' if data.get('zone') else 'domain.connect')
