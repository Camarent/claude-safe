ARG BASE=claude-safe:latest
FROM ${BASE}
# desc: extension testing: Firefox ESR + geckodriver + selenium, VS Code test host (@vscode/test-electron) with Electron libs, all under Xvfb

# Firefox ESR from Mozilla's tarball (pinned; ESR can load unsigned add-ons permanently via policies, stable only temporarily).
# Pinned 2026-11-11.
ARG FIREFOX_VERSION=153.7.0esr
ARG GECKODRIVER_VERSION=0.37.1
ARG SELENIUM_VERSION=4.52.0
ARG VSCODE_TEST_ELECTRON_VERSION=3.1.0

# Electron / VS Code runtime libraries and Firefox's GTK stack. xauth/xdotool help scripted GUI runs.
RUN apt-get update && apt-get install -y --no-install-recommends \
    xz-utils bzip2 xauth xdotool libgtk-3-0 libdbus-glib-1-2 libasound2 libx11-xcb1 libxt6 libxtst6 \
    libnss3 libgbm1 libxss1 libsecret-1-0 libxkbfile1 libatk-bridge2.0-0 libdrm2 libcups2 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://ftp.mozilla.org/pub/firefox/releases/${FIREFOX_VERSION}/linux-x86_64/en-US/firefox-${FIREFOX_VERSION}.tar.xz" -o /tmp/ff.tar.xz \
    && tar -xJf /tmp/ff.tar.xz -C /opt && rm /tmp/ff.tar.xz \
    && ln -sf /opt/firefox/firefox /usr/local/bin/firefox \
    && curl -fsSL "https://github.com/mozilla/geckodriver/releases/download/v${GECKODRIVER_VERSION}/geckodriver-v${GECKODRIVER_VERSION}-linux64.tar.gz" | tar -xz -C /usr/local/bin geckodriver \
    && firefox --version && geckodriver --version | head -1

# selenium in its own venv (driver.install_addon(path, temporary=True) loads an unsigned extension).
RUN uv venv -q /opt/ext-venv && uv pip install -q --python /opt/ext-venv/bin/python "selenium==${SELENIUM_VERSION}" \
    && chmod -R a+rX /opt/ext-venv

# VS Code test host: the package is global; the VS Code build itself is downloaded on first test run into
# VSCODE_TEST_CACHE (a mounted directory keeps it across --rm). Run with --no-sandbox (no user namespaces here).
RUN npm install -g "@vscode/test-electron@${VSCODE_TEST_ELECTRON_VERSION}"
ENV NODE_PATH=/usr/local/lib/node_modules MOZ_DISABLE_CONTENT_SANDBOX=1
