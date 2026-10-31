"""Provider-independent public-address validation and authoritative DNS checks."""
from __future__ import annotations

import contextlib
import ipaddress
import json
import secrets
import socket
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

import certifi
import dns.exception
import dns.flags
import dns.message
import dns.query
import dns.rcode
import dns.resolver
import urllib3
from sqlalchemy import select

from copydesk import jobs, settings
from copydesk.db import db
from copydesk.helpers import state
from copydesk.utils import utcnow


class DomainProblem(ValueError):
    pass


def hostname(value):
    value = str(value or '').strip().rstrip('.').lower()
    try:
        value = value.encode('idna').decode('ascii')
        if len(value) > 253 or '.' not in value or any(
            not label or len(label) > 63 or label.startswith('-') or label.endswith('-')
            or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in label)
            for label in value.split('.')
        ):
            raise ValueError
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return value
    except (ValueError, UnicodeError):
        pass
    raise DomainProblem('Enter a domain name, such as news.example.com.')


def dns_name(value):
    """DNS service labels such as _spf may contain underscores."""
    value = str(value or '').strip().lower().rstrip('.')
    labels = value.split('.')
    if len(value) > 253 or len(labels) < 2 or any(not label or len(label) > 63 or
            any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in label) for label in labels):
        raise DomainProblem('The DNS name is invalid.')
    return value


def public_url(value):
    try:
        parts = urlsplit(str(value).strip())
        host = hostname(parts.hostname)
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443) or parts.path not in ('', '/') or parts.query or parts.fragment:
            raise ValueError
        return 'https://' + host
    except (ValueError, UnicodeError):
        raise DomainProblem('Use an HTTPS address such as https://news.example.com, without a path or sign-in details.') from None


def instance_id():
    value = settings.get('system.instance_id')
    if not value:
        value = secrets.token_hex(16)
        settings.set_value('system.instance_id', value)
    return value


@dataclass(frozen=True)
class RecordSpec:
    name: str
    type: str
    value: str
    purpose: str = 'Website'


def _resolver():
    resolver = dns.resolver.Resolver()
    resolver.timeout = 2
    resolver.lifetime = 5
    return resolver


def authoritative_values(name, kind):
    """Query every authoritative server; never treat timeouts as an empty name.

    Recursive DNS is used only for delegation discovery and nameserver addresses.
    The final answer must be authoritative, including negative answers.
    """
    name = name.rstrip('.')
    resolver = _resolver()
    zone = dns.resolver.zone_for_name(dns.name.from_text(name), resolver=resolver)
    servers = resolver.resolve(zone, 'NS')
    answers = []
    for server in servers:
        addresses = []
        for family in ('A', 'AAAA'):
            with contextlib.suppress(dns.resolver.NoAnswer):
                addresses.extend(str(r) for r in resolver.resolve(str(server.target), family))
        if not addresses:
            raise DomainProblem('A nameserver has no reachable address. Try the check again later.')
        reply = None
        for address in addresses:
            try:
                query = dns.message.make_query(name, kind)
                reply = dns.query.udp(query, address, timeout=3)
                if reply.flags & dns.flags.TC:
                    reply = dns.query.tcp(query, address, timeout=3)
                if reply.flags & dns.flags.AA and reply.rcode() in (dns.rcode.NOERROR, dns.rcode.NXDOMAIN):
                    break
                reply = None
            except (dns.exception.DNSException, OSError):
                continue
        if reply is None:
            raise DomainProblem('An authoritative nameserver did not answer. Try again later.')
        values = []
        for rrset in reply.answer:
            if rrset.name != dns.name.from_text(name) or dns.rdatatype.to_text(rrset.rdtype) != kind:
                continue
            for item in rrset:
                values.append(b''.join(item.strings).decode() if kind == 'TXT' else item.to_text().rstrip('.'))
        answers.append(sorted(values))
    if not answers or any(answer != answers[0] for answer in answers):
        raise DomainProblem('The nameservers disagree. Wait for DNS to finish updating.')
    return answers[0]


def check_record(spec):
    try:
        values = authoritative_values(spec.name, spec.type)
        expected = spec.value.rstrip('.') if spec.type != 'TXT' else spec.value
        status = 'Found' if expected in values else ('Different' if values else 'Missing')
        return {**asdict(spec), 'status': status, 'values': values}
    except (dns.exception.DNSException, DomainProblem, OSError, UnicodeError):
        return {**asdict(spec), 'status': 'Unknown', 'values': []}


def reachable(url):
    """Pin a globally routable address, retain TLS hostname checks, never redirect.

    Resolving once and connecting to that IP also prevents DNS rebinding to a
    loopback/private service between validation and the actual HTTPS request.
    """
    pool = None
    try:
        parts = urlsplit(url)
        host = hostname(parts.hostname)
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443) or parts.query or parts.fragment:
            return False
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            return False
        address = sorted(addresses)[0]
        pool = urllib3.HTTPSConnectionPool(address, port=443, assert_hostname=host,
            server_hostname=host, cert_reqs='CERT_REQUIRED', ca_certs=certifi.where())
        response = pool.request('GET', parts.path.rstrip('/') + '/healthz', headers={'Host': host},
            timeout=urllib3.Timeout(connect=3, read=5), retries=False, redirect=False, preload_content=False)
        try:
            content = response.read(16385)
            return response.status == 200 and len(content) <= 16384 and json.loads(content).get('instance_id') == instance_id()
        finally:
            response.release_conn()
    except (urllib3.exceptions.HTTPError, OSError, ValueError, AttributeError):
        return False
    finally:
        if pool:
            pool.close()


def records():
    return [RecordSpec(**value) for value in settings.get('hosting.records', [])]


@jobs.handler('domain.verify')
def verify(payload):
    url = state().config.base_url
    result = {'records': [check_record(spec) for spec in records()], 'https': reachable(url)}
    from copydesk import cloudflare
    from copydesk.models import HostingConnection, ManagedRecord

    row = db.session.get(HostingConnection, 1)
    if row and row.status not in {'disconnecting', 'authorization_required'}:
        _, data = cloudflare.connection()
        if data.get('tunnel'):
            client = cloudflare.Client(data)
            website = db.session.scalar(select(ManagedRecord).where(ManagedRecord.purpose == 'website'))
            provider_ok = False
            if website:
                record = client.call('GET', '/zones/' + website.zone_id + '/dns_records/' + website.provider_id)
                provider_ok = record.get('type') == 'CNAME' and record.get('content') == website.value and record.get('proxied') is True
            try:
                addresses = authoritative_values(data['hostname'], 'A') or authoritative_values(data['hostname'], 'AAAA')
                dns_ok = bool(addresses) and all(ipaddress.ip_address(ip).is_global for ip in addresses)
            except (dns.exception.DNSException, DomainProblem, ValueError, OSError):
                dns_ok = False
            result['cloudflare'] = provider_ok and dns_ok
            if result['https'] and result['cloudflare']:
                row.status = 'online'
            else:
                row.status = 'connecting'
    settings.set_value('hosting.checks', result)
    settings.set_value('hosting.checked_at', utcnow().isoformat())
    db.session.commit()
    if row and row.status == 'connecting':
        raise jobs.RetryLater('Waiting for authoritative DNS and this publication’s HTTPS health response.', delay=60)


EMAIL_PRESETS = {
    'google': ('Google Workspace', '_spf.google.com'),
    'microsoft': ('Microsoft 365', 'spf.protection.outlook.com'),
    'ses': ('Amazon SES', 'amazonses.com'),
    'postmark': ('Postmark', 'spf.mtasv.net'),
    'mailgun': ('Mailgun', 'mailgun.org'),
    'sendgrid': ('SendGrid', 'sendgrid.net'),
    'resend': ('Resend', 'amazonses.com'),
}
CONSUMER_DOMAINS = {'gmail.com', 'googlemail.com', 'outlook.com', 'hotmail.com', 'live.com', 'yahoo.com', 'icloud.com', 'aol.com'}


def sending_domain():
    from copydesk.email_connections import effective_mail_config

    value = effective_mail_config().from_email.rsplit('@', 1)[-1].lower()
    return None if value in CONSUMER_DOMAINS else hostname(value)


def spf_lookups(value, resolve=authoritative_values, seen=None):
    """Count the worst-case lookup path, with a strict recursion/budget bound."""
    seen = set(seen or ())
    count = 0
    for term in value.split()[1:]:
        term = term.lstrip('+-~?')
        if term.startswith('include:'):
            name = dns_name(term[8:])
            if name in seen or len(seen) >= 10:
                raise DomainProblem('SPF includes form a loop or exceed ten lookups.')
            records = [r for r in resolve(name, 'TXT') if r.lower().startswith('v=spf1')]
            if len(records) != 1:
                raise DomainProblem('An SPF include has no single usable SPF record.')
            count += 1 + spf_lookups(records[0], resolve, seen | {name})
        elif term.startswith('redirect='):
            raise DomainProblem('An SPF redirect needs manual review. No record was changed.')
        elif term.split(':', 1)[0].split('/', 1)[0] in {'a', 'mx', 'ptr', 'exists'}:
            # MX can expand into many addresses; automated merging cannot safely
            # bound this with just the policy text. Leave it to the operator.
            if term.startswith(('mx', 'ptr')):
                raise DomainProblem('This SPF policy uses MX or PTR lookups. Review its lookup budget manually.')
            count += 1
        elif '%' in term:
            raise DomainProblem('SPF macros need manual review.')
        elif term != 'all' and not term.startswith(('ip4:', 'ip6:', 'exp=')):
            raise DomainProblem('An SPF mechanism is not supported. Review this policy manually.')
        if count > 10:
            raise DomainProblem('This SPF policy would exceed ten DNS lookups.')
    return count


def merge_spf(values, include, resolve=authoritative_values):
    import re

    include = dns_name(include)
    policies = [v for v in values if v.lower().startswith('v=spf1')]
    if len(policies) > 1:
        raise DomainProblem('There is more than one SPF record. Fix that before making changes.')
    old = policies[0] if policies else None
    value = old or 'v=spf1 ~all'
    if any(term.lower().startswith('redirect=') for term in value.split()):
        raise DomainProblem('SPF uses redirect=. Review it manually; nothing was changed.')
    wanted = 'include:' + include
    if wanted not in value.split():
        match = re.search(r'(?<!\S)[+?~-]?all(?=\s|$)', value)
        if not match:
            raise DomainProblem('This SPF policy has no all term. Review it manually.')
        value = value[:match.start()] + wanted + ' ' + value[match.start():]
    spf_lookups(value, resolve)
    return value


def parse_dkim(source, domain):
    specs = []
    for line in source.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            raise DomainProblem('Use one DKIM record per line: name TXT or CNAME value.')
        name, kind, value = parts
        name = name.lower().rstrip('.')
        kind = kind.upper()
        if not name.endswith('._domainkey.' + domain) or kind not in {'TXT', 'CNAME'} or len(value) > 8192:
            raise DomainProblem('DKIM records must be TXT or CNAME names under _domainkey in the sending domain.')
        # DNS allows underscores in service labels; validate the domain suffix.
        selector = name.removesuffix('._domainkey.' + domain)
        if not selector or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in selector):
            raise DomainProblem('The DKIM selector is invalid.')
        if kind == 'CNAME':
            value = dns_name(value)
        elif value.startswith('"'):
            import dns.rdata

            try:
                value = b''.join(dns.rdata.from_text('IN', 'TXT', value).strings).decode()
            except (dns.exception.DNSException, UnicodeError):
                raise DomainProblem('The quoted DKIM value could not be read.') from None
        if kind == 'TXT' and not ('p=' in value and 'v=DKIM1' in value):
            raise DomainProblem('Paste the complete public DKIM key from your email provider.')
        specs.append(RecordSpec(name, kind, value, 'DKIM'))
    if len(specs) > 12:
        raise DomainProblem('Use no more than twelve DKIM records at once.')
    return specs


@jobs.handler('domain.email.plan')
def email_plan(payload):
    """Save copyable records for any DNS provider without making DNS writes."""
    domain = sending_domain()
    if domain is None:
        settings.set_value('hosting.email_records', [])
        settings.set_value('hosting.email_note', 'Your email provider manages DNS for this personal email address.')
        return
    include = EMAIL_PRESETS[payload['preset']][1]
    specs = [RecordSpec(domain, 'TXT', merge_spf(authoritative_values(domain, 'TXT'), include), 'SPF')]
    dmarc = authoritative_values('_dmarc.' + domain, 'TXT')
    specs.append(RecordSpec('_dmarc.' + domain, 'TXT', dmarc[0] if dmarc else 'v=DMARC1; p=none;', 'DMARC'))
    specs.extend(parse_dkim(payload.get('dkim', ''), domain))
    settings.set_value('hosting.email_records', [asdict(spec) for spec in specs])
    results = [check_record(spec) for spec in specs]
    settings.set_value('hosting.email_checks', {spec['purpose']: spec['status'] for spec in results})
    settings.set_value('hosting.email_note', 'Copy these records to your DNS provider. Preserve existing DMARC policy and use only one SPF record.')


@jobs.handler('domain.keep_awake')
def keep_awake(payload):
    from flask import current_app

    runtime = current_app.extensions.get('copydesk_desktop')
    if runtime:
        runtime.apply_keep_awake()
