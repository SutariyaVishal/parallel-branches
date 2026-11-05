# Dev-mode browser

`pb.py browser <branch>` starts Chrome or Chromium with these flags:

```
--user-data-dir=<throwaway profile>  --remote-debugging-port=<port>
--disable-web-security  --disable-site-isolation-trials
--no-first-run  --no-default-browser-check
```

The profile lives in `.git/parallel-branches/profiles/<branch>` and is emptied before each launch and deleted by `down`. It never touches the user's real profile. The window opens one URL: the branch's own `http://localhost:<port>` plus an optional `--path`.

## What it fixes

CORS. A page on `localhost:4430` can read responses from an API on another port or host that sends no CORS headers.

Measured on Chrome 156 (macOS): a host page fetching a no-CORS remote was blocked in a normal Chrome and worked with the flags, in both headless and headed mode. An agent attached over CDP with `playwright-core`. See `docs/phase0-results.md`.

## What it does not fix

- OAuth and OIDC redirect URI mismatches. The identity server rejects the redirect; the browser never gets a say.
- Servers that reject requests by `Origin`, CSRF tokens or `Host`.
- Cookie rules such as `SameSite`, and cookies shared across ports on `localhost`.
- Anything in a native app or another browser.

## Risks

- Web security off means any site loaded in that window can read other origins. Use it only for the app it opened. Do not browse elsewhere in it.
- The DevTools port accepts connections from any local process. It listens on 127.0.0.1 only, and stops when `down` closes the browser.
- Chrome ignores `--disable-web-security` without a non-default `--user-data-dir`, which is why the profile is always separate.

## Not verified

Attaching through Playwright MCP's own `--cdp-endpoint` option (only `playwright-core` was tested), and Chrome versions other than 156.
