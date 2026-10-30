#!/bin/bash
# Comandos do tablet Xoom (192.168.4.72, ADB root) para o cron e o painel.
#   xoom.sh relogio-abrir   → acorda a tela e abre o relógio em tela cheia (cron 05:00)
#   xoom.sh relogio-fechar  → fecha o relógio e volta à tela inicial (cron 06:00)
# Usa o adb-server do notebook (porta 5037), o mesmo do Home Assistant.
XOOM=192.168.4.72:5555
RELOGIO=systems.sieber.fsclock   # "Clock" preto em tela cheia (Fullscreen Clock)
LOG=/home/rodrigo/xoom-painel/xoom.log

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
a() { timeout 20 adb -s "$XOOM" "$@"; }

conectar() {
  a get-state >/dev/null 2>&1 && return 0
  timeout 15 adb connect "$XOOM" >/dev/null 2>&1
  a get-state >/dev/null 2>&1 || { log "Xoom fora do ar ($XOOM)"; return 1; }
}

acordar() {
  # Android 4.4 não tem KEYCODE_WAKEUP: só aperta POWER se a tela estiver apagada
  a shell dumpsys power | grep -q 'mScreenOn=false' && a shell input keyevent 26
  a shell input keyevent 82 >/dev/null 2>&1   # tira a tela de bloqueio sem senha
}

case "$1" in
  relogio-abrir)
    conectar || exit 1
    acordar
    # Top 100 / câmera IP transmitindo pelo painel: encerra antes, senão o ffmpeg segue convertendo para ninguém
    for parar in top100_parar camera_ip_parar; do
      curl -s -m 10 -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8796/api/acao/$parar >/dev/null
    done
    a shell am start -n "$RELOGIO/.FullscreenActivity" >/dev/null
    log "relógio aberto"
    ;;
  relogio-fechar)
    conectar || exit 1
    a shell am force-stop "$RELOGIO"
    a shell input keyevent 3
    log "relógio fechado"
    ;;
  *)
    echo "uso: $0 relogio-abrir|relogio-fechar" >&2
    exit 2
    ;;
esac
