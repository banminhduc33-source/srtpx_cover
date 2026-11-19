#!/usr/bin/env bash
# Lấy từng cặp (video + .srtpx cùng tên) trong $REMOTE/processing, xuất video mới,
# gửi vào $REMOTE/out, chuyển file gốc sang $REMOTE/done (lỗi thì sang $REMOTE/failed).
set -uo pipefail

REMOTE="${REMOTE:-gd:srtpx}"
EXPORT_ARGS="${EXPORT_ARGS:-}"           # ví dụ: --crf 20 --preset medium
WORK="${WORK:-$PWD/_work}"
VIDEO_EXT_RE='\.(mp4|mov|mkv|webm|m4v|avi)$'

LIB_SANS=/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf
CJK=/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc

# Chuỗi font: Bevan -> Liberation Sans (như Arial, có tiếng Việt) -> Noto Sans CJK (chữ Trung)
FONT_ARGS=()
[ -f fonts/Bevan-Regular.ttf ] && FONT_ARGS+=(--font fonts/Bevan-Regular.ttf)
[ -f "$LIB_SANS" ] && FONT_ARGS+=(--font "$LIB_SANS")
[ -f "$CJK" ] && FONT_ARGS+=(--font "$CJK#0")

# Lồng tiếng: có secret TIKTOK_SESSIONID thì tự bật. Giọng Việt, âm gốc 30%, giọng đọc 100%.
# (App tự đọc sessionid từ biến môi trường TIKTOK_SESSIONID, không truyền qua dòng lệnh để khỏi lộ.)
DUB_ARGS=()
if [ -n "${TIKTOK_SESSIONID:-}" ]; then
  # --both: mỗi video ra HAI file, ${base}_out_tts.mp4 (có giọng đọc) và ${base}_out_notts.mp4 (âm thanh gốc, không TTS).
  DUB_ARGS+=(--dub --both --voice BV074_streaming --orig-volume 0.3 --voice-volume 1.0)
else
  echo "::notice::Chưa có secret TIKTOK_SESSIONID — xuất không lồng tiếng."
fi

rm -rf "$WORK"; mkdir -p "$WORK"
mapfile -t FILES < <(rclone lsf "$REMOTE/processing" --files-only 2>/dev/null)
if [ "${#FILES[@]}" -eq 0 ]; then echo "Không có file nào trong processing/. Xong."; exit 0; fi

ok=0; bad=0
for f in "${FILES[@]}"; do
  case "$f" in *.srtpx) ;; *) continue ;; esac
  base="${f%.srtpx}"
  video=""
  for g in "${FILES[@]}"; do
    if [ "${g%.*}" = "$base" ] && printf '%s' "$g" | grep -qiE "$VIDEO_EXT_RE"; then video="$g"; break; fi
  done
  if [ -z "$video" ]; then
    echo "::warning::Thiếu video cho '$f' — bỏ qua."; continue
  fi

  echo "::group::Xuất: $video + $f"
  job="$WORK/job"; rm -rf "$job"; mkdir -p "$job"
  log="$job/log.txt"; out="$job/${base}_out.mp4"
  success=0
  if rclone copyto "$REMOTE/processing/$video" "$job/in_video" \
     && rclone copyto "$REMOTE/processing/$f" "$job/in.srtpx"; then
    # shellcheck disable=SC2086
    if python -m srtpx_cover.cli "$job/in_video" "$job/in.srtpx" "$out" \
         "${FONT_ARGS[@]}" "${DUB_ARGS[@]}" --threads 0 $EXPORT_ARGS 2>&1 | tr '\r' '\n' | tee "$log"; then success=1; fi
  else
    echo "Không tải được file từ Drive" | tee "$log"
  fi
  # pipefail: tee luôn trả 0 nên kiểm lại bằng file đầu ra + dòng "Xong"
  grep -q '^Xong:' "$log" 2>/dev/null || success=0
  # Đầu ra: một video (${base}_out.mp4), hoặc hai video _tts / _notts khi có lồng tiếng. Lấy những file thật sự có.
  outs=()
  for cand in "$out" "${out%.mp4}_tts.mp4" "${out%.mp4}_notts.mp4"; do
    [ -s "$cand" ] && outs+=("$cand")
  done
  [ "${#outs[@]}" -gt 0 ] || success=0
  echo "::endgroup::"

  if [ "$success" = 1 ]; then
    sent=1
    for o in "${outs[@]}"; do rclone copyto "$o" "$REMOTE/out/$(basename "$o")" || sent=0; done
    [ "$sent" = 1 ] \
      && rclone moveto "$REMOTE/processing/$video" "$REMOTE/done/$video" \
      && rclone moveto "$REMOTE/processing/$f" "$REMOTE/done/$f" \
      && { echo "OK: $base"; ok=$((ok+1)); } || { echo "::error::Gửi kết quả về Drive lỗi: $base"; bad=$((bad+1)); }
  else
    echo "::error::Xuất lỗi: $base"
    rclone copyto "$log" "$REMOTE/failed/${base}.log.txt" || true
    rclone moveto "$REMOTE/processing/$video" "$REMOTE/failed/$video" || true
    rclone moveto "$REMOTE/processing/$f" "$REMOTE/failed/$f" || true
    bad=$((bad+1))
  fi
  rm -rf "$job"
done

echo "Thành công: $ok — Lỗi: $bad"
[ "$bad" -eq 0 ]
