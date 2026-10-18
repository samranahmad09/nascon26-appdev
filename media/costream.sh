#!/bin/sh
# MediaMTX runOnDemand for costream/{session_id}: composes the session's live participants into one grid.
# The API lists the participants' active live/ paths that are publishing right now (active host first,
# at most 4). With none live it publishes a placeholder so the composite URL always plays. When a
# participant's input ends (left, kicked, key revoked) ffmpeg exits and MediaMTX restarts this script,
# which picks up the new participant set.
SID="$1"
PORT="${RTSP_PORT:-8554}"
OUT="rtsp://localhost:$PORT/costream/$SID"
ENC="-c:v libx264 -preset ultrafast -tune zerolatency -g 50 -pix_fmt yuv420p -an -f rtsp -rtsp_transport tcp $OUT"
INPUTS=$(wget -qO- "http://api:8080/internal/costream/$SID/inputs")
set --
N=0
for P in $INPUTS; do
  set -- "$@" -rtsp_transport tcp -i "rtsp://localhost:$PORT/$P"
  N=$((N + 1))
done
TILE="scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,setsar=1"
case "$N" in
  0) exec ffmpeg -hide_banner -loglevel error -re -f lavfi -i "testsrc2=size=1280x720:rate=25" $ENC ;;
  1) F="[0:v]scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,setsar=1[v]" ;;
  2) F="[0:v]$TILE[a];[1:v]$TILE[b];[a][b]hstack=inputs=2[v]" ;;
  3) F="[0:v]$TILE[a];[1:v]$TILE[b];[2:v]$TILE[c];color=c=black:s=640x360:r=25[d];[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0:shortest=1[v]" ;;
  *) F="[0:v]$TILE[a];[1:v]$TILE[b];[2:v]$TILE[c];[3:v]$TILE[d];[a][b][c][d]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0[v]" ;;
esac
exec ffmpeg -hide_banner -loglevel error "$@" -filter_complex "$F" -map "[v]" $ENC
