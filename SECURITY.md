# Security policy

This is a testing library. It should not be enabled in production code paths.

Report vulnerabilities privately through GitHub's "Report a vulnerability" (Security tab) rather than a public issue. Expect an acknowledgement within 7 days. Supported versions: the latest minor release.

Notes:

- Call records keep request headers and bodies in memory, and the JSON report includes method and URL (not headers or bodies). Do not commit reports from tests that use real credentials.
- `LostResponse` and `Duplicate` really send the request to the upstream. Point them at test doubles or sandboxes.
