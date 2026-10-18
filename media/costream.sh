#!/bin/sh
# Co-stream composite for costream/{session_id}. The API starts it (MediaMTX runOnInit path) while the
# session is active and deletes the path when the session terminates or on reset, which stops ffmpeg.
# The API lists the participants' active live/ paths that are publishing right now (active host first,
# at most 4) and they are tiled into one grid; with none live a cheap static placeholder keeps the
# composite URL playing. When an input ends (participant left, kicked, key revoked) ffmpeg exits and
# MediaMTX restarts this script (runOnInitRestart), which picks up the current participant set.
SID="$1"
PORT="${RTSP_PORT:-8554}"
OUT="rtsp://localhost:$PORT/costream/$SID"
X264="-c:v libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p -an -f rtsp -rtsp_transport tcp"
INPUTS=$(wget -qO- "http://api:8080/internal/costream/$SID/inputs")
set --
N=0
for P in $INPUTS; do
  set -- "$@" -rtsp_transport tcp -i "rtsp://localhost:$PORT/$P"
  N=$((N + 1))
done
if [ "$N" -eq 0 ]; then
  # Static 640x360 frame at 10 fps: a few percent of one core.
  exec ffmpeg -hide_banner -loglevel error -re -f lavfi -i "color=c=0x14213d:s=640x360:r=10" \
    -r 10 -g 10 -threads 1 $X264 "$OUT"
fi
TILE="scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1"
case "$N" in
  1) F="[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[v]" ;;
  2) F="[0:v]$TILE[a];[1:v]$TILE[b];[a][b]hstack=inputs=2[v]" ;;
  3) F="[0:v]$TILE[a];[1:v]$TILE[b];[2:v]$TILE[c];color=c=black:s=640x360:r=15[d];[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:shortest=1[v]" ;;
  *) F="[0:v]$TILE[a];[1:v]$TILE[b];[2:v]$TILE[c];[3:v]$TILE[d];[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[v]" ;;
esac
exec ffmpeg -hide_banner -loglevel error "$@" -filter_complex "$F" -map "[v]" -r 15 -g 15 -threads 2 $X264 "$OUT"
