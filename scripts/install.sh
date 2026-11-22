#!/usr/bin/env bash
# Installs or updates Screenshot Buffer on macOS from GitHub Releases.
#
#   curl -fsSL https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.sh | bash
#
# Options (pass after "bash -s --"):
#   --start-at-login   also start Screenshot Buffer when you log in
#   --version vX.Y.Z   install a specific release instead of the latest
#   --no-launch        do not start the app after installing
#   --uninstall        remove the app (settings are kept)
#
#   curl -fsSL https://github.com/edward3423/screenshotbuffer/releases/latest/download/install.sh | bash -s -- --start-at-login
#
# Installs into /Applications when writable, otherwise ~/Applications. No sudo needed.

# Everything runs from main(), so a partially downloaded script does nothing.
set -euo pipefail

REPO="edward3423/screenshotbuffer"
APP_NAME="Screenshot Buffer"
APP_BUNDLE="Screenshot Buffer.app"
EXE_REL="Contents/MacOS/ScreenshotBuffer"
AGENT_LABEL="io.github.edward3423.screenshotbuffer"
AGENT_PLIST="$HOME/Library/LaunchAgents/$AGENT_LABEL.plist"

say() { printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

find_installed() {
    for dir in "/Applications" "$HOME/Applications"; do
        if [ -d "$dir/$APP_BUNDLE" ]; then
            printf '%s\n' "$dir/$APP_BUNDLE"
            return 0
        fi
    done
    return 1
}

stop_running() {
    # Ask any running copy to quit (works across versions), then make sure.
    local exe="$1"
    if [ -x "$exe" ]; then "$exe" --quit >/dev/null 2>&1 || true; fi
    for _ in $(seq 1 25); do
        pgrep -f "$APP_BUNDLE/$EXE_REL" >/dev/null 2>&1 || return 0
        sleep 0.2
    done
    pkill -f "$APP_BUNDLE/$EXE_REL" >/dev/null 2>&1 || true
    sleep 0.5
}

uninstall() {
    local app
    if ! app="$(find_installed)"; then
        say "$APP_NAME is not installed."
        rm -f "$AGENT_PLIST"
        return 0
    fi
    stop_running "$app/$EXE_REL"
    rm -rf "$app"
    rm -f "$AGENT_PLIST"
    say "$APP_NAME was uninstalled. Settings in ~/Library/Application Support/ScreenshotBuffer were kept."
}

write_login_agent() {
    local app="$1"
    mkdir -p "$(dirname "$AGENT_PLIST")"
    cat >"$AGENT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$AGENT_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>$app</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST
}

main() {
    local version="latest" start_at_login=0 launch=1 do_uninstall=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --start-at-login) start_at_login=1 ;;
            --no-launch) launch=0 ;;
            --uninstall) do_uninstall=1 ;;
            --version)
                [ $# -ge 2 ] || die "--version needs a value, e.g. --version v0.1.0"
                version="$2"
                shift
                ;;
            -h | --help)
                sed -n '2,16p' "$0" 2>/dev/null || true
                return 0
                ;;
            *) die "unknown option: $1" ;;
        esac
        shift
    done

    [ "$(uname -s)" = "Darwin" ] || die "this installer is for macOS. On Windows, use the PowerShell command from the README."

    if [ "$do_uninstall" = 1 ]; then
        uninstall
        return 0
    fi

    local arch
    case "$(uname -m)" in
        arm64) arch="arm64" ;;
        x86_64) arch="x86_64" ;;
        *) die "unsupported CPU architecture: $(uname -m)" ;;
    esac
    local asset="ScreenshotBuffer-macos-$arch.zip"
    local base
    if [ "$version" = "latest" ]; then
        base="https://github.com/$REPO/releases/latest/download"
    else
        base="https://github.com/$REPO/releases/download/$version"
    fi

    local tmp
    tmp="$(mktemp -d)"
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" EXIT

    say "Downloading $APP_NAME ($version, $arch)..."
    curl -fsSL -o "$tmp/$asset" "$base/$asset" || die "could not download $base/$asset"
    curl -fsSL -o "$tmp/SHA256SUMS" "$base/SHA256SUMS" || die "could not download $base/SHA256SUMS"
    local expected actual
    expected="$(awk -v f="$asset" '$2 == f || $2 == "*" f { print $1; exit }' "$tmp/SHA256SUMS")"
    [ -n "$expected" ] || die "no checksum for $asset in SHA256SUMS"
    actual="$(shasum -a 256 "$tmp/$asset" | awk '{ print $1 }')"
    [ "$expected" = "$actual" ] || die "checksum mismatch for $asset (expected $expected, got $actual); try again"

    ditto -x -k "$tmp/$asset" "$tmp/extract"
    [ -x "$tmp/extract/$APP_BUNDLE/$EXE_REL" ] || die "the download does not contain $APP_BUNDLE"

    local dest_dir="/Applications"
    if [ ! -w "$dest_dir" ]; then
        dest_dir="$HOME/Applications"
        mkdir -p "$dest_dir"
    fi
    local dest="$dest_dir/$APP_BUNDLE"

    local existing
    if existing="$(find_installed)"; then
        stop_running "$existing/$EXE_REL"
    fi
    stop_running "$tmp/extract/$APP_BUNDLE/$EXE_REL"
    if [ -n "${existing:-}" ] && [ "$existing" != "$dest" ]; then rm -rf "$existing"; fi
    rm -rf "$dest"
    ditto "$tmp/extract/$APP_BUNDLE" "$dest"
    xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true

    if [ "$start_at_login" = 1 ]; then
        write_login_agent "$dest"
    elif [ -f "$AGENT_PLIST" ]; then
        write_login_agent "$dest"  # keep an earlier choice, pointing at the new location
    fi

    local installed_version
    installed_version="$(/usr/bin/defaults read "$dest/Contents/Info" CFBundleShortVersionString 2>/dev/null || echo "")"
    say ""
    say "$APP_NAME $installed_version installed to $dest"
    [ -f "$AGENT_PLIST" ] && say "  Starts automatically when you log in."
    say "  Cmd+Shift+9 copies the screen from a few seconds ago; Cmd+Shift+0 shows or hides the preview."
    say "  On first start, allow Screen Recording for $APP_NAME in System Settings > Privacy & Security,"
    say "  then start it again."

    if [ "$launch" = 1 ]; then
        open "$dest"
    fi
}

main "$@"
