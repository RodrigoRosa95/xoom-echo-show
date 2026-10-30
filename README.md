# Xoom Echo Show

Transforma um tablet antigo (**Motorola Xoom, Android 4.4**, Tegra 2 sem NEON) numa espécie de **Echo Show**, integrando
**Echo Dot 5 + Home Assistant + Jellyfin + skills próprias na Amazon Developer + Claude Code**: tela fixa com relógio, previsão do
tempo e atalhos, música do Jellyfin no Echo com letra em tempo real, assistente de voz e controle da casa. O trabalho
pesado roda num notebook Linux na mesma rede; o tablet só mostra a tela, capta o microfone e abre os vídeos.

**[Como tudo se conecta, passo a passo das skills e do Home Assistant](docs/INTEGRACAO.md)**

## Partes

| Pasta | O que é |
|---|---|
| `app/` | App Android **Xoom Show** (`br.xoomshow`, Java puro, sem Gradle): WebView em tela cheia com a página `/show`, serviço de microfone sempre ligado (TCP para o notebook), toca as respostas, abre no boot. Com headset USB usa o microfone dele (`xoomshow-mic.sh`, root + `arecord`) e mantém o som no alto-falante. |
| `assistente/` | Serviço **xoom-assistente** (Python): palavra de ativação "hey jarvis" (openWakeWord) → Whisper (pt) → comandos → voz Piper pt-BR. Também escuta os eventos de voz dos Echo Dot no Home Assistant (comandos com "tablet") e repete no tablet o que os Echo falam. |
| `painel/` | Painel web do tablet (Flask, porta 8796): página `/show`, música e letra, YouTube `/yt` (yt-dlp, H.264 remuxado para o MX Player), câmera IP, Top 100, controle do tablet pelo ADB. |
| `alexa/` | Skills da Alexa **meu cinema** (Jellyfin, Sonarr, Radarr, Jellyseerr, qBittorrent e música no Echo com `AudioPlayer`) e **meu tablet** (comandos para o Xoom), com modelos de interação e testes. |
| `app-jellyfin/` | App **Jellyfin Xoom** (`br.jellyxoom`): cliente leve em 480p com TV ao vivo, que toca no MX Player. |
| `app-youtube/` | App **YouTube Xoom** (`br.ytxoom`): usa a página `/yt` do painel. |
| `jellyfin/` | [Guia do servidor](jellyfin/README.md): bibliotecas, letras `.lrc`, TV ao vivo e `fast_tv.py` (Pluto, Plex e canais abertos gratuitos). |
| `vigia/` | `vigia-xoom.sh`: cron que reabre o app travado e publica o estado no Home Assistant. |
| `docs/` | [Integração completa](docs/INTEGRACAO.md): arquitetura, Amazon Developer, Home Assistant, Claude Code e situação dos testes. |

## Comandos de voz

"Hey Jarvis" + … mostra/fecha a câmera · toca *música* no YouTube · toca o Top 100 · abre/fecha o WhatsApp ·
abre/fecha o Jellyfin · pausa · continua · para · volume 5 · aumenta o volume · que horas são · que dia é hoje ·
vai chover amanhã? · comandos da casa (Assist do Home Assistant) · perguntas gerais (Groq, com Claude Code de reserva).

Pelo Echo Dot: crie Rotinas na Alexa cuja resposta ("Alexa diz") contenha **"no tablet"**
(ex.: "Mostrando a câmera no tablet") — o histórico da Alexa nem sempre traz o texto falado.

## Música no Echo

O ícone do Spotify na tela `/show` abre uma tela de controle da música que toca no Echo Dot: faixa e artista, anterior,
play/pause, próxima, volume, "do início", "parar", troca de biblioteca do Jellyfin, a lista "a seguir" e a **letra em tempo
real**, com ajuste de sincronia de ±0,5 s.

- Os comandos vão como texto ao Echo pelo Home Assistant (`alexa_devices.send_text_command`), via `/show/echo/<ação>`.
- A música e o estado vêm da skill **meu cinema** (`alexa/`), que usa `AudioPlayer` e grava a fila em
  `~/.alexa-jellyflix-filas.json` (lida por `/show/echo/estado`).
- A letra vem do Jellyfin e, na falta dela, do [LRCLIB](https://lrclib.net). Nenhuma letra faz parte deste repositório.

## Como montar

1. **Notebook** (Linux, Python 3.12):
   ```sh
   cd assistente && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
   mkdir -p modelos && cd modelos   # voz pt-BR do Piper
   curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx
   curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium/pt_BR-faber-medium.onnx.json
   ```
   Crie o `~/.env` a partir de [`.env.exemplo`](.env.exemplo). Rode `painel/xoom_painel.py` e
   `assistente/assistente.py` (ex.: no PM2). O Whisper `small` é baixado na primeira execução.
2. **Tablet**: `cd app && ./compilar.sh` (android-sdk build-tools 29 + platform 23) e
   `adb install -r XoomShow.apk`. Para o headset USB, copie `xoomshow-mic.sh` para `/data/local/` e instale
   `alsa-utils` num Debian em chroot (o app chama `su`).

3. **Jellyfin**: siga [o guia do servidor](jellyfin/README.md).
4. **Skills**: siga [Amazon Developer](docs/INTEGRACAO.md#amazon-developer). Em `alexa/`, rode `npm install`, crie o `.env`
   e inicie `alexa-jellyflix.js`.

Os endereços (notebook `192.168.4.135`, Home Assistant `192.168.4.30`, entidades dos Echo) estão no topo dos
arquivos — ajuste para a sua rede.
