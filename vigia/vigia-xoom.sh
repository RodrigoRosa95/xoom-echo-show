#!/bin/bash
# Vigia do Xoom Show (cron a cada 2 min, 28/10/2026).
# - ADB fora → tenta reconectar; 3 checagens seguidas (6 min) → avisa no WhatsApp
# - app br.xoomshow parado → abre o Show
# - microfone sem conexão com o xoom-assistente (:8797) em 2 checagens → reabre o app
# - Show na frente, tela acesa e sem pedir /show/voz há 2 min (WebView travado) → reabre o app
# - no máx. 3 reaberturas por hora; passou disso → avisa no WhatsApp e só vigia
# - publica binary_sensor.xoom_show no HA (on = microfone conectado e tela respondendo)
# Para mexer no Xoom sem o vigia reabrir o app: touch ~/.xoom-manutencao (apague ao terminar).
HA_URL=${HA_URL:-http://192.168.4.30}
# aviso: VIGIA_AVISO_CMD recebe o texto como argumento (WhatsApp, Telegram...); sem ele, só vai para o log
whatsapp(){ logger -t vigia-xoom "$1"; [ -z "$VIGIA_AVISO_CMD" ] || "$VIGIA_AVISO_CMD" "$1"; }
log(){ logger -t vigia-xoom "$*"; echo "$(date '+%d/%m %H:%M:%S') $*"; }
XOOM=192.168.4.72:5555
ESTADO=$HOME/.local/share/vigia-xoom.estado   # falhas_adb falhas_mic aviso reaberturas(epoch,...)
BATIMENTO=/tmp/xoom-show-visto                         # o xoom-painel toca a cada /show/voz
TRAVADO=120; MAX_REABRIR=3; JANELA=3600

[ -e $HOME/.xoom-manutencao ] && exit 0
case "$(systemctl is-system-running 2>/dev/null)" in stopping|initializing|starting) exit 0 ;; esac

falhas_adb=0; falhas_mic=0; avisado=0; reaberturas=""
[ -f "$ESTADO" ] && . "$ESTADO"
salvar(){ printf 'falhas_adb=%s\nfalhas_mic=%s\navisado=%s\nreaberturas="%s"\n' \
  "$falhas_adb" "$falhas_mic" "$avisado" "$reaberturas" > "$ESTADO"; }
agora=$(date +%s)

sensor(){   # $1 on/off, $2 motivo
  local token
  token=$(grep -E '^HA_TOKEN=' $HOME/.env | cut -d= -f2- | tr -d "\"'")
  python3 -c 'import json,sys;print(json.dumps({"state":sys.argv[1],"attributes":{"friendly_name":"Xoom Show",
"device_class":"connectivity","motivo":sys.argv[2]}}))' "$1" "$2" |
    curl -s -m 10 -o /dev/null -H "Authorization: Bearer $token" -H 'Content-Type: application/json' \
      -d @- "${HA_URL%/}/api/states/binary_sensor.xoom_show"
}
xadb(){ timeout 20 adb -s "$XOOM" "$@" 2>/dev/null; }

# 1) ADB
if [ "$(xadb get-state)" != device ]; then
  timeout 15 adb connect "$XOOM" >/dev/null 2>&1; sleep 2
fi
if [ "$(xadb get-state)" != device ]; then
  falhas_adb=$((falhas_adb + 1)); log "Xoom sem ADB ($falhas_adb)"
  if [ "$falhas_adb" -ge 3 ] && [ "$avisado" != adb ]; then
    whatsapp "Xoom fora da rede há 6 min (ADB não responde). Pode estar desligado, sem bateria ou fora do Wi-Fi."
    avisado=adb
  fi
  sensor off "sem ADB"; salvar; exit 0
fi
if [ "$avisado" = adb ]; then whatsapp "Xoom de volta na rede ✅"; avisado=0; fi
falhas_adb=0

# reaberturas da última hora
recentes=""; for t in $reaberturas; do [ $((agora - t)) -lt $JANELA ] && recentes="$recentes $t"; done
reaberturas=${recentes# }

reabrir(){   # $1 motivo
  if [ "$(echo $reaberturas | wc -w)" -ge $MAX_REABRIR ]; then
    log "precisava reabrir ($1), mas já foram $MAX_REABRIR na última hora"
    if [ "$avisado" != limite ]; then
      whatsapp "Xoom Show com problema ($1) e já reabri $MAX_REABRIR vezes na última hora. Precisa olhar o tablet."
      avisado=limite
    fi
    return
  fi
  log "reabrindo o Show: $1"
  xadb shell "am force-stop br.xoomshow; am start -n br.xoomshow/.ShowActivity" >/dev/null
  reaberturas="$reaberturas $agora"; reaberturas=${reaberturas# }
}

foco=$(xadb shell dumpsys activity activities | grep -m1 mFocusedActivity)
tela=$(xadb shell dumpsys power | grep -m1 mScreenOn | tr -d '\r')
mic=$(ss -tnH state established '( sport = :8797 )' | grep -c "${XOOM%:*}:")
visto=$(( agora - $(stat -c %Y "$BATIMENTO" 2>/dev/null || echo 0) ))

# 2) app parado
if [ -z "$(xadb shell pidof br.xoomshow | tr -d '\r')" ]; then
  reabrir "app parado"; sensor off "app parado"; salvar; exit 0
fi

# 3) microfone
if [ "$mic" = 0 ]; then
  falhas_mic=$((falhas_mic + 1)); log "microfone sem conexão ($falhas_mic)"
  [ "$falhas_mic" -ge 2 ] && { reabrir "microfone desconectado"; falhas_mic=0; }
  sensor off "microfone desconectado"; salvar; exit 0
fi
falhas_mic=0

# 4) WebView travado (só conta com o Show na frente e a tela acesa; relógio das 5h, Jellyfin etc. não)
if [[ "$foco" == *br.xoomshow/.ShowActivity* && "$tela" == *true* && $visto -gt $TRAVADO ]]; then
  reabrir "tela travada (${visto}s sem atualizar)"; sensor off "tela travada"; salvar; exit 0
fi

[ "$avisado" = limite ] && [ -z "$reaberturas" ] && avisado=0
sensor on "ok"; salvar
