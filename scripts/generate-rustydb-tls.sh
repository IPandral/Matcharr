#!/usr/bin/env bash
set -euo pipefail

tls_dir=${1:-./rustydb-tls}
if [[ -e "$tls_dir" || -L "$tls_dir" ]]; then
  printf 'Choose a new certificate directory. Already exists: %s\n' "$tls_dir" >&2
  exit 1
fi

work_dir=$(mktemp -d)
trap 'rm -f -- "$work_dir"/{ca.crt,ca.key,ca.srl,server.crt,server.csr,server.ext,server.key}; rmdir -- "$work_dir"' EXIT
umask 077

export MSYS2_ARG_CONV_EXCL='/CN='
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -subj '/CN=Matcharr RustyDB CA' \
  -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' \
  -addext 'subjectKeyIdentifier=hash' \
  -keyout "$work_dir/ca.key" -out "$work_dir/ca.crt"
openssl req -newkey rsa:2048 -nodes -subj '/CN=rustydb' \
  -keyout "$work_dir/server.key" -out "$work_dir/server.csr"
cat > "$work_dir/server.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:rustydb,DNS:localhost,IP:127.0.0.1
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
EOF
openssl x509 -req -in "$work_dir/server.csr" -days 365 \
  -CA "$work_dir/ca.crt" -CAkey "$work_dir/ca.key" -CAcreateserial \
  -extfile "$work_dir/server.ext" -out "$work_dir/server.crt"
openssl verify -x509_strict -purpose sslserver -verify_hostname rustydb \
  -CAfile "$work_dir/ca.crt" "$work_dir/server.crt"

mkdir -- "$tls_dir"
chmod 755 "$tls_dir"
cp -- "$work_dir"/{ca.crt,server.crt,server.key} "$tls_dir/"
chmod 644 "$tls_dir/ca.crt" "$tls_dir/server.crt"
chmod 600 "$tls_dir/server.key"
