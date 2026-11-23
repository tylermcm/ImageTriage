#!/usr/bin/env bash
# Makes a self-signed code-signing certificate for test builds of Image Triage on this Mac (once), in a keychain of its own.
#
#   bash packaging/macos_dev_signing.sh
#
# Why: macOS ties a person's answers ("Image Triage would like to access files in your Desktop folder") to the app's signature. An ad hoc
# signature is a fingerprint of one build, so every rebuild is a new app to macOS and asks again. Signed with this certificate, every
# build has the same signature and the answers stay. Not for release: other Macs do not trust it (a release needs a Developer ID).
#
# The certificate lives in ~/Library/Keychains/imagetriage-dev.keychain-db, not the login keychain, so signing never needs the Mac's
# password: the keychain's own password is random and kept in ~/.imagetriage-signing (readable by this user only).
# packaging/build_macos.sh signs with it whenever it is there.
set -euo pipefail

NAME="Image Triage Development"
DIR="$HOME/.imagetriage-signing"
KEYCHAIN="$HOME/Library/Keychains/imagetriage-dev.keychain-db"
[[ "$(uname -s)" == "Darwin" ]] || { echo "Run this on the Mac that builds Image Triage." >&2; exit 1; }

mkdir -p "$DIR" && chmod 700 "$DIR"
if security find-identity -p codesigning "$KEYCHAIN" 2>/dev/null | grep -q "\"$NAME\""; then
  echo "Already set up: $NAME in $KEYCHAIN"
  exit 0
fi

cat > "$DIR/cert.cnf" <<CNF
[req]
distinguished_name = dn
x509_extensions = ext
prompt = no
[dn]
CN = $NAME
[ext]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = critical, codeSigning
CNF
/usr/bin/openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout "$DIR/key.pem" -out "$DIR/cert.pem" -config "$DIR/cert.cnf" 2>/dev/null
P12_PASSWORD="$(/usr/bin/openssl rand -hex 16)"
/usr/bin/openssl pkcs12 -export -inkey "$DIR/key.pem" -in "$DIR/cert.pem" -name "$NAME" -out "$DIR/identity.p12" \
  -passout "pass:$P12_PASSWORD" 2>/dev/null

[[ -f "$DIR/keychain-password" ]] || { /usr/bin/openssl rand -hex 24 > "$DIR/keychain-password"; chmod 600 "$DIR/keychain-password"; }
KEYCHAIN_PASSWORD="$(cat "$DIR/keychain-password")"
[[ -f "$KEYCHAIN" ]] || security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security set-keychain-settings "$KEYCHAIN"                     # no automatic locking
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
security import "$DIR/identity.p12" -k "$KEYCHAIN" -P "$P12_PASSWORD" -T /usr/bin/codesign >/dev/null
security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN" >/dev/null
# In the search list (after the existing keychains) so codesign finds the identity by name.
existing=$(security list-keychains -d user | tr -d '"' | tr '\n' ' ')
case " $existing " in *" $KEYCHAIN "*) ;; *) security list-keychains -d user -s $existing "$KEYCHAIN" ;; esac
rm -f "$DIR/identity.p12" "$DIR/key.pem" "$DIR/cert.cnf"     # the key now lives in the keychain only
security find-identity -p codesigning "$KEYCHAIN"
echo "Set up: builds made with packaging/build_macos.sh are now signed as \"$NAME\"."
