"""WSGI entry point for production servers.

    gunicorn "copydesk.wsgi:app"

The configuration file is found the same way as the `copydesk` command finds
it: the COPYDESK_CONFIG environment variable, then ./copydesk.toml, then
/etc/copydesk/copydesk.toml.
"""

from copydesk.app import create_app

app = create_app()
