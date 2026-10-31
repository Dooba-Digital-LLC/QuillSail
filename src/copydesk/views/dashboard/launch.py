"""Administration for launch state and provider-independent hosting."""
from flask import current_app, flash, redirect, render_template, request, url_for
from sqlalchemy import select

from copydesk import audit, cloudflare, domains, hosting_oauth, jobs, launch, permissions, settings
from copydesk.auth import current_user
from copydesk.db import db
from copydesk.helpers import state
from copydesk.models import HostingConnection, ManagedRecord
from copydesk.views.dashboard import bp


@bp.route('/launch', methods=['GET', 'POST'])
@permissions.require('manage_settings')
def launch_page():
    errors = {}
    if request.method == 'POST':
        action = request.form.get('action')
        if action in {'live', 'soon'}:
            launch.change(action)
        elif action == 'save':
            for definition in settings.for_tab('launch'):
                value, problem = settings.parse_form_value(definition, request.form.get(definition.key))
                if problem:
                    errors[definition.key] = problem
                else:
                    settings.set_value(definition.key, value)
            if not errors:
                audit.record('settings.updated', label='Launch page')
        else:
            errors['action'] = 'Choose a launch action.'
        if not errors:
            if settings.get('hosting.edge'):
                jobs.enqueue('edge.snapshot')
            db.session.commit()
            return redirect(url_for('dash.launch_page'))
        db.session.rollback()
        settings.invalidate()
    return render_template('dashboard/launch.html', errors=errors, checks=settings.get('hosting.checks', {}),
        signup_available=launch.signup_available(), public_url=state().config.base_url), 422 if errors else 200


@bp.route('/domain', methods=['GET', 'POST'])
@permissions.require('manage_settings')
def domain():
    errors = {}
    desktop = current_app.extensions.get('copydesk_desktop')
    if request.method == 'POST':
        try:
            action = request.form.get('action')
            if action == 'oauth.setup':
                hosting_oauth.configure(request.form.get('client_id'), request.form.get('relay_url'))
                audit.record('domain.oauth_configured', label='Cloudflare sign-in')
                db.session.commit()
                flash('Cloudflare sign-in is configured. You can now connect your account.', 'success')
                return redirect(url_for('dash.domain'))
            elif action in {'oauth', 'oauth.reconnect'}:
                url = hosting_oauth.start(current_user, reconnect=action == 'oauth.reconnect')
                db.session.commit()
                return redirect(url)
            elif action == 'connect':
                cloudflare.connect(request.form.get('token'))
            elif action == 'reauthorize':
                cloudflare.reauthorize(request.form.get('token'))
            elif action == 'provision':
                cloudflare.choose(request.form.get('zone'), request.form.get('subdomain', 'news'))
            elif action in {'edge.enable', 'edge.disable'}:
                if not desktop:
                    raise domains.DomainProblem('The offline fallback is for desktop publications.')
                _, credentials = cloudflare.connection()
                if action == 'edge.enable' and credentials.get('oauth'):
                    url = hosting_oauth.start(current_user, for_edge=True)
                    db.session.commit()
                    return redirect(url)
                jobs.enqueue(action, max_attempts=100)
                audit.record(action + '.requested', label='Offline fallback')
            elif action in {'email', 'email.plan'}:
                domain = domains.sending_domain()
                preset = request.form.get('preset')
                if domain is None or preset not in domains.EMAIL_PRESETS:
                    raise domains.DomainProblem('Choose a provider for a custom sending domain.')
                dkim = request.form.get('dkim', '')
                domains.parse_dkim(dkim, domain)
                jobs.enqueue('domain.email' if action == 'email' else 'domain.email.plan', {'domain': domain, 'preset': preset, 'dkim': dkim})
                audit.record('domain.email', label=domain)
            elif action == 'undo':
                record = db.session.get(ManagedRecord, int(request.form.get('record', '0')))
                if record is None:
                    raise domains.DomainProblem('Choose a managed record.')
                jobs.enqueue('domain.undo', {'record': record.id})
                audit.record('domain.undo', label=record.name)
            elif action == 'retry':
                row, data = cloudflare.connection()
                jobs.enqueue('domain.disconnect' if row.status == 'disconnecting' else ('domain.provision' if data.get('zone') else 'domain.connect'), max_attempts=100)
            elif action == 'disconnect':
                cloudflare.request_disconnect()
            elif action == 'awake' and desktop:
                settings.set_value('hosting.keep_awake', request.form.get('keep_awake') == '1')
                jobs.enqueue('domain.keep_awake')
                audit.record('domain.keep_awake', label='Desktop power preference')
            elif action == 'address' and desktop:
                if db.session.get(HostingConnection, 1):
                    raise domains.DomainProblem('Disconnect Cloudflare before changing the public address manually.')
                desktop.set_public_url(request.form.get('url', ''))
                audit.record('domain.address', label=desktop.public_url)
            elif action == 'records':
                name = domains.hostname(request.form.get('name'))
                kind = request.form.get('type')
                value = request.form.get('value', '').strip()
                if kind not in {'A', 'AAAA', 'CNAME'}:
                    raise domains.DomainProblem('Choose A, AAAA or CNAME.')
                if kind == 'CNAME':
                    value = domains.hostname(value)
                else:
                    import ipaddress

                    address = ipaddress.ip_address(value)
                    if address.version != (4 if kind == 'A' else 6):
                        raise domains.DomainProblem('Use an address matching the record type.')
                settings.set_value('hosting.records', [{'name': name, 'type': kind, 'value': value, 'purpose': 'Website'}])
                audit.record('domain.records', label=name)
            elif action != 'check':
                raise domains.DomainProblem('Choose a domain action.')
            jobs.enqueue('domain.verify')
            db.session.commit()
            flash('The domain check is queued. Reload this page shortly for the results.', 'success')
            return redirect(url_for('dash.domain'))
        except ValueError as exc:
            errors['domain'] = str(exc)
    return render_template('dashboard/domain.html', errors=errors, desktop=desktop,
        oauth_client=hosting_oauth.config(), oauth_available=hosting_oauth.available(), email_presets=domains.EMAIL_PRESETS, managed=db.session.scalars(select(ManagedRecord)).all(),
        hosting=db.session.get(HostingConnection, 1), zones=settings.get('hosting.zones', []),
        public_url=state().config.base_url, records=domains.records(), checks=settings.get('hosting.checks', {})), 422 if errors else 200


@bp.get('/domain/oauth/callback')
@permissions.require('manage_settings')
def domain_oauth_callback():
    try:
        hosting_oauth.finish(current_user, request.args.get('state', ''), request.args.get('code', ''))
        audit.record('domain.oauth', label='Cloudflare')
        db.session.commit()
        flash('Cloudflare is connected. The domain list is being checked.', 'success')
    except domains.DomainProblem as exc:
        flash(str(exc), 'error')
    response = redirect(url_for('dash.domain'))
    response.headers.update({'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})
    return response
