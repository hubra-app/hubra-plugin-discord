# Security policy

This policy covers the Discord plugin for Hubra in this repository, including the Hubra bot.

Problems in Hubra itself (the backend, the club app, SSO, manage) belong in
[hubra-app/hubra](https://github.com/hubra-app/hubra/security/advisories/new).
If you are not sure which repository is affected, report it here and we will
move it.

## Supported versions

Only the latest release gets security fixes. Clubs should update the plugin
from the Marketplace as soon as a fix is released. `main` is fixed
first, and the fix ships in the next release.

## Reporting a vulnerability

Please do not open a public issue, discussion or pull request for a security
problem.

Report it privately through GitHub instead:
[**Report a vulnerability**](https://github.com/hubra-app/hubra-plugin-discord/security/advisories/new)
(or the Security tab › Report a vulnerability). Only the maintainers can see
the report.

Please include:

- what is affected (component, page or endpoint, and the version or commit),
- the steps to reproduce it, with a proof of concept if you have one,
- what an attacker could do with it, and what they need first (an account,
  a role in a club, network access),
- whether it is already known or public anywhere.

Please test only against your own install, and do not access, change or
delete other people's data.

## What happens next

- We confirm that we got your report within **3 working days**.
- We tell you our assessment within **10 working days**: whether we can
  reproduce it, how severe we think it is, and when we plan to fix it.
- We keep you updated in the advisory until it is fixed. Critical and high
  issues are fixed first, usually within 30 days.
- When the fix is released we publish the advisory, and we credit you if you
  want that.

Hubra is maintained by a small team, so please allow for these times before
going public. We are happy to agree on a disclosure date with you.

