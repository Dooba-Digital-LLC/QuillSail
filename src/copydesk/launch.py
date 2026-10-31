"""Publication launch controls. Older installations remain live."""
from flask import abort, redirect, request, url_for

from copydesk import audit, auth, settings
from copydesk.helpers import state

PUBLIC_ENDPOINTS = {
    'public.home', 'public.health', 'public.robots', 'public.upload',
    'public.theme_asset', 'public.custom_css', 'static',
    'subscribe.subscribe', 'subscribe.thanks', 'subscribe.confirm',
    'subscribe.manage', 'subscribe.unsubscribe',
}
LOGIN_ENDPOINTS = {
    'auth.login', 'auth.logout', 'auth.forgot', 'auth.reset',
    'auth.forgot_password', 'auth.reset_password',
}


def is_soon():
    return settings.get('launch.state', 'live') == 'soon'


def editor():
    from copydesk.views.dashboard import can_use_dashboard

    return auth.current_user.is_authenticated and auth.current_user.is_verified and can_use_dashboard(auth.current_user)


def signup_available():
    from copydesk.email_connections import public_status

    return settings.get('launch.signup') and public_status()['connected']


def gate():
    if not is_soon() or editor():
        return None
    endpoint = request.endpoint or ''
    if endpoint in PUBLIC_ENDPOINTS or endpoint in LOGIN_ENDPOINTS or endpoint.startswith('setup.'):
        return None
    if endpoint in {'public.feed', 'public.sitemap'}:
        abort(404)
    return redirect(url_for('public.home'))


def change(new_state):
    if new_state not in {'soon', 'live'}:
        raise ValueError('Choose launching soon or live.')
    old = settings.get('launch.state', 'live')
    if old == new_state:
        return
    settings.set_value('launch.state', new_state)
    audit.record('publication.launched', label=new_state, details={'old': old, 'new': new_state})
    state().hooks.fire('publication.launched', old=old, new=new_state, actor=auth.current_user)
