# Security

Do not put passwords, subscriber exports, email tokens, or production configuration in public issues. Report vulnerabilities privately to the repository maintainers through the hosting platform's private vulnerability reporting channel, if enabled, or their published private contact. A project-specific reporting address has not yet been established; establish one before publishing this repository.

Include affected version, reproduction steps using synthetic data, likely impact, and any proposed fix. Do not test against other organizations' installations without authorization.

Deploy behind HTTPS, restrict filesystem access to configuration and backups, keep dependencies current, and run the web server as an unprivileged account. Extensions and themes execute trusted code/templates: install only reviewed packages. Uploaded image URLs are public; do not upload confidential material. Subscriber preference links are bearer credentials and must be kept private.

The initial release has no independent security audit. Only one QuillSail application/database should be configured per Python process. Use separate processes for separate publications.
