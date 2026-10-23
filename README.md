# Xoom Echo Show

Transforma um tablet antigo (**Motorola Xoom, Android 4.4**, Tegra 2 sem NEON) numa espécie de **Echo Show**:
tela fixa com relógio, previsão do tempo e atalhos, assistente de voz com palavra de ativação e integração com
o Home Assistant e os Echo Dot da casa. O trabalho pesado (voz, YouTube, câmera) roda num notebook Linux na
mesma rede; o tablet só mostra a tela, capta o microfone e toca as respostas.

## Partes

| Pasta | O que é |
|---|---|
| `app/` | App Android **Xoom Show** (`br.xoomshow`, Java puro, sem Gradle): WebView em tela cheia com a página `/show`, serviço de microfone sempre ligado (TCP para o notebook), toca as respostas, abre no boot. Com headset USB usa o microfone dele (`xoomshow-mic.sh`, root + `arecord`) e mantém o som no alto-falante. |
| `assistente/` | Serviço **xoom-assistente** (Python): palavra de ativação "hey jarvis" (openWakeWord) → Whisper (pt) → comandos → voz Piper pt-BR. Também escuta os eventos de voz dos Echo Dot no Home Assistant (comandos com "tablet") e repete no tablet o que os Echo falam. |
| `painel/` | Painel web do tablet (Flask, porta 8796): página `/show`, YouTube `/yt` (yt-dlp, H.264 remuxado para o MX Player), câmera IP, Top 100, controle do tablet pelo ADB. |

## Comandos de voz

"Hey Jarvis" + … mostra/fecha a câmera · toca *música* no YouTube · toca o Top 100 · abre/fecha o WhatsApp ·
abre/fecha o Jellyfin · pausa · continua · para · volume 5 · aumenta o volume · que horas são · que dia é hoje ·
vai chover amanhã? · comandos da casa (Assist do Home Assistant) · perguntas gerais (Claude).

Pelo Echo Dot: crie Rotinas na Alexa cuja resposta ("Alexa diz") contenha **"no tablet"**
(ex.: "Mostrando a câmera no tablet") — o histórico da Alexa nem sempre traz o texto falado.

## Como montar

1. **Notebook** (Linux, Python 3.12):
   ```sh
   cd assistente && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
   mkdir -p modelos && cd modelos   # voz pt-BR do Piper
   curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx
   curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json
   ```
   `~/.env` com `HA_TOKEN=` (token de longa duração do Home Assistant). Rode `painel/xoom_painel.py` e
   `assistente/assistente.py` (ex.: no PM2). O Whisper `small` é baixado na primeira execução.
2. **Tablet**: `cd app && ./compilar.sh` (android-sdk build-tools 29 + platform 23) e
   `adb install -r XoomShow.apk`. Para o headset USB, copie `xoomshow-mic.sh` para `/data/local/` e instale
   `alsa-utils` num Debian em chroot (o app chama `su`).

Os endereços (notebook `192.168.4.135`, Home Assistant `192.168.4.30`, entidades dos Echo) estão no topo dos
arquivos — ajuste para a sua rede.
