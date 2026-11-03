#!/bin/zsh
# Builds the double-click app and a disk image to share it with:
#
#   web/package.sh [version]        dist/RePod.app and dist/RePod-<version>.dmg
#
# The app holds everything it needs: ipodctl and the launcher for both kinds of Mac, the page, and a Python runtime
# for each, so nobody has to install anything. PYTHON_RUNTIME=system leaves the runtime out, and the app then uses the
# Mac's own, which needs Apple's command-line tools. SIGN_ID="Developer ID Application: ..." signs it properly; without
# that it is signed ad hoc, and a Mac that downloads it asks the user to allow it in System Settings.
set -euo pipefail

name="RePod"
identifier=app.repod.web
root=${0:A:h:h}
version=${1:-$(git -C "$root" describe --tags --always 2>/dev/null || echo 0.0.0)}
work=${PACKAGE_WORK:-$root/.build/package}
runtime=${PYTHON_RUNTIME:-bundled}
app="$root/dist/$name.app"

# The Python runtime: pinned by release and checksum, from github.com/astral-sh/python-build-standalone.
release=20261013
python=3.12.15
typeset -A sums=(aarch64 9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de x86_64 d09998f85752b646873a8a6bc7eea442a0d251b1c8d2413aa8ea06ef131c3335)

cd "$root"
rm -rf "$app"
mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources/bin" "$app/Contents/Resources/web" "$work" dist
resources="$app/Contents/Resources"

for arch in arm64 x86_64; do
    swift build -c release --product ipodctl --triple $arch-apple-macosx14.0 --scratch-path "$work/$arch"
    swift build -c release --product AppLauncher --triple $arch-apple-macosx14.0 --scratch-path "$work/$arch"
done
lipo -create "$work"/{arm64,x86_64}/release/ipodctl -output "$resources/bin/ipodctl"
lipo -create "$work"/{arm64,x86_64}/release/AppLauncher -output "$app/Contents/MacOS/$name"

if [[ $runtime == bundled ]]; then
    for arch in aarch64 x86_64; do
        file=cpython-$python+$release-$arch-apple-darwin-install_only_stripped.tar.gz
        [[ -f "$work/$file" ]] || curl -fsSL -o "$work/$file" "https://github.com/astral-sh/python-build-standalone/releases/download/$release/${file//+/%2B}"
        [[ $(shasum -a 256 "$work/$file" | cut -d' ' -f1) == ${sums[$arch]} ]] || { print -u2 "$file does not match its checksum"; exit 1; }
        target="$resources/python/${arch/aarch64/arm64}"
        mkdir -p "$target"
        tar -xzf "$work/$file" -C "$target" --strip-components 1
        # What a server with no window and no packages never loads.
        lib="$target/lib/python${python%.*}"
        rm -rf "$target"/{include,share} "$target"/bin/{pip*,idle*,2to3*,pydoc*} "$lib"/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,pydoc_data,site-packages} "$lib"/config-* "$target"/lib/{tcl*,tk*,itcl*,thread*,lib*.a,pkgconfig} 2>/dev/null || true
        mkdir -p "$lib/site-packages"
        find "$target" -name __pycache__ -type d -prune -exec rm -rf {} +
    done
    "$resources/python/$(uname -m)/bin/python3" -c "import ssl, http.server, json, hashlib, urllib.request; print('runtime ok,', ssl.OPENSSL_VERSION)"
fi

cp web/{web.py,enrich.py,meta.py,tidy.py,index.html,style.css} "$resources/web/"
cp -R web/assets "$resources/web/"
cp LICENSE THIRD-PARTY-NOTICES "$resources/"
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > "$resources/cacert.pem"

icons="$work/icon.iconset"
rm -rf "$icons"; mkdir -p "$icons"
for size in 16 32 128 256 512; do
    sips -s format png -z $size $size web/assets/apple-touch-icon.png --out "$icons/icon_${size}x$size.png" > /dev/null
    sips -s format png -z $((size * 2)) $((size * 2)) web/assets/apple-touch-icon.png --out "$icons/icon_${size}x$size@2x.png" > /dev/null
done
iconutil -c icns "$icons" -o "$resources/icon.icns"

cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$name</string>
  <key>CFBundleExecutable</key><string>$name</string>
  <key>CFBundleIdentifier</key><string>$identifier</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$version</string>
  <key>CFBundleVersion</key><string>$version</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>NSHumanReadableCopyright</key><string>Copyright 2026 Jakub Warzecha. Modified in 2026 by the RePod authors. GNU GPL, version 3.</string>
  <key>NSRemovableVolumesUsageDescription</key><string>The iPod is a removable disk: its library and songs are read and written there.</string>
  <key>NSAppleEventsUsageDescription</key><string>To open the window that chooses a file or a folder.</string>
</dict></plist>
PLIST

codesign --force --deep --sign "${SIGN_ID:--}" "$app"
disk="dist/RePod-$version.dmg"
rm -f "$disk"
hdiutil create -quiet -volname "$name" -srcfolder "$app" -ov -format UDZO "$disk"
du -sh "$app" "$disk"
