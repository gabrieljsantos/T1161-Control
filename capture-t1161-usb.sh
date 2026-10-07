#!/bin/sh

set -u

service=t1161-driver.service
output="${1:-usb-capture.txt}"

systemctl stop "$service"
trap 'systemctl start "$service"' EXIT HUP INT TERM

echo "Capturando por 12 segundos: mova, pressione, continue movendo e solte." >&2
timeout 12s usbhid-dump -d 08f2:6811 -e stream -t 1000 > "$output"

echo "Captura salva em $output" >&2
