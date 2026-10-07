#!/usr/bin/env bash

set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
manifest="$project_dir/driver/Cargo.toml"
binary="$project_dir/driver/target/release/t1161-diagnostic"
destination="/usr/libexec/t1161-driver"
service="t1161-driver.service"
user_directory="$(getent passwd "$(id -u)" | cut -d: -f6)/.local/bin"
actions_destination="$user_directory/t1161-actions"
control_destination="$user_directory/t1161-control"
configurator_destination="$user_directory/t1161-configurator"
profile_manager_destination="$user_directory/t1161-profile-manager"
runtime_config="$project_dir/t1161-driver-runtime.conf"
reset_helper="$project_dir/t1161-reset-driver"
applications_directory="$(getent passwd "$(id -u)" | cut -d: -f6)/.local/share/applications"
autostart_directory="$(getent passwd "$(id -u)" | cut -d: -f6)/.config/autostart"
extension_uuid="t1161-pointer-sync@local"
extension_source="$project_dir/gnome-shell-extension/$extension_uuid"
extension_destination="$(getent passwd "$(id -u)" | cut -d: -f6)/.local/share/gnome-shell/extensions/$extension_uuid"

finish() {
    status=$?
    printf '\n'
    if (( status == 0 )); then
        printf 'Instalação concluída. O driver T1161 está ativo.\n'
    else
        printf 'A instalação não foi concluída (código %d).\n' "$status"
    fi
    if [[ -t 0 ]]; then
        read -r -p 'Pressione Enter para fechar...' _
    fi
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'MT500–T1161 Linux Graphics Tablet Driver — instalador\n\n'

if ! command -v cargo >/dev/null 2>&1; then
    printf 'Erro: o compilador Rust/Cargo não está instalado.\n' >&2
    exit 1
fi

if [[ ! -f /etc/systemd/system/$service ]]; then
    printf 'Erro: o serviço %s não existe neste sistema.\n' "$service" >&2
    exit 1
fi

printf '1/5 Compilando o driver...\n'
cargo build --release --manifest-path "$manifest"

printf '\n2/5 Encerrando os componentes T1161 em execução...\n'
stop_component() {
    local executable="$1"
    pkill -TERM -f "^$executable( |$)" 2>/dev/null || true
    pkill -TERM -f "^/usr/bin/python3 $executable( |$)" 2>/dev/null || true
}
stop_component "$actions_destination"
stop_component "$control_destination"
stop_component "$configurator_destination"
stop_component "$profile_manager_destination"

printf '\n3/5 Atualizando painel, configurador e perfis...\n'
mkdir -p "$user_directory"
install -m 0755 "$project_dir/t1161_control.py" "$actions_destination"
install -m 0755 "$project_dir/t1161_control.py" "$control_destination"
install -m 0755 "$project_dir/t1161_configurator.py" "$configurator_destination"
install -m 0755 "$project_dir/t1161_profile_manager.py" "$profile_manager_destination"
install -m 0644 "$project_dir/t1161_profiles.py" "$user_directory/t1161_profiles.py"
install -m 0644 "$project_dir/t1161_input_profiles.py" "$user_directory/t1161_input_profiles.py"
mkdir -p "$applications_directory" "$autostart_directory"
install -m 0644 "$project_dir/io.github.t1161.Control.desktop" \
    "$applications_directory/io.github.t1161.Control.desktop"
install -m 0644 "$project_dir/io.github.t1161.Actions.desktop" \
    "$autostart_directory/io.github.t1161.Actions.desktop"
mkdir -p "$extension_destination"
install -m 0644 "$extension_source/metadata.json" "$extension_destination/metadata.json"
install -m 0644 "$extension_source/extension.js" "$extension_destination/extension.js"
find "$user_directory/__pycache__" -maxdepth 1 -type f -name 't1161*.pyc' -delete 2>/dev/null || true

printf '\n4/5 Instalando e reiniciando o driver em %s...\n' "$destination"
printf 'O sistema solicitará sua senha administrativa.\n'
pkexec /bin/sh -c '
    set -eu
    systemctl stop t1161-driver.service
    install -o root -g root -m 0755 "$1" /usr/libexec/t1161-driver
    install -D -o root -g root -m 0644 "$2" \
        /etc/systemd/system/t1161-driver.service.d/runtime.conf
    install -o root -g root -m 0755 "$3" /usr/libexec/t1161-reset-driver
    systemctl daemon-reload
    systemctl restart t1161-driver.service
' installer "$binary" "$runtime_config" "$reset_helper"

printf '\n5/5 Verificando o serviço e reabrindo o painel...\n'
if ! systemctl is-active --quiet "$service"; then
    systemctl status "$service" --no-pager || true
    printf 'Erro: o serviço não ficou ativo após a instalação.\n' >&2
    exit 1
fi
nohup "$actions_destination" --background >/dev/null 2>&1 &
if command -v gnome-extensions >/dev/null 2>&1; then
    gnome-extensions enable "$extension_uuid" 2>/dev/null || true
fi
if command -v gsettings >/dev/null 2>&1; then
    enabled_extensions="$(gsettings get org.gnome.shell enabled-extensions)"
    updated_extensions="$(python3 - "$extension_uuid" "$enabled_extensions" <<'PY'
import ast
import sys

uuid = sys.argv[1]
try:
    serialized = sys.argv[2].removeprefix('@as ').strip()
    enabled = list(ast.literal_eval(serialized))
except (SyntaxError, ValueError, TypeError):
    raise SystemExit("não foi possível preservar a lista de extensões do GNOME")
if uuid not in enabled:
    enabled.append(uuid)
print(repr(enabled))
PY
)"
    gsettings set org.gnome.shell enabled-extensions "$updated_extensions"
fi
